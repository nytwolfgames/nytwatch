---
name: narrative-wiki-update
description: "Updates the narrative wiki after lore decisions are made. Reads open-questions.md for answered questions, updates affected wiki pages, cross-links, and _index.md. Run after any Q&A session that resolves lore questions."
argument-hint: "[optional: specific wiki page or question number to update, e.g. Q2 or factions/high-clans/silent-conqueror]"
user-invocable: true
allowed-tools: Read, Glob, Grep, Write, Edit
---

## Overview

The narrative wiki lives at `planning/design/narrative/wiki/`.
This skill processes answered open questions and keeps the wiki consistent and cross-linked.

Run this after any session that resolves lore questions — similar to how `/wiki-update`
keeps the code wiki in sync.

---

## Phase 1: Check for Answered Questions

Read `planning/design/narrative/wiki/open-questions.md`.

**If an argument was provided** (e.g. `Q2` or `factions/high-clans/silent-conqueror`):
- Target only that question or page

**Otherwise:**
- Find all questions with status `ANSWERED` that have not yet been applied to their wiki pages
- A question is "not yet applied" if the answer field is filled but the linked wiki page
  still shows stub/partial content inconsistent with the answer

---

## Phase 2: Read Affected Pages

For each answered question, identify which wiki pages are affected via the `See` links
in that question entry. Read each affected page.

Also read `planning/design/narrative/wiki/_index.md`.

---

## Phase 3: Update Wiki Pages

For each affected page:

**What to update:**
- Promote `Status` from `stub` → `partial` or `partial` → `complete` as appropriate
- Add new facts to `Known Facts`
- Remove or resolve items from `Mysteries` that are now answered
- Update `Relations` with new cross-links if the answer introduces connections
- Update `History` if the answer is a historical fact
- Update `Canon Level` if the answer elevates provisional facts to established

**What NOT to update:**
- Do not remove `Mysteries` entries that are still open
- Do not change `Visible To Player` without explicit direction
- Do not synthesise speculative content — only document what was explicitly answered

**Format rules:**
- Update `> **Last updated**: YYYY-MM-DD` at the top of every page you touch
- Keep entries concise — the wiki is for LLM consumption, not player reading
- Use relative markdown links for all cross-references

**Inter-linking rule (mandatory):**
Every named entity that has its own wiki page MUST appear as a hyperlink — not plain text.
This applies to: faction names, lord names, race names, lore concepts, province/place names.
Applies everywhere on the page: table values, bullet points, body paragraphs, known facts.
When updating a page, scan all entity mentions and add links where missing.

**Path conventions:**
Read `.claude/narrative-config.md` for the relative link patterns between entity types.
Do NOT hardcode paths. The file specifies how to compute relative links from any entity type to any other.
If entity directory structure changes (e.g., faction tiers renamed), only narrative-config.md needs updating.

---

## Phase 4: Create New Pages if Needed

If an answered question names a new entity (e.g. a new clan name, a new character, a new region), determine:
1. The **entity type** (faction, character, people, geography, lore-event, etc.)
2. Read `.claude/narrative-config.md` to find the correct **directory** and **file naming convention** for that entity type
3. Create the new page using `planning/design/narrative/wiki/_template.md` as a template
4. Place it in the directory specified in narrative-config.md

**Do NOT hardcode faction tier names or directory structure.** narrative-config.md is authoritative.
If faction tiers change (e.g., from major/minor to tier1/tier2), only update narrative-config.md—this skill will follow automatically.

---

## Phase 5: Update `_index.md`

Add rows to `planning/design/narrative/wiki/_index.md` for:
- Any new entity names introduced by the answer
- Any new keywords that other agents would search for
- Any new page created in Phase 4

Update the **Status Summary** table counts at the top of `_index.md`.

---

## Phase 6: Mark Questions as Applied

In `open-questions.md`, add a note under answered questions confirming which wiki
pages were updated. Format:
```
**Applied to:** [page1.md](path), [page2.md](path) — YYYY-MM-DD
```

---

## Phase 7: Summary

Report:
- Which questions were processed
- Which pages were updated and what changed
- Any new pages created
- Any questions that could not be applied (missing information, contradictions)
- Current wiki status summary (stub/partial/complete counts)
