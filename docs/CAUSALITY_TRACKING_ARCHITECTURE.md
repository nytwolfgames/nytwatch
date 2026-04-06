# Nytwatch — Causality Tracking Architecture

Describes the design for extending the Gameplay Tracker with game-time awareness, named event causality, and cross-object grouping — while keeping the NytwatchAgent plugin fully generic.

---

## Problem Statement

The existing tracker answers: **what changed, and when (wall-clock)?**

For a grand strategy game this is insufficient. State changes in such games are:

- **Driven by game-time ticks** — `DayPassed`, `HourPassed`, specific named events — not by real-time physics or combat frames.
- **Semantically meaningful** — "Stability dropped because a settlement was captured by an enemy faction" is not recoverable from a raw numeric delta.
- **Cross-object** — A single event (settlement capture) simultaneously changes properties on the settlement, both factions, and the province. These should be grouped, not treated as independent deltas.

Wall-clock timestamps (`-25@5.50`) are meaningless in this context. `Day 45, Year 2` is not.

---

## Design Principles

1. **The plugin stays generic.** It uses UE5 reflection and knows nothing about game-specific types (`AFaction`, `FTimeScale`, `EventsHolder`). It is reusable across any UE5 project.
2. **Game-specific logic lives in a thin adapter.** One class in game code bridges the game's event system to the plugin's generic API. This is the only place both sides are visible.
3. **No instrumentation at property change sites.** No `Log_Event(...)` calls scattered through gameplay code. The adapter subscribes to existing broadcast delegates — it does not modify the methods that change state.
4. **Poll on game-time ticks, not wall-clock.** The primary poll rhythm is `DayPassed`. Sub-day polls fire only when a named event has occurred.

---

## Architecture Overview

```
Game Code                        NytwatchAgent Plugin
─────────────────────────────    ──────────────────────────────────
EventsHolder broadcasts    ───►  (not bound — plugin is generic)

NytwatchAdapter (game code)      NytwatchSubsystem
  Binds to EventsHolder    ───►    LogEvent(header, actors)
  Implements TimeProvider  ───►    SetTimeProvider(this)
  Calls RequestDeferredPoll ──►    RequestDeferredPoll()

                                 Next frame Tick:
                                   Poll all registered objects
                                   Group deltas by event context
                                   Emit formatted output
```

---

## Deferred Poll Mechanism

### Why deferred

`EventsHolder` broadcasts to all subscribers in registration order. The tracker cannot guarantee it runs last. If it polls during the broadcast, it may capture partial state — some subscribers have run, others have not.

**Solution:** when the adapter calls `LogEvent()` or `RequestDeferredPoll()`, the plugin sets a flag and defers the actual poll to the next `Tick()`. By then all subscribers have finished processing, and the polled state is fully settled.

### Flow

```
Frame N:
  EventsHolder->DayPassed fires
  → All Settlement, Army, Faction subscribers run and update state
  → NytwatchAdapter::OnDayPassed calls NW->RequestDeferredPoll()
  → (if SettlementCaptured also fires this frame)
    NytwatchAdapter::OnSettlementCaptured calls NW->LogEvent(header, {S, F1, F2})

Frame N+1:
  NytwatchSubsystem::Tick fires
  → bDeferredPollPending == true
  → Poll all registered objects
  → Assign deltas to event contexts (see grouping below)
  → Clear pending queue, reset flag
```

### Multiple events in the same frame

A `DayPassed` broadcast may trigger further broadcasts within the same frame (e.g. AI processes day → decides to attack → battle resolves → `SettlementCaptured` fires → `OnDiplomaticEventTriggered` fires). All of these call into the plugin in Frame N. The plugin queues them all and resolves in one poll on Frame N+1.

---

## Event Grouping

When the deferred poll fires, each polled object delta is assigned to an event context by the following rule:

- If the object appears in the `AffectedActors` list of a named event, its changes belong to that event's output block.
- If the object appears in multiple named events (rare), it belongs to the first event it was associated with.
- All remaining objects (not in any named event's actor list) belong to the routine `DayPassed` block.

This means one poll, one pass, clean separation between routine daily changes and specific named-event consequences.

---

## Plugin API Additions

Three additions to `UNytwatchSubsystem`. No game types leak into the plugin.

### `INytwatchTimeProvider`

```cpp
class INytwatchTimeProvider
{
public:
    virtual FString GetCurrentTimeString() = 0;
};

// On UNytwatchSubsystem:
void SetTimeProvider(INytwatchTimeProvider* Provider);
```

The plugin calls `GetCurrentTimeString()` as the timestamp on every poll output block. The plugin never sees `FTimeScale` or any game-specific time struct.

### `RequestDeferredPoll()`

```cpp
void RequestDeferredPoll();
```

Sets `bDeferredPollPending = true`. No event label — the time provider string becomes the block header. Used for routine game-time tick polls (`DayPassed`).

### `LogEvent()`

```cpp
void LogEvent(FString NarrativeHeader, TArray<UObject*> AffectedActors);
```

Records a named event with a pre-composed narrative string and the set of actors whose changes should be attributed to it. Also sets `bDeferredPollPending = true`.

---

## The Adapter (Game Code)

One class in game code. Knows about both the plugin API and the game's types. Written once per game project.

```cpp
// Example: ProjectAlpha
UCLASS()
class UProjectAlphaNytwatchAdapter : public UObject, public INytwatchTimeProvider
{
    GENERATED_BODY()

    void Initialize(AMainGameModeBase* GameMode);

    // INytwatchTimeProvider
    virtual FString GetCurrentTimeString() override;

    // One handler per EventsHolder delegate that matters
    UFUNCTION() void OnDayPassed();
    UFUNCTION() void OnSettlementCaptured(ASettlement* S);
    UFUNCTION() void OnDiplomaticEventTriggered(AFaction* A, AFaction* B, EDiplomaticEvent Type);
    // ...

private:
    UNytwatchSubsystem* NW;
};
```

```cpp
void UProjectAlphaNytwatchAdapter::Initialize(AMainGameModeBase* GameMode)
{
    NW = UNytwatchSubsystem::Get();
    NW->SetTimeProvider(this);

    GameMode->EventsHolder->DayPassed.AddDynamic(this, &ThisClass::OnDayPassed);
    GameMode->EventsHolder->SettlementCaptured.AddDynamic(this, &ThisClass::OnSettlementCaptured);
    GameMode->EventsHolder->OnDiplomaticEventTriggered.AddDynamic(this, &ThisClass::OnDiplomaticEventTriggered);
    // bind remaining delegates...
}

FString UProjectAlphaNytwatchAdapter::GetCurrentTimeString()
{
    auto T = AMainGameStateBase::GetInstance()->CurrentGameTime;
    return FString::Printf(TEXT("Day %d, Year %d"), T.Days, T.Years);
}

void UProjectAlphaNytwatchAdapter::OnDayPassed()
{
    NW->RequestDeferredPoll();
}

void UProjectAlphaNytwatchAdapter::OnSettlementCaptured(ASettlement* S)
{
    FString Header = FString::Printf(TEXT("%s captured %s from %s"),
        *S->FactionRef->Name,
        *S->Name.ToString(),
        *S->PreviousOwningFaction->Name);

    NW->LogEvent(Header, { S, S->FactionRef, S->PreviousOwningFaction });
}

void UProjectAlphaNytwatchAdapter::OnDiplomaticEventTriggered(
    AFaction* Instigator, AFaction* Target, EDiplomaticEvent Type)
{
    FString Header = FString::Printf(TEXT("%s: %s → %s"),
        *UEnum::GetValueAsString(Type),
        *Instigator->Name,
        *Target->Name);

    NW->LogEvent(Header, { Instigator, Target });
}
```

### Adapter scope

The adapter's `Initialize` is called once after game mode is ready. It is only compiled in editor builds (`#if WITH_EDITOR`) alongside the existing NytwatchSubsystem usage.

---

## Output Format

### Before (current — wall-clock, no causality)

```
## Campaign
Settlement_Thornvale | FactionRef: PlayerFaction→GreenskinHorde@5.50 | Stability: 80@0.00 -25@5.50 -15@7.20
Faction_PlayerFaction | SettlementCount: 12@0.00 -1@5.50
```

### After (game-time, narrative, grouped)

```
## Day 44, Year 2
Settlement_Thornvale  | Stability: 80→73 | Gold: 320→290 | Food: 45→38
Settlement_Millhaven  | Stability: 60→59 | Gold: 80→72

## Day 45, Year 2 | GreenskinHorde captured Thornvale from PlayerFaction
Settlement_Thornvale  | FactionRef: PlayerFaction→GreenskinHorde | Stability: 73→48 | bFlaggedForCapture: F→T→F
Faction_PlayerFaction | SettlementCount: 12→11 | AdminCount: 8→7
Faction_GreenskinHorde| SettlementCount: 8→9  | AdminCount: 6→7

## Day 45, Year 2 | DiplomaticEvent_WarDeclared: PlayerFaction → GreenskinHorde
Faction_PlayerFaction | RelationToGreenskinHorde: Neutral→AtWar
Faction_GreenskinHorde| RelationToPlayerFaction: Neutral→AtWar

## Day 46, Year 2
Settlement_Thornvale  | Stability: 48→46 | Gold: 0 (production paused)
Settlement_Millhaven  | Stability: 59→58
```

### Format rules

- Each game-day gets a `## Day N, Year Y` block for routine changes.
- Named events on the same day get a `## Day N, Year Y | <NarrativeHeader>` sub-block.
- Objects in a named event's `AffectedActors` set appear in the named block, not the routine block.
- Routine daily changes (unaffected objects) appear in the plain `## Day N, Year Y` block.
- Object identity lines carry semantic context resolved at registration time: `Settlement_Thornvale [Major | PlayerFaction | Province: Eastmarch]`.

---

## Responsibility Boundary

| Concern | Plugin | Adapter (game code) |
|---|---|---|
| UProperty diffing and snapshot | yes | no |
| Deferred poll mechanism | yes | no |
| Event grouping by affected actors | yes | no |
| Delta / transition encoding | yes | no |
| Output format, block structure | yes | no |
| Verbosity filtering | yes | no |
| Game time (`FTimeScale`) | no | yes |
| `EventsHolder` delegate bindings | no | yes |
| Narrative string composition | no | yes |
| Knowledge of `AFaction`, `ASettlement` | no | yes |

---

## Implementation Scope

### Plugin changes (NytwatchAgent)

- Add `INytwatchTimeProvider` interface
- Add `SetTimeProvider()`, `RequestDeferredPoll()`, `LogEvent()` to `UNytwatchSubsystem`
- Add deferred poll flag + pending event queue to subsystem state
- Update `Tick()` to drain the queue and run the grouped poll
- Update output format to use game-time strings and named event sub-blocks

### Server / consolidation changes

- Update consolidation script to parse the new `## Day N | Event` header format
- Game-time string becomes the primary timestamp dimension in session output
- Session metadata gains `game_time_start` / `game_time_end` fields alongside wall-clock timestamps

### Game code (per project, written once)

- `UNytwatchAdapter` (or equivalent): one `.h` / `.cpp`
- One handler per meaningful `EventsHolder` delegate
- `Initialize()` called from game mode after world is ready

### Per tracked class (already established pattern)

- `NW->RegisterObject(this)` in `BeginPlay`
- `NW->UnregisterObject(this)` in `EndPlay`
- `NytwatchVerbosity` metadata on UPROPERTY declarations

No changes to any gameplay methods. No instrumentation at property change sites.

---

## Open Questions

1. ~~**EventsHolder delegate count**~~ — resolved, see below.
2. ~~**Sub-day poll rhythm**~~ — resolved, see below.
3. **Adapter initialization timing** — `Initialize()` must be called after `EventsHolder` exists. Deferred.

---

## EventsHolder Delegate Catalogue

Full read of `EventsHolder.h`. Delegates split into three groups.

### Tracking-relevant — carry game-state parameters

| Delegate | Signature | Narrative context available |
|---|---|---|
| `SettlementCaptured` | `ASettlement*` | Capturing faction (FactionRef), previous faction (PreviousOwningFaction), settlement name, major/minor, province |
| `OnSettlementCapture` | `ASettlement*` | Same as above — fires before `SettlementCaptured`. Use `SettlementCaptured` (post-state) for tracking. |
| `SettlementCapturedAttempted` | `ASettlement*` | Settlement under siege started — useful as precursor event |
| `FactionDestroyed` | `AFaction*` | Faction name, race |
| `OnArmyBattleOver` | `ABattleManager*, AArmy* Winner, AArmy* Loser, EArmyBattleStatus x2` | Winner, loser, battle outcome type |
| `HeroDestroyed` | `AArmy*` | Army/hero name, owning faction |
| `OnDiplomaticEventTriggered` | `AFaction* Initiator, AFaction* Recipient, EDiplomacyEvent` | Full narrative from enum + factions |
| `OnAttitudeEventForLordsTriggered` | `AFaction*, AFaction*, EDiplomacyEventForAttitude` | Attitude shift between factions |
| `OnAttitudeEventForLeadersTriggered` | `UCultLeader*, UCultLeader*, EDiplomacyEventForAttitude` | Leader-level attitude shift |
| `AllianceFormed` | `AFaction*, AFaction*` | Two faction names |
| `SubjugationApplied` | `AFaction*, AFaction*` | Subjugator, subjugated |
| `PeaceDeclared` | `AFaction*, AFaction*` | Two faction names |
| `WarDeclared` | `AFaction*, AFaction*` | Two faction names |
| `OnQuestStarted` | `FQuestRuntime` | Quest name/id |
| `OnQuestCompleted` | `FQuestRuntime` | Quest name/id |

### Rhythm — no params, drive poll timing

| Delegate | Role |
|---|---|
| `DayPassed` | Primary poll trigger |
| `HourPassed` | Hour recorded in timestamp (see below) |
| `SeasonPassed` | Season transition — treat same as DayPassed |
| `YearPassed` | Year transition |
| `MinutePassed` | Not used for tracking |
| `OnTradeCycleStart` | No params — resource changes caught by DayPassed poll anyway |

### Irrelevant to tracking — UI, camera, input, rendering

`ActorSelected/Deselected`, `LMBPressed/Released`, `RMBPressed/Released`, `CameraPositionZoomChange`, `CameraLocationChanged`, `MoveCameraPawnReq`, `ChangeSettlementBannerReq`, `ChangeArmyBannerReq`, `WorldMapTransitionFadeIn/Out`, `UpdateTextRender`, `DisableTextRender`, `StartGridRender`, `StopGridRender`, `DayStartEvent`, `NightStartEvent`, `RefreshTutorial`, `RefreshStoryEvent`, `PauseTime`, `ResumeTime`, `UpdateGameTimeReq`, `ZoomEnteredVisibilityThreshold`, `FadeInCompleteReq`, `OnViewChanged`, `Player_CommandIssued`, `OnArmySelected`, `OnSettlementSelected`, `StateChanged`, `OnBattleUpdate`, `UpdateUnitsCount`.

`Player_BuildingConstructed/Upgraded/Demolished` carry no parameters — building slot changes are caught by the DayPassed poll through property diffing anyway.

---

## Sub-Day Timestamp Resolution

Day-resolution polling is sufficient. However, the **hour is recorded in the timestamp** so events that happen at different hours of the same day are distinguishable.

The adapter's `GetCurrentTimeString()` includes the hour:

```cpp
FString UProjectAlphaNytwatchAdapter::GetCurrentTimeString()
{
    auto T = AMainGameStateBase::GetInstance()->CurrentGameTime;
    return FString::Printf(TEXT("Day %d, Year %d, Hour %d"), T.Days, T.Years, T.Hours);
}
```

Output headers become:

```
## Day 45, Year 2, Hour 6
## Day 45, Year 2, Hour 14 | GreenskinHorde captured Thornvale from PlayerFaction
```

`HourPassed` is **not** a poll trigger. It is only used to keep the timestamp current so that when a named event fires mid-day, the hour is accurate. `MinutePassed` is ignored entirely.
