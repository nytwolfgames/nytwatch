#include "NytwatchSubsystem.h"

#include "Editor.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "Misc/DateTime.h"
#include "Misc/App.h"
#include "HAL/PlatformProcess.h"
#include "Misc/CoreDelegates.h"
#include "SourceCodeNavigation.h"
#include "Misc/PackageName.h"

DEFINE_LOG_CATEGORY_STATIC(LogNytwatchSubsystem, Log, All);

// ---------------------------------------------------------------------------
// Get  (static convenience accessor)
// ---------------------------------------------------------------------------

UNytwatchSubsystem* UNytwatchSubsystem::Get()
{
#if WITH_EDITOR
    if (GEditor)
        return GEditor->GetEditorSubsystem<UNytwatchSubsystem>();
#endif
    return nullptr;
}

// ---------------------------------------------------------------------------
// Initialize / Deinitialize
// ---------------------------------------------------------------------------

void UNytwatchSubsystem::Initialize(FSubsystemCollectionBase& Collection)
{
    Super::Initialize(Collection);

    FEditorDelegates::BeginPIE.AddUObject(this, &UNytwatchSubsystem::OnBeginPIE);
    FEditorDelegates::EndPIE.AddUObject(this, &UNytwatchSubsystem::OnEndPIE);
    CrashDelegateHandle = FCoreDelegates::OnHandleSystemError.AddUObject(
        this, &UNytwatchSubsystem::OnCrash);

    UE_LOG(LogNytwatchSubsystem, Log,
        TEXT("[NytwatchAgent] Subsystem initialised."));
}

void UNytwatchSubsystem::Deinitialize()
{
    FEditorDelegates::BeginPIE.RemoveAll(this);
    FEditorDelegates::EndPIE.RemoveAll(this);
    FCoreDelegates::OnHandleSystemError.Remove(CrashDelegateHandle);

    if (bTrackingActive)
    {
        FTSTicker::GetCoreTicker().RemoveTicker(TickHandle);
        const FString ProjectDir =
            FPaths::ConvertRelativePathToFull(FPaths::ProjectDir());
        Writer.Close(ProjectDir);
        bTrackingActive = false;
    }

    Super::Deinitialize();
}

// ---------------------------------------------------------------------------
// OnBeginPIE
// ---------------------------------------------------------------------------

void UNytwatchSubsystem::OnBeginPIE(bool /*bIsSimulating*/)
{
    // Reset state from any previous session
    Tracker.Reset();
    ClassSystemIndexCache.Reset();
    TrackedObjects.Reset();
    PIEElapsedSeconds     = 0.f;
    TimeSinceConfigReload = 0.f;
    bTrackingActive       = false;

    const FString ProjectDir =
        FPaths::ConvertRelativePathToFull(FPaths::ProjectDir());

    // 1. Load config
    Config = FNytwatchConfig::Load(ProjectDir);
    if (!Config.bValid || Config.ArmedSystems.Num() == 0)
    {
        UE_LOG(LogNytwatchSubsystem, Log,
            TEXT("[NytwatchAgent] No armed systems — tracking disabled for this session."));
        return;
    }

    // 2. Write lock file
    const FString NytwatchDir = FPaths::Combine(
        ProjectDir, TEXT("Saved"), TEXT("Nytwatch"));
    IFileManager::Get().MakeDirectory(*NytwatchDir, true);

    const FString LockPath = FPaths::Combine(NytwatchDir, TEXT("nytwatch.lock"));
    const FString LockJson = FString::Printf(
        TEXT("{\"session_id\":\"\",\"started_at\":\"%s\",\"plugin_version\":\"%s\",\"pid\":%d}"),
        *FDateTime::UtcNow().ToString(TEXT("%Y-%m-%dT%H:%M:%SZ")),
        TEXT(NYTWATCH_PLUGIN_VERSION),
        FPlatformProcess::GetCurrentProcessId());
    FFileHelper::SaveStringToFile(LockJson, *LockPath,
        FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM);

    // 3. Open session writer (also starts the background writer thread)
    Writer.Open(Config, ProjectDir);
    if (!Writer.IsOpen())
    {
        UE_LOG(LogNytwatchSubsystem, Error,
            TEXT("[NytwatchAgent] Failed to open session writer — tracking disabled."));
        IFileManager::Get().Delete(*LockPath, false, true);
        return;
    }

    // Update lock with the real session ID
    const FString LockJsonFinal = FString::Printf(
        TEXT("{\"session_id\":\"%s\",\"started_at\":\"%s\",\"plugin_version\":\"%s\",\"pid\":%d}"),
        *Writer.GetSessionId(),
        *FDateTime::UtcNow().ToString(TEXT("%Y-%m-%dT%H:%M:%SZ")),
        TEXT(NYTWATCH_PLUGIN_VERSION),
        FPlatformProcess::GetCurrentProcessId());
    FFileHelper::SaveStringToFile(LockJsonFinal, *LockPath,
        FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM);

    // 4. Start tick
    bTrackingActive = true;
    TickHandle = FTSTicker::GetCoreTicker().AddTicker(
        FTickerDelegate::CreateUObject(this, &UNytwatchSubsystem::OnTick),
        Config.TickIntervalSeconds);

    UE_LOG(LogNytwatchSubsystem, Log,
        TEXT("[NytwatchAgent] Session started — armed: %d system(s), poll interval: %.1fs."),
        Config.ArmedSystems.Num(), Config.TickIntervalSeconds);
}

// ---------------------------------------------------------------------------
// OnEndPIE
// ---------------------------------------------------------------------------

void UNytwatchSubsystem::OnEndPIE(bool /*bIsSimulating*/)
{
    if (!bTrackingActive) return;

    FTSTicker::GetCoreTicker().RemoveTicker(TickHandle);

    const FString ProjectDir =
        FPaths::ConvertRelativePathToFull(FPaths::ProjectDir());

    // Close waits for the background writer thread to flush then stops it.
    Writer.Close(ProjectDir);

    UE_LOG(LogNytwatchSubsystem, Log,
        TEXT("[NytwatchAgent] Session closed — %d events, %d objects tracked."),
        Writer.GetTotalEventCount(), TrackedObjects.Num());

    bTrackingActive = false;
    TrackedObjects.Reset();
    Tracker.Reset();
    ClassSystemIndexCache.Reset();
}

// ---------------------------------------------------------------------------
// OnCrash  (crash-handler thread — keep it simple, no allocations if avoidable)
// ---------------------------------------------------------------------------

void UNytwatchSubsystem::OnCrash()
{
    if (!bTrackingActive) return;

    // Ticker is no longer safe to touch from a crash context — just mark
    // tracking as inactive so any re-entrant path is a no-op.
    bTrackingActive = false;

    const FString ProjectDir =
        FPaths::ConvertRelativePathToFull(FPaths::ProjectDir());
    Writer.EmergencyClose(ProjectDir);

    UE_LOG(LogNytwatchSubsystem, Warning,
        TEXT("[NytwatchAgent] PIE hard close detected — session file marked as crashed."));
}

// ---------------------------------------------------------------------------
// RegisterObject  (game thread, called from BeginPlay)
// ---------------------------------------------------------------------------

void UNytwatchSubsystem::RegisterObject(UObject* Obj)
{
    if (!bTrackingActive || !IsValid(Obj)) return;

    // Eligibility is determined solely by whether the class's header falls
    // within an armed system's paths.  Any object that explicitly calls
    // RegisterObject is trusted to want tracking — no interface check here.
    const int32 SysIdx = FindSystemIndexForClass(Obj->GetClass());
    if (SysIdx == INDEX_NONE) return; // class not in any armed system

    // Prevent duplicate registrations
    for (const FTrackedObject& T : TrackedObjects)
    {
        if (T.Object == Obj) return;
    }

    // Snapshot initial property values so the first poll has a baseline
    const FNytwatchSystemConfig& System = Config.ArmedSystems[SysIdx];
    Tracker.SnapshotObject(Obj, System);

    TrackedObjects.Add({ Obj, SysIdx });

    UE_LOG(LogNytwatchSubsystem, Log,
        TEXT("[NytwatchAgent] Registered '%s' (%s) → system '%s'."),
        *Obj->GetName(), *Obj->GetClass()->GetName(),
        *Config.ArmedSystems[SysIdx].SystemName);
}

// ---------------------------------------------------------------------------
// UnregisterObject  (game thread, called from EndPlay while Obj is still valid)
// ---------------------------------------------------------------------------

void UNytwatchSubsystem::UnregisterObject(UObject* Obj)
{
    if (!Obj) return;

    for (int32 i = TrackedObjects.Num() - 1; i >= 0; --i)
    {
        if (TrackedObjects[i].Object == Obj)
        {
            Tracker.RemoveObject(Obj);
            TrackedObjects.RemoveAtSwap(i);

            UE_LOG(LogNytwatchSubsystem, Verbose,
                TEXT("[NytwatchAgent] Unregistered '%s'."), *Obj->GetName());
            return;
        }
    }
}

// ---------------------------------------------------------------------------
// FindSystemIndexForClass
// ---------------------------------------------------------------------------

int32 UNytwatchSubsystem::FindSystemIndexForClass(UClass* Class)
{
    if (int32* Cached = ClassSystemIndexCache.Find(Class))
        return *Cached;

    FString HeaderPath;
#if WITH_EDITOR
    if (!FSourceCodeNavigation::FindClassHeaderPath(Class, HeaderPath) || HeaderPath.IsEmpty())
    {
        // FindClassHeaderPath relies on an async database that may not be ready yet.
        // Fall back to deriving the path from the class package name — always synchronous.
        const FString PackageName = Class->GetOutermost()->GetName();
        if (FPackageName::IsValidLongPackageName(PackageName))
        {
            FPackageName::TryConvertLongPackageNameToFilename(PackageName, HeaderPath, TEXT(".h"));
            HeaderPath = FPaths::ConvertRelativePathToFull(HeaderPath);
        }
    }
    FPaths::NormalizeFilename(HeaderPath);
#endif

    int32 Result = INDEX_NONE;

    if (!HeaderPath.IsEmpty())
    {
        for (int32 i = 0; i < Config.ArmedSystems.Num(); ++i)
        {
            for (const FString& SysPath : Config.ArmedSystems[i].AbsolutePaths)
            {
                if (HeaderPath.StartsWith(SysPath))
                {
                    Result = i;
                    break;
                }
            }
            if (Result != INDEX_NONE) break;
        }
    }

    if (Result == INDEX_NONE)
    {
        if (HeaderPath.IsEmpty())
        {
            UE_LOG(LogNytwatchSubsystem, Verbose,
                TEXT("[NytwatchAgent] Class '%s' — FSourceCodeNavigation returned no header (skipped)."),
                *Class->GetName());
        }
        else
        {
            UE_LOG(LogNytwatchSubsystem, Verbose,
                TEXT("[NytwatchAgent] Class '%s' — header '%s' matched no armed system (skipped)."),
                *Class->GetName(), *HeaderPath);
        }
    }
    else
    {
        UE_LOG(LogNytwatchSubsystem, VeryVerbose,
            TEXT("[NytwatchAgent] Class '%s' → system[%d] '%s'  header: %s"),
            *Class->GetName(), Result, *Config.ArmedSystems[Result].SystemName, *HeaderPath);
    }

    ClassSystemIndexCache.Add(Class, Result);
    return Result;
}

// ---------------------------------------------------------------------------
// OnTick  (game thread, every Config.TickIntervalSeconds)
// ---------------------------------------------------------------------------

bool UNytwatchSubsystem::OnTick(float DeltaTime)
{
    if (!bTrackingActive) return true;

    PIEElapsedSeconds     += DeltaTime;
    TimeSinceConfigReload += DeltaTime;

    // Hot-reload config every 1 second so verbosity changes take effect
    // without requiring a PIE restart.  Also re-validates registered objects
    // in case a system was disarmed.
    if (TimeSinceConfigReload >= 1.0f)
    {
        TimeSinceConfigReload = 0.f;
        const FString ProjectDir =
            FPaths::ConvertRelativePathToFull(FPaths::ProjectDir());
        FNytwatchConfig NewConfig = FNytwatchConfig::Load(ProjectDir);
        if (NewConfig.bValid)
        {
            Config = NewConfig;

            // Re-resolve system indices: armed systems may have changed.
            ClassSystemIndexCache.Reset();
            for (int32 i = TrackedObjects.Num() - 1; i >= 0; --i)
            {
                UObject* Obj = TrackedObjects[i].Object.Get();
                if (!Obj || !IsValid(Obj))
                {
                    TrackedObjects.RemoveAtSwap(i);
                    continue;
                }
                const int32 NewIdx = FindSystemIndexForClass(Obj->GetClass());
                if (NewIdx == INDEX_NONE)
                    TrackedObjects.RemoveAtSwap(i); // system was disarmed
                else
                    TrackedObjects[i].SystemIdx = NewIdx;
            }
        }
    }

    if (Writer.IsCapReached()) return true;

    // Poll each registered object for property changes.
    TArray<FNytwatchEvent> Events;

    for (int32 i = TrackedObjects.Num() - 1; i >= 0; --i)
    {
        UObject* Obj = TrackedObjects[i].Object.Get();
        if (!Obj || !IsValid(Obj))
        {
            // Object was destroyed without calling UnregisterObject
            TrackedObjects.RemoveAtSwap(i);
            continue;
        }

        if (TrackedObjects[i].SystemIdx >= Config.ArmedSystems.Num()) continue;

        const FNytwatchSystemConfig& System = Config.ArmedSystems[TrackedObjects[i].SystemIdx];
        Events.Reset();

        Tracker.PollObject(Obj, System, PIEElapsedSeconds, Events);

        for (const FNytwatchEvent& Evt : Events)
        {
            Writer.AppendEvent(Evt);
            if (Writer.IsCapReached()) return true;
        }
    }

    return true; // keep ticking
}
