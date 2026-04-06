from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from fastapi import WebSocket, WebSocketDisconnect

if TYPE_CHECKING:
    from nytwatch.database import Database
    from nytwatch.ws_manager import ConnectionManager

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _elapsed_seconds(started_at: str) -> int:
    try:
        start = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        return max(0, int((datetime.now(timezone.utc) - start).total_seconds()))
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# In-memory session state
# ---------------------------------------------------------------------------

class _TrackingSession:
    __slots__ = ("session_id", "tmp_path", "started_at")

    def __init__(self, session_id: str, tmp_path: Path, started_at: str) -> None:
        self.session_id = session_id
        self.tmp_path   = tmp_path
        self.started_at = started_at


# ---------------------------------------------------------------------------
# TrackingWebSocketHandler
# ---------------------------------------------------------------------------

class TrackingWebSocketHandler:
    """Manages active plugin tracking WebSocket connections.

    One instance lives on ``app.state.tracking_ws_handler`` for the lifetime
    of the server process.  It holds the dict of in-progress sessions so that
    reconnects within the same process can resume the right temp file.
    """

    def __init__(self) -> None:
        # session_id → _TrackingSession (only present while PIE is active)
        self._sessions: dict[str, _TrackingSession] = {}

    # ── Public entry points ──────────────────────────────────────────────────

    async def handle(
        self,
        websocket: WebSocket,
        project_dir: str,
        ws_manager: "ConnectionManager",
        db: Optional["Database"],
    ) -> None:
        """Async coroutine that drives a single plugin WebSocket connection."""
        await websocket.accept()
        sessions_dir = _sessions_dir(project_dir)
        current: Optional[_TrackingSession] = None

        try:
            while True:
                raw = await websocket.receive_text()
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    log.warning("tracking ws: invalid JSON from plugin (project=%s)", project_dir)
                    continue

                msg_type = msg.get("type")

                if msg_type == "session_open":
                    current = self._on_session_open(msg, sessions_dir, project_dir, ws_manager)

                elif msg_type == "event_batch":
                    if current:
                        self._on_event_batch(msg, current)

                elif msg_type == "session_close":
                    if current:
                        await self._close_session(
                            current,
                            ended_at=msg.get("ended_at", _now_iso()),
                            duration_seconds=msg.get("duration_seconds", _elapsed_seconds(current.started_at)),
                            end_reason=msg.get("end_reason", "normal"),
                            project_dir=project_dir,
                            ws_manager=ws_manager,
                            db=db,
                        )
                        current = None

        except WebSocketDisconnect:
            if current:
                # Check whether a session_close record landed in the temp file
                # before the disconnect was detected (race between text frame and
                # close frame when the plugin sends both in rapid succession).
                existing = _read_close_record(current.tmp_path)
                if existing:
                    log.info(
                        "tracking ws: disconnect after session_close (race) — "
                        "using on-disk close record (session=%s, end_reason=%s)",
                        current.session_id, existing.get("end_reason"),
                    )
                    await self._consolidate_and_import(current, project_dir, ws_manager, db)
                    self._sessions.pop(current.session_id, None)
                    ws_manager.push_pie_state(
                        project_dir=project_dir,
                        running=False,
                        armed_systems=[],
                        event_count=None,
                        started_at=None,
                        crashed=(existing.get("end_reason") == "crash"),
                    )
                else:
                    log.warning(
                        "tracking ws: plugin disconnected without session_close — treating as crash "
                        "(session=%s)", current.session_id,
                    )
                    await self._close_session(
                        current,
                        ended_at=_now_iso(),
                        duration_seconds=_elapsed_seconds(current.started_at),
                        end_reason="crash",
                        project_dir=project_dir,
                        ws_manager=ws_manager,
                        db=db,
                    )
        except Exception:
            log.exception("tracking ws: unexpected error (project=%s)", project_dir)
            if current:
                await self._close_session(
                    current,
                    ended_at=_now_iso(),
                    duration_seconds=_elapsed_seconds(current.started_at),
                    end_reason="crash",
                    project_dir=project_dir,
                    ws_manager=ws_manager,
                    db=db,
                )

    async def recover_orphans(
        self,
        project_dir: str,
        ws_manager: "ConnectionManager",
        db: Optional["Database"],
    ) -> None:
        """Consolidate any .ndjson files left over from a previous server run.

        Called once per project on startup (or when a project is registered).
        """
        tmp_dir = _sessions_dir(project_dir) / ".tmp"
        if not tmp_dir.exists():
            return

        for ndjson_path in sorted(tmp_dir.glob("*.ndjson")):
            session_id = ndjson_path.stem
            if session_id in self._sessions:
                continue  # currently active — skip

            log.warning(
                "tracking ws: orphan session %s — consolidating as crash", session_id
            )
            try:
                await self._recover_one_orphan(ndjson_path, project_dir, ws_manager, db)
            except Exception:
                log.exception(
                    "tracking ws: orphan recovery failed for %s", session_id
                )

    # ── Session lifecycle helpers ────────────────────────────────────────────

    def _on_session_open(
        self,
        msg: dict,
        sessions_dir: Path,
        project_dir: str,
        ws_manager: "ConnectionManager",
    ) -> Optional[_TrackingSession]:
        session_id = msg.get("session_id", "")
        if not session_id:
            log.error("tracking ws: session_open missing session_id")
            return None

        tmp_dir  = sessions_dir / ".tmp"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        tmp_path = tmp_dir / f"{session_id}.ndjson"

        started_at = msg.get("started_at", _now_iso())

        if tmp_path.exists():
            # Reconnect: temp file already on disk — continue from where we left off.
            log.info("tracking ws: reconnect for session %s", session_id)
        else:
            # New session: write the session_open header line.
            header = {
                "type":             "session_open",
                "session_id":       session_id,
                "ue_project_name":  msg.get("ue_project_name", ""),
                "plugin_version":   msg.get("plugin_version",  ""),
                "started_at":       started_at,
                "armed_systems":    msg.get("armed_systems",   []),
            }
            tmp_path.write_text(json.dumps(header) + "\n", encoding="utf-8")
            log.info("tracking ws: session opened %s", session_id)

        session = _TrackingSession(session_id, tmp_path, started_at)
        self._sessions[session_id] = session

        armed = msg.get("armed_systems", [])
        ws_manager.push_pie_state(
            project_dir=project_dir,
            running=True,
            armed_systems=armed if isinstance(armed, list) else list(armed),
            event_count=0,
            started_at=started_at,
        )
        return session

    def _on_event_batch(self, msg: dict, session: _TrackingSession) -> None:
        t:      float     = float(msg.get("t", 0.0))
        events: list[dict] = msg.get("events", [])
        if not events:
            return

        # Batch-level causality fields — propagated into every event record.
        tl: str = msg.get("time_label",   "")  # game-time label from INytwatchTimeProvider
        eh: str = msg.get("event_header", "")  # narrative header from LogEvent()

        lines: list[str] = []
        for evt in events:
            record: dict = {
                "sys":  evt.get("sys",  ""),
                "obj":  evt.get("obj",  ""),
                "prop": evt.get("prop", ""),
                "old":  evt.get("old",  ""),
                "new":  evt.get("new",  ""),
                "t":    t,
            }
            if "num" in evt:
                record["num"] = evt["num"]
            if tl:
                record["tl"] = tl
            if eh:
                record["eh"] = eh
            lines.append(json.dumps(record))

        with session.tmp_path.open("a", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")

    async def _close_session(
        self,
        session: _TrackingSession,
        ended_at: str,
        duration_seconds: int,
        end_reason: str,
        project_dir: str,
        ws_manager: "ConnectionManager",
        db: Optional["Database"],
    ) -> None:
        self._sessions.pop(session.session_id, None)

        close_line = json.dumps({
            "type":             "session_close",
            "session_id":       session.session_id,
            "ended_at":         ended_at,
            "duration_seconds": duration_seconds,
            "end_reason":       end_reason,
        })
        with session.tmp_path.open("a", encoding="utf-8") as fh:
            fh.write(close_line + "\n")

        ws_manager.push_pie_state(
            project_dir=project_dir,
            running=False,
            armed_systems=[],
            event_count=None,
            started_at=None,
            crashed=(end_reason == "crash"),
        )

        await self._consolidate_and_import(session, project_dir, ws_manager, db)

    async def _consolidate_and_import(
        self,
        session: _TrackingSession,
        project_dir: str,
        ws_manager: "ConnectionManager",
        db: Optional["Database"],
    ) -> None:
        sessions_dir = session.tmp_path.parent.parent
        md_path      = sessions_dir / f"{session.session_id}.md"

        loop = asyncio.get_event_loop()
        from nytwatch.tracking.consolidator import consolidate
        try:
            await loop.run_in_executor(None, consolidate, session.tmp_path, md_path)
        except Exception:
            log.exception(
                "tracking ws: consolidation failed for session %s", session.session_id
            )
            return

        try:
            session.tmp_path.unlink(missing_ok=True)
        except OSError:
            pass

        if db is not None:
            from nytwatch.tracking.session_store import import_session_file
            try:
                imported = await loop.run_in_executor(
                    None, import_session_file, str(md_path), project_dir, db
                )
                if imported:
                    ws_manager.push_session_imported(imported)
            except Exception:
                log.exception(
                    "tracking ws: DB import failed for session %s", session.session_id
                )

    # ── Orphan recovery ──────────────────────────────────────────────────────

    async def _recover_one_orphan(
        self,
        ndjson_path: Path,
        project_dir: str,
        ws_manager: "ConnectionManager",
        db: Optional["Database"],
    ) -> None:
        session_id = ndjson_path.stem

        # Read started_at from the header line to compute duration.
        started_at  = ""
        ended_at    = _now_iso()
        try:
            first_line = ndjson_path.read_text(encoding="utf-8").splitlines()[0]
            open_meta  = json.loads(first_line)
            if open_meta.get("type") == "session_open":
                started_at = open_meta.get("started_at", "")
        except Exception:
            pass

        duration_seconds = _elapsed_seconds(started_at) if started_at else 0

        close_line = json.dumps({
            "type":             "session_close",
            "session_id":       session_id,
            "ended_at":         ended_at,
            "duration_seconds": duration_seconds,
            "end_reason":       "crash",
        })
        with ndjson_path.open("a", encoding="utf-8") as fh:
            fh.write(close_line + "\n")

        session = _TrackingSession(session_id, ndjson_path, started_at)
        await self._consolidate_and_import(session, project_dir, ws_manager, db)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sessions_dir(project_dir: str) -> Path:
    return Path(project_dir) / "Saved" / "Nytwatch" / "Sessions"


def _read_close_record(tmp_path: Path) -> dict | None:
    """Return the session_close record from the temp file if one is present.

    Called when a WebSocket disconnect is detected without a session_close
    message being received — the close record may have been written to the
    file by a previous receive loop iteration that lost the race with the
    disconnect handler.
    """
    try:
        lines = tmp_path.read_text(encoding="utf-8").splitlines()
        # session_close is always the last record — scan from the end,
        # stop as soon as we hit a non-empty line.
        for line in reversed(lines):
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
                if record.get("type") == "session_close":
                    return record
            except json.JSONDecodeError:
                pass
            # First non-empty line from the end is the only candidate.
            break
    except OSError:
        pass
    return None
