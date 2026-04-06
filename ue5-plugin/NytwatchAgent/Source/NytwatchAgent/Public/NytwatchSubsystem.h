#pragma once

#include "CoreMinimal.h"
#include "EditorSubsystem.h"
#include "Containers/Ticker.h"
#include "NytwatchConfig.h"
#include "NytwatchPropertyTracker.h"
#include "NytwatchSessionWriter.h"
#include "NytwatchTrackable.h"
#include "INytwatchTimeProvider.h"

#include "NytwatchSubsystem.generated.h"

// ---------------------------------------------------------------------------
// Editor subsystem that owns the full PIE tracking lifecycle.
// Instantiated automatically by the editor — no manual registration needed.
//
// Object registration
// ───────────────────
// Instead of scanning all UObjects every tick, the subsystem maintains an
// explicit list of registered objects.  Classes that implement
// INytwatchTrackable must call RegisterObject / UnregisterObject from their
// BeginPlay / EndPlay implementations (see NytwatchTrackable.h for the
// full code snippet).
//
// Every second (configurable via NytwatchConfig.json "tick_interval_seconds")
// the subsystem polls each registered object for UPROPERTY changes and
// sends them to the Nytwatch server via WebSocket.
// ---------------------------------------------------------------------------
UCLASS()
class NYTWATCHAGENT_API UNytwatchSubsystem : public UEditorSubsystem
{
    GENERATED_BODY()

public:
    // Convenience getter — returns the subsystem or nullptr outside the editor.
    // Use this from game code so GEditor never needs to be referenced directly:
    //
    //   if (auto* NW = UNytwatchSubsystem::Get())
    //       NW->RegisterObject(this);
    //
    static UNytwatchSubsystem* Get();

    // UEditorSubsystem interface
    virtual void Initialize(FSubsystemCollectionBase& Collection) override;
    virtual void Deinitialize() override;

    // ── Registration API (called from game-thread BeginPlay / EndPlay) ───────

    // Add Obj to the tracked list.
    // Eligibility is based entirely on whether the class's source file falls
    // within an armed system's paths — no interface or annotation check.
    // No-op when tracking is not active (outside PIE, or status "Off").
    // Must be called on the game thread.
    UFUNCTION()
    void RegisterObject(UObject* Obj);

    // Remove Obj from the tracked list and clean up its snapshot data.
    // Must be called on the game thread, while Obj is still valid.
    UFUNCTION()
    void UnregisterObject(UObject* Obj);

    // ── Causality / adapter API ──────────────────────────────────────────────

    // Set the active game-time provider.  Pass nullptr to revert to wall-clock
    // seconds.  The adapter for the outgoing gameplay mode should clear this
    // before the next adapter sets it, or check GetTimeProvider() == this first
    // to avoid overwriting a provider already set by the incoming mode.
    void SetTimeProvider(INytwatchTimeProvider* Provider);
    INytwatchTimeProvider* GetTimeProvider() const { return TimeProvider; }

    // Schedule a deferred poll on the next tick with no named event.
    // Use this from routine game-time tick handlers (e.g. DayPassed) so that
    // the poll captures fully-settled post-tick state rather than partial state
    // mid-broadcast.
    void RequestDeferredPoll();

    // Schedule a deferred poll on the next tick attributed to a named event.
    // NarrativeHeader is written verbatim as the output block header.
    // AffectedActors determines which polled objects are grouped under this
    // event; all other changed objects fall into the routine block.
    // Safe to call multiple times per frame — each call enqueues one event.
    void LogEvent(const FString& NarrativeHeader, const TArray<UObject*>& AffectedActors);

private:
    // ── PIE delegates ────────────────────────────────────────────────────────
    void OnBeginPIE(bool bIsSimulating);
    void OnEndPIE(bool bIsSimulating);

    // ── Crash / hard-close handler ───────────────────────────────────────────
    // Bound to FCoreDelegates::OnHandleSystemError.  Performs a best-effort
    // emergency session close so the session file is not left with unfilled
    // header placeholders after a PIE crash.
    void OnCrash();

    // ── Tick ─────────────────────────────────────────────────────────────────
    // Returns true to keep ticking.
    bool OnTick(float DeltaTime);

    // One-shot ticker fired ~100 ms after OnEndPIE to give the WebSocket send
    // buffer time to flush the session_close message before the socket closes.
    bool OnDeferredDisconnect(float DeltaTime);

    // ── Class → system index resolution ─────────────────────────────────────
    // Returns the index into Config.ArmedSystems that owns the class,
    // or INDEX_NONE if none.  Result is cached in ClassSystemIndexCache.
    // Called once per class at RegisterObject time, not during polling.
    int32 FindSystemIndexForClass(UClass* Class);

    // ── Deferred poll ────────────────────────────────────────────────────────
    // Executes the pending deferred poll: polls all tracked objects once,
    // groups their deltas by event context, and sends one batch per context.
    // Called from OnTick() when bDeferredPollPending is true.
    void RunDeferredPoll();

    // ── Tracked object list ──────────────────────────────────────────────────
    struct FTrackedObject
    {
        TWeakObjectPtr<UObject> Object;
        int32                   SystemIdx;
    };
    TArray<FTrackedObject> TrackedObjects;

    // ── Pending event queue (populated by LogEvent / RequestDeferredPoll) ────
    struct FNytwatchPendingEvent
    {
        FString            NarrativeHeader;   // empty = routine poll block
        TArray<FObjectKey> AffectedActorKeys; // GC-safe; determines output grouping
    };
    TArray<FNytwatchPendingEvent> PendingEvents;
    bool bDeferredPollPending = false;

    // ── State ────────────────────────────────────────────────────────────────
    FNytwatchConfig          Config;
    FNytwatchPropertyTracker Tracker;
    FNytwatchSessionWriter   Writer;

    // Optional game-time provider set by the adapter.  Null = wall-clock fallback.
    INytwatchTimeProvider* TimeProvider = nullptr;

    FTSTicker::FDelegateHandle TickHandle;
    FDelegateHandle            CrashDelegateHandle;
    float PIEElapsedSeconds     = 0.f;
    float TimeSinceConfigReload = 0.f;
    bool  bTrackingActive       = false;

    // Time spent waiting for the WebSocket handshake (seconds).
    // If this exceeds ConnectionTimeoutSeconds the session is aborted.
    float ConnectionWaitSeconds = 0.f;
    static constexpr float ConnectionTimeoutSeconds = 30.f;

    // Cache: UClass* → index in Config.ArmedSystems (-1 = no match).
    // Populated lazily on RegisterObject; cleared and re-resolved on
    // config hot-reload.
    TMap<UClass*, int32> ClassSystemIndexCache;
};
