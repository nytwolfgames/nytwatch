"""
Doc cache with live filesystem watcher.

Parses wiki / narrative / design docs once and holds them in memory.
A watchdog observer watches each registered planning directory; any .md
create / modify / delete / rename invalidates that directory's cache so
the next request triggers a fresh parse.

Usage
-----
  from nytwatch.pm.doc_cache import doc_cache

  # In app startup:
  doc_cache.start()

  # In a route:
  doc_cache.watch(str(planning_path))   # idempotent
  docs = doc_cache.store.get_design(planning_key)
  if docs is None:
      docs = load_design_docs(repo_path)
      doc_cache.store.set_design(planning_key, docs)

  # In app shutdown:
  doc_cache.stop()
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Optional

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

log = logging.getLogger(__name__)


# ── In-memory store ────────────────────────────────────────────────────────────

class _Store:
    """Thread-safe dict-of-lists for parsed doc results."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._wiki:      dict[str, list] = {}   # planning_key → [WikiDoc]
        self._narrative: dict[str, list] = {}   # planning_key → [WikiDoc]
        self._design:    dict[str, list] = {}   # planning_key → [DesignDoc]

    # ── wiki ──────────────────────────────────────────────────────────────────
    def get_wiki(self, key: str) -> Optional[list]:
        with self._lock:
            return self._wiki.get(key)

    def set_wiki(self, key: str, docs: list) -> None:
        with self._lock:
            self._wiki[key] = docs

    # ── narrative ─────────────────────────────────────────────────────────────
    def get_narrative(self, key: str) -> Optional[list]:
        with self._lock:
            return self._narrative.get(key)

    def set_narrative(self, key: str, docs: list) -> None:
        with self._lock:
            self._narrative[key] = docs

    # ── design / docs ─────────────────────────────────────────────────────────
    def get_design(self, key: str) -> Optional[list]:
        with self._lock:
            return self._design.get(key)

    def set_design(self, key: str, docs: list) -> None:
        with self._lock:
            self._design[key] = docs

    # ── invalidation ──────────────────────────────────────────────────────────
    def invalidate(self, planning_key: str) -> None:
        """Drop all cached data for a planning directory."""
        with self._lock:
            self._wiki.pop(planning_key, None)
            self._narrative.pop(planning_key, None)
            self._design.pop(planning_key, None)
        log.info("Doc cache invalidated: %s", planning_key)


# ── Watchdog handler ───────────────────────────────────────────────────────────

class _MarkdownHandler(FileSystemEventHandler):
    """
    Invalidates the store when any .md file under the watched path changes.

    Events are debounced with a 600 ms quiet-period timer so that startup
    storms (Windows fires dozens of ReadDirectoryChangesW events when a watch
    is first attached) collapse into a single invalidation.
    """

    _DEBOUNCE_S = 0.6  # seconds to wait after the last event before firing

    def __init__(self, store: _Store, planning_key: str) -> None:
        super().__init__()
        self._store = store
        self._key = planning_key
        self._timer: Optional[threading.Timer] = None
        self._timer_lock = threading.Lock()

    def on_any_event(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        # src_path covers create/modify/delete; dest_path covers renames
        src = getattr(event, "src_path", "") or ""
        dst = getattr(event, "dest_path", "") or ""
        if not (src.endswith(".md") or dst.endswith(".md")):
            return
        # Reset the debounce timer on every qualifying event
        with self._timer_lock:
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(self._DEBOUNCE_S, self._fire)
            self._timer.daemon = True
            self._timer.start()

    def _fire(self) -> None:
        with self._timer_lock:
            self._timer = None
        self._store.invalidate(self._key)


# ── Public cache object ────────────────────────────────────────────────────────

class DocCache:
    """
    Manages the _Store and a watchdog Observer.

    Lifecycle
    ---------
    Call ``start()`` once the FastAPI app starts (inside the startup event).
    Call ``stop()`` inside the shutdown event.
    Call ``watch(path)`` from any route that accesses a planning directory;
    it is idempotent — safe to call on every request.
    """

    def __init__(self) -> None:
        self.store = _Store()
        self._observer: Optional[Observer] = None
        self._watched: set[str] = set()    # planning paths already scheduled

    def start(self) -> None:
        """Start the filesystem observer thread."""
        self._observer = Observer()
        self._observer.start()
        log.info("Doc file watcher started")

    def stop(self) -> None:
        """Stop the filesystem observer thread (blocks until joined)."""
        if self._observer and self._observer.is_alive():
            self._observer.stop()
            self._observer.join()
        log.info("Doc file watcher stopped")

    def watch(self, planning_path: str) -> None:
        """
        Register *planning_path* for recursive .md change tracking.

        Safe to call multiple times with the same path; subsequent calls
        are no-ops.  Does nothing if the observer has not been started yet
        or if the directory does not exist.
        """
        if planning_path in self._watched:
            return
        if not self._observer or not self._observer.is_alive():
            log.debug("Watcher not running; skipping watch(%s)", planning_path)
            return
        if not Path(planning_path).exists():
            log.debug("Planning path does not exist: %s", planning_path)
            return
        handler = _MarkdownHandler(self.store, planning_path)
        self._observer.schedule(handler, planning_path, recursive=True)
        self._watched.add(planning_path)
        log.info("Watching for markdown changes: %s", planning_path)


# Module-level singleton — imported by routes and main
doc_cache = DocCache()
