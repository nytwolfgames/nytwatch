#pragma once

#include "CoreMinimal.h"
#include "EditorSubsystem.h"
#include "Containers/Ticker.h"
#include "NytwatchConfig.h"
#include "NytwatchPropertyTracker.h"
#include "NytwatchSessionWriter.h"
#include "NytwatchTrackable.h"

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
// pushes any changes to the background writer thread.
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

private:
    // ── PIE delegates ────────────────────────────────────────────────────────
    void OnBeginPIE(bool bIsSimulating);
    void OnEndPIE(bool bIsSimulating);

    // ── Tick ─────────────────────────────────────────────────────────────────
    // Returns true to keep ticking.
    bool OnTick(float DeltaTime);

    // ── Class → system index resolution ─────────────────────────────────────
    // Returns the index into Config.ArmedSystems that owns the class,
    // or INDEX_NONE if none.  Result is cached in ClassSystemIndexCache.
    // Called once per class at RegisterObject time, not during polling.
    int32 FindSystemIndexForClass(UClass* Class);

    // ── Tracked object list ──────────────────────────────────────────────────
    struct FTrackedObject
    {
        TWeakObjectPtr<UObject> Object;
        int32                   SystemIdx;
    };
    TArray<FTrackedObject> TrackedObjects;

    // ── State ────────────────────────────────────────────────────────────────
    FNytwatchConfig          Config;
    FNytwatchPropertyTracker Tracker;
    FNytwatchSessionWriter   Writer;

    FTSTicker::FDelegateHandle TickHandle;
    float PIEElapsedSeconds     = 0.f;
    float TimeSinceConfigReload = 0.f;
    bool  bTrackingActive       = false;

    // Cache: UClass* → index in Config.ArmedSystems (-1 = no match).
    // Populated lazily on RegisterObject; cleared and re-resolved on
    // config hot-reload.
    TMap<UClass*, int32> ClassSystemIndexCache;
};
