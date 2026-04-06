# NytwatchAgent — UE5 Plugin

**Version:** 1.0.0 | **Type:** Editor plugin | **UE requirement:** UE 5.1+

NytwatchAgent is an Unreal Engine 5 Editor plugin that records `UPROPERTY` value changes on your game objects during Play In Editor (PIE) sessions and streams them to the Nytwatch server. The server consolidates the stream into structured session files, which appear in the **Gameplay Tracker** dashboard for browsing, bookmarking, and AI analysis.

---

## How it works

```
PIE starts
    │
    ▼
Plugin reads NytwatchConfig.json from Saved/Nytwatch/
    │  (written by the Nytwatch server when you arm systems)
    ▼
Plugin connects to the Nytwatch server over WebSocket
    │  Sends session_open message with session metadata
    ▼
Actors call RegisterObject(this) from their BeginPlay
    │  Plugin records the initial property snapshot for each registered object
    ▼
Every tick — game thread polls registered objects for UPROPERTY changes
    │  Changed values are batched and sent as a single WebSocket message per tick
    ▼
PIE ends — plugin sends session_close, server runs consolidation
    │
    ▼
Session file written at Saved/Nytwatch/Sessions/<uuid>.md (server side)
    │
Nytwatch dashboard picks up the session automatically
```

All work after `RegisterObject` happens on the **game thread only**. There is no background writer thread — events are batched per tick and sent over WebSocket. No disk I/O occurs in the plugin after PIE starts.

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
| `Critical` | Always logged when the system is armed |
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

Sessions are consolidated by the Nytwatch server after PIE ends. Each session produces one markdown file in:

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

Settlement_0  | SettlementLevel:1 +1@4.0 | FactionRef:None→Faction_A@4.0
Settlement_1  | SettlementLevel:2 +1@12.5
```

**Numeric properties** use delta encoding: `PropName:InitialValue +Delta@t`.
**Non-numeric properties** use transition chains: `PropName:OldValue→NewValue@t`.

When an [adapter](#adapters-causality-and-game-time) is present, output uses game-time headers and named event grouping instead of wall-clock timestamps:

```markdown
## Day 44, Year 2, Hour 6
Settlement_Thornvale  | Stability: 80→73 | Gold: 320→290

## Day 45, Year 2, Hour 14 | GreenskinHorde captured Thornvale from PlayerFaction
  Settlement_Thornvale  | FactionRef: PlayerFaction→GreenskinHorde | Stability: 73→48
  Faction_PlayerFaction | SettlementCount: 12→11
  Faction_GreenskinHorde| SettlementCount: 8→9
```

### Limits

| Parameter | Default | Where to change |
|---|---|---|
| Poll interval | 1 s | Nytwatch dashboard → Tracker → Systems |
| Event cap per session | 50,000 events | Hardcoded in `NytwatchSessionWriter.h` |

When the event cap is reached, recording stops and an in-editor notification appears. The partial session is still written normally when PIE ends.

---

## Adapters — Causality and Game-Time

By default the plugin tracks property changes with wall-clock timestamps and no knowledge of why a change occurred. An **adapter** is an optional game-side class that adds two things:

- **Game-time timestamps** — output labels use your game's time (e.g. `Day 45, Year 2, Hour 14`) instead of seconds
- **Named event causality** — output blocks are attributed to the events that caused them (e.g. `GreenskinHorde captured Thornvale from PlayerFaction`)

The plugin knows nothing about your game's types. The adapter bridges your event system to the plugin's generic API. **No changes to existing gameplay methods are needed** — the adapter subscribes to your game's existing broadcast delegates from the outside.

### When you need an adapter

| You want | Adapter needed? |
|---|---|
| Property change tracking only | No |
| Game-time timestamps instead of wall-clock | Yes |
| Named event headers in session output | Yes |
| Cross-object event grouping | Yes |

### The plugin API

```cpp
// Set the active game-time provider. Pass nullptr to revert to wall-clock.
UNytwatchSubsystem::Get()->SetTimeProvider(INytwatchTimeProvider* Provider);

// Returns the currently active provider (use to check before clearing).
UNytwatchSubsystem::Get()->GetTimeProvider();

// Schedule a deferred poll on the next tick with no named event.
// Use from routine game-time tick handlers (e.g. DayPassed).
UNytwatchSubsystem::Get()->RequestDeferredPoll();

// Schedule a deferred poll attributed to a named event.
// AffectedActors are grouped under this header in the output.
// All other changed objects fall into the routine block.
// Safe to call multiple times per frame.
UNytwatchSubsystem::Get()->LogEvent(const FString& NarrativeHeader,
                                     const TArray<UObject*>& AffectedActors);
```

`RequestDeferredPoll` and `LogEvent` are fire-and-forget. The actual poll runs on the next tick after all game subscribers have finished processing the current frame — capturing fully-settled post-event state.

### INytwatchTimeProvider

```cpp
// Include: NytwatchAgent/Public/INytwatchTimeProvider.h
class INytwatchTimeProvider
{
public:
    virtual FString GetCurrentTimeString() = 0;
};
```

Implement this in your adapter and pass the instance to `SetTimeProvider()`. Return whatever time representation is meaningful for your game. Called once per poll.

### Minimal adapter (game-time only)

```cpp
UCLASS()
class UMyNytwatchAdapter : public UObject, public INytwatchTimeProvider
{
    GENERATED_BODY()
public:
    void Initialize()
    {
        UNytwatchSubsystem::Get()->SetTimeProvider(this);
    }

    virtual FString GetCurrentTimeString() override
    {
        auto T = AMyGameState::GetInstance()->CurrentTime;
        return FString::Printf(TEXT("Day %d, Year %d, Hour %d"),
            T.Days, T.Years, T.Hours);
    }
};
```

No delegate bindings. No `LogEvent` calls. The existing poll continues unchanged, now labelled with your game time.

### Full adapter (game-time + named events)

```cpp
UCLASS()
class UMyNytwatchAdapter : public UObject, public INytwatchTimeProvider
{
    GENERATED_BODY()
public:
    void Initialize(AMyGameMode* GameMode);
    void Shutdown();

    virtual FString GetCurrentTimeString() override;

    UFUNCTION() void OnDayPassed();
    UFUNCTION() void OnSettlementCaptured(ASettlement* S);
    UFUNCTION() void OnWarDeclared(AFaction* Instigator, AFaction* Target);

private:
    UNytwatchSubsystem* NW;
    AMyGameMode* GameModeRef;
};
```

```cpp
void UMyNytwatchAdapter::Initialize(AMyGameMode* GameMode)
{
    GameModeRef = GameMode;
    NW = UNytwatchSubsystem::Get();
    NW->SetTimeProvider(this);

    GameMode->Events->DayPassed.AddDynamic(this, &ThisClass::OnDayPassed);
    GameMode->Events->SettlementCaptured.AddDynamic(this, &ThisClass::OnSettlementCaptured);
    GameMode->Events->WarDeclared.AddDynamic(this, &ThisClass::OnWarDeclared);
}

void UMyNytwatchAdapter::Shutdown()
{
    if (GameModeRef)
    {
        GameModeRef->Events->DayPassed.RemoveDynamic(this, &ThisClass::OnDayPassed);
        GameModeRef->Events->SettlementCaptured.RemoveDynamic(this, &ThisClass::OnSettlementCaptured);
        GameModeRef->Events->WarDeclared.RemoveDynamic(this, &ThisClass::OnWarDeclared);
    }
    // Only clear if we are still the active provider
    if (NW && NW->GetTimeProvider() == this)
        NW->SetTimeProvider(nullptr);
}

FString UMyNytwatchAdapter::GetCurrentTimeString()
{
    auto T = AMyGameState::GetInstance()->CurrentTime;
    return FString::Printf(TEXT("Day %d, Year %d, Hour %d"), T.Days, T.Years, T.Hours);
}

void UMyNytwatchAdapter::OnDayPassed()
{
    NW->RequestDeferredPoll();
}

void UMyNytwatchAdapter::OnSettlementCaptured(ASettlement* S)
{
    NW->LogEvent(
        FString::Printf(TEXT("%s captured %s from %s"),
            *S->FactionRef->Name, *S->Name.ToString(), *S->PreviousOwningFaction->Name),
        { S, S->FactionRef, S->PreviousOwningFaction }
    );
}

void UMyNytwatchAdapter::OnWarDeclared(AFaction* Instigator, AFaction* Target)
{
    NW->LogEvent(
        FString::Printf(TEXT("War declared: %s → %s"), *Instigator->Name, *Target->Name),
        { Instigator, Target }
    );
}
```

Instantiate and initialize inside `#if WITH_EDITOR`:

```cpp
#if WITH_EDITOR
NytwatchAdapter = NewObject<UMyNytwatchAdapter>(this);
NytwatchAdapter->Initialize(this);
#endif
```

### What makes a good named event

Bind to events that:
- Change tracked properties on **multiple objects simultaneously** (capture, battle outcome, diplomatic shift)
- Are **semantically meaningful** — the header should read as a standalone sentence
- Have **delegate parameters** that identify the actors involved

Do not bind to:
- UI, camera, or input events
- Events with no parameters (property changes are caught by the routine poll)

### Multiple adapters

Multiple adapters can coexist. Each part of the plugin API handles this naturally:

| API | Multi-adapter behaviour |
|---|---|
| `LogEvent()` | Pushed to a shared queue — any adapter can call it freely |
| `RequestDeferredPoll()` | Same — any adapter can trigger a deferred poll |
| `SetTimeProvider()` | Singular — last caller wins. Fine in practice since only one mode is active at a time |
| Object registration | Automatic via `BeginPlay` / `EndPlay` — no adapter involvement |

**One adapter per gameplay mode.** For games with distinct modes backed by separate game modes (e.g. campaign and battle), create one adapter per mode. Each adapter initializes when its mode starts and **shuts down when its mode ends** — not just when the world ends.

```
Campaign game mode starts
  → UCampaignAdapter::Initialize()  — SetTimeProvider, bind delegates

Player enters battle
  → UCampaignAdapter::Shutdown()    — remove delegate bindings, clear provider
  → Battle game mode starts
  → URTSBattleAdapter::Initialize() — SetTimeProvider, bind battle delegates

Battle ends
  → URTSBattleAdapter::Shutdown()   — remove delegate bindings, clear provider
  → Campaign resumes
  → UCampaignAdapter::Initialize()  — SetTimeProvider, bind delegates again
```

If the outgoing adapter remains bound while a new mode runs, its delegates may still fire and push stale events into the plugin. Always call `Shutdown()` on mode transition.

### Adapter checklist

**Per adapter:**
- [ ] Inherits `INytwatchTimeProvider` if using game-time
- [ ] `GetCurrentTimeString()` returns a stable, human-readable time string
- [ ] `SetTimeProvider(this)` called in `Initialize()`
- [ ] Delegate handlers marked `UFUNCTION()` (required for UE5 dynamic delegates)
- [ ] `LogEvent` called only for events with meaningful parameters and tracked actors
- [ ] `RequestDeferredPoll()` called on routine game-time ticks (e.g. `DayPassed`)
- [ ] Instantiated inside `#if WITH_EDITOR`
- [ ] `Shutdown()` removes all delegate bindings added in `Initialize()`
- [ ] `Shutdown()` clears `SetTimeProvider` only if this adapter is still the active provider
- [ ] `Shutdown()` called on **mode end**, not just world end

**If using multiple adapters:**
- [ ] Each adapter is scoped to exactly one gameplay mode
- [ ] `Shutdown()` is called before the next mode's adapter initializes
- [ ] No two adapters are bound to the same delegate simultaneously

---

## Troubleshooting

**No sessions appearing in the dashboard**
- Confirm the Nytwatch server is running (`nytwatch serve`) before starting PIE.
- Verify `<project>/Saved/Nytwatch/NytwatchConfig.json` exists and has `"status": "On"`.
- Confirm the plugin is enabled in your `.uproject` and compiled.
- The server must be reachable at the `tracking_ws_url` in the config — check the UE Output Log for WebSocket connection errors.

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

**WebSocket connection timed out**

The plugin waits up to 30 seconds for the WebSocket handshake. If it times out, tracking is disabled for that session. Check that the Nytwatch server is running and the `tracking_ws_url` in `NytwatchConfig.json` is correct.

---

## File locations

| File | Purpose |
|---|---|
| `Saved/Nytwatch/NytwatchConfig.json` | Written by the Nytwatch server. Read by the plugin at PIE start and hot-reloaded every second during PIE. |
| `Saved/Nytwatch/nytwatch.lock` | Created when PIE starts, deleted when PIE ends. Used by the server to detect session boundaries and PIE crashes. |
| `Saved/Nytwatch/Sessions/<uuid>.md` | One file per PIE session. Written by the server after consolidation. Picked up automatically by the dashboard. |
