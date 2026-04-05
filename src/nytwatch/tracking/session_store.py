from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from nytwatch.models import now_iso
from nytwatch.tracking.session_parser import parse_session_file

if TYPE_CHECKING:
    from nytwatch.database import Database

log = logging.getLogger(__name__)


def import_session_file(
    file_path: str, project_dir: str, db: "Database"
) -> Optional[dict]:
    """Parse and insert a session file. Idempotent — skips if already imported."""
    if db.session_exists_for_file(file_path):
        log.debug("Session already imported: %s", file_path)
        return None

    session = parse_session_file(file_path)
    if not session.get("id"):
        log.error("Could not import session %s: no ID resolved", file_path)
        return None

    session["file_path"] = file_path
    session["project_dir"] = project_dir
    session["display_name"] = session["id"]
    session["created_at"] = now_iso()

    db.insert_session(session)
    log.info(
        "Imported session %s (%d events)", session["id"], session.get("event_count", 0)
    )
    return db.get_session(session["id"])


def rename_session(session_id: str, new_name: str, db: "Database") -> None:
    """Update display_name in DB only. The .md file is never renamed."""
    db.update_session(session_id, display_name=new_name)


def delete_session(session_id: str, db: "Database") -> None:
    """Delete .md file then DB row. Raises ValueError if session is bookmarked."""
    session = db.get_session(session_id)
    if session is None:
        return
    if session.get("bookmarked"):
        raise ValueError("Cannot delete a bookmarked session. Unbookmark it first.")

    file_path = session.get("file_path")
    if file_path:
        try:
            Path(file_path).unlink(missing_ok=True)
        except OSError as e:
            log.warning("Could not delete session file %s: %s", file_path, e)

    db.delete_session(session_id)
    log.info("Deleted session %s", session_id)


def bookmark_session(session_id: str, bookmarked: bool, db: "Database") -> None:
    db.update_session(session_id, bookmarked=bookmarked)
