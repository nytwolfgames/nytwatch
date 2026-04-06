# NytwatchAgent — Adapter Guide

An adapter is a single game-side class that bridges your game's event system to the NytwatchAgent plugin. It is optional. Without one, the plugin works exactly as it always has — property diffs, wall-clock timestamps, no causality layer. With one, you get game-time timestamps and named event causality.

The plugin knows nothing about your game's types. The adapter is where that knowledge lives.

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

Exact timing is game-specific and deferred to per-project documentation. The general rule: initialize from wherever your game mode or game instance confirms the world is ready, wrapped in `#if WITH_EDITOR`.

```cpp
#if WITH_EDITOR
NytwatchAdapter = NewObject<UMyGameNytwatchAdapter>(this);
NytwatchAdapter->Initialize(this); // pass whatever gives access to your event system
#endif
```

---

## Checklist

- [ ] Class inherits `INytwatchTimeProvider` if using game-time
- [ ] `GetCurrentTimeString()` returns a stable, human-readable time string
- [ ] `SetTimeProvider(this)` called in `Initialize()`
- [ ] Delegate bindings use `UFUNCTION()` on handlers (required for UE5 dynamic delegates)
- [ ] `LogEvent` called only for events with meaningful parameters and tracked affected actors
- [ ] `RequestDeferredPoll()` called on routine game-time ticks (e.g. `DayPassed`)
- [ ] Adapter instantiated and initialized inside `#if WITH_EDITOR`
- [ ] Adapter destroyed or unbound when the world ends (prevent stale delegate bindings)
