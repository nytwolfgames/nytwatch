from __future__ import annotations

import logging
import os
import re
from pathlib import Path

from auditor.config import SystemDef
from auditor.paths import normalize_path

log = logging.getLogger(__name__)

MAX_FILE_SIZE = 500 * 1024  # 500 KB


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


def estimate_tokens(text: str) -> int:
    return int(len(text) / 3.5)


def chunk_system(
    file_contents: dict[str, str],
    max_tokens: int = 120_000,
) -> list[dict[str, str]]:
    if not file_contents:
        return []

    header_files: dict[str, str] = {}
    cpp_files: dict[str, str] = {}

    for path, content in file_contents.items():
        if path.endswith(".h"):
            header_files[path] = content
        else:
            cpp_files[path] = content

    header_tokens = sum(estimate_tokens(c) for c in header_files.values())
    total_tokens = header_tokens + sum(estimate_tokens(c) for c in cpp_files.values())

    if total_tokens <= max_tokens:
        return [file_contents]

    budget_per_chunk = max_tokens - header_tokens
    if budget_per_chunk <= 0:
        log.warning(
            "Header files alone exceed max_tokens (%d > %d). "
            "Returning single chunk anyway.",
            header_tokens,
            max_tokens,
        )
        return [file_contents]

    chunks: list[dict[str, str]] = []
    current_chunk: dict[str, str] = dict(header_files)
    current_tokens = header_tokens

    for path, content in cpp_files.items():
        file_tokens = estimate_tokens(content)
        if current_tokens + file_tokens > max_tokens and current_chunk != header_files:
            chunks.append(current_chunk)
            current_chunk = dict(header_files)
            current_tokens = header_tokens

        current_chunk[path] = content
        current_tokens += file_tokens

    if len(current_chunk) > len(header_files) or not chunks:
        chunks.append(current_chunk)

    log.info("Split %d files into %d chunks", len(file_contents), len(chunks))
    return chunks


_INCLUDE_RE = re.compile(r'^\s*#include\s+"([^"]+)"', re.MULTILINE)


def resolve_includes(file_content: str, repo_path: str) -> list[str]:
    repo = Path(repo_path)
    found: list[str] = []

    for match in _INCLUDE_RE.finditer(file_content):
        include_path = match.group(1)
        candidate = repo / include_path
        if candidate.exists():
            found.append(str(candidate.relative_to(repo)))

    return found
