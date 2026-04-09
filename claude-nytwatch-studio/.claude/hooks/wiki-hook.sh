#!/bin/bash
# Claude Code Stop hook: Queue wiki update based on files changed this session.
# Runs at session end. Fast path — no LLM call here.
# Use /wiki-update skill to apply the queued update.

WIKI_DIR="planning/production/wiki"
SHA_FILE="$WIKI_DIR/.last-hook-sha"
PENDING_FILE="$WIKI_DIR/.pending-update.md"

# ── Recursion guard ──────────────────────────────────────────────────────────
# If only wiki files changed (e.g. /wiki-update just ran), do nothing.
ALL_CHANGED=$(git diff --name-only 2>/dev/null; git diff HEAD --name-only 2>/dev/null)
NON_WIKI=$(echo "$ALL_CHANGED" | grep -v "^planning/production/wiki/" | sort -u)

if [ -z "$NON_WIKI" ]; then
    exit 0
fi

# ── Get changed files since last hook run ────────────────────────────────────
LAST_SHA=""
if [ -f "$SHA_FILE" ]; then
    LAST_SHA=$(cat "$SHA_FILE" | tr -d '[:space:]')
fi

CURRENT_SHA=$(git rev-parse HEAD 2>/dev/null)

if [ -n "$LAST_SHA" ] && [ "$LAST_SHA" != "$CURRENT_SHA" ]; then
    COMMITTED_CHANGES=$(git diff --name-only "$LAST_SHA..$CURRENT_SHA" 2>/dev/null | grep -v "^planning/production/wiki/")
else
    COMMITTED_CHANGES=""
fi

UNCOMMITTED_CHANGES=$(git diff HEAD --name-only 2>/dev/null | grep -v "^planning/production/wiki/")

CHANGED_FILES=$(printf "%s\n%s" "$COMMITTED_CHANGES" "$UNCOMMITTED_CHANGES" | grep -v "^$" | sort -u)

if [ -z "$CHANGED_FILES" ]; then
    exit 0
fi

# ── Load in-scope source paths from config ───────────────────────────────────
SOURCE_PATHS_FILE=".claude/source-paths.md"
SOURCE_PATHS=""
if [ -f "$SOURCE_PATHS_FILE" ]; then
    SOURCE_PATHS=$(awk '/^```paths$/,/^```$/' "$SOURCE_PATHS_FILE" \
        | grep -v '^```' | grep -v '^#' | grep -v '^$')
fi

is_in_scope() {
    local file="$1"
    if [ -z "$SOURCE_PATHS" ]; then
        echo "in-scope"
        return
    fi
    while IFS= read -r path; do
        case "$file" in
            "$path"*) echo "in-scope"; return ;;
        esac
    done <<< "$SOURCE_PATHS"
    echo "external"
}

# ── Write pending update file ────────────────────────────────────────────────
mkdir -p "$WIKI_DIR" 2>/dev/null

TIMESTAMP=$(date +"%Y-%m-%d %H:%M:%S")

{
    echo "# Pending Wiki Update"
    echo ""
    echo "**Queued at**: $TIMESTAMP"
    echo "**Base SHA**: ${LAST_SHA:-none}"
    echo "**Current SHA**: ${CURRENT_SHA:-uncommitted}"
    echo ""
    echo "## Changed Files"
    echo ""
    echo "$CHANGED_FILES" | while read -r f; do
        scope=$(is_in_scope "$f")
        echo "- \`$f\` [$scope]"
    done
    echo ""
    echo "## Diffs (truncated at 300 lines each)"
    echo ""
    echo "$CHANGED_FILES" | while read -r f; do
        if [ -f "$f" ]; then
            scope=$(is_in_scope "$f")
            echo "### \`$f\` [$scope]"
            echo '```'
            if [ -n "$LAST_SHA" ] && [ "$LAST_SHA" != "$CURRENT_SHA" ]; then
                git diff "$LAST_SHA" -- "$f" 2>/dev/null | head -300
            else
                git diff HEAD -- "$f" 2>/dev/null | head -300
            fi
            echo '```'
            echo ""
        fi
    done
} > "$PENDING_FILE"

# ── Update SHA tracker ───────────────────────────────────────────────────────
if [ -n "$CURRENT_SHA" ]; then
    echo "$CURRENT_SHA" > "$SHA_FILE"
fi

echo "[wiki-hook] Wiki update queued for $(echo "$CHANGED_FILES" | wc -l | tr -d ' ') file(s). Run /wiki-update to apply."

exit 0
