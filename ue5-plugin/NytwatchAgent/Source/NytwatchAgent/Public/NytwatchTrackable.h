#pragma once

#include "CoreMinimal.h"
#include "UObject/Interface.h"

#include "NytwatchTrackable.generated.h"

// ---------------------------------------------------------------------------
// Optional interface for per-instance tracking control.
//
// This interface is NOT required for registration.  Any class that calls
// RegisterObject(this) from BeginPlay will be tracked, provided its source
// file falls within an armed system's paths and it carries a NytwatchVerbosity
// tag.  INytwatchTrackable is only needed when you want a per-instance
// enable/disable toggle.
//
// ── Registration (required, interface-independent) ─────────────────────────
//
// Add these two calls to any class you want tracked.  Use the static
// UNytwatchSubsystem::Get() accessor so your game code never has to
// reference GEditor or link UnrealEd directly:
//
//   // MyActor.h
//   virtual void BeginPlay() override;
//   virtual void EndPlay(const EEndPlayReason::Type Reason) override;
//
//   // MyActor.cpp
//   #if WITH_EDITOR
//   #include "NytwatchSubsystem.h"
//   #endif
//
//   void AMyActor::BeginPlay()
//   {
//       Super::BeginPlay();
//   #if WITH_EDITOR
//       if (auto* NW = UNytwatchSubsystem::Get())
//           NW->RegisterObject(this);
//   #endif
//   }
//
//   void AMyActor::EndPlay(const EEndPlayReason::Type Reason)
//   {
//   #if WITH_EDITOR
//       if (auto* NW = UNytwatchSubsystem::Get())
//           NW->UnregisterObject(this);
//   #endif
//       Super::EndPlay(Reason);
//   }
//
// Both calls are no-ops when tracking is not active, so they are safe to
// leave in your codebase wrapped in #if WITH_EDITOR.
//
// ── Per-instance toggle (optional, requires this interface) ────────────────
//
// Implement INytwatchTrackable only when you need to suppress tracking on
// specific instances at runtime.  Do NOT use #if WITH_EDITOR around the
// inheritance — add NytwatchAgent as an editor-only Build.cs dependency
// instead:
//
//   // ProjectAlpha.Build.cs
//   if (Target.bBuildEditor)
//       PrivateDependencyModuleNames.Add("NytwatchAgent");
//
//   // MyActor.h — no preprocessor in inheritance list (UHT limitation)
//   class AMyActor : public AActor, public INytwatchTrackable { ... }
//
//   // Per-instance Details-panel toggle
//   UPROPERTY(EditAnywhere, Category="Nytwatch")
//   bool bEnableNytwatchTrack = true;
//
//   virtual bool IsNytwatchTrackingEnabled_Implementation() const override
//   { return bEnableNytwatchTrack; }
//
// The default implementation returns true — implementing the interface alone
// has no effect on tracking.
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
