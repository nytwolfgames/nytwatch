#pragma once

#include "CoreMinimal.h"
#include "NytwatchConfig.h"
#include "UObject/ObjectKey.h"

// ---------------------------------------------------------------------------
// A single property-change event emitted during a poll.
// ---------------------------------------------------------------------------
struct FNytwatchEvent
{
    FName   SystemName;    // interned — no per-event allocation
    FString ObjectName;    // unique instance name — must stay FString
    FName   ClassName;     // interned
    FName   PropertyName;  // interned
    FString OldValue;      // serialised via FProperty::ExportText_Direct
    FString NewValue;
    float   TimeSeconds = 0.f;
    bool    bIsNumeric  = false; // true when Property->IsA<FNumericProperty>()
};

// ---------------------------------------------------------------------------
// Tracks UProperty snapshots across ticks and emits change events.
// One instance per PIE session — Reset() between sessions.
// ---------------------------------------------------------------------------
class FNytwatchPropertyTracker
{
public:
    void Reset();

    // Record the current property values of Obj without emitting events.
    // Call once when an object is first encountered.
    void SnapshotObject(UObject* Obj, const FNytwatchSystemConfig& System);

    // Diff current property values against the snapshot; emit events for any
    // that changed; update the snapshot.
    void PollObject(UObject* Obj, const FNytwatchSystemConfig& System,
                    float PIETimeSeconds, TArray<FNytwatchEvent>& OutEvents);

    // Returns true if this object has already been snapshotted this session.
    bool HasSeen(UObject* Obj) const;

private:
    // Key: "ObjectName::ClassName::PropertyName"  Value: last serialised value
    TMap<FString, FString> Snapshot;

    // GC-safe set of objects seen this session (used for cap prioritisation).
    TSet<FObjectKey> SeenObjects;

    // Per-UClass cache: absolute header path, resolved once per class.
    // Empty string = FSourceCodeNavigation returned nothing.
    TMap<UClass*, FString> ClassHeaderCache;

    static FString MakeSnapshotKey(UObject* Obj, FProperty* Prop);
    static FString SerializeProperty(FProperty* Prop, UObject* Obj);

    // Resolve the NytwatchVerbosity tier of a property.
    // Checks UPROPERTY meta, then walks the class hierarchy for UCLASS meta.
    // Returns Ignore if nothing is found all the way up to (but not including) UObject.
    static ENytwatchVerbosity ResolvePropertyVerbosity(FProperty* Prop);

    // Resolve the effective file filter for a given owning class within a system.
    // Checks per-file overrides first, then falls back to the system verbosity.
    FString GetOrCacheHeaderPath(UClass* Class);
    ENytwatchVerbosity ResolveFileFilter(UClass* OwnerClass,
                                         const FNytwatchSystemConfig& System);

    bool ShouldLogProperty(FProperty* Prop, UClass* OwnerClass,
                           const FNytwatchSystemConfig& System);
};
