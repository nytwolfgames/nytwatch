#!/bin/bash
# Claude Code PostToolUse hook: Queue source file edits for code audit.
# Fires after Write or Edit tool calls. Reads the file_path from JSON stdin.
# Lightweight — just appends to the pending queue. No LLM call.

PENDING_FILE="planning/production/session-state/pending-code-audit.md"
SOURCE_PATHS_FILE=".claude/source-paths.md"

# ── Parse file_path from JSON stdin ──────────────────────────────────────────
# Handles both Write (file_path) and Edit (file_path) tool inputs.
INPUT=$(cat)
FILE_PATH=$(echo "$INPUT" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    # tool_input is under different keys depending on hook schema version
    inp = data.get('tool_input') or data.get('input') or {}
    print(inp.get('file_path', ''))
except Exception:
    pass
" 2>/dev/null)

if [ -z "$FILE_PATH" ]; then
    exit 0
fi

# ── Check extension ───────────────────────────────────────────────────────────
case "$FILE_PATH" in
    *.cpp|*.h|*.cs) ;;
    *) exit 0 ;;
esac

# ── Check in-scope source root ────────────────────────────────────────────────
SOURCE_PATHS=""
if [ -f "$SOURCE_PATHS_FILE" ]; then
    SOURCE_PATHS=$(awk '/^```paths$/,/^```$/' "$SOURCE_PATHS_FILE" \
        | grep -v '^```' | grep '|' \
        | awk -F'|' '{gsub(/^[ \t]+|[ \t]+$/, "", $2); print $2}' \
        | grep -v '^Source Path$' | grep -v '^-' | grep -v '^$')
fi

IN_SCOPE=0
if [ -z "$SOURCE_PATHS" ]; then
    IN_SCOPE=1
else
    while IFS= read -r path; do
        case "$FILE_PATH" in
            "$path"*) IN_SCOPE=1; break ;;
        esac
    done <<< "$SOURCE_PATHS"
fi

if [ "$IN_SCOPE" -eq 0 ]; then
    exit 0
fi

# ── Append to pending queue ───────────────────────────────────────────────────
mkdir -p "planning/production/session-state" 2>/dev/null

TIMESTAMP=$(date +"%Y-%m-%d %H:%M:%S")

if [ ! -f "$PENDING_FILE" ]; then
    # Create file with header
    {
        echo "# Pending Code Audit"
        echo ""
        echo "**Queued at**: $TIMESTAMP"
        echo "**Base SHA**: $(cat .claude/last-audit-sha 2>/dev/null | tr -d '[:space:]' || echo 'none')"
        echo "**Current SHA**: $(git rev-parse HEAD 2>/dev/null || echo 'uncommitted')"
        echo ""
        echo "## Changed Source Files"
        echo ""
    } > "$PENDING_FILE"
fi

# Add file if not already listed
if ! grep -qF "$FILE_PATH" "$PENDING_FILE" 2>/dev/null; then
    echo "- \`$FILE_PATH\`" >> "$PENDING_FILE"
fi

exit 0
