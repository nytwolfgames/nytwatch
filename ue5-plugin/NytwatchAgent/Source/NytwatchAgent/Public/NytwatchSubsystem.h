#pragma once

#include "CoreMinimal.h"
#include "EditorSubsystem.h"
#include "Containers/Ticker.h"
#include "NytwatchConfig.h"
#include "NytwatchPropertyTracker.h"
#include "NytwatchSessionWriter.h"

#include "NytwatchSubsystem.generated.h"

// ---------------------------------------------------------------------------
// Editor subsystem that owns the full PIE tracking lifecycle.
// Instantiated automatically by the editor — no manual registration needed.
// ---------------------------------------------------------------------------
UCLASS()
class NYTWATCHAGENT_API UNytwatchSubsystem : public UEditorSubsystem
{
    GENERATED_BODY()

public:
    // UEditorSubsystem interface
    virtual void Initialize(FSubsystemCollectionBase& Collection) override;
    virtual void Deinitialize() override;

private:
    // --- PIE delegates ------------------------------------------------------
    void OnBeginPIE(bool bIsSimulating);
    void OnEndPIE(bool bIsSimulating);

    // --- Tick ---------------------------------------------------------------
    // Returns true to keep ticking; false to unregister.
    bool OnTick(float DeltaTime);

    // --- Helpers ------------------------------------------------------------

    // Returns true if Obj should be considered for tracking at all
    // (CDO, transient, unreachable, self-exclusion checks).
    bool PassesBasicFilter(UObject* Obj) const;

    // Returns the index into Config.ArmedSystems that owns Obj's class,
    // or INDEX_NONE if none.  Result is cached in ClassSystemIndexCache.
    int32 FindSystemIndexForClass(UClass* Class);

    // --- State --------------------------------------------------------------
    FNytwatchConfig         Config;
    FNytwatchPropertyTracker Tracker;
    FNytwatchSessionWriter   Writer;

    FTSTicker::FDelegateHandle TickHandle;
    float PIEElapsedSeconds = 0.f;
    bool  bTrackingActive   = false;

    // Cache: UClass* → index in Config.ArmedSystems (-1 = not tracked).
    // Populated lazily during Tick; cleared at session start/end.
    TMap<UClass*, int32> ClassSystemIndexCache;
};
