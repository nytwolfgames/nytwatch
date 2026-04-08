---
name: sprint-close
description: "Formally closes a sprint. Verifies all P0 tasks are done, runs the QA/smoke-check gate sequence, tallies velocity, handles carryover of incomplete tasks to the next sprint, marks the sprint file as Closed, and writes a retrospective summary. Use when a sprint's implementation work is complete and the team is ready to ship it."
argument-hint: "[sprint-number or blank for current] [--review full|lean|solo]"
user-invocable: true
allowed-tools: Read, Glob, Grep, Write, Edit, Bash, Task, AskUserQuestion
---

# Sprint Close

This skill is the formal end-of-sprint ceremony. It answers the question:
**"Is this sprint actually done, and what do we carry forward?"**

It works in three acts:
1. **Verify** — check that all P0 tasks are `[done]` before closing
2. **Gate** — run smoke check and QA sign-off (based on review mode)
3. **Close** — tally velocity, handle carryover, mark the sprint closed, write the retro summary

**Output:**
- Sprint markdown file updated with `**Status**: Closed` and `**Closed**: [date]`
- `production/retrospectives/retro-sprint-[N]-[date].md`

---

## Phase 0: Parse Arguments

**Sprint number:** `$ARGUMENTS[0]`
- If provided (e.g., `/sprint-close 3`), find `production/sprints/sprint-03.md` or `sprint-3.md`
- If blank, use the most recently modified file in `production/sprints/`

**Review mode** (resolve once, use for all gate spawns):
1. If `--review [full|lean|solo]` was passed → use that
2. Else read `production/review-mode.txt` → use that value
3. Else → default to `lean`

If no sprint files exist in `production/sprints/`, output:
> "No sprint files found. Start a sprint with `/sprint-plan new`."
Then stop.

---

## Phase 1: Read the Sprint

Read the sprint file in full. Extract:

- Sprint number and title
- Start date and end date
- Sprint goal (from `**Goal**:` or `## Sprint Goal` section)
- All tasks from P0, P1, and P2 sections with their current `[{status}]` markers
- Any `**Status**:` field in the sprint header (check if already closed)

**If the sprint is already marked `**Status**: Closed`**, report:

> "Sprint [N] is already closed (closed on [date])."

Then use `AskUserQuestion`:
- "This sprint is already closed. What would you like to do?"
- Options:
  - `[A] Re-run close — refresh the retrospective with current data`
  - `[B] Stop — sprint is already closed`

If [B]: stop here.

---

## Phase 2: Pre-Close Verification

Categorize every task by its current marker:

| Marker | Meaning |
|--------|---------|
| `[done]` | Complete |
| `[in_progress]` or `[review]` | Still active |
| `[backlog]` or `[ready]` | Not started |
| `[blocked]` | Blocked |

Produce a task status table grouped by priority tier (P0 / P1 / P2):

```
### P0 Items (Must Have)
| Task | Status |
|------|--------|
| [name] | [done] ✓ |
| [name] | [in_progress] ⚠ |

### P1 Items (Should Have)
| Task | Status |
|------|--------|
| [name] | [done] ✓ |
| [name] | [backlog] → carryover |

### P2 Items (Nice to Have)
| Task | Status |
|------|--------|
| [name] | [backlog] → carryover |
```

### P0 Blocker Check

If any P0 task is **not** `[done]`:

Present the incomplete P0 list and use `AskUserQuestion`:

```
question: "[N] P0 tasks are not done. Closing a sprint with incomplete P0s is
          unusual — these are Must Have items. How do you want to proceed?"
options:
  - "[A] Fix blockers first — I'll mark them done and re-run /sprint-close"
  - "[B] Accept and close anyway — I understand P0s are incomplete"
  - "[C] Extend the sprint — don't close yet, add more time"
```

- If [A]: stop here. User will fix and re-run.
- If [B]: proceed, but flag each incomplete P0 as a **carryover blocker** in the retrospective.
- If [C]: stop here. Sprint remains open.

---

## Phase 3: Smoke Check Gate

**Review mode check:**
- `solo` → skip. Note: "Smoke check skipped — Solo mode." Proceed to Phase 4.
- `lean` → skip (smoke check is a phase-gate, not a lean gate). Note: "Smoke check skipped — Lean mode." Proceed to Phase 4.
- `full` → prompt to run smoke check.

For `full` mode, check whether a passing smoke check report already exists for this sprint:

Use `Glob` to look for `production/qa/smoke-*.md`. Read the most recent one and check if:
- Its sprint number matches the current sprint
- Its verdict is `PASS` or `PASS WITH WARNINGS`

If a valid smoke check report is found:
> "Smoke check already on file for Sprint [N]: `[path]` — verdict [PASS / PASS WITH WARNINGS]. Using this report."

Proceed to Phase 4.

If no valid smoke check is found, use `AskUserQuestion`:

```
question: "No smoke check report found for Sprint [N]. A passing smoke check is
          required before closing in Full review mode."
options:
  - "[A] Run /smoke-check sprint now (recommended)"
  - "[B] I've run it manually — the build is clean, proceed"
  - "[C] Skip smoke check — I accept the risk"
```

- If [A]: spawn `smoke-check` skill via Task with argument `sprint`. Wait for result. If verdict is FAIL, surface the failures and stop — do not close a sprint that fails smoke check. If PASS or PASS WITH WARNINGS, proceed.
- If [B] or [C]: proceed with a note in the retrospective: "Smoke check: manually confirmed / skipped."

---

## Phase 4: QA Sign-Off Gate

**Review mode check:**
- `solo` → skip. Note: "QA gate skipped — Solo mode." Proceed to Phase 5.
- `lean` → skip. Note: "QA gate skipped — Lean mode." Proceed to Phase 5.
- `full` → check for QA sign-off report.

For `full` mode, check whether a QA sign-off report exists for this sprint:

Use `Glob` to look for `production/qa/` and find a file referencing this sprint (e.g., `qa-report-sprint-[N]-*.md` or any QA report with the sprint number). Read its verdict line.

If a report with verdict `APPROVED` or `APPROVED WITH CONDITIONS` is found:
> "QA sign-off on file for Sprint [N]: `[path]` — verdict [APPROVED / APPROVED WITH CONDITIONS]."

Proceed to Phase 5.

If no QA sign-off is found:

```
question: "No QA sign-off report found for Sprint [N]. Run /team-qa to get
          sign-off before closing in Full review mode."
options:
  - "[A] Run /team-qa sprint now (recommended)"
  - "[B] QA was done informally — proceed without a formal report"
  - "[C] Skip QA gate — I accept the risk"
```

- If [A]: spawn `team-qa` skill via Task with argument `sprint`. Wait for result. If verdict is FAIL or BLOCKED, surface it and stop. If APPROVED or APPROVED WITH CONDITIONS, proceed.
- If [B] or [C]: proceed with a note in the retrospective.

---

## Phase 5: Velocity and Carryover

### Velocity Tally

Count tasks by completion status and priority:

| Tier | Planned | Done | Incomplete | Completion % |
|------|---------|------|------------|-------------|
| P0 (Must Have) | [N] | [N] | [N] | [%] |
| P1 (Should Have) | [N] | [N] | [N] | [%] |
| P2 (Nice to Have) | [N] | [N] | [N] | [%] |
| **Total** | [N] | [N] | [N] | [%] |

Calculate **estimated days** vs **actual days elapsed** (from sprint dates).

Check previous sprint retrospectives (if any) in `production/retrospectives/` to detect
velocity trends: improving, stable, or declining.

### Carryover Decision

Collect all incomplete tasks (P0, P1, P2 that are not `[done]`).

For each incomplete task, propose a disposition:

- Incomplete P0s → **Carry over to next sprint as P0** (highest priority)
- Incomplete P1s → **Carry over as P1** (team decides)
- Incomplete P2s → **Drop or carry over as P2** (team decides)

Use `AskUserQuestion` to confirm carryover decisions (batch all in one call if ≤ 4 tasks; use multiple calls if more):

```
question: "[N] tasks are incomplete. Choose disposition for each:"
options:
  - "Carry over [task name] to next sprint as P0"
  - "Carry over [task name] to next sprint as P1"
  - "Carry over [task name] to next sprint as P2"
  - "Drop [task name] — descoped, not needed"
```

Record the user's choices. Carryover tasks will be listed in the retrospective for the next `/sprint-plan new` to include.

---

## Phase 6: Present the Closing Summary

Before writing any files, present the full closing summary for review:

```markdown
## Sprint [N] Close — [Today's Date]

**Sprint Goal**: [goal]
**Dates**: [start] → [end] ([N] days)
**Closed**: [today]

### Velocity
| Tier | Planned | Done | % |
|------|---------|------|---|
| P0 | [N] | [N] | [%] |
| P1 | [N] | [N] | [%] |
| P2 | [N] | [N] | [%] |
| **Total** | [N] | [N] | **[%]** |

### Completed Tasks
- [done] **[ID]**: [name]
- [done] **[ID]**: [name]

### Incomplete / Carryover
- [status] **[ID]**: [name] → [Carry over as P0 / Carry over as P1 / Dropped]

### Gate Results
- Smoke Check: [PASS / PASS WITH WARNINGS / Skipped]
- QA Sign-Off: [APPROVED / APPROVED WITH CONDITIONS / Skipped]

### Sprint Assessment
[2-3 sentences: was the goal achieved? why were items incomplete? any patterns?]

### Recommended Carryover for Sprint [N+1]
- **P0**: [task names, if any]
- **P1**: [task names, if any]
```

---

## Phase 7: Write the Files

Ask: "May I mark Sprint [N] as closed and write the retrospective summary?"

If yes, perform these writes:

### 7a. Update the Sprint File

Edit the sprint markdown to add a Closed status block directly below the H1 title line:

```markdown
**Status**: Closed
**Closed**: [YYYY-MM-DD]
```

If a `**Status**:` line already exists (e.g., `**Status**: Active`), replace it.
If no `**Status**:` line exists, insert after the first line of the sprint goal block.

### 7b. Write the Retrospective File

Write to `production/retrospectives/retro-sprint-[N]-[YYYY-MM-DD].md`:

```markdown
# Sprint [N] Retrospective — [Date]

**Sprint**: [title]
**Dates**: [start] → [end]
**Closed**: [today]
**Velocity**: [N/N tasks done] ([%])

## Goal Assessment
**Goal**: [sprint goal]
**Achieved**: [Yes / Partially / No]
[1-2 sentences explaining the assessment]

## Velocity
| Tier | Planned | Done | Incomplete | % |
|------|---------|------|------------|---|
| P0 (Must Have) | [N] | [N] | [N] | [%] |
| P1 (Should Have) | [N] | [N] | [N] | [%] |
| P2 (Nice to Have) | [N] | [N] | [N] | [%] |
| **Total** | [N] | [N] | [N] | **[%]** |

## Completed
[List of [done] tasks with IDs]

## Carryover
[List of carried-over tasks with their new priority tier, or "None"]

## Dropped
[List of descoped tasks, or "None"]

## Gate Results
- **Smoke Check**: [PASS / PASS WITH WARNINGS / Skipped (solo/lean/manual)]
- **QA Sign-Off**: [APPROVED / APPROVED WITH CONDITIONS / Skipped]

## What Went Well
[Leave blank — fill in during /retrospective if desired]

## What Could Be Better
[Leave blank — fill in during /retrospective if desired]

## Action Items for Next Sprint
- Carry over: [task list]
- Process improvements: [blank — fill during /retrospective]

## Notes
[Any observations from the close: blockers encountered, scope changes, dependencies]
```

---

## Phase 8: Next Steps

After the files are written, close with:

```
### Sprint [N] is closed.

**Carryover for Sprint [N+1]:**
[list carried items, or "None — clean slate"]

**Recommended next steps:**
1. `/sprint-plan new` — plan Sprint [N+1] (include carryover items listed above)
2. `/retrospective sprint-[N]` — run a full retrospective to capture team learnings
3. `/milestone-review [milestone]` — check milestone progress after this sprint
```

---

## Collaborative Protocol

- **Never close a sprint automatically** — Phase 7 requires explicit user approval before any file is edited or written.
- **Incomplete P0s require a decision** — don't silently close a sprint that has outstanding Must Have work; surface it and let the user decide.
- **Carryover is a decision, not an assumption** — always ask before assigning incomplete tasks to the next sprint.
- **Smoke check FAIL is a hard stop** — do not proceed past Phase 3 if the sprint fails smoke check in Full review mode. A broken build should not be formally closed.
- **Retrospective content is yours** — the "What Went Well / What Could Be Better" sections are left blank intentionally. Fill them in using `/retrospective sprint-[N]` for a deeper facilitated session.
