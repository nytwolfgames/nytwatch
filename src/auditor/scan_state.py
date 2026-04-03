from __future__ import annotations

import logging
import subprocess
import threading
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from auditor.database import Database


class _ScanCanceller:
    """Singleton that tracks the active Claude subprocess and exposes a cancel signal.

    Call reset() before starting a scan, register_process() when the subprocess
    starts, and cancel() from any thread to kill the process and signal the scan
    loop to stop.
    """

    def __init__(self) -> None:
        self._cancelled = threading.Event()
        self._lock = threading.Lock()
        self._process: Optional[subprocess.Popen] = None

    def reset(self) -> None:
        self._cancelled.clear()
        with self._lock:
            self._process = None

    def register_process(self, proc: subprocess.Popen) -> None:
        with self._lock:
            self._process = proc

    def unregister_process(self) -> None:
        with self._lock:
            self._process = None

    def cancel(self) -> None:
        """Set the cancel flag and kill the active subprocess immediately."""
        self._cancelled.set()
        with self._lock:
            if self._process is not None:
                try:
                    self._process.kill()
                except Exception:
                    pass

    @property
    def is_cancelled(self) -> bool:
        return self._cancelled.is_set()


# Module-level singleton — imported by engine.py, routes.py, and main.py
canceller = _ScanCanceller()


class ScanLogHandler(logging.Handler):
    """Logging handler that writes records to the scan_logs DB table for a given scan."""

    def __init__(self, scan_id: str, db: "Database") -> None:
        super().__init__()
        self.scan_id = scan_id
        self._db = db

    def emit(self, record: logging.LogRecord) -> None:
        try:
            from auditor.models import now_iso
            logged_at = now_iso()
            message = self.format(record)
            self._db.insert_scan_log(
                self.scan_id,
                record.levelname,
                record.name,
                message,
            )
            from auditor.ws_manager import manager as ws_manager
            ws_manager.push_log(
                scan_id=self.scan_id,
                level=record.levelname,
                logger_name=record.name,
                message=message,
                logged_at=logged_at,
            )
        except Exception:
            self.handleError(record)
