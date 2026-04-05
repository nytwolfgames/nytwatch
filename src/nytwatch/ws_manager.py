from __future__ import annotations

import asyncio
import json
import logging
from typing import Optional

from fastapi import WebSocket

log = logging.getLogger(__name__)


class ConnectionManager:
    """Manages active WebSocket connections and provides a thread-safe broadcast bridge."""

    def __init__(self) -> None:
        self._clients: list[WebSocket] = []
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._clients.append(ws)
        log.debug("WS client connected (%d total)", len(self._clients))

    def disconnect(self, ws: WebSocket) -> None:
        self._clients = [c for c in self._clients if c is not ws]
        log.debug("WS client disconnected (%d remaining)", len(self._clients))

    async def broadcast(self, message: dict) -> None:
        if not self._clients:
            return
        text = json.dumps(message, default=str)
        dead: list[WebSocket] = []
        for client in self._clients:
            try:
                await client.send_text(text)
            except Exception:
                dead.append(client)
        for d in dead:
            self.disconnect(d)

    def broadcast_from_thread(self, message: dict) -> None:
        """Thread-safe broadcast — schedules on the asyncio event loop."""
        if not self._clients or self._loop is None:
            return
        try:
            asyncio.run_coroutine_threadsafe(self.broadcast(message), self._loop)
        except Exception:
            pass

    def push_scan_status(self, running: bool, scan: Optional[dict], cancelling: bool) -> None:
        self.broadcast_from_thread({
            "type": "scan_status",
            "running": running,
            "scan": scan,
            "cancelling": cancelling,
        })

    def push_log(self, scan_id: str, level: str, logger_name: str, message: str, logged_at: str) -> None:
        self.broadcast_from_thread({
            "type": "log",
            "scan_id": scan_id,
            "entry": {
                "logged_at": logged_at,
                "level": level,
                "logger": logger_name,
                "message": message,
            },
        })

    def push_scan_due(self, scan_type: str = "incremental", reason: str = "schedule") -> None:
        """Notify connected clients that a scheduled scan is ready to run."""
        self.broadcast_from_thread({
            "type": "scan_due",
            "scan_type": scan_type,
            "reason": reason,
        })

    def push_findings_update(
        self,
        scan_id: str,
        system: str,
        chunk: int,
        total_chunks: int,
        chunk_findings: int,
        total_findings: int,
    ) -> None:
        self.broadcast_from_thread({
            "type": "findings_update",
            "scan_id": scan_id,
            "system": system,
            "chunk": chunk,
            "total_chunks": total_chunks,
            "chunk_findings": chunk_findings,
            "total_findings": total_findings,
        })


# Module-level singleton
manager = ConnectionManager()
