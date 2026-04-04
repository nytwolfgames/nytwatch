# Code Auditor Agent

A proactive code analysis agent for Unreal Engine C++ game projects. It scans your codebase on a schedule, identifies bugs, performance issues, UE anti-patterns, and improvement opportunities, then presents them in a local web dashboard where you can review, approve, and batch-apply fixes — with build verification and automated testing.

## How it works

```
1. SCAN        Agent scans code (scheduled or manual trigger)
                 |
2. FINDINGS    Dashboard shows findings with fix diffs + test cases
                 |
3. REVIEW      Dev approves/rejects each finding
                 |
4. APPLY       Dev clicks "Apply approved findings" -> batch created
                 |
5. BUILD       UE build runs on the patched code
                 |
6. TEST        Generated tests run against the patched code
                 |
7. PR          On success: branch committed, PR created
                 |
8. NOTIFY      Dev gets notification: "5 findings resolved, PR #87 ready"
                 |
9. MERGE       Dev reviews PR and merges at their convenience
```

## Documentation

| Document | Description |
|----------|-------------|
| [Product Brief](docs/PRODUCT_BRIEF.md) | What Code Auditor is, who it's for, features, roadmap, competitive landscape |
| [Technical Specification](docs/TECHNICAL_SPEC.md) | Complete module reference, database schema, API endpoints, prompt engineering, status lifecycles |
| [Setup Guide](docs/SETUP_GUIDE.md) | Step-by-step setup for humans and AI agents, with automated setup script |
| [Design System](docs/DESIGN_SYSTEM.md) | "Clinical Precision" visual language — colors, typography, components, interaction patterns |

## Requirements

- Python 3.9+
- Claude Code CLI (`claude`) installed and authenticated with a Claude Max subscription
- Git
- GitHub CLI (`gh`) for PR creation
- Unreal Engine (for build verification and test execution)

## Quick start

### 1. Install

```bash
cd /path/to/code-auditor
pip install -e .
```

### 2. Start the dashboard

```bash
code-auditor serve
```

Open http://127.0.0.1:8420 in your browser. If no project is configured yet, you will be redirected automatically to the setup wizard.

### 3. Run the Setup Wizard

Click **"+ Setup New Project"** on the Settings page (or follow the first-run redirect). The wizard walks you through six steps:

| Step | What you configure |
|------|--------------------|
| **1 — Setup** | Project name, repository path, UE installation directory (all with folder browsers) |
| **2 — Sources** | Top-level source directories auto-detected from the repo — mark each as **Active** (C++ to scan) or **Ignored** (skip entirely). Only Active directories proceed to the next step. |
| **3 — Systems** | Gameplay module systems, grouped under each active source directory. Each system groups related sub-paths that Claude analyses together. Click **✨ Suggest with Claude** to auto-generate systems from your source structure. |
| **4 — Build** | `.uproject` file path and timeout overrides |
| **5 — Schedule** | Incremental scan interval and rotation schedule |
| **6 — Review** | Grouped systems summary and config file path (auto-named `~/.code-auditor/<project-name>.yaml`) |

On completion the config is saved, source directory classifications and systems are stored in the database, and the new project becomes active.

### 4. Run your first scan

Click **"Trigger Scan"** in the dashboard, or from the CLI:

```bash
code-auditor scan --type incremental
```

---

## Project management

Code Auditor supports multiple projects. Each project is stored as its own YAML file named after the project:

```
~/.code-auditor/
  my-game.yaml          # project "MyGame"
  rts-prototype.yaml    # project "RTS Prototype"
  .active               # pointer to the currently active project config
```

The **Active Project** card on the Settings page shows the current project and lets you switch between any discovered project YAML files. Switching immediately reloads config and database in-memory — all dashboard views (findings, scans, batches) are scoped to the active project at all times.

### Config YAML structure

```yaml
repo_path: /path/to/your/game/repo

build:
  ue_installation_dir: "/Users/Shared/Epic Games/UE_5.4"   # editor cmd auto-derived
  ue_editor_cmd: ""                                         # explicit override (optional)
  project_file: "/path/to/MyGame.uproject"
  build_timeout_seconds: 1800
  test_timeout_seconds: 600

scan_schedule:
  incremental_interval_hours: 4
  rotation_enabled: false
  rotation_interval_hours: 24

notifications:
  desktop: true
  # slack_webhook: "https://hooks.slack.com/services/..."
  # discord_webhook: "https://discord.com/api/webhooks/..."

claude_fast_mode: true
min_confidence: "medium"
file_extensions: [".h", ".cpp"]
```

> **Systems are stored in the database** — source directory classifications and gameplay module systems are managed through the web dashboard or setup wizard and stored in `~/.code-auditor/<project>/auditor.db`. They are not written to the YAML config.

## Usage

### Dashboard

The web dashboard runs locally on port 8420 and provides:

- **Dashboard home** (`/`) — Overview stats (scoped to active project), severity breakdown, quick actions
- **Findings list** (`/findings`) — Filterable table of all findings with approve/reject controls
- **Finding detail** (`/findings/{id}`) — Full view with code snippet, diff preview, test case, and reasoning
- **Batch status** (`/batches/{id}`) — Real-time pipeline progress (applying → building → testing → verified)
- **Scan history** (`/scans`) — Past scans with file counts and finding counts
- **Settings** (`/settings`) — Active project card, project switcher, config health, source directory classification (Active/Ignored), system management grouped by source directory, setup wizard

All pages are scoped to the active project. The sidebar shows the active project name on every page.

### CLI commands

```bash
# Initialize config
code-auditor init /path/to/repo

# Start dashboard server
code-auditor serve
code-auditor serve --host 0.0.0.0 --port 9000

# Run scans directly
code-auditor scan --type incremental
code-auditor scan --type full --system Combat
```

### Scanning

**Incremental scan**: Analyzes only files changed since the last scan (`git diff`). This is the default and most efficient mode.

**Full system scan**: Analyzes all files in a specific game system. Use for deeper coverage.

**Rotation scan**: Automatically cycles through game systems, one per scheduled interval. Enable in config with `rotation_enabled: true`.

### Review workflow

1. Findings appear in the dashboard with severity badges (critical/high/medium/low/info)
2. Each finding includes:
   - Description of the problem
   - The problematic code
   - A suggested fix with unified diff
   - A UE Automation Test that verifies the fix
   - Reasoning for why it was flagged
3. Click **Approve** or **Reject** for each finding
4. When ready, click **"Apply N approved findings"** to create a batch

### Batch apply pipeline

When you apply approved findings:

1. All approved findings are collected into a batch
2. Claude generates a single unified patch that applies all fixes together (avoids ordering conflicts)
3. The patch is applied to a new git branch
4. UE build runs to verify compilation
5. Generated tests execute to verify correctness
6. On success: commit created, PR opened, notification sent
7. On failure: branch deleted, findings marked as failed with build/test logs visible in dashboard

The pipeline runs asynchronously — the dashboard auto-refreshes to show progress.

## Architecture

```
Scan Scheduler --> Analysis Engine --> Findings Store (SQLite)
   (APScheduler)    (Claude Code CLI)          |
                                               v
                  Apply Pipeline  <--  Web Dashboard
                  (git + UBT + tests)  (FastAPI + Jinja2/Tailwind)
                        |
                        v
                  Notification (closure)
```

### Key design decisions

- **System-based scanning**: Code is split by game system (Combat, Character, AI, etc.), not by finding category. One comprehensive prompt per system finds all issue types in a single pass. Fewer CLI calls, better contextual understanding.

- **Agent mode scanning**: Claude runs as an autonomous agent (`claude -p - --output-format json --dangerouslySkipPermissions`) with `cwd` set to the repo root. The prompt gives Claude a list of file paths; Claude reads them itself using the `Read`, `Glob`, and `Grep` tools. This avoids embedding file contents in the prompt, keeping prompts small and giving Claude the freedom to explore related files.

- **Claude Code CLI**: Uses `claude -p` via subprocess. No separate API key needed — runs on your existing Claude Max subscription at no extra cost.

- **Pydantic validation**: Every Claude response is validated against a strict schema. Failed validations trigger automatic retries (max 2).

- **Batch apply**: Approved findings are applied together in one patch, not sequentially. Claude produces a unified diff against the current code, so there are no ordering or conflict issues between findings.

- **Longest-prefix ownership**: When systems have overlapping path prefixes, the system with the longest matching prefix owns the file. This lets you define coarse systems like `Campaign/` and fine-grained sub-systems like `Campaign/AI/` without duplication.

- **Test generation at scan time**: Each finding includes a UE Automation Test generated alongside the fix. Tests are visible during review so you can assess quality before approving.

### Project structure

```
src/auditor/
  main.py              # FastAPI app + CLI entry point
  config.py            # YAML config management
  models.py            # Pydantic models (Finding, Scan, Batch)
  database.py          # SQLite schema + queries
  scanner/
    scheduler.py       # Scan orchestration + rotation
    incremental.py     # Git-diff scanning
    chunker.py         # System-level code assembly + chunking
  analysis/
    engine.py          # Claude Code CLI calls + validation
    prompts.py         # Prompt templates + UE reference sheet
    schemas.py         # JSON output schemas
  pipeline/
    batch.py           # Full batch pipeline orchestrator
    applicator.py      # Patch application (with retry)
    test_writer.py     # Write UE test files to project
    builder.py         # UE build trigger
    test_runner.py     # UE Automation Test execution
    git_ops.py         # Branch/commit/PR operations
    notifier.py        # Desktop/Slack/Discord notifications
  web/
    routes.py          # FastAPI routes
    templates/         # Jinja2 HTML templates
    static/            # JS/CSS
```

### Data storage

Each project has its own SQLite database derived from the active config path:

```
~/.code-auditor/
  my-game.yaml           # project config
  my-game/auditor.db     # project database
  rts-prototype.yaml
  rts-prototype/auditor.db
  .active                # active project pointer
  logs/                  # Claude call logs
```

The database has five main tables:

- **findings** — Individual code issues with severity, category, fix diff, test code, and lifecycle status
- **scans** — Scan metadata (type, system, files scanned, findings count)
- **batches** — Batch apply records (status, branch, build log, test log, PR URL)
- **source_dirs** — Source directory classifications (`active` / `ignored`), auto-populated by the scanner and manageable from the Settings page
- **systems** — Gameplay module system definitions (name, paths, `source_dir` foreign reference to the parent active source directory, per-system overrides)

## Configuration reference

### Global settings

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `repo_path` | string | `""` | Path to the game repository |
| `build.ue_installation_dir` | string | `""` | UE install root — editor cmd derived from this |
| `build.ue_editor_cmd` | string | `""` | Explicit path to UnrealEditor-Cmd (overrides `ue_installation_dir`) |
| `build.project_file` | string | `""` | Path to .uproject file |
| `build.build_timeout_seconds` | int | 1800 | Max build time (30 min) |
| `build.test_timeout_seconds` | int | 600 | Max test time (10 min) |
| `scan_schedule.incremental_interval_hours` | int | 4 | Hours between incremental scans (0 = disabled) |
| `scan_schedule.rotation_enabled` | bool | false | Enable full-system rotation scans |
| `scan_schedule.rotation_interval_hours` | int | 24 | Hours between rotation scans |
| `notifications.desktop` | bool | true | macOS/Linux desktop notifications |
| `notifications.slack_webhook` | string | null | Slack incoming webhook URL |
| `notifications.discord_webhook` | string | null | Discord webhook URL |
| `claude_fast_mode` | bool | true | Pass `fast=True` to the analysis engine |
| `min_confidence` | string | `"medium"` | Minimum confidence to surface (`"low"`, `"medium"`, `"high"`) |
| `file_extensions` | list | `[".h", ".cpp"]` | File types to scan |
| `data_dir` | string | `"~/.code-auditor"` | Database and log storage |

### System definition fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | yes | Display name for the system |
| `source_dir` | string | yes | Path of the parent active source directory this system belongs to |
| `paths` | list[string] | yes | Repo-relative paths to scan (trailing `/` recommended) |
| `min_confidence` | string | no | Override global `min_confidence` for this system only |
| `claude_fast_mode` | bool | no | Override global `claude_fast_mode` for this system only |
| `file_extensions` | list[string] | no | Override global `file_extensions` for this system only |

> Files are assigned to the system whose path prefix most specifically matches. If `Campaign/` and `Campaign/AI/` are both configured, files under `Campaign/AI/` belong to the more specific system.

## Finding categories

| Category | What it catches |
|----------|----------------|
| bug | Logic errors, null derefs, race conditions, use-after-free |
| performance | Unnecessary Tick, allocations in hot paths, TArray copies |
| ue-antipattern | Missing UPROPERTY on UObject*, raw `new`, FName in loops |
| memory | Dangling pointers, missing cleanup, circular references |
| modern-cpp | C-style casts, missing constexpr, raw owning pointers |
| readability | Unclear naming, overly complex logic, missing const |

## Troubleshooting

**"No project configured"**: Open the dashboard — you will be redirected to Settings. Click **"+ Setup New Project"** to run the wizard.

**"Claude CLI not found"**: Ensure `claude` is on your PATH. Install Claude Code if needed.

**Scan returns no findings**: Check that your system paths in config actually contain `.h` and `.cpp` files. Run `code-auditor scan --type full --system SystemName` to scan a specific system.

**Build fails after applying**: Check the build log in the batch detail view. The branch is automatically cleaned up on failure. You can unapprove specific findings and retry.

**Tests fail**: Check the test log in the batch detail view. Generated tests may need adjustment — reject findings with bad tests and re-run the scan.

**"No previous scan commit found"**: Normal on first run. The scanner uses HEAD~20 as baseline. Subsequent scans will diff from the last scan point.
