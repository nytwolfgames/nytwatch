#!/bin/bash
# Claude Code SessionStart hook: Load project context at session start
# Outputs context information that Claude sees when a session begins
#
# Input schema (SessionStart): No stdin input

echo "=== Claude Code Game Studios — Session Context ==="

# Current branch
BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null)
if [ -n "$BRANCH" ]; then
    echo "Branch: $BRANCH"

    # Recent commits
    echo ""
    echo "Recent commits:"
    git log --oneline -5 2>/dev/null | while read -r line; do
        echo "  $line"
    done
fi

# Current sprint — walk the Previous Sprint chain to find the active open sprint.
# Active sprint = Status: open AND (Previous Sprint: none OR previous sprint is closed).
ACTIVE_SPRINT=""
for sprint_file in $(ls production/sprints/sprint-*.md 2>/dev/null | sort); do
    STATUS=$(grep -im1 "^\*\*Status\*\*:" "$sprint_file" | sed 's/.*: *//' | tr -d ' \r')
    if [ "$(echo "$STATUS" | tr '[:upper:]' '[:lower:]')" = "open" ]; then
        PREV=$(grep -im1 "^\*\*Previous Sprint\*\*:" "$sprint_file" | sed 's/.*: *//' | tr -d ' \r')
        if [ -z "$PREV" ] || [ "$(echo "$PREV" | tr '[:upper:]' '[:lower:]')" = "none" ]; then
            ACTIVE_SPRINT="$sprint_file"
            break
        fi
        PREV_FILE="production/sprints/${PREV}.md"
        if [ -f "$PREV_FILE" ]; then
            PREV_STATUS=$(grep -im1 "^\*\*Status\*\*:" "$PREV_FILE" | sed 's/.*: *//' | tr -d ' \r')
            if [ "$(echo "$PREV_STATUS" | tr '[:upper:]' '[:lower:]')" = "closed" ]; then
                ACTIVE_SPRINT="$sprint_file"
                break
            fi
        fi
    fi
done
if [ -n "$ACTIVE_SPRINT" ]; then
    echo ""
    echo "Active sprint: $(basename "$LATEST_SPRINT" .md)"
    echo "Active sprint: $(basename "$ACTIVE_SPRINT" .md)"
fi

# Current milestone
LATEST_MILESTONE=$(ls -t production/milestones/*.md 2>/dev/null | head -1)
if [ -n "$LATEST_MILESTONE" ]; then
    echo "Active milestone: $(basename "$LATEST_MILESTONE" .md)"
fi

# Open bug count
BUG_COUNT=0
for dir in tests/playtest production; do
    if [ -d "$dir" ]; then
        count=$(find "$dir" -name "BUG-*.md" 2>/dev/null | wc -l)
        BUG_COUNT=$((BUG_COUNT + count))
    fi
done
if [ "$BUG_COUNT" -gt 0 ]; then
    echo "Open bugs: $BUG_COUNT"
fi

# Code health quick check
if [ -d "src" ]; then
    TODO_COUNT=$(grep -r "TODO" src/ 2>/dev/null | wc -l)
    FIXME_COUNT=$(grep -r "FIXME" src/ 2>/dev/null | wc -l)
    if [ "$TODO_COUNT" -gt 0 ] || [ "$FIXME_COUNT" -gt 0 ]; then
        echo ""
        echo "Code health: ${TODO_COUNT} TODOs, ${FIXME_COUNT} FIXMEs in src/"
    fi
fi

# --- Active session state recovery ---
STATE_FILE="production/session-state/active.md"
if [ -f "$STATE_FILE" ]; then
    echo ""
    echo "=== ACTIVE SESSION STATE DETECTED ==="
    echo "A previous session left state at: $STATE_FILE"
    echo "Read this file to recover context and continue where you left off."
    echo ""
    echo "Quick summary:"
    head -20 "$STATE_FILE" 2>/dev/null
    TOTAL_LINES=$(wc -l < "$STATE_FILE" 2>/dev/null)
    if [ "$TOTAL_LINES" -gt 20 ]; then
        echo "  ... ($TOTAL_LINES total lines — read the full file to continue)"
    fi
    echo "=== END SESSION STATE PREVIEW ==="
fi

echo "==================================="
exit 0
