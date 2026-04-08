"""
Write-back helpers: update sprint markdown files.

Task status is stored directly in sprint markdown as [{status}] markers.
No YAML files — markdown is the single source of truth.
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path


# ── Status ↔ marker mapping ───────────────────────────────────────────────────

_STATUS_TO_MARKER: dict[str, str] = {
    "backlog":      "backlog",
    "ready-for-dev": "ready",
    "in-progress":  "in_progress",
    "review":       "review",
    "done":         "done",
    "blocked":      "blocked",
}

# Recognized markers (for detecting checklist format)
_ALL_MARKERS = set(_STATUS_TO_MARKER.values()) | {" ", "x", "X"}


# ── Path helpers ──────────────────────────────────────────────────────────────

def _sprint_path(studio_path: Path, sprint_n: int) -> Path:
    """Return the sprint file path, handling both sprint-1.md and sprint-01.md naming."""
    sprints_dir = studio_path / "production" / "sprints"
    # Try zero-padded first (sprint-01.md), then plain (sprint-1.md)
    for candidate in [
        sprints_dir / f"sprint-{sprint_n:02d}.md",
        sprints_dir / f"sprint-{sprint_n}.md",
    ]:
        if candidate.exists():
            return candidate
    # Default to plain (for new files being created)
    return sprints_dir / f"sprint-{sprint_n}.md"


def _milestone_path(studio_path: Path, slug: str) -> Path:
    return studio_path / "production" / "milestones" / f"{slug}.md"


# ── Checklist markdown helpers ────────────────────────────────────────────────

def _is_checklist(content: str) -> bool:
    """Return True if this sprint file uses the checklist (P0/P1/P2) format."""
    return bool(re.search(
        r'^- \[(?:[ xX]|backlog|ready|in_progress|review|done|blocked)\]',
        content, re.MULTILINE,
    ))


def _find_task_line_index(lines: list[str], task_id: str, task_name: str) -> int:
    """
    Return the index of the checklist line that owns this task.

    Strategy 1 — TASK-XX explicit ID: match the bold ID marker in the line.
    Strategy 2 — name prefix: find a top-level checklist line containing the
                 first 30 characters of the task name.
    Returns -1 if not found.
    """
    # Strategy 1: TASK-XX base ID (works for named IDs like TASK-10, FEAT-3)
    base_m = re.match(r'([A-Za-z]+-\d+)', task_id)
    if base_m:
        base_id = base_m.group(1)
        pattern = re.compile(
            rf'^- \[[^\]]*\].*\*\*{re.escape(base_id)}', re.IGNORECASE
        )
        for i, line in enumerate(lines):
            if pattern.match(line):
                return i

    # Strategy 2: name prefix (top-level items only, not indented)
    if task_name:
        prefix = task_name[:30].lower()
        for i, line in enumerate(lines):
            if re.match(r'^- \[[^\]]*\]', line) and prefix in line.lower():
                return i

    return -1


def set_task_status_in_sprint(studio_path: Path, sprint_n: int, task_id: str,
                               task_name: str, new_status: str) -> bool:
    """
    Write the [{status}] marker for a task in a checklist-format sprint file.
    Handles both legacy [ ]/[x] files and new [status] files.
    """
    p = _sprint_path(studio_path, sprint_n)
    if not p.exists():
        return False
    content = p.read_text(encoding='utf-8')
    if not _is_checklist(content):
        return False

    lines = content.splitlines(keepends=True)
    idx = _find_task_line_index([l.rstrip('\n') for l in lines], task_id, task_name)
    if idx == -1:
        return False

    marker = _STATUS_TO_MARKER.get(new_status, new_status)
    lines[idx] = re.sub(r'^(- \[)[^\]]*(\])', rf'\g<1>{marker}\g<2>', lines[idx], count=1)
    p.write_text(''.join(lines), encoding='utf-8')
    return True


def _update_checklist_task_name(studio_path: Path, sprint_n: int, task_id: str,
                                 old_name: str, new_name: str) -> bool:
    """Update the task name text on its checklist line (after the ID marker or checkbox)."""
    p = _sprint_path(studio_path, sprint_n)
    if not p.exists():
        return False
    content = p.read_text(encoding='utf-8')
    if not _is_checklist(content):
        return False

    lines = content.splitlines(keepends=True)
    flat = [l.rstrip('\n') for l in lines]
    idx = _find_task_line_index(flat, task_id, old_name)
    if idx == -1:
        return False

    line = lines[idx]
    old_escaped = re.escape(old_name[:50])
    new_content = re.sub(old_escaped, new_name[:80], line, count=1)
    if new_content != line:
        lines[idx] = new_content
        p.write_text(''.join(lines), encoding='utf-8')
        return True
    return False


# ── Sprint markdown files ─────────────────────────────────────────────────────

_SPRINT_TEMPLATE = """\
# Sprint {n} -- {start} to {end}

**Goal**: {goal}

## P0 Items (Must Have)

## P1 Items (Should Have)

## P2 Items (Nice to Have)

## Definition of Done for this Sprint
- [done] All P0 tasks completed
- [done] All tasks pass acceptance criteria
- [done] No S1 or S2 bugs in delivered features
"""

_P_SECTION_HEADERS = {
    "must-have":    r"##\s+P0\b",
    "should-have":  r"##\s+P1\b",
    "nice-to-have": r"##\s+P2\b",
}

_TABLE_SECTION_HEADERS = {
    "must-have":    "### Must Have (Critical Path)",
    "should-have":  "### Should Have",
    "nice-to-have": "### Nice to Have",
}

_TABLE_HEADER = "| ID | Task | Agent/Owner | Est. Days | Dependencies | Acceptance Criteria |"
_TABLE_SEP    = "|----|------|-------------|-----------|-------------|-------------------|"


def create_sprint_file(studio_path: Path, sprint_n: int, goal: str,
                       start_date: str, end_date: str) -> Path:
    p = _sprint_path(studio_path, sprint_n)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        _SPRINT_TEMPLATE.format(n=sprint_n, start=start_date, end=end_date, goal=goal),
        encoding='utf-8',
    )
    return p


def update_sprint_metadata(studio_path: Path, sprint_n: int, goal: str,
                            start_date: str, end_date: str) -> bool:
    p = _sprint_path(studio_path, sprint_n)
    if not p.exists():
        return False
    content = p.read_text(encoding='utf-8')

    # Update title line
    content = re.sub(
        r'^(# Sprint \d+\s*)[-–—]+\s*.*$',
        f'# Sprint {sprint_n} -- {start_date} to {end_date}',
        content, flags=re.MULTILINE, count=1,
    )

    # Update goal: try ## Sprint Goal section first, then inline **Goal**:
    if re.search(r'##\s+Sprint Goal', content):
        content = re.sub(
            r'(## Sprint Goal\s*\n).*?(?=\n## |\Z)',
            f'\\g<1>{goal}\n\n',
            content, flags=re.DOTALL, count=1,
        )
    else:
        content = re.sub(
            r'(\*{0,2}Goal\*{0,2}[:\s]+)[^\n]+',
            f'\\g<1>{goal}',
            content, flags=re.IGNORECASE, count=1,
        )

    p.write_text(content, encoding='utf-8')
    return True


def delete_sprint_file(studio_path: Path, sprint_n: int) -> bool:
    p = _sprint_path(studio_path, sprint_n)
    if p.exists():
        p.unlink()
        return True
    return False


_RETRO_SKELETON = """\
# Sprint {n} Retrospective — {date}

**Sprint**: Sprint {n}
**Dates**: {start} → {end}
**Closed**: {date}
**Goal**: {goal}

## Velocity

| Tier | Planned | Done | Incomplete | % |
|------|---------|------|------------|---|
| P0 (Must Have) | {p0_planned} | {p0_done} | {p0_inc} | {p0_pct}% |
| P1 (Should Have) | {p1_planned} | {p1_done} | {p1_inc} | {p1_pct}% |
| P2 (Nice to Have) | {p2_planned} | {p2_done} | {p2_inc} | {p2_pct}% |
| **Total** | {total_planned} | {total_done} | {total_inc} | {total_pct}% |

## Completed
{completed_list}

## Incomplete / Carryover
{incomplete_list}

## Gate Results
- **Smoke Check**: Skipped — run `/sprint-close` for full ceremony
- **QA Sign-Off**: Skipped — run `/sprint-close` for full ceremony

## What Went Well
*(Fill in during `/retrospective sprint-{n}`)*

## What Could Be Better
*(Fill in during `/retrospective sprint-{n}`)*

## Action Items for Next Sprint
*(Fill in during `/sprint-close`)*

---
*Skeleton created by nytwatch UI. Run `/sprint-close` in claude-nytwatch-studio for the full ceremony: smoke check, QA gate, carryover decisions, and detailed retrospective.*
"""


def close_sprint_file(studio_path: Path, sprint_n: int,
                       sprint_data: dict | None = None) -> bool:
    """
    Mark a sprint as closed: writes **Status**: Closed + **Closed**: date to the
    sprint markdown, and creates a skeleton retrospective file.
    Returns True if the sprint file was updated.
    """
    p = _sprint_path(studio_path, sprint_n)
    if not p.exists():
        return False

    content = p.read_text(encoding='utf-8')
    today = datetime.now().strftime('%Y-%m-%d')

    # Already closed — still write/update retro, but don't re-write sprint file
    already_closed = bool(re.search(r'\*{0,2}Status\*{0,2}[:\s]+Closed', content, re.IGNORECASE))

    if not already_closed:
        # Update or insert **Status**: Closed
        if re.search(r'\*{0,2}Status\*{0,2}[:\s]+', content, re.IGNORECASE):
            content = re.sub(
                r'\*{0,2}Status\*{0,2}[:\s]+[^\n]+',
                f'**Status**: Closed',
                content, count=1,
            )
        else:
            # Insert after H1 title line
            content = re.sub(
                r'^(# Sprint[^\n]+\n)',
                f'\\g<1>\n**Status**: Closed\n',
                content, count=1, flags=re.MULTILINE,
            )

        # Add **Closed**: date if not already present
        if not re.search(r'\*{0,2}Closed\*{0,2}[:\s]+', content, re.IGNORECASE):
            content = content.replace('**Status**: Closed', f'**Status**: Closed\n**Closed**: {today}', 1)
        else:
            content = re.sub(
                r'\*{0,2}Closed\*{0,2}[:\s]+[^\n]+',
                f'**Closed**: {today}',
                content, count=1,
            )

        p.write_text(content, encoding='utf-8')

    # ── Write skeleton retrospective ──────────────────────────────────────────
    retro_dir = studio_path / "production" / "retrospectives"
    retro_dir.mkdir(parents=True, exist_ok=True)
    retro_path = retro_dir / f"retro-sprint-{sprint_n}-{today}.md"

    # Skip if a retro file already exists for this sprint+date
    if not retro_path.exists():
        tasks = (sprint_data or {}).get('tasks', [])
        p0 = [t for t in tasks if t.get('priority') == 'must-have']
        p1 = [t for t in tasks if t.get('priority') == 'should-have']
        p2 = [t for t in tasks if t.get('priority') == 'nice-to-have']

        def _tally(lst):
            done = sum(1 for t in lst if t.get('status') == 'done')
            inc  = len(lst) - done
            pct  = round(done / len(lst) * 100) if lst else 0
            return len(lst), done, inc, pct

        p0t, p0d, p0i, p0p = _tally(p0)
        p1t, p1d, p1i, p1p = _tally(p1)
        p2t, p2d, p2i, p2p = _tally(p2)
        tot, toD, toI, toP  = _tally(tasks)

        def _task_line(t):
            return f"- [{t.get('status','?')}] **{t.get('id','')}**: {t.get('name','')}"

        completed = [_task_line(t) for t in tasks if t.get('status') == 'done']
        incomplete = [_task_line(t) for t in tasks if t.get('status') != 'done']

        sd = (sprint_data or {}).get('start_date', '')
        ed = (sprint_data or {}).get('end_date', '')
        goal = (sprint_data or {}).get('goal', '')

        retro_path.write_text(
            _RETRO_SKELETON.format(
                n=sprint_n, date=today, start=sd, end=ed, goal=goal,
                p0_planned=p0t, p0_done=p0d, p0_inc=p0i, p0_pct=p0p,
                p1_planned=p1t, p1_done=p1d, p1_inc=p1i, p1_pct=p1p,
                p2_planned=p2t, p2_done=p2d, p2_inc=p2i, p2_pct=p2p,
                total_planned=tot, total_done=toD, total_inc=toI, total_pct=toP,
                completed_list='\n'.join(completed) or '*(none)*',
                incomplete_list='\n'.join(incomplete) or '*(none — all tasks done)*',
            ),
            encoding='utf-8',
        )

    return True


def _make_checklist_item(task: dict) -> str:
    """Build a `- [backlog] **ID**: name` checklist line for a new task."""
    marker = _STATUS_TO_MARKER.get(task.get('status', 'backlog'), 'backlog')
    tid  = task.get('id', '')
    name = task.get('name', '')
    if tid:
        return f"- [{marker}] **{tid}**: {name}\n"
    return f"- [{marker}] {name}\n"


def _make_row(task: dict) -> str:
    """Build a markdown table row string for the given task dict."""
    tid    = task.get('id', '')
    name   = task.get('name', '')
    owner  = task.get('owner', '')
    est    = str(task.get('estimate_days', 0))
    deps   = task.get('dependencies', '')
    ac     = task.get('acceptance_criteria', '')
    return f"| {tid} | {name} | {owner} | {est} | {deps} | {ac} |"


def add_task_to_sprint(studio_path: Path, sprint_n: int, task: dict) -> bool:
    p = _sprint_path(studio_path, sprint_n)
    if not p.exists():
        return False

    content = p.read_text(encoding='utf-8')
    priority = task.get('priority', 'should-have')

    if _is_checklist(content):
        # Find the matching P-section header and append after its last item
        sec_pattern = _P_SECTION_HEADERS.get(priority, _P_SECTION_HEADERS["should-have"])
        new_item = _make_checklist_item(task)

        # Match section header through to next ## or EOF, insert item before boundary
        def _insert_checklist(m: re.Match) -> str:
            block = m.group(0).rstrip('\n')
            return block + '\n' + new_item

        new_content, n = re.subn(
            rf'({sec_pattern}[^\n]*\n(?:(?!^##)[^\n]*\n)*)',
            _insert_checklist,
            content, count=1, flags=re.MULTILINE,
        )
        if n == 0:
            # Section not found — append to end
            new_content = content.rstrip('\n') + f"\n{new_item}"

        p.write_text(new_content, encoding='utf-8')
        return True

    else:
        # Table format
        header = _TABLE_SECTION_HEADERS.get(priority, _TABLE_SECTION_HEADERS["should-have"])
        row = _make_row(task)

        pattern = rf'({re.escape(header)}.*?{re.escape(_TABLE_SEP)}\n)(.*?)(?=\n###|\n##|\Z)'

        def _inserter(m: re.Match) -> str:
            table_top  = m.group(1)
            table_body = m.group(2).rstrip('\n')
            return f"{table_top}{table_body}\n{row}\n"

        new_content, n = re.subn(pattern, _inserter, content, count=1, flags=re.DOTALL)
        if n == 0:
            new_content = content.rstrip('\n') + f"\n{row}\n"

        p.write_text(new_content, encoding='utf-8')
        return True


def remove_task_from_sprint(studio_path: Path, sprint_n: int, task_id: str,
                             task_name: str = "") -> bool:
    p = _sprint_path(studio_path, sprint_n)
    if not p.exists():
        return False
    content = p.read_text(encoding='utf-8')

    if _is_checklist(content):
        lines = content.splitlines(keepends=True)
        idx = _find_task_line_index([l.rstrip('\n') for l in lines], task_id, task_name)
        if idx == -1:
            return False
        del lines[idx]
        p.write_text(''.join(lines), encoding='utf-8')
        return True
    else:
        # Table format: remove the row matching the task ID
        new_content = re.sub(
            rf'^\|\s*{re.escape(task_id)}\s*\|[^\n]*\n?', '', content, flags=re.MULTILINE
        )
        if new_content != content:
            p.write_text(new_content, encoding='utf-8')
            return True
        return False


def update_task_in_sprint(studio_path: Path, sprint_n: int, task: dict,
                           old_name: str = "") -> bool:
    """
    Update a task in the sprint markdown file.
    - Checklist format: updates [{status}] marker and task name (if changed).
    - Table format: replaces the full row.
    """
    p = _sprint_path(studio_path, sprint_n)
    if not p.exists():
        return False
    content = p.read_text(encoding='utf-8')
    task_id   = task['id']
    task_name = task.get('name', '')
    new_status = task.get('status', 'backlog')

    if _is_checklist(content):
        changed = False
        # Update status marker
        if set_task_status_in_sprint(studio_path, sprint_n, task_id,
                                      old_name or task_name, new_status):
            changed = True
        # Update name if it changed
        if old_name and old_name != task_name:
            if _update_checklist_task_name(studio_path, sprint_n, task_id,
                                            old_name, task_name):
                changed = True
        return changed
    else:
        # Table format: replace the full row
        new_row = _make_row(task)
        new_content = re.sub(
            rf'^\|\s*{re.escape(task_id)}\s*\|[^\n]*',
            new_row,
            content, flags=re.MULTILINE, count=1,
        )
        if new_content != content:
            p.write_text(new_content, encoding='utf-8')
            return True
        return False


def move_task_between_sprints(
    studio_path: Path,
    task: dict,
    from_sprint: int,
    to_sprint: int,
) -> bool:
    """Remove task from source sprint file and add to destination sprint file."""
    remove_task_from_sprint(studio_path, from_sprint, task['id'], task.get('name', ''))
    task_copy = dict(task, sprint=to_sprint)
    add_task_to_sprint(studio_path, to_sprint, task_copy)
    return True


# ── Milestones ────────────────────────────────────────────────────────────────

_MILESTONE_TEMPLATE = """\
# Milestone: {name}

> **Target Date**: {target_date}
> **Status**: {status}
> **Sprints**: {sprints}

## Goal
{goal}

## Features
- [backlog] (Add features here)
"""


def create_milestone(studio_path: Path, slug: str, name: str, target_date: str,
                     goal: str, sprints: list[int], status: str = "Planned") -> Path:
    p = _milestone_path(studio_path, slug)
    p.parent.mkdir(parents=True, exist_ok=True)
    sprint_str = ", ".join(str(n) for n in sprints) if sprints else ""
    p.write_text(
        _MILESTONE_TEMPLATE.format(
            name=name, target_date=target_date, status=status,
            sprints=sprint_str, goal=goal,
        ),
        encoding='utf-8',
    )
    return p


def update_milestone(studio_path: Path, slug: str, name: str, target_date: str,
                     goal: str, sprints: list[int], status: str) -> bool:
    p = _milestone_path(studio_path, slug)
    if not p.exists():
        return False
    content = p.read_text(encoding='utf-8')
    sprint_str = ", ".join(str(n) for n in sprints) if sprints else ""

    content = re.sub(r'\*{0,2}Target Date\*{0,2}[:\s]+[^\n]+',
                     f'**Target Date**: {target_date}', content)
    content = re.sub(r'\*{0,2}Status\*{0,2}[:\s]+[^\n]+',
                     f'**Status**: {status}', content)
    content = re.sub(r'\*{0,2}Sprints\*{0,2}[:\s]+[^\n]+',
                     f'**Sprints**: {sprint_str}', content)

    # Update H1 name
    content = re.sub(
        r'^#\s+(?:Milestone:\s*)?.*$',
        f'# Milestone: {name}',
        content, flags=re.MULTILINE, count=1,
    )

    # Update goal section
    content = re.sub(
        r'(## Goal\s*\n).*?(?=\n## |\Z)',
        f'\\g<1>{goal}\n\n',
        content, flags=re.DOTALL, count=1,
    )

    p.write_text(content, encoding='utf-8')
    return True


def delete_milestone(studio_path: Path, slug: str) -> bool:
    p = _milestone_path(studio_path, slug)
    if p.exists():
        p.unlink()
        return True
    return False
