---
name: balance-run
description: "Interactive workflow to apply balance fixes from /balance-check recommendations directly to data JSON files. Reads JSON files, allows user selection of fixes, applies changes, and saves atomically. Use after /balance-check identifies issues."
argument-hint: "[system-name]"
user-invocable: true
allowed-tools: Read, Write, Glob, Bash
agent: economy-designer
---

## Workflow Overview

`balance-run` takes a balance-check report and translates recommendations into atomic JSON edits. User selects which fixes to apply, changes are written to data files, and balance-check can be re-run to verify.

---

## Phase 1: Load Balance Recommendations

Accept one of two input forms:

**Option A: From Recent balance-check Report**
- If user ran `/balance-check [system]` recently, ask:
  > "I found the recent balance-check report for [system]. Apply these [N] recommendations?"
- Parse the recommendations from the report and show them in a selection table

**Option B: Manual List**
- If user provides a list of recommendations (copy-paste from report), parse and show them

**Show the Selection Menu:**
```
Recommended Balance Fixes for [System]

[ ] 1. [File] → [Path] = [Value]  ([Rationale])
[ ] 2. [File] → [Path] = [Value]  ([Rationale])
...

Apply selected fixes? (Y/N, or select specific indices: 1,3,5)
```

---

## Phase 2: Validate JSON Paths

For each selected fix:

1. Read the target data file (path determined from the balance-check report)
2. Validate the JSON path exists (e.g., `[0].upkeep.gold_cost`)
3. Show current value vs. proposed value:
   ```
   File: assets/data/buildings.json
   Path: [0].upkeep.gold_cost
   Current: 0
   Proposed: 10
   ✓ Valid path
   ```
4. If path invalid, skip that fix and warn user (don't halt)

---

## Phase 3: Batch Apply Changes

For all validated fixes:

1. Parse each JSON file once (cache in memory)
2. Apply all changes to that file
3. Write the modified JSON back (formatted, preserves spacing)
4. Log each change:
   ```
   ✓ assets/data/buildings.json[0].upkeep.gold_cost: 0 → 10
   ✓ assets/data/buildings.json[1].upkeep.gold_cost: 0 → 15
   ```

**Important:** Changes are atomic per-file (all changes to a single file are written in one pass).

---

## Phase 4: Summary & Verification

Display:
```
Applied 5 fixes across 3 files:
- assets/data/buildings.json (2 changes)
- assets/data/units.json (2 changes)
- assets/data/abilities.json (1 change)

Next steps:
1. Test locally or review the changed JSONs
2. Run `/balance-check [system]` to verify no new outliers
3. Commit: git add assets/data/ && git commit -m "Balance pass: [system]"
```

Ask:
> "Re-run `/balance-check` to verify these changes don't introduce new outliers?"

If yes:
- Spawn `/balance-check [system]` automatically
- Compare new report to previous (highlight any changes)
- Flag if new outliers appear
- Ask to revert if verification fails (offer `git checkout assets/data/` rollback)

If no:
- Suggest user test locally before committing

---

## JSON Editing Details

### Path Syntax

Paths follow dot-notation for nested objects and bracket-notation for arrays:

```
[0].upkeep.gold_cost              // Array index 0, object property chain
[3].recruitment_cost              // Array index 3, direct property
[0].cost_to_build.food_cost       // Deep nesting
```

### JSON Parsing & Formatting

- Read JSON with full structure preservation (parse → modify → stringify)
- Maintain original formatting (spaces, newlines) as much as possible
- Use `JSON.stringify(data, null, "\t")` for consistent re-writing (one-tab indents)
- Validate JSON is valid after edits before writing

### Data Type Coercion

Be smart about value types:
- If current value is `integer` (e.g., `10`), write integer
- If current value is `float` (e.g., `10.0`), write float
- If current value is `boolean`, write boolean
- Do not change types unless recommended value explicitly specifies

---

## Rollback

If verification fails or user requests rollback:

```bash
git checkout assets/data/
```

This restores all data JSON files to the last committed state.

---

## Example Workflow

User runs: `/balance-run economy`

**Output:**
```
Recommended Balance Fixes for economy

[ ] 1. assets/data/buildings.json[0] → upkeep.gold_cost = 10 (Barracks upkeep)
[ ] 2. assets/data/buildings.json[1] → upkeep.gold_cost = 15 (Smithy upkeep)
[ ] 3. assets/data/units.json[3] → recruitment_cost = 750 (Elite unit)

Select fixes (e.g., "1 2 3" or leave blank for all):
> 1 2 3
```

**Processing:**
```
✓ assets/data/buildings.json: Applied 2 changes
✓ assets/data/units.json: Applied 1 change

Re-run balance-check to verify? (Y/N)
> Y
```

**Verification:**
```
Running /balance-check economy...

Previous outliers: 8
New outliers: 3
✓ Improvement detected (5 issues resolved)
```

---

## Integration with Sprint Tasks

When applying balance fixes:

1. `/balance-check [system]` → generates report
2. Review recommendations against current sprint scope
3. `/balance-run [system]` → apply selected fixes
4. Verify with re-check
5. Update sprint task status
6. Commit all changed JSON files
