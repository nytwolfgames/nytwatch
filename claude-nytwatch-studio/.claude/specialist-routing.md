# Specialist Routing Configuration

Maps game systems and keywords to their responsible project-specific specialist agents.

Use this file to route work to the correct specialist, and to ensure specialists know which systems are theirs.

**This file is project-specific.** It is empty by default. Populate it when you create project-specific specialist agents for your game's subsystems.

## Specialist-to-Systems Mapping

| Specialist Agent | Systems Owned | Keywords for Routing |
|---|---|---|
| (none configured yet) | — | — |

> **Note:** Generic roles (gameplay-programmer, engine-programmer, ai-programmer, etc.) are always available
> and do not need entries here. Add rows only for project-specific specialist agents you create — for example,
> a `myproject-combat-specialist` or `myproject-inventory-specialist`.

## How to Use

**For routing implementation work:**
1. Identify the system/class/keyword involved
2. Find it in the rightmost "Keywords for Routing" column
3. Contact the specialist agent listed in the first column

**For specialist self-check:**
- Each row is one specialist
- The middle column lists all systems they own
- The rightmost column lists keywords that trigger their involvement

**Modifying this table:**
- Adding a new system? Add it to the appropriate specialist's row and keywords column
- Splitting a system between two specialists? Discuss with both, update their rows, and ensure no duplicate ownership
- Renaming a specialist? Update the first column and any references in skills/agents
- Moving a system to a different specialist? Update both the old and new specialist's rows

## When to Create Project Specialists

Create a project-specific specialist when:
- A subsystem is large enough to have its own specialist knowledge (>500 lines of code)
- The subsystem is touched frequently and benefits from routing
- The system has complex conventions not covered by generic agents

Example agent name convention: `[project-slug]-[system]-specialist.md`

## Implementation Notes

Skills like `dev-story` use this routing table to dispatch work to specialists. If you:
- Rename a specialist → update this file AND any skills that reference the old name
- Add new keywords → update this file (skills will pick up the change automatically)
- Create a new specialist → add a row, add keywords, and notify lead-programmer to update affected skills
