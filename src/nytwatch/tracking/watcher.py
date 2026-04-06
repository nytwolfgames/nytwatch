from __future__ import annotations

import json
import logging
import sys
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Optional

from watchdog.events import FileSystemEventHandler, FileSystemEvent
from watchdog.observers import Observer

if TYPE_CHECKING:
    from nytwatch.database import Database
    from nytwatch.ws_manager import ConnectionManager

log = logging.getLogger(__name__)

_CRASH_POLL_INTERVAL = 5.0  # seconds between PID liveness checks


if sys.platform == "win32":
    import ctypes
    import ctypes.wintypes

    _PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    _STILL_ACTIVE = 259

    def _pid_is_alive(pid: int) -> bool:
        """Return True if the process is still running (Windows implementation).

        os.kill(pid, 0) is NOT safe on Windows — CPython routes non-signal-event
        signals to TerminateProcess(), which would kill the UE editor outright.
        Use OpenProcess + GetExitCodeProcess instead.
        """
        if pid <= 0:
            return False
        handle = ctypes.windll.kernel32.OpenProcess(
            _PROCESS_QUERY_LIMITED_INFORMATION, False, pid
        )
        if not handle:
            return False
        try:
            exit_code = ctypes.wintypes.DWORD()
            ok = ctypes.windll.kernel32.GetExitCodeProcess(
                handle, ctypes.byref(exit_code)
            )
            return bool(ok) and exit_code.value == _STILL_ACTIVE
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)

else:
    import os

    def _pid_is_alive(pid: int) -> bool:
        """Return True if the process is still running (Unix implementation)."""
        if pid <= 0:
            return False
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            # Process exists but we lack permission to signal it — still alive.
            return True
        except OSError:
            return False


def _read_lock_pid(lock_path: str) -> Optional[int]:
    """Parse the PID field from a nytwatch.lock JSON file. Returns None on failure."""
    try:
        data = json.loads(Path(lock_path).read_text(encoding="utf-8"))
        pid = data.get("pid")
        return int(pid) if pid is not None else None
    except Exception:
        return None


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
        # .md files are NOT imported on creation — the plugin creates the file at
        # PIE start and writes events throughout the session. Import happens after
        # PIE ends via _scan_for_new_sessions (triggered by lock deletion).

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
        self._pie_pids: dict[str, int] = {}          # project_dir → PIE process PID
        self._dbs: dict[str, "Database"] = {}        # project_dir → per-project Database
        self._debounce_timers: dict[str, threading.Timer] = {}
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._poll_thread = threading.Thread(
            target=self._poll_for_crashes, daemon=True, name="NytwatchCrashPoller"
        )
        self._poll_thread.start()

    def add_watch(self, project_dir: str, db: Optional["Database"] = None) -> None:
        """Start watching <project_dir>/Saved/Nytwatch/. Creates dir if absent.

        Pass the project's Database instance so session imports and event
        handlers always use the correct DB regardless of which project is
        currently active in app.state.
        """
        if not project_dir:
            return
        watch_path = Path(project_dir) / "Saved" / "Nytwatch"
        watch_path.mkdir(parents=True, exist_ok=True)
        with self._lock:
            if project_dir in self._watches:
                return
            if db is not None:
                self._dbs[project_dir] = db
            try:
                handler = _NytwatchEventHandler(self)
                watch = self._observer.schedule(handler, str(watch_path), recursive=True)
                self._watches[project_dir] = watch
                self._pie_state[project_dir] = False
                log.info("TrackingWatcher: watching %s", watch_path)
            except Exception:
                log.exception("TrackingWatcher: failed to watch %s", watch_path)
                self._dbs.pop(project_dir, None)
                return

        # Check for a stale lock left by a previous crash (outside the write lock).
        lock_path = watch_path / "nytwatch.lock"
        if lock_path.exists():
            pid = _read_lock_pid(str(lock_path))
            if pid is not None and _pid_is_alive(pid):
                # The editor process that wrote this lock is still running —
                # PIE is actually active (server restarted mid-session).
                with self._lock:
                    self._pie_state[project_dir] = True
                    self._pie_pids[project_dir] = pid
                log.info(
                    "TrackingWatcher: live lock found for %s (pid=%d) — PIE is active",
                    project_dir, pid,
                )
            else:
                # Dead PID or unreadable lock → previous session crashed.
                log.warning(
                    "TrackingWatcher: stale lock found for %s (pid=%s) — scanning for crashed session",
                    project_dir, pid,
                )
                self._trigger_crash_recovery(project_dir)

    def remove_watch(self, project_dir: str) -> None:
        with self._lock:
            watch = self._watches.pop(project_dir, None)
            self._pie_state.pop(project_dir, None)
            self._pie_pids.pop(project_dir, None)
            self._dbs.pop(project_dir, None)
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
        self._stop_event.set()
        self._observer.stop()
        self._observer.join()

    # ── Internal helpers ─────────────────────────────────────────────────────

    def _get_project_db(self, project_dir: str) -> Optional["Database"]:
        """Return the Database for the given project.

        Prefers the per-project DB registered via add_watch(db=...).
        Falls back to the global getter only if no per-project DB is stored,
        so old callers that didn't pass a DB still work.
        """
        return self._dbs.get(project_dir) or self._db_getter()

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
        pid = _read_lock_pid(lock_path)
        db = self._get_project_db(pd)
        armed_names = [s["name"] for s in db.get_armed_systems()] if db else []
        with self._lock:
            self._pie_state[pd] = True
            if pid is not None:
                self._pie_pids[pd] = pid
        self._ws.push_pie_state(
            project_dir=pd,
            running=True,
            armed_systems=armed_names,
            event_count=0,
            started_at=None,
        )
        log.info("TrackingWatcher: PIE started in %s (pid=%s)", pd, pid)

    def _on_lock_deleted(self, lock_path: str) -> None:
        pd = self._resolve_project_dir(lock_path)
        if pd is None:
            return
        with self._lock:
            if not self._pie_state.get(pd):
                # Already handled (e.g. crash poller got here first) — ignore.
                return
            self._pie_state[pd] = False
            self._pie_pids.pop(pd, None)
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
        db = self._get_project_db(pd)
        if db is None:
            return
        from nytwatch.tracking.session_store import import_session_file
        session = import_session_file(file_path, pd, db)
        if session:
            self._ws.push_session_imported(session)

    def _on_session_file_deleted(self, file_path: str) -> None:
        # On Windows, plugins often rewrite session files by truncating then
        # recreating them (delete + create). Verify the file is actually gone
        # before removing the DB record so we don't react to transient events.
        if Path(file_path).exists():
            return
        pd = self._resolve_project_dir(file_path)
        db = self._get_project_db(pd) if pd else self._db_getter()
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

    def _poll_for_crashes(self) -> None:
        """Daemon thread: periodically check whether active PIE processes are still alive."""
        while not self._stop_event.wait(timeout=_CRASH_POLL_INTERVAL):
            with self._lock:
                candidates = [
                    (pd, pid)
                    for pd, pid in list(self._pie_pids.items())
                    if self._pie_state.get(pd)
                ]
            for pd, pid in candidates:
                if not _pid_is_alive(pid):
                    log.warning(
                        "TrackingWatcher: PIE process (pid=%d) for %s is no longer alive — treating as crash",
                        pid, pd,
                    )
                    self._on_pie_crashed(pd)

    def _on_pie_crashed(self, project_dir: str) -> None:
        """Handle a PIE session that ended without a clean lock-file deletion."""
        with self._lock:
            if not self._pie_state.get(project_dir):
                return  # Already handled by a concurrent path
            self._pie_state[project_dir] = False
            self._pie_pids.pop(project_dir, None)
            old_timer = self._debounce_timers.pop(project_dir, None)
        if old_timer:
            old_timer.cancel()
        self._ws.push_pie_state(
            project_dir=project_dir,
            running=False,
            armed_systems=[],
            event_count=None,
            started_at=None,
            crashed=True,
        )
        self._trigger_crash_recovery(project_dir)
        log.warning("TrackingWatcher: crash recovery initiated for %s", project_dir)

    def _trigger_crash_recovery(self, project_dir: str) -> None:
        """Debounce a session scan after a crash (same window as normal end)."""
        with self._lock:
            old_timer = self._debounce_timers.pop(project_dir, None)
        if old_timer:
            old_timer.cancel()
        timer = threading.Timer(1.0, self._scan_for_new_sessions, args=(project_dir,))
        timer.daemon = True
        with self._lock:
            self._debounce_timers[project_dir] = timer
        timer.start()

    def _scan_for_new_sessions(self, project_dir: str) -> None:
        """Import any .md session files not yet in the DB (called after PIE end)."""
        sessions_dir = Path(project_dir) / "Saved" / "Nytwatch" / "Sessions"
        if not sessions_dir.exists():
            return
        db = self._get_project_db(project_dir)
        if db is None:
            return
        from nytwatch.tracking.session_store import import_session_file
        for md_file in sessions_dir.glob("*.md"):
            if not db.session_exists_for_file(str(md_file)):
                session = import_session_file(str(md_file), project_dir, db)
                if session:
                    self._ws.push_session_imported(session)
