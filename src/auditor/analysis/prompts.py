from __future__ import annotations

import json


UE_REFERENCE_SHEET = """
## Unreal Engine C++ Reference Sheet

### Macros & Decorators
- UPROPERTY(EditAnywhere, BlueprintReadWrite, Category="...", meta=(...))
- UFUNCTION(BlueprintCallable, Server, Client, NetMulticast, Reliable/Unreliable)
- UCLASS(Blueprintable, ClassGroup=(...), meta=(...))
- USTRUCT(BlueprintType)
- UENUM(BlueprintType)
- GENERATED_BODY() — required in every UCLASS/USTRUCT

### UObject Lifecycle
- NewObject<T>() for UObjects (never raw new)
- CreateDefaultSubobject<T>() in constructors only
- ConstructorHelpers::FObjectFinder only in constructors
- BeginPlay / EndPlay / BeginDestroy lifecycle hooks
- IsValid(), IsValidLowLevel(), ::IsValid(Ptr) null-safety checks
- ConditionalBeginDestroy() for manual teardown
- AddToRoot() / RemoveFromRoot() for GC pinning (use sparingly)

### Smart Pointers & Memory
- TSharedPtr / TSharedRef / TWeakPtr for non-UObject types
- TWeakObjectPtr<T> for weak UObject references
- TStrongObjectPtr<T> for preventing GC collection
- TSoftObjectPtr<T> / TSoftClassPtr<T> for async loading
- UPROPERTY() prevents GC of UObject members (mandatory for UObject* fields)
- Never mix raw new/delete with UObject system

### Containers
- TArray, TMap, TSet — standard UE containers
- Reserve() to avoid reallocations when size is known
- Empty() vs Reset() — Reset keeps allocation, Empty frees
- FindByPredicate, FilterByPredicate, RemoveAll — prefer over manual loops
- Algo::Sort, Algo::Reverse — UE algorithm namespace

### Delegates & Events
- DECLARE_DYNAMIC_MULTICAST_DELEGATE for Blueprint-exposed events
- DECLARE_DELEGATE / DECLARE_MULTICAST_DELEGATE for C++-only
- FTimerHandle + GetWorldTimerManager() preferred over Tick for periodic work
- AddDynamic / RemoveDynamic macros

### Replication
- DOREPLIFETIME / DOREPLIFETIME_CONDITION in GetLifetimeReplicatedProps
- UPROPERTY(Replicated) / UPROPERTY(ReplicatedUsing=OnRep_X)
- Server/Client/NetMulticast UFUNCTION specifiers
- HasAuthority() checks before server-only logic

### Strings
- FName — immutable, case-insensitive, fast comparison (use for identifiers)
- FString — mutable, general purpose
- FText — localization-ready (use for all user-visible text)
- Never use FName for user-facing strings
- Never construct FName in hot loops (hash table lookup)

### Tick vs Timer
- Tick every frame: only when truly needed (interpolation, input)
- FTimerHandle for periodic work (inventory checks, cooldowns)
- SetTickEnabled(false) when actor doesn't need it
- TickInterval for reduced-frequency ticking
""".strip()


# Compact inline schema — ~400 tokens cheaper than dumping the full Pydantic JSON schema
_FINDING_SCHEMA = """\
Each finding object has these fields:
  title            string   — short title of the issue
  description      string   — detailed explanation
  severity         string   — "critical" | "high" | "medium" | "low" | "info"
  category         string   — "bug" | "performance" | "ue-antipattern" | "modern-cpp" | "memory" | "readability"
  confidence       string   — "high" | "medium" | "low"
  file_path        string   — repo-relative path of the file containing the issue
  line_start       int      — first line of the issue
  line_end         int      — last line of the issue
  code_snippet     string   — verbatim problematic code
  suggested_fix    string?  — corrected code or description of the fix
  fix_diff         string?  — unified diff of the fix (if auto-fixable)
  can_auto_fix     bool     — true only if fix_diff can be applied without human judgment
  reasoning        string   — why this is an issue and why the fix is correct
  test_code        string?  — UE Automation Test that verifies the fix (see format below)
  test_description string?  — one-line description of what the test validates"""


def build_scan_prompt(system_name: str, file_paths: list[str]) -> str:
    if not file_paths:
        return ""

    paths_block = "\n".join(f"- {p}" for p in file_paths)

    return f"""\
You are a senior Unreal Engine C++ code auditor. Analyze the "{system_name}" system and find ALL issues.

Use the Read tool to read each file listed below. You may also use Grep to search for related patterns and Glob to discover related headers. Read whatever additional context you need to make accurate findings.

## Files to Analyze

{paths_block}

---

{UE_REFERENCE_SHEET}

---

## Issue Categories to Check

Find every issue across ALL of these categories:
1. **Bugs** — Logic errors, null dereference, race conditions, off-by-one, use-after-free
2. **Performance** — Unnecessary Tick, allocations in hot paths, redundant calculations, missing const ref, TArray copies
3. **UE Anti-patterns** — Missing UPROPERTY on UObject*, raw new for UObjects, ConstructorHelpers outside constructors, FName in loops, FString for display text
4. **Memory** — Leaks, dangling pointers, missing cleanup in EndPlay/BeginDestroy, circular references
5. **Modern C++** — Raw owning pointers where smart pointers fit, C-style casts, missing constexpr/consteval, unnecessary heap allocation, auto improvements

---

## Output Format

After reading and analyzing the files, return a JSON object:

```json
{{
  "findings": [ ... ],
  "files_analyzed": [ "<list of file paths you read>" ],
  "scan_notes": "<high-level observations about the system>"
}}
```

{_FINDING_SCHEMA}

## Test Case Format

Every finding MUST include a `test_code` field with a UE Automation Test:

```cpp
IMPLEMENT_SIMPLE_AUTOMATION_TEST(F<TestName>, "<TestPath>", EAutomationTestFlags::ApplicationContextMask | EAutomationTestFlags::ProductFilter)
bool F<TestName>::RunTest(const FString& Parameters)
{{
    // Arrange — set up the scenario
    // Act — trigger the behavior
    // Assert — verify the fix
    TestTrue(TEXT("Description"), Condition);
    return true;
}}
```

Test path format: "CodeAuditor.{system_name}.<Category>.<ShortTitle>"

Return ONLY the JSON object. No markdown fences, no commentary outside the JSON.\
"""


_CHAT_HISTORY_LIMIT = 10  # max messages sent to Claude per turn


def build_finding_chat_prompt(
    finding: dict,
    history: list[dict],
    user_message: str,
) -> str:
    """Build a prompt for a single chat turn about a specific finding.

    History is capped to the last ``_CHAT_HISTORY_LIMIT`` messages so the
    context window stays bounded on long conversations.
    """
    lines = [
        "You are a code review assistant helping a developer refine a code finding and its suggested fix.",
        "",
        "## Finding",
        f"Title:      {finding['title']}",
        f"File:       {finding['file_path']}  (lines {finding.get('line_start', '?')}–{finding.get('line_end', '?')})",
        f"Severity:   {finding.get('severity', '')}  |  Category: {finding.get('category', '')}  |  Confidence: {finding.get('confidence', '')}",
        "",
        "### Description",
        finding.get("description", ""),
        "",
        "### Current Code",
        "```cpp",
        finding.get("code_snippet", ""),
        "```",
    ]

    if finding.get("suggested_fix"):
        lines += ["", "### Suggested Fix", finding["suggested_fix"]]

    if finding.get("fix_diff"):
        lines += ["", "### Current Diff", "```diff", finding["fix_diff"], "```"]

    if finding.get("test_code"):
        lines += ["", "### Current Test Code", "```cpp", finding["test_code"], "```"]

    lines += [
        "",
        "## Instructions",
        "- Use the Read tool to examine the file at the path above whenever you need more context.",
        "- Answer the developer's questions conversationally and accurately.",
        "- **Only** if the developer explicitly asks you to update, regenerate, or change the fix,",
        "  diff, or test code: append a JSON block at the **very end** of your response:",
        "  ```json",
        '  {"suggested_fix": "...", "fix_diff": "...", "test_code": "..."}',
        "  ```",
        "  Include only the fields you are actually changing. Omit unchanged fields.",
        "  fix_diff must be a valid unified diff patch.",
        "  Do NOT include the JSON block for conversational replies.",
    ]

    capped = history[-_CHAT_HISTORY_LIMIT:] if len(history) > _CHAT_HISTORY_LIMIT else history
    if capped:
        lines += ["", "## Conversation History"]
        for msg in capped:
            label = "User" if msg["role"] == "user" else "Assistant"
            lines.append(f"{label}: {msg['content']}")

    lines += ["", "## Current Message", f"User: {user_message}"]
    return "\n".join(lines)


def build_batch_apply_prompt(
    findings: list[dict], file_paths: list[str]
) -> str:
    """Build the batch-apply prompt.

    Uses agent mode — file contents are NOT embedded in the prompt.  Instead,
    Claude reads each file itself with the Read tool (same approach as the
    scan prompt), keeping the prompt small regardless of file sizes.
    """
    if not findings or not file_paths:
        return ""

    paths_block = "\n".join(f"- {p}" for p in file_paths)
    findings_json = json.dumps(findings, indent=2)

    return f"""\
You are an Unreal Engine C++ code auditor applying approved fixes.

Use the Read tool to read the current contents of each file listed below, then produce a single unified diff that applies ALL findings together.

## Files to Read

{paths_block}

## Approved Findings

```json
{findings_json}
```

---

## Instructions

1. Read every file in the list above before producing any output.
2. Apply every finding's `suggested_fix` to the current file contents.
3. When multiple findings affect the same file, merge them correctly — watch for overlapping line ranges.
4. Preserve all existing code that is not part of a fix.
5. Produce one unified diff covering all changes across all files.
6. Use standard unified diff format (--- a/path, +++ b/path, @@ line ranges @@).

## Output Format

Return a JSON object:

```json
{{
  "unified_diff": "<the complete unified diff as a string>",
  "files_modified": ["<list of file paths that were changed>"],
  "notes": "<any warnings about conflicts, overlapping fixes, or manual steps needed>"
}}
```

If two findings conflict (overlapping edits to the same lines), apply the higher-severity one and note the conflict in `notes`.

Return ONLY the JSON object. No markdown fences, no commentary outside the JSON.\
"""
