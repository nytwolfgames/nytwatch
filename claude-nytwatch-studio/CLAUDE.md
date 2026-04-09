# Claude Code Game Studios -- Game Studio Agent Architecture

Indie game development managed through 48 coordinated Claude Code subagents.
Each agent owns a specific domain, enforcing separation of concerns and quality.

## Technology Stack

- **Engine**: [CHOOSE: Godot 4 / Unity / Unreal Engine 5]
- **Language**: [CHOOSE: GDScript / C# / C++ / Blueprint]
- **Version Control**: Git with trunk-based development
- **Build System**: [SPECIFY after choosing engine]
- **Asset Pipeline**: [SPECIFY after choosing engine]

> **Note**: Engine-specialist agents exist for Godot, Unity, and Unreal with
> dedicated sub-specialists. Use the set matching your engine.

## Project Structure

@.claude/docs/directory-structure.md

## Engine Version Reference

@docs/engine-reference/godot/VERSION.md

## Technical Preferences

@.claude/docs/technical-preferences.md

## Coordination Rules

@.claude/docs/coordination-rules.md

## Collaboration Protocol

**User-driven collaboration, not autonomous execution.**
Every task follows: **Question -> Options -> Decision -> Draft -> Approval**

- Agents MUST ask "May I write this to [filepath]?" before using Write/Edit tools
- Agents MUST show drafts or summaries before requesting approval
- Multi-file changes require explicit approval for the full changeset
- No commits without user instruction

See `docs/COLLABORATIVE-DESIGN-PRINCIPLE.md` for full protocol and examples.

> **First session?** If the project has no engine configured and no game concept,
> run `/start` to begin the guided onboarding flow.

## Coding Standards

@.claude/docs/coding-standards.md

## Context Management

@.claude/docs/context-management.md

## LLM Wiki

The project maintains a living knowledge base in `planning/production/wiki/`.
Read relevant wiki sections at the start of any task touching a known subsystem.

| Section | When to read |
|---|---|
| `planning/production/wiki/architecture.md` | Any task touching routes, data model, or module structure |
| `planning/production/wiki/conventions.md` | Any Python authoring task |
| `planning/production/wiki/decisions.md` | Before proposing architectural changes |
| `planning/production/wiki/known-gotchas.md` | Before touching routes, modals, or markdown writer |
| `planning/production/wiki/features/tracker.md` | Any tracker feature work |
| `planning/production/wiki/features/auditor.md` | Any auditor feature work |
| `planning/production/wiki/features/project-management.md` | Any PM feature work (sprints, stories, sub-tasks) |
| `planning/production/wiki/features/settings.md` | Any settings or configuration work |

**Wiki is queued automatically**: The Stop hook writes a pending update at session end.
Run `/wiki-update` to apply the queued changes after any significant session.
Run `/wiki-init` to build the wiki from scratch (first time or after major refactor).
