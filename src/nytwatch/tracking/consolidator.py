from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Formatting helpers  (mirror of FNytwatchSessionWriter C++ equivalents)
# ---------------------------------------------------------------------------

def _strip_ue_prefix(name: str) -> str:
    if len(name) >= 2 and name[0] in ("A", "U") and name[1].isupper():
        return name[1:]
    return name


def _trim_float(val: float, precision: int = 6) -> str:
    if abs(val) < 1e-9:
        return "0"
    s = f"{val:.{precision}f}"
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s


def _format_bool(val: str) -> str:
    if val == "True":
        return "T"
    if val == "False":
        return "F"
    return val


def _is_numeric(evt: dict) -> bool:
    """Determine whether an event represents a numeric property.

    Prefers the explicit ``num`` flag written by the plugin.  Falls back to
    trying to parse the ``old`` value as a float so the consolidator still
    works if the flag is absent.
    """
    if "num" in evt:
        return bool(evt["num"])
    try:
        float(evt.get("old", ""))
        return True
    except (ValueError, TypeError):
        return False


# ---------------------------------------------------------------------------
# consolidate
# ---------------------------------------------------------------------------

def consolidate(ndjson_path: Path, output_md_path: Path) -> None:
    """Read a .ndjson temp session file and write the consolidated .md log.

    The .ndjson file has three kinds of lines:
      • First line  — ``{"type":"session_open", ...}``  — session metadata
      • Middle lines — raw event records (one per property change)
      • Last line   — ``{"type":"session_close", ...}`` — close metadata

    Raises ``ValueError`` if no ``session_open`` record is found.
    Raises ``OSError`` if the input file cannot be read.
    """
    try:
        text = ndjson_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        log.error("consolidate: cannot read %s: %s", ndjson_path, exc)
        raise

    open_meta: dict | None = None
    close_meta: dict | None = None
    events: list[dict] = []

    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            log.warning("consolidate: skipping malformed line in %s", ndjson_path.name)
            continue
        t = record.get("type")
        if t == "session_open":
            open_meta = record
        elif t == "session_close":
            close_meta = record
        else:
            events.append(record)

    if open_meta is None:
        msg = f"No session_open record in {ndjson_path.name}"
        log.error("consolidate: %s", msg)
        raise ValueError(msg)

    # ── Group events: sys → obj → prop (insertion order preserved) ───────────
    sys_order: list[str] = []
    obj_order: dict[str, list[str]] = {}
    prop_order: dict[str, dict[str, list[str]]] = {}
    data: dict[str, dict[str, dict[str, list[dict]]]] = {}

    for evt in events:
        sys  = evt.get("sys",  "Unknown")
        obj  = _strip_ue_prefix(evt.get("obj",  "Unknown"))
        prop = evt.get("prop", "Unknown")

        if sys not in data:
            sys_order.append(sys)
            obj_order[sys]  = []
            prop_order[sys] = {}
            data[sys]       = {}

        if obj not in data[sys]:
            obj_order[sys].append(obj)
            prop_order[sys][obj] = []
            data[sys][obj]       = {}

        if prop not in data[sys][obj]:
            prop_order[sys][obj].append(prop)
            data[sys][obj][prop] = []

        data[sys][obj][prop].append(evt)

    # ── Build body ────────────────────────────────────────────────────────────
    body_lines: list[str] = []
    for sys in sys_order:
        body_lines.append(f"## {sys}\n")
        for obj in obj_order[sys]:
            prop_strings: list[str] = []
            for prop in prop_order[sys][obj]:
                evts = data[sys][obj][prop]
                numeric = _is_numeric(evts[0])

                if numeric:
                    try:
                        init_val = float(evts[0]["old"])
                    except (ValueError, KeyError):
                        init_val = 0.0
                    s = f"{prop}:{_trim_float(init_val)}"
                    for e in evts:
                        try:
                            old_n = float(e["old"])
                            new_n = float(e["new"])
                        except (ValueError, KeyError):
                            continue
                        delta = new_n - old_n
                        sign  = "+" if delta >= 0 else ""
                        s += f" {sign}{_trim_float(delta)}@{_trim_float(float(e.get('t', 0)), 2)}"
                else:
                    old_v = _format_bool(str(evts[0].get("old", "")))
                    new_v = _format_bool(str(evts[0].get("new", "")))
                    s = f"{prop}:{old_v}\u2192{new_v}@{_trim_float(float(evts[0].get('t', 0)))}"
                    for e in evts[1:]:
                        s += f"\u2192{_format_bool(str(e.get('new', '')))}@{_trim_float(float(e.get('t', 0)))}"

                prop_strings.append(s)

            padded = obj.ljust(20)
            body_lines.append(padded + "| " + " | ".join(prop_strings) + "\n")

        body_lines.append("\n")

    body = "".join(body_lines)

    # ── Build YAML header ─────────────────────────────────────────────────────
    session_id      = open_meta.get("session_id",      ndjson_path.stem)
    started_at      = open_meta.get("started_at",      "")
    ue_project_name = open_meta.get("ue_project_name", "")
    plugin_version  = open_meta.get("plugin_version",  "")
    armed_systems: list = open_meta.get("armed_systems", [])
    systems_tracked_json = json.dumps(armed_systems)

    ended_at         = close_meta.get("ended_at",         "")  if close_meta else ""
    duration_seconds = close_meta.get("duration_seconds", 0)   if close_meta else 0
    end_reason       = close_meta.get("end_reason",       "crash") if close_meta else "crash"
    event_count      = len(events)

    try:
        dt = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        heading_date = dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        heading_date = started_at

    header = (
        "---\n"
        f"session_id: {session_id}\n"
        f"ue_project_name: {ue_project_name}\n"
        f"plugin_version: {plugin_version}\n"
        f"started_at: {started_at}\n"
        f"ended_at: {ended_at}\n"
        f"duration_seconds: {duration_seconds}\n"
        f"end_reason: {end_reason}\n"
        f"systems_tracked: {systems_tracked_json}\n"
        f"event_count: {event_count}\n"
        "---\n"
        "\n"
        "> This is a Nytwatch gameplay session log from Unreal Engine 5. "
        "It records UObject property changes captured during a Play-In-Editor session.\n"
        "> Format: one line per object. Properties separated by `|`. "
        "Numeric properties use delta encoding: `PropName:InitialValue +N@t -N@t` "
        "where `t` is seconds from session start. "
        "Non-numeric properties (enum, string, bool, vector) use transition chains: "
        "`PropName:From\u2192To@t`. "
        "Booleans abbreviated as T/F. "
        "UE class prefixes (A/U) are stripped from object names. "
        "Objects with no recorded changes are omitted.\n"
        "\n"
        f"# {ue_project_name} \u2014 {heading_date}\n"
        "\n"
    )

    # Atomic write: temp file → rename
    tmp_path = output_md_path.with_suffix(".md.tmp")
    tmp_path.write_text(header + body, encoding="utf-8")
    tmp_path.replace(output_md_path)

    log.info(
        "consolidate: wrote %s (%d events, end_reason=%s)",
        output_md_path.name, event_count, end_reason,
    )
