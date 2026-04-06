using UnrealBuildTool;

public class NytwatchAgent : ModuleRules
{
    public NytwatchAgent(ReadOnlyTargetRules Target) : base(Target)
    {
        PCHUsage = ModuleRules.PCHUsageMode.UseExplicitOrSharedPCHs;

        PublicDependencyModuleNames.AddRange(new string[]
        {
            "Core",
            "CoreUObject",
            "Engine",
        });

        PrivateDependencyModuleNames.AddRange(new string[]
        {
            "UnrealEd",          // FEditorDelegates
            "EditorSubsystem",   // UEditorSubsystem
            "SourceCodeAccess",  // FSourceCodeNavigation (FindClassHeaderPath)
            "Json",              // TJsonReader / TJsonWriter
            "JsonUtilities",     // FJsonObjectConverter
            "Slate",             // FSlateNotificationManager
            "SlateCore",
        });

        PublicDefinitions.Add("NYTWATCH_PLUGIN_VERSION=\"1.0.0\"");
    }
}
