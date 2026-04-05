from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Optional

from watchdog.events import FileSystemEventHandler, FileSystemEvent
from watchdog.observers import Observer

if TYPE_CHECKING:
    from nytwatch.database import Database
    from nytwatch.ws_manager import ConnectionManager

log = logging.getLogger(__name__)


class _NytwatchEventHandler(FileSystemEventHandler):
    def __init__(self, watcher: "TrackingWatcher") -> None:
        super().__init__()
        self._watcher = watcher

    def on_created(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        path = Path(str(event.src_path))
        if path.name == "nytwatch.lock":
            self._watcher._on_lock_created(str(path))
        elif path.suffix == ".md" and path.parent.name == "Sessions":
            self._watcher._on_session_file(str(path))

    def on_deleted(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        path = Path(str(event.src_path))
        if path.name == "nytwatch.lock":
            self._watcher._on_lock_deleted(str(path))
        elif path.suffix == ".md" and path.parent.name == "Sessions":
            self._watcher._on_session_file_deleted(str(path))


class TrackingWatcher:
    """Watchdog-based observer for a single UE project's Saved/Nytwatch/ directory."""

    def __init__(
        self,
        ws_manager: "ConnectionManager",
        db_getter: Callable[[], Optional["Database"]],
    ) -> None:
        self._ws = ws_manager
        self._db_getter = db_getter
        self._observer = Observer()
        self._observer.start()
        self._watches: dict[str, object] = {}       # project_dir → watchdog watch handle
        self._pie_state: dict[str, bool] = {}        # project_dir → running
        self._debounce_timers: dict[str, threading.Timer] = {}
        self._lock = threading.Lock()

    def add_watch(self, project_dir: str) -> None:
        """Start watching <project_dir>/Saved/Nytwatch/. Creates dir if absent."""
        if not project_dir:
            return
        watch_path = Path(project_dir) / "Saved" / "Nytwatch"
        watch_path.mkdir(parents=True, exist_ok=True)
        with self._lock:
            if project_dir in self._watches:
                return
            try:
                handler = _NytwatchEventHandler(self)
                watch = self._observer.schedule(handler, str(watch_path), recursive=True)
                self._watches[project_dir] = watch
                # Stale lock on startup is ignored — PIE state starts False
                self._pie_state[project_dir] = False
                log.info("TrackingWatcher: watching %s", watch_path)
            except Exception:
                log.exception("TrackingWatcher: failed to watch %s", watch_path)

    def remove_watch(self, project_dir: str) -> None:
        with self._lock:
            watch = self._watches.pop(project_dir, None)
            self._pie_state.pop(project_dir, None)
            timer = self._debounce_timers.pop(project_dir, None)
        if timer:
            timer.cancel()
        if watch is not None:
            try:
                self._observer.unschedule(watch)
            except Exception:
                pass
        log.debug("TrackingWatcher: removed watch for %s", project_dir)

    def get_pie_state(self, project_dir: str) -> bool:
        return self._pie_state.get(project_dir, False)

    def stop(self) -> None:
        self._observer.stop()
        self._observer.join()

    # ── Internal helpers ─────────────────────────────────────────────────────

    def _resolve_project_dir(self, event_path: str) -> Optional[str]:
        """Given any path under Saved/Nytwatch/, return the matching project_dir."""
        try:
            p_resolved = Path(event_path).resolve()
        except (OSError, ValueError):
            return None
        with self._lock:
            for pd in list(self._watches.keys()):
                try:
                    expected = (Path(pd) / "Saved" / "Nytwatch").resolve()
                    # Check if event_path is inside the watched nytwatch dir
                    p_resolved.relative_to(expected)
                    return pd
                except (ValueError, OSError):
                    continue
        return None

    # ── Event handlers ───────────────────────────────────────────────────────

    def _on_lock_created(self, lock_path: str) -> None:
        pd = self._resolve_project_dir(lock_path)
        if pd is None:
            return
        db = self._db_getter()
        armed_names = [s["name"] for s in db.get_armed_systems()] if db else []
        with self._lock:
            self._pie_state[pd] = True
        self._ws.push_pie_state(
            project_dir=pd,
            running=True,
            armed_systems=armed_names,
            event_count=0,
            started_at=None,
        )
        log.info("TrackingWatcher: PIE started in %s", pd)

    def _on_lock_deleted(self, lock_path: str) -> None:
        pd = self._resolve_project_dir(lock_path)
        if pd is None:
            return
        with self._lock:
            self._pie_state[pd] = False
            old_timer = self._debounce_timers.pop(pd, None)
        if old_timer:
            old_timer.cancel()
        self._ws.push_pie_state(
            project_dir=pd,
            running=False,
            armed_systems=[],
            event_count=None,
            started_at=None,
        )
        # Debounce session import by 1 second to let the plugin finish writing
        timer = threading.Timer(1.0, self._scan_for_new_sessions, args=(pd,))
        timer.daemon = True
        with self._lock:
            self._debounce_timers[pd] = timer
        timer.start()
        log.info("TrackingWatcher: PIE ended in %s", pd)

    def _on_session_file(self, file_path: str) -> None:
        pd = self._resolve_project_dir(file_path)
        if pd is None:
            return
        db = self._db_getter()
        if db is None:
            return
        from nytwatch.tracking.session_store import import_session_file
        session = import_session_file(file_path, pd, db)
        if session:
            self._ws.push_session_imported(session)

    def _on_session_file_deleted(self, file_path: str) -> None:
        db = self._db_getter()
        if db is None:
            return
        # Find matching DB row by file_path and remove it regardless of bookmark status
        # (external deletion bypasses the API's bookmark guard)
        rows = db.list_sessions()
        for s in rows:
            if s.get("file_path") == file_path:
                db.delete_session(s["id"])
                self._ws.push_session_deleted(s["id"])
                log.info("TrackingWatcher: externally deleted session %s", s["id"])
                break

    def _scan_for_new_sessions(self, project_dir: str) -> None:
        """Import any .md session files not yet in the DB (called after PIE end)."""
        sessions_dir = Path(project_dir) / "Saved" / "Nytwatch" / "Sessions"
        if not sessions_dir.exists():
            return
        db = self._db_getter()
        if db is None:
            return
        from nytwatch.tracking.session_store import import_session_file
        for md_file in sessions_dir.glob("*.md"):
            if not db.session_exists_for_file(str(md_file)):
                session = import_session_file(str(md_file), project_dir, db)
                if session:
                    self._ws.push_session_imported(session)
