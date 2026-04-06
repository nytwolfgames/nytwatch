#include "NytwatchConfig.h"

#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "Dom/JsonObject.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"
#include "Framework/Notifications/NotificationManager.h"
#include "Widgets/Notifications/SNotificationList.h"

DEFINE_LOG_CATEGORY_STATIC(LogNytwatchConfig, Log, All);

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

static void ParseSemver(const FString& V, int32& Major, int32& Minor, int32& Patch)
{
    TArray<FString> Parts;
    V.ParseIntoArray(Parts, TEXT("."));
    Major = Parts.IsValidIndex(0) ? FCString::Atoi(*Parts[0]) : 0;
    Minor = Parts.IsValidIndex(1) ? FCString::Atoi(*Parts[1]) : 0;
    Patch = Parts.IsValidIndex(2) ? FCString::Atoi(*Parts[2]) : 0;
}

ENytwatchVerbosity FNytwatchConfig::ParseVerbosityString(const FString& Str)
{
    if (Str == TEXT("Critical")) return ENytwatchVerbosity::Critical;
    if (Str == TEXT("Verbose"))  return ENytwatchVerbosity::Verbose;
    if (Str == TEXT("Ignore"))   return ENytwatchVerbosity::Ignore;
    return ENytwatchVerbosity::Standard; // default for "Standard" and unknown
}

// ---------------------------------------------------------------------------
// FNytwatchConfig::Load
// ---------------------------------------------------------------------------

FNytwatchConfig FNytwatchConfig::Load(const FString& ProjectDir)
{
    FNytwatchConfig Out;

    const FString ConfigPath = FPaths::Combine(
        ProjectDir, TEXT("Saved"), TEXT("Nytwatch"), TEXT("NytwatchConfig.json"));

    FString JsonStr;
    if (!FFileHelper::LoadFileToString(JsonStr, *ConfigPath))
    {
        UE_LOG(LogNytwatchConfig, Log,
            TEXT("[NytwatchAgent] NytwatchConfig.json not found at %s — tracking disabled."),
            *ConfigPath);
        return Out; // bValid = false
    }

    TSharedPtr<FJsonObject> Root;
    TSharedRef<TJsonReader<>> Reader = TJsonReaderFactory<>::Create(JsonStr);
    if (!FJsonSerializer::Deserialize(Reader, Root) || !Root.IsValid())
    {
        UE_LOG(LogNytwatchConfig, Error,
            TEXT("[NytwatchAgent] Failed to parse NytwatchConfig.json."));
        return Out;
    }

    // --- Version check -------------------------------------------------------
    Out.PluginCompatVersion = Root->GetStringField(TEXT("version"));

    constexpr const TCHAR* PluginVersion = TEXT(NYTWATCH_PLUGIN_VERSION);
    int32 CfgMaj, CfgMin, CfgPatch, PlgMaj, PlgMin, PlgPatch;
    ParseSemver(Out.PluginCompatVersion, CfgMaj, CfgMin, CfgPatch);
    ParseSemver(PluginVersion,           PlgMaj, PlgMin, PlgPatch);

    if (CfgMaj != PlgMaj)
    {
        const FString Msg = FString::Printf(
            TEXT("Nytwatch plugin version mismatch (config %s, plugin %s) — "
                 "re-run `nytwatch install-plugin`."),
            *Out.PluginCompatVersion, PluginVersion);

        UE_LOG(LogNytwatchConfig, Error, TEXT("[NytwatchAgent] %s"), *Msg);

        FNotificationInfo Info(FText::FromString(Msg));
        Info.bFireAndForget  = true;
        Info.ExpireDuration  = 12.0f;
        Info.bUseSuccessFailIcons = true;
        FSlateNotificationManager::Get().AddNotification(Info);
        return Out; // bValid = false
    }

    if (CfgMin != PlgMin)
    {
        UE_LOG(LogNytwatchConfig, Warning,
            TEXT("[NytwatchAgent] Minor version mismatch (config %s, plugin %s). "
                 "Continuing — consider re-running `nytwatch install-plugin`."),
            *Out.PluginCompatVersion, PluginVersion);
    }

    // --- Status --------------------------------------------------------------
    // If the server set status to "Off", return a valid but empty config so
    // the subsystem's "no armed systems" check handles it cleanly.
    const FString Status = Root->GetStringField(TEXT("status"));
    if (Status != TEXT("On"))
    {
        UE_LOG(LogNytwatchConfig, Log,
            TEXT("[NytwatchAgent] Tracking status is Off — session will not start."));
        Out.bValid = true; // config itself is fine; just no systems armed
        return Out;
    }

    // --- Global settings -----------------------------------------------------
    Out.ObjectScanCap       = (int32)Root->GetNumberField(TEXT("object_scan_cap"));
    Out.TickIntervalSeconds = (float)Root->GetNumberField(TEXT("tick_interval_seconds"));
    Out.WebSocketUrl        = Root->GetStringField(TEXT("tracking_ws_url"));
    if (Out.ObjectScanCap <= 0)       Out.ObjectScanCap       = 2000;
    if (Out.TickIntervalSeconds <= 0) Out.TickIntervalSeconds = 0.1f;

    // --- Armed systems -------------------------------------------------------
    const TArray<TSharedPtr<FJsonValue>>* SystemsArr;
    if (Root->TryGetArrayField(TEXT("armed_systems"), SystemsArr))
    {
        for (const TSharedPtr<FJsonValue>& SysVal : *SystemsArr)
        {
            const TSharedPtr<FJsonObject>* SysObj;
            if (!SysVal->TryGetObject(SysObj)) continue;

            FNytwatchSystemConfig Sys;
            Sys.SystemName      = (*SysObj)->GetStringField(TEXT("name"));
            Sys.SystemVerbosity = ParseVerbosityString(
                (*SysObj)->GetStringField(TEXT("system_verbosity")));

            // Absolute paths for this system
            const TArray<TSharedPtr<FJsonValue>>* PathsArr;
            if ((*SysObj)->TryGetArrayField(TEXT("paths"), PathsArr))
            {
                for (const TSharedPtr<FJsonValue>& PV : *PathsArr)
                {
                    FString P = PV->AsString();
                    FPaths::NormalizeFilename(P);
                    // Ensure trailing slash for StartsWith matching
                    if (!P.EndsWith(TEXT("/"))) P += TEXT("/");
                    Sys.AbsolutePaths.Add(P);
                }
            }

            // Per-file verbosity overrides
            const TSharedPtr<FJsonObject>* OverridesObj;
            if ((*SysObj)->TryGetObjectField(TEXT("file_overrides"), OverridesObj))
            {
                for (const auto& KV : (*OverridesObj)->Values)
                {
                    FString NormPath = KV.Key;
                    FPaths::NormalizeFilename(NormPath);
                    Sys.FileOverrides.Add(NormPath,
                        ParseVerbosityString(KV.Value->AsString()));
                }
            }

            Out.ArmedSystems.Add(MoveTemp(Sys));
        }
    }

    Out.bValid = true;
    return Out;
}
