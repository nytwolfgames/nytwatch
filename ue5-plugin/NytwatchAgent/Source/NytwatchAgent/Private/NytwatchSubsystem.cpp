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
        Writer.Close(PIEElapsedSeconds);
        bTrackingActive = false;
    }

    // Flush any pending disconnect synchronously — the deferred ticker won't
    // fire after Deinitialize (subsystem is being torn down).
    Writer.FlushAndDisconnect();

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
    PIEElapsedSeconds       = 0.f;
    TimeSinceConfigReload   = 0.f;
    ConnectionWaitSeconds   = 0.f;
    bTrackingActive         = false;

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

    if (Config.WebSocketUrl.IsEmpty())
    {
        UE_LOG(LogNytwatchSubsystem, Warning,
            TEXT("[NytwatchAgent] No tracking_ws_url in config — tracking disabled."));
        return;
    }

    // 2. Open WebSocket writer (initiates async connection)
    Writer.Open(Config);
    if (!Writer.IsOpen())
    {
        UE_LOG(LogNytwatchSubsystem, Error,
            TEXT("[NytwatchAgent] Failed to open WebSocket writer — tracking disabled."));
        return;
    }

    // 3. Write lock file (PIE-active indicator for watchdog crash detection)
    const FString LockPath = FPaths::Combine(
        ProjectDir, TEXT("Saved"), TEXT("Nytwatch"), TEXT("nytwatch.lock"));
    const FString LockJson = FString::Printf(
        TEXT("{\"session_id\":\"%s\",\"started_at\":\"%s\",\"plugin_version\":\"%s\",\"pid\":%d}"),
        *Writer.GetSessionId(),
        *FDateTime::UtcNow().ToString(TEXT("%Y-%m-%dT%H:%M:%SZ")),
        TEXT(NYTWATCH_PLUGIN_VERSION),
        FPlatformProcess::GetCurrentProcessId());
    FFileHelper::SaveStringToFile(LockJson, *LockPath,
        FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM);

    // 4. Start tick — polling is deferred until Writer.IsReady() is true
    bTrackingActive = true;
    TickHandle = FTSTicker::GetCoreTicker().AddTicker(
        FTickerDelegate::CreateUObject(this, &UNytwatchSubsystem::OnTick),
        Config.TickIntervalSeconds);

    UE_LOG(LogNytwatchSubsystem, Log,
        TEXT("[NytwatchAgent] Session opening — %d system(s) armed, poll interval: %.2fs."),
        Config.ArmedSystems.Num(), Config.TickIntervalSeconds);
}

// ---------------------------------------------------------------------------
// OnEndPIE
// ---------------------------------------------------------------------------

void UNytwatchSubsystem::OnEndPIE(bool /*bIsSimulating*/)
{
    if (!bTrackingActive) return;

    FTSTicker::GetCoreTicker().RemoveTicker(TickHandle);
    bTrackingActive = false;

    // Send session_close.  Writer sets bPendingDisconnect — actual socket
    // close is deferred to OnDeferredDisconnect so the send can flush first.
    Writer.Close(PIEElapsedSeconds);

    // Delete lock file so the watchdog sees the clean end
    const FString ProjectDir =
        FPaths::ConvertRelativePathToFull(FPaths::ProjectDir());
    const FString LockPath = FPaths::Combine(
        ProjectDir, TEXT("Saved"), TEXT("Nytwatch"), TEXT("nytwatch.lock"));
    IFileManager::Get().Delete(*LockPath, false, true);

    UE_LOG(LogNytwatchSubsystem, Log,
        TEXT("[NytwatchAgent] Session closed — %d events, %d objects tracked."),
        Writer.GetTotalEventCount(), TrackedObjects.Num());

    TrackedObjects.Reset();
    Tracker.Reset();
    ClassSystemIndexCache.Reset();

    if (Writer.IsPendingDisconnect())
    {
        FTSTicker::GetCoreTicker().AddTicker(
            FTickerDelegate::CreateUObject(this, &UNytwatchSubsystem::OnDeferredDisconnect),
            0.1f);
    }
}

// ---------------------------------------------------------------------------
// OnDeferredDisconnect  (one-shot, fires ~100 ms after OnEndPIE)
// ---------------------------------------------------------------------------

bool UNytwatchSubsystem::OnDeferredDisconnect(float /*DeltaTime*/)
{
    Writer.FlushAndDisconnect();
    return false; // one-shot — do not re-register
}

// ---------------------------------------------------------------------------
// OnCrash  (crash-handler thread — keep it simple)
// ---------------------------------------------------------------------------

void UNytwatchSubsystem::OnCrash()
{
    if (!bTrackingActive) return;

    bTrackingActive = false;
    FTSTicker::GetCoreTicker().RemoveTicker(TickHandle);

    Writer.EmergencyClose(PIEElapsedSeconds);

    // Intentionally leave the lock file on disk so the watchdog's crash
    // poller sees the dead PID and can trigger orphan consolidation.

    UE_LOG(LogNytwatchSubsystem, Warning,
        TEXT("[NytwatchAgent] PIE hard close detected — session_close (crash) sent."));
}

// ---------------------------------------------------------------------------
// RegisterObject  (game thread, called from BeginPlay)
// ---------------------------------------------------------------------------

void UNytwatchSubsystem::RegisterObject(UObject* Obj)
{
    if (!bTrackingActive || !IsValid(Obj)) return;

    const int32 SysIdx = FindSystemIndexForClass(Obj->GetClass());
    if (SysIdx == INDEX_NONE) return;

    for (const FTrackedObject& T : TrackedObjects)
    {
        if (T.Object == Obj) return; // already registered
    }

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
    UClass* NativeClass = Class;
    while (NativeClass && !NativeClass->IsNative())
        NativeClass = NativeClass->GetSuperClass();

    if (NativeClass)
    {
        // Primary: ModuleRelativePath is baked in by UHT at compile time —
        // reliable and synchronous, unlike FSourceCodeNavigation which depends
        // on an async database that may not be ready at BeginPlay time.
        const FString ModuleRelPath = NativeClass->GetMetaData(TEXT("ModuleRelativePath"));
        if (!ModuleRelPath.IsEmpty())
        {
            // Derive module name from package: "/Script/ProjectAlpha" → "ProjectAlpha"
            FString ModuleName;
            NativeClass->GetOutermost()->GetName().Split(
                TEXT("/Script/"), nullptr, &ModuleName, ESearchCase::CaseSensitive);

            if (!ModuleName.IsEmpty())
            {
                const FString ProjectDir =
                    FPaths::ConvertRelativePathToFull(FPaths::ProjectDir());
                HeaderPath = FPaths::Combine(
                    ProjectDir, TEXT("Source"), ModuleName, ModuleRelPath);
            }
        }

        // Fallback: FSourceCodeNavigation (async — may return empty on first PIE).
        if (HeaderPath.IsEmpty())
        {
            FSourceCodeNavigation::FindClassHeaderPath(NativeClass, HeaderPath);
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
            UE_LOG(LogNytwatchSubsystem, Log,
                TEXT("[NytwatchAgent] Class '%s' — no header path (skipped)."),
                *Class->GetName());
        }
        else
        {
            UE_LOG(LogNytwatchSubsystem, Log,
                TEXT("[NytwatchAgent] Class '%s' — header '%s' matched no armed system (skipped)."),
                *Class->GetName(), *HeaderPath);
        }
    }
    else
    {
        UE_LOG(LogNytwatchSubsystem, Log,
            TEXT("[NytwatchAgent] Class '%s' → system[%d] '%s'  (header: %s)"),
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

    // ── Connection management ────────────────────────────────────────────────

    // Connection error: IsOpen() goes false when OnConnectionError fires.
    if (!Writer.IsOpen())
    {
        UE_LOG(LogNytwatchSubsystem, Warning,
            TEXT("[NytwatchAgent] WebSocket connection failed — tracking disabled for this session."));
        bTrackingActive = false;
        return true;
    }

    // Still waiting for the initial handshake.
    if (!Writer.IsReady())
    {
        ConnectionWaitSeconds += DeltaTime;
        if (ConnectionWaitSeconds > ConnectionTimeoutSeconds)
        {
            UE_LOG(LogNytwatchSubsystem, Warning,
                TEXT("[NytwatchAgent] WebSocket connection timed out after %.0fs — tracking disabled."),
                ConnectionWaitSeconds);
            bTrackingActive = false;
            Writer.Abort();
        }
        return true;
    }
    // First tick after connection established: log how many objects are queued
    // and prevent the hot-reload from firing immediately (TimeSinceConfigReload
    // accumulated during the connection wait and would flush the class cache on
    // the very first polling tick, potentially dropping all tracked objects).
    if (ConnectionWaitSeconds > 0.f)
    {
        UE_LOG(LogNytwatchSubsystem, Log,
            TEXT("[NytwatchAgent] WebSocket connected after %.1fs — %d object(s) queued for tracking."),
            ConnectionWaitSeconds, TrackedObjects.Num());
        TimeSinceConfigReload = 0.f;
    }
    ConnectionWaitSeconds = 0.f;

    // Reconnect after mid-session drop.
    if (Writer.NeedsReconnect())
    {
        Writer.TryReconnect();
        return true; // skip polling this tick; wait for OnConnected
    }

    // ── Config hot-reload ────────────────────────────────────────────────────
    if (TimeSinceConfigReload >= 1.0f)
    {
        TimeSinceConfigReload = 0.f;
        const FString ProjectDir =
            FPaths::ConvertRelativePathToFull(FPaths::ProjectDir());
        FNytwatchConfig NewConfig = FNytwatchConfig::Load(ProjectDir);
        if (NewConfig.bValid)
        {
            const int32 CountBefore = TrackedObjects.Num();
            Config = NewConfig;
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
                {
                    UE_LOG(LogNytwatchSubsystem, Warning,
                        TEXT("[NytwatchAgent] Hot-reload: '%s' no longer maps to any armed system — dropping."),
                        *Obj->GetName());
                    TrackedObjects.RemoveAtSwap(i);
                }
                else
                    TrackedObjects[i].SystemIdx = NewIdx;
            }
            if (TrackedObjects.Num() != CountBefore)
            {
                UE_LOG(LogNytwatchSubsystem, Log,
                    TEXT("[NytwatchAgent] Hot-reload: %d → %d tracked object(s)."),
                    CountBefore, TrackedObjects.Num());
            }
        }
    }

    if (Writer.IsCapReached()) return true;

    // ── Poll objects and send batch ──────────────────────────────────────────
    TArray<FNytwatchEvent> BatchEvents;

    for (int32 i = TrackedObjects.Num() - 1; i >= 0; --i)
    {
        UObject* Obj = TrackedObjects[i].Object.Get();
        if (!Obj || !IsValid(Obj))
        {
            TrackedObjects.RemoveAtSwap(i);
            continue;
        }

        if (TrackedObjects[i].SystemIdx >= Config.ArmedSystems.Num()) continue;

        const FNytwatchSystemConfig& System = Config.ArmedSystems[TrackedObjects[i].SystemIdx];
        TArray<FNytwatchEvent> ObjEvents;
        Tracker.PollObject(Obj, System, PIEElapsedSeconds, ObjEvents);
        BatchEvents.Append(ObjEvents);
    }

    if (BatchEvents.Num() > 0)
    {
        UE_LOG(LogNytwatchSubsystem, Verbose,
            TEXT("[NytwatchAgent] Sending batch: %d event(s), t=%.2fs, %d object(s) polled."),
            BatchEvents.Num(), PIEElapsedSeconds, TrackedObjects.Num());
        Writer.SendBatch(BatchEvents, PIEElapsedSeconds);
    }

    return true; // keep ticking
}
