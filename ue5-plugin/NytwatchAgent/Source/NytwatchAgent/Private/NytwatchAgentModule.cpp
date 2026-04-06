#include "NytwatchAgentModule.h"
#include "Modules/ModuleManager.h"

DEFINE_LOG_CATEGORY_STATIC(LogNytwatchAgent, Log, All);

#define LOCTEXT_NAMESPACE "FNytwatchAgentModule"

void FNytwatchAgentModule::StartupModule()
{
    UE_LOG(LogNytwatchAgent, Log,
        TEXT("[NytwatchAgent] Plugin loaded — version " NYTWATCH_PLUGIN_VERSION));
}

void FNytwatchAgentModule::ShutdownModule()
{
}

#undef LOCTEXT_NAMESPACE

IMPLEMENT_MODULE(FNytwatchAgentModule, NytwatchAgent)
