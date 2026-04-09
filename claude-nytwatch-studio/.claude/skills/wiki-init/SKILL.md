---
name: wiki-init
description: "Initializes or rebuilds the LLM wiki from scratch. Reads all project source, routes, templates, and sprint files to seed wiki entries for all nytwatch subsystems. Use when setting up the wiki for the first time or after a major refactor."
argument-hint: "[optional: section to re-init, e.g. tracker]"
user-invocable: true
allowed-tools: Read, Glob, Grep, Bash, Write, Edit
---

## Overview

Creates or rebuilds the full wiki in `planning/production/wiki/`. Reads all sprint files,
source code, routes, and templates to produce grounded entries.

If an argument is provided, only re-init that section. Otherwise, rebuild all sections.

---

## Phase 1: Survey Existing State

List all files in `planning/production/wiki/` recursively (if it exists).
List all sprint files in `planning/production/sprints/` (if any).

Determine which wiki sections already exist and which need to be created.

---

## Phase 2: Read Source Material

Read the following nytwatch source files:
- `src/nytwatch/web/routes.py` — all API endpoints
- `src/nytwatch/web/templates/*.html` — all page templates
- `src/nytwatch/pm/parser.py` — project management data model
- `src/nytwatch/pm/writer.py` — markdown writer
- `src/nytwatch/auditor/*.py` if it exists — auditor subsystem
- `src/nytwatch/tracker/*.py` if it exists — tracker subsystem

Also read sprint files and any design docs found.

---

## Phase 3: Read Existing Wiki (if re-initing)

If wiki files already exist, read them to preserve content not derivable from code
(e.g. hard-won gotchas, human-added context, decisions).

---

## Phase 4: Build or Rebuild Wiki Sections

For each section, generate content grounded in what was found in Phase 2.
Do not invent endpoints, classes, or decisions not evidenced in the source.

Sections to create/update:
- `_index.md` — routing table mapping source paths to wiki sections
- `architecture.md` — system overview, module structure, key classes
- `conventions.md` — Python patterns, API conventions, template conventions
- `decisions.md` — architectural decisions (ADRs found in code or sprints)
- `known-gotchas.md` — bugs and footguns found (e.g. route conflicts, modal UX issues)
- `features/tracker.md` — time tracker feature
- `features/auditor.md` — code auditor feature
- `features/project-management.md` — PM feature (sprints, stories, sub-tasks)
- `features/settings.md` — settings and configuration

Only create feature files for features with actual evidence in the source.

---

## Phase 5: Write Files

Write each section using the Write tool.

Set `> **Last updated by**: wiki-init` in the header of each file.
Set the current date.

---

## Phase 6: Update `.gitignore`

Ensure `planning/production/wiki/.last-hook-sha` and `planning/production/wiki/.pending-update.md`
are listed in `.gitignore`. Read `.gitignore` first, then append if missing.

---

## Phase 7: Update `CLAUDE.md`

If `planning/production/wiki/` is not yet referenced in `CLAUDE.md`, add a wiki section
pointing to the key wiki files with guidance on when to read each one.

---

## Phase 8: Summary

Report:
- Sections created vs updated
- Source files that provided the richest content
- Any features mentioned in code but not yet in the wiki
- Recommended next steps
