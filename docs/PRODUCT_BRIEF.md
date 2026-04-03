# Code Auditor Agent -- Product Brief

**Version:** 0.1.0 (MVP)
**Last updated:** April 2026

---

## 1. Executive Summary

Code Auditor is an AI-powered, proactive code analysis agent purpose-built for Unreal Engine C++ game projects. It scans a game codebase on a configurable schedule, identifies bugs, performance issues, UE anti-patterns, memory problems, and modern C++ violations, then presents findings in a local web dashboard where a developer can review, approve, and batch-apply fixes -- with full build verification and automated test generation before a PR is ever created.

**Who it's for:** Solo game developers and small teams building games in Unreal Engine C++ who lack the bandwidth or team size for thorough code review.

**What it replaces:** The manual, inconsistent, or nonexistent code review process that solo/small-team game developers currently rely on.

---

## 2. Problem Statement

Game development in Unreal Engine C++ is uniquely difficult to review:

- **Solo developers have no reviewer.** A solo dev writing C++ gameplay code has nobody to catch their null dereferences, missing `UPROPERTY` decorators, or performance-killing Tick functions. The first reviewer is the crash report.

- **UE C++ has a massive surface area of anti-patterns.** The Unreal Engine object model (UObjects, garbage collection, replication, delegates) introduces an entire class of bugs that standard C++ linters and static analyzers don't understand. Missing a `UPROPERTY()` on a `UObject*` field causes silent garbage collection of live objects. Using `FName` in a hot loop tanks frame rate. These are domain-specific and easy to miss.

- **Small teams can't afford dedicated code review.** A two- or three-person team already has every member heads-down on features. Code review becomes a bottleneck or gets skipped entirely. Bugs ship to production and get discovered by players.

- **Existing static analysis tools don't understand game systems.** Tools like PVS-Studio and SonarQube analyze files in isolation. They don't understand that a Combat system's damage calculation interacts with a Character system's health component. They produce noise without game-context understanding.

- **The cost of shipped bugs in games is high.** A crash or performance regression in a shipped game means negative Steam reviews, refunds, and reputation damage that's hard to reverse. Prevention is orders of magnitude cheaper than post-release patching.

---

## 3. Solution

Code Auditor is an always-on auditing agent that:

1. **Runs automatically** on a configurable schedule (default: every 4 hours) or on manual trigger
2. **Analyzes code by game system** (Combat, Character, AI, UI), not file-by-file, giving the AI model holistic context about how components interact
3. **Detects six categories** of issues in a single pass: bugs, performance, UE anti-patterns, memory, modern C++, and readability
4. **Generates fixes and tests alongside every finding** -- the developer reviews a complete package (problem + fix + verification test) rather than just a warning
5. **Applies approved fixes as a single unified patch** to avoid ordering conflicts between overlapping changes
6. **Verifies fixes compile** by running the Unreal Build Tool
7. **Verifies fixes work** by executing auto-generated UE Automation Tests
8. **Creates a PR with full audit trail** only after build and tests pass

The developer's involvement is reduced to: open dashboard, review findings, approve/reject, click apply, merge the PR.

---

## 4. Target User

### Primary: Solo UE C++ Game Developers

- Working alone on a PC game in Unreal Engine
- Writing gameplay code in C++ (not Blueprint-only projects)
- Using Git and GitHub for version control
- Already has a Claude Max subscription (or willing to get one)
- Wants the safety net of code review without the overhead of finding a reviewer

### Secondary: Small Game Teams (2-5 developers)

- Teams too small to dedicate time to formal code review
- Want automated first-pass review to catch the obvious issues
- Use the dashboard as a shared review surface before merging

### Non-target

- Large studios with established code review pipelines
- Blueprint-only Unreal projects (no C++ to scan)
- Non-Unreal codebases (the prompts, anti-pattern detection, build integration, and test generation are all UE-specific)

---

## 5. Key Features

### 5.1 Automated Code Scanning

Three scan modes to balance thoroughness with cost:

| Mode | What it scans | When it runs |
|------|--------------|--------------|
| **Incremental** | Only files changed since last scan (via `git diff`) | Every N hours (default: 4) |
| **Full system** | All files in a specific game system | On manual trigger or rotation |
| **Rotation** | Cycles through systems automatically, one per interval | Every N hours (default: 24) |

Incremental is the workhorse for daily use. Rotation provides background full-coverage scanning without requiring manual intervention.

### 5.2 System-Based Analysis

Code is organized by game systems (Combat, Character, AI, UI, etc.) defined in the configuration. When the agent scans, it sends all files in a system together in a single prompt -- headers and implementation files -- so the AI model understands how classes, components, and functions interact within that system.

Large systems are automatically chunked into ~120K-token segments. Header files are included in every chunk to preserve type information.

### 5.3 Multi-Category Detection

Every scan checks for all six categories simultaneously in a single pass:

| Category | Examples |
|----------|---------|
| **Bug** | Logic errors, null dereferences, race conditions, use-after-free, off-by-one |
| **Performance** | Unnecessary `Tick`, allocations in hot paths, redundant calculations, `TArray` copies |
| **UE Anti-pattern** | Missing `UPROPERTY` on `UObject*`, raw `new` for UObjects, `FName` in loops, `ConstructorHelpers` outside constructors |
| **Memory** | Leaks, dangling pointers, missing cleanup in `EndPlay`/`BeginDestroy`, circular references |
| **Modern C++** | C-style casts, raw owning pointers, missing `constexpr`, unnecessary heap allocation |
| **Readability** | Unclear naming, overly complex logic, missing `const` |

Each finding includes a severity (critical/high/medium/low/info) and a confidence rating (high/medium/low). A configurable minimum confidence threshold filters noise.

### 5.4 Source Classification

The agent automatically classifies directories in the repo as:

- **Project** -- first-party game code (scanned and findings are actionable)
- **Plugin** -- third-party or reusable plugin code (scanned but findings are tagged as plugin-sourced)
- **Ignored** -- no C++ code or not relevant

Classification uses a two-layer approach: deterministic heuristics first (`.uplugin` files, project name matching, known UE directory patterns), then Claude AI fallback for ambiguous directories. Classifications are persisted and can be overridden in the Settings UI.

### 5.5 Web Dashboard

A local web application (FastAPI + Jinja2 + Tailwind) running on port 8420:

- **Dashboard** (`/`) -- Overview stats, severity breakdown, recent batches, quick actions
- **Findings** (`/findings`) -- Filterable list by status, severity, category, confidence, file path, and source. Approve/reject controls inline.
- **Finding Detail** (`/findings/{id}`) -- Full view with code snippet, diff preview, generated test case, and reasoning
- **Batch Status** (`/batches/{id}`) -- Real-time pipeline progress through apply/build/test/verify stages
- **Scan History** (`/scans`) -- Past scans with file counts and finding counts
- **Settings** (`/settings`) -- Source directory classification management

### 5.6 Batch Apply Pipeline

Approved findings are not applied one at a time. Instead:

1. All approved findings are collected into a batch
2. Claude generates a **single unified patch** that applies all fixes together, resolving any overlapping edits
3. The patch is applied to a new git branch (`auditor/batch-{id}`)
4. If patch application fails, the agent automatically retries with error feedback (two-layer retry)
5. On failure, the branch is cleaned up and findings are marked as failed

This avoids the cascading breakage that sequential file-by-file patching would cause.

### 5.7 Build Verification

After applying fixes, the agent runs the Unreal Build Tool (`UnrealEditor-Cmd`) to verify the patched code compiles:

- Configurable build timeout (default: 30 minutes)
- Cross-platform support (Mac, Linux, Win64)
- Build output is captured and visible in the batch detail view
- On build failure: branch is deleted, findings are marked failed, developer sees the build log

### 5.8 Automated Test Generation

Every finding includes a generated UE Automation Test at scan time (visible during review, not generated after the fact):

- Tests use the standard `IMPLEMENT_SIMPLE_AUTOMATION_TEST` macro
- Follow Arrange/Act/Assert structure
- Written to `Source/{ProjectName}/Tests/Auditor/` during the batch pipeline
- Test path convention: `CodeAuditor.{SystemName}.{Category}.{ShortTitle}`
- Executed via `UnrealEditor-Cmd -ExecCmds=Automation RunTests Auditor`
- Test results are parsed and stored with the batch
- Test files are cleaned up on failure

### 5.9 PR Creation with Audit Trail

On successful build + test:

- Changes are committed with a structured message listing all resolved findings
- A PR is created via GitHub CLI (`gh pr create`)
- PR body includes: summary, findings resolved (with severity and file path), and verification results
- Commit messages follow: `fix: batch #{id} - N findings resolved`

The developer merges at their convenience after a final review.

### 5.10 Notifications

Three notification channels:

| Channel | How it works |
|---------|-------------|
| **Desktop** | macOS `osascript` / Linux `notify-send`. Enabled by default. |
| **Slack** | Incoming webhook. Message includes finding count, status, and PR link. |
| **Discord** | Webhook. Same content as Slack. |

Notifications fire after a batch pipeline completes (success or failure).

### 5.11 Excel Export

Full findings export to `.xlsx` with two sheets:

- **Overview** -- Project name, config, severity breakdown, scan history
- **Findings** -- Every finding with severity, source, title, file, category, description, suggested fix, reasoning, and timestamps. Severity cells are color-coded (red/orange/amber/blue/gray).

Useful for sharing with team leads, sending to publishers, or archiving audit results.

---

## 6. User Journey

### Setup (one-time, ~10 minutes)

1. **Install**: `pip install -e .` in the code-auditor directory
2. **Initialize config**: `code-auditor init /path/to/game/repo`
3. **Define game systems**: Edit `~/.code-auditor/config.yaml` to map directory paths to logical game systems (Combat, Character, AI, etc.)
4. **Point to UE**: Set `ue_editor_cmd` and `project_file` in the build config
5. **Start dashboard**: `code-auditor serve`

### Daily Use

```
Morning: Open http://127.0.0.1:8420
         Dashboard shows: "12 new findings since last visit"
         |
         Click into Findings list
         Filter by severity: critical, high
         |
         Review each finding:
           - Read the description and reasoning
           - Check the code snippet and suggested fix
           - Preview the diff
           - Check the generated test -- does it actually validate the fix?
           - Click Approve or Reject
         |
         "5 findings approved" banner appears
         Click "Apply 5 approved findings"
         |
         Dashboard shows batch pipeline progress:
           Applying... -> Building... -> Testing... -> Verified
         |
         Desktop notification: "Batch abc123: 5 findings resolved, PR #87 ready"
         |
         Open GitHub, review the PR
         Changes look good, tests passed, build passed
         Merge the PR
         |
         Back to writing game code.
```

### What happens in the background

- Every 4 hours, an incremental scan runs automatically
- If rotation is enabled, a full system scan rotates through systems every 24 hours
- Source directories are auto-classified on each scan
- Duplicate findings are deduplicated by fingerprint (file + line range + category + title)
- The last scan commit is tracked so incremental scans only analyze new changes

---

## 7. Architecture Overview

```
+-----------------+     +------------------+     +------------------+
|  Scan Scheduler |---->|  Analysis Engine  |---->|  Findings Store  |
|  (APScheduler)  |     |  (Claude CLI)     |     |  (SQLite)        |
+-----------------+     +------------------+     +--------+---------+
                                                          |
                                                          v
+------------------+     +------------------+     +------------------+
|  Notification    |<----|  Apply Pipeline   |<----|  Web Dashboard   |
|  (Desktop/Slack/ |     |  (git + UBT +    |     |  (FastAPI +      |
|   Discord)       |     |   tests + PR)    |     |   Jinja2)        |
+------------------+     +------------------+     +------------------+
```

**Data flow:**

1. **Scheduler** triggers scans on a timer or via dashboard/CLI
2. **Scanner** collects files by system, chunks large systems, and passes code to the analysis engine
3. **Analysis Engine** calls Claude Code CLI (`claude -p --output-format json`), validates responses against Pydantic schemas, retries on validation failure
4. **Findings** are stored in SQLite with deduplication by fingerprint
5. **Dashboard** presents findings for human review
6. **Apply Pipeline** collects approved findings, generates a unified patch, applies it to a branch, runs build, runs tests, commits, creates PR
7. **Notifier** sends completion alerts through configured channels

**Key architectural properties:**

- All state is in a single SQLite database (`~/.code-auditor/auditor.db`) with WAL mode for concurrent reads
- The dashboard and pipeline run in the same process; batch pipelines execute on background threads
- Claude CLI calls are logged to `~/.code-auditor/logs/` (prompts and responses) for debugging
- No external services required beyond Claude CLI and GitHub CLI

---

## 8. Technology Stack

| Component | Technology | Why |
|-----------|-----------|-----|
| **Language** | Python 3.9+ | Fast to develop, good subprocess/process management, rich ecosystem |
| **Web framework** | FastAPI + Uvicorn | Async-capable, lightweight, good for local tools |
| **Templates** | Jinja2 + Tailwind CSS | Server-rendered HTML keeps the frontend simple; no JS framework needed |
| **AI engine** | Claude Code CLI (`claude -p`) | Uses existing Claude Max subscription -- no separate API key, no per-token billing |
| **Validation** | Pydantic v2 | Strict schema enforcement on AI outputs; automatic retry on validation failure |
| **Database** | SQLite (WAL mode) | Zero-config, single-file, sufficient for a local tool |
| **Scheduling** | APScheduler | Background scheduling without a separate process or cron setup |
| **Config** | YAML | Human-readable, easy to edit by hand |
| **Build tool** | Unreal Build Tool (UnrealEditor-Cmd) | The only way to verify UE C++ compilation |
| **Test framework** | UE Automation Tests | Native to Unreal Engine; tests run inside the engine runtime |
| **Version control** | Git + GitHub CLI (`gh`) | Branch management, patch application, PR creation |
| **Export** | openpyxl | Excel generation with formatting, color-coded severity cells |
| **Packaging** | Hatchling | Modern Python build system, `pip install -e .` for development |
| **Platform support** | macOS, Linux, Windows | Cross-platform with automatic path normalization (`auditor.paths`). All internal paths use POSIX forward slashes regardless of host OS. |

---

## 9. Cost Model

**$0 incremental cost.**

Code Auditor runs entirely on a developer's existing Claude Max subscription. There is no separate API key, no per-token billing, and no cloud service to pay for.

| Resource | Cost |
|----------|------|
| Claude Max subscription | Already paid (required for Claude Code CLI) |
| Claude API calls | $0 -- uses CLI, not API |
| Hosting | $0 -- runs locally |
| Database | $0 -- SQLite, local file |
| GitHub | $0 -- uses existing repo |

The only variable is how many Claude CLI calls per scan. An incremental scan of a single system typically requires 1-3 CLI calls (depending on chunk count). A full system scan of a large system might require 3-5 calls. At the default 4-hour interval, that's roughly 6-18 calls per day.

---

## 10. Roadmap

### Phase 1: MVP (current)

- [x] Incremental and full system scanning
- [x] System rotation scheduling
- [x] Six-category finding detection
- [x] Source directory classification (heuristic + AI)
- [x] Web dashboard with findings review
- [x] Approve/reject workflow
- [x] Batch apply with unified patch generation
- [x] Two-layer retry on patch failure
- [x] UE build verification
- [x] UE Automation Test generation and execution
- [x] Git branch management and PR creation
- [x] Desktop/Slack/Discord notifications
- [x] Excel export
- [x] CLI for init, serve, scan
- [x] Configurable via YAML
- [x] Fingerprint-based deduplication

### Phase 2: Daily Driver

- [ ] **Finding trends** -- charts showing findings over time, resolution rate, most-flagged files
- [ ] **System health scores** -- per-system quality grades based on finding density and severity
- [ ] **Cross-system analysis** -- detect issues that span system boundaries (e.g., Combat calling Character APIs incorrectly)
- [ ] **Blueprint integration** -- scan Blueprint-exposed C++ interfaces for common misuse patterns
- [ ] **Custom rules** -- user-defined patterns to flag project-specific anti-patterns
- [ ] **Finding comments** -- add notes to findings before approving/rejecting
- [ ] **Multi-project support** -- manage multiple game projects from one dashboard
- [ ] **Diff preview improvements** -- syntax-highlighted side-by-side diff in the dashboard

### Phase 3: Full System

- [ ] **CI/CD integration** -- run as a GitHub Action on every push/PR
- [ ] **Team features** -- multiple reviewers, assignment, review history
- [ ] **Plugin marketplace scanning** -- audit marketplace plugins before integrating them
- [ ] **Performance profiling integration** -- correlate findings with actual frame time data
- [ ] **Engine version migration** -- detect deprecated APIs when upgrading UE versions
- [ ] **Telemetry** -- opt-in anonymous metrics on finding categories and resolution rates

---

## 11. Competitive Landscape

| Tool | UE-Aware | Understands Systems | Generates Fixes | Generates Tests | Applies Fixes | Verifies Build | Creates PR | Cost |
|------|----------|-------------------|-----------------|-----------------|--------------|---------------|-----------|------|
| **Code Auditor** | Yes | Yes | Yes | Yes | Yes | Yes | Yes | $0 (Claude Max) |
| **SonarQube** | No | No | No | No | No | No | No | Free/$150+/mo |
| **Coverity** | Partial | No | No | No | No | No | No | Enterprise pricing |
| **PVS-Studio** | Partial | No | No | No | No | No | No | ~$570/yr solo |
| **GitHub Copilot** | No | No | Suggestions only | No | No | No | No | $10-39/mo |
| **Clang-Tidy** | No | No | Some auto-fix | No | Partial | No | No | Free |

### Key differentiators

**vs. SonarQube / Coverity / PVS-Studio:**
These are traditional static analysis tools. They analyze files in isolation, produce warnings without fixes, and have no understanding of Unreal Engine's object model, lifecycle, or replication system. They won't catch a missing `UPROPERTY` on a `UObject*` or warn about `FName` construction in a Tick function. Code Auditor's prompts include a UE-specific reference sheet and analyze code by game system, catching domain-specific issues these tools miss entirely.

**vs. GitHub Copilot:**
Copilot is a code completion tool. It helps you write code, but it doesn't proactively review existing code, doesn't run on a schedule, doesn't generate test cases, and doesn't verify that its suggestions compile. Code Auditor operates in the opposite direction -- it's a reviewer, not a writer.

**vs. Clang-Tidy:**
Clang-Tidy catches some C++ issues and can auto-fix a narrow set of patterns. It doesn't understand UE macros, can't reason about game logic, doesn't generate tests, and requires manual integration. Code Auditor covers a broader set of issues with richer context and a complete fix-verify-PR pipeline.

**The fundamental difference:** Code Auditor is not a linter. It's a full review-fix-verify pipeline that understands Unreal Engine C++ at the system level and delivers merged PRs, not warnings.

---

## Appendix: Configuration Quick Reference

```yaml
repo_path: /path/to/game/repo
systems:
  - name: "Combat"
    paths: ["Source/MyGame/Weapons/", "Source/MyGame/Damage/"]
  - name: "Character"
    paths: ["Source/MyGame/Character/", "Source/MyGame/Animation/"]
build:
  ue_editor_cmd: "/path/to/UnrealEditor-Cmd"
  project_file: "/path/to/MyGame.uproject"
scan_schedule:
  incremental_interval_hours: 4
  rotation_enabled: true
  rotation_interval_hours: 24
notifications:
  desktop: true
  slack_webhook: "https://hooks.slack.com/services/..."
  discord_webhook: "https://discord.com/api/webhooks/..."
claude_fast_mode: true
min_confidence: "medium"
file_extensions: [".h", ".cpp"]
```

Config file location: `~/.code-auditor/config.yaml`
Database location: `~/.code-auditor/auditor.db`
Log files: `~/.code-auditor/logs/`
Dashboard: `http://127.0.0.1:8420`
