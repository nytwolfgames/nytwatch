---
name: code-audit
description: "Detects recent source code changes (uncommitted + commits since last audit), analyses them against GDDs, code-wiki, and ADRs, then auto-applies updates. Source code is the source of truth — GDDs and wiki are updated to match the code."
argument-hint: "[--since <git-ref>] [--dry-run]"
user-invocable: true
allowed-tools: Read, Glob, Grep, Bash, Write, Edit, Task
---

# Code Audit

Source code is the **source of truth** in this project. GDDs and the code-wiki
are derived documentation — they are updated to reflect what the code actually
does, not the other way around.

This skill detects what changed, reads the affected documentation, re-derives
the correct content from the code, and writes the updates automatically.

ADRs are treated differently: they are architectural decisions. The skill checks
whether code confirms or contradicts each ADR decision, then records an
implementation note — it does NOT rewrite ADR decisions.

**Usage:**
- `/code-audit` — audit all changes since the last recorded audit SHA
- `/code-audit --since abc1234` — audit from a specific git ref
- `/code-audit --dry-run` — detect and analyse, but do not write any files

---

## Phase 1: Detect Changes

### 1a. Check for Queued Audit

Check if `planning/production/session-state/pending-code-audit.md` exists.

**If it exists** (automatic path — queued by hook):
- Note the list of changed files and SHAs from the file
- Proceed to Phase 2

**If it does not exist** (manual path):
- Read `.claude/last-audit-sha` if it exists; otherwise use `HEAD~10` as the base
- If `--since` was provided, use that as the base ref instead
- Run:
  ```bash
  # Committed changes since last audit
  git log <base-ref>..HEAD --oneline --name-only -- src/
  # Uncommitted changes
  git status --porcelain
  git diff HEAD --name-only -- src/
  ```
- Build the full list of changed files

### 1b. Dry-run notice

If `--dry-run` was passed, print:
> "DRY RUN — analysis will run but no files will be written."

---

## Phase 2: Filter to In-Scope Source Files

Read `.claude/source-paths.md`. Parse the `paths` table to get all in-scope
source roots (e.g. `src/`, `src/gameplay/`, etc.).

Filter the changed file list:
- **Keep**: files under any in-scope source root with source code extensions
  (`.gd`, `.cs`, `.cpp`, `.h`, `.ts`, `.js`, `.py`, `.rs`, `.go`, etc. — as
  configured for this project's engine/language)
- **Skip**: documentation files, wiki files, GDDs, ADRs, config files, assets
- **Skip**: files under `tests/` (test changes don't drive GDD updates)

If no in-scope source files remain after filtering, print:
> "No in-scope source file changes detected. Nothing to audit."
> Then exit.

Build a work list: each entry = `{ file, source_root, gdd_subfolder, wiki_subfolder }`.

---

## Phase 3: Map Files to Docs

For each in-scope file in the work list:

### 3a. Map to Wiki Sections

Read `planning/production/wiki/_index.md`. Scan the routing table for entries
matching the file path, class names found in the file, or system keywords.

Build a wiki target list: `{ wiki_file, anchor (if any) }`.

### 3b. Map to GDD Files

Using the `gdd_subfolder` from source-paths.md and keywords/class names from
the changed files:
- Search `planning/design/<gdd_subfolder>/` for GDD files whose names or
  content reference the same system
- A source file may map to multiple GDDs — include all matches
- Record: `{ gdd_file, sections_likely_affected }`

Sections to flag for potential update:
- **Detailed Rules** — behaviour described in code logic
- **Formulas** — constants, calculations, math in the code
- **Tuning Knobs** — configurable values, especially from data config files

Do NOT flag for update:
- Overview, Player Fantasy — these are design intent, not implementation facts
- Acceptance Criteria — these are test conditions, not implementation descriptions

### 3c. Map to ADRs

Read all files in `planning/docs/architecture/` matching `adr-*.md`.
For each ADR, check if its content references any of the changed systems
(by class name, file path, or system keyword). Collect relevant ADRs.

---

## Phase 4: Read Current State (Parallel)

Spawn three parallel Task agents to read current state:

**Agent A — Source Reader**: Read every in-scope changed source file in full.
For each file, extract:
- Class/module name, public methods, key constants, major logic blocks
- Any data config constants references with their values
- System dependencies (imports, subsystem references)

**Agent B — Doc Reader**: Read every mapped GDD, wiki section, and ADR.
For each doc, note the current content of the sections flagged in Phase 3.

**Agent C — Diff Reader**: For each in-scope file, run:
```bash
git diff <base-ref> -- <file>   # committed changes
git diff HEAD -- <file>          # uncommitted changes
```
Summarise: what was added, removed, or changed in each file.

Collect all three agents' findings before proceeding.

---

## Phase 5: Analyse Staleness

For each mapped system, compare Agent A + C findings (current code and diff)
against Agent B findings (current docs):

### GDD Staleness Check

For each GDD section flagged in Phase 3:

**Detailed Rules**: Is the behaviour described in the GDD still accurate?
Compare: what the code does vs. what the GDD says it does.
- If accurate → CURRENT (no update needed)
- If diverged → STALE (rewrite from code)
- If the code adds new behaviour not mentioned → INCOMPLETE (extend)

**Formulas**: Are the constants/calculations in the GDD correct?
Compare: actual values in code or data config vs. values stated in GDD.
- If values differ → STALE

**Tuning Knobs**: Do the configurable values listed match the code/config?
- If values or ranges differ → STALE

### Wiki Staleness Check

For each wiki target:
- Are class/module descriptions, method signatures, and behaviours accurate?
- Are new classes/modules present that aren't in the wiki?
- Are any documented classes removed or renamed?

### ADR Status Check

For each mapped ADR, evaluate:
- **IMPLEMENTED**: Code clearly implements the ADR decision as written
- **PARTIAL**: Some aspects implemented, others missing
- **CONTRADICTED**: Code pattern violates the ADR decision
- **UNRELATED**: Code change doesn't affect this ADR's scope → skip

---

## Phase 6: Auto-Apply GDD Updates

> Skip this phase if `--dry-run`.

For each GDD with one or more STALE or INCOMPLETE sections:

Read the full GDD. For each stale section, rewrite it to accurately describe
the current code behaviour. Rules:

- **Write from code facts**, not design preferences
  - "The system calculates X as Y" not "The system should calculate X as Y"
- **Preserve the section heading and structure** — only replace the body text
- **Formulas**: express using the exact variable names and constants from the code
- **Tuning Knobs**: list actual current values, not intended values
- **Do not touch**: Overview, Player Fantasy, Edge Cases, Dependencies,
  Acceptance Criteria — these sections are owned by design, not implementation
- Append to the bottom of the GDD:
  ```
  > **Last code-audit**: YYYY-MM-DD — sections updated: [list]
  ```

Use Edit for targeted section replacements. Use Write only if the GDD does not
exist yet (new system with no GDD — create a skeleton using the 8-section template
from CLAUDE.md, filling only the sections derivable from code).

---

## Phase 7: Auto-Apply Wiki Updates

> Skip this phase if `--dry-run`.

For each wiki target with stale or missing entries, apply updates using the
same rules as `code-wiki-update` Phase 4:

- New classes/modules/enums → add to appropriate table or section
- Changed method signatures or key behaviour → update descriptions
- New architectural patterns → add to `conventions.md`
- New gotchas or footguns discovered → add to `known-gotchas.md`
- Removed/renamed classes → update or remove entries

Format rules:
- Update `> **Last updated by**: code-audit` and the date at the top
- Preserve existing structure and heading style
- Use Edit (targeted replacement), not full Write, unless the entry is new

### Phase 7b: Detect & Fix Index Staleness

After all wiki updates, audit all three indices for staleness and missing links.

**For Code Wiki Index** (`planning/production/wiki/_index.md`):

1. **Detect stale entries**: For each file path in the index, verify the file exists:
   ```bash
   find planning/production/wiki -name "*.md"
   ```
   - If a file path in index doesn't exist → flag as STALE
   - Move stale entries to a new "## Deprecated Pages" section at the end

2. **Detect missing entries**: For each .md file that exists on disk:
   - If a file is not referenced in the index → flag as MISSING
   - Add new entries to the appropriate section
   - Use best-guess keywords from the file's ## Overview section

3. **Fix broken links**: For each file reference in routing table entries:
   - Verify path is correct
   - If path is wrong → find correct file and update

**For GDD Systems Index** (`planning/design/gdd/systems-index.md`):

1. **Detect stale entries**: For each GDD file path in the systems table:
   - If a GDD path in index doesn't exist → flag as STALE
   - Move stale rows to "## Deprecated Systems" section

2. **Detect missing entries**: For each .md file that exists on disk:
   - If a GDD file is not in the systems table → flag as MISSING
   - Add new system row with best-guess Layer

3. **Verify Design Order**: Scan "## Design Order" priority list for stale or missing entries

**For Narrative Wiki Index** (`planning/design/narrative/wiki/_index.md`):

Same pattern as Code Wiki Index (stale detection, missing link detection, broken link fixing).

### Phase 7c: Apply Index Fixes

Use Edit tool to:
- Move stale entries to deprecated sections
- Add missing entries to the appropriate sections
- Update broken file paths

---

## Phase 8: Update Sprint Tasks

> Skip this phase if `--dry-run`.

### 8a. Find the Active Sprint

Walk `planning/production/sprints/sprint-*.md` in order, find the first file
with `**Status**: open`.

If no active sprint is found, skip this phase.

Read the full active sprint file.

### 8b. Build a Task Match Index

For each unchecked (`[ ]`) or in-progress (`[in_progress]`) task line:

Extract match signals:
- **File references**: any `FileName.[ext]` or `FileName.[ext]:NNN` pattern
- **Class names**: any class/module name pattern
- **Task ID**: any `TASK-NN` or `ADR-NNNN` reference

### 8c. Match Changed Files to Tasks

For each changed source file, scan the task index for tasks whose signals overlap with:
- The filename
- Class names defined in the file
- TASK-ID or ADR-ID referenced in the diff/commit message

### 8d. Classify Each Matched Task

**DONE**: The change clearly resolves what the task describes.
**IN_PROGRESS**: The change is related and makes progress but is incomplete.
**TOUCHED**: The file was changed but the change is unrelated → do not update.

Bias toward **IN_PROGRESS** over **DONE** when in doubt.

### 8e. Apply Sprint Updates

For each task classified as DONE:
- Replace `- [ ]` with `- [done]` on that line

For each task classified as IN_PROGRESS (and currently `[ ]`):
- Replace `- [ ]` with `- [in_progress]` on that line

Append to the sprint file:
```markdown
## Code Audit Log

| Date | Files Changed | Tasks Updated |
|------|---------------|---------------|
| YYYY-MM-DD | [file list] | [task descriptions] |
```

---

## Phase 9: Record ADR Implementation Status

> Skip this phase if `--dry-run`.

For each ADR with a status of IMPLEMENTED, PARTIAL, or CONTRADICTED:

Add or update an `## Implementation Status` section at the end of the ADR file:

```markdown
## Implementation Status

**Status**: Implemented | Partial | Contradicted
**Last checked**: YYYY-MM-DD (code-audit)
**Evidence**: [brief description of where/how the code implements or contradicts the decision]
**Files**: [list of source files that are relevant]
```

Update the ADR's frontmatter `Status` field:
- `[done]` sprint task → Change `Status: Proposed` → `Status: Accepted`
- `[in_progress]` sprint task → Change `Status: Proposed` → `Status: Accepted`
- **CONTRADICTED** → Keep Status as-is (requires manual architectural review)

For CONTRADICTED ADRs, print a warning:
> "⚠ ADR-NNNN contradicted by code — review required."

---

## Phase 10: Finalize

> Skip SHA update and file deletion if `--dry-run`.

1. Write the current HEAD SHA to `.claude/last-audit-sha`:
   ```bash
   git rev-parse HEAD > .claude/last-audit-sha
   ```

2. If `planning/production/session-state/pending-code-audit.md` exists, delete it:
   ```bash
   rm planning/production/session-state/pending-code-audit.md
   ```

---

## Phase 11: Summary Report

Print a structured summary:

```
## Code Audit Summary
Date: [today]
Base ref: [SHA or ref used]
Source files analysed: [N]

### GDDs Updated ([count])
- [gdd-file.md] — sections updated: [list]

### GDDs Current (no update needed) ([count])
- [gdd-file.md]

### Wiki Updates ([count])
- [wiki-file.md] — [what changed]

### Index Health
- Code Wiki Index: [N] stale, [N] missing, [N] broken links fixed
- GDD Systems Index: [N] stale, [N] missing
- Narrative Wiki Index: [N] stale, [N] missing

### Sprint Task Updates ([count updated])
Sprint: [sprint-NN.md]
- [done] Task description
- [in_progress] Task description

### ADR Implementation Status ([count checked])
- ADR-NNNN ([title]): Implemented ✓
- ADR-NNNN ([title]): Partial — [what's missing]
- ADR-NNNN ([title]): ⚠ CONTRADICTED — [detail]

### Skipped Files
- [file] — [reason: external / test file / no doc mapping]

### Follow-up Actions Required
[Any CONTRADICTED ADRs, missing GDDs for large systems, wiki files over 200 lines, etc.]
```

If `--dry-run`: append
> "DRY RUN complete — no files were written. Remove --dry-run to apply."
