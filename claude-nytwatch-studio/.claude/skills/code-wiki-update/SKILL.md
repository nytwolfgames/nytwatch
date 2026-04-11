---
name: code-wiki-update
description: "Applies pending wiki updates queued by the Stop hook. Maps changed files to their read-context wiki sections via _index.md and rewrites relevant entries. Can also be run manually to update a specific area or based on recent commits."
argument-hint: "[optional: specific wiki file or subsystem to update, e.g. subsystems/diplomacy]"
user-invocable: true
allowed-tools: Read, Glob, Grep, Bash, Write, Edit
---

## Overview

The wiki hook queues updates at session end. This skill processes the queue and
updates the relevant wiki sections. It can also be run without a queue for a
deliberate update on a specific area or based on recent commits.

The wiki uses a **read-pattern model** — each file covers one read context, not
one GDD. Some files merge multiple GDDs; some files cover only part of a GDD.
When mapping changed source files to wiki sections, always check `_index.md`
for the correct target — do not assume one-to-one GDD correspondence.

---

## Phase 1: Check for Pending Update

Read `planning/production/wiki/.pending-update.md`.

**If it exists** (automatic path):
- Note the list of changed files and their diffs
- Note base SHA and current SHA

**If it does not exist** (manual path):
- If an argument was provided (e.g. `/code-wiki-update subsystems/diplomacy`), treat that as the only target
- Otherwise: run `git log --oneline -20` and `git diff HEAD~5..HEAD --name-only` to find recently changed files

---

## Phase 2: Map Changed Files to Wiki Sections

Read `planning/production/wiki/_index.md`.
Read `.claude/source-paths.md` — parse the mapping table inside the fenced
`paths` block. Each row maps a **Source Path** → **GDD Subfolder** → **Wiki
Subfolder**. Use the Source Path column to determine whether a changed file is
in-scope. Use the Wiki Subfolder column to determine where any new wiki file
created for that source area should be placed.

For each changed file, scan the routing table for matching class names, file
path patterns, or keywords. Build a work list of:
- Which wiki file to update
- Which source files / diffs feed into it

**Routing rules:**
- A source file may affect multiple wiki sections — add all matches to the work list
- `_index.md` may link to named anchors (`file.md#anchor`) — note the anchor so
  the update targets the right section of the file
- If a changed file matches no routing entry:
  1. Check whether it falls under any path listed in `.claude/source-paths.md`.
     If it does NOT — it is an external or third-party file. **Skip it silently.**
  2. If it IS in-scope: check whether the change introduces a **new class or system** —
     determine which read context it belongs to and add it to the appropriate existing
     wiki file
  3. Check whether the change is large enough to warrant a **new wiki file** — apply
     the extract rule: if this concept will be referenced by 3+ other systems, it
     deserves its own file; flag this in the Phase 6 summary and create the file
  4. If neither applies, fold it into `architecture.md` as a minor addition

**Do not default everything unmatched to `architecture.md`.** That file covers
only the core framework classes. Unmatched content belongs somewhere else.

---

## Phase 3: Read Current Wiki Sections

Read each wiki file in the work list.
Read the relevant source files (or their diffs from the pending update).
Read the GDDs that correspond to any changed source area if needed for context.

---

## Phase 4: Update Wiki Entries

For each wiki section in the work list, update entries based on what changed.

**What to update:**
- New classes, structs, enums → add to the appropriate table or section
- Changed method signatures or key behaviour → update the description
- New architectural decisions → add a compact entry to `decisions.md`
- Bugs, footguns, or traps discovered → add to `known-gotchas.md` AND to the
  relevant subsystem file's "Known Constraints" section
- New patterns established → add to `conventions.md`
- A system previously stubbed/commented-out becomes active → update the
  "Active vs. stub" note in the relevant file

**What NOT to update:**
- Do not echo the diff verbatim — synthesize what it means for an editor
- Do not add entries for cosmetic changes (whitespace, comments, formatting)
- Do not remove entries unless the class/system was deleted or renamed

**Size check:**
- After updating, if a file now exceeds 200 lines, flag it in the Phase 6 summary
  as a split candidate — do not split it automatically, just note it

**Format rules:**
- Preserve the existing structure and heading style of each file
- Update `> **Last updated by**: wiki-update` and the date at the top
- Keep entries concise — one paragraph or table row per concept
- Use relative markdown links to other wiki sections when referencing them
- If adding a new major sub-concept that will be cross-referenced, add a named
  anchor: `## Concept Name {#anchor-slug}`
- Update the `## See Also` footer if the change creates a new cross-reference

Write each updated section using the Edit tool (targeted replacement, not full
rewrite) unless the change is large enough to warrant a full Write.

---

## Phase 5: Update `_index.md` if Needed

If any new class, method, or keyword was added to the codebase in the changed files,
add corresponding rows to `planning/production/wiki/_index.md`.

If a new wiki file was created, add all its major classes to the routing table.

---

## Phase 6: Clean Up

If `planning/production/wiki/.pending-update.md` exists:
```bash
rm planning/production/wiki/.pending-update.md
```

---

## Phase 7: Summary

Report:
- Which wiki sections were updated and what changed in each
- Any changed source files that had no routing match (and where the content was placed)
- Any wiki files now over 200 lines (split candidates)
- Any new wiki files created
- Whether the pending update file was cleared
