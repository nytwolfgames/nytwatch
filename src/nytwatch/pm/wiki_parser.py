from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class WikiDoc:
    slug: str           # relative path without .md, e.g. "architecture" or "subsystems/campaign"
    title: str
    rel_path: str       # relative path from wiki root with extension
    section: str        # top-level subdirectory name, or "" for root-level docs
    links: list = field(default_factory=list)   # list of slugs this doc links to
    raw_content: str = ""


def _slug_from_path(wiki_root: Path, file_path: Path) -> str:
    rel = file_path.relative_to(wiki_root)
    return str(rel.with_suffix("")).replace("\\", "/")


def _extract_title(content: str, stem: str) -> str:
    m = re.search(r'^#\s+(.+)$', content, re.MULTILINE)
    if m:
        # Strip markdown formatting from the heading
        title = m.group(1).strip()
        title = re.sub(r'\*+|`|_+', '', title).strip()
        # Strip trailing " — LLM Wiki" style suffixes
        title = re.sub(r'\s+[—–-].*$', '', title).strip()
        return title or stem
    return stem.replace("-", " ").replace("_", " ").title()


def _resolve_link(href: str, file_path: Path, wiki_root: Path) -> Optional[str]:
    """Resolve a relative .md href to a wiki slug, or None if outside wiki."""
    # Strip anchor fragments
    href = href.split('#')[0].strip()
    if not href:
        return None
    resolved = (file_path.parent / href).resolve()
    try:
        return _slug_from_path(wiki_root.resolve(), resolved)
    except ValueError:
        return None


def _extract_links(content: str, file_path: Path, wiki_root: Path) -> list[str]:
    """Extract all wiki-internal .md links from markdown content."""
    links: list[str] = []
    for m in re.finditer(r'\[.*?\]\(([^)]+)\)', content):
        href = m.group(1)
        if href.startswith('http') or href.startswith('//'):
            continue
        if not href.endswith('.md') and '.md#' not in href:
            continue
        slug = _resolve_link(href, file_path, wiki_root)
        if slug and slug not in links:
            links.append(slug)
    return links


def load_wiki_docs(wiki_path: Path) -> list[WikiDoc]:
    """Load all .md files from wiki_path, skipping hidden/underscore files."""
    if not wiki_path.exists():
        return []

    docs: list[WikiDoc] = []
    for md_file in sorted(wiki_path.rglob("*.md")):
        name = md_file.name
        # Skip private/system files
        if name.startswith('_') or name.startswith('.'):
            continue

        try:
            content = md_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        slug = _slug_from_path(wiki_path, md_file)
        title = _extract_title(content, md_file.stem)
        links = _extract_links(content, md_file, wiki_path)

        # Section: name of first path component if nested, else ""
        parts = slug.split("/")
        section = parts[0] if len(parts) > 1 else ""

        docs.append(WikiDoc(
            slug=slug,
            title=title,
            rel_path=str(md_file.relative_to(wiki_path)).replace("\\", "/"),
            section=section,
            links=links,
            raw_content=content,
        ))

    return docs


def doc_to_dict(doc: WikiDoc) -> dict:
    return {
        "slug": doc.slug,
        "title": doc.title,
        "rel_path": doc.rel_path,
        "section": doc.section,
        "links": doc.links,
        "raw_content": doc.raw_content,
    }
