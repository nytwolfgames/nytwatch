from __future__ import annotations

import json
import logging
import re
from pathlib import Path

log = logging.getLogger(__name__)

_TIMESTAMP_RE = re.compile(r"^\[\d{2}:\d{2}\.\d{2}\]")


def _bundled_version() -> str:
    try:
        version_file = (
            Path(__file__).parent.parent.parent.parent
            / "ue5-plugin"
            / "NytwatchAgent"
            / "VERSION"
        )
        return version_file.read_text().strip()
    except Exception:
        return "1.0.0"


def _parse_semver(v: str) -> tuple[int, int, int]:
    parts = v.strip().split(".")
    try:
        major = int(parts[0]) if len(parts) > 0 else 0
        minor = int(parts[1]) if len(parts) > 1 else 0
        patch = int(parts[2]) if len(parts) > 2 else 0
        return (major, minor, patch)
    except (ValueError, IndexError):
        return (0, 0, 0)


def parse_session_file(file_path: str) -> dict:
    """
    Parse a session .md file into a structured dict.
    On parse error, returns a partial dict with an 'import_error' key.
    """
    path = Path(file_path)
    result: dict = {"file_path": file_path}

    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        result["import_error"] = f"Cannot read file: {e}"
        return result

    lines = text.splitlines()

    # Parse line-by-line key: value header between --- fences
    header: dict[str, str] = {}
    in_header = False
    header_done = False
    for line in lines:
        stripped = line.strip()
        if stripped == "---":
            if not in_header:
                in_header = True
            else:
                header_done = True
                break
        elif in_header and ":" in stripped:
            key, _, val = stripped.partition(":")
            header[key.strip()] = val.strip()

    if not header_done:
        result["import_error"] = "Missing or malformed header block"

    session_id = header.get("session_id", "") or path.stem
    result["id"] = session_id
    result["started_at"] = header.get("started_at", "")
    result["ended_at"] = header.get("ended_at") or None
    result["end_reason"] = header.get("end_reason") or None
    result["ue_project_name"] = header.get("ue_project_name", "")
    result["plugin_version"] = header.get("plugin_version", "")

    try:
        result["duration_secs"] = int(header.get("duration_seconds", 0))
    except (ValueError, TypeError):
        result["duration_secs"] = None

    try:
        result["systems_tracked"] = json.loads(header.get("systems_tracked", "[]"))
    except Exception:
        result["systems_tracked"] = []

    try:
        result["event_count"] = int(header.get("event_count", 0))
    except (ValueError, TypeError):
        result["event_count"] = 0

    # Cross-check event count by counting timestamp lines
    actual_count = sum(1 for line in lines if _TIMESTAMP_RE.match(line))
    if actual_count != result["event_count"]:
        log.debug(
            "Session %s: header event_count=%d, counted=%d",
            session_id,
            result["event_count"],
            actual_count,
        )

    # Plugin version compatibility check
    plugin_ver = result["plugin_version"]
    bundled_ver = _bundled_version()
    if plugin_ver and bundled_ver:
        bundled_major, bundled_minor, _ = _parse_semver(bundled_ver)
        plugin_major, plugin_minor, _ = _parse_semver(plugin_ver)
        if plugin_major != bundled_major:
            log.error(
                "Session %s: major plugin version mismatch (session=%s, server=%s)",
                session_id,
                plugin_ver,
                bundled_ver,
            )
        elif plugin_minor != bundled_minor:
            log.warning(
                "Session %s: minor plugin version mismatch (session=%s, server=%s)",
                session_id,
                plugin_ver,
                bundled_ver,
            )

    return result
