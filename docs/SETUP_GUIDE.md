# Nytwatch -- Setup Guide

A complete, step-by-step guide to installing, configuring, and running the Nytwatch agent. Written to be followed by a human or an AI agent with zero ambiguity.

---

## Table of Contents

1. [Prerequisites](#1-prerequisites)
2. [Installation](#2-installation)
3. [Configuration](#3-configuration)
4. [First Run](#4-first-run)
5. [Reviewing Findings](#5-reviewing-findings)
6. [Applying Fixes](#6-applying-fixes)
7. [Settings](#7-settings)
8. [Scheduling](#8-scheduling)
9. [Export](#9-export)
10. [Notifications](#10-notifications)
11. [CLI Reference](#11-cli-reference)
12. [Troubleshooting](#12-troubleshooting)
13. [AI Agent Setup Script](#13-ai-agent-setup-script)

---

## 1. Prerequisites

### Operating System

| OS | Supported | Notes |
|---|---|---|
| macOS (12+) | Yes | Primary development target. Desktop notifications via `osascript`. |
| Linux (Ubuntu 22.04+, Fedora 38+) | Yes | Desktop notifications via `notify-send` (`libnotify`). |
| Windows | Partial | Python tooling works. UE build paths differ. Desktop notifications not implemented. |

### Python 3.11+

Required. The project uses `from __future__ import annotations`, Pydantic v2, and `watchdog` for filesystem monitoring.

```bash
python3 --version
# Expected: Python 3.11.x or higher (3.12, 3.13 also work)
```

**If not installed:**

| OS | Install command |
|---|---|
| macOS | `brew install python@3.12` |
| Ubuntu/Debian | `sudo apt install python3 python3-pip python3-venv` |
| Windows | Download from https://www.python.org/downloads/ and check "Add to PATH" |

### Git

```bash
git --version
# Expected: git version 2.x.x
```

The scanner uses `git rev-parse`, `git diff --name-only`, and `git apply`. Any Git 2.x works.

### GitHub CLI (`gh`)

Required for PR creation during the batch apply pipeline.

```bash
gh --version
# Expected: gh version 2.x.x
```

**If not installed:**

| OS | Install command |
|---|---|
| macOS | `brew install gh` |
| Ubuntu/Debian | See https://github.com/cli/cli/blob/trunk/docs/install_linux.md |
| Windows | `winget install GitHub.cli` |

**Authenticate after install:**

```bash
gh auth login
# Follow the interactive flow. Select GitHub.com, HTTPS, and authenticate via browser.
```

**Verify authentication:**

```bash
gh auth status
# Expected: Logged in to github.com as <your-username>
```

### Claude Code CLI (`claude`)

The analysis engine calls `claude -p - --output-format json` via subprocess. This requires:

1. Claude Code CLI installed
2. Authenticated with a **Claude Max subscription** (the CLI runs on your subscription, no separate API key needed)

**Install:**

```bash
npm install -g @anthropic-ai/claude-code
```

**Authenticate:**

```bash
claude
# First launch opens a browser for authentication. Complete the sign-in flow.
```

**Verify:**

```bash
claude --version
# Expected: a version string
echo "Say OK" | claude -p - --output-format json --dangerouslySkipPermissions
# Expected: JSON output containing "OK" in the result field
```

If the `claude` command is not found after install, ensure your npm global bin directory is on your PATH:

```bash
# Find npm global bin:
npm config get prefix
# Typical: /usr/local (macOS Homebrew) or ~/.npm-global

# Add to PATH if missing (add to ~/.zshrc or ~/.bashrc):
export PATH="$(npm config get prefix)/bin:$PATH"
```

### Unreal Engine

Required only for the build verification and test execution steps of the batch apply pipeline. Scanning and reviewing findings works without UE installed.

**Find UnrealEditor-Cmd on your system:**

| OS | Typical path |
|---|---|
| macOS | `/Users/Shared/Epic Games/UE_5.x/Engine/Binaries/Mac/UnrealEditor-Cmd` |
| Linux | `/home/<user>/UnrealEngine/Engine/Binaries/Linux/UnrealEditor-Cmd` |
| Windows | `C:\Program Files\Epic Games\UE_5.x\Engine\Binaries\Win64\UnrealEditor-Cmd.exe` |

Replace `5.x` with your installed version (e.g., `5.4`, `5.5`).

**Verify:**

```bash
# macOS/Linux:
ls -la "/Users/Shared/Epic Games/UE_5.4/Engine/Binaries/Mac/UnrealEditor-Cmd"

# Windows (PowerShell):
Test-Path "C:\Program Files\Epic Games\UE_5.4\Engine\Binaries\Win64\UnrealEditor-Cmd.exe"
```

**Find your .uproject file:**

```bash
# From your game repo root:
ls *.uproject
# Expected: MyGame.uproject (exactly one file)
```

The full absolute path to this file is what goes into `config.yaml`.

---

## 2. Installation

### Clone the repository

```bash
git clone https://github.com/hariharanvelusamy2607/nytwatch.git
cd nytwatch
```

### Install in editable mode

```bash
pip install -e .
```

This installs the `nytwatch` CLI command and all dependencies:

| Dependency | Purpose |
|---|---|
| fastapi | Web dashboard server |
| uvicorn | ASGI server |
| jinja2 | HTML template rendering |
| pydantic | Config and data validation |
| apscheduler | Scheduled scan execution |
| pyyaml | Config file parsing |
| python-multipart | Form handling |
| openpyxl | Excel export |

### Verify installation

```bash
nytwatch --help
```

**Expected output:**

```
usage: nytwatch [-h] {init,serve,scan} ...

Nytwatch

positional arguments:
  {init,serve,scan}
    init             Initialize config file
    serve            Start the dashboard server
    scan             Run a scan immediately

options:
  -h, --help         show this help message and exit
```

If the command is not found, check that your Python scripts directory is on PATH:

```bash
# Find where pip installs scripts:
python3 -m site --user-base
# Add <that-path>/bin to your PATH
```

### Troubleshooting installation

**`pip not found`**: Use `python3 -m pip install -e .` instead.

**Permission errors**: Never use `sudo pip`. Either use a virtual environment or install with `--user`:

```bash
# Option A: Virtual environment (recommended)
python3 -m venv .venv
source .venv/bin/activate  # macOS/Linux
# .venv\Scripts\activate   # Windows
pip install -e .

# Option B: User install
pip install --user -e .
```

**Python version mismatch**: If you have multiple Python versions, be explicit:

```bash
python3.12 -m pip install -e .
```

---

## 3. Configuration

### Option A — Setup Wizard (recommended)

Start the server and open the dashboard:

```bash
nytwatch serve
# Open http://127.0.0.1:8420
```

If no project is configured, the browser redirects automatically to `/settings?setup=1`. Otherwise click **"+ Setup New Project"** on the Settings page.

The wizard runs entirely in the browser — no manual YAML editing required.

**Step 1 — Project Setup**

| Field | Required | Description |
|-------|----------|-------------|
| Project name | Yes | Used as the config filename: `~/.nytwatch/<slug>.yaml` (e.g. "Green Leaf" → `green-leaf.yaml`) |
| Repository path | Yes | Absolute path to your game repo root. Use the **Browse…** button to navigate the filesystem. |
| UE installation directory | No | UE root folder (e.g. `C:\Epic Games\UE_5.4`). Use **Browse…**. `UnrealEditor-Cmd` is located automatically inside `Engine/Binaries/`. |

The **Next** button is disabled until both Project name and Repository path are filled. Clicking it calls `/api/validate-repo` to confirm the path exists and contains a `.git` directory (lightweight check — no filesystem scanning). Auto-detection of source directories runs after validation passes.

**Step 2 — Sources**

These are the top-level source folders in your repository, auto-detected from `.uplugin` files and `*.Build.cs` modules.
Mark each directory as **Active** (contains C++ code to scan) or **Ignored** (skip entirely — no findings generated).
Use the **All Active / All Ignored** bulk toggle to set every directory at once. The bulk toggle is hidden while detection is in progress.
Only Active directories will proceed to the next step.
Click **Re-detect** to refresh if you've changed the repo structure.

**Step 3 — Systems**

Systems categorize gameplay modules within each active source directory. Each system groups related sub-paths that Claude analyses together in one pass — this keeps context focused and Claude's output high-quality.

The view is grouped by active source directory. Each directory has its own **+ Add System** button. Click **✨ Suggest with Claude** to auto-generate systems for all active directories at once. Suggestion runs in agent mode — Claude explores the repo using its own tools with no pre-built directory listing sent in the prompt, and handles UE Public/Private module structure correctly (returns module root paths or explicit `Public/Feature/` + `Private/Feature/` pairs rather than bare `Public/` or `Private/`).

The **folder picker** (path selector) within each system has full UE module awareness: Public/Private banners, auto-promotion of bare `Public/` or `Private/` to the module root, auto-mirroring of feature folders, and root clamping to the source directory.

System names should reflect logical gameplay areas (e.g. "Combat", "AI", "Character"). A system must have at least one path. Path overlaps across systems in the same directory are flagged as warnings.

**Step 4 — Build**

| Field | Description |
|-------|-------------|
| `.uproject file` | Absolute path to your `.uproject`. Use **Browse…**. Required only for the batch apply pipeline. |
| Build timeout | Maximum seconds for UE compilation (default 1800). |
| Test timeout | Maximum seconds for UE Automation Tests (default 600). |

**Step 5 — Schedule**

Configure automatic incremental scans and rotation schedules.

**Step 6 — Review**

Shows a grouped systems summary (organized by source directory) and the config file path (auto-named `~/.nytwatch/<project-name>.yaml`, editable). Click **Create Project** to save.

On success, the new project becomes active, the config YAML is written, and source directory classifications and systems are stored in the database.

---

### Option B — CLI init (advanced / AI agent use)

```bash
nytwatch init /absolute/path/to/your/game/repo
# Optional custom config path:
nytwatch init ~/Projects/DragonRacer --config ~/.nytwatch/dragon-racer.yaml
```

**Expected output:**

```
Config created at: /Users/<you>/.nytwatch/dragon-racer.yaml
Edit the config to define your game systems and UE paths.
```

Then open `~/.nytwatch/dragon-racer.yaml` in your editor. See the annotated example below.

### Complete annotated config YAML

```yaml
# --------------------------------------------------------------------------
# repo_path (REQUIRED for scanning)
# Absolute path to the root of your Unreal Engine game repository.
# This directory must contain a .uproject file and a Source/ directory.
# Leave blank only if you are using the web setup wizard on first run.
# --------------------------------------------------------------------------
repo_path: /Users/hari/Projects/DragonRacer

# --------------------------------------------------------------------------
# systems — stored in the database, not in this YAML
# Use the Setup Wizard or the Systems section on the Settings page to
# manage systems. They are organized under their parent Active source
# directory and stored in ~/.nytwatch/<slug>.db (e.g. dragon-racer.db).
# --------------------------------------------------------------------------

# --------------------------------------------------------------------------
# build (REQUIRED for batch apply pipeline)
# Paths to your Unreal Engine editor and project file.
# Not needed for scanning/reviewing -- only for applying fixes.
# --------------------------------------------------------------------------
build:
  # UE installation root directory (used to derive the editor command path).
  # Set this and leave ue_editor_cmd blank to auto-derive.
  # macOS:   /Users/Shared/Epic Games/UE_5.4
  # Linux:   /home/<user>/UnrealEngine
  # Windows: C:\Program Files\Epic Games\UE_5.4
  ue_installation_dir: "/Users/Shared/Epic Games/UE_5.4"

  # Explicit path to UnrealEditor-Cmd (optional — overrides ue_installation_dir).
  # macOS:   /Users/Shared/Epic Games/UE_5.4/Engine/Binaries/Mac/UnrealEditor-Cmd
  # Linux:   /home/<user>/UnrealEngine/Engine/Binaries/Linux/UnrealEditor-Cmd
  # Windows: C:\Program Files\Epic Games\UE_5.4\Engine\Binaries\Win64\UnrealEditor-Cmd.exe
  ue_editor_cmd: ""

  # Absolute path to the .uproject file in your repo.
  project_file: "/Users/hari/Projects/DragonRacer/DragonRacer.uproject"

  # Maximum time (seconds) to wait for UE build. Default: 1800 (30 min).
  build_timeout_seconds: 1800

  # Maximum time (seconds) to wait for UE Automation Tests. Default: 600 (10 min).
  test_timeout_seconds: 600

# --------------------------------------------------------------------------
# scan_schedule
# Controls automatic scanning when the server is running.
# --------------------------------------------------------------------------
scan_schedule:
  # Hours between incremental scans (git-diff based).
  # Set to 0 to disable automatic scanning.
  # Default: 4
  incremental_interval_hours: 4

  # Enable rotation scanning: cycles through each system in order,
  # doing a full scan of one system per interval.
  # Default: false
  rotation_enabled: false

  # Hours between rotation scans. Only used when rotation_enabled is true.
  # Default: 24
  rotation_interval_hours: 24

# --------------------------------------------------------------------------
# notifications
# Where to send notifications when a batch pipeline completes.
# --------------------------------------------------------------------------
notifications:
  # macOS: uses osascript. Linux: uses notify-send.
  desktop: true

  # Slack incoming webhook URL. Uncomment to enable.
  # slack_webhook: "https://hooks.slack.com/services/T.../B.../xxx"

  # Discord webhook URL. Uncomment to enable.
  # discord_webhook: "https://discord.com/api/webhooks/123/abc"

# --------------------------------------------------------------------------
# data_dir
# Where the SQLite database, logs, and Claude prompt/response logs are stored.
# Default: ~/.nytwatch
# --------------------------------------------------------------------------
data_dir: "~/.nytwatch"

# --------------------------------------------------------------------------
# claude_fast_mode
# Passed as the fast= parameter to the analysis engine.
# When true (default), uses the standard/fast Claude model for scans.
# When false, uses a more thorough model pass (slower, higher quality).
# Can be overridden per-system via systems[].claude_fast_mode.
# Default: true
# --------------------------------------------------------------------------
claude_fast_mode: true

# --------------------------------------------------------------------------
# min_confidence
# Minimum confidence level to surface findings.
# Values: "high", "medium", "low"
# Default: "medium"
# --------------------------------------------------------------------------
min_confidence: "medium"

# --------------------------------------------------------------------------
# file_extensions
# Which file types to include in scans.
# Default: [".h", ".cpp"]
# --------------------------------------------------------------------------
file_extensions:
  - ".h"
  - ".cpp"
```

### How to define game systems

Systems are the core organizational unit. Each system should represent a logical gameplay area within an active source directory.

**Where to manage systems**

Systems are managed in the dashboard (**Settings page > Systems section**) or through the Setup Wizard (Step 3). They are not written to the YAML config — they live in the project database (`~/.nytwatch/<slug>.db`, e.g. `~/.nytwatch/greenleaf.db`).

**Two-level hierarchy**

Each system belongs to one active source directory. Within that directory, systems group related sub-paths that Claude analyses together in one pass:

```
Active Source Directory: Source/MyGame/
  System: Combat       → Source/MyGame/Weapons/, Source/MyGame/Damage/
  System: Character    → Source/MyGame/Character/, Source/MyGame/Animation/
  System: AI           → Source/MyGame/AI/

Active Source Directory: Plugins/MyPlugin/
  System: MyPlugin     → Plugins/MyPlugin/Source/
```

**Ownership rules**

- Paths are relative to `repo_path`
- Each path should end with `/` (trailing slash recommended)
- A file belongs to the system whose path prefix most specifically matches it (longest prefix wins, not first match)
- Files not matching any system go to `"__uncategorized"` and are skipped

**Hierarchical systems** (sub-systems supported via longest-prefix ownership):

Within the same source directory, you can define a coarse system alongside a fine-grained sub-system. Files in `Source/MyGame/Campaign/AI/` belong exclusively to `Campaign-AI`. `Campaign-Core` gets everything else under `Campaign/`. No duplication needed.

**Avoid overly broad paths** — a single system covering the entire source tree loses context quality. Aim for cohesive, related files per system.

**Common UE project layouts:**

```
MyGame/
  MyGame.uproject
  Source/
    MyGame/                  # Project module root
      MyGame.Build.cs
      MyGame.h
      Private/               # .cpp files
      Public/                # .h files
      Character/
      Weapons/
      AI/
      UI/
  Plugins/
    MyPlugin/
      MyPlugin.uplugin
      Source/
        MyPlugin/
          Private/
          Public/
```

**UE Public/Private module structure**

When your module uses `Public/` and `Private/` subdirectories, prefer the module root path over splitting into two paths:

```
# Preferred — module root covers both Public/ and Private/:
Source/MyGame/Combat/

# Use feature splits only when breaking the module into logical sub-areas:
Source/MyGame/Combat/Public/Weapons/
Source/MyGame/Combat/Private/Weapons/
```

Rules:
- Never configure a bare `Source/MyGame/Public/` or `Source/MyGame/Private/` path without a feature subfolder.
- If splitting by feature, always include both `Public/Feature/` and `Private/Feature/` so neither is missed.
- Auto-promote bare `Public/` or `Private/` to the module root when no feature split is needed.

```yaml
# Correct — flat module with Public/Private:
- name: "Character"
  paths:
    - "Source/MyGame/Character/"      # covers both Public/ and Private/ inside

# Correct — feature split:
- name: "Character-Weapons"
  paths:
    - "Source/MyGame/Character/Public/Weapons/"
    - "Source/MyGame/Character/Private/Weapons/"
```

### How to find the correct UE editor command path

**macOS:**

```bash
# List installed UE versions:
ls "/Users/Shared/Epic Games/"
# Expected: UE_5.3  UE_5.4  UE_5.5  etc.

# Construct the path:
# /Users/Shared/Epic Games/UE_<version>/Engine/Binaries/Mac/UnrealEditor-Cmd

# Verify it exists:
ls "/Users/Shared/Epic Games/UE_5.4/Engine/Binaries/Mac/UnrealEditor-Cmd"
```

**Linux:**

```bash
# Common locations:
ls ~/UnrealEngine/Engine/Binaries/Linux/UnrealEditor-Cmd
# or
ls /opt/UnrealEngine/Engine/Binaries/Linux/UnrealEditor-Cmd
```

**Windows:**

```powershell
# Common location:
dir "C:\Program Files\Epic Games\UE_5.4\Engine\Binaries\Win64\UnrealEditor-Cmd.exe"
```

### How to find your .uproject file path

```bash
# From your game repo root:
find /path/to/your/game/repo -maxdepth 1 -name "*.uproject"
# Expected: /path/to/your/game/repo/MyGame.uproject
```

Use the full absolute path in `config.yaml`.

---

## 4. First Run

### Start the server

```bash
nytwatch serve
```

**Expected log output (with a configured project):**

```
2026-04-02 10:00:00 [INFO] auditor: Scheduled incremental scans every 4 hours
2026-04-02 10:00:00 [INFO] auditor: Starting Nytwatch on http://127.0.0.1:8420
INFO:     Uvicorn running on http://127.0.0.1:8420 (Press CTRL+C to quit)
```

If no project is configured yet, the scheduler lines are absent and you are redirected to the setup wizard.

### Verify it is running

Open http://127.0.0.1:8420 in your browser. You should see:

- The dashboard home page
- Stats showing 0 findings, 0 scans
- A "Trigger Scan" button

**From the command line:**

```bash
curl -s http://127.0.0.1:8420/api/stats | python3 -m json.tool
```

**Expected:**

```json
{
    "status_counts": {},
    "severity_counts": {},
    "total_scans": 0,
    "total_batches": 0,
    "last_scan": null,
    "pending_count": 0,
    "approved_count": 0
}
```

### Trigger first scan

**Via the dashboard:** Click the "Trigger Scan" button on the home page. The scan runs asynchronously in a background thread.

**Via CLI (in a separate terminal):**

```bash
nytwatch scan --type incremental
```

Or scan a specific system:

```bash
nytwatch scan --type full --system Combat
```

### What to expect

**Duration:** Depends on the number of files and systems. A typical incremental scan with 10-30 changed files takes 1-3 minutes. A full system scan of 50+ files may take 3-10 minutes per system.

**Log output during scan:**

```
2026-04-02 10:05:00 [INFO] auditor.scanner.source_detector: Auto-classified 'Source/DragonRacer' as 'project' (heuristic)
2026-04-02 10:05:00 [INFO] auditor.scanner.source_detector: Auto-classified 'Plugins/SomePlugin' as 'plugin' (heuristic)
2026-04-02 10:05:00 [INFO] auditor.scanner.incremental: Found 15 changed files since a1b2c3d4
2026-04-02 10:05:00 [INFO] auditor.scanner.chunker: Collected 12 files for system 'Combat'
2026-04-02 10:05:01 [INFO] auditor.scanner.incremental: Analyzing system 'Combat' chunk 1/1 (12 files)
2026-04-02 10:05:01 [INFO] auditor.analysis.engine: Claude call abc12345: prompt_len=45000, timeout=600s
2026-04-02 10:06:30 [INFO] auditor.analysis.engine: Claude call abc12345: completed in 89.2s, response_len=12000
2026-04-02 10:06:30 [INFO] auditor.analysis.engine: Scan complete for 'Combat': 7 findings
2026-04-02 10:06:30 [INFO] auditor.scanner.incremental: Incremental scan a1b2c3d4 completed: 15 files, 7 findings, 0/1 systems failed
```

**First scan baseline:** On the very first incremental scan, there is no previous commit to diff against. The scanner uses `HEAD~20` as the baseline, covering the last 20 commits.

### Verify findings appear

After the scan completes:

1. Refresh the dashboard at http://127.0.0.1:8420
2. The stats should show finding counts by severity
3. Navigate to http://127.0.0.1:8420/findings to see the findings list

**From the CLI:**

```bash
curl -s http://127.0.0.1:8420/api/stats | python3 -m json.tool
```

You should see non-zero values in `pending_count`.

---

## 5. Reviewing Findings

### Navigate to the findings list

Open http://127.0.0.1:8420/findings in your browser.

### Using filters

The findings list supports filtering by these parameters (via URL query params or the dashboard UI):

| Filter | Values | URL example |
|---|---|---|
| status | `pending`, `approved`, `rejected`, `applied`, `verified`, `failed`, `superseded` | `/findings?status=pending` |
| severity | `critical`, `high`, `medium`, `low`, `info` | `/findings?severity=high` |
| category | `bug`, `performance`, `ue-antipattern`, `modern-cpp`, `memory`, `readability` | `/findings?category=bug` |
| confidence | `high`, `medium`, `low` | `/findings?confidence=high` |
| source | `project`, `plugin` | `/findings?source=project` |
| file_path | Any substring of the file path | `/findings?file_path=Weapons` |

Combine multiple filters:

```
/findings?severity=high&category=bug&source=project
```

Findings are sorted by severity (critical first) then by creation date (newest first).

### Reading a finding detail page

Click any finding title to open its detail page at `/findings/{id}`. Each finding contains:

| Field | Description |
|---|---|
| **Title** | One-line summary of the issue |
| **Severity** | `critical` / `high` / `medium` / `low` / `info` |
| **Category** | `bug` / `performance` / `ue-antipattern` / `modern-cpp` / `memory` / `readability` |
| **Confidence** | `high` / `medium` / `low` -- how certain the agent is this is a real issue |
| **Source** | `project` (your game code) or `plugin` (third-party/plugin code) |
| **File path** | Relative path from repo root (e.g., `Source/MyGame/Weapons/WeaponBase.cpp`) |
| **Line range** | Approximate start and end line numbers |
| **Code snippet** | The exact problematic code, verbatim from the source |
| **Suggested fix** | Corrected code or description of the fix |
| **Fix diff** | Unified diff format showing the change (if auto-fixable) |
| **Test case** | A UE Automation Test that verifies the fix works |
| **Test description** | One-line summary of what the test validates |
| **Reasoning** | Detailed explanation of why this is an issue and why the fix is correct |

### Approving and rejecting findings

On each finding (list or detail view):

- **Approve**: Marks the finding as ready for batch application. Only `pending` and `rejected` findings can be approved.
- **Reject**: Dismisses the finding. Only `pending` and `approved` findings can be rejected.

The approve/reject actions are immediate and update the finding status in the database.

### What "source: project" vs "source: plugin" means

When the server starts a scan, it runs **source detection** on the repository. This classifies directories as:

| Source type | Meaning |
|---|---|
| `project` | First-party game code. Your team wrote this. Findings here are actionable. |
| `plugin` | Third-party or reusable plugin code. Usually under `Plugins/` or contains `.uplugin`. Findings here may not be yours to fix. |
| `ignored` | Directories with no C++ code, or UE-generated directories (Intermediate, Saved, Binaries, etc.). |

Classification happens in two layers:
1. **Heuristic** (deterministic): Directories under `Plugins/` with `.uplugin` files are plugins. Directories under `Source/` matching the project name are project code.
2. **AI fallback**: Ambiguous directories are sent to Claude for classification.

You can override any classification in the Settings page.

---

## 6. Applying Fixes

### Batch-apply approved findings

1. Navigate to the findings list: http://127.0.0.1:8420/findings
2. Approve all findings you want to apply
3. Click the **"Apply N approved findings"** button (shown when approved_count > 0)
4. A batch is created and the pipeline starts in a background thread

**Via API:**

```bash
curl -X POST http://127.0.0.1:8420/batch/apply
# Response: {"ok": true, "batch_id": "abc123def456"}
```

### What happens during the pipeline

The batch pipeline runs these steps sequentially:

| Step | Status shown | What happens |
|---|---|---|
| 1. Prepare | `applying` | Stashes local changes, creates branch `auditor/batch-<id>` off `main` |
| 2. Collect | `applying` | Reads current file contents for all affected files |
| 3. Patch | `applying` | Claude generates a unified diff for all fixes. Applied via `git apply`. If first attempt fails, retries with error feedback (two-layer approach). |
| 4. Write tests | `applying` | Generated UE Automation Test files are written to `Source/<Project>/Tests/Auditor/` |
| 5. Build | `building` | Runs `UnrealEditor-Cmd <project> -build -platform=<current> -configuration=Development` |
| 6. Test | `testing` | Runs `UnrealEditor-Cmd <project> -ExecCmds="Automation RunTests Auditor" -unattended -nopause -NullRHI -log` |
| 7. Commit + PR | `testing` | Commits all changes, pushes branch, creates PR via `gh pr create` |
| 8. Verify | `verified` | All findings marked as `verified`, batch marked as `verified` |
| 9. Notify | `verified` | Desktop/Slack/Discord notification sent with PR link |
| 10. Cleanup | `verified` | Switches back to `main`, pops stash if needed |

### Monitor batch progress

Navigate to http://127.0.0.1:8420/batches to see all batches.

Click a batch ID to see its detail page at `/batches/{id}`, which shows:

- Current status
- Branch name
- Build log
- Test log
- Commit SHA
- PR URL (when created)
- List of findings in the batch

### What to do if a batch fails

**Build failure:**
1. Check the build log in the batch detail page
2. The branch is automatically deleted and the working tree is restored
3. All findings in the batch are marked as `failed`
4. Common cause: the fix introduced a compilation error
5. Resolution: reject the problematic finding(s), re-scan, approve the corrected finding, and retry

**Test failure:**
1. Check the test log in the batch detail page
2. The branch and test files are cleaned up
3. All findings are marked as `failed`
4. Common cause: the generated test has incorrect assertions
5. Resolution: reject findings with bad tests, re-scan for improved tests

**Patch application failure:**
1. Build log will show "Patch apply failed" with the error
2. Common cause: the code has changed since the scan (line numbers shifted)
3. Resolution: re-run a scan to get fresh findings based on current code

### Review and merge the created PR

1. The PR URL is shown in the batch detail page and in the notification
2. Open the PR on GitHub
3. Review the diff -- all changes are in a single commit
4. The PR body lists every finding that was resolved, with severity tags
5. Merge at your convenience (squash merge recommended)

---

## 7. Settings

Navigate to http://127.0.0.1:8420/settings.

### Active Project card

Shows the currently active project's repository path and config file path. The **Switch to** dropdown lists all discovered project YAMLs in `~/.nytwatch/`. Switching instantly reloads config and database — all dashboard pages reflect the new project immediately.

The sidebar on every page shows the active project name. When no project is configured, a red "No project configured" badge links to the setup wizard.

### Config Health card

Displays:
- Whether the repo path and system paths exist on disk
- Any configuration errors (missing paths, empty system names, path overlaps)
- Database size (the active project's own `.db` file, e.g. `greenleaf.db`)
- Last scanned commit hash

Use **Repair Config** to re-save the active config with all Pydantic defaults filled in (useful after manual YAML edits that leave optional fields missing).

### Source Directory Classification

Shows all source directories classified as **Project**, **Plugin**, or **Ignored**. These are auto-populated when the setup wizard creates a project (based on the type selector in step 2). You can override any classification manually.

| Type | Effect |
|---|---|
| `project` | Findings from these directories get `source: project`. Primary actionable findings. |
| `plugin` | Findings from these directories get `source: plugin`. Awareness only — typically not your code to fix. |
| `ignored` | Directories are skipped entirely during scanning. No findings generated. |

**Change a classification via the dashboard:** Use the dropdown next to each directory entry.

**Change a classification via API:**

```bash
# Reclassify a directory:
curl -X POST http://127.0.0.1:8420/settings/source-dirs \
  -H "Content-Type: application/json" \
  -d '{"path": "Plugins/MyInternalPlugin", "source_type": "project"}'

# Delete a classification:
curl -X DELETE http://127.0.0.1:8420/settings/source-dirs \
  -H "Content-Type: application/json" \
  -d '{"path": "Plugins/MyInternalPlugin"}'
```

**Add a custom directory:**

```bash
curl -X POST http://127.0.0.1:8420/settings/source-dirs \
  -H "Content-Type: application/json" \
  -d '{"path": "Source/ThirdPartyCode", "source_type": "plugin"}'
```

User-set classifications are never overwritten by auto-detection. They persist across scans.

### Setup Wizard

Click **"+ Setup New Project"** to open the wizard and configure an additional project. See [Section 3](#3-configuration) for a full description of each step.

---

## 8. Scheduling

### How automatic scans work

When the server starts (`nytwatch serve`) with a configured project (`repo_path` non-empty), it sets up APScheduler with background jobs:

1. **Incremental scan**: Runs every `incremental_interval_hours` (default: 4). Only analyzes files changed since the last scan via `git diff`.
2. **Rotation scan** (optional): Runs every `rotation_interval_hours` (default: 24). Cycles through game systems in order, doing a full scan of one system per interval.

The scheduler is not started when no project is configured (wizard-only mode). No "scan ready" notifications are sent until a project is set up.

The scheduler runs as a background thread inside the server process.

### Configure scan intervals

Edit `~/.nytwatch/config.yaml`:

```yaml
scan_schedule:
  incremental_interval_hours: 2   # Scan every 2 hours
  rotation_enabled: true           # Enable rotation scans
  rotation_interval_hours: 12      # Full system scan every 12 hours
```

Set `incremental_interval_hours: 0` to disable automatic incremental scans entirely.

Restart the server after changing the config:

```bash
# Stop the server (Ctrl+C), then:
nytwatch serve
```

### How rotation scanning works

Rotation scanning cycles through your defined systems in order. With 4 systems and a 24-hour interval:

| Day | System scanned |
|---|---|
| Day 1 | Combat (index 0) |
| Day 2 | Character (index 1) |
| Day 3 | AI (index 2) |
| Day 4 | UI (index 3) |
| Day 5 | Combat (index 0, wraps around) |

The current rotation index is stored in the database (`config` table, key `rotation_index`).

### Run as a background service

**macOS (launchd):**

Create `~/Library/LaunchAgents/com.nytwatch.serve.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.nytwatch.serve</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/local/bin/nytwatch</string>
        <string>serve</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/tmp/nytwatch-stdout.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/nytwatch-stderr.log</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/usr/local/bin:/usr/bin:/bin:/opt/homebrew/bin</string>
    </dict>
</dict>
</plist>
```

Adjust the path to `nytwatch` based on your installation. Find it with `which nytwatch`.

```bash
# Load the service:
launchctl load ~/Library/LaunchAgents/com.nytwatch.serve.plist

# Verify it's running:
launchctl list | grep nytwatch

# Stop the service:
launchctl unload ~/Library/LaunchAgents/com.nytwatch.serve.plist
```

**Linux (systemd):**

Create `~/.config/systemd/user/nytwatch.service`:

```ini
[Unit]
Description=Nytwatch
After=network.target

[Service]
Type=simple
ExecStart=/usr/local/bin/nytwatch serve
Restart=on-failure
RestartSec=10
Environment=PATH=/usr/local/bin:/usr/bin:/bin

[Install]
WantedBy=default.target
```

Adjust the path to `nytwatch` based on your installation.

```bash
# Enable and start:
systemctl --user daemon-reload
systemctl --user enable nytwatch
systemctl --user start nytwatch

# Check status:
systemctl --user status nytwatch

# View logs:
journalctl --user -u nytwatch -f

# Stop:
systemctl --user stop nytwatch
```

---

## 9. Export

### Export findings to Excel

Navigate to http://127.0.0.1:8420/findings and click the "Export" link (or use the direct URL).

**Direct URL:**

```
http://127.0.0.1:8420/findings/export
```

**With filters** (same query params as the findings list):

```
http://127.0.0.1:8420/findings/export?severity=high&status=pending
```

**Via curl:**

```bash
curl -o nytwatch_findings.xlsx "http://127.0.0.1:8420/findings/export"
```

### What the export contains

The Excel file has two sheets:

**Sheet 1: Overview**
- Project name (derived from repo_path)
- Generation timestamp
- Confidence threshold and file extensions
- Configured systems with their paths
- Severity breakdown (counts per severity level)
- Scan history (system, files scanned, findings count, status, date)

**Sheet 2: Findings**
- Columns: Severity, Source, Title, File, Line Start, Line End, Category, Confidence, Status, Description, Suggested Fix, Reasoning, Test Description, Created, Reviewed
- Severity cells are color-coded (red for critical, orange for high, amber for medium, blue for low, gray for info)
- Frozen header row
- Auto-sized columns (capped at 60 chars width)

---

## 10. Notifications

### Desktop notifications

Enabled by default. Uses native OS notification systems:

- **macOS**: `osascript -e 'display notification "..." with title "..."'`
- **Linux**: `notify-send "title" "message"` (requires `libnotify-bin` / `libnotify`)

To disable:

```yaml
notifications:
  desktop: false
```

### Slack webhook

1. Create a Slack Incoming Webhook:
   - Go to https://api.slack.com/apps
   - Create a new app (or use an existing one)
   - Enable "Incoming Webhooks"
   - Add a webhook to a channel
   - Copy the webhook URL

2. Add to config:

```yaml
notifications:
  slack_webhook: "your-slack-incoming-webhook-url-here"
```

**Message format:** Bold title, message body, and a "View PR" link when applicable.

### Discord webhook

1. In your Discord server, go to Channel Settings > Integrations > Webhooks
2. Create a new webhook
3. Copy the webhook URL

4. Add to config:

```yaml
notifications:
  discord_webhook: "https://discord.com/api/webhooks/123456789/abcdefghijklmnop"
```

**Message format:** Bold title (`**...**`), message body, and PR URL on a separate line.

---

## 11. CLI Reference

### `nytwatch init <repo_path>`

Initialize the config file with a starter template.

| Argument/Flag | Required | Default | Description |
|---|---|---|---|
| `repo_path` | Yes | -- | Absolute path to the game repository |
| `--config` | No | `~/.nytwatch/config.yaml` | Custom config file path |

**Creates:** The config file at the specified path with default values. The parent directory is created if it does not exist.

**Idempotent:** No. Overwrites the config file if it already exists.

**Example:**

```bash
nytwatch init ~/Projects/DragonRacer
nytwatch init ~/Projects/DragonRacer --config ./my-config.yaml
```

### `nytwatch serve`

Start the web dashboard and scheduled scan server.

| Flag | Default | Description |
|---|---|---|
| `--config` | `~/.nytwatch/config.yaml` | Path to config file |
| `--host` | `127.0.0.1` | Host to bind to. Use `0.0.0.0` for network access. |
| `--port` | `8420` | Port to bind to |

**Creates:** SQLite database at `~/.nytwatch/<slug>.db` for the active project (only when a project is configured — no DB is created when running in wizard-only mode). Static files directory at `src/nytwatch/web/static/` (if it does not exist).

**Blocks:** The command runs until interrupted (Ctrl+C).

**Examples:**

```bash
nytwatch serve
nytwatch serve --host 0.0.0.0 --port 9000
nytwatch serve --config ~/custom-config.yaml
```

### `nytwatch scan`

Run a scan immediately from the command line (blocks until complete).

| Flag | Default | Description |
|---|---|---|
| `--config` | `~/.nytwatch/config.yaml` | Path to config file |
| `--type` | `incremental` | Scan type: `incremental`, `full`, or `rotation` |
| `--system` | (auto-selected) | System name for `full` scan. Required for `--type full`. Ignored for `incremental`. |

**Scan types:**

| Type | Behavior |
|---|---|
| `incremental` | Diffs against last scan commit. Only analyzes changed `.h`/`.cpp` files. Maps changed files to systems and analyzes each affected system. |
| `full` | Scans ALL files in the specified system (not just changed ones). Use `--system` to specify which. |
| `rotation` | Selects the next system in rotation order and runs a full scan on it. |

**Examples:**

```bash
nytwatch scan
nytwatch scan --type incremental
nytwatch scan --type full --system Combat
nytwatch scan --type rotation
nytwatch scan --config ~/custom-config.yaml --type full --system UI
```

---

## 12. Troubleshooting

### Common errors and solutions

**"Config file not found"**

```
Config file not found: /Users/<you>/.nytwatch/config.yaml
Run 'nytwatch init' or create it manually.
```

Solution: Run `nytwatch init /path/to/repo`.

---

**"Claude CLI not found -- is 'claude' on PATH?"**

The `claude` command is not available in the shell environment.

Solution:

```bash
which claude
# If empty, install it:
npm install -g @anthropic-ai/claude-code
# Then verify:
which claude
```

If installed but not found, add the npm bin directory to PATH (see Prerequisites section).

---

**"No previous scan commit found. Running against HEAD~20 as baseline."**

Normal on first run. This is informational, not an error. The scanner uses the last 20 commits as the initial baseline. Subsequent scans will diff from the actual last scan commit.

---

**"Could not determine baseline commit. Aborting incremental scan."**

The repository has fewer than 20 commits.

Solution: Use a full scan instead:

```bash
nytwatch scan --type full --system <SystemName>
```

---

**Scan returns no findings**

Possible causes:
1. System paths in config don't match actual directory structure. Verify with `ls <repo_path>/<system_path>`.
2. No `.h` or `.cpp` files in the specified paths.
3. No files have changed since the last scan (for incremental scans).
4. Claude analysis returned no issues (the code is clean).

Debug: Check logs at `~/.nytwatch/logs/` -- each Claude call generates `<id>_prompt.txt` and `<id>_response.txt`.

---

**Build fails after applying**

Check the build log in the batch detail view at `/batches/<id>`. Common causes:
- Fix introduced a syntax error
- Fix references an undefined symbol
- Header includes became circular

The branch is automatically deleted on failure. Resolution: reject the problematic finding, re-scan, and retry.

---

**"PR creation failed"**

The `gh` CLI is not authenticated or the repository does not have a remote.

Solution:

```bash
cd /path/to/repo
gh auth status       # Check authentication
git remote -v        # Check remote exists
gh pr create --help  # Verify gh works
```

---

**Validation failed for ScanResult**

Claude returned JSON that didn't match the expected schema. The engine automatically retries up to 2 times.

If persistent, check `~/.nytwatch/logs/<id>_response.txt` for the raw response. Common causes:
- Claude returned commentary outside the JSON
- Missing required fields in the response

---

**Windows backslash paths**

No action needed. All internal path handling automatically converts Windows backslashes (`\`) to forward slashes (`/`). The `auditor.paths.normalize_path()` function handles this at every point where `pathlib.Path.relative_to()` produces OS-specific separators. Git output, database storage, source classification, and system prefix matching all use normalized POSIX-style paths regardless of the host OS.

This means:
- Config paths can use either `Source/MyGame/` or `Source\MyGame\` — both work
- Findings stored on Windows will match correctly when the database is moved to macOS/Linux
- The Settings page directory classifications are portable across platforms

---

### How to check logs

All Claude prompt/response pairs are logged to `~/.nytwatch/logs/`:

```bash
ls ~/.nytwatch/logs/
# Files: <call_id>_prompt.txt, <call_id>_response.txt, <call_id>_timeout.txt
```

Application logs go to stdout/stderr (visible in the terminal running `nytwatch serve`).

To see more detail, check the log files:

```bash
# Latest prompt sent to Claude:
ls -lt ~/.nytwatch/logs/*_prompt.txt | head -1

# Latest response from Claude:
ls -lt ~/.nytwatch/logs/*_response.txt | head -1
```

### How to reset the database

Each project has its own database named after its config file slug (e.g. `greenleaf.yaml` → `greenleaf.db`). Delete the project's DB file to reset it. It will be recreated when the project is next used.

```bash
rm ~/.nytwatch/greenleaf.db
```

This removes ALL findings, scans, batches, source classifications, and the last-scan-commit reference for that project. The next incremental scan will use `HEAD~20` as baseline.

To reset only the source directory classifications (forcing re-detection):

```bash
sqlite3 ~/.nytwatch/greenleaf.db "DELETE FROM source_dirs;"
```

### How to re-run source detection

Source detection runs automatically at the start of every scan. To force it:

1. Clear existing classifications: `sqlite3 ~/.nytwatch/greenleaf.db "DELETE FROM source_dirs;"`
2. Run any scan: `nytwatch scan --type incremental`
3. Check the Settings page to verify classifications

---

## 13. AI Agent Setup Script

The following script automates the entire setup process. An AI agent or human can run it to go from zero to a working Nytwatch instance.

```bash
#!/bin/bash
set -euo pipefail

# ============================================================================
# nytwatch-setup.sh
# Automated setup for Nytwatch
#
# Usage:
#   ./nytwatch-setup.sh /path/to/ue/repo
#
# Prerequisites: Python 3.9+, Git, npm (for Claude CLI), gh (GitHub CLI)
# ============================================================================

REPO_PATH="${1:?Usage: $0 /path/to/ue/repo}"
AUDITOR_REPO="https://github.com/hariharanvelusamy2607/nytwatch.git"
AUDITOR_DIR="${AUDITOR_DIR:-$HOME/Projects/nytwatch}"
CONFIG_PATH="$HOME/.nytwatch/config.yaml"
DATA_DIR="$HOME/.nytwatch"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log()   { echo -e "${GREEN}[OK]${NC} $1"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
fail()  { echo -e "${RED}[FAIL]${NC} $1"; exit 1; }

# --------------------------------------------------------------------------
# Step 1: Prerequisite checks
# --------------------------------------------------------------------------
echo "=== Step 1: Checking prerequisites ==="

# Python
python3 --version >/dev/null 2>&1 || fail "Python 3 not found. Install with: brew install python@3.12"
PYVER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
PYMAJOR=$(echo "$PYVER" | cut -d. -f1)
PYMINOR=$(echo "$PYVER" | cut -d. -f2)
if [ "$PYMAJOR" -lt 3 ] || ([ "$PYMAJOR" -eq 3 ] && [ "$PYMINOR" -lt 9 ]); then
    fail "Python 3.9+ required, found $PYVER"
fi
log "Python $PYVER"

# Git
git --version >/dev/null 2>&1 || fail "Git not found. Install with: brew install git"
log "Git $(git --version | awk '{print $3}')"

# GitHub CLI
gh --version >/dev/null 2>&1 || fail "GitHub CLI not found. Install with: brew install gh"
gh auth status >/dev/null 2>&1 || fail "GitHub CLI not authenticated. Run: gh auth login"
log "GitHub CLI authenticated"

# Claude Code CLI
claude --version >/dev/null 2>&1 || fail "Claude CLI not found. Install with: npm install -g @anthropic-ai/claude-code"
log "Claude CLI found"

# Validate repo path
[ -d "$REPO_PATH" ] || fail "Repository path does not exist: $REPO_PATH"
REPO_PATH=$(cd "$REPO_PATH" && pwd)  # Resolve to absolute path
UPROJECT=$(find "$REPO_PATH" -maxdepth 1 -name "*.uproject" -print -quit)
[ -n "$UPROJECT" ] || warn "No .uproject file found in $REPO_PATH (build/test will not work)"
if [ -n "$UPROJECT" ]; then
    PROJECT_NAME=$(basename "$UPROJECT" .uproject)
    log "Found project: $PROJECT_NAME ($UPROJECT)"
else
    PROJECT_NAME="UnknownProject"
fi

# Check for Source directory
[ -d "$REPO_PATH/Source" ] || warn "No Source/ directory found in $REPO_PATH"

# Check it's a git repo
(cd "$REPO_PATH" && git rev-parse --git-dir >/dev/null 2>&1) || fail "$REPO_PATH is not a git repository"
log "Repository is a git repo"

# --------------------------------------------------------------------------
# Step 2: Install nytwatch
# --------------------------------------------------------------------------
echo ""
echo "=== Step 2: Installing nytwatch ==="

if [ -d "$AUDITOR_DIR" ]; then
    log "Nytwatch already cloned at $AUDITOR_DIR"
    (cd "$AUDITOR_DIR" && git pull --ff-only 2>/dev/null) || warn "Could not pull latest (may have local changes)"
else
    git clone "$AUDITOR_REPO" "$AUDITOR_DIR"
    log "Cloned nytwatch to $AUDITOR_DIR"
fi

pip install -e "$AUDITOR_DIR" || pip install --user -e "$AUDITOR_DIR" || fail "Failed to install nytwatch"
log "Installed nytwatch"

# Verify
nytwatch --help >/dev/null 2>&1 || fail "nytwatch command not found after install. Check your PATH."
log "nytwatch CLI verified"

# --------------------------------------------------------------------------
# Step 3: Initialize config
# --------------------------------------------------------------------------
echo ""
echo "=== Step 3: Initializing configuration ==="

if [ -f "$CONFIG_PATH" ]; then
    warn "Config already exists at $CONFIG_PATH -- skipping init (delete it to regenerate)"
else
    nytwatch init "$REPO_PATH"
    log "Config created at $CONFIG_PATH"
fi

# --------------------------------------------------------------------------
# Step 4: Auto-detect systems and generate config
# --------------------------------------------------------------------------
echo ""
echo "=== Step 4: Auto-detecting game systems ==="

# Discover Source/<ProjectName>/ subdirectories as systems
SYSTEMS_YAML=""
SOURCE_DIR="$REPO_PATH/Source/$PROJECT_NAME"
if [ -d "$SOURCE_DIR" ]; then
    for dir in "$SOURCE_DIR"/*/; do
        [ -d "$dir" ] || continue
        DIRNAME=$(basename "$dir")
        # Skip common non-system dirs
        case "$DIRNAME" in
            Tests|Private|Public|ThirdParty) continue ;;
        esac
        # Check for .h or .cpp files
        if find "$dir" -name "*.h" -o -name "*.cpp" 2>/dev/null | head -1 | grep -q .; then
            SYSTEMS_YAML="$SYSTEMS_YAML  - name: \"$DIRNAME\"\n    paths:\n      - \"Source/$PROJECT_NAME/$DIRNAME/\"\n"
            log "Detected system: $DIRNAME"
        fi
    done
fi

if [ -z "$SYSTEMS_YAML" ]; then
    warn "No game systems auto-detected. You must edit $CONFIG_PATH manually."
else
    log "Auto-detected systems. Updating config..."
fi

# --------------------------------------------------------------------------
# Step 5: Detect UE editor path
# --------------------------------------------------------------------------
echo ""
echo "=== Step 5: Detecting Unreal Engine ==="

UE_CMD=""
case "$(uname)" in
    Darwin)
        for ver in 5.5 5.4 5.3 5.2 5.1 5.0; do
            CANDIDATE="/Users/Shared/Epic Games/UE_$ver/Engine/Binaries/Mac/UnrealEditor-Cmd"
            if [ -f "$CANDIDATE" ]; then
                UE_CMD="$CANDIDATE"
                log "Found UE $ver at $UE_CMD"
                break
            fi
        done
        ;;
    Linux)
        for loc in "$HOME/UnrealEngine" /opt/UnrealEngine; do
            CANDIDATE="$loc/Engine/Binaries/Linux/UnrealEditor-Cmd"
            if [ -f "$CANDIDATE" ]; then
                UE_CMD="$CANDIDATE"
                log "Found UE at $UE_CMD"
                break
            fi
        done
        ;;
esac

if [ -z "$UE_CMD" ]; then
    warn "UnrealEditor-Cmd not found. Build/test pipeline will not work. Set build.ue_editor_cmd in $CONFIG_PATH manually."
fi

# --------------------------------------------------------------------------
# Step 6: Write final config
# --------------------------------------------------------------------------
echo ""
echo "=== Step 6: Writing final config ==="

# Build the systems block
if [ -n "$SYSTEMS_YAML" ]; then
    SYSTEMS_BLOCK=$(echo -e "$SYSTEMS_YAML")
else
    SYSTEMS_BLOCK="  - name: \"Example\"
    paths:
      - \"Source/$PROJECT_NAME/Example/\""
fi

UE_CMD_YAML="${UE_CMD:-/path/to/UnrealEditor-Cmd}"
UPROJECT_YAML="${UPROJECT:-/path/to/$PROJECT_NAME.uproject}"

cat > "$CONFIG_PATH" << CONFIGEOF
repo_path: $REPO_PATH
systems:
$SYSTEMS_BLOCK
scan_schedule:
  incremental_interval_hours: 4
  rotation_enabled: false
  rotation_interval_hours: 24
build:
  ue_editor_cmd: "$UE_CMD_YAML"
  project_file: "$UPROJECT_YAML"
  build_timeout_seconds: 1800
  test_timeout_seconds: 600
notifications:
  desktop: true
data_dir: "~/.nytwatch"
claude_fast_mode: true
min_confidence: "medium"
file_extensions:
  - ".h"
  - ".cpp"
CONFIGEOF

log "Config written to $CONFIG_PATH"

# --------------------------------------------------------------------------
# Step 7: Run first scan
# --------------------------------------------------------------------------
echo ""
echo "=== Step 7: Running first scan ==="

# Determine which system to scan
FIRST_SYSTEM=$(python3 -c "
import yaml
with open('$CONFIG_PATH') as f:
    c = yaml.safe_load(f)
systems = c.get('systems', [])
if systems:
    print(systems[0]['name'])
" 2>/dev/null || echo "")

if [ -n "$FIRST_SYSTEM" ]; then
    log "Running full scan on system: $FIRST_SYSTEM"
    nytwatch scan --type full --system "$FIRST_SYSTEM" && log "Scan complete" || warn "Scan failed (check logs at $DATA_DIR/logs/)"
else
    log "Running incremental scan"
    nytwatch scan --type incremental && log "Scan complete" || warn "Scan failed (check logs at $DATA_DIR/logs/)"
fi

# --------------------------------------------------------------------------
# Step 8: Verification
# --------------------------------------------------------------------------
echo ""
echo "=== Step 8: Verification ==="

# Each project has its own slug-named DB (e.g. dragon-racer.db).
# The DB is only created after the project is configured via the wizard.
DB_FILE=$(ls "$DATA_DIR"/*.db 2>/dev/null | head -1)
if [ -n "$DB_FILE" ]; then
    log "Database exists at $DB_FILE"
else
    warn "No project database found yet -- run the setup wizard in the dashboard to create a project"
    DB_FILE=""
fi

if [ -n "$DB_FILE" ]; then
    FINDING_COUNT=$(python3 -c "
import sqlite3
conn = sqlite3.connect('$DB_FILE')
row = conn.execute('SELECT COUNT(*) FROM findings').fetchone()
print(row[0])
conn.close()
" 2>/dev/null || echo "0")
    log "Total findings in database: $FINDING_COUNT"

    SCAN_COUNT=$(python3 -c "
import sqlite3
conn = sqlite3.connect('$DB_FILE')
row = conn.execute('SELECT COUNT(*) FROM scans').fetchone()
print(row[0])
conn.close()
" 2>/dev/null || echo "0")
    log "Total scans in database: $SCAN_COUNT"
fi

echo ""
echo "=== Setup complete ==="
echo ""
echo "Next steps:"
echo "  1. Review config: $CONFIG_PATH"
echo "  2. Start the dashboard: nytwatch serve"
echo "  3. Open in browser: http://127.0.0.1:8420"
echo "  4. Review findings and approve/reject"
if [ -z "$UE_CMD" ]; then
    echo "  5. [REQUIRED] Set build.ue_editor_cmd in config before using batch apply"
fi
echo ""
```

**Save and run:**

```bash
chmod +x nytwatch-setup.sh
./nytwatch-setup.sh /path/to/your/game/repo
```

The script is idempotent: running it again will skip steps that are already done (existing clone, existing config, etc.).
