# Tracking — WebSocket Session Architecture

Describes the gameplay tracking pipeline. The UE5 plugin streams batched property-change events to the Nytwatch server over WebSocket; the server owns all file I/O and session consolidation.

---

## Overview

The plugin is a pure event emitter — it polls registered objects each tick, diffs property values, and sends any changes to the server. The server buffers events to a temp file, consolidates them into a final `.md` log on session close, and imports the result into the DB.

```
Plugin (UE5)                     Server (Python)
─────────────────────────────    ──────────────────────────────────────
BeginPIE → session_open    ───►  create temp .ndjson, write header line
Tick     → event_batch     ───►  append events to temp file
EndPIE   → session_close   ───►  consolidate → write .md → import to DB
```

---

## Files

| File | Role |
|------|------|
| `ue5-plugin/.../NytwatchSessionWriter.h/.cpp` | WebSocket connection + message sending |
| `ue5-plugin/.../NytwatchSubsystem.h/.cpp` | PIE lifecycle, object registry, tick loop |
| `ue5-plugin/.../NytwatchConfig.h/.cpp` | Config loading; exposes `WebSocketUrl` |
| `src/nytwatch/tracking/tracking_ws.py` | Server-side WebSocket handler |
| `src/nytwatch/tracking/consolidator.py` | `.ndjson` → `.md` consolidation |
| `src/nytwatch/tracking/session_store.py` | DB import after consolidation |
| `src/nytwatch/web/routes.py` | `/ws/tracking` endpoint |
| `src/nytwatch/main.py` | Startup orphan recovery |
| `src/nytwatch/tracking/config_writer.py` | Writes `tracking_ws_url` into `NytwatchConfig.json` |

---

## Plugin side

### What was removed
- Background writer thread (`FNytwatchSessionWriter` / `FRunnable`)
- All `FFileHelper` / `IFileManager` session file I/O
- `EmergencyClose` file-backfill logic
- The `EventQueue` SPSC pipeline

### What remains / is new
- Property polling (`FNytwatchPropertyTracker::PollObject`) — unchanged
- Snapshot diffing and value serialisation — unchanged
- `FNytwatchSessionWriter` — rewritten as a WebSocket sender (no threads)
- Per-tick event batching before sending

### Object registration

Objects must opt in by calling `RegisterObject` / `UnregisterObject` from `BeginPlay` / `EndPlay`:

```cpp
// BeginPlay
if (auto* NW = UNytwatchSubsystem::Get())
    NW->RegisterObject(this);

// EndPlay
if (auto* NW = UNytwatchSubsystem::Get())
    NW->UnregisterObject(this);
```

Only registered objects are polled. Objects whose class source path does not fall within any armed system's paths are silently ignored at `RegisterObject` time.

### Tick interval

Configurable in the Nytwatch Settings UI; written into `NytwatchConfig.json`. Default 0.1 s (10 Hz). The architecture document previously specified a 0.5 s minimum, but there is no enforced floor — the configured value is used as-is.

### Per-tick batching

All property-change events from a single tick are collected into one `event_batch` message. The tick timestamp `t` is carried at the batch level; individual events do not repeat it.

### Session lifecycle messages

| Message | Sent when | Key payload fields |
|---------|-----------|-------------------|
| `session_open` | `OnBeginPIE`, after WebSocket connects | `session_id`, `ue_project_name`, `plugin_version`, `started_at`, `armed_systems` |
| `event_batch` | Each tick that produced changes | `session_id`, `t` (seconds from session start), `events` array |
| `session_close` | `OnEndPIE` or `OnCrash` | `session_id`, `ended_at`, `duration_seconds`, `end_reason` (`normal` / `crash`) |

### Raw event format (per event in a batch)

```json
{
  "sys":  "Combat",
  "obj":  "BP_Hero_1",
  "prop": "Health",
  "old":  "100.0",
  "new":  "87.5",
  "num":  1
}
```

`num` is `1` for numeric properties, `0` otherwise. The server uses this to select delta encoding vs. transition chains during consolidation.

### WebSocket URL

The server writes `tracking_ws_url` into `NytwatchConfig.json` when tracking is configured:

```
ws://127.0.0.1:8420/ws/tracking?project_dir=<url-encoded-project-dir>
```

The plugin reads this at `OnBeginPIE` and connects to it. The `project_dir` query param is used server-side to route events to the correct DB and session directory.

### Connection timeout

The plugin waits up to **30 seconds** for the WebSocket handshake to complete after `OnBeginPIE`. If the connection is not established within that window, `bTrackingActive` is set to `false` and tracking is disabled for the session.

### Server offline at PIE start

If the WebSocket connection cannot be established at all (server not running), `OnConnectionError` fires, `IsOpen()` returns `false`, and the subsystem's next tick sets `bTrackingActive = false`. No retry at session start — tracking either starts cleanly or not at all.

### Mid-session disconnect (transient)

If the WebSocket drops during an active session, `OnClosed` sets `bNeedsReconnect = true`. The subsystem checks `NeedsReconnect()` on each tick and calls `TryReconnect()`. Up to 5 reconnect attempts are made. Events produced while disconnected are discarded (not buffered). On reconnect the plugin resumes sending batches for the existing session using the same `session_id`; the server resumes appending to the existing temp file.

### Crash handling

On `OnHandleSystemError`, the plugin calls `EmergencyClose`, which sends `session_close` with `end_reason: crash` and closes the WebSocket. The lock file is intentionally left on disk so the watchdog's crash poller detects the dead PID.

---

## Server side

### Endpoint

```
WebSocket /ws/tracking?project_dir=<url-encoded-path>
```

Defined in `routes.py`. Delegates entirely to `TrackingWebSocketHandler.handle()` in `tracking_ws.py`. One handler instance lives on `app.state.tracking_ws_handler` for the server lifetime.

### Receiving events

On `session_open` the server creates:

```
Saved/Nytwatch/Sessions/.tmp/<session_id>.ndjson
```

The **first line** is the `session_open` payload written as JSON with `"type": "session_open"`:

```json
{"type":"session_open","session_id":"...","ue_project_name":"...","plugin_version":"...","started_at":"...","armed_systems":["Combat","AI"]}
```

This makes the temp file self-contained — consolidation reads metadata from it directly, with no reliance on in-memory state. This is critical for orphan recovery.

On each `event_batch`, one line is appended per event with `t` resolved from the batch timestamp:

```json
{"sys":"Combat","obj":"BP_Hero_1","prop":"Health","old":"100.0","new":"87.5","num":1,"t":12.34}
```

The temp file is written with append-only I/O. No in-memory event accumulation.

### Session close → consolidation

On `session_close`, the server appends the close record as the final line:

```json
{"type":"session_close","session_id":"...","ended_at":"...","duration_seconds":51,"end_reason":"normal"}
```

Then runs consolidation (`consolidator.py`) in a thread executor and writes:

```
Saved/Nytwatch/Sessions/<session_id>.md
```

The temp file is deleted after successful consolidation. The `.md` is then imported into the DB via `session_store.import_session_file`, and a `session_imported` WebSocket message is broadcast to connected browsers.

### Consolidation (`consolidator.py`)

Single-pass over the `.ndjson` temp file:

1. Parse the first line as the `session_open` header
2. Parse the last line as the `session_close` record (for `end_reason`, `ended_at`, `duration_seconds`)
3. Group events by `sys` → `obj` → `prop`, preserving first-seen insertion order
4. For numeric properties (`num: 1`): delta encoding — `PropName:InitVal +N@t -N@t`
5. For non-numeric properties: transition chains — `PropName:A→B@t→C@t`
6. Strip UE class prefixes (`A`/`U`) from object names
7. Write `.md` with YAML front matter and one `## SystemName` section per system, one line per object

### No DB write for raw events

Raw events are not stored in the database. The `.ndjson` temp file is a transient intermediate only.

---

## Crash / disconnect handling

| Scenario | Outcome |
|----------|---------|
| Plugin sends `session_close end_reason: crash` | Consolidation runs normally; `end_reason: crash` in front matter |
| Plugin sends `session_close end_reason: normal` then immediately closes socket | Server checks temp file for existing close record before treating as crash — normal `end_reason` is preserved |
| WebSocket disconnects without any `session_close` | Server treats as crash, consolidates whatever arrived |
| Server restarts mid-session | On startup, server consolidates all orphan `.ndjson` files as crashed sessions |

### Race: `session_close` + immediate socket close

The plugin sends `session_close` and then calls `WebSocket->Close()` synchronously. The close frame can arrive at the server before the text frame is processed. The server handles this by checking the last line of the temp file when a disconnect fires without a `session_close` message having been received in the handler loop. If a `session_close` record is found on disk, its `end_reason` is used as-is.

### Orphan recovery on server startup

On startup, the server scans `Saved/Nytwatch/Sessions/.tmp/` for any `.ndjson` files left over from a previous run. For each file found:

1. Read the `session_open` header line to recover metadata
2. Append a `session_close` record with `end_reason: crash`
3. Run consolidation → write `.md`
4. Delete the temp file

The `.md` is picked up by the session import path and inserted into the DB normally.

---

## Scale

At 0.1 s tick interval (default) with heavy tracking (100 objects, multiple properties each):

| Metric | Estimate |
|--------|----------|
| Events per tick | ~200–500 |
| Messages per second | 10 (one per tick) |
| Events per minute | ~120,000–300,000 |
| Events per 15 min session | ~1.8M–4.5M |
| Temp file size (15 min) | ~180–450 MB |
| Final `.md` size | Significantly smaller (delta-encoded) |

At 0.5 s tick interval (minimum recommended for long sessions):

| Metric | Estimate |
|--------|----------|
| Events per tick | ~200–500 |
| Messages per second | 2 |
| Events per 15 min session | ~180,000–450,000 |
| Temp file size (15 min) | ~18–45 MB |

Consolidation is a single-pass Python script; both sizes are well within the capacity of sequential file I/O.
