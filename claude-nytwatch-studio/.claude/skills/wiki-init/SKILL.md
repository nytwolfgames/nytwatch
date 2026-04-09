---
name: wiki-init
description: "Initializes or rebuilds the LLM wiki from scratch. Discovery-based and read-pattern-driven: auto-detects project source structure and design docs, then creates one focused wiki file per read context. Use when setting up the wiki for the first time, after major refactors, or after significant new features."
argument-hint: "[optional: subsystem slug to re-init only, e.g. subsystems/auth]"
user-invocable: true
allowed-tools: Read, Glob, Grep, Bash, Write, Edit
---

## Purpose

Build or rebuild `planning/production/wiki/` as a **Karpathy-style LLM wiki**:
one focused, dense file per **read context** — small enough for an LLM to read
cheaply and get exactly what it needs before touching a specific area of code.

Reference: https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f

**The atomic unit is a read context, not a source module or design document.**
A read context is the complete set of knowledge an LLM needs before safely
editing a specific area. Sometimes that maps to one module. Sometimes it merges
two tightly-coupled modules. Sometimes it extracts a cross-cutting concept
(e.g. an interface contract, an event bus) into its own entry.

If an argument is given, re-init only that subsystem file and update `_index.md`.
Otherwise, rebuild all sections from scratch.

---

## Phase 1: Discover Project Structure

Run these discovery steps to understand what the project contains before reading anything:

1. **Read source path config**: Read `.claude/source-paths.md`. Extract the list of
   source roots from the fenced `paths` block (ignore lines starting with `#`).
   These are the directories to survey for source files. If the file is missing,
   fall back to auto-detecting common roots: `src/`, `lib/`, `app/`, `Source/`,
   `packages/`.

2. **Detect primary language(s)**: Count files by extension across the configured
   source roots. The most common extension is the primary language.

3. **Find design documents**: Glob for any of:
   - `planning/design/**/*.md`
   - `planning/design/gdd/*.md`
   - `docs/design/**/*.md`
   - `design/**/*.md`
   - `*.design.md`
   Record every file found — these are the authoritative source of truth.

4. **Find source root(s)**: For each path in `.claude/source-paths.md`, glob for
   source files matching the detected language (`**/*.py`, `**/*.ts`, `**/*.h`, etc.).
   List the top-level directories to understand the module structure.

4. **Find sprint and milestone files**:
   - `planning/production/sprints/*.md`
   - `planning/production/milestones/*.md`
   Mine these for P0/P1 bugs, decisions, and gotchas.

5. **Find ADRs**: `planning/docs/architecture/**/*.md` or `docs/adr/*.md`

6. **List existing wiki files** to preserve any human-added gotchas or notes.

---

## Phase 2: Read Source Material

Read every design document found in Phase 1 in full.

Then read source files. Prioritise by subsystem: read entry points, core interfaces,
and data models before implementation details. For each language:

- **Python**: read `*.py` files — prioritise modules that define classes, routes,
  data models, or interfaces; skip test files and migrations for now
- **C++**: read `.h` header files — class/struct/enum definitions are in headers
- **TypeScript/JavaScript**: read `.ts`/`.js` files — prioritise interfaces, types,
  services, and controllers
- **Go/Rust**: read the main module files and interface definitions

Limit to ~50 source files per pass. Prioritise files that are imported/referenced
most frequently — they are the core of the system.

Read all sprint files for P0/P1 bugs, hard-won decisions, and footgun warnings.

---

## Phase 3: Preserve Existing Gotchas

If wiki files already exist, read `known-gotchas.md` and each subsystem file.
Extract any content that cannot be re-derived from source alone — sprint discoveries,
trap warnings, human-added edge case notes. Carry this content forward.

---

## Phase 4: Decompose Into Read Contexts

### Decomposition Rules

Apply these rules before deciding on the final file list:

**Merge rule — always co-read → one file**
If touching module A always requires knowing module B (they are tightly coupled
and never modified independently), merge them into one file.
Example: a data model and its serializer that are always changed together.

**Extract rule — 3+ cross-system references → own file**
If a concept appears as a dependency in 3 or more other wiki entries, extract it
as a standalone file with a stable link target others can reference.
Examples: an interface contract, an event bus, an auth middleware layer, a shared
data schema — these are cross-cutting and justify their own entry.

**Split rule — different edit contexts → separate files**
If a design document or module covers two areas that developers touch independently,
split them into separate files.
Example: a module covering both "data ingestion" and "report generation" — a
developer fixing ingestion never needs to read about report generation.

**Size rule — hard cap**
Target 100–150 lines per file. A file that would exceed 200 lines must be split.
If a concept is too small for its own file (< 20 meaningful lines), fold it into
the closest related file and add a routing entry in `_index.md`.

### Canonical Structure

Always create these top-level files:

- `architecture.md` — entry points, module map, key classes/interfaces, system
  boundaries. Keep this to the core framework only — subsystems go in their own files.
- `conventions.md` — naming, patterns, framework idioms, code style decisions,
  the project's specific opinions on how things are done
- `decisions.md` — one compact entry per ADR or major architectural decision:
  what was decided, why, and what was rejected
- `known-gotchas.md` — traps, null-ref risks, hardcoded values, footguns,
  P0/P1 bugs from sprints. Each entry: what the trap is, how to trigger it,
  how to avoid it.

Then create subsystem files. Derive the list from what was actually discovered —
do not create files for systems that have no source evidence. For each major
module, feature area, or domain found in Phase 2, create:

- `subsystems/<name>.md` or `subsystems/<domain>/<name>.md`

For cross-cutting concepts that qualify under the extract rule:
- `subsystems/<concept>.md` (e.g. `subsystems/auth.md`, `subsystems/event-bus.md`,
  `subsystems/data-schema.md`)

---

## Phase 5: Content Rules Per File

Every wiki file must be:

- **Focused**: covers exactly one read context. No cross-system sprawl.
- **Dense**: every line earns its place. No padding, no generic boilerplate.
- **Grounded**: only document classes, functions, and behaviours evidenced in source
  or design docs. Do not invent.
- **Actionable**: a reader should be able to make correct edits after reading this
  file alone.
- **Compact**: 100–150 lines. Hard cap at 200 — split if exceeded.

### Required header

```markdown
# [Subsystem Name] — LLM Wiki

> **Last updated by**: wiki-init  
> **Date**: [today]  
> **Source**: [list design docs and/or source files used]
```

### Required footer

```markdown
## See Also
- [link to file](relative/path.md) — one-line reason to read it
```

Link to the 1–3 most related wiki files an editor of this system would also need.
Omit if there are no meaningful cross-references.

### Named anchors

For any major sub-concept within a file that is referenced by 2+ other files,
add a named anchor so `_index.md` can link directly to it:
`## Concept Name {#anchor-slug}`

### Content to include per file

1. **One-line role** of this read context
2. **Key classes / functions / endpoints** — location, role, important fields/params
3. **Critical flows** — the main execution paths an editor must understand
4. **Key types / enums / schemas** defined in this domain
5. **Active vs. stub/incomplete status** — flag anything not yet wired or commented out
6. **Integration points** — what this system calls and what calls it
7. **Known constraints** — hard limits, magic values, known bugs from P0/P1 items

---

## Phase 6: Write All Files

Write each file with the Write tool. Create subdirectories under
`planning/production/wiki/` as needed.

Skip a file entirely (do not write a placeholder) if it would contain only stub
content — note it in the Phase 10 summary instead.

---

## Phase 7: Write `_index.md`

Build a comprehensive routing table. Every major class name, function name, file
path pattern, and domain keyword should have a row pointing to the correct wiki
file. Multiple rows may point to the same file. Rows may link to named anchors
within a file (e.g. `subsystems/auth.md#token-refresh`).

```markdown
# LLM Wiki — Routing Index

> **Last updated by**: wiki-init  
> **Date**: [today]

Read this file first. Find the class, function, or keyword you are about to
touch. Follow the link to the correct wiki section before editing.

| Pattern / Class / Keyword | Wiki Section |
|---|---|
...

## Source Directories

| Directory | Contents |
|---|---|
...
```

---

## Phase 8: Verify CLAUDE.md Is Not Stale

Read `CLAUDE.md`. Find the `## LLM Wiki` section.

The section should contain **only** the three always-read files and the two run
instructions — it must NOT contain a per-subsystem routing table (that lives in
`_index.md` only).

The correct minimal form is:

```markdown
## LLM Wiki

The project maintains a living knowledge base in `planning/production/wiki/`.
Before touching any known subsystem, look up the class or keyword in
`planning/production/wiki/_index.md` and read the linked section.

These files apply to every task — read them proactively:

- `planning/production/wiki/conventions.md` — naming, patterns, framework idioms
- `planning/production/wiki/decisions.md` — ADRs; check before proposing changes
- `planning/production/wiki/known-gotchas.md` — traps, risks, hardcoded values

**Wiki is queued automatically**: The Stop hook writes a pending update at session end.
Run `/wiki-update` to apply the queued changes after any significant session.
Run `/wiki-init` to rebuild the wiki from scratch if it becomes stale.
```

If `CLAUDE.md` has no `## LLM Wiki` section at all, add the above.
If it exists but has a stale routing table, replace the table with the minimal form.
Use the Edit tool for targeted replacement — do not rewrite the whole file.

---

## Phase 9: Update `.gitignore`

Read `.gitignore`. Ensure these two lines are present:
```
planning/production/wiki/.last-hook-sha
planning/production/wiki/.pending-update.md
```
Append if missing.

---

## Phase 10: Summary

Report:
- Files created vs skipped (with skip reason)
- Which merges were applied and why
- Which concepts were extracted as standalone entries and why
- Design docs / source modules with rich coverage vs sparse coverage
- Source directories found with no corresponding design doc (flag for new doc)
- Any file that exceeded the 200-line target (flag for future split)
- Recommended next steps
