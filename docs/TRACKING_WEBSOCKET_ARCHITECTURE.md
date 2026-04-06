# Tracking — WebSocket Session Architecture

Describes the planned rework of the gameplay tracking pipeline, replacing the UE5 plugin's file-based session writer with a WebSocket stream to the Nytwatch server.

---

## Overview

Currently the UE5 plugin owns the full session write path: it accumulates property-change events on a background writer thread and appends them directly to a `.md` file on disk. Under the new architecture the plugin is a pure event emitter — it streams batched events to the Nytwatch server over WebSocket, and the server owns all file I/O.

```
Plugin (UE5)                     Server (Python)
─────────────────────────────    ──────────────────────────────────────
BeginPIE → open message    ───►  create temp file, begin session
Tick     → event batch     ───►  append raw events to temp file
EndPIE   → close message   ───►  run consolidation → write .md
                                 watchdog picks up .md → imports to DB
```

---

## Plugin side

### What is removed
- Background writer thread (`FNytwatchSessionWriter` / `FRunnable`)
- All `FFileHelper` / `IFileManager` session file I/O
- `EmergencyClose` file-backfill logic (crash safety moves to server)
- The `EventQueue` SPSC pipeline

### What remains
- Property polling (`FNytwatchPropertyTracker::PollObject`)
- Snapshot diffing and value serialisation
- WebSocket connection management (open/close tied to PIE lifecycle)
- Per-tick event batching before sending

### Tick interval

The minimum tracking tick interval is **0.5 seconds**. This bounds the worst-case event rate and keeps WebSocket message volume manageable.

### Per-tick batching

All property-change events produced in a single tick are collected into one WebSocket message before sending. This keeps the message rate equal to the tick rate (≤ 2 msg/s at the 0.5 s minimum) regardless of how many objects or properties changed in that tick.

### Session lifecycle messages

| Message | Sent when | Payload |
|---------|-----------|---------|
| `session_open` | `OnBeginPIE` | `session_id`, `ue_project_name`, `plugin_version`, `started_at`, `armed_systems` |
| `event_batch` | Each tick that produced changes | `session_id`, `t` (tick time, seconds from session start), `events` array |
| `session_close` | `OnEndPIE` or `OnCrash` | `session_id`, `ended_at`, `duration_seconds`, `end_reason` (`normal` / `crash`) |

### Raw event format (per event in a batch)

```json
{
  "sys":  "Combat",
  "obj":  "BP_Hero_1",
  "prop": "Health",
  "old":  "100.0",
  "new":  "87.5"
}
```

`t` (timestamp) is carried at the batch level, not per event, since all events in a batch share the same tick time.

### Crash handling

On `OnHandleSystemError`, the plugin sends a `session_close` message with `end_reason: crash` and closes the WebSocket. The server handles the rest. The plugin no longer needs to backfill any file.

---

## Server side

### Receiving events

A dedicated WebSocket endpoint handles tracking sessions. On `session_open` the server creates a temporary file:

```
Saved/Nytwatch/Sessions/.tmp/<session_id>.ndjson
```

On each `event_batch`, the server appends a line per event to the temp file:

```json
{"sys":"Combat","obj":"BP_Hero_1","prop":"Health","old":"100.0","new":"87.5","t":12.34}
```

`t` is resolved from the batch-level timestamp before writing.

The temp file is written with append-only I/O. No in-memory event accumulation — the file is the buffer.

### Session close → consolidation

On receiving `session_close`, the server runs the consolidation script over the temp file and writes the final session log:

```
Saved/Nytwatch/Sessions/<session_id>.md
```

The temp file is deleted after successful consolidation.

### Consolidation script

Single-pass over the `.ndjson` temp file. Responsibilities:

1. Group events by `sys` → `obj` → `prop`, preserving first-seen insertion order
2. Apply delta encoding for numeric properties (`PropName:InitVal +N@t -N@t`)
3. Apply transition chains for non-numeric properties (`PropName:A→B@t→C@t`)
4. Format the final `.md` with YAML front matter (using metadata from `session_open` / `session_close` messages) and one `## SystemName` section per system, one line per object
5. Strip UE class prefixes (`A`/`U`) from object names

This is equivalent to the current `BuildFlushBlock` C++ logic, ported to Python and operating over the full event set rather than per-flush batches.

### No DB write for raw events

Raw events are not stored in the database. The `.ndjson` temp file is a transient intermediate only. The existing post-consolidation import path (watchdog detects the new `.md`, `session_parser.py` parses it, inserts into DB) is unchanged.

---

## Crash / disconnect handling

| Scenario | Outcome |
|----------|---------|
| Plugin sends `session_close` with `end_reason: crash` | Consolidation runs normally; `end_reason` in front matter is `crash` |
| WebSocket disconnects without `session_close` | Server detects disconnect, treats as crash, runs consolidation on whatever arrived |
| Server restarts mid-session | Temp file is on disk; on restart the server can detect orphaned `.ndjson` files and consolidate them as crashed sessions |

---

## Scale

At 0.5 s tick interval with heavy tracking (100 objects, multiple properties each):

| Metric | Estimate |
|--------|----------|
| Events per tick | ~200–500 |
| Messages per second | 2 (one per tick) |
| Events per minute | ~12,000–30,000 |
| Events per 15 min session | ~180,000–450,000 |
| Temp file size (15 min) | ~18–45 MB |
| Consolidation input size | Same |
| Final `.md` size | Significantly smaller (delta-encoded) |

All figures are well within the capacity of sequential file I/O and a single-pass Python consolidation.
