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
  [incremental.py]  git diff --name-only <last_commit> HEAD
     |
     v
  [incremental.py]  map_files_to_systems() -> {system: [files]}
     |
     v
  [chunker.py]  collect_system_files() -> {path: content}
     |
     v
  [chunker.py]  chunk_system() -> [chunk1, chunk2, ...]
     |                                   (each chunk <= 120k tokens)
     v
  [engine.py]  analyze_system()
     |            |
     |            +---> build_scan_prompt() -----> call_claude() ---> parse_and_validate()
     |
     v
  [incremental.py]  _compute_fingerprint() -> deduplicate
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
| `create_app` | `(config: AuditorConfig) -> FastAPI` | Creates the FastAPI application. Initializes database, mounts static files, includes router, sets up APScheduler for incremental/rotation scans, registers shutdown handler. |
| `run` | `() -> None` | CLI entry point. Parses `init`, `serve`, `scan` subcommands via argparse. Default command is `serve`. |

**CLI Commands:**

| Command | Arguments | Description |
|---------|-----------|-------------|
| `init` | `repo_path`, `--config` | Creates default config YAML at `~/.code-auditor/config.yaml` |
| `serve` | `--config`, `--host` (default `127.0.0.1`), `--port` (default `8420`) | Starts FastAPI/uvicorn server |
| `scan` | `--config`, `--type` (`incremental`/`full`/`rotation`), `--system` | Runs a scan immediately and exits |

---

### 2.2 `auditor/config.py` -- Configuration

**Pydantic Config Models:**

| Model | Fields | Description |
|-------|--------|-------------|
| `SystemDef` | `name: str`, `paths: list[str]` | Defines a named game system and its source paths relative to repo root |
| `ScanSchedule` | `incremental_interval_hours: int = 4`, `rotation_enabled: bool = False`, `rotation_interval_hours: int = 24` | Scan scheduling parameters |
| `BuildConfig` | `ue_editor_cmd: str = ""`, `project_file: str = ""`, `build_timeout_seconds: int = 1800`, `test_timeout_seconds: int = 600` | UE build/test configuration |
| `NotificationConfig` | `desktop: bool = True`, `slack_webhook: Optional[str] = None`, `discord_webhook: Optional[str] = None` | Notification channels |
| `AuditorConfig` | `repo_path: str`, `systems: list[SystemDef]`, `scan_schedule: ScanSchedule`, `build: BuildConfig`, `notifications: NotificationConfig`, `data_dir: str = "~/.code-auditor"`, `claude_fast_mode: bool = True`, `min_confidence: str = "medium"`, `file_extensions: list[str] = [".h", ".cpp"]` | Root configuration model |

**Module Constants:**

| Constant | Value | Description |
|----------|-------|-------------|
| `DEFAULT_CONFIG_PATH` | `~/.code-auditor/config.yaml` (expanded) | Default location for config file |

**Functions:**

| Function | Signature | Description |
|----------|-----------|-------------|
| `load_config` | `(path: Optional[Path] = None) -> AuditorConfig` | Loads and validates config from YAML. Raises `FileNotFoundError` if missing. |
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
| `classify_path` | `(file_path: str) -> str` | Matches a file path against stored source dirs (longest prefix first). Returns `"project"` if no match. |
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
| `_format_file_contents` | `(file_contents: dict[str, str]) -> str` | Formats files as `### FILE: {path}\n```cpp\n{content}\n``` ` blocks. |
| `_finding_schema_description` | `() -> str` | Returns the JSON Schema of `FindingOutput` via `model_json_schema()`. |
| `build_scan_prompt` | `(system_name: str, file_contents: dict[str, str]) -> str` | Builds the full analysis prompt. Includes: UE reference sheet, issue categories (Bugs, Performance, UE Anti-patterns, Memory, Modern C++), the code to analyze, output format with JSON schema, and UE Automation Test template with path convention `"CodeAuditor.{system_name}.<Category>.<ShortTitle>"`. Returns empty string if `file_contents` is empty. |
| `build_batch_apply_prompt` | `(findings: list[dict], file_contents: dict[str, str]) -> str` | Builds the batch fix prompt. Includes approved findings as JSON, current source files, merge instructions (higher-severity wins on conflict), and output format for unified diff. Returns empty string if inputs are empty. |

---

### 2.7 `auditor/analysis/engine.py` -- Claude CLI Integration

**Functions:**

| Function | Signature | Description |
|----------|-----------|-------------|
| `call_claude` | `(prompt: str, fast: bool = True, timeout: int = 600) -> str` | Invokes `claude -p - --output-format json` via subprocess. Writes prompt and response to `~/.code-auditor/logs/{call_id}_*.txt`. Raises `ValueError` on empty prompt, `TimeoutExpired`, `CalledProcessError`, or `FileNotFoundError`. |
| `_strip_markdown_fences` | `(text: str) -> str` | Removes markdown code fences (handles both closed and unclosed fences). |
| `_extract_json` | `(raw: str) -> dict` | Unwraps the Claude CLI JSON envelope (`{"type":"result","result":"..."}`) and parses the inner JSON. Handles nested string-encoded JSON with optional markdown fences. |
| `parse_and_validate` | `(raw: str, schema_class) -> Optional[T]` | Extracts JSON from raw response, validates against a Pydantic model class via `model_validate()`. Returns None on parse or validation failure. |
| `analyze_system` | `(system_name: str, file_contents: dict[str, str], fast: bool = True, max_retries: int = 2) -> Optional[ScanResult]` | Builds a scan prompt, calls Claude with retries, parses/validates the response. Returns `ScanResult` or None. |
| `generate_batch_patch` | `(findings: list[dict], file_contents: dict[str, str], max_retries: int = 2) -> Optional[BatchApplyResult]` | Builds a batch apply prompt, calls Claude (with `fast=False`), parses/validates. Returns `BatchApplyResult` or None. |

---

### 2.8 `auditor/scanner/chunker.py` -- File Collection and Chunking

**Module Constants:**

| Constant | Value | Description |
|----------|-------|-------------|
| `MAX_FILE_SIZE` | `500 * 1024` (500 KB) | Files larger than this are skipped during collection. |

**Compiled Regex:**

| Name | Pattern | Description |
|------|---------|-------------|
| `_INCLUDE_RE` | `^\s*#include\s+"([^"]+)"` (MULTILINE) | Matches local C++ `#include "..."` directives. |

**Functions:**

| Function | Signature | Description |
|----------|-----------|-------------|
| `collect_system_files` | `(repo_path: str, system: SystemDef, extensions: list[str]) -> dict[str, str]` | Walks all paths defined for a system, collects files matching extensions (skipping files > 500KB). Returns `{relative_path: content}`. |
| `estimate_tokens` | `(text: str) -> int` | Estimates token count as `len(text) / 3.5`. |
| `chunk_system` | `(file_contents: dict[str, str], max_tokens: int = 120_000) -> list[dict[str, str]]` | Splits files into chunks that fit within the token budget. Header files (`.h`) are included in every chunk as shared context. `.cpp` files are distributed across chunks. Returns `[chunk]` where each chunk is `{path: content}`. |
| `resolve_includes` | `(file_content: str, repo_path: str) -> list[str]` | Extracts local `#include "..."` paths from a file and resolves them against the repo. Returns list of relative paths that exist on disk. |

---

### 2.9 `auditor/scanner/incremental.py` -- Incremental Scanning

**Functions:**

| Function | Signature | Description |
|----------|-----------|-------------|
| `get_current_commit` | `(repo_path: str) -> str` | Runs `git rev-parse HEAD`. Raises on failure. |
| `get_changed_files` | `(repo_path: str, since_commit: str, extensions: list[str]) -> list[str]` | Runs `git diff --name-only {since_commit} HEAD`, filters by extensions. |
| `map_files_to_systems` | `(changed_files: list[str], systems: list[SystemDef]) -> dict[str, list[str]]` | Maps each changed file to its system based on path prefix. Unmatched files go to `"__uncategorized"`. |
| `_compute_fingerprint` | `(file_path: str, line_range: str, category: str, title: str) -> str` | MD5 hash of `"{file_path}|{line_range}|{category}|{title}"`. Used for deduplication. |
| `_process_system` | `(system_name: str, config: AuditorConfig, db: Database, scan_id: str, fast: bool) -> int` | Collects files, chunks, analyzes each chunk via Claude, deduplicates via fingerprint, classifies source type, inserts findings. Returns finding count or `-1` if all chunks failed. |
| `run_incremental_scan` | `(config: AuditorConfig, db: Database) -> str` | Orchestrates an incremental scan. Detects source dirs, diffs from last scanned commit (falls back to HEAD~20), processes each system, updates scan record and `last_scan_commit` config. Returns scan ID. |

---

### 2.10 `auditor/scanner/scheduler.py` -- Scan Orchestration

**Functions:**

| Function | Signature | Description |
|----------|-----------|-------------|
| `run_scan` | `(config: AuditorConfig, db: Database, scan_type: str = "incremental", system_name: Optional[str] = None) -> str` | Dispatcher. Routes to `run_incremental_scan` or `run_full_system_scan`. Raises `ValueError` for unknown scan types. |
| `run_full_system_scan` | `(config: AuditorConfig, db: Database, system_name: str) -> str` | Runs a full scan of a single system. Detects source dirs, collects all files (not just changed), processes via `_process_system`. Returns scan ID. |
| `get_next_rotation_system` | `(config: AuditorConfig, db: Database) -> str` | Round-robin system selector. Reads/increments `rotation_index` in the config table. Raises `ValueError` if no systems defined. |

---

### 2.11 `auditor/scanner/source_detector.py` -- Source Directory Classification

**Functions:**

| Function | Signature | Description |
|----------|-----------|-------------|
| `detect_source_dirs` | `(repo_path: str, db: Database) -> None` | Two-layer classification: (1) deterministic UE heuristics, (2) Claude AI fallback for ambiguous dirs. Never overwrites existing DB classifications (preserves user overrides). |
| `_heuristic_classify` | `(repo: Path) -> tuple[dict[str, str], list[str]]` | Deterministic rules: `.uplugin` presence -> plugin; project-name match under `Source/` -> project; `ThirdParty` -> plugin; generated dirs -> ignored. Returns `(classified, unclassified)`. |
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

### 2.12 `auditor/pipeline/batch.py` -- Batch Pipeline Orchestrator

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
| `get_db` | `(request: Request) -> Database` | Extracts the Database instance from `request.app.state.db`. |
| `get_config` | `(request: Request) -> AuditorConfig` | Extracts config from `request.app.state.config`. |

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
| `scans.scan_type` | `TEXT` | `ScanType` enum | Values: incremental, full, manual |
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
ScanType:       incremental | full | manual
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

| Method | Path | Handler | Template | Description |
|--------|------|---------|----------|-------------|
| GET | `/` | `dashboard` | `dashboard.html` | Dashboard with aggregate stats and recent batches |
| GET | `/findings` | `findings_list` | `findings_list.html` | Filtered findings list with approve/reject actions |
| GET | `/findings/{finding_id}` | `finding_detail` | `finding_detail.html` | Single finding detail view |
| GET | `/scans` | `scans_list` | `scans.html` | Scan history list |
| GET | `/settings` | `settings_page` | `settings.html` | Source directory classification management |
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

### File Export

| Method | Path | Handler | Description |
|--------|------|---------|-------------|
| GET | `/findings/export` | `findings_export` | Excel export (.xlsx) with two sheets: "Overview" (config, severity breakdown, scan history) and "Findings" (all columns, color-coded severity, frozen header row). Same query params as findings list. |

### JSON API Endpoints

| Method | Path | Handler | Request Body | Response | Description |
|--------|------|---------|--------------|----------|-------------|
| POST | `/findings/{finding_id}/approve` | `approve_finding` | -- | `{"ok": true, "status": "approved"}` | Approve a pending/rejected finding. 400 if status invalid. |
| POST | `/findings/{finding_id}/reject` | `reject_finding` | -- | `{"ok": true, "status": "rejected"}` | Reject a pending/approved finding. 400 if status invalid. |
| POST | `/scans/trigger` | `trigger_scan` | -- | `{"ok": true, "scan_id": "started"}` | Triggers an incremental scan in a background thread. |
| POST | `/settings/source-dirs` | `update_source_dir` | `{"path": str, "source_type": str}` | `{"ok": true, "path": str, "source_type": str}` | Upsert source dir classification. `source_type` must be `project`, `plugin`, or `ignored`. |
| DELETE | `/settings/source-dirs` | `delete_source_dir` | `{"path": str}` | `{"ok": true, "path": str}` | Delete a source directory classification. |
| POST | `/batch/apply` | `apply_batch` | -- | `{"ok": true, "batch_id": str}` | Creates a batch from all approved findings and runs the pipeline in a background thread. 400 if no approved findings. |
| GET | `/api/stats` | `api_stats` | -- | `{status_counts, severity_counts, total_scans, total_batches, last_scan, pending_count, approved_count}` | JSON stats endpoint. |

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
# Required: absolute path to the game repository
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

# Scan scheduling
scan_schedule:
  incremental_interval_hours: 4       # 0 to disable scheduled scans
  rotation_enabled: false             # Enable round-robin full system scans
  rotation_interval_hours: 24         # Interval for rotation scans

# Unreal Engine build configuration
build:
  ue_editor_cmd: /path/to/UnrealEditor-Cmd
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
claude_fast_mode: true                # Not currently used in CLI args; reserved
min_confidence: medium                # Minimum confidence threshold
file_extensions:                      # File types to scan
  - .h
  - .cpp
```

### Field Reference

| Field | Type | Default | Required | Description |
|-------|------|---------|----------|-------------|
| `repo_path` | `str` | -- | Yes | Absolute path to the game repo root |
| `systems` | `list[SystemDef]` | `[]` | No | Named game systems with their source paths |
| `systems[].name` | `str` | -- | Yes (if systems defined) | Human-readable system name |
| `systems[].paths` | `list[str]` | -- | Yes (if systems defined) | Paths relative to repo root |
| `scan_schedule.incremental_interval_hours` | `int` | `4` | No | Hours between incremental scans. 0 disables scheduling. |
| `scan_schedule.rotation_enabled` | `bool` | `false` | No | Enable round-robin full scans |
| `scan_schedule.rotation_interval_hours` | `int` | `24` | No | Hours between rotation scans |
| `build.ue_editor_cmd` | `str` | `""` | No | Path to UnrealEditor-Cmd binary |
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
