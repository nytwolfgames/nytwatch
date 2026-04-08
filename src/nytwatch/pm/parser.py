"""
Parse claude-nytwatch-studio production/ markdown files into Python dataclasses.

Reads:
  production/sprints/sprint-N.md  — sprint task checklists or tables
  production/milestones/*.md      — milestone definitions

Task status is stored directly in the markdown as [{status}] markers.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# ── Constants ─────────────────────────────────────────────────────────────────

TASK_STATUSES = [
    "backlog",
    "ready-for-dev",
    "in-progress",
    "review",
    "done",
    "blocked",
]

STATUS_LABELS = {
    "backlog": "Backlog",
    "ready-for-dev": "Ready",
    "in-progress": "In Progress",
    "review": "Review",
    "done": "Done",
    "blocked": "Blocked",
}

PRIORITY_ORDER = {"must-have": 0, "should-have": 1, "nice-to-have": 2}

# Marker ↔ status mapping for [{status}] checklist format
# Supports both legacy [ ]/[x] and new named-status markers
_MARKER_TO_STATUS: dict[str, str] = {
    " ":          "backlog",
    "x":          "done",
    "X":          "done",
    "backlog":    "backlog",
    "ready":      "ready-for-dev",
    "in_progress":"in-progress",
    "review":     "review",
    "done":       "done",
    "blocked":    "blocked",
}

_STATUS_TO_MARKER: dict[str, str] = {
    "backlog":     "backlog",
    "ready-for-dev": "ready",
    "in-progress": "in_progress",
    "review":      "review",
    "done":        "done",
    "blocked":     "blocked",
}

MILESTONE_STATUSES = ["planned", "in progress", "complete", "at risk", "on hold"]


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class Task:
    id: str
    name: str
    owner: str = ""
    estimate_days: float = 0.0
    dependencies: str = ""
    acceptance_criteria: str = ""
    priority: str = "should-have"
    status: str = "backlog"
    sprint: int = 0
    blocker: str = ""
    completed: str = ""
    file: str = ""


@dataclass
class Sprint:
    number: int
    title: str = ""
    goal: str = ""
    start_date: str = ""
    end_date: str = ""
    tasks: list = field(default_factory=list)  # list[Task]
    file_path: str = ""


@dataclass
class Milestone:
    slug: str
    name: str
    target_date: str = ""
    status: str = "planned"
    goal: str = ""
    sprints: list = field(default_factory=list)   # list[int]
    features: list = field(default_factory=list)  # list[str]
    file_path: str = ""


# ── Studio path detection ─────────────────────────────────────────────────────

def find_studio_path(repo_path: str) -> Optional[Path]:
    """
    Return the root directory that contains a production/ folder.
    Checks the repo root first, then one level of subdirectories.
    """
    root = Path(repo_path)
    if (root / "production").exists():
        return root
    # Check immediate subdirectories (e.g. claude-nytwatch-studio/)
    try:
        for sub in sorted(root.iterdir()):
            if sub.is_dir() and not sub.name.startswith('.') and (sub / "production").exists():
                return sub
    except PermissionError:
        pass
    return None


# ── Markdown table helpers ────────────────────────────────────────────────────

def _parse_table_rows(block: str) -> list[list[str]]:
    """Extract data rows from a markdown table block (skips header + separator)."""
    rows = []
    for line in block.splitlines():
        stripped = line.strip()
        if not stripped.startswith('|'):
            continue
        if re.match(r'^\|[-| :]+\|$', stripped):
            continue  # separator row
        cells = [c.strip() for c in stripped.strip('|').split('|')]
        if not cells:
            continue
        # Skip header rows where first cell looks like a column heading
        if re.match(r'^(ID|#|Task|Story)$', cells[0], re.IGNORECASE):
            continue
        rows.append(cells)
    return rows


def _parse_float(s: str) -> float:
    try:
        m = re.search(r'[\d.]+', s)
        return float(m.group()) if m else 0.0
    except Exception:
        return 0.0


# ── Checklist-format parser (P0/P1/P2 sections with - [ ] items) ─────────────

def _is_checklist_format(content: str) -> bool:
    """Return True if the file uses checklist-style tasks (not markdown tables).
    Detects both legacy [ ]/[x] and named [{status}] markers."""
    return bool(re.search(
        r'^- \[(?:[ xX]|backlog|ready|in_progress|review|done|blocked)\]',
        content, re.MULTILINE,
    ))


def _parse_checklist_tasks(content: str, sprint_n: int) -> list[Task]:
    """
    Parse tasks from a checklist-format sprint file.
    Sections: ## P0 Items → must-have, ## P1 Items → should-have, ## P2 Items → nice-to-have.
    Task IDs extracted from **TASK-XX**: patterns; others auto-generated.
    Sub-items (indented) are collected as acceptance criteria.
    """
    tasks: list[Task] = []
    seen_ids: set[str] = set()
    auto_n = 0

    priority_sections = [
        ("must-have",    r'##\s+P0\b[^\n]*\n(.*?)(?=\n##|\Z)'),
        ("should-have",  r'##\s+P1\b[^\n]*\n(.*?)(?=\n##|\Z)'),
        ("nice-to-have", r'##\s+P2\b[^\n]*\n(.*?)(?=\n##|\Z)'),
    ]

    for priority, pattern in priority_sections:
        sec_m = re.search(pattern, content, re.DOTALL | re.IGNORECASE)
        if not sec_m:
            continue
        block = sec_m.group(1)
        lines = block.splitlines()

        i = 0
        while i < len(lines):
            line = lines[i]
            # Top-level checklist item (no leading spaces); accepts [ ]/[x]/[status]
            top_m = re.match(r'^- \[([^\]]*)\] (.+)$', line)
            if not top_m:
                i += 1
                continue

            marker = top_m.group(1)
            status = _MARKER_TO_STATUS.get(marker, 'backlog')
            text = top_m.group(2).strip()

            # Extract explicit task ID: **TASK-10**: or **TASK-10 (complete)**:
            id_m = re.match(r'\*\*([A-Za-z]+-\d+(?:\s*\([^)]*\))?)\*\*[:\s]*(.*)$', text)
            if id_m:
                raw_id = id_m.group(1).strip()
                task_name = id_m.group(2).strip() or raw_id
                # Normalize: "TASK-10 (complete)" → "TASK-10-complete"
                task_id = re.sub(r'\s+', '-', re.sub(r'[().]', '', raw_id)).rstrip('-')
            else:
                auto_n += 1
                task_id = f"S{sprint_n}-{auto_n:03d}"
                # Strip any remaining bold markup from name
                task_name = re.sub(r'\*\*[^*]+\*\*:\s*', '', text)

            # Deduplicate IDs
            orig_id = task_id
            dup = 0
            while task_id in seen_ids:
                dup += 1
                task_id = f"{orig_id}-{dup}"
            seen_ids.add(task_id)

            # Collect indented sub-items as acceptance criteria
            sub_items: list[str] = []
            j = i + 1
            while j < len(lines):
                sub_line = lines[j]
                sub_m = re.match(r'^\s+- \[[^\]]*\] (.+)$', sub_line)
                if sub_m:
                    sub_items.append(sub_m.group(1).strip())
                    j += 1
                elif sub_line.startswith('  ') or not sub_line.strip():
                    j += 1
                else:
                    break

            tasks.append(Task(
                id=task_id,
                name=task_name,
                priority=priority,
                status=status,
                sprint=sprint_n,
                acceptance_criteria='; '.join(sub_items) if sub_items else '',
            ))
            i = j

    return tasks


def _parse_table_tasks(content: str, sprint_n: int) -> list[Task]:
    """Parse tasks from a table-format sprint file (Must Have / Should Have / Nice to Have)."""
    tasks: list[Task] = []
    seen_ids: set[str] = set()

    sections = [
        ("must-have",    r'###\s+Must Have.*?\n(.*?)(?=\n###|\n##|\Z)'),
        ("should-have",  r'###\s+Should Have.*?\n(.*?)(?=\n###|\n##|\Z)'),
        ("nice-to-have", r'###\s+Nice to Have.*?\n(.*?)(?=\n###|\n##|\Z)'),
    ]

    for priority, pattern in sections:
        sec_m = re.search(pattern, content, re.DOTALL | re.IGNORECASE)
        if not sec_m:
            continue
        for row in _parse_table_rows(sec_m.group(1)):
            if len(row) < 2:
                continue
            task_id, task_name = row[0], row[1]
            if not task_id or not task_name or task_id in seen_ids:
                continue
            seen_ids.add(task_id)
            tasks.append(Task(
                id=task_id,
                name=task_name,
                owner=row[2] if len(row) > 2 else "",
                estimate_days=_parse_float(row[3]) if len(row) > 3 else 0.0,
                dependencies=row[4] if len(row) > 4 else "",
                acceptance_criteria=row[5] if len(row) > 5 else "",
                priority=priority,
                status="backlog",
                sprint=sprint_n,
            ))

    return tasks


# ── Sprint parser ─────────────────────────────────────────────────────────────

def _parse_sprint_file(file_path: Path) -> Sprint:
    content = file_path.read_text(encoding='utf-8', errors='replace')

    # Sprint number from filename
    m = re.search(r'(\d+)', file_path.stem)
    number = int(m.group(1)) if m else 0

    # Title (H1 line, strip trailing theme after em-dash or --)
    title_m = re.search(r'^#\s+(.+)$', content, re.MULTILINE)
    title = title_m.group(1).strip() if title_m else f"Sprint {number}"

    # Dates: ISO format "2024-01-01 to 2024-01-14"
    dates_m = re.search(r'(\d{4}-\d{2}-\d{2})\s+to\s+(\d{4}-\d{2}-\d{2})', content)
    start_date = dates_m.group(1) if dates_m else ""
    end_date   = dates_m.group(2) if dates_m else ""

    # Weeks range fallback: "**Weeks**: 1–2"
    if not start_date:
        weeks_m = re.search(r'\*{0,2}Weeks?\*{0,2}[:\s]+([^\n]+)', content, re.IGNORECASE)
        start_date = weeks_m.group(1).strip() if weeks_m else ""

    # Goal: "## Sprint Goal\n..." or "**Goal**: ..."
    goal_m = re.search(r'##\s+Sprint Goal\s*\n(.*?)(?=\n##|\Z)', content, re.DOTALL)
    if goal_m:
        goal = goal_m.group(1).strip()
    else:
        goal_m2 = re.search(r'\*{0,2}Goal\*{0,2}[:\s]+([^\n]+)', content, re.IGNORECASE)
        goal = goal_m2.group(1).strip() if goal_m2 else ""

    # Detect format and parse tasks
    if _is_checklist_format(content):
        tasks = _parse_checklist_tasks(content, number)
    else:
        tasks = _parse_table_tasks(content, number)

    return Sprint(
        number=number,
        title=title,
        goal=goal,
        start_date=start_date,
        end_date=end_date,
        tasks=tasks,
        file_path=str(file_path),
    )


def load_sprints(studio_path: Path) -> list[Sprint]:
    """Load and return all sprints sorted by sprint number."""
    sprints_dir = studio_path / "production" / "sprints"
    if not sprints_dir.exists():
        return []

    sprints = []
    for f in sorted(sprints_dir.glob("sprint-*.md")):
        try:
            sprints.append(_parse_sprint_file(f))
        except Exception:
            pass

    sprints.sort(key=lambda s: s.number)
    return sprints


# ── Milestone parser ──────────────────────────────────────────────────────────

def _parse_milestone_file(file_path: Path) -> Milestone:
    content = file_path.read_text(encoding='utf-8', errors='replace')
    slug = file_path.stem

    # Name from H1 (strip "Milestone:" prefix if present)
    name_m = re.search(r'^#\s+(?:Milestone:\s*)?(.+)$', content, re.MULTILINE)
    name = name_m.group(1).strip() if name_m else slug.replace('-', ' ').title()

    # Target date
    date_m = re.search(r'\*{0,2}Target Date\*{0,2}[:\s]+([^\n]+)', content, re.IGNORECASE)
    target_date = date_m.group(1).strip().strip('*').strip() if date_m else ""

    # Status
    status_m = re.search(r'\*{0,2}Status\*{0,2}[:\s]+([^\n]+)', content, re.IGNORECASE)
    status_raw = status_m.group(1).strip().strip('*').strip().lower() if status_m else "planned"
    status = status_raw if status_raw in MILESTONE_STATUSES else "planned"

    # Associated sprints — try explicit "**Sprints**: 1, 2" first,
    # then fall back to Sprint Structure table: "[Sprint 1](...)" links
    sprints_m = re.search(r'\*{0,2}Sprints?\*{0,2}[:\s]+([^\n]+)', content, re.IGNORECASE)
    sprints: list[int] = []
    if sprints_m:
        sprints = [int(n) for n in re.findall(r'\b(\d+)\b', sprints_m.group(1))]
    if not sprints:
        # Sprint Structure table: | [Sprint 1](...) | ... |
        sprints = [int(n) for n in re.findall(r'\[Sprint\s+(\d+)\]', content, re.IGNORECASE)]

    # Goal: "## Alpha Definition" or "## Goal" section, or the first paragraph after H1
    goal = ""
    for sec_pattern in [
        r'##\s+(?:Alpha|Beta|Release|Milestone)?\s*Definition\s*\n(.*?)(?=\n##|\Z)',
        r'##\s+Goal\s*\n(.*?)(?=\n##|\Z)',
    ]:
        gm = re.search(sec_pattern, content, re.DOTALL | re.IGNORECASE)
        if gm:
            goal = gm.group(1).strip()
            # Truncate at first blank line if very long
            goal = goal.split('\n\n')[0].strip()
            break

    # Feature checklist items (supports both legacy [ ]/[x] and [{status}])
    features = re.findall(r'-\s+\[[^\]]*\]\s+(.+)', content)

    return Milestone(
        slug=slug,
        name=name,
        target_date=target_date,
        status=status,
        goal=goal,
        sprints=sprints,
        features=features,
        file_path=str(file_path),
    )


def load_milestones(studio_path: Path) -> list[Milestone]:
    """Load all milestone definition files (skips -review.md files)."""
    ms_dir = studio_path / "production" / "milestones"
    if not ms_dir.exists():
        return []

    milestones = []
    for f in sorted(ms_dir.glob("*.md")):
        if f.name.endswith('-review.md'):
            continue
        try:
            milestones.append(_parse_milestone_file(f))
        except Exception:
            pass

    return milestones


# ── Dict serialization ────────────────────────────────────────────────────────

def task_to_dict(t: Task) -> dict:
    return {
        "id": t.id,
        "name": t.name,
        "owner": t.owner,
        "estimate_days": t.estimate_days,
        "dependencies": t.dependencies,
        "acceptance_criteria": t.acceptance_criteria,
        "priority": t.priority,
        "status": t.status,
        "sprint": t.sprint,
        "blocker": t.blocker,
        "completed": t.completed,
        "file": t.file,
    }


def sprint_to_dict(s: Sprint) -> dict:
    return {
        "number": s.number,
        "title": s.title,
        "goal": s.goal,
        "start_date": s.start_date,
        "end_date": s.end_date,
        "tasks": [task_to_dict(t) for t in s.tasks],
        "file_path": s.file_path,
    }


def milestone_to_dict(m: Milestone) -> dict:
    return {
        "slug": m.slug,
        "name": m.name,
        "target_date": m.target_date,
        "status": m.status,
        "goal": m.goal,
        "sprints": m.sprints,
        "features": m.features,
        "file_path": m.file_path,
    }
