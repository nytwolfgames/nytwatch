# Nytwatch — Windows Installer
# Installs the nytwatch package, adds the CLI to User PATH, and creates the config directory.
# No administrator privileges required.

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$PACKAGE_NAME  = "nytwatch"
$LEGACY_NAME   = "code-auditor"
$MIN_PYTHON    = [version]"3.11"
$CONFIG_DIR    = "$env:USERPROFILE\.nytwatch"

function Write-Step($msg) { Write-Host "`n>> $msg" -ForegroundColor Cyan }
function Write-OK($msg)   { Write-Host "   OK  $msg" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "   WARN $msg" -ForegroundColor Yellow }
function Write-Fail($msg) { Write-Host "`n   ERROR $msg" -ForegroundColor Red }

# ---------------------------------------------------------------------------
# 1. Check Python version
# ---------------------------------------------------------------------------
Write-Step "Checking Python version..."

try {
    $pyVer = & python --version 2>&1
} catch {
    Write-Fail "Python not found on PATH. Install Python 3.11+ from https://www.python.org/downloads/ and check 'Add to PATH'."
    exit 1
}

if ($pyVer -match "Python (\d+\.\d+)") {
    $installedVer = [version]$Matches[1]
    if ($installedVer -lt $MIN_PYTHON) {
        Write-Fail "Python $installedVer found but $MIN_PYTHON or higher is required."
        exit 1
    }
    Write-OK "Python $installedVer"
} else {
    Write-Fail "Could not parse Python version from: $pyVer"
    exit 1
}

# ---------------------------------------------------------------------------
# 2. Check for existing installations
# ---------------------------------------------------------------------------
Write-Step "Checking for existing installations..."

$legacyOnPath = Get-Command $LEGACY_NAME -ErrorAction SilentlyContinue
if ($legacyOnPath) {
    Write-Fail "Found an existing '$LEGACY_NAME' installation at: $($legacyOnPath.Source)"
    Write-Host "   Run 'scripts\windows\uninstall.ps1' first to remove it before installing Nytwatch." -ForegroundColor Yellow
    exit 1
}

$existingOnPath = Get-Command $PACKAGE_NAME -ErrorAction SilentlyContinue
if ($existingOnPath) {
    $existingVer = & nytwatch --version 2>&1
    $pkgVer = (& python -m pip show $PACKAGE_NAME 2>&1 | Select-String "^Version:") -replace "Version:\s*", ""
    if ($pkgVer -eq $existingVer) {
        Write-OK "Nytwatch $pkgVer is already installed."
        exit 0
    }
    Write-Warn "Upgrading Nytwatch from $existingVer to $pkgVer."
}

# ---------------------------------------------------------------------------
# 3. Install the package
# ---------------------------------------------------------------------------
Write-Step "Installing Nytwatch..."

$scriptRoot = Split-Path -Parent $PSCommandPath
$repoRoot   = Split-Path -Parent (Split-Path -Parent $scriptRoot)

if (Test-Path "$repoRoot\pyproject.toml") {
    Write-Host "   Detected development install (pyproject.toml found)."
    & python -m pip install -e "$repoRoot" --quiet
} else {
    & python -m pip install $PACKAGE_NAME --quiet
}

if ($LASTEXITCODE -ne 0) {
    Write-Fail "pip install failed."
    exit 1
}
Write-OK "Package installed."

# ---------------------------------------------------------------------------
# 4. Locate the CLI entrypoint directory (Scripts/)
# ---------------------------------------------------------------------------
Write-Step "Locating CLI entrypoint..."

$scriptsDir = & python -c "import sysconfig; print(sysconfig.get_path('scripts'))"
if (-not (Test-Path "$scriptsDir\nytwatch.exe") -and -not (Test-Path "$scriptsDir\nytwatch")) {
    Write-Warn "Could not find nytwatch executable in $scriptsDir — PATH update skipped."
} else {
    Write-OK "CLI at: $scriptsDir"
}

# ---------------------------------------------------------------------------
# 5. Add to User PATH (registry, no admin required)
# ---------------------------------------------------------------------------
Write-Step "Updating User PATH..."

$currentPath = [Environment]::GetEnvironmentVariable("PATH", "User")
$pathEntries = $currentPath -split ";" | Where-Object { $_ -ne "" }

if ($pathEntries -contains $scriptsDir) {
    Write-OK "Already in PATH: $scriptsDir"
} else {
    $newPath = ($pathEntries + $scriptsDir) -join ";"
    [Environment]::SetEnvironmentVariable("PATH", $newPath, "User")
    Write-OK "Added to User PATH: $scriptsDir"
}

# ---------------------------------------------------------------------------
# 6. Create config directory
# ---------------------------------------------------------------------------
Write-Step "Creating config directory..."

if (-not (Test-Path $CONFIG_DIR)) {
    New-Item -ItemType Directory -Path $CONFIG_DIR -Force | Out-Null
    Write-OK "Created: $CONFIG_DIR"
} else {
    Write-OK "Already exists: $CONFIG_DIR"
}

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------
$installedVersion = & python -m pip show $PACKAGE_NAME 2>&1 | Select-String "^Version:" | ForEach-Object { $_ -replace "Version:\s*", "" }

Write-Host ""
Write-Host "Nytwatch installed successfully." -ForegroundColor Green
Write-Host ""
Write-Host "   Version  : $installedVersion"
Write-Host "   Config   : $CONFIG_DIR"
Write-Host "   Command  : nytwatch"
Write-Host ""
Write-Host "   Run 'nytwatch --help' to get started."
Write-Host "   Note: Open a new terminal window for PATH changes to take effect."
Write-Host ""
