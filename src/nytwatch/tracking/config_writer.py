from __future__ import annotations

import json
import logging
import os
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from nytwatch.database import Database

log = logging.getLogger(__name__)

_PLUGIN_DIR_FRAGMENT = "Plugins/NytwatchAgent/"


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


def write_config(
    repo_path: str,
    db: "Database",
    tracking_active: bool,
    ws_host: str = "127.0.0.1",
    ws_port: int = 8420,
) -> None:
    """Generate and atomically write NytwatchConfig.json into <repo_path>/Saved/Nytwatch/."""
    project_dir = Path(repo_path)
    nytwatch_dir = project_dir / "Saved" / "Nytwatch"
    nytwatch_dir.mkdir(parents=True, exist_ok=True)

    armed = db.get_armed_systems()
    tick_interval = float(db.get_config("tracking_tick_interval", "1.0"))
    scan_cap = int(db.get_config("tracking_scan_cap", "2000"))

    systems_payload = []
    for s in armed:
        abs_paths = []
        for p in s.get("paths", []):
            resolved = str((project_dir / p).resolve()) if not os.path.isabs(p) else p
            # Auto-exclude NytwatchAgent plugin itself
            if _PLUGIN_DIR_FRAGMENT in resolved.replace("\\", "/"):
                continue
            abs_paths.append(resolved)

        if not abs_paths:
            continue

        # Resolve per-file verbosity overrides to absolute paths
        overrides_raw = db.get_file_verbosity_overrides(s["name"])
        file_overrides: dict[str, str] = {}
        for o in overrides_raw:
            fp = o["file_path"]
            abs_fp = str((project_dir / fp).resolve()) if not os.path.isabs(fp) else fp
            file_overrides[abs_fp] = o["verbosity"]

        systems_payload.append({
            "name": s["name"],
            "system_verbosity": s.get("tracking_verbosity", "Standard"),
            "file_overrides": file_overrides,
            "paths": abs_paths,
        })

    tracking_ws_url = (
        f"ws://{ws_host}:{ws_port}/ws/tracking"
        f"?project_dir={urllib.parse.quote(repo_path, safe='')}"
    )

    payload = {
        "version": _bundled_version(),
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "status": "On" if tracking_active else "Off",
        "armed_systems": systems_payload,
        "object_scan_cap": scan_cap,
        "tick_interval_seconds": tick_interval,
        "tracking_ws_url": tracking_ws_url,
    }

    config_path = nytwatch_dir / "NytwatchConfig.json"
    tmp_path = nytwatch_dir / "NytwatchConfig.json.tmp"
    tmp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp_path.replace(config_path)
    log.info(
        "Wrote NytwatchConfig.json (%d armed system(s), status=%s)",
        len(systems_payload),
        payload["status"],
    )
