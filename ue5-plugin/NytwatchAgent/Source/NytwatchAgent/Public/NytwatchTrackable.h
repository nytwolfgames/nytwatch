#pragma once

#include "CoreMinimal.h"
#include "UObject/Interface.h"

#include "NytwatchTrackable.generated.h"

// ---------------------------------------------------------------------------
// Opt-in interface for classes that should be tracked by Nytwatch.
//
// Implementing this interface on a class does two things:
//   1. Registers instances with the Nytwatch subsystem so their properties
//      are polled each second — without this, even a correctly annotated
//      class will not be tracked.
//   2. Provides a per-instance enable/disable toggle via
//      IsNytwatchTrackingEnabled().
//
// ── Required wiring ────────────────────────────────────────────────────────
//
// In BeginPlay, call RegisterObject to add this instance to the tracked list:
//
//   #include "NytwatchSubsystem.h"
//
//   void AMyActor::BeginPlay()
//   {
//       Super::BeginPlay();
//   #if WITH_EDITOR
//       if (auto* NW = GEditor->GetEditorSubsystem<UNytwatchSubsystem>())
//           NW->RegisterObject(this);
//   #endif
//   }
//
// In EndPlay, unregister so stale entries are cleaned up immediately:
//
//   void AMyActor::EndPlay(const EEndPlayReason::Type Reason)
//   {
//   #if WITH_EDITOR
//       if (auto* NW = GEditor->GetEditorSubsystem<UNytwatchSubsystem>())
//           NW->UnregisterObject(this);
//   #endif
//       Super::EndPlay(Reason);
//   }
//
// Both calls are no-ops when tracking is not active (outside PIE, or when
// NytwatchConfig.json has status "Off"), so they are safe to leave in
// shipping builds as long as they are wrapped in #if WITH_EDITOR.
//
// ── NytwatchVerbosity annotation ───────────────────────────────────────────
//
// The class (or individual UPROPERTYs) must still carry a NytwatchVerbosity
// meta tag, otherwise no properties will be recorded even for registered
// instances:
//
//   UCLASS(meta=(NytwatchVerbosity="Standard"))
//   class AMyActor : public AActor, public INytwatchTrackable { ... };
//
// ── Per-instance toggle ─────────────────────────────────────────────────────
//
// Override IsNytwatchTrackingEnabled_Implementation() to expose a per-instance
// enable/disable toggle in the Details panel:
//
//   UPROPERTY(EditAnywhere, Category="Nytwatch")
//   bool bEnableNytwatchTrack = true;
//
//   virtual bool IsNytwatchTrackingEnabled_Implementation() const override
//   { return bEnableNytwatchTrack; }
//
// The default implementation returns true — adding the interface alone does
// not suppress tracking.
// ---------------------------------------------------------------------------

UINTERFACE(MinimalAPI, Blueprintable)
class UNytwatchTrackable : public UInterface
{
    GENERATED_BODY()
};

class NYTWATCHAGENT_API INytwatchTrackable
{
    GENERATED_BODY()

public:
    // Returns whether Nytwatch should track this specific instance.
    // Default implementation returns true.
    UFUNCTION(BlueprintNativeEvent, BlueprintCallable, Category = "Nytwatch")
    bool IsNytwatchTrackingEnabled() const;
    virtual bool IsNytwatchTrackingEnabled_Implementation() const { return true; }
};
