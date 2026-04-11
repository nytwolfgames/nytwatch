# Project Configuration

Defines project-specific paths and settings that may vary between projects or change during development.

## Source Code Structure

```
[PROJECT_ROOT]/
├── src/                                # Main game source code
│   ├── core/                           # Engine/framework code
│   ├── gameplay/                       # Gameplay systems
│   ├── ai/                             # AI systems
│   ├── networking/                     # Multiplayer code
│   ├── ui/                             # UI code
│   └── tools/                          # Dev tools
└── tests/                              # Automated tests
    ├── unit/                           # Unit tests
    └── integration/                    # Integration tests
```

**Key paths:**
- `PROJECT_SOURCE_ROOT` = `src/`
- `PROJECT_TESTS_ROOT` = `tests/`

**How to modify:**
- Rename source directory → update this file + `.claude/source-paths.md`
- Add new source root (e.g., for tools) → add entry to this file
- Change test location → update this file + any CI/CD config

**Skills that reference these paths:**
- create-stories, dev-story, code-review
- code-wiki-init (when finding source files)
- Any skill that looks up source files

Update skills to read from `.claude/project-config.md` instead of hardcoding `src/`.

## Content and Asset Paths

```
assets/
├── art/                # Sprites, models, textures
├── audio/              # Music, SFX
├── vfx/                # Particle effects
├── shaders/            # Shader files
└── data/               # JSON config/balance data
```

## Planning and Documentation Paths

```
[PROJECT_ROOT]/
├── planning/
│   ├── design/                         # Design documentation
│   │   ├── gdd/                        # Game design documents
│   │   │   └── *.md                    # Top-level GDDs (add subfolders per module)
│   │   └── narrative/
│   │       └── wiki/                   # Living narrative wiki (lore, factions, characters)
│   ├── docs/                           # Architecture and reference documentation
│   │   ├── architecture/               # Architecture Decision Records (ADRs)
│   │   ├── engine-reference/           # Engine-specific API and feature docs
│   │   └── registry/                   # Central architecture registry (YAML)
│   ├── production/                     # Production management
│   │   ├── wiki/                       # Code wiki (LLM knowledge base)
│   │   │   ├── subsystems/             # One file per read context
│   │   │   ├── conventions.md          # Code conventions
│   │   │   ├── decisions.md            # ADR index
│   │   │   ├── known-gotchas.md        # Known issues and traps
│   │   │   └── _index.md               # Master wiki routing table
│   │   ├── sprints/                    # Sprint plans and tracking
│   │   ├── session-state/              # Ephemeral session state (gitignored)
│   │   └── session-logs/               # Session audit logs (gitignored)
│   └── prototypes/                     # Throwaway prototype work
```

**Key paths:**
- `GDD_ROOT` = `planning/design/gdd/`
- `NARRATIVE_WIKI_ROOT` = `planning/design/narrative/wiki/`
- `ARCHITECTURE_DOCS_ROOT` = `planning/docs/architecture/`
- `CODE_WIKI_ROOT` = `planning/production/wiki/`

**How to modify:**
- Move GDD directory → update this file + `.claude/source-paths.md`
- Move code wiki → update this file + all skills that reference it
- Add new planning subdirectory → document it here

**Skills that reference these paths:**
- code-wiki-init, code-wiki-update
- narrative-wiki-init, narrative-wiki-update
- create-stories, dev-story, estimate
- architecture-decision
- Any skill that reads from planning/

Update skills to use variables like `${GDD_ROOT}` or to read from config instead of hardcoding.

## Validation

Before modifying this file:

1. Ensure source-paths.md is updated if paths change
2. Ensure all referenced directories exist
3. Notify all affected skills (see lists above)
4. Test that relative paths still resolve correctly

Failing to update this file when restructuring the project will cause skills to fail silently or create files in wrong locations.
