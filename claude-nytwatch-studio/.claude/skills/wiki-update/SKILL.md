---
name: wiki-update
description: "Applies pending wiki updates queued by the Stop hook. Reads changed files since the last update, maps them to wiki sections via _index.md, and rewrites the relevant wiki entries to reflect the current state of the project."
argument-hint: "[optional: specific file or subsystem to update]"
user-invocable: true
allowed-tools: Read, Glob, Grep, Bash, Write, Edit
---

## Overview

The wiki hook queues updates at session end. This skill processes the queue and
updates the wiki. It can also be run without a queue to do a deliberate update
based on recent commits or a specific area.

---

## Phase 1: Check for Pending Update

Read `production/wiki/.pending-update.md`.

If it exists:
- Note the list of changed files
- Note the base SHA and current SHA
- This is the **automatic path** — update wiki sections for those specific files

If it does not exist:
- If an argument was provided (e.g. `/wiki-update tracker`), treat that as the target
- Otherwise: run `git log --oneline -20` and `git diff HEAD~5..HEAD --name-only` to find recently changed files
- This is the **manual path** — update wiki based on recent git activity

---

## Phase 2: Map Files to Wiki Sections

Read `production/wiki/_index.md`.

For each changed file, find the matching wiki section using the routing table.
Build a list of:
- Wiki section file to update
- Source files that affect it

If a source file matches no pattern, note it for `architecture.md`.

---

## Phase 3: Read Current Wiki Sections

Read each wiki section file that needs updating.
Read the relevant source files (or their diffs from the pending update).

---

## Phase 4: Update Wiki Entries

For each wiki section, update or add entries based on what changed:

**What to update**:
- New routes, endpoints, or API changes → update the relevant section
- New features or settings → add to the appropriate section
- Bugs or footguns discovered → add to `known-gotchas.md`
- New patterns established → add to `conventions.md`
- Changed configuration → update the config section

**What NOT to update**:
- Do not echo the diff verbatim — synthesize what it means
- Do not add entries for cosmetic changes (whitespace, formatting, comments)
- Do not remove entries unless the feature was deleted

**Format rules**:
- Keep the existing structure and heading style
- Update `> **Last updated by**: wiki-update` and the date at the top of each file
- Keep entries concise — one paragraph or table row per concept

Write each updated wiki section using the Write or Edit tool.

---

## Phase 5: Clean Up

If `production/wiki/.pending-update.md` exists, delete it:
```bash
rm production/wiki/.pending-update.md
```

---

## Phase 6: Summary

Report:
- Which wiki sections were updated
- What was added or changed in each
- Any files that had no matching wiki section (flag for `_index.md` update)
- Whether the pending update file was cleared
