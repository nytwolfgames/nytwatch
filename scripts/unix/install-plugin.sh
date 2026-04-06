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
# 3. Interactive project selection menu
# ---------------------------------------------------------------------------
_mcursor=0
declare -A _msel=()

_draw_menu() {
    local i prefix check
    for ((i = 0; i < total; i++)); do
        if (( i == _mcursor )); then
            prefix=" \033[36m►\033[0m "
        else
            prefix="   "
        fi
        if [[ -v _msel[$i] ]]; then
            check="\033[32m[x]\033[0m"
        else
            check="[ ]"
        fi
        printf "\r\033[2K${prefix}${check}  %s\n" "${project_names[$i]}"
        printf "\r\033[2K         \033[90m%s\033[0m\n" "${project_paths[$i]}"
    done
}

printf "\n"
printf "   Use \033[36m↑↓\033[0m to navigate,  \033[36mSpace\033[0m to select,  "
printf "\033[36mEnter\033[0m to confirm,  \033[36mA\033[0m to toggle all.\n\n"

tput civis 2>/dev/null || true          # hide cursor
tput sc    2>/dev/null || printf "\033[s"  # save cursor position
_draw_menu

_mdone=0
while (( ! _mdone )); do
    IFS= read -r -s -n1 _k </dev/tty || true
    case "$_k" in
        $'\x1b')
            IFS= read -r -s -n2 -t 0.15 _seq </dev/tty 2>/dev/null || _seq=""
            case "$_seq" in
                '[A') (( _mcursor > 0       )) && (( _mcursor-- )) || true ;;  # Up
                '[B') (( _mcursor < total-1 )) && (( _mcursor++ )) || true ;;  # Down
            esac
            ;;
        ' ')
            if [[ -v _msel[$_mcursor] ]]; then
                unset "_msel[$_mcursor]"
            else
                _msel[$_mcursor]=1
            fi
            ;;
        '')  # Enter (read strips the newline delimiter)
            (( ${#_msel[@]} > 0 )) && _mdone=1 || true
            ;;
        a|A)
            if (( ${#_msel[@]} == total )); then
                unset _msel; declare -A _msel=()
            else
                for ((i = 0; i < total; i++)); do _msel[$i]=1; done
            fi
            ;;
    esac
    if (( ! _mdone )); then
        tput rc 2>/dev/null || printf "\033[u"
        _draw_menu
    fi
done

tput cnorm 2>/dev/null || true  # restore cursor
printf "\n"

selected_indices=()
for _i in "${!_msel[@]}"; do selected_indices+=("$_i"); done
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
    info "  3. Start the Nytwatch server and arm systems from the Tracker page"
else
    printf "\033[33mCompleted with errors.\033[0m\n\n"
    info "Failed:"
    for f in "${failed[@]}"; do printf "   \033[31m- %s\033[0m\n" "$f"; done
    printf "\n"
    info "Re-run this script for failed projects after resolving any issues."
fi
printf "\n"
