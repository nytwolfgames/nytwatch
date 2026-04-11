# Documentation Configuration

Defines paths, file naming conventions, and schemas for architecture and design documentation.

## Architecture Decision Records (ADRs)

**Storage location:** `planning/docs/architecture/`

**File naming pattern:** `adr-[NNNN]-[slug].md`
- `[NNNN]` = zero-padded 4-digit decision number (e.g., 0001, 0042, 1234)
- `[slug]` = kebab-case summary of the decision (e.g., adr-0001-resource-economy-design.md)

**Required frontmatter:**
```yaml
---
number: [NNNN]
title: [Human-readable title]
status: proposed | accepted | superseded | deprecated
date: YYYY-MM-DD
author: [Name or agent name]
---
```

**Sections required in every ADR:**
1. **Decision** — What decision was made?
2. **Context** — Why was this decision needed?
3. **Rationale** — Why was this the best option?
4. **Consequences** — What changes as a result?
5. **Alternatives considered** — What other options were rejected and why?

**How to modify:**
- Change file naming pattern → update this file + any skills that generate ADRs
- Change required sections → update this file + any ADR templates
- Change storage location → update `.claude/source-paths.md` + any skills that read ADRs

## Architecture Registry

**Storage location:** `planning/docs/registry/architecture.yaml`

**Purpose:** Central registry of all architectural decisions, interfaces, ownership, and constraints.

**YAML schema:**
```yaml
state_ownership:
  [system-name]:
    owner: [specialist-agent-name]
    classes: [list of classes/modules]
    description: [brief description]

interfaces:
  [interface-name]:
    implementers: [list of classes implementing this interface]
    purpose: [what this interface does]

forbidden_patterns:
  - pattern: [description of forbidden pattern]
    reason: [why it's forbidden]
    contact: [specialist to discuss exceptions]

api_decisions:
  [api-name]:
    decision: [what was decided about this API]
    rationale: [why]
    adr: [link to ADR if applicable]
```

**Validation rules:**
- Every system in `state_ownership` must have a corresponding specialist
- Every specialist in `.claude/specialist-routing.md` should appear in `state_ownership`
- Circular ownership is forbidden (system A → B → A)

**How to modify:**
- Add new system → add entry to `state_ownership` and link to specialist in `specialist-routing.md`
- Change specialist ownership → update both `state_ownership` AND `specialist-routing.md`
- Add API decision → add entry to `api_decisions` with rationale + ADR reference

## Game Design Documents (GDDs)

**Storage location:** `planning/design/gdd/` (and subfolders per `.claude/source-paths.md`)

**File naming pattern:** [system-name]-[aspect].md or [system-name].md
- Example: `combat-system.md`, `economy-progression.md`, `inventory-system.md`

**Required sections in every GDD (per CLAUDE.md Coding Standards):**
1. Overview
2. Player Fantasy
3. Detailed Rules
4. Formulas
5. Edge Cases
6. Dependencies
7. Tuning Knobs
8. Acceptance Criteria

**GDD-to-Wiki mapping:**
See `.claude/source-paths.md` for how GDDs map to wiki output folders. The wiki is NOT one-to-one with GDDs.

**How to modify:**
- Change GDD subfolder organization → update `.claude/source-paths.md` (which skills reference)
- Change required GDD sections → update `CLAUDE.md` (which points to this standard)

## Engine Reference Docs

**Storage location:** `planning/docs/engine-reference/[engine]/`

Example structure:
```
planning/docs/engine-reference/godot/
├── VERSION.md              # Pinned engine version, knowledge cutoff, breaking changes summary
├── breaking-changes.md    # APIs removed since LLM knowledge cutoff
├── deprecated-apis.md     # APIs marked deprecated
└── modules/
    ├── rendering.md
    ├── physics.md
    ├── input.md
    └── ...
```

**How to modify:**
- Add new engine → create new `planning/docs/engine-reference/[engine]/` directory
- Add new module docs → add `modules/[module].md` file
- Update engine version → update `VERSION.md` with new cutoff date and breaking changes summary

## Configuration Location

All of these paths are project-specific and may change. Before modifying:

1. Check `.claude/source-paths.md` for path mappings (where GDDs go, where wiki goes, etc.)
2. Check `.claude/project-config.md` for project root paths (source directory structure)
3. Check this file for documentation structure specifics
4. Notify all skills and agents that reference these paths

**Affected skills when paths change:**
- code-wiki-init, code-wiki-update
- architecture-decision
- create-stories, dev-story
- Any skill that reads from `planning/docs/`

Update skills to reference config files instead of hardcoding paths.
