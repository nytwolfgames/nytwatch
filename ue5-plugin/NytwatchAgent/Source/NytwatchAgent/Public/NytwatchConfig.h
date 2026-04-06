#pragma once

#include "CoreMinimal.h"

// ---------------------------------------------------------------------------
// Verbosity tiers — ordered so that (uint8)Tier <= (uint8)Filter means "log".
// Ignore is a special value that bypasses the filter entirely (never logged).
// ---------------------------------------------------------------------------
enum class ENytwatchVerbosity : uint8
{
    Critical = 0,
    Standard = 1,
    Verbose  = 2,
    Ignore   = 3,
};

// ---------------------------------------------------------------------------
// Per-system configuration read from NytwatchConfig.json
// ---------------------------------------------------------------------------
struct FNytwatchSystemConfig
{
    FString SystemName;

    // Default logging filter for all files in this system.
    ENytwatchVerbosity SystemVerbosity = ENytwatchVerbosity::Standard;

    // Per-file overrides keyed by absolute (normalised) file path.
    TMap<FString, ENytwatchVerbosity> FileOverrides;

    // Absolute directory paths that belong to this system.
    TArray<FString> AbsolutePaths;
};

// ---------------------------------------------------------------------------
// Full config loaded from NytwatchConfig.json
// ---------------------------------------------------------------------------
struct FNytwatchConfig
{
    FString PluginCompatVersion;
    TArray<FNytwatchSystemConfig> ArmedSystems;
    int32  ObjectScanCap       = 2000;  // retained for config compatibility; no longer used by the subsystem
    float  TickIntervalSeconds = 1.0f;  // poll registered objects once per second
    bool   bValid              = false;

    // Reads <ProjectDir>/Saved/Nytwatch/NytwatchConfig.json.
    // Returns bValid=false on missing file, parse error, or major version mismatch.
    // If status == "Off" the returned config has an empty ArmedSystems list.
    static FNytwatchConfig Load(const FString& ProjectDir);

private:
    static ENytwatchVerbosity ParseVerbosityString(const FString& Str);
};
