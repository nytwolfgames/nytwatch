from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class DesignDoc:
    slug: str        # e.g. "gdd/battle-system" or "docs/architecture/overview"
    title: str
    rel_path: str    # display path, e.g. "gdd/battle-system.md"
    section: str     # top-level group shown in the list panel
    subsection: str  # sub-group within the section (empty if flat)
    raw_content: str = ""


def _planning_root(repo_path: str) -> Optional[Path]:
    """Return the planning/ directory for the active project.

    Checks studio layout (<studio>/production/ sibling) first,
    then falls back to <repo>/planning/.
    """
    from nytwatch.pm.parser import find_studio_path
    # find_studio_path returns the dir that contains production/ — typically <repo>/planning
    studio = find_studio_path(repo_path)
    if studio is not None:
        if (studio / "design").exists() or (studio / "docs").exists():
            return studio
    p2 = Path(repo_path) / "planning"
    return p2 if p2.exists() else None


def _extract_title(content: str, stem: str) -> str:
    m = re.search(r'^#\s+(.+)$', content, re.MULTILINE)
    if m:
        title = re.sub(r'\*+|`|_+', '', m.group(1)).strip()
        title = re.sub(r'\s+[—–-].*$', '', title).strip()
        return title or stem
    return stem.replace("-", " ").replace("_", " ").title()


def _scan_folder(folder: Path, section: str, slug_prefix: str) -> list[DesignDoc]:
    """Recursively scan *folder*, producing docs with the given section name.

    slug_prefix is prepended to the relative path within the folder.
    e.g. slug_prefix="gdd", file="battle-system.md" → slug="gdd/battle-system"
    """
    docs: list[DesignDoc] = []
    for md_file in sorted(folder.rglob("*.md")):
        name = md_file.name
        if name.startswith('_') or name.startswith('.'):
            continue
        try:
            content = md_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        # rel_path within the folder, forward slashes
        rel_within = str(md_file.relative_to(folder)).replace("\\", "/")
        slug = slug_prefix + "/" + rel_within[: -len(".md")]
        rel_path = slug_prefix + "/" + rel_within

        # subsection = intermediate dirs between the folder root and the file
        parts = rel_within.split("/")
        subsection = "/".join(parts[:-1]) if len(parts) > 1 else ""

        docs.append(DesignDoc(
            slug=slug,
            title=_extract_title(content, md_file.stem),
            rel_path=rel_path,
            section=section,
            subsection=subsection,
            raw_content=content,
        ))
    return docs


def load_design_docs(repo_path: str) -> list[DesignDoc]:
    """Load docs from planning/design/* (each subdir = section)
    and planning/docs/ (section = "docs", subdirs = subsections)."""
    planning = _planning_root(repo_path)
    if planning is None:
        return []

    docs: list[DesignDoc] = []

    # ── planning/design/<folder>/ ─────────────────────────────────────────
    design = planning / "design"
    if design.exists():
        try:
            subdirs = sorted(
                d for d in design.iterdir()
                if d.is_dir() and not d.name.startswith('.') and not d.name.startswith('_')
            )
        except OSError:
            subdirs = []
        for subdir in subdirs:
            docs.extend(_scan_folder(subdir, section=subdir.name, slug_prefix=subdir.name))

    # ── planning/docs/ ────────────────────────────────────────────────────
    docs_dir = planning / "docs"
    if docs_dir.exists():
        docs.extend(_scan_folder(docs_dir, section="docs", slug_prefix="docs"))

    return docs


def doc_to_dict(doc: DesignDoc) -> dict:
    return {
        "slug": doc.slug,
        "title": doc.title,
        "rel_path": doc.rel_path,
        "section": doc.section,
        "subsection": doc.subsection,
        "raw_content": doc.raw_content,
    }
