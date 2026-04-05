#!/usr/bin/env bash
# Nytwatch — Unix Uninstaller (macOS / Linux)
# Removes nytwatch (or the legacy code-auditor) from pip and from the shell profile.
# No sudo required.

set -euo pipefail

PACKAGE_NAME="nytwatch"
LEGACY_NAME="code-auditor"
CONFIG_DIR="$HOME/.nytwatch"
LEGACY_DIR="$HOME/.code-auditor"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
step()  { printf "\n\033[36m>> %s\033[0m\n" "$*"; }
ok()    { printf "   \033[32mOK\033[0m  %s\n" "$*"; }
warn()  { printf "   \033[33mWARN\033[0m %s\n" "$*"; }
fail()  { printf "\n   \033[31mERROR\033[0m %s\n" "$*"; exit 1; }

PYTHON_BIN=""
for candidate in python3 python; do
    if command -v "$candidate" &>/dev/null; then
        PYTHON_BIN="$candidate"
        break
    fi
done

if [ -z "$PYTHON_BIN" ]; then
    fail "Python not found on PATH."
fi

# ---------------------------------------------------------------------------
# 1. Detect what is installed
# ---------------------------------------------------------------------------
step "Detecting installed packages..."

NYTWATCH_INSTALLED=$("$PYTHON_BIN" -m pip show "$PACKAGE_NAME" 2>/dev/null | grep "^Name:" || true)
LEGACY_INSTALLED=$("$PYTHON_BIN" -m pip show "$LEGACY_NAME" 2>/dev/null | grep "^Name:" || true)
UNINSTALLING_LEGACY=false

if [ -n "$NYTWATCH_INSTALLED" ]; then
    TARGET_PACKAGE="$PACKAGE_NAME"
    ok "Found: nytwatch"
elif [ -n "$LEGACY_INSTALLED" ]; then
    TARGET_PACKAGE="$LEGACY_NAME"
    UNINSTALLING_LEGACY=true
    ok "Found: code-auditor (legacy)"
else
    warn "Neither nytwatch nor code-auditor appears to be installed via pip."
    printf "   Nothing to uninstall.\n"
    exit 0
fi

# ---------------------------------------------------------------------------
# 2. Uninstall the package
# ---------------------------------------------------------------------------
step "Uninstalling $TARGET_PACKAGE..."

"$PYTHON_BIN" -m pip uninstall "$TARGET_PACKAGE" -y --quiet
ok "$TARGET_PACKAGE uninstalled."

# ---------------------------------------------------------------------------
# 3. Remove PATH entry from shell profile
# ---------------------------------------------------------------------------
step "Removing PATH entry from shell profile(s)..."

BIN_DIR=$("$PYTHON_BIN" -c "import sysconfig; print(sysconfig.get_path('scripts'))" 2>/dev/null || true)

for profile in "$HOME/.bashrc" "$HOME/.zshrc" "$HOME/.bash_profile" "$HOME/.profile"; do
    if [ -f "$profile" ]; then
        # Remove the Nytwatch installer comment line and the export PATH line for this bin dir
        if grep -qF "$BIN_DIR" "$profile" 2>/dev/null || grep -q "Added by Nytwatch installer" "$profile" 2>/dev/null; then
            # Use a temp file to remove matching lines
            grep -v "Added by Nytwatch installer" "$profile" \
              | grep -v "export PATH=\"$BIN_DIR:" \
              | grep -v "export PATH=\".*code-auditor" \
              > "${profile}.nytwatch_tmp" && mv "${profile}.nytwatch_tmp" "$profile"
            ok "Cleaned: $profile"
        fi
    fi
done

# ---------------------------------------------------------------------------
# 4. Handle data directory
# ---------------------------------------------------------------------------
step "Handling data directory..."

if [ "$UNINSTALLING_LEGACY" = true ]; then
    # Migration scenario: uninstalling code-auditor
    if [ -d "$CONFIG_DIR" ]; then
        # ~/.nytwatch already exists — migration completed, auto-remove legacy dir
        if [ -d "$LEGACY_DIR" ]; then
            rm -rf "$LEGACY_DIR"
            ok "Migration detected — removed legacy data directory: $LEGACY_DIR"
        else
            ok "No legacy data directory found."
        fi
    else
        # ~/.nytwatch does not exist — prompt before removing legacy data
        warn "~/.nytwatch does not exist. Migration has not been run yet."
        printf "   Remove legacy data directory (%s)? [y/N]: " "$LEGACY_DIR"
        read -r answer
        if [[ "${answer:-}" =~ ^[Yy]$ ]]; then
            if [ -d "$LEGACY_DIR" ]; then
                rm -rf "$LEGACY_DIR"
                ok "Removed: $LEGACY_DIR"
            else
                ok "Legacy directory not found — nothing to remove."
            fi
        else
            printf "\n"
            warn "Data preserved at $LEGACY_DIR"
            warn "Run 'nytwatch migrate --from $LEGACY_DIR' after installing Nytwatch to import it."
        fi
    fi
else
    # Clean uninstall of nytwatch
    if [ -d "$CONFIG_DIR" ]; then
        printf "   Remove config and data directory (%s)? This will delete your database and settings. [y/N]: " "$CONFIG_DIR"
        read -r answer
        if [[ "${answer:-}" =~ ^[Yy]$ ]]; then
            rm -rf "$CONFIG_DIR"
            ok "Removed: $CONFIG_DIR"
        else
            ok "Data directory preserved at $CONFIG_DIR"
        fi
    fi
fi

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------
printf "\n"
if [ "$UNINSTALLING_LEGACY" = true ]; then
    printf "\033[32mcode-auditor uninstalled successfully.\033[0m\n"
    printf "   Run 'scripts/unix/install.sh' to install Nytwatch.\n"
else
    printf "\033[32mNytwatch uninstalled successfully.\033[0m\n"
fi
printf "\n"
