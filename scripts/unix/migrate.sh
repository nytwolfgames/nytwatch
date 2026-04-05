#!/usr/bin/env bash
# Nytwatch - Unix Migration Script (macOS / Linux)
# Migrates from a legacy code-auditor installation to Nytwatch.
# Steps: install Nytwatch, copy data from ~/.code-auditor, uninstall code-auditor, remove legacy folders.
# No sudo required.

set -euo pipefail

LEGACY_NAME="code-auditor"
LEGACY_DIR="$HOME/.code-auditor"
NYTWATCH_DIR="$HOME/.nytwatch"

step()  { printf "\n\033[36m>> %s\033[0m\n" "$*"; }
ok()    { printf "   \033[32mOK\033[0m   %s\n" "$*"; }
warn()  { printf "   \033[33mWARN\033[0m %s\n" "$*"; }
fail()  { printf "\n   \033[31mERROR\033[0m %s\n" "$*"; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

PYTHON_BIN=""
for candidate in python3 python; do
    if command -v "$candidate" &>/dev/null; then
        PYTHON_BIN="$candidate"
        break
    fi
done
[ -n "$PYTHON_BIN" ] || fail "Python not found on PATH."

# ---------------------------------------------------------------------------
# 1. Confirm a legacy installation exists
# ---------------------------------------------------------------------------
step "Checking for legacy code-auditor installation..."

legacy_pip=$("$PYTHON_BIN" -m pip show "$LEGACY_NAME" 2>/dev/null | grep "^Name:" || true)
legacy_dir=false
[ -d "$LEGACY_DIR" ] && legacy_dir=true

if [ -z "$legacy_pip" ] && [ "$legacy_dir" = false ]; then
    warn "No legacy code-auditor installation or data directory found."
    printf "   If you are doing a fresh install, run 'scripts/unix/install.sh' instead.\n"
    exit 0
fi

[ -n "$legacy_pip" ] && ok "Found: code-auditor (pip)"
[ "$legacy_dir" = true ] && ok "Found: legacy data at $LEGACY_DIR"

# ---------------------------------------------------------------------------
# 2. Install Nytwatch
# ---------------------------------------------------------------------------
step "Installing Nytwatch..."

bash "$SCRIPT_DIR/install.sh" || fail "Nytwatch install failed. Aborting migration."

# ---------------------------------------------------------------------------
# 3. Migrate data from ~/.code-auditor to ~/.nytwatch
# ---------------------------------------------------------------------------
step "Migrating data from $LEGACY_DIR to $NYTWATCH_DIR..."

if [ -d "$LEGACY_DIR" ]; then
    # Copy project YAML config files
    for f in "$LEGACY_DIR"/*.yaml; do
        [ -f "$f" ] || continue
        fname="$(basename "$f")"
        dest="$NYTWATCH_DIR/$fname"
        if [ ! -f "$dest" ]; then
            cp "$f" "$dest"
            ok "Copied config: $fname"
        else
            warn "Skipped (already exists): $fname"
        fi
    done

    # Copy project database subdirectories.
    # Rename auditor.db -> nytwatch.db during copy so the new CLI finds it.
    for d in "$LEGACY_DIR"/*/; do
        [ -d "$d" ] || continue
        dname="$(basename "$d")"
        destdir="$NYTWATCH_DIR/$dname"
        if [ ! -d "$destdir" ]; then
            cp -r "$d" "$destdir"
            if [ -f "$destdir/auditor.db" ] && [ ! -f "$destdir/nytwatch.db" ]; then
                mv "$destdir/auditor.db" "$destdir/nytwatch.db"
                ok "Renamed auditor.db -> nytwatch.db in $dname"
            fi
            ok "Copied project data: $dname"
        else
            warn "Skipped (already exists): $dname"
        fi
    done

    # Copy .active pointer if present
    if [ -f "$LEGACY_DIR/.active" ] && [ ! -f "$NYTWATCH_DIR/.active" ]; then
        cp "$LEGACY_DIR/.active" "$NYTWATCH_DIR/.active"
        ok "Copied .active pointer"
    fi

    ok "Data migration complete."
    printf "   Note: Database schema will be updated automatically on first 'nytwatch serve'.\n"
else
    warn "No legacy data directory found - skipping data migration."
fi

# ---------------------------------------------------------------------------
# 4. Uninstall code-auditor
# ---------------------------------------------------------------------------
step "Uninstalling code-auditor..."

if [ -n "$legacy_pip" ]; then
    "$PYTHON_BIN" -m pip uninstall "$LEGACY_NAME" -y --quiet
    ok "code-auditor uninstalled."
else
    warn "code-auditor was not installed via pip - skipping pip uninstall."
fi

# ---------------------------------------------------------------------------
# 5. Remove legacy PATH entry from shell profile(s)
# ---------------------------------------------------------------------------
step "Removing legacy PATH entries..."

for profile in "$HOME/.bashrc" "$HOME/.zshrc" "$HOME/.bash_profile" "$HOME/.profile"; do
    if [ -f "$profile" ]; then
        if grep -q "code-auditor" "$profile" 2>/dev/null; then
            grep -v "code-auditor" "$profile" \
              | grep -v "Added by code-auditor installer" \
              > "${profile}.nytwatch_tmp" && mv "${profile}.nytwatch_tmp" "$profile"
            ok "Cleaned: $profile"
        fi
    fi
done

# ---------------------------------------------------------------------------
# 6. Remove legacy data directory
# ---------------------------------------------------------------------------
step "Removing legacy data directory..."

if [ -d "$LEGACY_DIR" ]; then
    rm -rf "$LEGACY_DIR"
    ok "Removed: $LEGACY_DIR"
else
    ok "Already gone: $LEGACY_DIR"
fi

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------
printf "\n\033[32mMigration complete.\033[0m\n\n"
printf "   Nytwatch is installed and your previous project data has been migrated.\n"
printf "   Run 'nytwatch serve' to start. The database schema will update automatically.\n\n"
