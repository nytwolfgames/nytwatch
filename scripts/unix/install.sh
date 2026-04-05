#!/usr/bin/env bash
# Nytwatch - Unix Installer (macOS / Linux)
# Clean install of nytwatch. No knowledge of legacy code-auditor.
# No sudo required.

set -euo pipefail

PACKAGE_NAME="nytwatch"
MIN_PYTHON_MAJOR=3
MIN_PYTHON_MINOR=11
CONFIG_DIR="$HOME/.nytwatch"

step()  { printf "\n\033[36m>> %s\033[0m\n" "$*"; }
ok()    { printf "   \033[32mOK\033[0m   %s\n" "$*"; }
warn()  { printf "   \033[33mWARN\033[0m %s\n" "$*"; }
fail()  { printf "\n   \033[31mERROR\033[0m %s\n" "$*"; exit 1; }

detect_profile() {
    if [ -n "${ZSH_VERSION:-}" ] || [ "$(basename "${SHELL:-}")" = "zsh" ]; then
        echo "$HOME/.zshrc"
    else
        echo "$HOME/.bashrc"
    fi
}

# ---------------------------------------------------------------------------
# 1. Check Python version
# ---------------------------------------------------------------------------
step "Checking Python version..."

PYTHON_BIN=""
for candidate in python3 python; do
    if command -v "$candidate" &>/dev/null; then
        ver=$("$candidate" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null || true)
        major=$(echo "$ver" | cut -d. -f1)
        minor=$(echo "$ver" | cut -d. -f2)
        if [ "$major" -ge "$MIN_PYTHON_MAJOR" ] && [ "$minor" -ge "$MIN_PYTHON_MINOR" ]; then
            PYTHON_BIN="$candidate"
            ok "Python $ver ($PYTHON_BIN)"
            break
        fi
    fi
done

[ -n "$PYTHON_BIN" ] || fail "Python ${MIN_PYTHON_MAJOR}.${MIN_PYTHON_MINOR}+ not found. Install it and re-run this script."

# ---------------------------------------------------------------------------
# 2. Check for existing nytwatch installation
# ---------------------------------------------------------------------------
step "Checking for existing installation..."

existing_ver=$("$PYTHON_BIN" -m pip show "$PACKAGE_NAME" 2>/dev/null | grep "^Version:" | sed 's/Version: *//' || true)
if [ -n "$existing_ver" ]; then
    warn "Nytwatch $existing_ver already installed - upgrading if needed."
fi

# ---------------------------------------------------------------------------
# 3. Install the package
# ---------------------------------------------------------------------------
step "Installing Nytwatch..."

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

if [ -f "$REPO_ROOT/pyproject.toml" ]; then
    echo "   Detected development install (pyproject.toml found)."
    "$PYTHON_BIN" -m pip install -e "$REPO_ROOT" --quiet
else
    "$PYTHON_BIN" -m pip install "$PACKAGE_NAME" --quiet
fi
ok "Package installed."

# ---------------------------------------------------------------------------
# 4. Locate the CLI entrypoint directory (bin/)
# ---------------------------------------------------------------------------
step "Locating CLI entrypoint..."

BIN_DIR=$("$PYTHON_BIN" -c "import sysconfig; print(sysconfig.get_path('scripts'))")
ok "CLI at: $BIN_DIR"

# ---------------------------------------------------------------------------
# 5. Add to PATH via shell profile
# ---------------------------------------------------------------------------
step "Updating shell profile..."

PROFILE="$(detect_profile)"
EXPORT_LINE="export PATH=\"$BIN_DIR:\$PATH\""

if echo "$PATH" | tr ':' '\n' | grep -qx "$BIN_DIR"; then
    ok "Already in PATH: $BIN_DIR"
elif grep -qF "$BIN_DIR" "$PROFILE" 2>/dev/null; then
    ok "Already in profile: $PROFILE"
else
    echo "" >> "$PROFILE"
    echo "# Added by Nytwatch installer" >> "$PROFILE"
    echo "$EXPORT_LINE" >> "$PROFILE"
    export PATH="$BIN_DIR:$PATH"
    ok "Added to $PROFILE (active in current session)"
fi

# ---------------------------------------------------------------------------
# 6. Create config directory
# ---------------------------------------------------------------------------
step "Creating config directory..."

if [ ! -d "$CONFIG_DIR" ]; then
    mkdir -p "$CONFIG_DIR"
    ok "Created: $CONFIG_DIR"
else
    ok "Already exists: $CONFIG_DIR"
fi

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------
installed_ver=$("$PYTHON_BIN" -m pip show "$PACKAGE_NAME" 2>/dev/null | grep "^Version:" | sed 's/Version: *//')

printf "\n\033[32mNytwatch installed successfully.\033[0m\n\n"
printf "   Version  : %s\n" "$installed_ver"
printf "   Config   : %s\n" "$CONFIG_DIR"
printf "   Command  : nytwatch\n\n"
printf "   Run 'nytwatch --help' to get started.\n\n"
