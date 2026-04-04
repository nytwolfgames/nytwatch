# Code Auditor Agent -- Technical Specification

> Canonical technical reference for the Code Auditor system.
> Generated from source at `src/auditor/` -- all function signatures, schemas, and data structures are authoritative.

---

## Table of Contents

1. [System Architecture](#1-system-architecture)
2. [Module Reference](#2-module-reference)
3. [Database Schema](#3-database-schema)
4. [Pydantic Models and Enums](#4-pydantic-models-and-enums)
5. [API and HTTP Endpoints](#5-api-and-http-endpoints)
6. [Configuration YAML Schema](#6-configuration-yaml-schema)
7. [Prompt Engineering](#7-prompt-engineering)
8. [Inter-Module Dependency Graph](#8-inter-module-dependency-graph)
9. [Status Lifecycle Diagrams](#9-status-lifecycle-diagrams)
10. [Security Considerations](#10-security-considerations)

---

## 1. System Architecture

### High-Level Component Diagram

```
+-----------------------------------------------------------------------+
|                          CLI Entry Point (main.py)                     |
|  code-auditor init | serve | scan                                     |
+----+----------------------------+-----------------------------+-------+
     |                            |                             |
     v                            v                             v
+----------+            +------------------+           +----------------+
|  Config  |            |   FastAPI App    |           |   Scheduler    |
| (config) |<---------->|   (web/routes)   |           | (APScheduler)  |
+----+-----+            +--------+---------+           +-------+--------+
     |                           |                             |
     v                           v                             v
+----------+    +----------------+----------------+   +----------------+
| Database |<---|  Dashboard  | API | Settings   |   | Scanner        |
| (SQLite) |    +----------------+----------------+   | (scheduler,    |
+----+-----+                                          |  incremental,  |
     ^                                                |  chunker,      |
     |                                                |  source_detect)|
     |                                                +-------+--------+
     |                                                        |
     |          +---------------------------------------------+
     |          |
     |          v
     |   +-------------+          +------------------+
     |   |  Analysis   |--------->|  Claude CLI      |
     |   |  (engine,   |          |  (subprocess)    |
     |   |   prompts,  |          +------------------+
     |   |   schemas)  |
     |   +-------------+
     |
     |   +-------------------------------------------------------+
     |   |              Fix Pipeline (pipeline/)                  |
     |   |  batch -> applicator -> builder -> test_writer ->      |
     |   |  test_runner -> git_ops -> notifier                    |
     +---+-------------------------------------------------------+
```

### Data Flow: Scan Cycle

```
  git repo
     |
     v
  ┌─ INCREMENTAL ──────────────────────────────────────────────────────┐
  │  [incremental.py]  git diff --name-only <last_commit> HEAD         │
  │       |                                                             │
  │       v                                                             │
  │  [incremental.py]  map_files_to_systems() → {system: [files]}      │
  │       |             (longest-prefix ownership wins)                 │
  │       v                                                             │
  │  [chunker.py]  collect_system_files() → {path: content}            │
  │       |         (loads file contents for include-graph analysis)    │
  │       v                                                             │
  │  [chunker.py]  build_neighbourhood() → {path: content}             │
  │       |         (adds headers + reverse-dep .cpps up to token cap) │
  │       v                                                             │
  │  extract paths → list[str]                                          │
  └─────────────────────────────────────────────────────────────────────┘

  ┌─ FULL ──────────────────────────────────────────────────────────────┐
  │  [chunker.py]  list_system_files() → list[str]                      │
  │       |         (path-only, no content loaded)                      │
  │       v                                                             │
  │  ownership filter: find_owning_system() removes sub-system paths   │
  └─────────────────────────────────────────────────────────────────────┘
     |
     v
  [chunker.py]  chunk_paths_by_count(max_files=20) → [chunk1, chunk2, ...]
     |
     v
  [engine.py]  analyze_system(system_name, file_paths, repo_path)
     |            |
     |            +---> build_scan_prompt(system_name, file_paths)
     |            |      (prompt contains ONLY file paths, not contents)
     |            |
     |            +---> call_claude(prompt, repo_path=repo_path)
     |            |      claude -p - --output-format json
     |            |             --dangerouslySkipPermissions
     |            |      cwd = repo_path
     |            |      Claude reads files autonomously via Read/Glob/Grep tools
     |            |
     |            +---> parse_and_validate() → ScanResult
     |
     v
  [incremental.py]  _compute_fingerprint() → deduplicate
     |
     v
  [database.py]  insert_finding() per finding
     |
     v
  [database.py]  update_scan() with status=COMPLETED
```

### Data Flow: Batch Fix Pipeline

```
  User approves findings in dashboard
     |
     v
  POST /batch/apply
     |
     v
  [batch.py]  run_batch_pipeline(config, db, batch_id)
     |
     +---> stash_changes() -> create_branch("auditor/batch-{id}")
     |
     +---> apply_batch_fixes()
     |        |
     |        +---> generate_batch_patch() -> call_claude() with batch prompt
     |        +---> git_ops.apply_patch()
     |        +---> If L1 fails: retry with error feedback (L2)
     |
     +---> write_test_files()
     |
     +---> run_ue_build()
     |
     +---> run_tests()
     |
     +---> commit_changes() -> create_pr()
     |
     +---> notify() -> desktop / Slack / Discord
     |
     +---> checkout_main() -> stash_pop()
```

---

## 2. Module Reference

### 2.1 `auditor/main.py` -- Entry Point

| Function | Signature | Description |
|----------|-----------|-------------|
| `create_app` | `(config: AuditorConfig, config_path: Optional[Path] = None) -> FastAPI` | Creates the FastAPI application. Initializes database, mounts static files, includes router, stores `config_path` in `app.state` (empty string if `None`), sets up APScheduler for incremental/rotation scans, registers shutdown handler. Auto-scan on startup is skipped if `repo_path` is empty. |
| `run` | `() -> None` | CLI entry point. Parses `init`, `serve`, `scan` subcommands. On `serve` with no `--config` flag, calls `get_active_config_path()` to find the active project; falls back to `DEFAULT_CONFIG_PATH` only if it exists; starts with a blank `AuditorConfig()` if nothing is found — the wizard handles first-run configuration. |

**CLI Commands:**

| Command | Arguments | Description |
|---------|-----------|-------------|
| `init` | `repo_path`, `--config` | Creates a default config YAML at the specified path (or `~/.code-auditor/config.yaml` if omitted) |
| `serve` | `--config`, `--host` (default `127.0.0.1`), `--port` (default `8420`) | Starts FastAPI/uvicorn server. Without `--config`, uses the active project pointer (`~/.code-auditor/.active`). |
| `scan` | `--config`, `--type` (`incremental`/`full`/`rotation`), `--system` | Runs a scan immediately and exits |

**Startup config resolution order (`serve` without `--config`):**

1. Read `~/.code-auditor/.active` — use the pointed-to YAML if the file exists on disk
2. Fall back to `~/.code-auditor/config.yaml` if it exists (legacy compatibility)
3. Start with a blank `AuditorConfig()` — user is redirected to the setup wizard

**`app.state` keys:**

| Key | Type | Description |
|-----|------|-------------|
| `config` | `AuditorConfig` | Active project configuration |
| `config_path` | `str` | Absolute path to the active config YAML, or `""` if none |
| `db` | `Database` | Active project SQLite database |
| `scheduler` | `BackgroundScheduler` | APScheduler instance (only present if scheduling is enabled) |

---

### 2.2 `auditor/config.py` -- Configuration

**Pydantic Config Models:**

| Model | Fields | Description |
|-------|--------|-------------|
| `SystemDef` | `name: str`, `paths: list[str]`, `min_confidence: Optional[str] = None`, `file_extensions: Optional[list[str]] = None`, `claude_fast_mode: Optional[bool] = None` | Defines a named game system with its source paths relative to repo root. Optional per-system overrides; `None` means inherit from global config. |
| `ScanSchedule` | `incremental_interval_hours: int = 4`, `rotation_enabled: bool = False`, `rotation_interval_hours: int = 24` | Scan scheduling parameters |
| `BuildConfig` | `ue_installation_dir: str = ""`, `ue_editor_cmd: str = ""`, `project_file: str = ""`, `build_timeout_seconds: int = 1800`, `test_timeout_seconds: int = 600` | UE build/test configuration. `ue_installation_dir` is the UE root (e.g. `/Users/Shared/Epic Games/UE_5.4`); `ue_editor_cmd` is an explicit override that takes precedence. |
| `NotificationConfig` | `desktop: bool = True`, `slack_webhook: Optional[str] = None`, `discord_webhook: Optional[str] = None` | Notification channels |
| `AuditorConfig` | `repo_path: str = ""`, `systems: list[SystemDef] = []`, `scan_schedule: ScanSchedule`, `build: BuildConfig`, `notifications: NotificationConfig`, `data_dir: str = "~/.code-auditor"`, `claude_fast_mode: bool = True`, `min_confidence: str = "medium"`, `file_extensions: list[str] = [".h", ".cpp"]` | Root configuration model. `repo_path` defaults to `""` so the app can start without a config (setup wizard mode). |

**Module Constants:**

| Constant | Value | Description |
|----------|-------|-------------|
| `DEFAULT_CONFIG_PATH` | `~/.code-auditor/config.yaml` (expanded) | Legacy default config path. Used only as a fallback when no active pointer exists and the file is present. |
| `ACTIVE_POINTER_PATH` | `~/.code-auditor/.active` (expanded) | Plain-text file containing the absolute path to the currently active project config. Written by `set_active_config_path`; read by `get_active_config_path` on server startup and after project switch. |

**Functions:**

| Function | Signature | Description |
|----------|-----------|-------------|
| `get_active_config_path` | `() -> Optional[Path]` | Reads `ACTIVE_POINTER_PATH`. Returns the pointed-to `Path` if it exists on disk, otherwise `None`. |
| `set_active_config_path` | `(path: Path) -> None` | Writes the absolute path string to `ACTIVE_POINTER_PATH`. Called by `init_project` (after wizard create) and `switch_project`. |
| `load_config` | `(path: Optional[Path] = None) -> AuditorConfig` | Loads and validates config from YAML. Raises `FileNotFoundError` if missing. Falls back to `DEFAULT_CONFIG_PATH` if `path` is None. |
| `save_config` | `(config: AuditorConfig, path: Optional[Path] = None) -> None` | Reads the existing YAML, updates only the `systems` key (preserving all other config), writes back. Used by the system editor API. |
| `save_full_config` | `(config: AuditorConfig, path: Optional[Path] = None) -> None` | Serializes all config fields to YAML. Creates parent dirs if needed. Used by the setup wizard and config repair. |
| `_serialize_systems` | `(systems: list[SystemDef]) -> list[dict]` | Serializes system list including per-system overrides, omitting `None` fields. |
| `list_project_configs` | `() -> list[dict]` | Scans `~/.code-auditor/*.yaml` for files with a non-empty `repo_path`. Returns `[{path, repo_path, name}]`. Empty/unconfigured YAMLs are excluded. |
| `validate_config_errors` | `(config: AuditorConfig) -> list[str]` | Returns human-readable validation problems (missing repo, bad paths, duplicate system paths, empty system names). |
| `detect_systems_from_repo` | `(repo_path: str) -> list[dict]` | Auto-detects systems from repo structure using UE heuristics: `.uplugin` files → plugin systems (hint=`"plugin"`), `Source/**/*.Build.cs` → game module systems (hint=`"module"`). Returns `[{name, paths, hint}]`. Skips `Binaries/`, `Intermediate/`, `Saved/`, etc. |
| `get_data_dir` | `(config: AuditorConfig) -> Path` | Returns expanded data directory, creating it if needed. |
| `get_db_path` | `(config: AuditorConfig) -> Path` | Returns `{data_dir}/auditor.db`. |
| `init_config` | `(repo_path: str, config_path: Optional[Path] = None) -> Path` | Writes a default config YAML template. Returns the path written. |

---

### 2.3 `auditor/models.py` -- Domain Models

**Utility Functions:**

| Function | Signature | Description |
|----------|-----------|-------------|
| `new_id` | `() -> str` | Generates a 12-char hex ID from `uuid4`. |
| `now_iso` | `() -> str` | Returns current UTC time as ISO 8601 string. |

**Enums** (all are `str, Enum`):

| Enum | Values |
|------|--------|
| `Severity` | `CRITICAL = "critical"`, `HIGH = "high"`, `MEDIUM = "medium"`, `LOW = "low"`, `INFO = "info"` |
| `Category` | `BUG = "bug"`, `PERFORMANCE = "performance"`, `UE_ANTIPATTERN = "ue-antipattern"`, `MODERN_CPP = "modern-cpp"`, `MEMORY = "memory"`, `READABILITY = "readability"` |
| `Confidence` | `HIGH = "high"`, `MEDIUM = "medium"`, `LOW = "low"` |
| `FindingSource` | `PROJECT = "project"`, `PLUGIN = "plugin"`, `IGNORED = "ignored"` |
| `FindingStatus` | `PENDING = "pending"`, `APPROVED = "approved"`, `REJECTED = "rejected"`, `APPLIED = "applied"`, `VERIFIED = "verified"`, `FAILED = "failed"`, `SUPERSEDED = "superseded"` |
| `ScanType` | `INCREMENTAL = "incremental"`, `FULL = "full"`, `MANUAL = "manual"` |
| `ScanStatus` | `RUNNING = "running"`, `COMPLETED = "completed"`, `FAILED = "failed"` |
| `BatchStatus` | `PENDING = "pending"`, `APPLYING = "applying"`, `BUILDING = "building"`, `TESTING = "testing"`, `VERIFIED = "verified"`, `FAILED = "failed"` |

**Pydantic Models:**

**`Finding`**

| Field | Type | Default |
|-------|------|---------|
| `id` | `str` | `new_id()` |
| `scan_id` | `str` | required |
| `title` | `str` | required |
| `description` | `str` | required |
| `severity` | `Severity` | required |
| `category` | `Category` | required |
| `confidence` | `Confidence` | required |
| `file_path` | `str` | required |
| `line_start` | `int` | required |
| `line_end` | `int` | required |
| `code_snippet` | `str` | required |
| `suggested_fix` | `Optional[str]` | `None` |
| `fix_diff` | `Optional[str]` | `None` |
| `can_auto_fix` | `bool` | `False` |
| `reasoning` | `str` | required |
| `test_code` | `Optional[str]` | `None` |
| `test_description` | `Optional[str]` | `None` |
| `source` | `FindingSource` | `FindingSource.PROJECT` |
| `status` | `FindingStatus` | `FindingStatus.PENDING` |
| `batch_id` | `Optional[str]` | `None` |
| `fingerprint` | `str` | `""` |
| `created_at` | `str` | `now_iso()` |
| `reviewed_at` | `Optional[str]` | `None` |

**`Scan`**

| Field | Type | Default |
|-------|------|---------|
| `id` | `str` | `new_id()` |
| `scan_type` | `ScanType` | required |
| `system_name` | `Optional[str]` | `None` |
| `started_at` | `str` | `now_iso()` |
| `completed_at` | `Optional[str]` | `None` |
| `base_commit` | `str` | `""` |
| `files_scanned` | `int` | `0` |
| `findings_count` | `int` | `0` |
| `status` | `ScanStatus` | `ScanStatus.RUNNING` |

**`Batch`**

| Field | Type | Default |
|-------|------|---------|
| `id` | `str` | `new_id()` |
| `created_at` | `str` | `now_iso()` |
| `status` | `BatchStatus` | `BatchStatus.PENDING` |
| `branch_name` | `Optional[str]` | `None` |
| `build_log` | `Optional[str]` | `None` |
| `test_log` | `Optional[str]` | `None` |
| `commit_sha` | `Optional[str]` | `None` |
| `pr_url` | `Optional[str]` | `None` |
| `finding_ids` | `list[str]` | `[]` |
| `completed_at` | `Optional[str]` | `None` |

---

### 2.4 `auditor/database.py` -- Persistence Layer

**Class: `Database`**

Constructor: `__init__(self, db_path: Path)`

Creates parent directories. Lazy-initializes SQLite connection with WAL journal mode and foreign keys enabled.

| Method | Signature | Description |
|--------|-----------|-------------|
| `conn` | `@property -> sqlite3.Connection` | Lazy connection getter. Sets `row_factory=sqlite3.Row`, enables WAL and foreign keys. |
| `init_schema` | `() -> None` | Executes all CREATE TABLE/INDEX statements. |
| `close` | `() -> None` | Closes connection if open. |
| **Config** | | |
| `get_config` | `(key: str, default: str = "") -> str` | Reads a key from the `config` table. |
| `set_config` | `(key: str, value: str) -> None` | Upserts a key/value in the `config` table. |
| **Scans** | | |
| `insert_scan` | `(scan: Scan) -> None` | Inserts a Scan record. |
| `update_scan` | `(scan_id: str, **kwargs) -> None` | Updates arbitrary columns on a scan. Enum values auto-extracted via `.value`. |
| `get_scan` | `(scan_id: str) -> Optional[dict]` | Returns a scan as dict or None. |
| `list_scans` | `(limit: int = 50) -> list[dict]` | Returns scans ordered by `started_at DESC`. |
| **Findings** | | |
| `insert_finding` | `(finding: Finding) -> None` | Inserts a Finding record. `can_auto_fix` stored as int. |
| `get_finding` | `(finding_id: str) -> Optional[dict]` | Returns a finding as dict or None. |
| `list_findings` | `(status: Optional[str] = None, severity: Optional[str] = None, category: Optional[str] = None, confidence: Optional[str] = None, file_path: Optional[str] = None, source: Optional[str] = None, limit: int = 100, offset: int = 0) -> list[dict]` | Filtered listing. `file_path` uses LIKE `%pattern%`. Ordered by severity rank then `created_at DESC`. |
| `update_finding_status` | `(finding_id: str, status: FindingStatus) -> None` | Updates status. Sets `reviewed_at` for APPROVED/REJECTED. |
| `set_finding_batch` | `(finding_id: str, batch_id: str) -> None` | Associates a finding with a batch. |
| `has_fingerprint` | `(fingerprint: str) -> bool` | Returns True if fingerprint exists with status in (`pending`, `approved`, `applied`, `verified`). |
| `get_approved_findings` | `() -> list[dict]` | Returns all findings with `status='approved'`, ordered by `file_path, line_start`. |
| `count_by_status` | `() -> dict[str, int]` | Aggregate count of all findings grouped by status. |
| `count_by_severity` | `() -> dict[str, int]` | Aggregate count of pending findings grouped by severity. |
| **Batches** | | |
| `insert_batch` | `(batch: Batch) -> None` | Inserts a Batch record. `finding_ids` serialized as JSON. |
| `update_batch` | `(batch_id: str, **kwargs) -> None` | Updates arbitrary columns. `finding_ids` JSON-serialized, enums auto-extracted. |
| `get_batch` | `(batch_id: str) -> Optional[dict]` | Returns batch as dict (with `finding_ids` deserialized from JSON) or None. |
| `list_batches` | `(limit: int = 20) -> list[dict]` | Returns batches ordered by `created_at DESC`. `finding_ids` deserialized. |
| **Source Dirs** | | |
| `list_source_dirs` | `() -> list[dict]` | Returns all source directory classifications. |
| `upsert_source_dir` | `(path: str, source_type: str) -> None` | Inserts or replaces a source directory classification. |
| `delete_source_dir` | `(path: str) -> None` | Deletes a source directory entry. |
| `has_source_dir` | `(path: str) -> bool` | Returns True if path exists in `source_dirs`. |
| `classify_path` | `(file_path: str) -> str` | Normalizes both input and stored paths (backslash to forward slash) before matching against source dirs (longest prefix first). Returns `"project"` if no match. Cross-platform safe. |
| **Stats** | | |
| `get_stats` | `() -> dict` | Returns aggregate stats: `status_counts`, `severity_counts`, `total_scans`, `total_batches`, `last_scan`, `pending_count`, `approved_count`. |

---

### 2.5 `auditor/analysis/schemas.py` -- Analysis Output Schemas

| Model | Fields | Description |
|-------|--------|-------------|
| `FindingOutput` | `title: str`, `description: str`, `severity: str`, `category: str`, `confidence: str`, `file_path: str`, `line_start: int`, `line_end: int`, `code_snippet: str`, `suggested_fix: Optional[str]`, `fix_diff: Optional[str]`, `can_auto_fix: bool = False`, `reasoning: str`, `test_code: Optional[str]`, `test_description: Optional[str]` | Schema for a single finding as returned by Claude. String-typed enums (validated later). |
| `ScanResult` | `findings: list[FindingOutput] = []`, `files_analyzed: list[str] = []`, `scan_notes: str = ""` | Top-level scan response from Claude. |
| `BatchApplyResult` | `unified_diff: str`, `files_modified: list[str] = []`, `notes: str = ""` | Top-level batch fix response from Claude. |

---

### 2.6 `auditor/analysis/prompts.py` -- Prompt Construction

**Module Constants:**

| Constant | Description |
|----------|-------------|
| `UE_REFERENCE_SHEET` | Multi-line string containing an Unreal Engine C++ reference covering: Macros/Decorators, UObject Lifecycle, Smart Pointers/Memory, Containers, Delegates/Events, Replication, Strings, Tick vs Timer. Injected into every scan prompt. |

**Functions:**

| Function | Signature | Description |
|----------|-----------|-------------|
| `_finding_schema_description` | `() -> str` | Returns the JSON Schema of `FindingOutput` via `model_json_schema()`. |
| `build_scan_prompt` | `(system_name: str, file_paths: list[str]) -> str` | Builds the agent-mode analysis prompt. Lists file paths as bullets and instructs Claude to read them itself via the `Read` tool (and use `Grep`/`Glob` for related context). Includes: UE reference sheet, all five issue categories, output JSON schema, and UE Automation Test template. Returns empty string if `file_paths` is empty. **Note:** does not embed file contents — Claude reads them autonomously. |
| `_format_file_contents` | `(file_contents: dict[str, str]) -> str` | Formats files as `### {path}\n```cpp\n{content}\n``` ` blocks. Used only by `build_batch_apply_prompt` (batch-fix pipeline still embeds content). |
| `build_batch_apply_prompt` | `(findings: list[dict], file_contents: dict[str, str]) -> str` | Builds the batch fix prompt. Includes approved findings as JSON, current source files (content-embedded), merge instructions (higher-severity wins on conflict), and output format for unified diff. Returns empty string if inputs are empty. |

---

### 2.7 `auditor/analysis/engine.py` -- Claude CLI Integration

**CLI command used:**

```
claude -p - --output-format json --dangerouslySkipPermissions
```

Run with `cwd=repo_path` so Claude's file-reading tools resolve paths relative to the repository root. `--dangerouslySkipPermissions` bypasses all interactive tool-permission prompts (required for headless/subprocess operation).

**Functions:**

| Function | Signature | Description |
|----------|-----------|-------------|
| `call_claude` | `(prompt: str, fast: bool = True, timeout: int = 600, repo_path: str = None) -> str` | Invokes the Claude CLI via subprocess. Writes prompt to a temp file (avoids Windows pipe-buffer deadlock). Reads stdout/stderr in background threads; polls every 30 s for cancellation and timeout. Writes prompt and response to `~/.code-auditor/logs/{call_id}_*.txt`. Raises `ValueError` on empty prompt, `InterruptedError` on cancellation, `TimeoutExpired`, `CalledProcessError`, or `FileNotFoundError`. |
| `_strip_markdown_fences` | `(text: str) -> str` | Removes markdown code fences (handles both closed and unclosed fences). |
| `_extract_json` | `(raw: str) -> dict` | Unwraps the Claude CLI JSON envelope (`{"type":"result","result":"..."}`) and parses the inner JSON. Handles nested string-encoded JSON with optional markdown fences. |
| `parse_and_validate` | `(raw: str, schema_class) -> Optional[T]` | Extracts JSON from raw response, validates against a Pydantic model class via `model_validate()`. Returns None on parse or validation failure. |
| `analyze_system` | `(system_name: str, file_paths: list[str], repo_path: str, fast: bool = True, max_retries: int = 2) -> Optional[ScanResult]` | Builds a scan prompt (paths only), calls Claude as an agent with `cwd=repo_path` so it can read files itself. Retries up to `max_retries` on failure or validation error. Returns `ScanResult` or None. |
| `generate_batch_patch` | `(findings: list[dict], file_contents: dict[str, str], max_retries: int = 2) -> Optional[BatchApplyResult]` | Builds a batch apply prompt (content-embedded), calls Claude (with `fast=False`), parses/validates. Returns `BatchApplyResult` or None. |

---

### 2.8 `auditor/scanner/chunker.py` -- File Collection and Chunking

**Module Constants:**

| Constant | Value | Description |
|----------|-------|-------------|
| `MAX_FILE_SIZE` | `500 * 1024` (500 KB) | Files larger than this are skipped during collection. |
| `MAX_TOKENS` | `35_000` | Token ceiling per chunk (leaves room for prompt + Claude output). |

**Compiled Regex:**

| Name | Pattern | Description |
|------|---------|-------------|
| `_INCLUDE_RE` | `^\s*#include\s+"([^"]+)"` (MULTILINE) | Matches local C++ `#include "..."` directives. |

**Functions:**

| Function | Signature | Description |
|----------|-----------|-------------|
| `collect_system_files` | `(repo_path: str, system: SystemDef, extensions: list[str]) -> dict[str, str]` | Walks all paths defined for a system, reads file contents (skipping files > 500KB). Returns `{relative_path: content}`. Used by incremental scans to build the include graph for neighbourhood analysis. |
| `list_system_files` | `(repo_path: str, system: SystemDef, extensions: list[str]) -> list[str]` | Walks system paths and returns repo-relative paths only — no content loaded. Used by full scans where Claude reads files itself via agent mode. |
| `collect_specific_files` | `(repo_path: str, file_paths: list[str], extensions: list[str]) -> dict[str, str]` | Reads a specific list of repo-relative paths. Used by the batch-fix pipeline to supply file contents for the patch prompt. |
| `estimate_tokens` | `(text: str) -> int` | Estimates token count as `len(text) / 3.0`. Uses 3.0 chars/token (conservative for dense C++ with templates and macros) rather than the natural-language default of ~4. |
| `chunk_paths_by_count` | `(file_paths: list[str], max_files: int = 20) -> list[list[str]]` | Splits a path list into chunks of at most `max_files`. Used by both full and incremental scans in agent mode — each chunk is passed to one `analyze_system()` call. |
| `build_neighbourhood` | `(changed_files: list[str], all_files: dict[str, str], repo_path: str, context_budget: int = MAX_TOKENS) -> dict[str, str]` | Builds a context neighbourhood around changed files for incremental scans. Always includes all changed files. Adds headers they include and `.cpp` files that depend on them, up to the token budget. Returns `{path: content}`. |
| `build_include_graph` | `(file_contents: dict[str, str], repo_path: str) -> dict[str, set[str]]` | Builds a directed include dependency graph across all files. Used by `chunk_system` for semantic clustering. |
| `chunk_system` | `(file_contents: dict[str, str], repo_path: str = "", max_tokens: int = MAX_TOKENS) -> list[dict[str, str]]` | Semantic chunking for content-embedding mode (used by batch-fix pipeline). Builds include graph, finds connected components, groups by cluster. Falls back to token-count splitting for oversized clusters. |
| `resolve_includes` | `(file_content: str, repo_path: str) -> list[str]` | Legacy helper: extracts and resolves local `#include "..."` paths. Kept for compatibility. |

---

### 2.9 `auditor/scanner/incremental.py` -- Incremental Scanning

**Functions:**

| Function | Signature | Description |
|----------|-----------|-------------|
| `get_current_commit` | `(repo_path: str) -> str` | Runs `git rev-parse HEAD`. Raises on failure. |
| `get_changed_files` | `(repo_path: str, since_commit: str, extensions: list[str]) -> list[str]` | Runs `git diff --name-only {since_commit} HEAD`, filters by extensions. |
| `find_owning_system` | `(file_path: str, systems: list[SystemDef]) -> Optional[str]` | Returns the system name whose path prefix most specifically matches `file_path`. Longest prefix wins — if `Campaign/` and `Campaign/AI/` are both configured, files under `Campaign/AI/` resolve to the more specific system. |
| `map_files_to_systems` | `(changed_files: list[str], systems: list[SystemDef]) -> dict[str, list[str]]` | Maps each changed file to its owning system via `find_owning_system()`. Unmatched files go to `"__uncategorized"`. |
| `_compute_fingerprint` | `(file_path: str, line_range: str, category: str, title: str) -> str` | MD5 hash of `"{file_path}|{line_range}|{category}|{title}"`. Used for deduplication. |
| `_process_system` | `(system_name: str, config: AuditorConfig, db: Database, scan_id: str, fast: bool, changed_files: Optional[list[str]] = None) -> tuple[int, int]` | Processes one system for either a full scan or incremental scan. **Incremental** (`changed_files` provided): loads all system files, builds neighbourhood around changed files, extracts paths. **Full** (`changed_files=None`): lists paths only, applies ownership filter. In both cases, splits into chunks of ≤20 files and calls `analyze_system(file_paths, repo_path)` — Claude reads the files itself. Deduplicates via fingerprint, classifies source type, inserts findings. Returns `(findings_count, files_scanned)`. Returns `(-1, files_scanned)` if all chunks failed. |
| `run_incremental_scan` | `(config: AuditorConfig, db: Database, system_name: Optional[str] = None) -> str` | Orchestrates an incremental scan. Inserts scan record immediately (so UI shows it running), detects source dirs, diffs from last scanned commit (falls back to HEAD~20), processes each affected system, updates scan record and `last_scan_commit`. Supports optional single-system filter. Returns scan ID. |

---

### 2.10 `auditor/scanner/scheduler.py` -- Scan Orchestration

**Functions:**

| Function | Signature | Description |
|----------|-----------|-------------|
| `run_scan` | `(config: AuditorConfig, db: Database, scan_type: str = "incremental", system_name: Optional[str] = None) -> str` | Dispatcher. Routes `"incremental"` to `run_incremental_scan`. Routes `"full"` and `"rotation"` (alias) to `run_full_scan`. Raises `ValueError` for other types. |
| `run_full_scan` | `(config: AuditorConfig, db: Database, system_name: Optional[str] = None) -> str` | Runs a full scan across all configured systems (or a single named system). Inserts scan record immediately so the UI shows progress. Iterates systems sequentially, calling `_process_system` for each. Updates `files_scanned` and `findings_count` on the scan record after each system. Handles `InterruptedError` for cancellation. Returns scan ID. |

---

### 2.11 `auditor/scanner/source_detector.py` -- Source Directory Classification

**Functions:**

| Function | Signature | Description |
|----------|-----------|-------------|
| `detect_source_dirs` | `(repo_path: str, db: Database) -> None` | Two-layer classification: (1) deterministic UE heuristics, (2) Claude AI fallback for ambiguous dirs. Never overwrites existing DB classifications (preserves user overrides). |
| `_heuristic_classify` | `(repo: Path) -> tuple[dict[str, str], list[str]]` | Deterministic rules: `.uplugin` presence -> plugin; project-name match under `Source/` -> project; `ThirdParty` -> plugin; generated dirs -> ignored. Normalizes all `relative_to()` outputs via `normalize_path()`. Returns `(classified, unclassified)`. |
| `_ai_classify` | `(repo: Path, dirs: list[str]) -> dict[str, str]` | Sends ambiguous directory listings (capped at 30 entries each) to Claude for classification. Falls back to `"project"` on failure. |
| `_build_classify_prompt` | `(dir_listings: dict[str, list[str]]) -> str` | Builds the classification prompt. Asks Claude to return `{"classifications": {"path": "project"|"plugin"}}`. |

**Heuristic Rules (in order):**

1. Everything under `Plugins/` with a `.uplugin` (direct or nested) -> `"plugin"`
2. `Plugins/` subdirs without `.uplugin` -> `"plugin"` (conservative)
3. `Source/{ProjectName}` or `Source/{ProjectName}*` -> `"project"`
4. `Source/ThirdParty` or `Source/ThirdPartyLibs` -> `"plugin"`
5. Any dir containing `.uplugin` anywhere in the repo -> `"plugin"`
6. Top-level dirs with no C++ code (no `.h`/`.cpp`) -> `"ignored"`
7. UE generated dirs (`.git`, `Intermediate`, `Saved`, `Binaries`, `DerivedDataCache`, `.vs`, `.idea`) -> skipped

---

### 2.12 `auditor/paths.py` -- Path Normalization

**Purpose:** Cross-platform path handling. Ensures all internal paths use POSIX-style forward slashes regardless of host OS.

**Functions:**

| Function | Signature | Description |
|----------|-----------|-------------|
| `normalize_path` | `(p: str) -> str` | Replaces all backslashes with forward slashes. Called by `chunker.collect_system_files`, `incremental.map_files_to_systems`, `source_detector._heuristic_classify`. Also used inline in `database.classify_path`. |

**Consumers:** `scanner/chunker.py`, `scanner/incremental.py`, `scanner/source_detector.py`, `database.py`

**Rationale:** `pathlib.Path.relative_to()` produces backslash-separated paths on Windows. Git output uses forward slashes on all platforms. This mismatch breaks path prefix matching (system mapping, source classification). The normalizer ensures consistent forward-slash paths at every storage and comparison boundary.

---

### 2.13 `auditor/pipeline/batch.py` -- Batch Pipeline Orchestrator

**Functions:**

| Function | Signature | Description |
|----------|-----------|-------------|
| `run_batch_pipeline` | `(config: AuditorConfig, db: Database, batch_id: str) -> None` | 10-step pipeline: (1) prepare branch, (2) collect file contents, (3) apply fixes via applicator, (4) write test files, (5) UE build, (6) run tests, (7) commit + create PR, (8) mark verified, (9) notify, (10) return to main. Rolls back on failure at any step. |
| `_cleanup` | `(repo_path: str, branch_name: str, branch_created: bool, stashed: bool) -> None` | Cleanup helper: checks out main, deletes branch if created, pops stash if stashed. All operations wrapped in try/except. |

---

### 2.13 `auditor/pipeline/applicator.py` -- Fix Application

**Functions:**

| Function | Signature | Description |
|----------|-----------|-------------|
| `apply_batch_fixes` | `(repo_path: str, findings: list[dict], file_contents: dict[str, str]) -> tuple[bool, str, str]` | Two-layer fix application. Layer 1: generate patch via Claude, apply with `git apply`. Layer 2 (on L1 failure): retry with error feedback injected into findings. Returns `(success, patch_or_error, notes)`. |

---

### 2.14 `auditor/pipeline/builder.py` -- UE Build Execution

**Functions:**

| Function | Signature | Description |
|----------|-----------|-------------|
| `_current_platform` | `() -> str` | Returns `"Mac"`, `"Linux"`, or `"Win64"` based on `platform.system()`. |
| `run_ue_build` | `(config: AuditorConfig) -> tuple[bool, str]` | Runs `{ue_editor_cmd} {project_file} -build -platform={platform} -configuration=Development`. Returns `(success, combined_stdout_stderr)`. Times out per `build_timeout_seconds`. |

---

### 2.15 `auditor/pipeline/test_writer.py` -- Test File Generation

**Functions:**

| Function | Signature | Description |
|----------|-----------|-------------|
| `_detect_project_name` | `(repo_path: str) -> str` | Finds the `.uproject` file and returns its stem. Raises `FileNotFoundError` if missing. |
| `write_test_files` | `(repo_path: str, findings: list[dict]) -> list[str]` | Writes each finding's `test_code` to `Source/{ProjectName}/Tests/Auditor/Test_{finding_id}.cpp`. Generates an `AuditorTests.h` header that includes all test files. Returns list of file paths written. |
| `cleanup_test_files` | `(repo_path: str, findings: list[dict]) -> None` | Removes test files and the header for the given findings. |

---

### 2.16 `auditor/pipeline/test_runner.py` -- UE Test Execution

**Functions:**

| Function | Signature | Description |
|----------|-----------|-------------|
| `run_tests` | `(config: AuditorConfig) -> tuple[bool, str, dict[str, bool]]` | Runs `{ue_editor_cmd} {project_file} -ExecCmds=Automation RunTests Auditor -unattended -nopause -NullRHI -log`. Parses output for pass/fail. Returns `(all_passed, raw_output, {test_name: passed})`. Times out per `test_timeout_seconds`. |
| `_parse_test_output` | `(output: str) -> dict[str, bool]` | Regex parser for UE Automation Test output. Matches `"Test Completed. Auditor.* Success/Fail"` and `"[Passed]/[Failed] Auditor.*"` patterns. |

---

### 2.17 `auditor/pipeline/git_ops.py` -- Git Operations

**Functions:**

| Function | Signature | Description |
|----------|-----------|-------------|
| `_run` | `(args: list[str], cwd: str, **kwargs) -> subprocess.CompletedProcess` | Subprocess wrapper for git commands with debug logging. |
| `stash_changes` | `(repo_path: str) -> bool` | Runs `git stash`. Returns True if changes were stashed. |
| `stash_pop` | `(repo_path: str) -> None` | Runs `git stash pop`. Warns on failure (ignores "No stash entries"). |
| `create_branch` | `(repo_path: str, branch_name: str) -> None` | Runs `git checkout -b {branch_name} main`. Raises `RuntimeError` on failure. |
| `checkout_main` | `(repo_path: str) -> None` | Runs `git checkout main`. Warns on failure. |
| `delete_branch` | `(repo_path: str, branch_name: str) -> None` | Runs `git branch -D {branch_name}`. Warns on failure. |
| `apply_patch` | `(repo_path: str, patch_content: str) -> tuple[bool, str]` | Writes patch to a temp file, runs `git apply --check` then `git apply`. Returns `(success, error_msg)`. Cleans up temp file. |
| `commit_changes` | `(repo_path: str, message: str) -> str` | Runs `git add -A` then `git commit -m {message}`. Returns the commit SHA. Raises `RuntimeError` on failure. |
| `create_pr` | `(repo_path: str, title: str, body: str) -> str` | Runs `gh pr create --title {title} --body {body}`. Returns the PR URL. Raises `RuntimeError` on failure. |
| `get_current_commit` | `(repo_path: str) -> str` | Runs `git rev-parse HEAD`. Raises `RuntimeError` on failure. |

---

### 2.18 `auditor/pipeline/notifier.py` -- Notifications

**Functions:**

| Function | Signature | Description |
|----------|-----------|-------------|
| `notify` | `(config: AuditorConfig, title: str, message: str, pr_url: Optional[str] = None) -> None` | Dispatches to enabled channels based on `NotificationConfig`. |
| `_desktop_notify` | `(title: str, message: str) -> None` | macOS: `osascript` display notification. Linux: `notify-send`. |
| `_slack_notify` | `(webhook: str, title: str, message: str, pr_url: Optional[str]) -> None` | Posts Slack-formatted message to webhook URL via `urllib.request`. |
| `_discord_notify` | `(webhook: str, title: str, message: str, pr_url: Optional[str]) -> None` | Posts Discord-formatted message to webhook URL via `urllib.request`. |
| `format_batch_complete_message` | `(batch: dict, findings: list[dict]) -> tuple[str, str]` | Formats a batch completion notification. Returns `(title, body)` with finding count, status, PR URL, commit SHA, and test results. |

---

### 2.19 `auditor/web/routes.py` -- HTTP Routes

See [Section 5: API and HTTP Endpoints](#5-api-and-http-endpoints) for the full endpoint reference.

**Helper Functions:**

| Function | Signature | Description |
|----------|-----------|-------------|
| `get_db` | `(request: Request) -> Database` | Extracts the `Database` instance from `request.app.state.db`. Always reflects the active project. |
| `get_config` | `(request: Request) -> AuditorConfig` | Extracts config from `request.app.state.config`. Always reflects the active project. |

**Jinja2 Template Globals:**

Registered on `templates.env.globals` at module load time. All are callables that accept `request` so they always reflect the live `app.state` without requiring every route to pass these values explicitly.

| Global | Signature | Description |
|--------|-----------|-------------|
| `active_project_name` | `(request) -> str` | Short project name — `Path(config.repo_path).name`. Empty string if no project configured. |
| `active_config_path` | `(request) -> str` | Absolute path to the active config YAML, or `""`. |
| `active_repo_path` | `(request) -> str` | `config.repo_path` for the active project, or `""`. |

Used in `base.html` to render the sidebar project pill and browser tab title suffix on every page.

---

## 3. Database Schema

SQLite database stored at `{data_dir}/auditor.db`. Uses WAL journal mode and enforces foreign keys.

### Tables

```sql
CREATE TABLE IF NOT EXISTS findings (
    id              TEXT PRIMARY KEY,
    scan_id         TEXT NOT NULL,
    title           TEXT NOT NULL,
    description     TEXT NOT NULL,
    severity        TEXT NOT NULL,
    category        TEXT NOT NULL,
    confidence      TEXT NOT NULL,
    file_path       TEXT NOT NULL,
    line_start      INTEGER NOT NULL,
    line_end        INTEGER NOT NULL,
    code_snippet    TEXT NOT NULL,
    suggested_fix   TEXT,
    fix_diff        TEXT,
    can_auto_fix    INTEGER NOT NULL DEFAULT 0,
    reasoning       TEXT NOT NULL,
    test_code       TEXT,
    test_description TEXT,
    source          TEXT NOT NULL DEFAULT 'project',
    status          TEXT NOT NULL DEFAULT 'pending',
    batch_id        TEXT,
    fingerprint     TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL,
    reviewed_at     TEXT,
    FOREIGN KEY (scan_id) REFERENCES scans(id)
);

CREATE TABLE IF NOT EXISTS scans (
    id              TEXT PRIMARY KEY,
    scan_type       TEXT NOT NULL,
    system_name     TEXT,
    started_at      TEXT NOT NULL,
    completed_at    TEXT,
    base_commit     TEXT NOT NULL DEFAULT '',
    files_scanned   INTEGER DEFAULT 0,
    findings_count  INTEGER DEFAULT 0,
    status          TEXT NOT NULL DEFAULT 'running'
);

CREATE TABLE IF NOT EXISTS batches (
    id              TEXT PRIMARY KEY,
    created_at      TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending',
    branch_name     TEXT,
    build_log       TEXT,
    test_log        TEXT,
    commit_sha      TEXT,
    pr_url          TEXT,
    finding_ids     TEXT NOT NULL DEFAULT '[]',
    completed_at    TEXT
);

CREATE TABLE IF NOT EXISTS source_dirs (
    path            TEXT PRIMARY KEY,
    source_type     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS config (
    key             TEXT PRIMARY KEY,
    value           TEXT NOT NULL
);
```

### Indexes

```sql
CREATE INDEX IF NOT EXISTS idx_findings_status      ON findings(status);
CREATE INDEX IF NOT EXISTS idx_findings_severity     ON findings(severity);
CREATE INDEX IF NOT EXISTS idx_findings_file         ON findings(file_path);
CREATE INDEX IF NOT EXISTS idx_findings_fingerprint  ON findings(fingerprint);
CREATE INDEX IF NOT EXISTS idx_findings_scan         ON findings(scan_id);
CREATE INDEX IF NOT EXISTS idx_findings_batch        ON findings(batch_id);
CREATE INDEX IF NOT EXISTS idx_findings_source       ON findings(source);
```

### Column Type Notes

| Column | Storage | Application Type | Notes |
|--------|---------|------------------|-------|
| `findings.can_auto_fix` | `INTEGER` | `bool` | Stored as 0/1 |
| `findings.severity` | `TEXT` | `Severity` enum | Values: critical, high, medium, low, info |
| `findings.category` | `TEXT` | `Category` enum | Values: bug, performance, ue-antipattern, modern-cpp, memory, readability |
| `findings.confidence` | `TEXT` | `Confidence` enum | Values: high, medium, low |
| `findings.source` | `TEXT` | `FindingSource` enum | Values: project, plugin, ignored |
| `findings.status` | `TEXT` | `FindingStatus` enum | Values: pending, approved, rejected, applied, verified, failed, superseded |
| `scans.scan_type` | `TEXT` | `ScanType` enum | Values: incremental, full (rotation is an alias for full) |
| `scans.status` | `TEXT` | `ScanStatus` enum | Values: running, completed, failed |
| `batches.status` | `TEXT` | `BatchStatus` enum | Values: pending, applying, building, testing, verified, failed |
| `batches.finding_ids` | `TEXT` | `list[str]` | JSON-encoded array of finding IDs |

### Known Config Keys

| Key | Description | Set By |
|-----|-------------|--------|
| `last_scan_commit` | Git SHA of last scanned commit | `run_incremental_scan` |
| `rotation_index` | Current index in round-robin system rotation | `get_next_rotation_system` |

---

## 4. Pydantic Models and Enums

Complete reference -- see [Section 2.3](#23-auditormodelspy----domain-models) for `models.py` and [Section 2.5](#25-auditoranalysisschemaspy----analysis-output-schemas) for `analysis/schemas.py`.

### Enum Summary

```
Severity:       critical > high > medium > low > info
Category:       bug | performance | ue-antipattern | modern-cpp | memory | readability
Confidence:     high | medium | low
FindingSource:  project | plugin | ignored
FindingStatus:  pending -> approved -> applied -> verified
                pending -> rejected
                any -> failed
                any -> superseded
ScanType:       incremental | full  ("rotation" is a scheduler alias for full)
ScanStatus:     running -> completed | failed
BatchStatus:    pending -> applying -> building -> testing -> verified
                any -> failed
```

### Model Hierarchy

```
Config Layer:
  AuditorConfig
    +-- SystemDef[]
    +-- ScanSchedule
    +-- BuildConfig
    +-- NotificationConfig

Domain Layer:
  Finding   (persisted to findings table)
  Scan      (persisted to scans table)
  Batch     (persisted to batches table)

Analysis Layer (Claude I/O):
  FindingOutput     (individual finding from Claude)
  ScanResult        (scan response: findings + metadata)
  BatchApplyResult  (batch fix response: diff + metadata)
```

---

## 5. API and HTTP Endpoints

Base URL: `http://{host}:{port}` (default `http://127.0.0.1:8420`)

### HTML Pages (Server-Side Rendered)

All pages are scoped to the active project (`app.state.config`, `app.state.db`). The sidebar on every page shows the active project name via Jinja2 template globals (`active_project_name`, `active_repo_path`, `active_config_path`). If no project is configured, `/` redirects to `/settings?setup=1`.

| Method | Path | Handler | Template | Description |
|--------|------|---------|----------|-------------|
| GET | `/` | `dashboard` | `dashboard.html` | Dashboard with aggregate stats and recent batches. Redirects to `/settings?setup=1` if `repo_path` or `systems` are empty. |
| GET | `/findings` | `findings_list` | `findings_list.html` | Filtered findings list with approve/reject actions |
| GET | `/findings/{finding_id}` | `finding_detail` | `finding_detail.html` | Single finding detail view |
| GET | `/scans` | `scans_list` | `scans.html` | Scan history list |
| GET | `/settings` | `settings_page` | `settings.html` | Active project card, project switcher, config health, source directory classification, setup wizard modal |
| GET | `/batches` | `batches_list` | `batches.html` | Batch list |
| GET | `/batches/{batch_id}` | `batch_detail` | `batch_status.html` | Batch detail with associated findings |

### Findings List Query Parameters

| Param | Type | Description |
|-------|------|-------------|
| `status` | `Optional[str]` | Filter by FindingStatus value |
| `severity` | `Optional[str]` | Filter by Severity value |
| `category` | `Optional[str]` | Filter by Category value |
| `confidence` | `Optional[str]` | Filter by Confidence value |
| `file_path` | `Optional[str]` | Partial match (LIKE %value%) |
| `source` | `Optional[str]` | Filter by FindingSource value |
| `system` | `Optional[str]` | Filter by system name — resolved to path prefixes via config |

### File Export

| Method | Path | Handler | Description |
|--------|------|---------|-------------|
| GET | `/findings/export` | `findings_export` | Excel export (.xlsx) with two sheets: "Overview" (config, severity breakdown, scan history) and "Findings" (all columns, color-coded severity, frozen header row). Same query params as findings list. |

### JSON API Endpoints

| Method | Path | Handler | Request Body | Response | Description |
|--------|------|---------|--------------|----------|-------------|
| POST | `/findings/{finding_id}/approve` | `approve_finding` | -- | `{"ok": true, "status": "approved"}` | Approve a pending/rejected finding. 400 if status invalid. |
| POST | `/findings/{finding_id}/reject` | `reject_finding` | -- | `{"ok": true, "status": "rejected"}` | Reject a pending/approved finding. 400 if status invalid. |
| POST | `/scans/trigger` | `trigger_scan` | `{"scan_type": "incremental"\|"full"\|"rotation", "system_name": str\|null}` | `{"ok": true}` or `{"error": "...", "scan_id": "..."}` (409) | Starts a scan in a background thread. Returns 409 if a scan is already running. Resets the canceller only when the slot is free. |
| POST | `/scans/cancel` | `cancel_scan` | -- | `{"ok": true}` | Sets the cancel flag, kills any active Claude subprocess, marks the running scan as CANCELLED immediately. |
| DELETE | `/scans/{scan_id}` | `delete_scan` | -- | `{"ok": true}` | Deletes a scan record. 400 if the scan is still running. |
| GET | `/api/scan-status` | `api_scan_status` | -- | `{"running": bool, "scan": dict\|null, "cancelling": bool}` | Current scan state (polling fallback for non-WS clients). |
| GET | `/api/scans/{scan_id}/logs` | `api_scan_logs` | -- | `{"logs": [...], "running": bool, "total": int}` | Paginated scan log lines. `offset` query param for incremental polling. |
| GET | `/api/findings/stream` | `api_findings_stream` | -- | `{"findings": [...], "total": int}` | Findings for a scan starting from `offset`. Used for live-streaming findings during a scan. |
| GET | `/api/systems` | `get_systems_api` | -- | `{"systems": [{name, paths, min_confidence?, file_extensions?, claude_fast_mode?}]}` | Returns current system definitions including per-system overrides. |
| POST | `/api/systems` | `save_systems_api` | `{"systems": [{name, paths, ...}]}` | `{"ok": true}` | Validates and saves system definitions. Hot-reloads `app.state.config`. |
| GET | `/api/browse` | `browse_directory` | -- | `{"path": str, "entries": [...], "parent": str\|null}` | Browse subdirectories within the repo. `path` query param is repo-relative. Optional `base` param overrides the root directory (used by setup wizard before a project is active). |
| GET | `/api/browse-abs` | `browse_absolute` | -- | `{"path": str, "entries": [...], "parent": str\|null}` | Browse the local filesystem by absolute path. On Windows with empty `path`, returns available drive letters (`C:/`, `D:/`, …). Skips system/hidden directories. |
| GET | `/api/projects` | `list_projects` | -- | `{"projects": [{path, repo_path, name}], "current": str}` | Lists all `*.yaml` files in `~/.code-auditor/` that have a non-empty `repo_path`. Excludes blank/unconfigured YAMLs. |
| POST | `/api/projects/switch` | `switch_project` | `{"path": str}` | `{"ok": true, "repo_path": str}` | Loads a different project config, swaps `app.state.config` and `app.state.db`, writes the new path to `~/.code-auditor/.active`. |
| POST | `/api/projects/init` | `init_project` | `{"project_name", "repo_path", "systems", "build", "scan_schedule", "claude_fast_mode", "min_confidence", "config_path", "source_dirs"}` | `{"ok": true, "config_path": str}` | Creates a new project config YAML (path derived from `project_name` slug if `config_path` not provided), upserts `source_dirs` into the DB, writes `.active` pointer. |
| GET | `/api/detect-systems` | `detect_systems_api` | -- | `{"systems": [{name, paths, hint}]}` | Detects systems from `repo_path` query param using `detect_systems_from_repo()`. Falls back to active config's repo_path if query param is empty. |
| GET | `/api/config/status` | `config_status` | -- | `{"config_path", "repo_path", "repo_exists", "errors", "last_commit", "db_size_bytes", "systems": [{name, paths_exist}]}` | Full config health check for the active project. |
| POST | `/api/config/repair` | `repair_config` | -- | `{"ok": true}` | Re-saves the active config with all Pydantic defaults filled in. |
| POST | `/settings/source-dirs` | `update_source_dir` | `{"path": str, "source_type": str}` | `{"ok": true, "path": str, "source_type": str}` | Upsert source directory classification. `source_type` must be `"project"`, `"plugin"`, or `"ignored"`. |
| DELETE | `/settings/source-dirs` | `delete_source_dir` | `{"path": str}` | `{"ok": true, "path": str}` | Delete a source directory classification. |
| POST | `/batch/apply` | `apply_batch` | -- | `{"ok": true, "batch_id": str}` | Creates a batch from all approved findings and runs the pipeline in a background thread. 400 if no approved findings. |
| GET | `/api/stats` | `api_stats` | -- | `{status_counts, severity_counts, total_scans, total_batches, last_scan, pending_count, approved_count}` | JSON stats for the active project's database. |

### WebSocket

| Path | Description |
|------|-------------|
| `/ws` | Real-time push channel. On connect, immediately sends current `scan_status`. Messages types: `scan_status` (running/cancelling/scan), `log` (per-scan log line), `findings_update` (chunk progress per system). |

### Static Files

Mounted at `/static` from `src/auditor/web/static/`.

### Templates

Jinja2 templates at `src/auditor/web/templates/`:

- `base.html` -- Base layout
- `dashboard.html` -- Dashboard view
- `findings_list.html` -- Findings listing
- `finding_detail.html` -- Finding detail
- `scans.html` -- Scan history
- `settings.html` -- Source dir management
- `batches.html` -- Batch listing
- `batch_status.html` -- Batch detail

---

## 6. Configuration YAML Schema

Default location: `~/.code-auditor/config.yaml`

```yaml
# Absolute path to the game repository (required for scanning)
repo_path: /path/to/GameRepo

# Game systems to audit (name + source paths relative to repo root)
systems:
  - name: DragonFlight
    paths:
      - Source/DragonRacer/DragonFlight/
  - name: Combat
    paths:
      - Source/DragonRacer/Combat/
      - Source/DragonRacer/Weapons/
    # Optional per-system overrides (null = inherit global setting):
    # min_confidence: high
    # claude_fast_mode: false
    # file_extensions: [".h", ".cpp"]

# Scan scheduling
scan_schedule:
  incremental_interval_hours: 4       # 0 to disable scheduled scans
  rotation_enabled: false             # Enable round-robin full system scans
  rotation_interval_hours: 24         # Interval for rotation scans

# Unreal Engine build configuration
build:
  ue_installation_dir: ""             # UE root (editor cmd derived from this)
  ue_editor_cmd: ""                   # Explicit override; takes precedence
  project_file: /path/to/MyGame.uproject
  build_timeout_seconds: 1800         # 30 minutes
  test_timeout_seconds: 600           # 10 minutes

# Notification channels
notifications:
  desktop: true                       # macOS/Linux desktop notifications
  slack_webhook: null                 # Slack incoming webhook URL
  discord_webhook: null               # Discord webhook URL

# Storage
data_dir: ~/.code-auditor             # Database and logs location

# Analysis settings
claude_fast_mode: true                # Passed as fast= to analyze_system()
min_confidence: medium                # Minimum confidence threshold
file_extensions:                      # File types to scan
  - .h
  - .cpp
```

### Field Reference

| Field | Type | Default | Required | Description |
|-------|------|---------|----------|-------------|
| `repo_path` | `str` | `""` | No* | Absolute path to the game repo root. Blank = wizard mode. |
| `systems` | `list[SystemDef]` | `[]` | No | Named game systems with their source paths |
| `systems[].name` | `str` | -- | Yes (per system) | Human-readable system name |
| `systems[].paths` | `list[str]` | -- | Yes (per system) | Paths relative to repo root. Longest prefix wins for ownership. |
| `systems[].min_confidence` | `str\|null` | `null` | No | Per-system override for `min_confidence` |
| `systems[].claude_fast_mode` | `bool\|null` | `null` | No | Per-system override for `claude_fast_mode` |
| `systems[].file_extensions` | `list[str]\|null` | `null` | No | Per-system override for `file_extensions` |
| `scan_schedule.incremental_interval_hours` | `int` | `4` | No | Hours between incremental scans. 0 disables scheduling. |
| `scan_schedule.rotation_enabled` | `bool` | `false` | No | Enable round-robin full scans |
| `scan_schedule.rotation_interval_hours` | `int` | `24` | No | Hours between rotation scans |
| `build.ue_installation_dir` | `str` | `""` | No | UE installation root; used to derive editor command path |
| `build.ue_editor_cmd` | `str` | `""` | No | Explicit path to UnrealEditor-Cmd binary (overrides ue_installation_dir) |
| `build.project_file` | `str` | `""` | No | Path to .uproject file |
| `build.build_timeout_seconds` | `int` | `1800` | No | Build timeout |
| `build.test_timeout_seconds` | `int` | `600` | No | Test run timeout |
| `notifications.desktop` | `bool` | `true` | No | Enable desktop notifications |
| `notifications.slack_webhook` | `str` | `null` | No | Slack incoming webhook URL |
| `notifications.discord_webhook` | `str` | `null` | No | Discord webhook URL |
| `data_dir` | `str` | `~/.code-auditor` | No | Data directory (tilde-expanded) |
| `claude_fast_mode` | `bool` | `true` | No | Reserved for future use |
| `min_confidence` | `str` | `medium` | No | Minimum confidence for displayed findings |
| `file_extensions` | `list[str]` | `[".h", ".cpp"]` | No | File extensions to include in scans |

---

## 7. Prompt Engineering

### 7.1 UE Reference Sheet

Injected into every scan prompt as domain context. Covers 8 topic areas:

| Section | Key Points |
|---------|------------|
| **Macros & Decorators** | UPROPERTY, UFUNCTION, UCLASS, USTRUCT, UENUM, GENERATED_BODY |
| **UObject Lifecycle** | NewObject, CreateDefaultSubobject, ConstructorHelpers, BeginPlay/EndPlay, IsValid, AddToRoot/RemoveFromRoot |
| **Smart Pointers & Memory** | TSharedPtr/Ref, TWeakPtr, TWeakObjectPtr, TStrongObjectPtr, TSoftObjectPtr, UPROPERTY prevents GC |
| **Containers** | TArray, TMap, TSet, Reserve, Empty vs Reset, FindByPredicate, Algo namespace |
| **Delegates & Events** | DECLARE_DYNAMIC_MULTICAST_DELEGATE, FTimerHandle, AddDynamic/RemoveDynamic |
| **Replication** | DOREPLIFETIME, ReplicatedUsing, Server/Client/NetMulticast, HasAuthority |
| **Strings** | FName (identifiers), FString (general), FText (localization), anti-patterns |
| **Tick vs Timer** | SetTickEnabled(false), TickInterval, FTimerHandle for periodic work |

### 7.2 Scan Prompt Structure

Built by `build_scan_prompt(system_name, file_contents)`:

```
1. Role assignment: "senior Unreal Engine C++ code auditor"
2. UE Reference Sheet (full text)
3. Issue categories to check:
   - Bugs (logic errors, null deref, race conditions, use-after-free)
   - Performance (Tick abuse, hot-path allocations, missing const ref)
   - UE Anti-patterns (missing UPROPERTY, raw new, ConstructorHelpers)
   - Memory (leaks, dangling ptrs, missing cleanup, circular refs)
   - Modern C++ (raw owning ptrs, C-style casts, missing constexpr)
4. Code block: each file as ### FILE: {path} with cpp code fence
5. Output format: JSON matching ScanResult schema
6. FindingOutput JSON Schema (auto-generated from Pydantic)
7. Field rules (enum values, line ranges, diff format)
8. Test case template: UE Automation Test with IMPLEMENT_SIMPLE_AUTOMATION_TEST
   - Test path: "CodeAuditor.{system_name}.<Category>.<ShortTitle>"
9. Instruction: "Return ONLY the JSON object"
```

### 7.3 Batch Apply Prompt Structure

Built by `build_batch_apply_prompt(findings, file_contents)`:

```
1. Role: "Unreal Engine C++ code auditor applying approved fixes"
2. Approved findings as JSON array
3. Current source files (same format as scan prompt)
4. Instructions:
   - Apply every finding's suggested_fix
   - Merge overlapping fixes in same file
   - Preserve untouched code
   - Higher-severity wins on conflict
5. Output format: JSON matching BatchApplyResult schema
   - unified_diff: standard unified diff format
   - files_modified: list of changed paths
   - notes: conflict warnings
6. Instruction: "Return ONLY the JSON object"
```

### 7.4 Source Classification Prompt

Built by `_build_classify_prompt(dir_listings)`:

```
1. Context: "analyzing an Unreal Engine project's directory structure"
2. Classification task: "project" (first-party) or "plugin" (third-party)
3. Signals to consider:
   - Plugin-like naming (vendor names, generic utilities)
   - Game-specific names (game modes, character systems)
   - Plugin structure (Public/Private with generic module names)
4. Directory listings as JSON (max 30 entries per dir)
5. Output format: {"classifications": {"path": "project"|"plugin"}}
```

### 7.5 Claude CLI Invocation

```
claude -p - --output-format json
```

- Input: prompt via stdin (`-p -`)
- Output: JSON envelope `{"type": "result", "result": "<escaped JSON string>"}`
- Timeout: 600s for scans, 60s for source classification
- Logs: `~/.code-auditor/logs/{call_id}_prompt.txt` and `{call_id}_response.txt`

---

## 8. Inter-Module Dependency Graph

```
main.py
  +-- config.py
  +-- database.py
  +-- web/routes.py
  |     +-- database.py
  |     +-- models.py
  |     +-- pipeline/batch.py
  |     +-- scanner/scheduler.py
  +-- scanner/scheduler.py
        +-- scanner/incremental.py
        |     +-- scanner/chunker.py
        |     +-- scanner/source_detector.py
        |     +-- analysis/engine.py
        |     +-- database.py
        |     +-- models.py
        |     +-- config.py
        +-- scanner/chunker.py
        +-- scanner/source_detector.py
              +-- analysis/engine.py (call_claude, _extract_json)
              +-- database.py

analysis/engine.py
  +-- analysis/prompts.py
  |     +-- analysis/schemas.py
  +-- analysis/schemas.py
  +-- models.py (new_id only)

pipeline/batch.py
  +-- pipeline/applicator.py
  |     +-- analysis/engine.py (generate_batch_patch)
  |     +-- pipeline/git_ops.py (apply_patch)
  +-- pipeline/builder.py
  +-- pipeline/test_writer.py
  +-- pipeline/test_runner.py
  +-- pipeline/git_ops.py
  +-- pipeline/notifier.py
  +-- scanner/chunker.py (collect_system_files)
  +-- config.py
  +-- database.py
  +-- models.py
```

### External Dependencies

| Package | Used By | Purpose |
|---------|---------|---------|
| `fastapi` | `main.py`, `web/routes.py` | Web framework |
| `uvicorn` | `main.py` | ASGI server |
| `pydantic` | `config.py`, `models.py`, `analysis/schemas.py` | Data validation |
| `yaml` (PyYAML) | `config.py` | Config file parsing |
| `apscheduler` | `main.py` | Background job scheduling |
| `jinja2` | `web/routes.py` | HTML templating |
| `openpyxl` | `web/routes.py` | Excel export (lazy import) |
| `sqlite3` | `database.py` | Database (stdlib) |

### System Binaries

| Binary | Used By | Purpose |
|--------|---------|---------|
| `claude` | `analysis/engine.py` | Claude CLI for AI analysis |
| `git` | `scanner/incremental.py`, `pipeline/git_ops.py` | Version control operations |
| `gh` | `pipeline/git_ops.py` | GitHub CLI for PR creation |
| `osascript` | `pipeline/notifier.py` | macOS desktop notifications |
| `notify-send` | `pipeline/notifier.py` | Linux desktop notifications |
| UnrealEditor-Cmd | `pipeline/builder.py`, `pipeline/test_runner.py` | UE build and test execution |

---

## 9. Status Lifecycle Diagrams

### Finding Status Lifecycle

```
                          +----------+
                 +------->| APPROVED |--------+
                 |        +----------+        |
                 |             |               |
            (user action)     |          (batch pipeline)
                 |            |               |
           +----------+      |          +---------+
  (new)--->| PENDING  |      |    +---->| APPLIED |
           +----------+      |    |     +---------+
                 |            |   |          |
            (user action)     |   |    (build+test pass)
                 |            |   |          |
                 v            |   |          v
           +----------+      |   |    +----------+
           | REJECTED |      |   |    | VERIFIED |
           +----------+      |   |    +----------+
                              |   |
                              |   +--- (pipeline step)
                              |
                              +------> +--------+
                               (fail)  | FAILED |
                                       +--------+

           +-----------+
           | SUPERSEDED|  (finding replaced by newer scan)
           +-----------+
```

**Transition Rules:**

| From | To | Trigger |
|------|----|---------|
| (new) | PENDING | Finding inserted by scanner |
| PENDING | APPROVED | User approves via POST `/findings/{id}/approve` |
| PENDING | REJECTED | User rejects via POST `/findings/{id}/reject` |
| REJECTED | APPROVED | User re-approves via POST `/findings/{id}/approve` |
| APPROVED | REJECTED | User re-rejects via POST `/findings/{id}/reject` |
| APPROVED | VERIFIED | Batch pipeline succeeds (build + tests pass) |
| APPROVED | FAILED | Batch pipeline fails at any step |
| PENDING | SUPERSEDED | Not currently implemented; reserved |

### Batch Status Lifecycle

```
  +---------+     +----------+     +----------+     +---------+     +----------+
  | PENDING |---->| APPLYING |---->| BUILDING |---->| TESTING |---->| VERIFIED |
  +---------+     +----------+     +----------+     +---------+     +----------+
                       |                |                |
                       v                v                v
                  +--------+       +--------+       +--------+
                  | FAILED |       | FAILED |       | FAILED |
                  +--------+       +--------+       +--------+
```

**Transition Rules:**

| From | To | Trigger |
|------|----|---------|
| (new) | PENDING | Batch created from approved findings |
| PENDING | APPLYING | Pipeline starts, branch created |
| APPLYING | BUILDING | Fixes applied successfully |
| APPLYING | FAILED | Patch generation or application fails |
| BUILDING | TESTING | UE build succeeds |
| BUILDING | FAILED | UE build fails |
| TESTING | VERIFIED | All UE automation tests pass |
| TESTING | FAILED | Any test fails |

---

## 10. Security Considerations

### Credential Handling

- **Webhook URLs**: Slack and Discord webhook URLs are stored in the config YAML. These should be treated as secrets.
- **No authentication on web UI**: The FastAPI server has no authentication or authorization. It is designed for local-only use (`127.0.0.1`).
- **GitHub CLI**: PR creation relies on `gh` being authenticated. Credentials are managed by the `gh` CLI itself (outside this system).

### Subprocess Execution

- **Claude CLI**: Prompts are passed via stdin, not as command-line arguments. This avoids shell injection via prompt content.
- **Git commands**: All git operations use list-based `subprocess.run()` (not shell=True), preventing command injection.
- **UE build/test**: Commands are constructed from config values. A malicious config could execute arbitrary commands via `ue_editor_cmd`. The config file should be protected.
- **Patch application**: Uses `git apply --check` before actual application (dry-run validation).

### Data Handling

- **SQLite WAL mode**: Provides concurrent read access and crash recovery.
- **Foreign keys enforced**: `findings.scan_id` references `scans.id`.
- **No SQL injection**: All queries use parameterized statements (`?` placeholders).
- **File size cap**: Files > 500KB are skipped during collection to prevent memory issues.
- **Token budget**: Chunking enforces a 120k token limit per Claude call.

### File System Access

- **Reads game repo files**: The scanner reads source files from the configured `repo_path`. It does not write to the game repo during scanning.
- **Writes during batch**: The batch pipeline writes test files and applies patches to the game repo. This is done on a separate git branch.
- **Log files**: Prompts and responses are written to `~/.code-auditor/logs/`. These may contain source code and should be treated accordingly.
- **Temp files**: Patch files are written to the repo directory as temp files and cleaned up after use.

### Network Access

- **Claude CLI**: Calls the Claude API through the CLI binary. Network access is managed by the CLI.
- **Webhook notifications**: Outbound HTTP POST to configured Slack/Discord URLs via `urllib.request`.
- **GitHub CLI**: Outbound HTTPS to GitHub API for PR creation.
- **No inbound network exposure by default**: Server binds to `127.0.0.1`. Changing `--host` to `0.0.0.0` would expose the unauthenticated UI.

### Recommendations

1. Do not bind the server to `0.0.0.0` without adding authentication.
2. Store webhook URLs in environment variables rather than the config YAML if the config is committed to version control.
3. Restrict file permissions on `~/.code-auditor/` (contains database, logs with source code, and config with webhook secrets).
4. The `ue_editor_cmd` config field accepts an arbitrary path -- validate it points to a real UE binary if accepting config from untrusted sources.
5. Log files in `~/.code-auditor/logs/` accumulate indefinitely. Implement rotation or cleanup.
