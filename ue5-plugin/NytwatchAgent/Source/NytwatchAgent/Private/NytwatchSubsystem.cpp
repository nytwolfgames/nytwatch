#include "NytwatchSubsystem.h"

#include "Editor.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "Misc/DateTime.h"
#include "Misc/App.h"
#include "HAL/PlatformProcess.h"
#include "UObject/UObjectIterator.h"
#include "GameFramework/Actor.h"
#include "SourceCodeNavigation.h"

DEFINE_LOG_CATEGORY_STATIC(LogNytwatchSubsystem, Log, All);

// ---------------------------------------------------------------------------
// Initialize / Deinitialize
// ---------------------------------------------------------------------------

void UNytwatchSubsystem::Initialize(FSubsystemCollectionBase& Collection)
{
    Super::Initialize(Collection);

    FEditorDelegates::BeginPIE.AddUObject(this, &UNytwatchSubsystem::OnBeginPIE);
    FEditorDelegates::EndPIE.AddUObject(this, &UNytwatchSubsystem::OnEndPIE);

    UE_LOG(LogNytwatchSubsystem, Log,
        TEXT("[NytwatchAgent] Subsystem initialised."));
}

void UNytwatchSubsystem::Deinitialize()
{
    FEditorDelegates::BeginPIE.RemoveAll(this);
    FEditorDelegates::EndPIE.RemoveAll(this);

    // If the editor is shut down while PIE is active, clean up gracefully.
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
    // Reset any state from previous sessions
    Tracker.Reset();
    ClassSystemIndexCache.Reset();
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

    // 3. Open session writer
    Writer.Open(Config, ProjectDir);
    if (!Writer.IsOpen())
    {
        UE_LOG(LogNytwatchSubsystem, Error,
            TEXT("[NytwatchAgent] Failed to open session file — tracking disabled."));
        IFileManager::Get().Delete(*LockPath, false, true);
        return;
    }

    // Update lock with the real session ID now that the writer has generated it
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
        TEXT("[NytwatchAgent] Tracking session started. Armed: %d system(s)."),
        Config.ArmedSystems.Num());
}

// ---------------------------------------------------------------------------
// OnEndPIE
// ---------------------------------------------------------------------------

void UNytwatchSubsystem::OnEndPIE(bool /*bIsSimulating*/)
{
    if (!bTrackingActive) return;

    // Stop tick
    FTSTicker::GetCoreTicker().RemoveTicker(TickHandle);

    // Close session (flushes + backfills header + deletes lock)
    const FString ProjectDir =
        FPaths::ConvertRelativePathToFull(FPaths::ProjectDir());
    Writer.Close(ProjectDir);

    UE_LOG(LogNytwatchSubsystem, Log,
        TEXT("[NytwatchAgent] Session closed. %d events written to %s.md"),
        Writer.GetTotalEventCount(), *Writer.GetSessionId());

    bTrackingActive = false;
    Tracker.Reset();
    ClassSystemIndexCache.Reset();
}

// ---------------------------------------------------------------------------
// PassesBasicFilter
// ---------------------------------------------------------------------------

bool UNytwatchSubsystem::PassesBasicFilter(UObject* Obj) const
{
    if (!IsValid(Obj))                             return false; // RF_Unreachable / GC'd
    if (Obj->HasAnyFlags(RF_ClassDefaultObject))   return false;

    // Self-exclusion: skip objects whose class lives in this plugin
    const FString& PackageName = Obj->GetClass()->GetPackage()->GetName();
    if (PackageName == TEXT("/Script/NytwatchAgent")) return false;

    return true;
}

// ---------------------------------------------------------------------------
// FindSystemIndexForClass
// ---------------------------------------------------------------------------

int32 UNytwatchSubsystem::FindSystemIndexForClass(UClass* Class)
{
    if (int32* Cached = ClassSystemIndexCache.Find(Class))
    {
        return *Cached;
    }

    // Resolve header path via FSourceCodeNavigation (cached inside Tracker)
    FString HeaderPath;
#if WITH_EDITOR
    FSourceCodeNavigation::FindClassHeaderPath(Class, HeaderPath);
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

    // Log every new class lookup so mismatches are visible in the Output Log.
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
// OnTick
// ---------------------------------------------------------------------------

bool UNytwatchSubsystem::OnTick(float DeltaTime)
{
    if (!bTrackingActive) return false;

    PIEElapsedSeconds     += DeltaTime;
    TimeSinceConfigReload += DeltaTime;

    // Hot-reload NytwatchConfig.json every 1 second so the server can arm or
    // disarm systems mid-session without requiring a PIE restart.
    if (TimeSinceConfigReload >= 1.0f)
    {
        TimeSinceConfigReload = 0.f;
        const FString ProjectDir =
            FPaths::ConvertRelativePathToFull(FPaths::ProjectDir());
        FNytwatchConfig NewConfig = FNytwatchConfig::Load(ProjectDir);
        if (NewConfig.bValid)
        {
            Config = NewConfig;
            ClassSystemIndexCache.Reset(); // evict stale class→system mappings
        }
    }

    // If the session has hit the event cap, keep ticking (so we don't lose
    // the PIE end delegate) but skip all polling.
    if (Writer.IsCapReached()) return true;

    // ----------------------------------------------------------------
    // Collect candidates, separating previously-seen from new objects
    // so that seen objects are prioritised when the cap is applied.
    // ----------------------------------------------------------------
    struct FCandidate { UObject* Obj; int32 SystemIdx; };
    TArray<FCandidate> Seen, Unseen;

    for (TObjectIterator<UObject> It; It; ++It)
    {
        UObject* Obj = *It;
        if (!PassesBasicFilter(Obj)) continue;

        // Restrict to PIE game world — skip editor-world and transient objects
        UWorld* World = Obj->GetWorld();
        if (!World || !World->IsGameWorld()) continue;

        const int32 SysIdx = FindSystemIndexForClass(Obj->GetClass());
        if (SysIdx == INDEX_NONE) continue;

        // Respect per-instance toggle when the object implements INytwatchTrackable.
        if (Obj->GetClass()->ImplementsInterface(UNytwatchTrackable::StaticClass()))
        {
            if (!INytwatchTrackable::Execute_IsNytwatchTrackingEnabled(Obj))
                continue;
        }

        if (Tracker.HasSeen(Obj))
            Seen.Add({Obj, SysIdx});
        else
            Unseen.Add({Obj, SysIdx});
    }

    // Log candidate summary once every ~5 seconds so it's easy to confirm
    // objects are being found without flooding the Output Log every tick.
    static float LastDiagTime = -5.f;
    if (PIEElapsedSeconds - LastDiagTime >= 5.f)
    {
        LastDiagTime = PIEElapsedSeconds;
        UE_LOG(LogNytwatchSubsystem, Log,
            TEXT("[NytwatchAgent] Scan @ %.1fs — %d seen + %d new candidates (total events so far: %d)"),
            PIEElapsedSeconds, Seen.Num(), Unseen.Num(), Writer.GetTotalEventCount());
    }

    // ----------------------------------------------------------------
    // Poll within cap: seen objects first, then new ones.
    // ----------------------------------------------------------------
    int32 Remaining = Config.ObjectScanCap;
    TArray<FNytwatchEvent> Events;

    auto ProcessCandidate = [&](const FCandidate& C)
    {
        if (Remaining-- <= 0) return;

        const FNytwatchSystemConfig& System = Config.ArmedSystems[C.SystemIdx];
        Events.Reset();

        if (!Tracker.HasSeen(C.Obj))
        {
            Tracker.SnapshotObject(C.Obj, System);
        }
        else
        {
            Tracker.PollObject(C.Obj, System, PIEElapsedSeconds, Events);
            for (const FNytwatchEvent& Evt : Events)
            {
                Writer.AppendEvent(Evt);
                if (Writer.IsCapReached()) return;
            }
        }
    };

    for (const FCandidate& C : Seen)
    {
        ProcessCandidate(C);
        if (Writer.IsCapReached()) break;
    }

    if (!Writer.IsCapReached())
    {
        for (const FCandidate& C : Unseen)
        {
            ProcessCandidate(C);
            if (Writer.IsCapReached()) break;
        }
    }

    return true; // keep ticking
}
