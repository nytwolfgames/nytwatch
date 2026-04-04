# Gameplay Tracking Module — Implementation Plan

**Code-Auditor + UE5 Plugin**
Version 1.0 | April 2026

---

## 1. Executive Summary

The Gameplay Tracking Module adds a second major capability to code-auditor: capturing runtime UE5 gameplay data during Play-In-Editor (PIE) sessions and making that data available as structured markdown context files for use with Claude.

The module consists of two tightly coordinated components:

**UE5 Plugin (CodeAuditorAgent)** — An editor-only C++ plugin installed into the game project on demand. On PIE start, it reads a `CodeAuditorConfig.json` file written by code-auditor into the game's `Saved/CodeAuditor/` directory, determines which systems are armed and at what verbosity, then uses a polling tick to detect changes to tracked UProperties. It writes `session_active.lock` when PIE begins and removes it when PIE ends. Completed sessions are written as structured markdown files to `Saved/CodeAuditor/Sessions/`.

**Code-Auditor Server (Python/FastAPI)** — Gains a filesystem watcher that monitors each registered game project's `Saved/CodeAuditor/` directory. It detects PIE state changes via the lock file, shows a live PIE banner on the dashboard, and provides a Sessions management interface where users can browse, rename, bookmark, and delete session logs. Two new columns are added to the `systems` table to hold tracking configuration, and a new `sessions` table indexes imported session metadata. A new `install-plugin` CLI command copies the plugin source into a target game project.

The two components communicate entirely through the filesystem — no network connection between plugin and server is needed.

---

## 2. Repo Structure Changes

```
code-auditor/
├── src/auditor/
│   ├── tracking/                          # NEW package
│   │   ├── __init__.py
│   │   ├── watcher.py                     # Filesystem watcher (watchdog)
│   │   ├── session_parser.py              # Parse/validate session markdown
│   │   ├── session_store.py               # DB ops for sessions table
│   │   ├── config_writer.py               # Write CodeAuditorConfig.json to game dir
│   │   └── plugin_installer.py            # CLI install-plugin logic
│   └── web/
│       └── templates/
│           └── sessions.html              # NEW: sessions list page
│
└── ue5-plugin/                            # NEW top-level directory
    └── CodeAuditorAgent/
        ├── CodeAuditorAgent.uplugin
        ├── VERSION                        # Plain text: "1.0.0"
        └── Source/
            └── CodeAuditorAgent/
                ├── CodeAuditorAgent.Build.cs
                ├── Public/
                │   ├── CodeAuditorAgentModule.h
                │   ├── CodeAuditorSubsystem.h
                │   ├── CodeAuditorConfig.h
                │   ├── PropertyTracker.h
                │   └── SessionWriter.h
                └── Private/
                    ├── CodeAuditorAgentModule.cpp
                    ├── CodeAuditorSubsystem.cpp
                    ├── CodeAuditorConfig.cpp
                    ├── PropertyTracker.cpp
                    └── SessionWriter.cpp
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

### 3b. New `tracking_sessions` table

```sql
CREATE TABLE IF NOT EXISTS tracking_sessions (
    id              TEXT PRIMARY KEY,        -- slug from filename e.g. "20260404-141523"
    file_path       TEXT NOT NULL UNIQUE,    -- absolute path to .md file on disk
    project_dir     TEXT NOT NULL,           -- absolute path to UE project root
    started_at      TEXT NOT NULL,           -- ISO timestamp from session header
    ended_at        TEXT,                    -- ISO timestamp, NULL if session was aborted
    duration_secs   INTEGER,                 -- derived from header
    display_name    TEXT NOT NULL,           -- user-editable label (defaults to timestamp)
    bookmarked      INTEGER NOT NULL DEFAULT 0,
    plugin_version  TEXT NOT NULL DEFAULT '',
    ue_project_name TEXT NOT NULL DEFAULT '',
    systems_tracked TEXT NOT NULL DEFAULT '[]',   -- JSON array of system names
    event_count     INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL            -- when code-auditor first imported this session
);

CREATE INDEX IF NOT EXISTS idx_sessions_project    ON tracking_sessions(project_dir);
CREATE INDEX IF NOT EXISTS idx_sessions_bookmarked ON tracking_sessions(bookmarked);
CREATE INDEX IF NOT EXISTS idx_sessions_started    ON tracking_sessions(started_at DESC);
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

Responsibility: Generate and write `CodeAuditorConfig.json` into the game project whenever tracking configuration changes.

Key logic:
- Reads armed systems from the database (`tracking_enabled = 1`)
- Constructs the JSON payload (see Section 8 for schema)
- Writes atomically: write to `.tmp` then rename so the plugin never reads a partial file
- Called from the API handler whenever `/api/tracking/arm` is saved
- Filters out any path resolving to inside `Plugins/CodeAuditorAgent/` (auto-exclusion)

### 4b. `src/auditor/tracking/session_parser.py`

Responsibility: Parse a session markdown file from disk into a structured dict for DB import and UI display.

Key logic:
- Reads the header block (lines between `---` fences) as line-by-line `key: value` pairs
- Extracts: `started_at`, `ended_at`, `duration_seconds`, `ue_project_name`, `plugin_version`, `systems_tracked`, `event_count`
- Validates plugin version against the bundled `ue5-plugin/CodeAuditorAgent/VERSION`; logs warning if mismatched, does not reject
- Counts events by matching lines against the `[MM:SS.mm]` timestamp pattern as a cross-check
- On parse error: returns a partial dict with an `import_error` key so the UI can show a warning badge rather than silently dropping the file

### 4c. `src/auditor/tracking/session_store.py`

Responsibility: Thin wrapper around `tracking_sessions` DB methods, coordinating DB state with filesystem operations.

Key logic:
- `import_session_file(file_path, project_dir, db)` — parse then upsert; idempotent via `session_exists_for_file()`
- `rename_session(session_id, new_name, db)` — updates `display_name` in DB and renames the `.md` file on disk; handles name collision with `_2` suffix
- `delete_session(session_id, db)` — deletes the `.md` file then removes DB row; if file is already gone, removes DB row anyway
- `bookmark_session(session_id, bookmarked, db)` — DB update only

### 4d. `src/auditor/tracking/watcher.py`

Responsibility: Watchdog-based filesystem observer monitoring `Saved/CodeAuditor/` directories.

Key logic:
- Instantiated once at app startup (in `create_app()`), started in a daemon thread
- Uses `watchdog.observers.Observer` with a custom `FileSystemEventHandler` subclass
- On `session_active.lock` created: broadcast `{"type": "pie_state", "running": true, ...}` via `ws_manager`
- On `session_active.lock` deleted: broadcast `{"type": "pie_state", "running": false, ...}`, schedule session import after 1-second debounce
- On new `.md` file in `Sessions/`: call `session_store.import_session_file()`, broadcast `{"type": "session_imported", ...}`
- On `.md` file deleted externally: remove DB row if present, broadcast `{"type": "session_deleted", ...}`
- PIE state held in a module-level dict keyed by `project_dir`
- `add_watch(project_dir)` and `remove_watch(project_dir)` for dynamic registration

### 4e. `src/auditor/tracking/plugin_installer.py`

Responsibility: Logic for the `install-plugin` CLI subcommand.

Key logic:
- Validates target contains a `.uproject` file
- Checks existing installation via `VERSION` file; compares versions; prompts only on upgrade
- Copies `ue5-plugin/CodeAuditorAgent/` tree using `shutil.copytree(dirs_exist_ok=True)`
- Writes a `.auditor_install` manifest JSON for diagnostics
- Prints summary with next steps (enable plugin, recompile)

### 4f. `src/auditor/main.py` changes

- Add `install-plugin` subcommand to the argument parser: `code-auditor install-plugin --project /path [--force]`
- Add watcher startup in `create_app()` startup handler
- Add watcher shutdown in the shutdown handler

### 4g. `src/auditor/database.py` changes

- Extend `_migrate()` to add the two `systems` columns if absent
- Add `tracking_sessions` table to `SCHEMA_SQL`
- Add all new method bodies listed in Section 3c
- Update `list_systems()`, `replace_systems()`, `upsert_system()` to include tracking fields

---

## 5. UE5 Plugin Implementation Scope

The plugin is editor-only (`"Type": "Editor"` in `.uplugin`). It never compiles into shipping builds.

### 5a. `CodeAuditorAgent.uplugin`

```json
{
  "FileVersion": 3,
  "Version": 1,
  "VersionName": "1.0.0",
  "FriendlyName": "Code Auditor Agent",
  "Description": "Runtime gameplay tracking for code-auditor static analysis tool.",
  "Category": "Editor",
  "EnabledByDefault": false,
  "CanContainContent": false,
  "Modules": [
    {
      "Name": "CodeAuditorAgent",
      "Type": "Editor",
      "LoadingPhase": "Default"
    }
  ]
}
```

### 5b. `CodeAuditorAgent.Build.cs`

Module dependencies:
- `"Core"`, `"CoreUObject"`, `"Engine"` — UObject reflection and FProperty access
- `"UnrealEd"` — `FEditorDelegates` (PIE start/end hooks)
- `"Json"`, `"JsonUtilities"` — reading `CodeAuditorConfig.json`

Version injection:
```csharp
PublicDefinitions.Add("AUDITOR_PLUGIN_VERSION=\"1.0.0\"");
```

### 5c. `CodeAuditorConfig.h / .cpp`

Responsibility: Load and represent `CodeAuditorConfig.json`.

Data structures:
```cpp
enum class EAuditorVerbosity : uint8 { Critical, Standard, Verbose, Ignore };

struct FCodeAuditorSystemConfig
{
    FString SystemName;
    EAuditorVerbosity VerbosityCeiling;
    TArray<FString> Paths;
};

struct FCodeAuditorConfig
{
    FString PluginCompatVersion;
    TArray<FCodeAuditorSystemConfig> ArmedSystems;
    bool bValid = false;
};
```

Implementation:
- `static FCodeAuditorConfig Load(const FString& ProjectDir)` — reads `Saved/CodeAuditor/CodeAuditorConfig.json` with `FFileHelper::LoadFileToString`, parses with `TJsonReader`
- Returns `bValid = false` if file is absent or malformed; caller treats this as tracking disabled

### 5d. `PropertyTracker.h / .cpp`

Responsibility: Enumerate tracked UProperties on a UObject and emit change events.

Key design:
- Snapshot stored as `TMap<FString, FString>` keyed by `"ClassName::PropertyName"`, value is previous serialised value
- Value serialisation uses `FProperty::ExportText_Direct()` — works for all numeric, boolean, enum, FName, FString, FVector, FRotator types
- `FObjectProperty` / `FWeakObjectProperty` values serialised as `GetName()` string, not dereferenced

**Three-tier verbosity resolution** (most specific wins, applied at snapshot time):
1. `UPROPERTY(meta=(AuditorVerbosity="Ignore"))` → always skip
2. `UPROPERTY(meta=(AuditorVerbosity="..."))` → compare against system ceiling
3. `UCLASS(meta=(AuditorVerbosity="..."))` → class-level default
4. System ceiling as final fallback
5. UI ceiling is a hard cap: if system set to `Critical`, `Verbose`-tagged properties are skipped

Ceiling stacking:
- `Critical` → Critical only
- `Standard` → Critical + Standard
- `Verbose` → Critical + Standard + Verbose
- `Ignore` → never tracked, regardless of ceiling

API:
- `void SnapshotObject(UObject* Obj)` — initial snapshot, no events emitted
- `void PollObject(UObject* Obj, float PIETimeSeconds, TArray<FTrackingEvent>& OutEvents)` — diff current vs snapshot, emit events for changed properties, update snapshot
- `FTrackingEvent` struct: `{ FString ClassName; FString PropertyName; FString OldValue; FString NewValue; float TimeSeconds; }`

Object discovery:
- Iterates `TObjectIterator<UObject>` each tick
- Filters by whether the object's source path falls within an armed system's configured paths
- Configurable cap (default: 2000 objects) prevents runaway performance impact
- Skips CDOs, transient objects, and objects with `RF_Unreachable` flag

### 5e. `SessionWriter.h / .cpp`

Responsibility: Buffer events during PIE and flush to `.md` when PIE ends.

Key design:
- Holds in-memory `TArray<FTrackingEvent>` buffer
- `void Open(const FCodeAuditorConfig& Config, const FString& ProjectDir)` — records start time, creates `Saved/CodeAuditor/Sessions/` if absent
- `void AppendEvent(const FTrackingEvent& Event)` — appends to buffer
- `void Close(const FString& ProjectDir)` — serialises buffer to markdown, writes with `FFileHelper::SaveStringToFile`, deletes `session_active.lock`
- File naming: `YYYYMMDD-HHmmss.md` using session start time
- If `Close()` is called with zero events, still writes a valid minimal session file

### 5f. `CodeAuditorSubsystem.h / .cpp`

Responsibility: `UEditorSubsystem` that owns the full session lifecycle.

Key design:
- Inherits `UEditorSubsystem` — instantiated automatically by the editor, no manual registration needed
- `Initialize()` — binds `FEditorDelegates::BeginPIE` and `FEditorDelegates::EndPIE`

`OnBeginPIE(bool bIsSimulating)`:
1. Load `CodeAuditorConfig.json` via `FCodeAuditorConfig::Load()`
2. If invalid or no armed systems: set `bTrackingActive = false`, return
3. Write `session_active.lock` using `FFileHelper::SaveStringToFile`
4. Call `SessionWriter.Open()`
5. Register tick delegate: `FTicker::GetCoreTicker().AddTicker(...)`
6. Log: `[CodeAuditorAgent] Tracking session started. Armed: X systems.`

`Tick(float DeltaTime)`:
1. Accumulate `PIEElapsedSeconds`
2. Iterate tracked objects, call `PropertyTracker.PollObject()`
3. Append events to `SessionWriter`
4. Default tick interval: 0.1s (10 Hz), configurable via `CodeAuditorConfig.json`

`OnEndPIE(bool bIsSimulating)`:
1. Unregister tick delegate
2. Call `SessionWriter.Close()` — writes file, removes lock
3. Set `bTrackingActive = false`
4. Log: `[CodeAuditorAgent] Session closed. X events written.`

### 5g. `CodeAuditorAgentModule.h / .cpp`

Responsibility: `IModuleInterface` implementation. Minimal — subsystem handles the real work.

- `StartupModule()` — log plugin version only
- `ShutdownModule()` — no-op

---

## 6. API Changes

### New endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/tracking/pie-state` | Current PIE state for all watched projects |
| `GET` | `/api/sessions` | List sessions. Query params: `project_dir`, `bookmarked_only`, `q`, `limit` |
| `GET` | `/api/sessions/{id}` | Single session detail |
| `POST` | `/api/sessions/{id}/rename` | Body: `{"display_name": "..."}`. Renames DB record and file on disk |
| `POST` | `/api/sessions/{id}/bookmark` | Body: `{"bookmarked": true/false}` |
| `DELETE` | `/api/sessions/{id}` | Deletes DB record and `.md` file. Returns error if bookmarked |
| `GET` | `/api/sessions/{id}/content` | Returns raw markdown text (for inline preview) |
| `GET` | `/api/tracking/arm` | Returns current tracking config for all systems |
| `POST` | `/api/tracking/arm` | Body: `{"systems": [{name, tracking_enabled, tracking_verbosity}]}`. Saves to DB and writes `CodeAuditorConfig.json` |

### Modified endpoints

| Method | Path | Change |
|---|---|---|
| `GET` | `/api/systems` | Include `tracking_enabled` and `tracking_verbosity` in response |
| `POST` | `/api/systems` | Persist `tracking_enabled` and `tracking_verbosity` |
| `GET` | `/` (dashboard) | Pass `pie_state` to template context |

### WebSocket new message types

```python
# In ws_manager.py
def push_pie_state(self, project_dir, running, armed_systems, event_count, started_at): ...
def push_session_imported(self, session): ...
```

Message shapes:
```json
{"type": "pie_state", "project_dir": "...", "running": true, "armed_systems": ["Combat", "AI"], "event_count": 142, "started_at": "2026-04-04T14:15:23Z"}
{"type": "session_imported", "session": { ... }}
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
- Live elapsed duration counter (updated every second via `setInterval`)
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
- Name filter input (client-side, debounced 200ms)
- "Bookmarked only" toggle
- Sorted: bookmarked first (pinned), then by `started_at` descending
- Each row: bookmark star toggle, display name, timestamp, duration, system count badges, event count
- Bookmarked rows have visual accent (left border or background tint)

**Session detail panel** (shown on row click or `?session={id}` in URL):
- Header block: project name, plugin version, duration, start/end times, systems tracked
- Per-system event count bar chart (horizontal, relative proportions)
- Timeline density strip: 1D strip divided into time buckets coloured by event density
- Log access: "Open File" button, "Copy Path" button, "Preview" toggle
- Inline preview: scrollable `<pre>` with raw markdown from `/api/sessions/{id}/content`
- Inline rename: click display name to edit, confirm with Enter or blur
- Delete button: confirmation modal mentioning file will be deleted from disk; disabled if bookmarked (tooltip: "Unbookmark before deleting")

### 7d. Settings page changes

Each system row gains a tracking control section (collapsed by default):
- Toggle switch: "Arm for Tracking"
- When armed: reveal verbosity selector (Critical / Standard / Verbose)
- Saves via `POST /api/tracking/arm`
- System name shows small "Tracked" badge when `tracking_enabled = 1`
- Systems whose paths resolve entirely inside `Plugins/CodeAuditorAgent/` are greyed out with tooltip: "This is the code-auditor plugin itself and cannot be tracked."

### 7e. Dashboard changes (`dashboard.html`)

- Systems table: new "Tracked" dot badge column; clicking badge navigates to settings tracking section
- New "Recent Sessions" card below the Systems table: shows 3 most recent sessions with links to Sessions page
- If no sessions exist and no systems are armed: shows "Set up tracking" call-to-action

---

## 8. Config / File Formats

### 8a. `CodeAuditorConfig.json` (written by code-auditor, read by plugin)

Location: `<GameProject>/Saved/CodeAuditor/CodeAuditorConfig.json`

```json
{
  "version": "1.0.0",
  "generated_at": "2026-04-04T14:10:00Z",
  "armed_systems": [
    {
      "name": "Combat",
      "verbosity_ceiling": "Standard",
      "paths": ["Source/DragonRacer/Combat/"]
    },
    {
      "name": "AI",
      "verbosity_ceiling": "Critical",
      "paths": ["Source/DragonRacer/AI/"]
    }
  ],
  "object_scan_cap": 2000,
  "tick_interval_seconds": 0.1
}
```

Schema rules:
- `version`: Semver string. Plugin logs warning on minor mismatch, rejects on major mismatch.
- `verbosity_ceiling`: One of `"Critical"`, `"Standard"`, `"Verbose"`.
- `paths`: Repo-relative paths matching `SystemDef.paths` from the database.
- `object_scan_cap`: Max UObjects inspected per tick. Plugin silently truncates at this limit.
- `tick_interval_seconds`: How frequently the property tracker runs (default 0.1s = 10 Hz).

Written atomically: code-auditor writes to `.tmp` then renames.

### 8b. `session_active.lock` (written by plugin, watched by code-auditor)

Location: `<GameProject>/Saved/CodeAuditor/session_active.lock`

Contents: single-line JSON for diagnostics. Code-auditor only checks file presence/absence.

```json
{"started_at": "2026-04-04T14:15:23Z", "plugin_version": "1.0.0", "pid": 12345}
```

### 8c. Session log format

Location: `<GameProject>/Saved/CodeAuditor/Sessions/YYYYMMDD-HHmmss.md`

```markdown
---
ue_project_name: DragonRacer
plugin_version: 1.0.0
started_at: 2026-04-04T14:15:23Z
ended_at: 2026-04-04T14:17:45Z
duration_seconds: 142
systems_tracked: Combat, AI
event_count: 87
---

# Gameplay Session — DragonRacer — 2026-04-04 14:15:23

## Combat

[00:03.42] ASpearEnemy::CurrentHealth  100.000000 → 75.000000
[00:03.42] ASpearEnemy::bIsStaggered  False → True
[00:07.18] ASpearEnemy::CurrentHealth  75.000000 → 0.000000

## AI

[00:03.50] UDragonAIController::CurrentState  Patrol → Combat
[00:03.51] UDragonAIController::TargetActor  None → BP_Player_C_0
```

Format rules:
- Header is a line-by-line `key: value` block between `---` fences (no full YAML parser required)
- Events grouped by system under `## SystemName` headings
- Event lines begin with `[MM:SS.mm]` where `mm` is centiseconds (2 digits); timestamps are relative to PIE start
- Values use `FProperty::ExportText_Direct()` output verbatim
- Systems with zero events have their `##` heading omitted entirely
- The `OldValue → NewValue` separator uses Unicode right arrow `→` (U+2192)

### 8d. Plugin `VERSION` file

Location: `ue5-plugin/CodeAuditorAgent/VERSION`

Contents: a single semver line e.g. `1.0.0`. This is the authoritative version source. The value is injected at build time via a `PublicDefinitions` macro in `Build.cs`.

---

## 9. Install Flow — `install-plugin` CLI Command

### Invocation

```
code-auditor install-plugin --project /path/to/GameProject [--force]
```

### Step-by-step logic

1. **Locate plugin source**: resolve `ue5-plugin/CodeAuditorAgent/` relative to the installed package using `importlib.resources`. Plugin source must be included in the wheel as package data.

2. **Validate target**: check that `<project>/` contains exactly one `.uproject` file. Print the discovered project name. On failure: print error, exit non-zero.

3. **Check existing installation**: look for `<project>/Plugins/CodeAuditorAgent/VERSION`.
   - Absent: proceed with fresh install
   - Present, version matches: print "Already installed. Use `--force` to reinstall." — exit 0
   - Present, version differs: print "Upgrading from X to Y." — proceed
   - Present, `--force` flag: print "Reinstalling (--force)." — proceed

4. **Create target directory**: `<project>/Plugins/CodeAuditorAgent/`

5. **Copy files**: `shutil.copytree(source, dest, dirs_exist_ok=True)`

6. **Write install manifest**: `<project>/Plugins/CodeAuditorAgent/.auditor_install` as JSON with `source_version`, `installed_at`, `code_auditor_version`. Used for diagnostics only.

7. **Print summary**:
   ```
   CodeAuditorAgent plugin installed successfully.

   Location : /path/to/GameProject/Plugins/CodeAuditorAgent/
   Version  : 1.0.0

   Next steps:
     1. Open your project in the Unreal Editor
     2. Enable "Code Auditor Agent" in Edit > Plugins
     3. Recompile the project
     4. Start the code-auditor server and arm systems from Settings
   ```

8. **Exit 0** on success.

### `pyproject.toml` changes

```toml
[tool.hatch.build.targets.wheel.shared-data]
"ue5-plugin" = "share/code-auditor/ue5-plugin"
```

---

## 10. Auto-Exclusion Logic

When code-auditor is used with a game project that has the `CodeAuditorAgent` plugin installed, the plugin's own source code must be excluded from both static analysis and gameplay tracking.

### Scan exclusion (`config.py` / `source_detector.py`)

In `detect_systems_from_repo()`:
```python
EXCLUDED_PLUGIN_NAMES = {"CodeAuditorAgent"}
candidates = [c for c in candidates if c["name"] not in EXCLUDED_PLUGIN_NAMES]
```

In source directory heuristic classification:
```python
# Auto-ignore the code-auditor agent plugin directory itself
if item.is_dir() and item.name == "CodeAuditorAgent":
    classified[normalize_path(str(item.relative_to(repo)))] = "ignored"
```

### Tracking exclusion (`config_writer.py`)

When generating `CodeAuditorConfig.json`, filter out any system path that resolves to inside `Plugins/CodeAuditorAgent/`. Enforced server-side regardless of what the user has armed.

### Plugin self-exclusion (`PropertyTracker.cpp`)

The plugin skips any UObject whose source module name is `"CodeAuditorAgent"`. Defensive, belt-and-suspenders.

### UI exclusion (`settings.html`)

Systems whose paths resolve entirely inside `Plugins/CodeAuditorAgent/` are rendered greyed-out with a tooltip: "This is the code-auditor plugin itself and cannot be tracked."

---

## 11. Phased Rollout

### Phase 1 — MVP (Core Tracking Loop)

Everything needed to go from zero to a working, logged session.

**Backend**:
- Database migration (two new `systems` columns, `tracking_sessions` table)
- `session_parser.py`
- `session_store.py`
- `watcher.py`
- `config_writer.py`
- `plugin_installer.py` and `install-plugin` CLI subcommand
- New API endpoints: `/api/tracking/arm`, `/api/sessions/*`, `/api/tracking/pie-state`
- Watcher startup/shutdown in `create_app()`
- All DB method additions

**UE5 Plugin**:
- All source files as described in Section 5
- Lock file write/delete
- `FProperty::ExportText_Direct()` value snapshotting
- Three-tier verbosity resolution
- Session markdown writer
- `UEditorSubsystem` lifecycle hooks

**UI**:
- PIE banner in `base.html` (all three states)
- Sessions page (`sessions.html`) — list with rename, bookmark, delete, basic detail view
- Settings tracking arm toggles and verbosity selector

### Phase 2 — Polish

- Session detail panel: per-system event count bars, timeline density strip
- Session import on server startup (re-import sessions created while server was offline)
- Live event count during active PIE (plugin appends running count to lock file on each tick flush)
- Debounced bulk session import
- Dashboard "Recent Sessions" card
- "Tracked" badge on Systems table rows
- `--force` flag for install-plugin
- Session pruning API and UI control

### Phase 3 — Future

- Blueprint property support (requires a separate approach — likely a Blueprint function library sending events via UDP loopback to avoid the reflection gap)
- Live streaming mode: plugin writes to a rolling append-only log; code-auditor tails it and serves via Server-Sent Events
- Session diff view: compare two sessions side-by-side
- Multi-project watcher support
- Session annotation: user markdown notes stored in DB alongside session
- "Copy for Claude" button: wraps session markdown in a Claude context block with a preamble

---

## 12. Open Questions

The following must be resolved before implementation begins:

**Q1: UE version compatibility floor**
The plan assumes UE 5.0+ (`FProperty` API). If UE 4.25–4.27 support is needed, `#if ENGINE_MAJOR_VERSION` guards are required throughout the plugin.

**Q2: Object discovery scope**
`TObjectIterator<UObject>` includes CDOs, transient objects, and garbage. Should the tracker filter to only `AActor` and `UActorComponent` subclasses (lower scope, lower overhead) or all UObjects (broader coverage)? Decision required before implementing `PropertyTracker::PollObject()`.

**Q3: Tick frequency and performance budget**
Is there a maximum acceptable overhead target (e.g., "must not cost more than 2ms per frame")? This informs whether the object scan needs to be amortized across frames or can run synchronously each tick.

**Q4: Multi-project watcher registration**
When the user switches projects in the dashboard, should the watcher automatically re-target the new project's `Saved/CodeAuditor/` directory? The watcher's `add_watch()` / `remove_watch()` design supports this, but it needs to explicitly hook into the project-switch API endpoint.

**Q5: Session file naming collision**
`YYYYMMDD-HHmmss.md` has a one-second collision window. Should the plugin append a random suffix to guarantee uniqueness (e.g. `YYYYMMDD-HHmmss-a3f2.md`), or is sub-second collision acceptable in practice?

**Q6: Paths as repo-relative vs absolute in config JSON**
`paths` in `CodeAuditorConfig.json` are currently repo-relative. The plugin needs to resolve them to absolute paths. If the UE project root differs from the repo root (common with monorepos), an offset must be accounted for. Simplest fix: embed absolute paths in the generated config instead of repo-relative ones.

**Q7: Watchdog on Windows network drives**
`watchdog` uses `ReadDirectoryChangesW` on Windows, which has known issues with network shares. Are game projects ever expected to live on network drives? If so, a polling fallback interval should be specified in the watcher config.

**Q8: Version mismatch in-editor notification**
On major version mismatch between plugin and server config, should the plugin surface an in-editor notification via `FNotificationManager`, or only log to the Output Log?

**Q9: Bookmarked session file deleted externally**
If a bookmarked session's `.md` file is deleted from disk outside code-auditor, should the watcher: (a) remove the DB record regardless, (b) keep it as an "orphaned" record with a warning badge, or (c) something else?

**Q10: Metadata key namespace**
`AuditorVerbosity` as a UPROPERTY metadata key could collide with another tool. Should it be namespaced to `CodeAuditorVerbosity`? Changing it after release would require all users to update their annotations.
