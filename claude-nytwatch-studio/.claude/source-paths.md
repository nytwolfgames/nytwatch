# Source Paths

Directories surveyed for wiki init, wiki updates, and GDD coverage checks.
One path per line inside the fenced block. Paths are relative to the project root.
Lines starting with `#` inside the block are comments and are ignored.

Add any source root that contains application code the wiki should document.
Do NOT add third-party package directories (node_modules, .venv, site-packages, etc.).

```paths
src/
```

## Usage

- **wiki-init** reads these paths in Phase 1 to discover source files for wiki content.
- **wiki-update** uses these paths in Phase 2 to determine whether an unmatched changed
  file is in-scope (new code that deserves wiki coverage) or external (skip silently).
- **wiki-hook.sh** annotates changed files as `[in-scope]` or `[external]` in the
  pending update, so wiki-update can process the queue efficiently.
