# Narrative Wiki Configuration

Defines the structure, naming conventions, and entity organization for the narrative wiki at `planning/design/narrative/wiki/`.

## Directory Structure

The narrative wiki is organized by entity type:

```
planning/design/narrative/wiki/
├── _index.md              # Master routing table
├── _template.md           # Template for new entity pages
├── open-questions.md      # Unanswered narrative gaps
├── factions/              # All faction entries
│   ├── major/             # High-tier factions (entity type: faction-major)
│   └── minor/             # Low-tier factions (entity type: faction-minor)
├── characters/            # Named individuals (entity type: character)
├── peoples/               # Races and cultures (entity type: people)
├── geography/             # Regions and locations (entity type: geography)
├── lore/                  # Historical events and concepts (entity type: lore-event)
└── world/                 # World overview (entity type: world)
```

**How to modify:**
- Add new directory under root → Add new entity-type entry to this file
- Rename faction tier (major→ tier1, minor→tier2) → Update both this file and any skills that reference the tier

## Entity Types

Each entity type has:
- **Directory path**: relative to `planning/design/narrative/wiki/`
- **File naming**: kebab-case pattern (e.g., `iron-guard.md`)
- **Parent status entry**: where to register in `_index.md`

| Entity Type | Directory | File Naming | Example Path |
|---|---|---|---|
| faction-major | `factions/major/` | kebab-case faction name | `factions/major/iron-guard.md` |
| faction-minor | `factions/minor/` | kebab-case faction name | `factions/minor/wandering-clan.md` |
| character | `characters/` | kebab-case character name | `characters/aldric.md` |
| people | `peoples/` | kebab-case race/culture name | `peoples/stoneborn.md` |
| geography | `geography/` | plural grouping or region name | `geography/northern-reaches.md` |
| lore-event | `lore/` | kebab-case event name | `lore/the-great-sundering.md` |
| world | `world/` | world name (singular) | `world/erethos.md` |

## Inter-Linking Convention

When a page mentions a named entity that has its own wiki page, create a markdown link.

**Relative path resolution by entity type:**

From `factions/major/X.md`, to link to:
- Another major faction: `[Name](name.md)`
- A minor faction: `[Name](../minor/name.md)`
- A character: `[Name](../../characters/name.md)`
- A people: `[Name](../../peoples/name.md)`
- A lore event: `[Name](../../lore/name.md)`
- A geography entry: `[Name](../../geography/name.md)`

From `characters/X.md`, to link to:
- A major faction: `[Name](../factions/major/name.md)`
- A minor faction: `[Name](../factions/minor/name.md)`
- A people: `[Name](../peoples/name.md)`
- A lore event: `[Name](../lore/name.md)`

From `peoples/X.md`, to link to:
- A major faction: `[Name](../factions/major/name.md)`
- A minor faction: `[Name](../factions/minor/name.md)`
- A character: `[Name](../characters/name.md)`
- A lore event: `[Name](../lore/name.md)`

**How to modify:** If directory structure changes, update all relative paths. Use a helper script to validate all links post-change.

## Page Metadata

All entity pages use this frontmatter format:

```yaml
---
Status: stub | partial | complete
Canon Level: core | established | provisional | speculation
Visible To Player: visible | hidden | implied
---
```

Status definitions:
- `stub` — Minimal info, skeleton only
- `partial` — Major facts documented, some mysteries remain
- `complete` — Comprehensive entry, ready for lore lookup

Canon Level definitions:
- `core` — Foundational lore, immutable
- `established` — High confidence, unlikely to change
- `provisional` — Subject to refinement based on new questions
- `speculation` — Inferential or in-universe rumor

## Index Registration

All entity pages must be registered in `_index.md` with:
- Entity name (as it would be linked)
- File path (relative to wiki root)
- Status and Canon Level (for summary counts)

Register at the appropriate section header (Factions, Characters, Peoples, Geography, Lore, World).

## Configuration Enforcement

When creating or updating pages:
1. Validate page is in the correct directory per entity type
2. Validate filename matches naming convention
3. Validate all entity mentions are hyperlinks (no plain text for entities with pages)
4. Validate metadata frontmatter is present and valid
5. Validate page is registered in `_index.md`

Tools and skills that process the narrative wiki should read this file to understand:
- Where new pages should be created
- How to compute relative links between entities
- What metadata is required
- How to categorize entities for registration
