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

### 2. Initialize config

```bash
code-auditor init /path/to/your/game/repo
```

This creates `~/.code-auditor/config.yaml`. Edit it to match your project.

### 3. Configure your game systems

Edit `~/.code-auditor/config.yaml`:

```yaml
repo_path: /path/to/your/game/repo

systems:
  - name: "Combat"
    paths:
      - "Source/MyGame/Weapons/"
      - "Source/MyGame/Damage/"
      - "Source/MyGame/Projectiles/"
  - name: "Character"
    paths:
      - "Source/MyGame/Character/"
      - "Source/MyGame/Animation/"
  - name: "AI"
    paths:
      - "Source/MyGame/AI/"
      - "Source/MyGame/BehaviorTree/"
  - name: "UI"
    paths:
      - "Source/MyGame/UI/"

build:
  ue_editor_cmd: "/path/to/UnrealEditor-Cmd"
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

### 4. Start the dashboard

```bash
code-auditor serve
```

Open http://127.0.0.1:8420 in your browser.

### 5. Run your first scan

Click "Trigger Scan" in the dashboard, or from the CLI:

```bash
code-auditor scan --type incremental
```

## Usage

### Dashboard

The web dashboard runs locally on port 8420 and provides:

- **Dashboard home** (`/`) — Overview stats, severity breakdown, quick actions
- **Findings list** (`/findings`) — Filterable table of all findings with approve/reject controls
- **Finding detail** (`/findings/{id}`) — Full view with code snippet, diff preview, test case, and reasoning
- **Batch status** (`/batches/{id}`) — Real-time pipeline progress (applying -> building -> testing -> verified)
- **Scan history** (`/scans`) — Past scans with file counts and finding counts

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

- **Claude Code CLI**: Uses `claude -p --output-format json` via subprocess. No separate API key needed — runs on existing Claude Max subscription at no extra cost.

- **Pydantic validation**: Every Claude response is validated against a strict schema. Failed validations trigger automatic retries (max 2).

- **Batch apply**: Approved findings are applied together in one patch, not sequentially. Claude produces a unified diff against the current code, so there are no ordering or conflict issues between findings.

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

SQLite database at `~/.code-auditor/auditor.db` with three main tables:

- **findings** — Individual code issues with severity, category, fix diff, test code, and lifecycle status
- **scans** — Scan metadata (type, system, files scanned, findings count)
- **batches** — Batch apply records (status, branch, build log, test log, PR URL)

## Configuration reference

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `repo_path` | string | required | Path to the game repository |
| `systems` | list | required | Game system definitions (name + paths) |
| `build.ue_editor_cmd` | string | required | Path to UnrealEditor-Cmd |
| `build.project_file` | string | required | Path to .uproject file |
| `build.build_timeout_seconds` | int | 1800 | Max build time (30 min) |
| `build.test_timeout_seconds` | int | 600 | Max test time (10 min) |
| `scan_schedule.incremental_interval_hours` | int | 4 | Hours between incremental scans |
| `scan_schedule.rotation_enabled` | bool | false | Enable full-system rotation |
| `scan_schedule.rotation_interval_hours` | int | 24 | Hours between rotation scans |
| `notifications.desktop` | bool | true | macOS/Linux desktop notifications |
| `notifications.slack_webhook` | string | null | Slack incoming webhook URL |
| `notifications.discord_webhook` | string | null | Discord webhook URL |
| `claude_fast_mode` | bool | true | Use `--fast` flag for bulk scans |
| `min_confidence` | string | "medium" | Minimum confidence to surface |
| `file_extensions` | list | [".h", ".cpp"] | File types to scan |
| `data_dir` | string | "~/.code-auditor" | Database and config storage |

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

**"Config file not found"**: Run `code-auditor init /path/to/repo` first.

**"Claude CLI not found"**: Ensure `claude` is on your PATH. Install Claude Code if needed.

**Scan returns no findings**: Check that your system paths in config actually contain `.h` and `.cpp` files. Run `code-auditor scan --type full --system SystemName` to scan a specific system.

**Build fails after applying**: Check the build log in the batch detail view. The branch is automatically cleaned up on failure. You can unapprove specific findings and retry.

**Tests fail**: Check the test log in the batch detail view. Generated tests may need adjustment — reject findings with bad tests and re-run the scan.

**"No previous scan commit found"**: Normal on first run. The scanner uses HEAD~20 as baseline. Subsequent scans will diff from the last scan point.
