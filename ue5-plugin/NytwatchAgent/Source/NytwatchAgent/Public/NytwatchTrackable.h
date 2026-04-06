#pragma once

#include "CoreMinimal.h"
#include "UObject/Interface.h"

#include "NytwatchTrackable.generated.h"

// ---------------------------------------------------------------------------
// Opt-in interface for per-instance tracking control.
//
// Add to any UCLASS that already carries a NytwatchVerbosity tag to gain a
// per-instance enable/disable toggle.
//
// Usage (C++):
//   UCLASS(meta=(NytwatchVerbosity="Standard"))
//   class AMyActor : public AActor, public INytwatchTrackable { ... };
//
//   Override IsNytwatchTrackingEnabled_Implementation() and return a
//   UPROPERTY bool to expose a Details-panel toggle per instance.
//
// Usage (Blueprint):
//   Implement the interface on a Blueprint class and override
//   IsNytwatchTrackingEnabled to drive tracking from a variable.
//
// Default behaviour: returns true — implementing the interface alone does
// not affect tracking.
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
