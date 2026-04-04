# Nytwatch — Gameplay Tracking Module Implementation Plan

**Nytwolf Games | Nytwatch + NytwatchAgent UE5 Plugin**
Version 1.2 | April 2026

---

## 1. Executive Summary

Nytwatch is a gameplay telemetry module that adds runtime tracking capability to the existing static analysis platform. It captures UE5 gameplay data during Play-In-Editor (PIE) sessions and produces structured markdown session logs for use as Claude context.

The module consists of two tightly coordinated components:

**NytwatchAgent (UE5 Plugin)** — An editor-only C++ plugin installed into the game project on demand. On PIE start, it reads a `NytwatchConfig.json` file written by the Nytwatch server into the game's `Saved/Nytwatch/` directory, determines which systems are armed and at what verbosity, then uses a configurable-rate polling tick to detect changes to tracked UProperties across all UObjects belonging to armed systems. It writes `nytwatch.lock` when PIE begins and removes it when PIE ends. Completed sessions are written as structured markdown files to `Saved/Nytwatch/Sessions/`, named by a unique session UUID.

**Nytwatch Server (Python/FastAPI)** — Gains a filesystem watcher that monitors each registered game project's `Saved/Nytwatch/` directory. It detects PIE state changes via the lock file, shows a live PIE banner on the dashboard, and provides a Sessions management interface where users can browse, rename, bookmark, and delete session logs. Two new columns are added to the `systems` table to hold tracking configuration, and a new `nytwatch_sessions` table indexes imported session metadata. A new `nytwatch install-plugin` CLI command copies the plugin source into a target game project. When the user switches active projects in the dashboard, the watcher automatically re-targets the new project — provided NytwatchAgent is installed there.

The two components communicate entirely through the filesystem — no network connection between plugin and server is needed.

---

## 2. Repo Structure Changes

```
nytwatch/                                  # repo root (renamed from code-auditor)
├── src/auditor/
│   ├── tracking/                          # NEW package
│   │   ├── __init__.py
│   │   ├── watcher.py                     # Filesystem watcher (watchdog)
│   │   ├── session_parser.py              # Parse/validate session markdown
│   │   ├── session_store.py               # DB ops for nytwatch_sessions table
│   │   ├── config_writer.py               # Write NytwatchConfig.json to game dir
│   │   └── plugin_installer.py            # CLI install-plugin logic
│   └── web/
│       └── templates/
│           └── sessions.html              # NEW: sessions list page
│
└── ue5-plugin/                            # NEW top-level directory
    └── NytwatchAgent/
        ├── NytwatchAgent.uplugin
        ├── VERSION                        # Plain text: "1.0.0"
        └── Source/
            └── NytwatchAgent/
                ├── NytwatchAgent.Build.cs
                ├── Public/
                │   ├── NytwatchAgentModule.h
                │   ├── NytwatchSubsystem.h
                │   ├── NytwatchConfig.h
                │   ├── NytwatchPropertyTracker.h
                │   └── NytwatchSessionWriter.h
                └── Private/
                    ├── NytwatchAgentModule.cpp
                    ├── NytwatchSubsystem.cpp
                    ├── NytwatchConfig.cpp
                    ├── NytwatchPropertyTracker.cpp
                    └── NytwatchSessionWriter.cpp
```

New Python dependency to add to `pyproject.toml`:

```
watchdog>=4.0.0
```

---

## 3. Database Changes

### 3a. New columns on `systems` table (added via `_migrate()`, not destructively)

```sql
ALTER TABLE systems ADD COLUMN tracking_enabled   INTEGER NOT NULL DEFAULT 0;
ALTER TABLE systems ADD COLUMN tracking_verbosity TEXT    NOT NULL DEFAULT 'Standard';
```

### 3b. New `nytwatch_sessions` table

```sql
CREATE TABLE IF NOT EXISTS nytwatch_sessions (
    id              TEXT PRIMARY KEY,        -- UUID4 session ID (matches filename)
    file_path       TEXT NOT NULL UNIQUE,    -- absolute path to .md file on disk
    project_dir     TEXT NOT NULL,           -- absolute path to UE project root
    started_at      TEXT NOT NULL,           -- ISO timestamp from session header
    ended_at        TEXT,                    -- ISO timestamp, NULL if session was aborted
    duration_secs   INTEGER,                 -- derived from header
    display_name    TEXT NOT NULL,           -- user-editable label (defaults to session ID)
    bookmarked      INTEGER NOT NULL DEFAULT 0,
    plugin_version  TEXT NOT NULL DEFAULT '',
    ue_project_name TEXT NOT NULL DEFAULT '',
    systems_tracked TEXT NOT NULL DEFAULT '[]',   -- JSON array of system names
    event_count     INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL            -- when Nytwatch first imported this session
);

CREATE INDEX IF NOT EXISTS idx_nytwatch_sessions_project    ON nytwatch_sessions(project_dir);
CREATE INDEX IF NOT EXISTS idx_nytwatch_sessions_bookmarked ON nytwatch_sessions(bookmarked);
CREATE INDEX IF NOT EXISTS idx_nytwatch_sessions_started    ON nytwatch_sessions(started_at DESC);
```

### 3c. New database methods (added to `Database` class)

```python
# Systems (tracking columns)
get_armed_systems() -> list[dict]
set_system_tracking(name: str, enabled: bool, verbosity: str) -> None

# Sessions
insert_session(session: dict) -> None
get_session(session_id: str) -> Optional[dict]
list_sessions(project_dir: Optional[str], bookmarked_only: bool, name_filter: str, limit: int) -> list[dict]
update_session(session_id: str, **kwargs) -> None
delete_session(session_id: str) -> None
session_exists_for_file(file_path: str) -> bool
```

Also update `list_systems()`, `replace_systems()`, and `upsert_system()` to include `tracking_enabled` and `tracking_verbosity`.

---

## 4. Backend Implementation Scope

### 4a. `src/auditor/tracking/config_writer.py`

Responsibility: Generate and write `NytwatchConfig.json` into the game project whenever tracking configuration changes.

Key logic:
- Reads armed systems from the database (`tracking_enabled = 1`)
- Constructs the JSON payload (see Section 8a for schema)
- Resolves system `paths` to absolute paths using the registered game project root — paths in the config are absolute, not repo-relative
- Writes atomically: write to `.tmp` then rename so the plugin never reads a partial file
- Called from the API handler whenever `POST /api/nytwatch/arm` is saved
- Filters out any path resolving to inside `Plugins/NytwatchAgent/` (auto-exclusion)

### 4b. `src/auditor/tracking/session_parser.py`

Responsibility: Parse a session markdown file from disk into a structured dict for DB import and UI display.

Key logic:
- Reads the header block (lines between `---` fences) as line-by-line `key: value` pairs — no full YAML parser required
- Extracts: `session_id`, `started_at`, `ended_at`, `duration_seconds`, `ue_project_name`, `plugin_version`, `systems_tracked`, `event_count`
- Validates plugin version against the bundled `ue5-plugin/NytwatchAgent/VERSION`; logs warning on minor mismatch, logs error on major mismatch but does not reject
- Counts events by matching lines against the `[MM:SS.mm]` timestamp pattern as a cross-check
- On parse error: returns a partial dict with an `import_error` key so the UI can show a warning badge rather than silently dropping the file

### 4c. `src/auditor/tracking/session_store.py`

Responsibility: Thin wrapper around `nytwatch_sessions` DB methods, coordinating DB state with filesystem operations.

Key logic:
- `import_session_file(file_path, project_dir, db)` — parse then upsert; idempotent via `session_exists_for_file()`
- `rename_session(session_id, new_name, db)` — updates `display_name` in DB and renames the `.md` file on disk; handles name collision with `_2` suffix
- `delete_session(session_id, db)` — deletes the `.md` file then removes DB row; if file is already gone, removes DB row anyway
- `bookmark_session(session_id, bookmarked, db)` — DB update only

### 4d. `src/auditor/tracking/watcher.py`

Responsibility: Watchdog-based filesystem observer monitoring `Saved/Nytwatch/` directories.

Key logic:
- Instantiated once at app startup (in `create_app()`), started in a daemon thread
- Uses `watchdog.observers.Observer` with a custom `FileSystemEventHandler` subclass
- On `nytwatch.lock` created: broadcast `{"type": "pie_state", "running": true, ...}` via `ws_manager`
- On `nytwatch.lock` deleted: broadcast `{"type": "pie_state", "running": false, ...}`, schedule session import after 1-second debounce
- On new `.md` file in `Sessions/`: call `session_store.import_session_file()`, broadcast `{"type": "session_imported", ...}`
- On `.md` file deleted externally: remove DB row regardless of bookmark status, broadcast `{"type": "session_deleted", ...}`
- PIE state held in a module-level dict keyed by `project_dir`
- `add_watch(project_dir)` — registers a new watch path; called automatically when the user switches to a project that has NytwatchAgent installed (detected by presence of `Saved/Nytwatch/` directory)
- `remove_watch(project_dir)` — unregisters; called when switching away from a project
- Project switch hook: the existing `/settings/switch-project` endpoint must call `watcher.remove_watch(old_dir)` and `watcher.add_watch(new_dir)` if the new project has NytwatchAgent installed

### 4e. `src/auditor/tracking/plugin_installer.py`

Responsibility: Logic for the `nytwatch install-plugin` CLI subcommand.

Key logic:
- Validates target contains a `.uproject` file
- Checks existing installation via `VERSION` file; compares versions; prompts only on upgrade
- Copies `ue5-plugin/NytwatchAgent/` tree using `shutil.copytree(dirs_exist_ok=True)`
- Writes a `.nytwatch_install` manifest JSON for diagnostics
- Prints summary with next steps (enable plugin, recompile)

### 4f. `src/auditor/main.py` changes

- Rename CLI entrypoint from `code-auditor` to `nytwatch` in `pyproject.toml`
- Add `install-plugin` subcommand: `nytwatch install-plugin --project /path [--force]`
- Add watcher startup in `create_app()` startup handler
- Add watcher shutdown in the shutdown handler

### 4g. `src/auditor/database.py` changes

- Extend `_migrate()` to add the two `systems` columns if absent
- Add `nytwatch_sessions` table to `SCHEMA_SQL`
- Add all new method bodies listed in Section 3c
- Update `list_systems()`, `replace_systems()`, `upsert_system()` to include tracking fields

---

## 5. UE5 Plugin Implementation Scope

**UE version requirement: 5.7 and above only.** No version guards needed; assume latest `FProperty` APIs throughout.

The plugin is editor-only (`"Type": "Editor"` in `.uplugin`). It never compiles into shipping builds.

### 5a. `NytwatchAgent.uplugin`

```json
{
  "FileVersion": 3,
  "Version": 1,
  "VersionName": "1.0.0",
  "FriendlyName": "Nytwatch Agent",
  "Description": "Runtime gameplay tracking for Nytwatch by Nytwolf Games.",
  "Category": "Editor",
  "EnabledByDefault": false,
  "CanContainContent": false,
  "Modules": [
    {
      "Name": "NytwatchAgent",
      "Type": "Editor",
      "LoadingPhase": "Default"
    }
  ]
}
```

### 5b. `NytwatchAgent.Build.cs`

Module dependencies:
- `"Core"`, `"CoreUObject"`, `"Engine"` — UObject reflection and FProperty access
- `"UnrealEd"` — `FEditorDelegates` (PIE start/end hooks)
- `"Json"`, `"JsonUtilities"` — reading `NytwatchConfig.json`
- `"Slate"`, `"SlateCore"`, `"EditorStyle"` — `FNotificationManager` for in-editor popups

Version injection:
```csharp
PublicDefinitions.Add("NYTWATCH_PLUGIN_VERSION=\"1.0.0\"");
```

### 5c. `NytwatchConfig.h / .cpp`

Responsibility: Load and represent `NytwatchConfig.json`.

Data structures:
```cpp
enum class ENytwatchVerbosity : uint8 { Critical, Standard, Verbose, Ignore };

struct FNytwatchSystemConfig
{
    FString SystemName;
    ENytwatchVerbosity VerbosityCeiling;
    TArray<FString> AbsolutePaths;    // absolute paths, written by Nytwatch server
};

struct FNytwatchConfig
{
    FString PluginCompatVersion;
    TArray<FNytwatchSystemConfig> ArmedSystems;
    int32   ObjectScanCap;            // default 2000
    float   TickIntervalSeconds;      // default 0.1
    bool    bValid = false;
};
```

Implementation:
- `static FNytwatchConfig Load(const FString& ProjectDir)` — reads `Saved/Nytwatch/NytwatchConfig.json` with `FFileHelper::LoadFileToString`, parses with `TJsonReader`
- Returns `bValid = false` if file is absent or malformed; caller treats this as tracking disabled
- On major version mismatch: shows an in-editor `FNotificationManager` popup ("Nytwatch plugin version mismatch — re-run `nytwatch install-plugin`") and sets `bValid = false`
- On minor version mismatch: logs warning to Output Log only, continues normally

### 5d. `NytwatchPropertyTracker.h / .cpp`

Responsibility: Enumerate tracked UProperties on all UObjects belonging to armed systems and emit change events.

Key design:
- Snapshot stored as `TMap<FString, FString>` keyed by `"ObjectName::ClassName::PropertyName"`, value is previous serialised value — `ObjectName` disambiguates multiple instances of the same class
- Value serialisation uses `FProperty::ExportText_Direct()` — works for all numeric, boolean, enum, FName, FString, FVector, FRotator types
- `FObjectProperty` / `FWeakObjectProperty` values serialised as the referenced object's `GetName()` string, not dereferenced further

**Object discovery scope**: all UObjects (`TObjectIterator<UObject>`), filtered to those whose class source path falls within an armed system's absolute paths. Applies to all UObject subclasses — Actors, Components, subsystems, data assets, etc.

**Object scan cap**: configurable via `NytwatchConfig.json` (`object_scan_cap`, default 2000). Plugin prioritises previously-seen objects when truncating at cap.

Skip conditions per object:
- Has `RF_ClassDefaultObject` flag (CDO)
- Has `RF_Unreachable` or `RF_PendingKill` flags
- Has `RF_Transient` flag and is not a registered Actor
- Source module name is `"NytwatchAgent"` (self-exclusion)

**Three-tier verbosity resolution** (most specific wins, evaluated per property per object):

1. `UPROPERTY(meta=(NytwatchVerbosity="Ignore"))` → always skip, hard excluded
2. `UPROPERTY(meta=(NytwatchVerbosity="Critical|Standard|Verbose"))` → compare against system ceiling
3. `UCLASS(meta=(NytwatchVerbosity="..."))` → class-level default for all untagged properties
4. System ceiling (from UI) as final fallback for untagged properties

Ceiling stacking (additive upward):
- `Critical` → Critical only
- `Standard` → Critical + Standard
- `Verbose` → Critical + Standard + Verbose
- `Ignore` → never tracked regardless of ceiling level

UI ceiling is a **hard cap**: if a system is set to `Critical` in the dashboard, `Verbose`-tagged properties are silently skipped even if they explicitly requested to be tracked.

Usage in game code:
```cpp
UCLASS(meta=(NytwatchVerbosity="Standard"))
class APlayerCharacter : public ACharacter
{
    UPROPERTY(meta=(NytwatchVerbosity="Critical"))
    float CurrentHealth;          // promoted above class default

    UPROPERTY()
    FVector CurrentVelocity;      // inherits Standard from class

    UPROPERTY(meta=(NytwatchVerbosity="Verbose"))
    float AccelerationDelta;      // only tracked at Verbose ceiling

    UPROPERTY(meta=(NytwatchVerbosity="Ignore"))
    float CachedFrameDelta;       // never tracked
};
```

API:
- `void SnapshotObject(UObject* Obj, const FNytwatchSystemConfig& System)` — initial snapshot, no events emitted
- `void PollObject(UObject* Obj, const FNytwatchSystemConfig& System, float PIETimeSeconds, TArray<FNytwatchEvent>& OutEvents)` — diff current vs snapshot, emit events for changed properties, update snapshot
- `FNytwatchEvent` struct: `{ FString SystemName; FString ObjectName; FString ClassName; FString PropertyName; FString OldValue; FString NewValue; float TimeSeconds; }`

### 5e. `NytwatchSessionWriter.h / .cpp`

Responsibility: Buffer events during PIE and flush to a uniquely-identified `.md` file when PIE ends.

Key design:
- Holds in-memory `TArray<FNytwatchEvent>` buffer
- Session ID generated at `Open()` time as a UUID4 string via `FGuid::NewGuid().ToString(EGuidFormats::DigitsWithHyphens)`
- `void Open(const FNytwatchConfig& Config, const FString& ProjectDir)` — generates session ID, records start time, creates `Saved/Nytwatch/Sessions/` if absent
- `void AppendEvent(const FNytwatchEvent& Event)` — appends to buffer
- `void Close(const FString& ProjectDir)` — serialises buffer to markdown format, writes with `FFileHelper::SaveStringToFile`, then deletes `nytwatch.lock`
- File naming: `<session_id>.md` (UUID4) — guarantees uniqueness, no collision window
- The session ID is also embedded in the markdown header for cross-referencing
- If `Close()` is called with zero events, still writes a valid minimal session file

### 5f. `NytwatchSubsystem.h / .cpp`

Responsibility: `UEditorSubsystem` that owns the full session lifecycle.

Key design:
- Inherits `UEditorSubsystem` — instantiated automatically by the editor, no manual registration needed
- `Initialize()` — binds `FEditorDelegates::BeginPIE` and `FEditorDelegates::EndPIE`

`OnBeginPIE(bool bIsSimulating)`:
1. Load `NytwatchConfig.json` via `FNytwatchConfig::Load()`
2. If `bValid = false` or no armed systems: set `bTrackingActive = false`, return
3. Write `nytwatch.lock` using `FFileHelper::SaveStringToFile`
4. Call `NytwatchSessionWriter.Open()`
5. Register tick delegate at configured interval: `FTicker::GetCoreTicker().AddTicker(FTickerDelegate::CreateUObject(this, &UNytwatchSubsystem::Tick), Config.TickIntervalSeconds)`
6. Log to Output Log: `[NytwatchAgent] Tracking session started. Armed: X systems.`

`Tick(float DeltaTime)`:
1. Accumulate `PIEElapsedSeconds`
2. Iterate all UObjects via `TObjectIterator<UObject>`, determine which armed system (if any) owns each object by checking absolute paths against the object's class source path
3. Call `NytwatchPropertyTracker.PollObject()` for each matching object
4. Append emitted events to `NytwatchSessionWriter`

`OnEndPIE(bool bIsSimulating)`:
1. Unregister tick delegate
2. Call `NytwatchSessionWriter.Close()` — writes file, removes `nytwatch.lock`
3. Set `bTrackingActive = false`
4. Log: `[NytwatchAgent] Session closed. X events written to <session_id>.md`

**Tick interval**: read from `NytwatchConfig.json` (`tick_interval_seconds`). Configurable from the Nytwatch Settings UI, written into the config file. Default: 0.1s (10 Hz).

### 5g. `NytwatchAgentModule.h / .cpp`

Responsibility: `IModuleInterface` implementation. Minimal — subsystem handles the real work.

- `StartupModule()` — log plugin version to Output Log only
- `ShutdownModule()` — no-op

---

## 6. API Changes

### New endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/nytwatch/pie-state` | Current PIE state for all watched projects |
| `GET` | `/api/sessions` | List sessions. Query params: `project_dir`, `bookmarked_only`, `q`, `limit` |
| `GET` | `/api/sessions/{id}` | Single session detail |
| `POST` | `/api/sessions/{id}/rename` | Body: `{"display_name": "..."}`. Renames DB record and file on disk |
| `POST` | `/api/sessions/{id}/bookmark` | Body: `{"bookmarked": true/false}` |
| `DELETE` | `/api/sessions/{id}` | Deletes DB record and `.md` file. Returns error if bookmarked |
| `GET` | `/api/sessions/{id}/content` | Returns raw markdown text of the session file (for inline preview) |
| `GET` | `/api/nytwatch/arm` | Returns current tracking config for all systems |
| `POST` | `/api/nytwatch/arm` | Body: `{"systems": [{name, tracking_enabled, tracking_verbosity}], "tick_interval_seconds": 0.1}`. Saves to DB and writes `NytwatchConfig.json` |

### Modified endpoints

| Method | Path | Change |
|---|---|---|
| `GET` | `/api/systems` | Include `tracking_enabled` and `tracking_verbosity` in response |
| `POST` | `/api/systems` | Persist `tracking_enabled` and `tracking_verbosity` |
| `GET` | `/` (dashboard) | Pass `pie_state` to template context |
| `POST` | `/settings/switch-project` | After switch, call `watcher.remove_watch(old)` / `watcher.add_watch(new)` if NytwatchAgent is installed in new project |

### WebSocket new message types

```python
# In ws_manager.py
def push_pie_state(self, project_dir, running, armed_systems, event_count, started_at): ...
def push_session_imported(self, session): ...
def push_session_deleted(self, session_id): ...
```

Message shapes:
```json
{"type": "pie_state", "project_dir": "...", "running": true, "armed_systems": ["Combat", "AI"], "event_count": 142, "started_at": "2026-04-04T14:15:23Z"}
{"type": "session_imported", "session": { ... }}
{"type": "session_deleted", "session_id": "..."}
```

---

## 7. UI Changes

### 7a. Sidebar (`base.html`)

Add a "Sessions" link in the sidebar nav.

### 7b. Active PIE Banner (`base.html`)

Persistent banner element always present in DOM, hidden by default. Three mutually exclusive states controlled via a `data-pie-state` attribute:

**Recording** (PIE active, systems armed):
- Pulsing red dot (CSS `@keyframes` animation)
- Text: "Recording — {SystemA}, {SystemB}"
- Live elapsed duration counter (updated every second via `setInterval`, initialised from `started_at` in the WS message)
- Event count (updated on each `pie_state` WS push, ~3s server intervals)

**Session saved** (PIE ended, session imported):
- Green checkmark
- Text: "Session saved — {display_name}" with link to `/sessions?highlight={id}`
- Auto-fades after 6 seconds

**Warning** (PIE active, no systems armed):
- Amber warning icon
- Text: "PIE is running but no systems are armed"
- Link to settings tracking section

### 7c. Sessions page (`sessions.html`, route: `/sessions`)

**Session list panel**:
- Name filter input (client-side, debounced 200ms, matches against `display_name`)
- "Bookmarked only" toggle
- Sorted: bookmarked first (pinned), then by `started_at` descending
- Each row: bookmark star toggle, display name (or session ID if unnamed), timestamp, duration, system count badges, event count
- Bookmarked rows have visual accent (left border or background tint)

**Session detail panel** (shown on row click or `?session={id}` in URL):
- Header block: project name, session ID, plugin version, duration, start/end times, systems tracked
- Per-system event count bar chart (horizontal bars, relative proportions)
- Timeline density strip: 1D strip divided into equal time buckets coloured by event density (light → dark)
- Log access: "Open File" button (opens containing folder), "Copy Path" button, "Preview" toggle
- Inline preview: scrollable `<pre>` with raw markdown fetched from `/api/sessions/{id}/content`
- Inline rename: click display name to edit, confirm with Enter or blur; calls `/api/sessions/{id}/rename`
- Delete button: confirmation modal explicitly mentioning the file will be deleted from disk; disabled if bookmarked (tooltip: "Unbookmark before deleting")

### 7d. Settings page changes

**Nytwatch Tracking** section added to each system row (collapsed by default, expandable):
- Toggle switch: "Arm for Tracking"
- When armed: reveals verbosity selector (Critical / Standard / Verbose)
- Tick interval field (shared across all systems): number input in seconds, saved via `POST /api/nytwatch/arm`
- System name shows small "Tracked" badge when `tracking_enabled = 1`
- Systems whose paths resolve entirely inside `Plugins/NytwatchAgent/` are rendered greyed-out with tooltip: "This is the NytwatchAgent plugin itself and cannot be tracked."

### 7e. Dashboard changes (`dashboard.html`)

- Systems table: new "Tracked" dot badge column; clicking badge expands the Nytwatch tracking section for that system in settings
- New "Recent Sessions" card below the Systems table: shows 3 most recent sessions with links to the Sessions page
- If no sessions exist and no systems are armed: shows "Set up Nytwatch tracking" call-to-action

---

## 8. Config / File Formats

### 8a. `NytwatchConfig.json` (written by Nytwatch server, read by NytwatchAgent plugin)

**Purpose**: tells the plugin which systems to track, at what verbosity, and with what performance settings. Written by the Nytwatch server when the user saves tracking configuration. Read by the plugin on every PIE start.

Location: `<GameProject>/Saved/Nytwatch/NytwatchConfig.json`

```json
{
  "version": "1.0.0",
  "generated_at": "2026-04-04T14:10:00Z",
  "armed_systems": [
    {
      "name": "Combat",
      "verbosity_ceiling": "Standard",
      "paths": [
        "C:/Projects/DragonRacer/Source/DragonRacer/Combat/"
      ]
    },
    {
      "name": "AI",
      "verbosity_ceiling": "Critical",
      "paths": [
        "C:/Projects/DragonRacer/Source/DragonRacer/AI/"
      ]
    }
  ],
  "object_scan_cap": 2000,
  "tick_interval_seconds": 0.1
}
```

Schema rules:
- `version`: Semver string matching the plugin's `VERSION` file. Plugin logs warning on minor mismatch; shows `FNotificationManager` popup and refuses to track on major mismatch.
- `verbosity_ceiling`: One of `"Critical"`, `"Standard"`, `"Verbose"`. Acts as a hard UI ceiling.
- `paths`: **Absolute filesystem paths** (not repo-relative). Nytwatch server resolves these from `SystemDef.paths` + the registered game project root before writing.
- `object_scan_cap`: Max UObjects inspected per tick cycle. Plugin prioritises previously-seen objects when truncating.
- `tick_interval_seconds`: Polling frequency. Configurable from Nytwatch Settings UI.

Written atomically: server writes to `NytwatchConfig.json.tmp` then renames.

### 8b. `nytwatch.lock` (written by plugin, watched by Nytwatch server)

Location: `<GameProject>/Saved/Nytwatch/nytwatch.lock`

Contents: single-line JSON for diagnostics. Nytwatch watcher only checks file presence/absence, not contents.

```json
{"session_id": "550e8400-e29b-41d4-a716-446655440000", "started_at": "2026-04-04T14:15:23Z", "plugin_version": "1.0.0", "pid": 12345}
```

### 8c. Session log format

Location: `<GameProject>/Saved/Nytwatch/Sessions/<session_id>.md`

The filename is a UUID4 generated by the plugin at session start. Guarantees uniqueness with no collision window.

```markdown
---
session_id: 550e8400-e29b-41d4-a716-446655440000
ue_project_name: DragonRacer
plugin_version: 1.0.0
started_at: 2026-04-04T14:15:23Z
ended_at: 2026-04-04T14:17:45Z
duration_seconds: 142
systems_tracked: Combat, AI
event_count: 87
---

# Nytwatch Session — DragonRacer — 2026-04-04 14:15:23

## Combat

[00:03.42] ASpearEnemy::CurrentHealth  100.000000 → 75.000000
[00:03.42] ASpearEnemy::bIsStaggered  False → True
[00:07.18] ASpearEnemy::CurrentHealth  75.000000 → 0.000000

## AI

[00:03.50] UDragonAIController::CurrentState  Patrol → Combat
[00:03.51] UDragonAIController::TargetActor  None → BP_Player_C_0
```

Format rules:
- Header is a line-by-line `key: value` block between `---` fences — no full YAML parser required to read
- `session_id` in the header matches the filename (without `.md` extension)
- Events are grouped by system under `## SystemName` headings
- Event lines begin with `[MM:SS.mm]` where `mm` is centiseconds (2 digits); timestamps are relative to PIE start
- Values use `FProperty::ExportText_Direct()` output verbatim
- Systems with zero events have their `##` heading omitted entirely
- The `OldValue → NewValue` separator uses Unicode right arrow `→` (U+2192)
- The `display_name` (user-set friendly label) lives only in the database — it is not written back to the file

### 8d. Plugin `VERSION` file

Location: `ue5-plugin/NytwatchAgent/VERSION`

Contents: a single semver line e.g. `1.0.0`. The authoritative version source. The value is injected at build time via a `PublicDefinitions` macro in `NytwatchAgent.Build.cs` and embedded in session headers and the lock file.

---

## 9. Install Flow — `nytwatch install-plugin` CLI Command

### Invocation

```
nytwatch install-plugin --project /path/to/GameProject [--force]
```

### Step-by-step logic

1. **Locate plugin source**: resolve `ue5-plugin/NytwatchAgent/` relative to the installed package using `importlib.resources`. Plugin source must be included in the wheel as package data (see `pyproject.toml` changes below).

2. **Validate target**: check that `<project>/` contains exactly one `.uproject` file. Print the discovered project name. On failure: print error, exit non-zero.

3. **Check existing installation**: look for `<project>/Plugins/NytwatchAgent/VERSION`.
   - Absent: proceed with fresh install
   - Present, version matches: print "Already installed at current version. Use `--force` to reinstall." — exit 0
   - Present, version differs: print "Upgrading from X to Y." — proceed
   - Present, `--force` flag: print "Reinstalling (--force)." — proceed

4. **Create target directory**: `<project>/Plugins/NytwatchAgent/`

5. **Copy files**: `shutil.copytree(source, dest, dirs_exist_ok=True)`

6. **Write install manifest**: `<project>/Plugins/NytwatchAgent/.nytwatch_install` as JSON with `source_version`, `installed_at`, `nytwatch_version`. Used for diagnostics only, not read by the plugin.

7. **Print summary**:
   ```
   NytwatchAgent plugin installed successfully.

   Location : /path/to/GameProject/Plugins/NytwatchAgent/
   Version  : 1.0.0

   Next steps:
     1. Open your project in the Unreal Editor
     2. Enable "Nytwatch Agent" in Edit > Plugins
     3. Recompile the project
     4. Start the Nytwatch server and arm systems from Settings
   ```

8. **Exit 0** on success.

### `pyproject.toml` changes

```toml
[project.scripts]
nytwatch = "auditor.main:main"

[tool.hatch.build.targets.wheel.shared-data]
"ue5-plugin" = "share/nytwatch/ue5-plugin"
```

---

## 10. Auto-Exclusion Logic

When Nytwatch is used with a game project that has NytwatchAgent installed, the plugin's own source code must be excluded from both static analysis and gameplay tracking.

### Scan exclusion (`config.py` / `source_detector.py`)

In `detect_systems_from_repo()`:
```python
EXCLUDED_PLUGIN_NAMES = {"NytwatchAgent"}
candidates = [c for c in candidates if c["name"] not in EXCLUDED_PLUGIN_NAMES]
```

In source directory heuristic classification:
```python
# Auto-ignore the NytwatchAgent plugin directory itself
if item.is_dir() and item.name == "NytwatchAgent":
    classified[normalize_path(str(item.relative_to(repo)))] = "ignored"
```

### Tracking exclusion (`config_writer.py`)

When generating `NytwatchConfig.json`, filter out any system path that resolves to inside `Plugins/NytwatchAgent/`. Enforced server-side regardless of what the user has armed.

### Plugin self-exclusion (`NytwatchPropertyTracker.cpp`)

The plugin skips any UObject whose source module name is `"NytwatchAgent"`. Defensive belt-and-suspenders check.

### UI exclusion (`settings.html`)

Systems whose paths resolve entirely inside `Plugins/NytwatchAgent/` are rendered greyed-out with tooltip: "This is the NytwatchAgent plugin itself and cannot be tracked."

---

## 11. Phased Rollout

### Phase 1 — MVP (Core Tracking Loop)

Everything needed to go from zero to a working, logged session.

**Backend**:
- Database migration (two new `systems` columns, `nytwatch_sessions` table)
- `session_parser.py`
- `session_store.py`
- `watcher.py` (including project-switch hook)
- `config_writer.py` (with absolute path resolution)
- `plugin_installer.py` and `nytwatch install-plugin` CLI subcommand
- New API endpoints: `/api/nytwatch/arm`, `/api/sessions/*`, `/api/nytwatch/pie-state`
- Watcher startup/shutdown in `create_app()`
- All DB method additions
- CLI entrypoint renamed from `code-auditor` to `nytwatch`

**NytwatchAgent Plugin**:
- All source files as described in Section 5
- UUID4 session ID generation
- `nytwatch.lock` write/delete
- `FProperty::ExportText_Direct()` value snapshotting across all UObjects
- Three-tier `NytwatchVerbosity` metadata resolution
- `FNotificationManager` popup on major version mismatch
- Session markdown writer
- `UEditorSubsystem` lifecycle hooks
- Configurable tick interval from `NytwatchConfig.json`

**UI**:
- PIE banner in `base.html` (all three states)
- Sessions page (`sessions.html`) — list with rename, bookmark, delete, basic detail view
- Settings Nytwatch tracking arm toggles, verbosity selector, tick interval field

### Phase 2 — Polish

- Session detail panel: per-system event count bars, timeline density strip
- Session import on server startup (re-import sessions created while server was offline)
- Live event count during active PIE (plugin writes running count into `nytwatch.lock` on each tick flush)
- Debounced bulk session import
- Dashboard "Recent Sessions" card
- "Tracked" badge on Systems table rows
- `--force` flag for `nytwatch install-plugin`
- Session pruning API and UI control (delete all non-bookmarked sessions older than N days)

### Phase 3 — Future

- Blueprint property support (separate approach — likely a Blueprint function library sending events via UDP loopback)
- Live streaming mode: plugin writes to a rolling append-only log; Nytwatch server tails it and serves via Server-Sent Events
- Session diff view: compare two sessions side-by-side
- Multi-project simultaneous watcher support
- Session annotation: user markdown notes stored in DB alongside session
- "Copy for Claude" button: wraps session markdown in a Claude context block with a preamble

---

## 12. Resolved Decisions

| # | Question | Decision |
|---|---|---|
| Q1 | UE version floor | UE 5.7+ only. No version guards needed. |
| Q2 | Object discovery scope | All UObjects, filtered by armed system paths. Skips CDOs, unreachable, transient non-Actors, and self (NytwatchAgent module). |
| Q3 | Tick frequency | Configurable from Nytwatch Settings UI. Written into `NytwatchConfig.json`. Default 0.1s (10 Hz). |
| Q4 | Project switching | Watcher automatically re-targets on project switch — only if `Saved/Nytwatch/` exists in the new project (indicating NytwatchAgent is installed). |
| Q5 | Session filename collision | Each session uses a UUID4 as its ID and filename (`<uuid>.md`). Zero collision risk. Same ID embedded in log header and lock file. |
| Q6 | Paths in config JSON | Absolute filesystem paths. Nytwatch server resolves repo-relative `SystemDef.paths` to absolute using the registered game project root before writing. |
| Q7 | Network drives | Not supported. `watchdog` on Windows (`ReadDirectoryChangesW`) used as-is. |
| Q8 | Version mismatch notification | Minor mismatch: Output Log warning only. Major mismatch: `FNotificationManager` in-editor popup + tracking disabled. |
| Q9 | Session deleted from disk | DB record removed unconditionally regardless of bookmark status. No orphaned records. |
| Q10 | Metadata key name | `NytwatchVerbosity` — applied to both `UCLASS` and `UPROPERTY` metadata tags throughout the plugin and all game code annotations. |

---

## 13. Naming Reference

| Thing | Name |
|---|---|
| Product | Nytwatch |
| Company | Nytwolf Games |
| UE5 Plugin | NytwatchAgent |
| Plugin folder in game | `Plugins/NytwatchAgent/` |
| UPROPERTY/UCLASS tag | `NytwatchVerbosity` |
| Server config file | `NytwatchConfig.json` |
| Game saved directory | `Saved/Nytwatch/` |
| Lock file | `nytwatch.lock` |
| Sessions folder | `Saved/Nytwatch/Sessions/` |
| CLI entrypoint | `nytwatch` |
| Install command | `nytwatch install-plugin` |
| DB sessions table | `nytwatch_sessions` |
| Install manifest | `.nytwatch_install` |
| Build macro | `NYTWATCH_PLUGIN_VERSION` |
