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
Plugin polls armed UObjects every tick (default 10 Hz)
    │  Detects changed UPROPERTY values via FProperty reflection
    ▼
Changes buffered in memory → flushed to .md file every 10,000 events
    │
PIE ends
    ▼
Session file finalised at Saved/Nytwatch/Sessions/<uuid>.md
    │
Nytwatch server detects the new file via filesystem watch
    ▼
Session appears in the Gameplay Tracker dashboard
```

Communication between the plugin and the server is **entirely filesystem-based** — no network connection, no sockets. The plugin writes files; the server watches for them.

---

## Installation

### Recommended — via Nytwatch CLI

With the Nytwatch server installed, run from your terminal:

```bash
nytwatch install-plugin --project /path/to/your/ue5-project
```

This copies the plugin into `<project>/Plugins/NytwatchAgent/` and patches your `.uproject` to enable it. Open the project in the Unreal Editor and recompile when prompted.

To reinstall over an existing copy:

```bash
nytwatch install-plugin --project /path/to/your/ue5-project --force
```

### Interactive — via install script (Windows)

If you have the Nytwatch install scripts, run:

```powershell
.\scripts\windows\install-plugin.ps1
```

This presents a menu of all projects configured in Nytwatch and installs into your selection.

### Manual

1. Copy the `NytwatchAgent` folder into `<your-project>/Plugins/NytwatchAgent/`
2. Add the following entry to your `.uproject` file under `"Plugins"`:
   ```json
   {
     "Name": "NytwatchAgent",
     "Enabled": true
   }
   ```
3. Open the project in the Unreal Editor and recompile when prompted.

---

## Arming systems

The plugin only tracks systems you have explicitly armed. Arming is done from the **Nytwatch dashboard**, not in Unreal.

1. Start the Nytwatch server: `nytwatch serve`
2. Open http://127.0.0.1:8420/tracker in your browser
3. Switch to the **Systems** tab
4. Toggle **Armed** on the systems you want to track
5. Set the **verbosity** level for each armed system (see [Verbosity levels](#verbosity-levels) below)
6. Click **Save & Write Config**

The server writes `<project>/Saved/Nytwatch/NytwatchConfig.json`. The plugin reads this file at the start of every PIE session. If the file is absent or has `"status": "Off"`, the plugin does nothing.

---

## Annotating your classes

### Opt-in with `NytwatchVerbosity`

The plugin only tracks properties on classes that carry the `NytwatchVerbosity` metadata tag. Without this tag, a class is invisible to the plugin regardless of whether its system is armed.

```cpp
UCLASS(meta=(NytwatchVerbosity="Standard"))
class AMyCharacter : public ACharacter
{
    GENERATED_BODY()

public:
    UPROPERTY(EditAnywhere)
    float Health = 100.f;       // tracked at Standard tier

    UPROPERTY(EditAnywhere)
    float Stamina = 100.f;      // tracked at Standard tier
};
```

The tag can be placed on both `UCLASS` and `UPROPERTY`. The resolution order is:

1. **`UPROPERTY` tag** — takes priority over everything
2. **`UCLASS` tag on the declaring class** — fallback if no `UPROPERTY` tag
3. **`UCLASS` tag on a parent class** — walks the class hierarchy upward
4. **No tag found** — defaults to `Ignore` (property is never tracked)

### Per-property overrides

Use `UPROPERTY` tags to promote or demote individual properties from their class default:

```cpp
UCLASS(meta=(NytwatchVerbosity="Standard"))
class AMyCharacter : public ACharacter
{
    GENERATED_BODY()

public:
    // Promoted — always tracked even when system filter is set to Critical only
    UPROPERTY(meta=(NytwatchVerbosity="Critical"))
    float Health = 100.f;

    // Inherits Standard from UCLASS — tracked at Standard tier
    UPROPERTY()
    FVector Velocity;

    // Demoted — only tracked when system filter is set to Verbose
    UPROPERTY(meta=(NytwatchVerbosity="Verbose"))
    float AccelerationDelta = 0.f;

    // Never tracked, regardless of system filter
    UPROPERTY(meta=(NytwatchVerbosity="Ignore"))
    float CachedFrameDelta = 0.f;
};
```

### Verbosity levels

| Level | When it is logged |
|---|---|
| `Critical` | Always logged when the system is armed (filter ≤ Critical) |
| `Standard` | Logged when system filter is Standard or Verbose |
| `Verbose` | Logged only when system filter is set to Verbose |
| `Ignore` | Never logged — bypasses the filter entirely |

The system filter is the verbosity level you set when arming the system in the dashboard. Individual files within a system can have their own filter override, set from the **File Verbosity** page (`/settings/tracking/<system>/files`).

**Logging rule:** a property is logged if its tier ≤ the effective filter threshold.

```
Critical filter  →  Critical only
Standard filter  →  Critical + Standard
Verbose  filter  →  Critical + Standard + Verbose
```

### Per-instance tracking toggle with `INytwatchTrackable`

To control tracking at the object-instance level (rather than the whole class), implement the `INytwatchTrackable` interface. This gives each instance its own enable/disable toggle that can be flipped in the Details panel or via Blueprint.

```cpp
#include "NytwatchTrackable.h"

UCLASS(meta=(NytwatchVerbosity="Standard"))
class AMyCharacter : public ACharacter, public INytwatchTrackable
{
    GENERATED_BODY()

public:
    // Expose the toggle in the Details panel
    UPROPERTY(EditAnywhere, Category = "Nytwatch")
    bool bEnableNytwatchTrack = true;

    virtual bool IsNytwatchTrackingEnabled_Implementation() const override
    {
        return bEnableNytwatchTrack;
    }
};
```

**Rules:**
- The default implementation returns `true` — adding the interface does not affect tracking unless you override it.
- Classes that do not implement the interface are always tracked when their system is armed (existing behaviour unchanged).
- Blueprint classes can implement `INytwatchTrackable` and override `IsNytwatchTrackingEnabled` to drive the toggle from a Blueprint variable.

---

## What gets tracked

The plugin tracks all `UPROPERTY` members on every `UObject` instance (Actors, Components, subsystems, data assets, etc.) whose class source file falls within an armed system's paths.

**Property types that are tracked:**
- All numeric types (`float`, `int32`, `uint8`, etc.)
- `bool`
- `FString`, `FName`, `FText`
- `FVector`, `FRotator`, `FQuat`, `FTransform`
- Enums (logged as their string label)
- Object references (logged as the referenced object's name)

**Automatically skipped:**
- Class Default Objects (CDOs)
- Unreachable or garbage-collected objects
- Transient non-Actor objects
- Objects whose class lives in the `NytwatchAgent` module itself

---

## Session output

Each PIE session produces one markdown file in:

```
<project>/Saved/Nytwatch/Sessions/<uuid>.md
```

The file has a YAML frontmatter header followed by timestamped event blocks:

```markdown
---
session_id: "a1b2c3d4-..."
started_at: "2026-04-06T10:15:00Z"
ended_at:   "2026-04-06T10:17:42Z"
duration_seconds: 162
ue_project_name: "MyGame"
plugin_version: "1.0.0"
systems_tracked: ["Combat", "Character"]
event_count: 847
---

## AMyCharacter_0  (AMyCharacter)  [Combat]

[00:03.45]  Health: 100.0 → 75.0
[00:03.45]  Stamina: 100.0 → 92.0
[00:07.12]  Health: 75.0 → 50.0
```

### Limits

| Parameter | Default | Where to change |
|---|---|---|
| Tick interval | 0.1s (10 Hz) | Nytwatch dashboard → Tracker → Systems |
| Object scan cap per tick | 2,000 objects | Nytwatch dashboard → Tracker → Systems |
| Event flush threshold | 10,000 events | Hardcoded |
| Event cap per session | 50,000 events | Hardcoded |

When the event cap is reached, recording stops and an in-editor notification appears. The partial session is still written normally when PIE ends.

---

## Troubleshooting

**No sessions appearing in the dashboard**

- Check that the Nytwatch server is running (`nytwatch serve`) before starting PIE.
- Verify that `<project>/Saved/Nytwatch/NytwatchConfig.json` exists and contains `"status": "On"`. If the file is missing, arm at least one system from the Tracker page and click **Save & Write Config**.
- Confirm the plugin is enabled in your `.uproject` and compiled (check the Plugins panel in the Unreal Editor).

**"Plugin version mismatch" notification in the editor**

The plugin version does not match what the Nytwatch server expects. Reinstall the plugin:

```bash
nytwatch install-plugin --project /path/to/project --force
```

**Properties not appearing in session files**

- Ensure the class has a `NytwatchVerbosity` tag on the `UCLASS` or on individual `UPROPERTY` members.
- Confirm the class's source file path falls within an armed system's configured paths.
- Check the system verbosity level — if set to `Critical`, only `Critical`-tagged properties log.
- If the class implements `INytwatchTrackable`, check that `IsNytwatchTrackingEnabled` returns `true` for the instance.

**Event cap hit mid-session**

Reduce the scope of tracking: set high-frequency or low-value properties to `Ignore`, lower the system verbosity level to `Critical` or `Standard`, or reduce the tick rate from the dashboard.

---

## File locations

| File | Purpose |
|---|---|
| `Saved/Nytwatch/NytwatchConfig.json` | Written by the Nytwatch server. Read by the plugin at PIE start. |
| `Saved/Nytwatch/nytwatch.lock` | Created when PIE starts, deleted when PIE ends. Used by the server to detect session boundaries. |
| `Saved/Nytwatch/Sessions/<uuid>.md` | One file per PIE session. Picked up automatically by the server. |
