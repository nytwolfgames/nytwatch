# Nytwatch - Windows Installer
# Clean install of nytwatch. No knowledge of legacy code-auditor.
# No administrator privileges required.

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$PACKAGE_NAME = "nytwatch"
$MIN_PYTHON   = [version]"3.11"
$CONFIG_DIR   = "$env:USERPROFILE\.nytwatch"

function Write-Step($msg) { Write-Host "`n>> $msg" -ForegroundColor Cyan }
function Write-OK($msg)   { Write-Host "   OK   $msg" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "   WARN $msg" -ForegroundColor Yellow }
function Write-Fail($msg) { Write-Host "`n   ERROR $msg" -ForegroundColor Red; exit 1 }

# ---------------------------------------------------------------------------
# 1. Check Python version
# ---------------------------------------------------------------------------
Write-Step "Checking Python version..."

try { $pyVer = & python --version 2>&1 }
catch { Write-Fail "Python not found on PATH. Install Python 3.11+ from https://www.python.org/downloads/ and check 'Add to PATH'." }

if ($pyVer -match "Python (\d+\.\d+)") {
    $installedVer = [version]$Matches[1]
    if ($installedVer -lt $MIN_PYTHON) {
        Write-Fail "Python $installedVer found but $MIN_PYTHON or higher is required."
    }
    Write-OK "Python $installedVer"
} else {
    Write-Fail "Could not parse Python version from: $pyVer"
}

# ---------------------------------------------------------------------------
# 2. Check for existing nytwatch installation
# ---------------------------------------------------------------------------
Write-Step "Checking for existing installation..."

$existingVer = (& python -m pip show $PACKAGE_NAME 2>&1 | Select-String "^Version:") -replace "Version:\s*", ""
if ($existingVer) {
    Write-Warn "Nytwatch $existingVer already installed - upgrading if needed."
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

if ($LASTEXITCODE -ne 0) { Write-Fail "pip install failed." }
Write-OK "Package installed."

# ---------------------------------------------------------------------------
# 4. Locate the CLI entrypoint directory (Scripts/)
# ---------------------------------------------------------------------------
Write-Step "Locating CLI entrypoint..."

$scriptsDir = & python -c "import sysconfig; print(sysconfig.get_path('scripts'))"
if (-not (Test-Path "$scriptsDir\nytwatch.exe") -and -not (Test-Path "$scriptsDir\nytwatch")) {
    Write-Warn "Could not find nytwatch executable in $scriptsDir - PATH update skipped."
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
$installedVersion = (& python -m pip show $PACKAGE_NAME 2>&1 | Select-String "^Version:") -replace "Version:\s*", ""

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
