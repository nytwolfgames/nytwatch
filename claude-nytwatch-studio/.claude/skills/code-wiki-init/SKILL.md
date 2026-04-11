---
name: code-wiki-init
description: "Initializes or rebuilds the LLM wiki from scratch. Read-pattern-driven: creates one focused wiki file per read context, not one per GDD. Merges tightly-coupled GDDs, extracts cross-cutting concepts as standalone entries. Use when setting up the wiki for the first time, after major refactors, or after adding new GDDs."
argument-hint: "[optional: subsystem slug to re-init only, e.g. subsystems/diplomacy]"
user-invocable: true
allowed-tools: Read, Glob, Grep, Bash, Write, Edit
---

## Purpose

Build or rebuild `planning/production/wiki/` as a **Karpathy-style LLM wiki**:
one focused, dense file per **read context** — small enough for an LLM to read
cheaply and get exactly what it needs before touching a specific area of code.

Reference: https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f

**The atomic unit is a read context, not a GDD.** A read context is the
complete set of knowledge an LLM needs before safely editing a specific area.
Sometimes that maps to one GDD. Sometimes it is a sub-section of a GDD.
Sometimes it merges two tightly-coupled GDDs. Sometimes it extracts a
cross-cutting concept that appears in many GDDs into its own entry.

If an argument is given, re-init only that subsystem file and update `_index.md`.
Otherwise, rebuild all sections from scratch.

---

## Phase 1: Survey Source Material

1. Read `.claude/source-paths.md` — parse the mapping table inside the fenced
   `paths` block. Each row maps a **Source Path** → **GDD Subfolder** → **Wiki
   Subfolder**. Keep this table in memory — it governs both which source files to
   survey and where output files are written in Phases 6 and 7.
2. Glob GDDs for each source path: for the `Source/` root glob
   `planning/design/gdd/*.md`; for plugin rows glob the corresponding GDD subfolder
   (e.g. `planning/design/gdd/rts/*.md`). Read every GDD found — these are authoritative.
3. For each source path, glob for source files: `**/*.h` for C++/Unreal,
   `**/*.py` for Python, `**/*.ts` / `**/*.js` for TypeScript/JavaScript, etc.
   Read relevant headers per domain (see Phase 2 mapping).
4. `Glob("planning/production/sprints/*.md")` — mine for P0/P1 bugs, decisions, gotchas
5. `Glob("planning/docs/architecture/**/*.md")` — read all ADRs
6. List existing wiki files to preserve any human-added gotchas

---

## Phase 2: Read All GDDs and Identify Source Headers

For each GDD file found in Phase 1:

1. Read the GDD in full
2. Extract all class names, system names, and keywords from the GDD
3. Use those keywords to search source headers in the source paths from `.claude/source-paths.md`
   - For each source path, glob `**/*.h` files and search for matching class definitions
   - Example: if GDD mentions "AArmy", "ASettlement", "supply", grep for these classes in the corresponding source roots
4. Collect ALL matching source header files that document the GDD's systems

**Do NOT rely on a hardcoded mapping.** The GDD is authoritative for what systems it covers.
The source code is authoritative for which files implement those systems.
Use Grep to find the implementation, don't assume you know where it lives.

---

## Phase 3: Preserve Existing Gotchas

If wiki files exist, read `known-gotchas.md` and any subsystem files.
Extract content that cannot be derived from source — sprint discoveries, trap warnings,
edge case commentary, human-added notes. Carry it forward into the rebuilt files.

---

## Phase 4: Decompose Into Read Contexts

### Decomposition Rules

Apply these rules before deciding on the file list:

**Merge rule — always co-read → one file**
If touching system A always requires knowing system B, and vice versa, merge them.
Example: `rts-mass-units` (Mass ECS entities) and `rts-squad-system` (squad owns entities)
are read together whenever you touch soldier behaviour — merge into `rts-squads-units.md`.

**Extract rule — 3+ cross-system references → own file**
If a concept appears as a dependency in 3 or more other wiki entries, extract it
as a standalone file with a stable link target other entries can reference.
Example: `ISavableObjectInterface` (3-hook contract) is needed when adding any new
savable class — extract to `subsystems/save-interface.md`.
Example: `UEventsHolder` delegates are subscribed to from every major system —
extract to `subsystems/event-bus.md`.

**Split rule — different edit contexts → separate files**
If a GDD covers two areas that are touched independently, split them.
Example: campaign-simulation covers (a) time/army-FSM/supply and (b) settlements/province.
A developer fixing army attrition never needs settlement building slots. Split.

**Size rule — hard cap**
Target 100–150 lines per file. A file that would exceed 200 lines must be split.
If a concept is too small for its own file (< 20 meaningful lines), fold it into
the most closely related file and add a routing entry in `_index.md`.

### Wiki File Generation

After decomposing GDDs and source into read contexts, generate wiki files as follows:

**Always create (top-level council):**
1. `_index.md` — Master routing table. See Phase 7.
2. `architecture.md` — Only the core framework classes: game mode, game state, player state, game instance, main HUD. Nothing else.
3. `conventions.md` — Code conventions from GDDs and codebase observation (naming, prefixes, Blueprint/C++ split, module structure).
4. `decisions.md` — Extract from `planning/docs/architecture/*.md` (ADRs). One compact entry per decision.
5. `known-gotchas.md` — Traps, gotchas, P0/P1 bugs from sprints and code inspection.

**For each GDD:**
After Phase 4 decomposition, create a file for each identified read context:
- If a GDD maps 1:1 to a read context → create one file
- If a GDD maps to multiple read contexts (split per Phase 4 rules) → create multiple files
- Output location determined by the **Wiki Subfolder** in `.claude/source-paths.md`
- Filename: kebab-case version of the system/feature name (e.g., `campaign-simulation.md`)

**File naming conventions:**
- Top-level files go directly in `planning/production/wiki/`
- Plugin/subsystem files go in corresponding `planning/production/wiki/subsystems/[plugin]/` folder per `.claude/source-paths.md`
- Example: GDD at `planning/design/gdd/rts/squad-system.md` → wiki at `planning/production/wiki/subsystems/rts/squad-system.md`

**Do NOT maintain a hardcoded list of "expected files".** The file structure emerges from GDD decomposition.
Only create files for systems actually documented in your GDDs. Remove files if their GDDs are deleted.

---

## Phase 5: Content Rules Per File

Every wiki file must be:

- **Focused**: covers exactly one read context. No cross-system sprawl.
- **Dense**: every line earns its place. No generic UE boilerplate, no padding.
- **Grounded**: only document classes, flags, and behaviours evidenced in GDDs or source headers. Do not invent.
- **Actionable**: a reader should be able to make correct edits after reading this file alone.
- **Compact**: 100–150 lines. Hard cap at 200 — split if exceeded.

### Required header

```markdown
# [Subsystem Name] — LLM Wiki

> **Last updated by**: wiki-init  
> **Date**: [today]  
> **Source**: [list GDDs and/or source files used]
```

### Required footer

```markdown
## See Also
- [link to file](path) — one-line reason to read it
```

Include a See Also section on every file. Link to the 1–3 most related wiki files
an editor of this system would also need. Do not list unrelated files.

### Required named anchors

For any major sub-concept within a file that is referenced by 2+ other files,
add a named anchor: `## Concept Name {#anchor-slug}` so `_index.md` can link
directly to that section rather than just the file.

### Content to include per file

1. **One-line role** of this read context
2. **Key classes** — file path, role, important fields/methods
3. **Critical flows** — the main execution paths an editor must understand
4. **Key enums/structs** defined in this domain
5. **Active vs. stub/commented status** — flag anything not yet wired
6. **Integration points** — what this system calls and what calls it
7. **Known constraints** — hard limits, magic numbers, known bugs from P0/P1 items

---

## Phase 6: Write All Files

Write each file with the Write tool. Use the **Wiki Subfolder** column from
`.claude/source-paths.md` to determine the output directory for each file:

- Systems sourced from `Source/` → `planning/production/wiki/subsystems/<file>.md`
- Systems sourced from `Plugins/RTS/Source/` → `planning/production/wiki/subsystems/rts/<file>.md`
- Systems sourced from `Plugins/DynamicUI/Source/` → `planning/production/wiki/subsystems/dynamicui/<file>.md`
- Systems sourced from `Plugins/HierarchicalNavMesh/Source/` → `planning/production/wiki/subsystems/hnav/<file>.md`
- Top-level files (`architecture.md`, `conventions.md`, `decisions.md`, `known-gotchas.md`) always go in `planning/production/wiki/`

Create subdirectories as needed. Skip a file entirely (do not write a placeholder)
if it would contain only stub content — note it in the Phase 10 summary instead.

---

## Phase 7: Write `_index.md`

Build a comprehensive routing table. Every major class name and keyword should
have a row. Multiple rows may point to the same file. Rows may link to named
anchors within a file (e.g. `subsystems/persistence.md#isavableobjectinterface`).

```markdown
# LLM Wiki — Routing Index

> **Last updated by**: wiki-init  
> **Date**: [today]

Read this file first. Find the class or keyword you are about to touch.
Follow the link to the correct wiki section before editing.

| Pattern / Class / Keyword | Wiki Section |
|---|---|
| `AMainGameModeBase`, `AMainGameStateBase`, `UGameInstanceBase` | [architecture.md](architecture.md) |
| `UEventsHolder`, delegates, `OnDayPassed`, `OnGameStart` | [subsystems/event-bus.md](subsystems/event-bus.md) |
| `ISavableObjectInterface`, `PreSaving`, `PostLoading`, `OnActorLoadedFromSaveFile` | [subsystems/save-interface.md](subsystems/save-interface.md) |
...
(add a row for every major class/keyword discovered)

## Source Directories

| Directory | Contents |
|---|---|
...
```

---

## Phase 8: Verify CLAUDE.md Is Not Stale

Read `CLAUDE.md`. Find the `## LLM Wiki` section.

The section should contain **only** the four always-read files and the two run
instructions — it must NOT contain a per-subsystem routing table (that lives in
`_index.md` only).

If the section has been accidentally expanded with a routing table, remove the
table rows and restore the minimal form:

```markdown
## LLM Wiki

The project maintains a living knowledge base in `planning/production/wiki/`.
Before touching any known subsystem, look up the class or keyword in
`planning/production/wiki/_index.md` and read the linked section.

These four files apply to every task — read them proactively:

- `planning/production/wiki/conventions.md` — naming, UE prefixes, Blueprint/C++ split
- `planning/production/wiki/decisions.md` — ADRs; check before proposing architecture changes
- `planning/production/wiki/known-gotchas.md` — traps, null-ref risks, hardcoded values
- `planning/production/wiki/subsystems/save-interface.md` — `ISavableObjectInterface` contract; required before adding or modifying any savable class

**Wiki is queued automatically**: The Stop hook writes a pending update at session end.
Run `/code-wiki-update` to apply the queued changes after any significant session.
Run `/code-wiki-init` to rebuild the wiki from scratch if it becomes stale.
```

Use the Edit tool only if a correction is needed. Do not touch the rest of `CLAUDE.md`.

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
- Which merges were applied (and why)
- Which concepts were extracted as standalone entries (and why)
- GDDs that had rich source header coverage vs GDD-only coverage
- Source directories found with no corresponding GDD (flag for new GDD creation)
- Any file that exceeded the 200-line target (flag for future split)
- Recommended next steps
