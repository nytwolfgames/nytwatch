---
name: sprint-plan
description: "Generates a new sprint plan or updates an existing one based on the current milestone, completed work, and available capacity. Pulls context from production documents and design backlogs."
argument-hint: "[new|update|status] [--review full|lean|solo]"
user-invocable: true
allowed-tools: Read, Glob, Grep, Write, Edit, Task, AskUserQuestion
context: |
  !ls production/sprints/ 2>/dev/null
---

## Phase 0: Parse Arguments

Extract the mode argument (`new`, `update`, or `status`) and resolve the review mode (once, store for all gate spawns this run):
1. If `--review [full|lean|solo]` was passed → use that
2. Else read `production/review-mode.txt` → use that value
3. Else → default to `lean`

See `.claude/docs/director-gates.md` for the full check pattern.

---

## Phase 1: Gather Context

1. **Read the current milestone** from `production/milestones/`.

2. **Read the previous sprint** (if any) from `production/sprints/` to
   understand velocity and carryover.

3. **Scan design documents** in `design/gdd/` for features tagged as ready
   for implementation.

4. **Check the risk register** at `production/risk-register/`.

---

## Phase 2: Generate Output

For `new`:

**Generate a sprint plan** following this format and present it to the user. Do NOT ask to write yet — the producer feasibility gate (Phase 4) runs first and may require revisions before the file is written.

```markdown
# Sprint [N] -- [Start Date] to [End Date]

**Goal**: [One sentence describing what this sprint achieves toward the milestone]
**Weeks**: [e.g. Weeks 1–2]

## Capacity
- Total days: [X]
- Buffer (20%): [Y days reserved for unplanned work]
- Available: [Z days]

## P0 Items (Must Have — Critical Path)
- [backlog] **[ID]**: [Task description]
  - [acceptance criterion]

## P1 Items (Should Have)
- [backlog] **[ID]**: [Task description]

## P2 Items (Nice to Have)
- [backlog] **[ID]**: [Task description]

## Carryover from Previous Sprint
- [backlog] **[ID]**: [Task description] — Carried from Sprint [N-1]

## Risks
| Risk | Probability | Impact | Mitigation |
|------|------------|--------|------------|

## Dependencies on External Factors
- [List any external dependencies]

## Definition of Done for this Sprint
- [backlog] All P0 tasks completed
- [backlog] All tasks pass acceptance criteria
- [backlog] QA plan exists (`production/qa/qa-plan-sprint-[N].md`)
- [backlog] All Logic/Integration stories have passing unit/integration tests
- [backlog] Smoke check passed (`/smoke-check sprint`)
- [backlog] QA sign-off report: APPROVED or APPROVED WITH CONDITIONS (`/team-qa sprint`)
- [backlog] No S1 or S2 bugs in delivered features
- [backlog] Design documents updated for any deviations
- [backlog] Code reviewed and merged
```

**Status marker values**: `[backlog]` · `[ready]` · `[in_progress]` · `[review]` · `[done]` · `[blocked]`

Update the marker on each task as work progresses — this is the single source of truth for task status. No separate YAML file is needed.

For `status`:

**Generate a status report**:

```markdown
# Sprint [N] Status -- [Date]

## Progress: [X/Y tasks complete] ([Z%])

### Completed
| Task | Completed By | Notes |
|------|-------------|-------|

### In Progress
| Task | Owner | % Done | Blockers |
|------|-------|--------|----------|

### Not Started
| Task | Owner | At Risk? | Notes |
|------|-------|----------|-------|

### Blocked
| Task | Blocker | Owner of Blocker | ETA |
|------|---------|-----------------|-----|

## Burndown Assessment
[On track / Behind / Ahead]
[If behind: What is being cut or deferred]

## Emerging Risks
- [Any new risks identified this sprint]
```

---

## Phase 3: Status Tracking

Task status is tracked directly in the sprint markdown file using `[{status}]` markers
on each checklist item. No separate YAML file is needed or written.

Valid markers: `[backlog]` · `[ready]` · `[in_progress]` · `[review]` · `[done]` · `[blocked]`

- P0 tasks start as `[ready]` (critical path, ready to begin)
- P1 and P2 tasks start as `[backlog]` (not yet started)

When updating an existing sprint plan (`update` mode): edit the markers in-place on each
task line to reflect current status. Do not add or remove tasks unless explicitly asked.

---

## Phase 4: Producer Feasibility Gate

**Review mode check** — apply before spawning PR-SPRINT:
- `solo` → skip. Note: "PR-SPRINT skipped — Solo mode." Proceed to Phase 5 (QA plan gate).
- `lean` → skip (not a PHASE-GATE). Note: "PR-SPRINT skipped — Lean mode." Proceed to Phase 5 (QA plan gate).
- `full` → spawn as normal.

Before finalising the sprint plan, spawn `producer` via Task using gate **PR-SPRINT** (`.claude/docs/director-gates.md`).

Pass: proposed story list (titles, estimates, dependencies), total team capacity in hours/days, any carryover from the previous sprint, milestone constraints and deadline.

Present the producer's assessment. If UNREALISTIC, revise the story selection (defer stories to Should Have or Nice to Have) before asking for write approval. If CONCERNS, surface them and let the user decide whether to adjust.

After handling the producer's verdict, ask: "May I write this sprint plan to `production/sprints/sprint-[N].md`?" If yes, write the file, creating the directory if needed. Verdict: **COMPLETE** — sprint plan created. If no: Verdict: **BLOCKED** — user declined write.

After writing, add:

> **Scope check:** If this sprint includes stories added beyond the original epic scope, run `/scope-check [epic]` to detect scope creep before implementation begins.

---

## Phase 5: QA Plan Gate

Before closing the sprint plan, check whether a QA plan exists for this sprint.

Use `Glob` to look for `production/qa/qa-plan-sprint-[N].md` or any file in `production/qa/` referencing this sprint number.

**If a QA plan is found**: note it in the sprint plan output — "QA Plan: `[path]`" — and proceed.

**If no QA plan exists**: do not silently proceed. Surface this explicitly:

> "This sprint has no QA plan. A sprint plan without a QA plan means test requirements are undefined — developers won't know what 'done' looks like from a QA perspective, and the sprint cannot pass the Production → Polish gate without one.
>
> Run `/qa-plan sprint` now, before starting any implementation. It takes one session and produces the test case requirements each story needs."

Use `AskUserQuestion`:
- Prompt: "No QA plan found for this sprint. How do you want to proceed?"
- Options:
  - `[A] Run /qa-plan sprint now — I'll do that before starting implementation (Recommended)`
  - `[B] Skip for now — I understand QA sign-off will be blocked at the Production → Polish gate`

If [A]: close with "Sprint plan written. Run `/qa-plan sprint` next — then begin implementation."
If [B]: add a warning block to the sprint plan document:

```markdown
> ⚠️ **No QA Plan**: This sprint was started without a QA plan. Run `/qa-plan sprint`
> before the last story is implemented. The Production → Polish gate requires a QA
> sign-off report, which requires a QA plan.
```

---

## Phase 6: Next Steps

After the sprint plan is written and QA plan status is resolved:

- `/qa-plan sprint` — **required before implementation begins** — defines test cases per story so developers implement against QA specs, not a blank slate
- `/story-readiness [story-file]` — validate a story is ready before starting it
- `/dev-story [story-file]` — begin implementing the first story
- `/sprint-status` — check progress mid-sprint
- `/scope-check [epic]` — verify no scope creep before implementation begins
