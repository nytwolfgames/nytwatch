#include "NytwatchPropertyTracker.h"

#include "UObject/UnrealType.h"
#include "UObject/ObjectPtr.h"
#include "Misc/Paths.h"
#include "SourceCodeNavigation.h"  // FSourceCodeNavigation (UnrealEd)
#include "Misc/PackageName.h"

DEFINE_LOG_CATEGORY_STATIC(LogNytwatchTracker, Log, All);

// ---------------------------------------------------------------------------
// Internal helpers
// ---------------------------------------------------------------------------

static const FName NytwatchVerbosityMeta(TEXT("NytwatchVerbosity"));

// Serialize a single property value to a string.
// FObjectPropertyBase references are serialised as the referenced object's
// name rather than the full object path.
static FString SerializePropertyValue(FProperty* Prop, UObject* Obj)
{
    const void* ValuePtr = Prop->ContainerPtrToValuePtr<void>(Obj);

    // Object references → just the name of the referenced object
    if (const FObjectPropertyBase* ObjPropBase = CastField<FObjectPropertyBase>(Prop))
    {
        UObject* Ref = ObjPropBase->GetObjectPropertyValue(ValuePtr);
        return Ref ? Ref->GetName() : TEXT("None");
    }

    FString Out;
    Prop->ExportText_Direct(Out, ValuePtr, nullptr, Obj, PPF_None);
    return Out;
}

// Parse a verbosity string to the enum.
static ENytwatchVerbosity ParseVerbosityMeta(const FString& Str)
{
    if (Str == TEXT("Critical")) return ENytwatchVerbosity::Critical;
    if (Str == TEXT("Verbose"))  return ENytwatchVerbosity::Verbose;
    if (Str == TEXT("Ignore"))   return ENytwatchVerbosity::Ignore;
    return ENytwatchVerbosity::Standard;
}

// ---------------------------------------------------------------------------
// FNytwatchPropertyTracker
// ---------------------------------------------------------------------------

void FNytwatchPropertyTracker::Reset()
{
    Snapshot.Reset();
    SeenObjects.Reset();
    ClassHeaderCache.Reset();
}

bool FNytwatchPropertyTracker::HasSeen(UObject* Obj) const
{
    return SeenObjects.Contains(FObjectKey(Obj));
}

void FNytwatchPropertyTracker::RemoveObject(UObject* Obj)
{
    SeenObjects.Remove(FObjectKey(Obj));

    // Snapshot keys have the form "ObjectName::ClassName::PropertyName".
    // Remove every key that belongs to this object.
    const FString Prefix = FString::Printf(TEXT("%s::%s::"),
        *Obj->GetName(), *Obj->GetClass()->GetName());

    TArray<FString> ToRemove;
    for (const auto& KV : Snapshot)
    {
        if (KV.Key.StartsWith(Prefix))
            ToRemove.Add(KV.Key);
    }
    for (const FString& Key : ToRemove)
        Snapshot.Remove(Key);
}

FString FNytwatchPropertyTracker::MakeSnapshotKey(UObject* Obj, FProperty* Prop)
{
    return FString::Printf(TEXT("%s::%s::%s"),
        *Obj->GetName(),
        *Obj->GetClass()->GetName(),
        *Prop->GetName());
}

FString FNytwatchPropertyTracker::SerializeProperty(FProperty* Prop, UObject* Obj)
{
    return SerializePropertyValue(Prop, Obj);
}

// ---------------------------------------------------------------------------
// Verbosity resolution
// ---------------------------------------------------------------------------

ENytwatchVerbosity FNytwatchPropertyTracker::ResolvePropertyVerbosity(FProperty* Prop)
{
    // 1. UPROPERTY-level meta tag takes priority
#if WITH_EDITOR
    if (Prop->HasMetaData(NytwatchVerbosityMeta))
    {
        return ParseVerbosityMeta(Prop->GetMetaData(NytwatchVerbosityMeta));
    }
#endif

    // 2. Walk class hierarchy from the declaring class upward.
    //    Stop before UObject::StaticClass() — if nothing found, return Ignore.
    UClass* Class = CastField<FObjectPropertyBase>(Prop)
        ? nullptr  // skip hierarchy walk for bare object props; use Ignore
        : Prop->GetOwnerClass();

    while (Class && Class != UObject::StaticClass())
    {
#if WITH_EDITOR
        if (Class->HasMetaData(NytwatchVerbosityMeta))
        {
            return ParseVerbosityMeta(Class->GetMetaData(NytwatchVerbosityMeta));
        }
#endif
        Class = Class->GetSuperClass();
    }

    // 3. No tag found anywhere — default to Ignore (per project convention).
    return ENytwatchVerbosity::Ignore;
}

FString FNytwatchPropertyTracker::GetOrCacheHeaderPath(UClass* Class)
{
    if (FString* Cached = ClassHeaderCache.Find(Class))
    {
        return *Cached;
    }

    FString HeaderPath;
#if WITH_EDITOR
    // Blueprint classes have no source header; walk up to the nearest native
    // C++ ancestor so the path can be matched against armed system directories.
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

    ClassHeaderCache.Add(Class, HeaderPath);
    return HeaderPath;
}

ENytwatchVerbosity FNytwatchPropertyTracker::ResolveFileFilter(
    UClass* OwnerClass, const FNytwatchSystemConfig& System)
{
    const FString HeaderPath = GetOrCacheHeaderPath(OwnerClass);
    if (!HeaderPath.IsEmpty())
    {
        if (const ENytwatchVerbosity* Override = System.FileOverrides.Find(HeaderPath))
        {
            return *Override;
        }
    }
    return System.SystemVerbosity;
}

bool FNytwatchPropertyTracker::ShouldLogProperty(
    FProperty* Prop, UClass* OwnerClass, const FNytwatchSystemConfig& System)
{
    const ENytwatchVerbosity PropTier   = ResolvePropertyVerbosity(Prop);
    if (PropTier == ENytwatchVerbosity::Ignore) return false;

    const ENytwatchVerbosity FilterTier = ResolveFileFilter(OwnerClass, System);
    if (FilterTier == ENytwatchVerbosity::Ignore) return false;

    // Log if property tier is at or below (more severe than) the filter threshold.
    return static_cast<uint8>(PropTier) <= static_cast<uint8>(FilterTier);
}

// ---------------------------------------------------------------------------
// SnapshotObject
// ---------------------------------------------------------------------------

void FNytwatchPropertyTracker::SnapshotObject(
    UObject* Obj, const FNytwatchSystemConfig& System)
{
    SeenObjects.Add(FObjectKey(Obj));

    for (TFieldIterator<FProperty> It(Obj->GetClass(), EFieldIteratorFlags::IncludeSuper); It; ++It)
    {
        FProperty* Prop = *It;
        UClass* OwnerClass = Prop->GetOwnerClass();
        if (!OwnerClass) continue;

        if (!ShouldLogProperty(Prop, OwnerClass, System)) continue;

        const FString Key   = MakeSnapshotKey(Obj, Prop);
        const FString Value = SerializeProperty(Prop, Obj);
        Snapshot.Add(Key, Value);
    }
}

// ---------------------------------------------------------------------------
// PollObject
// ---------------------------------------------------------------------------

void FNytwatchPropertyTracker::PollObject(
    UObject* Obj, const FNytwatchSystemConfig& System,
    float PIETimeSeconds, TArray<FNytwatchEvent>& OutEvents)
{
    SeenObjects.Add(FObjectKey(Obj));

    for (TFieldIterator<FProperty> It(Obj->GetClass(), EFieldIteratorFlags::IncludeSuper); It; ++It)
    {
        FProperty* Prop = *It;
        UClass* OwnerClass = Prop->GetOwnerClass();
        if (!OwnerClass) continue;

        if (!ShouldLogProperty(Prop, OwnerClass, System)) continue;

        const FString Key          = MakeSnapshotKey(Obj, Prop);
        const FString CurrentValue = SerializeProperty(Prop, Obj);

        FString* PrevValue = Snapshot.Find(Key);
        if (!PrevValue)
        {
            // First time seeing this property — just record the baseline.
            Snapshot.Add(Key, CurrentValue);
            continue;
        }

        if (*PrevValue != CurrentValue)
        {
            FNytwatchEvent Evt;
            Evt.SystemName   = FName(*System.SystemName);
            Evt.ObjectName   = Obj->GetName();
            Evt.ClassName    = FName(*Obj->GetClass()->GetName());
            Evt.PropertyName = FName(*Prop->GetName());
            Evt.OldValue     = *PrevValue;
            Evt.NewValue     = CurrentValue;
            Evt.TimeSeconds  = PIETimeSeconds;
            Evt.bIsNumeric   = Prop->IsA<FNumericProperty>();

            OutEvents.Add(MoveTemp(Evt));
            *PrevValue = CurrentValue;
        }
    }
}
