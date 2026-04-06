#!/usr/bin/env bash
# Nytwatch - NytwatchAgent Plugin Installer (macOS / Linux)
# Interactively installs the UE5 plugin into one or more game projects.
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
    # Try Python sysconfig (handles venvs / editable installs)
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
# 2. Collect project paths interactively
# ---------------------------------------------------------------------------
step "Project selection"
printf "\n"
info "Enter the full path to each Unreal Engine project root."
info "The directory must contain a .uproject file."
info "Press Enter on a blank line when done."
printf "\n"

projects=()

while true; do
    printf "   Project path: "
    read -r raw || true
    raw="${raw%"${raw##*[![:space:]]}"}"  # rtrim
    raw="${raw#"${raw%%[![:space:]]*}"}"  # ltrim

    if [ -z "$raw" ]; then
        if [ ${#projects[@]} -eq 0 ]; then
            warn "No projects entered. Please provide at least one path."
            continue
        fi
        break
    fi

    # Expand ~ manually
    expanded="${raw/#\~/$HOME}"

    if [ ! -d "$expanded" ]; then
        warn "Path not found: $expanded — skipping."
        continue
    fi

    # Check for .uproject
    uproject_count=$(find "$expanded" -maxdepth 1 -name "*.uproject" 2>/dev/null | wc -l | tr -d ' ')
    if [ "$uproject_count" -eq 0 ]; then
        warn "No .uproject file found in: $expanded — skipping."
        continue
    fi

    projects+=("$expanded")
    ok "Queued: $expanded"
done

printf "\n"
info "Installing into ${#projects[@]} project(s)..."

# ---------------------------------------------------------------------------
# 3. Install into each project
# ---------------------------------------------------------------------------
failed=()

for proj in "${projects[@]}"; do
    step "Installing into: $proj"
    if "$NYW_CMD" install-plugin --project "$proj"; then
        ok "Done: $proj"
    else
        warn "Installation failed for: $proj"
        failed+=("$proj")
    fi
done

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
printf "\n"
if [ ${#failed[@]} -eq 0 ]; then
    printf "\033[32mAll projects updated successfully.\033[0m\n\n"
    info "Next steps:"
    info "  1. Open each project in the Unreal Editor"
    info "  2. Recompile when prompted"
    info "  3. Start the Nytwatch server and arm systems from Settings"
else
    printf "\033[33mCompleted with errors.\033[0m\n\n"
    info "Failed projects:"
    for f in "${failed[@]}"; do
        printf "   \033[31m- %s\033[0m\n" "$f"
    done
    printf "\n"
    info "Successful projects can be used immediately."
    info "Re-run this script for failed projects after resolving any issues."
fi
printf "\n"
