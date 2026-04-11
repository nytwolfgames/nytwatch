#!/bin/bash
# Claude Code Stop hook: Queue pending code audit based on source files changed this session.
# Mirrors wiki-hook.sh but scopes to in-scope source files only (.cpp, .h, .cs).
# Runs at session end. Fast path — no LLM call here.
# Use /code-audit to apply the queued audit.

PENDING_FILE="planning/production/session-state/pending-code-audit.md"
SHA_FILE=".claude/last-audit-sha"
SOURCE_PATHS_FILE=".claude/source-paths.md"

# ── Ensure output directory exists ───────────────────────────────────────────
mkdir -p "planning/production/session-state" 2>/dev/null

# ── Recursion guard: skip if only doc/wiki files changed ─────────────────────
ALL_CHANGED=$(git diff --name-only 2>/dev/null; git diff HEAD --name-only 2>/dev/null)
NON_DOC=$(echo "$ALL_CHANGED" \
    | grep -v "^planning/" \
    | grep -v "^\.claude/" \
    | sort -u)

if [ -z "$NON_DOC" ]; then
    exit 0
fi

# ── Get in-scope source paths from config ────────────────────────────────────
SOURCE_PATHS=""
if [ -f "$SOURCE_PATHS_FILE" ]; then
    SOURCE_PATHS=$(awk '/^```paths$/,/^```$/' "$SOURCE_PATHS_FILE" \
        | grep -v '^```' | grep '|' \
        | awk -F'|' '{gsub(/^[ \t]+|[ \t]+$/, "", $2); print $2}' \
        | grep -v '^Source Path$' | grep -v '^-' | grep -v '^$')
fi

is_source_file() {
    local file="$1"
    # Must be a source code extension
    case "$file" in
        *.cpp|*.h|*.cs) ;;
        *) echo "skip"; return ;;
    esac
    # Must be in an in-scope source root
    if [ -z "$SOURCE_PATHS" ]; then
        echo "in-scope"
        return
    fi
    while IFS= read -r path; do
        case "$file" in
            "$path"*) echo "in-scope"; return ;;
        esac
    done <<< "$SOURCE_PATHS"
    echo "skip"
}

# ── Get SHAs ─────────────────────────────────────────────────────────────────
LAST_SHA=""
if [ -f "$SHA_FILE" ]; then
    LAST_SHA=$(cat "$SHA_FILE" | tr -d '[:space:]')
fi

CURRENT_SHA=$(git rev-parse HEAD 2>/dev/null)

# ── Collect changed files ────────────────────────────────────────────────────
if [ -n "$LAST_SHA" ] && [ "$LAST_SHA" != "$CURRENT_SHA" ]; then
    COMMITTED=$(git diff --name-only "$LAST_SHA..$CURRENT_SHA" 2>/dev/null)
else
    COMMITTED=""
fi

UNCOMMITTED=$(git diff HEAD --name-only 2>/dev/null)

ALL_FILES=$(printf "%s\n%s" "$COMMITTED" "$UNCOMMITTED" | grep -v "^$" | sort -u)

# ── Filter to in-scope source files only ─────────────────────────────────────
SOURCE_FILES=""
while IFS= read -r f; do
    [ -z "$f" ] && continue
    result=$(is_source_file "$f")
    if [ "$result" = "in-scope" ]; then
        SOURCE_FILES="$SOURCE_FILES
$f"
    fi
done <<< "$ALL_FILES"

SOURCE_FILES=$(echo "$SOURCE_FILES" | grep -v "^$")

if [ -z "$SOURCE_FILES" ]; then
    exit 0
fi

# ── Write pending audit file ──────────────────────────────────────────────────
TIMESTAMP=$(date +"%Y-%m-%d %H:%M:%S")

{
    echo "# Pending Code Audit"
    echo ""
    echo "**Queued at**: $TIMESTAMP"
    echo "**Base SHA**: ${LAST_SHA:-none}"
    echo "**Current SHA**: ${CURRENT_SHA:-uncommitted}"
    echo ""
    echo "## Changed Source Files"
    echo ""
    echo "$SOURCE_FILES" | while read -r f; do
        [ -z "$f" ] && continue
        echo "- \`$f\`"
    done
    echo ""
    echo "## Diffs (truncated at 200 lines each)"
    echo ""
    echo "$SOURCE_FILES" | while read -r f; do
        [ -z "$f" ] && continue
        if [ -f "$f" ]; then
            echo "### \`$f\`"
            echo '```'
            if [ -n "$LAST_SHA" ] && [ "$LAST_SHA" != "$CURRENT_SHA" ]; then
                git diff "$LAST_SHA" -- "$f" 2>/dev/null | head -200
            else
                git diff HEAD -- "$f" 2>/dev/null | head -200
            fi
            echo '```'
            echo ""
        fi
    done
} > "$PENDING_FILE"

FILE_COUNT=$(echo "$SOURCE_FILES" | grep -c ".")

echo "[code-audit-hook] Code audit queued for $FILE_COUNT source file(s). Run /code-audit to apply."

exit 0
