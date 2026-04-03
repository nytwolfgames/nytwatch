from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Optional

from auditor.database import Database
from auditor.paths import normalize_path

log = logging.getLogger(__name__)


def detect_source_dirs(repo_path: str, db: Database) -> None:
    """Classify repo directories as 'project' or 'plugin' using two layers.

    Layer 1: Deterministic UE heuristics (no API call).
    Layer 2: Claude AI fallback for ambiguous directories.

    Existing classifications in the DB are never overwritten (preserving
    user overrides from the Settings UI).
    """
    repo = Path(repo_path)
    if not repo.exists():
        log.warning("Repo path does not exist: %s", repo_path)
        return

    classified, unclassified = _heuristic_classify(repo)

    # Persist heuristic results (skip already-classified dirs)
    for dir_path, source_type in classified.items():
        if not db.has_source_dir(dir_path):
            db.upsert_source_dir(dir_path, source_type)
            log.info("Auto-classified '%s' as '%s' (heuristic)", dir_path, source_type)

    # Filter out already-classified dirs before sending to Claude
    unclassified = [d for d in unclassified if not db.has_source_dir(d)]

    if unclassified:
        ai_results = _ai_classify(repo, unclassified)
        for dir_path, source_type in ai_results.items():
            if not db.has_source_dir(dir_path):
                db.upsert_source_dir(dir_path, source_type)
                log.info("Auto-classified '%s' as '%s' (AI)", dir_path, source_type)


def _heuristic_classify(repo: Path) -> tuple[dict[str, str], list[str]]:
    """Apply deterministic UE heuristics to classify directories.

    Returns (classified, unclassified) where classified is {path: source_type}
    and unclassified is a list of paths that couldn't be determined.
    """
    classified: dict[str, str] = {}
    unclassified: list[str] = []

    # Find .uproject file to determine project name
    uproject_files = list(repo.glob("*.uproject"))
    project_name = uproject_files[0].stem if uproject_files else None

    # Scan top-level directories for Source/ and Plugins/
    source_dir = repo / "Source"
    plugins_dir = repo / "Plugins"

    # Rule 1: Everything under Plugins/ that has a .uplugin is a plugin
    if plugins_dir.exists():
        for item in plugins_dir.iterdir():
            if item.is_dir():
                rel = normalize_path(str(item.relative_to(repo)))
                uplugin_files = list(item.glob("*.uplugin"))
                if uplugin_files:
                    classified[rel] = "plugin"
                else:
                    # Also check nested dirs (some plugins nest one level deeper)
                    nested_uplugin = list(item.rglob("*.uplugin"))
                    if nested_uplugin:
                        classified[rel] = "plugin"
                    else:
                        # Under Plugins/ but no .uplugin — still likely a plugin
                        classified[rel] = "plugin"

    # Rule 2: Directories under Source/ matching the project name are project code
    if source_dir.exists():
        for item in source_dir.iterdir():
            if item.is_dir():
                rel = normalize_path(str(item.relative_to(repo)))
                if project_name and item.name == project_name:
                    classified[rel] = "project"
                elif project_name and item.name.startswith(project_name):
                    classified[rel] = "project"
                elif item.name in ("ThirdParty", "ThirdPartyLibs"):
                    classified[rel] = "plugin"
                elif rel not in classified:
                    # Source/ subdirs that don't match project name — ambiguous
                    unclassified.append(rel)

    # Rule 3: Check for .uplugin files anywhere else (in-project plugins)
    for uplugin in repo.rglob("*.uplugin"):
        plugin_dir = uplugin.parent
        rel = normalize_path(str(plugin_dir.relative_to(repo)))
        if rel not in classified:
            classified[rel] = "plugin"
            if rel in unclassified:
                unclassified.remove(rel)

    # Rule 4: Classify remaining top-level dirs as ignored if they have no C++ code
    _ue_generated = {".git", "Intermediate", "Saved", "Binaries", "DerivedDataCache", ".vs", ".idea"}
    for item in repo.iterdir():
        if not item.is_dir() or item.name in _ue_generated or item.name.startswith("."):
            continue
        rel = str(item.relative_to(repo))
        # Skip if already classified (Source/DragonRacer, Plugins/X, etc.)
        already_covered = any(
            c == rel or c.startswith(rel + "/") or rel.startswith(c + "/")
            for c in classified
        )
        if already_covered or rel in unclassified:
            continue
        # Check for any .h/.cpp files
        has_code = any(item.rglob("*.h")) or any(item.rglob("*.cpp"))
        if has_code:
            unclassified.append(rel)
        else:
            classified[rel] = "ignored"

    return classified, unclassified


def _ai_classify(repo: Path, dirs: list[str]) -> dict[str, str]:
    """Use Claude to classify ambiguous directories."""
    if not dirs:
        return {}

    # Build a lightweight listing for each directory
    dir_listings: dict[str, list[str]] = {}
    for d in dirs:
        full = repo / d
        if full.exists():
            try:
                entries = sorted(os.listdir(full))[:30]  # cap at 30 entries
                dir_listings[d] = entries
            except OSError:
                dir_listings[d] = []
        else:
            dir_listings[d] = []

    prompt = _build_classify_prompt(dir_listings)

    try:
        from auditor.analysis.engine import call_claude, _extract_json
        raw = call_claude(prompt, fast=True, timeout=60)
        data = _extract_json(raw)

        results: dict[str, str] = {}
        classifications = data.get("classifications", data)
        if isinstance(classifications, dict):
            for path, source_type in classifications.items():
                if source_type in ("project", "plugin"):
                    results[path] = source_type
                else:
                    results[path] = "project"
        return results

    except Exception:
        log.exception("AI classification failed, defaulting ambiguous dirs to 'project'")
        return {d: "project" for d in dirs}


def _build_classify_prompt(dir_listings: dict[str, list[str]]) -> str:
    listing_text = json.dumps(dir_listings, indent=2)

    return f"""\
You are analyzing an Unreal Engine project's directory structure. For each directory below, classify it as either "project" (first-party game code) or "plugin" (third-party or reusable plugin code).

Consider these signals:
- Directories with plugin-like naming (e.g., vendor names, generic utility names) are likely plugins
- Directories matching game-specific names (e.g., game modes, character systems) are likely project code
- Directories containing typical plugin structures (Public/Private with generic module names) may be plugins
- Game-specific modules under Source/ are usually project code

## Directories to classify

{listing_text}

## Output Format

Return a JSON object with this exact structure:
```json
{{
  "classifications": {{
    "<directory_path>": "project" or "plugin",
    ...
  }}
}}
```

Return ONLY the JSON object. No markdown fences, no commentary.\
"""
