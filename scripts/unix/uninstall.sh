#!/usr/bin/env bash
# Nytwatch - Unix Uninstaller (macOS / Linux)
# Clean uninstall of nytwatch. No knowledge of legacy code-auditor.
# No sudo required.

set -euo pipefail

PACKAGE_NAME="nytwatch"
CONFIG_DIR="$HOME/.nytwatch"

step()  { printf "\n\033[36m>> %s\033[0m\n" "$*"; }
ok()    { printf "   \033[32mOK\033[0m   %s\n" "$*"; }
warn()  { printf "   \033[33mWARN\033[0m %s\n" "$*"; }
fail()  { printf "\n   \033[31mERROR\033[0m %s\n" "$*"; exit 1; }

PYTHON_BIN=""
for candidate in python3 python; do
    if command -v "$candidate" &>/dev/null; then
        PYTHON_BIN="$candidate"
        break
    fi
done
[ -n "$PYTHON_BIN" ] || fail "Python not found on PATH."

# ---------------------------------------------------------------------------
# 1. Check nytwatch is installed
# ---------------------------------------------------------------------------
step "Checking installation..."

installed=$("$PYTHON_BIN" -m pip show "$PACKAGE_NAME" 2>/dev/null | grep "^Name:" || true)
if [ -z "$installed" ]; then
    warn "nytwatch does not appear to be installed via pip. Nothing to uninstall."
    exit 0
fi
ok "Found: nytwatch"

# ---------------------------------------------------------------------------
# 2. Uninstall the package
# ---------------------------------------------------------------------------
step "Uninstalling nytwatch..."

"$PYTHON_BIN" -m pip uninstall "$PACKAGE_NAME" -y --quiet
ok "nytwatch uninstalled."

# ---------------------------------------------------------------------------
# 3. Remove PATH entry from shell profile(s)
# ---------------------------------------------------------------------------
step "Removing PATH entry from shell profile(s)..."

BIN_DIR=$("$PYTHON_BIN" -c "import sysconfig; print(sysconfig.get_path('scripts'))" 2>/dev/null || true)

for profile in "$HOME/.bashrc" "$HOME/.zshrc" "$HOME/.bash_profile" "$HOME/.profile"; do
    if [ -f "$profile" ]; then
        if grep -qF "$BIN_DIR" "$profile" 2>/dev/null || grep -q "Added by Nytwatch installer" "$profile" 2>/dev/null; then
            grep -v "Added by Nytwatch installer" "$profile" \
              | grep -v "export PATH=\"$BIN_DIR:" \
              > "${profile}.nytwatch_tmp" && mv "${profile}.nytwatch_tmp" "$profile"
            ok "Cleaned: $profile"
        fi
    fi
done

# ---------------------------------------------------------------------------
# 4. Optionally remove config and data directory
# ---------------------------------------------------------------------------
step "Handling data directory..."

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

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------
printf "\n\033[32mNytwatch uninstalled successfully.\033[0m\n\n"
