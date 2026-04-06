#!/usr/bin/env bash
# Nytwatch - NytwatchAgent Plugin Installer (macOS / Linux)
# Lists configured projects and installs the UE5 plugin into selected ones.
# No sudo required.

set -euo pipefail

step()  { printf "\n\033[36m>> %s\033[0m\n" "$*"; }
ok()    { printf "   \033[32mOK\033[0m   %s\n" "$*"; }
warn()  { printf "   \033[33mWARN\033[0m %s\n" "$*"; }
fail()  { printf "\n   \033[31mERROR\033[0m %s\n" "$*"; exit 1; }
info()  { printf "   %s\n" "$*"; }

# ---------------------------------------------------------------------------
# 1. Locate nytwatch CLI
# ---------------------------------------------------------------------------
step "Locating Nytwatch..."

NYW_CMD=""
if command -v nytwatch &>/dev/null; then
    NYW_CMD="nytwatch"
else
    for candidate in python3 python; do
        if command -v "$candidate" &>/dev/null; then
            bin_dir=$("$candidate" -c "import sysconfig; print(sysconfig.get_path('scripts'))" 2>/dev/null || true)
            if [ -x "$bin_dir/nytwatch" ]; then
                NYW_CMD="$bin_dir/nytwatch"
                break
            fi
        fi
    done
fi

[ -n "$NYW_CMD" ] || fail "nytwatch not found on PATH. Run install.sh first."
ok "Found: $NYW_CMD"

# ---------------------------------------------------------------------------
# 2. Load configured projects (requires python3 or jq for JSON parsing)
# ---------------------------------------------------------------------------
step "Loading configured projects..."

PYTHON_BIN=""
for candidate in python3 python; do
    if command -v "$candidate" &>/dev/null; then
        PYTHON_BIN="$candidate"
        break
    fi
done
[ -n "$PYTHON_BIN" ] || fail "Python not found — needed to parse project list."

projects_json=$("$NYW_CMD" list-projects 2>&1) || fail "Failed to list projects: $projects_json"

# Parse with Python: extract name and repo_path arrays
mapfile -t project_names  < <("$PYTHON_BIN" -c "
import json, sys
data = json.loads(sys.stdin.read())
for p in data: print(p['name'])
" <<< "$projects_json")

mapfile -t project_paths  < <("$PYTHON_BIN" -c "
import json, sys
data = json.loads(sys.stdin.read())
for p in data: print(p['repo_path'])
" <<< "$projects_json")

total=${#project_names[@]}
[ "$total" -gt 0 ] || fail "No projects configured in Nytwatch yet. Set up a project first via the Nytwatch dashboard."

# ---------------------------------------------------------------------------
# 3. Display project menu and collect selection
# ---------------------------------------------------------------------------
printf "\n"
printf "   \033[1mAvailable projects:\033[0m\n\n"

for ((i=0; i<total; i++)); do
    printf "   [%d]  %s\n" "$((i+1))" "${project_names[$i]}"
    printf "         \033[90m%s\033[0m\n" "${project_paths[$i]}"
done

printf "\n"
info "Enter project number(s) to install into, separated by spaces."
info "Example: 1   or   1 3   or   all"
printf "\n"

printf "   Selection: "
read -r raw || true
raw=$(echo "$raw" | tr '[:upper:]' '[:lower:]' | xargs)

selected_indices=()

if [ "$raw" = "all" ]; then
    for ((i=0; i<total; i++)); do selected_indices+=("$i"); done
else
    for token in $raw; do
        if [[ "$token" =~ ^[0-9]+$ ]] && [ "$token" -ge 1 ] && [ "$token" -le "$total" ]; then
            selected_indices+=("$((token-1))")
        else
            warn "Ignoring invalid selection: $token"
        fi
    done
fi

[ "${#selected_indices[@]}" -gt 0 ] || fail "No valid projects selected."

# Deduplicate
mapfile -t selected_indices < <(printf '%s\n' "${selected_indices[@]}" | sort -un)

# ---------------------------------------------------------------------------
# 4. Install into each selected project
# ---------------------------------------------------------------------------
failed=()

for idx in "${selected_indices[@]}"; do
    name="${project_names[$idx]}"
    path="${project_paths[$idx]}"
    step "Installing into: $name"
    info "$path"
    if "$NYW_CMD" install-plugin --project "$path"; then
        ok "Done: $name"
    else
        warn "Installation failed for: $name"
        failed+=("$name")
    fi
done

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
printf "\n"
if [ "${#failed[@]}" -eq 0 ]; then
    printf "\033[32mAll selected projects updated successfully.\033[0m\n\n"
    info "Next steps:"
    info "  1. Open each project in the Unreal Editor"
    info "  2. Recompile when prompted"
    info "  3. Start the Nytwatch server and arm systems from the Sessions page"
else
    printf "\033[33mCompleted with errors.\033[0m\n\n"
    info "Failed:"
    for f in "${failed[@]}"; do printf "   \033[31m- %s\033[0m\n" "$f"; done
    printf "\n"
    info "Re-run this script for failed projects after resolving any issues."
fi
printf "\n"
