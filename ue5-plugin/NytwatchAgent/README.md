# NytwatchAgent — UE5 Plugin

**Version:** 1.0.0 | **Type:** Editor plugin | **UE requirement:** UE 5.1+

NytwatchAgent is an Unreal Engine 5 Editor plugin that records `UPROPERTY` value changes on your game objects during Play In Editor (PIE) sessions and writes them to structured session files. The Nytwatch server picks up these files automatically and makes them available in the **Gameplay Tracker** dashboard, where you can browse, bookmark, and send sessions to Claude for analysis.

---

## How it works

```
PIE starts
    │
    ▼
Plugin reads NytwatchConfig.json from Saved/Nytwatch/
    │  (written by the Nytwatch server when you arm systems)
    ▼
Actors call RegisterObject(this) from their BeginPlay
    │  Plugin records the initial property snapshot for each registered object
    ▼
Every second — game thread polls registered objects for UPROPERTY changes
    │  Changed values are pushed to a lock-free queue (no file I/O on game thread)
    ▼
Background writer thread drains the queue, formats events, appends to .md file
    │
PIE ends — game thread signals writer thread to flush and stop, then waits
    ▼
Session file finalised at Saved/Nytwatch/Sessions/<uuid>.md
    │
Nytwatch server detects the new file via filesystem watch
    ▼
Session appears in the Gameplay Tracker dashboard
```

Communication between the plugin and the server is **entirely filesystem-based** — no network connection, no sockets.

### Threading model

The plugin splits work across two threads to minimise game-thread impact:

| Thread | Responsibility |
|---|---|
| **Game thread** | `RegisterObject` / `UnregisterObject`, 1 s property poll, enqueue changed values |
| **Background writer thread** | Dequeue events, format markdown, append to session file |

The game thread never touches the disk after `Open()`. All formatting and I/O happen on the writer thread.

---

## Installation

### Recommended — via Nytwatch CLI

```bash
nytwatch install-plugin --project /path/to/your/ue5-project
```

This copies the plugin into `<project>/Plugins/NytwatchAgent/` and patches your `.uproject` to enable it. Open the project in the Unreal Editor and recompile when prompted.

To reinstall over an existing copy:

```bash
nytwatch install-plugin --project /path/to/your/ue5-project --force
```

### Interactive — via install script (Windows)

```powershell
.\scripts\windows\install-plugin.ps1
```

Presents a menu of all projects configured in Nytwatch and installs into your selection.

### Manual

1. Copy the `NytwatchAgent` folder into `<your-project>/Plugins/NytwatchAgent/`
2. Add the following entry to your `.uproject` under `"Plugins"`:
   ```json
   { "Name": "NytwatchAgent", "Enabled": true }
   ```
3. Open the project in the Unreal Editor and recompile when prompted.

---

## Arming systems

The plugin only tracks systems you have explicitly armed. Arming is done from the **Nytwatch dashboard**.

1. Start the Nytwatch server: `nytwatch serve`
2. Open `http://127.0.0.1:8420/tracker` in your browser
3. Switch to the **Systems** tab
4. Toggle **Armed** on the systems you want to track
5. Set the **verbosity** level for each armed system (see [Verbosity levels](#verbosity-levels) below)
6. Click **Save & Write Config**

The server writes `<project>/Saved/Nytwatch/NytwatchConfig.json`. The plugin reads this file at the start of every PIE session. If the file is absent or has `"status": "Off"`, the plugin does nothing.

---

## Annotating your classes

### Step 1 — NytwatchVerbosity tag

The plugin only tracks properties on classes that carry the `NytwatchVerbosity` metadata tag. Without this tag, a class is invisible to the plugin regardless of system arming.

```cpp
UCLASS(meta=(NytwatchVerbosity="Standard"))
class AMyCharacter : public ACharacter
{
    GENERATED_BODY()

public:
    UPROPERTY(EditAnywhere)
    float Health = 100.f;    // tracked at Standard tier (inherits from UCLASS)

    UPROPERTY(meta=(NytwatchVerbosity="Critical"))
    int32 Level = 1;         // tracked at Critical tier (UPROPERTY overrides UCLASS)

    UPROPERTY(meta=(NytwatchVerbosity="Ignore"))
    float CachedDelta = 0.f; // never tracked
};
```

Tag resolution order (highest priority first):
1. `UPROPERTY` meta tag on the property
2. `UCLASS` meta tag on the declaring class
3. `UCLASS` meta tag on a parent class (walks up to, but not including, `UObject`)
4. No tag found → `Ignore` (property is never tracked)

### Step 2 — Register in BeginPlay / EndPlay

The plugin uses an explicit registration model — it does **not** scan all UObjects. Each class that should be tracked must call `RegisterObject` in `BeginPlay` and `UnregisterObject` in `EndPlay`. No interface is required for this.

```cpp
// MyActor.h
UCLASS(meta=(NytwatchVerbosity="Standard"))
class AMyCharacter : public ACharacter
{
    GENERATED_BODY()
public:
    virtual void BeginPlay() override;
    virtual void EndPlay(const EEndPlayReason::Type Reason) override;
};
```

```cpp
// MyActor.cpp
#if WITH_EDITOR
#include "NytwatchSubsystem.h"
#endif

void AMyCharacter::BeginPlay()
{
    Super::BeginPlay();
#if WITH_EDITOR
    if (auto* NW = UNytwatchSubsystem::Get())
        NW->RegisterObject(this);
#endif
}

void AMyCharacter::EndPlay(const EEndPlayReason::Type Reason)
{
#if WITH_EDITOR
    if (auto* NW = UNytwatchSubsystem::Get())
        NW->UnregisterObject(this);
#endif
    Super::EndPlay(Reason);
}
```

Both calls are **no-ops** when tracking is not active (outside PIE, or `"status": "Off"`).

### Per-instance tracking toggle (optional)

To suppress tracking on specific instances at runtime, implement `INytwatchTrackable`. This is entirely optional — registration does not require it.

Because UHT does not allow preprocessor directives in a `UCLASS` inheritance list, add `NytwatchAgent` as an editor-only dependency in your game module's `Build.cs` instead of using `#if WITH_EDITOR` around the inheritance:

```csharp
// ProjectAlpha.Build.cs
if (Target.bBuildEditor)
    PrivateDependencyModuleNames.Add("NytwatchAgent");
```

Then inherit unconditionally:

```cpp
#include "NytwatchTrackable.h"

class AMyCharacter : public ACharacter, public INytwatchTrackable
{
    UPROPERTY(EditAnywhere, Category="Nytwatch")
    bool bEnableNytwatchTrack = true;

    virtual bool IsNytwatchTrackingEnabled_Implementation() const override
    { return bEnableNytwatchTrack; }
};
```

The default implementation returns `true` — implementing the interface alone has no effect on tracking.

---

## Verbosity levels

| Level | When it is logged |
|---|---|
| `Critical` | Always logged when the system is armed (filter ≤ Critical) |
| `Standard` | Logged when system filter is Standard or Verbose |
| `Verbose` | Logged only when system filter is set to Verbose |
| `Ignore` | Never logged — bypasses the filter entirely |

The system filter is the verbosity level you set when arming the system in the dashboard. Individual files within a system can have their own filter override from the **File Verbosity** page.

**Logging rule:** a property is logged if its tier ≤ the effective filter threshold.

```
Critical filter  →  Critical only
Standard filter  →  Critical + Standard
Verbose  filter  →  Critical + Standard + Verbose
```

---

## What gets tracked

The plugin tracks all `UPROPERTY` members on every registered object whose class carries a `NytwatchVerbosity` tag and whose source file falls within an armed system's paths.

**Property types tracked:**
- All numeric types (`float`, `int32`, `uint8`, etc.)
- `bool`
- `FString`, `FName`, `FText`
- `FVector`, `FRotator`, `FQuat`, `FTransform`
- Enums (logged as their string label)
- Object references (logged as the referenced object's name)

**The plugin records changes, not initial state.** A property that is set at construction and never changes during PIE will not produce any events.

---

## Session output

Each PIE session produces one markdown file in:

```
<project>/Saved/Nytwatch/Sessions/<uuid>.md
```

YAML frontmatter header followed by per-object event blocks:

```markdown
---
session_id: a1b2c3d4-...
started_at: 2026-04-06T10:15:00Z
ended_at:   2026-04-06T10:17:42Z
duration_seconds: 162
ue_project_name: MyGame
plugin_version: 1.0.0
systems_tracked: ["Campaign"]
event_count: 312
---

## Campaign

Settlement_0            | SettlementLevel:1 +1@4.0 | FactionRef:None→Faction_A@4.0
Settlement_1            | SettlementLevel:2 +1@12.5
```

**Numeric properties** use delta encoding: `PropName:InitialValue +Delta@t`.
**Non-numeric properties** use transition chains: `PropName:OldValue→NewValue@t`.

### Limits

| Parameter | Default | Where to change |
|---|---|---|
| Poll interval | 1 s | Nytwatch dashboard → Tracker → Systems |
| Event cap per session | 50,000 events | Hardcoded in `NytwatchSessionWriter.h` |

When the event cap is reached, recording stops and an in-editor notification appears. The partial session is still written normally when PIE ends.

---

## Troubleshooting

**No sessions appearing in the dashboard**
- Confirm the Nytwatch server is running (`nytwatch serve`) before starting PIE.
- Verify `<project>/Saved/Nytwatch/NytwatchConfig.json` exists and has `"status": "On"`.
- Confirm the plugin is enabled in your `.uproject` and compiled.

**Session file is empty (`event_count: 0`)**
- Confirm each tracked class calls `RegisterObject(this)` in `BeginPlay`. Without registration, no objects are polled.
- Check the UE Output Log (filter `NytwatchAgent`) — successful registrations log `Registered 'ActorName' → system 'SystemName'`. A line saying `matched no armed system` means the class's header path is not within the configured system paths.
- Confirm the class has a `NytwatchVerbosity` tag on the `UCLASS` or on the relevant `UPROPERTY` members.
- Remember that the plugin records *changes* — if the tracked properties never change during PIE, the session will have zero events.

**"Plugin version mismatch" notification in the editor**

Reinstall the plugin:
```bash
nytwatch install-plugin --project /path/to/project --force
```

**Event cap hit mid-session**

Reduce tracking scope: set noisy properties to `Ignore`, lower the system verbosity to `Standard` or `Critical`, or reduce the set of registered classes.

---

## File locations

| File | Purpose |
|---|---|
| `Saved/Nytwatch/NytwatchConfig.json` | Written by the Nytwatch server. Read by the plugin at PIE start and hot-reloaded every second during PIE. |
| `Saved/Nytwatch/nytwatch.lock` | Created when PIE starts, deleted when PIE ends. Used by the server to detect session boundaries. |
| `Saved/Nytwatch/Sessions/<uuid>.md` | One file per PIE session. Picked up automatically by the server. |
