from __future__ import annotations

import logging
import os
import re
from collections import defaultdict
from pathlib import Path
from typing import Optional

from auditor.config import SystemDef
from auditor.paths import normalize_path

log = logging.getLogger(__name__)

MAX_FILE_SIZE = 500 * 1024  # 500 KB
MAX_TOKENS = 35_000          # Hard ceiling per chunk (leaves room for prompt + output)
_INCLUDE_RE = re.compile(r'^\s*#include\s+"([^"]+)"', re.MULTILINE)


# ---------------------------------------------------------------------------
# File collection
# ---------------------------------------------------------------------------

def collect_system_files(
    repo_path: str,
    system: SystemDef,
    extensions: list[str],
) -> dict[str, str]:
    repo = Path(repo_path)
    collected: dict[str, str] = {}

    for sys_path in system.paths:
        full_path = repo / sys_path
        if not full_path.exists():
            log.warning("System path does not exist: %s", full_path)
            continue

        for root, _dirs, files in os.walk(full_path):
            for fname in files:
                if not any(fname.endswith(ext) for ext in extensions):
                    continue
                fpath = Path(root) / fname
                if fpath.stat().st_size > MAX_FILE_SIZE:
                    log.debug("Skipping large file: %s (%d bytes)", fpath, fpath.stat().st_size)
                    continue
                rel = normalize_path(str(fpath.relative_to(repo)))
                try:
                    collected[rel] = fpath.read_text(errors="replace")
                except OSError as exc:
                    log.warning("Could not read %s: %s", fpath, exc)

    log.info("Collected %d files for system '%s'", len(collected), system.name)
    return collected


def list_system_files(
    repo_path: str,
    system: SystemDef,
    extensions: list[str],
) -> list[str]:
    """Return repo-relative paths of all files in a system without loading content.

    Used for full scans where we only need paths (agent mode reads the files itself).
    """
    repo = Path(repo_path)
    paths: list[str] = []

    for sys_path in system.paths:
        full_path = repo / sys_path
        if not full_path.exists():
            log.warning("System path does not exist: %s", full_path)
            continue
        for root, _dirs, files in os.walk(full_path):
            for fname in files:
                if not any(fname.endswith(ext) for ext in extensions):
                    continue
                fpath = Path(root) / fname
                if fpath.stat().st_size > MAX_FILE_SIZE:
                    continue
                paths.append(normalize_path(str(fpath.relative_to(repo))))

    log.info("Listed %d files for system '%s'", len(paths), system.name)
    return paths


def chunk_paths_by_count(
    file_paths: list[str],
    max_files: int = 20,
) -> list[list[str]]:
    """Split a list of file paths into chunks of at most max_files each."""
    return [file_paths[i:i + max_files] for i in range(0, len(file_paths), max_files)]


def collect_specific_files(
    repo_path: str,
    file_paths: list[str],
    extensions: list[str],
) -> dict[str, str]:
    """Read a specific list of repo-relative file paths instead of walking directories."""
    repo = Path(repo_path)
    collected: dict[str, str] = {}

    for rel_path in file_paths:
        if not any(rel_path.endswith(ext) for ext in extensions):
            continue
        fpath = repo / rel_path
        if not fpath.exists():
            log.warning("Changed file not found on disk: %s", fpath)
            continue
        if fpath.stat().st_size > MAX_FILE_SIZE:
            log.debug("Skipping large file: %s (%d bytes)", fpath, fpath.stat().st_size)
            continue
        norm = normalize_path(rel_path)
        try:
            collected[norm] = fpath.read_text(errors="replace")
        except OSError as exc:
            log.warning("Could not read %s: %s", fpath, exc)

    log.info("Collected %d/%d specific files", len(collected), len(file_paths))
    return collected


# ---------------------------------------------------------------------------
# Token estimation
# ---------------------------------------------------------------------------

def estimate_tokens(text: str) -> int:
    # C++ is token-dense (templates, macros, symbols) — use 3.0 chars/token
    # rather than the natural-language default of ~4, to avoid underestimating.
    return int(len(text) / 3.0)


# ---------------------------------------------------------------------------
# Include resolution
# ---------------------------------------------------------------------------

def _parse_includes(content: str) -> list[str]:
    """Return all quoted #include paths found in content."""
    return [m.group(1) for m in _INCLUDE_RE.finditer(content)]


def _resolve_include(include_path: str, repo_path: str, known_files: set[str]) -> Optional[str]:
    """
    Resolve a raw #include path to a normalised repo-relative path that exists
    in known_files, or None if not found.

    Tries:
      1. Direct match (include_path is already relative to repo root)
      2. Basename match — finds the first file in known_files whose filename
         matches (handles UE-style includes like "MyClass.h" without a path prefix)
    """
    repo = Path(repo_path)
    # Try 1: direct path relative to repo root
    norm = normalize_path(include_path)
    if norm in known_files:
        return norm
    candidate = repo / include_path
    if candidate.exists():
        norm = normalize_path(str(candidate.relative_to(repo)))
        if norm in known_files:
            return norm

    # Try 2: match by filename only
    basename = Path(include_path).name.lower()
    for known in known_files:
        if Path(known).name.lower() == basename:
            return known

    return None


def build_include_graph(
    file_contents: dict[str, str],
    repo_path: str,
) -> dict[str, set[str]]:
    """
    Build a dependency graph: file -> set of files it #includes (within the
    known file set only).

    Returns adjacency list keyed by normalised repo-relative path.
    """
    known = set(file_contents.keys())
    graph: dict[str, set[str]] = {f: set() for f in known}

    for path, content in file_contents.items():
        for raw in _parse_includes(content):
            resolved = _resolve_include(raw, repo_path, known)
            if resolved and resolved != path:
                graph[path].add(resolved)

    return graph


# ---------------------------------------------------------------------------
# Semantic neighbourhood (for incremental scans)
# ---------------------------------------------------------------------------

def build_neighbourhood(
    changed_files: list[str],
    all_files: dict[str, str],
    repo_path: str,
    context_budget: int = MAX_TOKENS,
) -> dict[str, str]:
    """
    Build a context neighbourhood around the changed files:
      - ALL changed files are always included (they must be analysed)
      - Headers they #include and .cpp files that depend on them are added
        until the extra context budget is exhausted

    The result may exceed one chunk — callers should pass it through
    chunk_paths_by_count() for splitting.
    """
    known = set(all_files.keys())
    changed_set = set(normalize_path(f) for f in changed_files if normalize_path(f) in known)

    if not changed_set:
        return {}

    # Start with ALL changed files — no token cap, they are the primary focus
    neighbourhood: dict[str, str] = {p: all_files[p] for p in changed_set}
    changed_tokens = sum(estimate_tokens(c) for c in neighbourhood.values())

    # Forward: headers included by the changed files, ranked by reference count
    forward_headers: dict[str, int] = defaultdict(int)
    for path in changed_set:
        for raw in _parse_includes(all_files.get(path, "")):
            resolved = _resolve_include(raw, repo_path, known)
            if resolved and resolved.endswith(".h") and resolved not in changed_set:
                forward_headers[resolved] += 1

    # Reverse: .cpp files that directly include any changed file
    reverse_deps: set[str] = set()
    for path, content in all_files.items():
        if path in changed_set or not path.endswith(".cpp"):
            continue
        for raw in _parse_includes(content):
            if _resolve_include(raw, repo_path, known) in changed_set:
                reverse_deps.add(path)
                break

    # Fill context budget with extra files (reverse deps first, then headers)
    context_tokens = 0
    for path in sorted(reverse_deps):
        t = estimate_tokens(all_files[path])
        if context_tokens + t <= context_budget:
            neighbourhood[path] = all_files[path]
            context_tokens += t

    for header in sorted(forward_headers, key=lambda h: -forward_headers[h]):
        if header in neighbourhood:
            continue
        t = estimate_tokens(all_files[header])
        if context_tokens + t <= context_budget:
            neighbourhood[header] = all_files[header]
            context_tokens += t

    total_tokens = changed_tokens + context_tokens
    log.info(
        "Neighbourhood: %d changed files (%d tokens) + %d context files (%d tokens) = %d total",
        len(changed_set), changed_tokens,
        len(neighbourhood) - len(changed_set), context_tokens,
        total_tokens,
    )
    return neighbourhood


# ---------------------------------------------------------------------------
# Semantic clustering (for full scans)
# ---------------------------------------------------------------------------

def _connected_components(graph: dict[str, set[str]]) -> list[set[str]]:
    """
    Find connected components in an undirected version of the include graph.
    Files that include each other (directly or transitively) end up in the
    same component.
    """
    visited: set[str] = set()
    components: list[set[str]] = []

    # Build undirected adjacency
    undirected: dict[str, set[str]] = defaultdict(set)
    for node, neighbours in graph.items():
        for nb in neighbours:
            undirected[node].add(nb)
            undirected[nb].add(node)

    for start in graph:
        if start in visited:
            continue
        component: set[str] = set()
        stack = [start]
        while stack:
            node = stack.pop()
            if node in visited:
                continue
            visited.add(node)
            component.add(node)
            stack.extend(undirected[node] - visited)
        components.append(component)

    return components


def _split_by_tokens(
    files: dict[str, str],
    max_tokens: int,
) -> list[dict[str, str]]:
    """
    Fallback splitter: when a semantic cluster is too large, split it by token
    count while keeping headers together with the first chunk they fit in.
    """
    headers = {p: c for p, c in files.items() if p.endswith(".h")}
    cpps = {p: c for p, c in files.items() if not p.endswith(".h")}

    # Separate headers used by which .cpp (resolved within this cluster only)
    chunks: list[dict[str, str]] = []
    current: dict[str, str] = {}
    current_tokens = 0

    for path, content in cpps.items():
        file_tokens = estimate_tokens(content)
        # Headers directly included by this .cpp that fit in scope
        needed_headers = {}
        for h_path, h_content in headers.items():
            if Path(h_path).name in content or h_path in content:
                needed_headers[h_path] = h_content

        needed_tokens = sum(estimate_tokens(c) for c in needed_headers.values())
        total_needed = file_tokens + needed_tokens

        if current and current_tokens + total_needed > max_tokens:
            chunks.append(current)
            current = {}
            current_tokens = 0

        current[path] = content
        current_tokens += file_tokens
        for h, hc in needed_headers.items():
            if h not in current:
                current[h] = hc
                current_tokens += estimate_tokens(hc)

    if current:
        chunks.append(current)

    # If no cpps (header-only cluster), send as one chunk
    if not chunks and headers:
        chunks.append(headers)

    return chunks


def chunk_system(
    file_contents: dict[str, str],
    repo_path: str = "",
    max_tokens: int = MAX_TOKENS,
) -> list[dict[str, str]]:
    """
    Semantically chunk a file set for a full scan:

    1. Build an include graph across all files
    2. Find connected components (semantically related clusters)
    3. For each cluster that fits within max_tokens: emit as one chunk
       (with only headers included by that cluster's .cpp files)
    4. For oversized clusters: fall back to token-count splitting
    """
    if not file_contents:
        return []

    total_tokens = sum(estimate_tokens(c) for c in file_contents.values())
    if total_tokens <= max_tokens:
        log.info("Files fit in one chunk (%d tokens)", total_tokens)
        return [file_contents]

    graph = build_include_graph(file_contents, repo_path) if repo_path else {}
    components = _connected_components(graph) if graph else [set(file_contents.keys())]

    chunks: list[dict[str, str]] = []

    for component in sorted(components, key=lambda c: -len(c)):
        cluster = {p: file_contents[p] for p in component if p in file_contents}
        if not cluster:
            continue

        # Filter headers: only keep those included by a .cpp in this cluster
        cpps_in_cluster = {p for p in cluster if not p.endswith(".h")}
        needed_headers: set[str] = set()
        for cpp_path in cpps_in_cluster:
            for raw in _parse_includes(cluster.get(cpp_path, "")):
                resolved = _resolve_include(raw, repo_path, set(cluster.keys()))
                if resolved and resolved.endswith(".h"):
                    needed_headers.add(resolved)

        # Build filtered cluster: all .cpp files + only needed headers
        filtered = {p: c for p, c in cluster.items() if not p.endswith(".h")}
        for h in needed_headers:
            if h in cluster:
                filtered[h] = cluster[h]

        cluster_tokens = sum(estimate_tokens(c) for c in filtered.values())

        if cluster_tokens <= max_tokens:
            chunks.append(filtered)
        else:
            # Too large — fall back to token-count splitting within this cluster
            sub_chunks = _split_by_tokens(filtered, max_tokens)
            chunks.extend(sub_chunks)
            log.info(
                "Oversized cluster (%d tokens, %d files) split into %d sub-chunks",
                cluster_tokens, len(filtered), len(sub_chunks),
            )

    log.info(
        "Semantic chunking: %d files → %d chunks (was %d tokens total)",
        len(file_contents), len(chunks), total_tokens,
    )
    return chunks


# ---------------------------------------------------------------------------
# Legacy resolve_includes (kept for compatibility)
# ---------------------------------------------------------------------------

def resolve_includes(file_content: str, repo_path: str) -> list[str]:
    repo = Path(repo_path)
    found: list[str] = []
    for match in _INCLUDE_RE.finditer(file_content):
        include_path = match.group(1)
        candidate = repo / include_path
        if candidate.exists():
            found.append(str(candidate.relative_to(repo)))
    return found
