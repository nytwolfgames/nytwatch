# NytwatchAgent — Adapter Guide

An adapter is a game-side class that bridges your game's event system to the NytwatchAgent plugin. It is optional. Without one, the plugin works exactly as it always has — property diffs, wall-clock timestamps, no causality layer. With one, you get game-time timestamps and named event causality.

The plugin knows nothing about your game's types. The adapter is where that knowledge lives.

**Multiple adapters can coexist.** A game with distinct gameplay modes (e.g. campaign and battle) should have one adapter per mode. Each adapter is scoped to its mode's lifetime. See [Multiple Adapters](#multiple-adapters).

---

## When You Need an Adapter

| You want | Adapter needed? |
|---|---|
| Property change tracking only (existing behaviour) | No |
| Game-time timestamps instead of wall-clock | Yes |
| Named event headers in output (`WarDeclared`, `SettlementCaptured`, etc.) | Yes |
| Cross-object event grouping | Yes |

---

## The Two Interfaces

An adapter implements one required interface and uses the plugin's push API.

### 1. `INytwatchTimeProvider` (required if using game-time)

```cpp
class INytwatchTimeProvider
{
public:
    virtual FString GetCurrentTimeString() = 0;
};
```

The plugin calls this on every poll to label the output block. Return whatever time representation is meaningful for your game. If not set, the plugin falls back to wall-clock seconds.

### 2. Plugin push API (optional, use as needed)

```cpp
// Trigger a deferred poll on the next frame. No event label.
// Use this on routine game-time ticks (DayPassed, HourPassed, etc.)
UNytwatchSubsystem::RequestDeferredPoll();

// Trigger a deferred poll with a named event header.
// AffectedActors determines which objects are grouped under this event in the output.
// Objects not in this list fall under the routine poll block.
UNytwatchSubsystem::LogEvent(FString NarrativeHeader, TArray<UObject*> AffectedActors);
```

Both calls are fire-and-forget. The actual poll runs on the next `Tick()`, after all game subscribers have finished processing. Multiple calls in the same frame are queued and resolved in one poll.

---

## Minimal Adapter (Game-Time Only, No Named Events)

For a game where you just want game-time timestamps and nothing else:

```cpp
UCLASS()
class UMyGameNytwatchAdapter : public UObject, public INytwatchTimeProvider
{
    GENERATED_BODY()
public:
    void Initialize()
    {
        UNytwatchSubsystem::Get()->SetTimeProvider(this);
    }

    virtual FString GetCurrentTimeString() override
    {
        // Return whatever your game uses for time
        return FString::Printf(TEXT("Day %d"), MyGameState->CurrentDay);
    }
};
```

No delegate bindings. No `LogEvent` calls. The existing 0.5s wall-clock poll continues unchanged — it just uses your time string for labels instead of seconds.

---

## Full Adapter (Game-Time + Named Events)

For a game with a broadcast event system (delegates, signals, etc.) that you want reflected in the tracking output:

```cpp
UCLASS()
class UMyGameNytwatchAdapter : public UObject, public INytwatchTimeProvider
{
    GENERATED_BODY()
public:
    void Initialize(AMyGameMode* GameMode);

    // INytwatchTimeProvider
    virtual FString GetCurrentTimeString() override;

    // One handler per event you care about
    UFUNCTION() void OnDayPassed();
    UFUNCTION() void OnSettlementCaptured(ASettlement* Settlement);
    UFUNCTION() void OnWarDeclared(AFaction* Instigator, AFaction* Target);

private:
    UNytwatchSubsystem* NW;
};
```

```cpp
void UMyGameNytwatchAdapter::Initialize(AMyGameMode* GameMode)
{
    NW = UNytwatchSubsystem::Get();
    NW->SetTimeProvider(this);

    // Bind to whatever broadcast system your game uses
    GameMode->Events->DayPassed.AddDynamic(this, &ThisClass::OnDayPassed);
    GameMode->Events->SettlementCaptured.AddDynamic(this, &ThisClass::OnSettlementCaptured);
    GameMode->Events->WarDeclared.AddDynamic(this, &ThisClass::OnWarDeclared);
}

FString UMyGameNytwatchAdapter::GetCurrentTimeString()
{
    auto T = AMyGameState::GetInstance()->CurrentTime;
    return FString::Printf(TEXT("Day %d, Year %d, Hour %d"), T.Days, T.Years, T.Hours);
}

// Routine tick — poll all objects, no named event
void UMyGameNytwatchAdapter::OnDayPassed()
{
    NW->RequestDeferredPoll();
}

// Named event — poll fires next frame, changes on affected objects
// are grouped under this header in the output
void UMyGameNytwatchAdapter::OnSettlementCaptured(ASettlement* S)
{
    FString Header = FString::Printf(TEXT("%s captured %s from %s"),
        *S->FactionRef->Name,
        *S->Name.ToString(),
        *S->PreviousOwningFaction->Name);

    NW->LogEvent(Header, { S, S->FactionRef, S->PreviousOwningFaction });
}

void UMyGameNytwatchAdapter::OnWarDeclared(AFaction* Instigator, AFaction* Target)
{
    FString Header = FString::Printf(TEXT("War declared: %s → %s"),
        *Instigator->Name, *Target->Name);

    NW->LogEvent(Header, { Instigator, Target });
}
```

---

## Adapter for a Realtime Action Game

No game-time ticks. No named events. The adapter is nearly empty — the existing 0.5s wall-clock poll handles everything.

```cpp
void UActionGameNytwatchAdapter::Initialize()
{
    // No time provider set — plugin uses wall-clock seconds (existing behaviour)
    // No delegate bindings — poll runs on existing 0.5s tick
    // Nothing to do
}
```

In this case: don't create an adapter at all. The plugin works without one.

---

## What Makes a Good Named Event

Not every broadcast warrants a `LogEvent` call. Bind to events that:

- **Change tracked properties on multiple objects simultaneously** — a single cause with cross-object consequences (capture, battle outcome, diplomatic shift).
- **Are semantically meaningful** — the narrative header should tell a story on its own without needing to read the property block beneath it.
- **Have parameters that identify the actors involved** — if the delegate gives you no pointers, you can't identify affected actors or write a useful header.

Do not bind to:
- UI events (`BannerClicked`, `SettlementSelected`)
- Camera or input events
- Events with no parameters where property changes would be caught by the routine poll anyway (`Player_BuildingConstructed` — building slot changes appear in the next `DayPassed` poll)

---

## Output Effect

Without adapter:
```
## Campaign
Settlement_Thornvale | FactionRef: PlayerFaction→GreenskinHorde@5.50 | Stability: 80@0.00 -25@5.50
Faction_PlayerFaction | SettlementCount: 12@0.00 -1@5.50
```

With adapter (`LogEvent` + `INytwatchTimeProvider`):
```
## Day 45, Year 2, Hour 14 | GreenskinHorde captured Thornvale from PlayerFaction
  Settlement_Thornvale  | FactionRef: PlayerFaction→GreenskinHorde | Stability: 80→55
  Faction_PlayerFaction | SettlementCount: 12→11
  Faction_GreenskinHorde| SettlementCount: 8→9
```

The property changes are the same. The context is not.

---

## Initialization

The adapter must be initialized after the game's event system exists and before any events you care about fire.

Exact timing is game-specific and deferred to per-project documentation. The general rule: initialize from wherever your game mode confirms the world is ready, wrapped in `#if WITH_EDITOR`.

```cpp
#if WITH_EDITOR
NytwatchAdapter = NewObject<UMyGameNytwatchAdapter>(this);
NytwatchAdapter->Initialize(this); // pass whatever gives access to your event system
#endif
```

---

## Multiple Adapters

Multiple adapters can coexist without any changes to the plugin. Each part of the plugin API is designed for this:

| API | Multi-adapter behaviour |
|---|---|
| `LogEvent()` | Push to a shared queue. Any adapter can call it freely. |
| `RequestDeferredPoll()` | Same — any adapter can trigger a deferred poll. |
| `SetTimeProvider()` | Singular. Last caller wins. In practice, only one mode is active at a time, so the active adapter's provider is always current. |
| Object registration | Handled automatically by `RegisterObject` / `UnregisterObject` in `BeginPlay` / `EndPlay`. Each mode's objects register themselves when they spawn and unregister when they are destroyed. The subsystem's tracked object list always reflects what is currently alive. |

### One adapter per gameplay mode

For a game with distinct modes backed by separate game modes (e.g. campaign and battle), create one adapter per mode. Each adapter:

- Initializes when its game mode starts
- Binds only to that mode's event system
- Calls `SetTimeProvider(this)` on init, taking over as the active time provider
- **Unbinds all delegates when its mode ends** — not just when the world ends

The last point is important. If the campaign adapter remains bound to campaign delegates while the battle game mode is running, those delegates may still fire and push stale events or incorrect time strings into the plugin. Each adapter must clean up on mode transition.

### Shutdown pattern

```cpp
void UCampaignNytwatchAdapter::Shutdown()
{
    if (CampaignGameMode)
    {
        CampaignGameMode->Events->DayPassed.RemoveDynamic(this, &ThisClass::OnDayPassed);
        CampaignGameMode->Events->SettlementCaptured.RemoveDynamic(this, &ThisClass::OnSettlementCaptured);
        // unbind all delegates bound in Initialize()
    }

    // Only clear the time provider if this adapter set it.
    // If the battle adapter has already called SetTimeProvider, don't overwrite it.
    if (NW && NW->GetTimeProvider() == this)
        NW->SetTimeProvider(nullptr);
}
```

Call `Shutdown()` from the game mode's `EndPlay` or equivalent teardown, before the new mode starts.

### Example: campaign + battle

```
Campaign game mode starts
  → UCampaignNytwatchAdapter::Initialize()
  → SetTimeProvider(this)           // "Day 45, Year 2, Hour 14"
  → binds DayPassed, SettlementCaptured, WarDeclared, etc.

Player enters battle
  → UCampaignNytwatchAdapter::Shutdown()
    → removes campaign delegate bindings
    → clears time provider (if still set to self)
  → Battle game mode starts
  → URTSBattleNytwatchAdapter::Initialize()
  → SetTimeProvider(this)           // "Battle, Turn 3" or "Wave 2" or wall-clock
  → binds battle-specific events (unit destroyed, wave started, etc.)

Battle ends
  → URTSBattleNytwatchAdapter::Shutdown()
    → removes battle delegate bindings
    → clears time provider
  → Campaign game mode resumes
  → UCampaignNytwatchAdapter::Initialize()
  → SetTimeProvider(this)           // campaign time resumes
```

Each adapter is active for exactly its mode's duration. The plugin sees a continuous stream of events and polls — it has no concept of mode boundaries.

---

## Checklist

### Per adapter
- [ ] Class inherits `INytwatchTimeProvider` if using game-time
- [ ] `GetCurrentTimeString()` returns a stable, human-readable time string
- [ ] `SetTimeProvider(this)` called in `Initialize()`
- [ ] Delegate bindings use `UFUNCTION()` on handlers (required for UE5 dynamic delegates)
- [ ] `LogEvent` called only for events with meaningful parameters and tracked affected actors
- [ ] `RequestDeferredPoll()` called on routine game-time ticks (e.g. `DayPassed`)
- [ ] Adapter instantiated and initialized inside `#if WITH_EDITOR`
- [ ] `Shutdown()` removes all delegate bindings bound in `Initialize()`
- [ ] `Shutdown()` clears `SetTimeProvider` only if this adapter is still the active provider
- [ ] `Shutdown()` called on **mode end**, not just world end

### If using multiple adapters
- [ ] Each adapter is scoped to exactly one gameplay mode
- [ ] `Shutdown()` is called before the next mode's adapter initializes
- [ ] No two adapters are bound to the same delegate simultaneously
