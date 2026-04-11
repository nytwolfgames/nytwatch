---
name: narrative-wiki-init
description: "Initializes or rebuilds the narrative wiki from scratch. Creates wiki pages for all factions, characters, peoples, lore, and world entities. Use when setting up the wiki for the first time, after major narrative refactors, or when rebuilding from incomplete sources."
argument-hint: "[optional: section to re-init only, e.g. factions/major or peoples]"
user-invocable: true
allowed-tools: Read, Glob, Grep, Write, Edit
---

## Purpose

Build or rebuild `planning/design/narrative/wiki/` as a **queryable lore knowledge base**:
focused wiki pages for each faction, character, people, lore event, and world element.

The atomic unit is a named entity (faction, character, race, location, event). One page per entity.

If an argument is given, re-init only that section and update `_index.md`.
Otherwise, rebuild all sections from scratch.

---

## Phase 1: Survey Existing Narrative Wiki

1. List all existing files in `planning/design/narrative/wiki/` to identify what already exists
2. If files exist, read `_index.md` to understand current structure
3. If `open-questions.md` exists, read it to preserve unanswered questions
4. If `_template.md` exists, read it to understand page format

---

## Phase 2: Read Narrative Content Sources

Survey available narrative sources:
- `planning/design/gdd/` — any GDD files that cover lore, factions, characters
- Game data JSON or config files if available (faction data, character data, province data)
- Any existing narrative wiki pages that serve as source material
- `planning/docs/` — any lore design documents or narrative decisions

---

## Phase 3: Identify Named Entities

Parse and extract all named entities:
- **Factions**: High Clans (major), Low Clans (minor), any others
- **Characters**: Named lords, heroes, key figures
- **Peoples**: Races, cultural groups (Ashenclad, Luminarae, Covenborn, Humans, etc.)
- **Geography**: Provinces, regions, named locations
- **Lore**: Historical events, prophecies, religious concepts, notable artifacts
- **World**: The world itself and its properties

---

## Phase 4: Decompose into Wiki Pages

Read `.claude/narrative-config.md` to understand the narrative wiki directory structure and entity-type-to-directory mappings.

Create one page per named entity using:
- **Entity type** from narrative config (faction-major, faction-minor, character, people, geography, lore-event, world)
- **Directory path** from narrative config
- **File naming pattern** from narrative config (typically kebab-case)

**Reference narrative-config.md for:**
- Which directories exist and what entity types belong in each
- Exact file naming conventions for each entity type
- Required frontmatter metadata (Status, Canon Level, Visible To Player)
- Mandatory section headers for page structure

**Do NOT hardcode directory names or naming patterns.** All entity organization is defined in `.claude/narrative-config.md`.
If the directory structure changes, update only that config file—skills will follow automatically.

---

## Phase 5: Generate Wiki Pages

For each entity, create a page using `_template.md` as the base:

**Required frontmatter fields (from _template.md):**
- `Status`: `stub` | `partial` | `complete`
- `Canon Level`: `core` | `established` | `provisional` | `speculation`
- `Visible To Player`: `visible` | `hidden` | `implied` (for meta lore)

**Page sections:**
- **Overview**: One-paragraph summary of what this entity is
- **Known Facts**: Bulleted list of confirmed facts
- **Mysteries**: Unanswered questions (link to open-questions.md if applicable)
- **Relations**: Links to related entities (factions, characters, locations)
- **History** (if applicable): Timeline or significant events

**Inter-linking (mandatory):**
Every named entity mention MUST be a hyperlink to its wiki page (if a page exists).
Applies to: faction names, character names, race names, place names, lore concepts.

Use relative paths:
- `factions/major/` to another major faction: `[Name](name.md)`
- `factions/major/` to a minor faction: `[Name](../minor/name.md)`
- `factions/major/` to a people: `[Name](../../peoples/name.md)`
- `characters/` to factions: `[Name](../factions/major/name.md)`

---

## Phase 6: Create _template.md (if missing)

If `_template.md` doesn't exist, create it as a reference template for new pages:

```markdown
---
Status: stub
Canon Level: provisional
Visible To Player: visible
---

# [Entity Name]

## Overview
[One-paragraph summary]

## Known Facts
- [Fact 1]
- [Fact 2]

## Mysteries
- [Open question]

## Relations
- Related to [Other Entity](link)

## History
[Timeline or historical notes if applicable]
```

---

## Phase 7: Generate _index.md

Create a master routing table and status summary:

**Top section — Status Summary:**
```
| Status | Count |
|--------|-------|
| Complete | X |
| Partial | Y |
| Stub | Z |
```

**Sections by entity type:**
- Factions (Major, Minor)
- Characters
- Peoples
- Geography
- Lore Events
- World

Each section lists: `Entity Name` → `path/to/page.md`

**Example rows:**
```
| Ashenclad (people) | [peoples/ashenclad.md](peoples/ashenclad.md) |
| Blood Brothers of Raal (faction) | [factions/major/blood-brothers-of-raal.md](factions/major/blood-brothers-of-raal.md) |
| Korrath (character) | [characters/korrath.md](characters/korrath.md) |
```

---

## Phase 8: Initialize or Preserve open-questions.md

If `open-questions.md` exists, preserve it as-is (contains accumulated narrative questions).

If it doesn't exist, create a stub:
```markdown
# Open Narrative Questions

Unanswered lore questions that need resolution. See corresponding wiki pages for context.

| # | Question | Related Pages | Status |
|---|----------|---------------|--------|
| Q1 | ? | | OPEN |
```

---

## Phase 9: Summary

Report:
- Number of entities categorized by type (factions, characters, peoples, etc.)
- Status summary (how many stub/partial/complete)
- Any entities missing pages or information
- Links to `_index.md` for verification
