from __future__ import annotations

import json

from auditor.analysis.schemas import FindingOutput, ScanResult, BatchApplyResult


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


def _format_file_contents(file_contents: dict[str, str]) -> str:
    sections = []
    for path, content in file_contents.items():
        sections.append(f"### FILE: {path}\n```cpp\n{content}\n```")
    return "\n\n".join(sections)


def _finding_schema_description() -> str:
    schema = FindingOutput.model_json_schema()
    return json.dumps(schema, indent=2)


def build_scan_prompt(system_name: str, file_contents: dict[str, str]) -> str:
    if not file_contents:
        return ""

    files_block = _format_file_contents(file_contents)
    schema_json = _finding_schema_description()

    return f"""\
You are a senior Unreal Engine C++ code auditor. Analyze the "{system_name}" game system below and find ALL issues in a single pass.

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

## Code to Analyze

{files_block}

---

## Output Format

Return a JSON object matching this structure exactly:

```json
{{
  "findings": [ <list of FindingOutput objects> ],
  "files_analyzed": [ <list of file paths analyzed> ],
  "scan_notes": "<any high-level observations about the system>"
}}
```

Each finding in the `findings` array must match this schema:
```json
{schema_json}
```

Field rules:
- `severity`: one of "critical", "high", "medium", "low", "info"
- `category`: one of "bug", "performance", "ue-antipattern", "modern-cpp", "memory", "readability"
- `confidence`: one of "high", "medium", "low"
- `file_path`: the exact path from the file list above
- `line_start` / `line_end`: approximate line range of the issue
- `code_snippet`: the problematic code verbatim
- `suggested_fix`: corrected code or description of the fix
- `fix_diff`: unified diff format of the fix (if auto-fixable)
- `can_auto_fix`: true only if the fix_diff can be applied without human judgment
- `reasoning`: why this is an issue and why the fix is correct
- `test_code`: a UE Automation Test that verifies the fix (see format below)
- `test_description`: one-line description of what the test validates

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

Use descriptive test names that reference the finding. The test path should follow: "CodeAuditor.{system_name}.<Category>.<ShortTitle>"

---

Return ONLY the JSON object. No markdown fences, no commentary outside the JSON.\
"""


def build_batch_apply_prompt(
    findings: list[dict], file_contents: dict[str, str]
) -> str:
    if not findings or not file_contents:
        return ""

    files_block = _format_file_contents(file_contents)
    findings_json = json.dumps(findings, indent=2)

    return f"""\
You are an Unreal Engine C++ code auditor applying approved fixes. Given the findings below and the current source files, produce a single unified diff that applies ALL fixes together.

## Approved Findings

```json
{findings_json}
```

## Current Source Files

{files_block}

---

## Instructions

1. Apply every finding's `suggested_fix` to the current file contents.
2. When multiple findings affect the same file, merge them correctly — watch for overlapping line ranges.
3. Preserve all existing code that is not part of a fix.
4. Produce one unified diff covering all changes across all files.
5. Use standard unified diff format (--- a/path, +++ b/path, @@ line ranges @@).

## Output Format

Return a JSON object matching this structure exactly:

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
