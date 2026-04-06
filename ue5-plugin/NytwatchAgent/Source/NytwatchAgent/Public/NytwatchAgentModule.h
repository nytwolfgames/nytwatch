#pragma once

#include "Modules/ModuleManager.h"

class FNytwatchAgentModule : public IModuleInterface
{
public:
    virtual void StartupModule() override;
    virtual void ShutdownModule() override;
};
