# Nytwatch — Windows Uninstaller
# Removes nytwatch (or the legacy code-auditor) from pip and from User PATH.
# No administrator privileges required.

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$PACKAGE_NAME  = "nytwatch"
$LEGACY_NAME   = "code-auditor"
$CONFIG_DIR    = "$env:USERPROFILE\.nytwatch"
$LEGACY_DIR    = "$env:USERPROFILE\.code-auditor"

function Write-Step($msg) { Write-Host "`n>> $msg" -ForegroundColor Cyan }
function Write-OK($msg)   { Write-Host "   OK  $msg" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "   WARN $msg" -ForegroundColor Yellow }
function Write-Fail($msg) { Write-Host "`n   ERROR $msg" -ForegroundColor Red }

# ---------------------------------------------------------------------------
# 1. Detect what is installed
# ---------------------------------------------------------------------------
Write-Step "Detecting installed packages..."

$nytwatchInstalled    = & python -m pip show $PACKAGE_NAME 2>&1 | Select-String "^Name:"
$legacyInstalled      = & python -m pip show $LEGACY_NAME  2>&1 | Select-String "^Name:"
$uninstallingLegacy   = $false

if ($nytwatchInstalled) {
    $targetPackage = $PACKAGE_NAME
    Write-OK "Found: nytwatch"
} elseif ($legacyInstalled) {
    $targetPackage      = $LEGACY_NAME
    $uninstallingLegacy = $true
    Write-OK "Found: code-auditor (legacy)"
} else {
    Write-Warn "Neither nytwatch nor code-auditor appears to be installed via pip."
    Write-Host "   Nothing to uninstall." -ForegroundColor Yellow
    exit 0
}

# ---------------------------------------------------------------------------
# 2. Uninstall the package
# ---------------------------------------------------------------------------
Write-Step "Uninstalling $targetPackage..."

& python -m pip uninstall $targetPackage -y --quiet
if ($LASTEXITCODE -ne 0) {
    Write-Fail "pip uninstall failed."
    exit 1
}
Write-OK "$targetPackage uninstalled."

# ---------------------------------------------------------------------------
# 3. Remove PATH entry
# ---------------------------------------------------------------------------
Write-Step "Removing PATH entry..."

$scriptsDir  = & python -c "import sysconfig; print(sysconfig.get_path('scripts'))"
$currentPath = [Environment]::GetEnvironmentVariable("PATH", "User")
$pathEntries = $currentPath -split ";" | Where-Object { $_ -ne "" -and $_ -ne $scriptsDir }

# Also remove any entry referencing the legacy name
$pathEntries = $pathEntries | Where-Object { $_ -notmatch "code-auditor" }

$newPath = $pathEntries -join ";"
[Environment]::SetEnvironmentVariable("PATH", $newPath, "User")
Write-OK "PATH entry removed."

# ---------------------------------------------------------------------------
# 4. Handle data directory
# ---------------------------------------------------------------------------
Write-Step "Handling data directory..."

if ($uninstallingLegacy) {
    # Migration scenario: uninstalling code-auditor
    if (Test-Path $CONFIG_DIR) {
        # ~/.nytwatch already exists — migration completed, auto-remove legacy dir
        if (Test-Path $LEGACY_DIR) {
            Remove-Item -Recurse -Force $LEGACY_DIR
            Write-OK "Migration detected — removed legacy data directory: $LEGACY_DIR"
        } else {
            Write-OK "No legacy data directory found."
        }
    } else {
        # ~/.nytwatch does not exist — prompt before removing legacy data
        Write-Warn "~/.nytwatch does not exist. Migration has not been run yet."
        $answer = Read-Host "   Remove legacy data directory ($LEGACY_DIR)? [y/N]"
        if ($answer -match "^[Yy]$") {
            if (Test-Path $LEGACY_DIR) {
                Remove-Item -Recurse -Force $LEGACY_DIR
                Write-OK "Removed: $LEGACY_DIR"
            } else {
                Write-OK "Legacy directory not found — nothing to remove."
            }
        } else {
            Write-Host ""
            Write-Host "   Data preserved at $LEGACY_DIR" -ForegroundColor Yellow
            Write-Host "   Run 'nytwatch migrate --from $LEGACY_DIR' after installing Nytwatch to import it." -ForegroundColor Yellow
        }
    }
} else {
    # Clean uninstall of nytwatch
    if (Test-Path $CONFIG_DIR) {
        $answer = Read-Host "   Remove config and data directory ($CONFIG_DIR)? This will delete your database and settings. [y/N]"
        if ($answer -match "^[Yy]$") {
            Remove-Item -Recurse -Force $CONFIG_DIR
            Write-OK "Removed: $CONFIG_DIR"
        } else {
            Write-OK "Data directory preserved at $CONFIG_DIR"
        }
    }
}

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------
Write-Host ""
if ($uninstallingLegacy) {
    Write-Host "code-auditor uninstalled successfully." -ForegroundColor Green
    Write-Host "   Run 'scripts\windows\install.ps1' to install Nytwatch."
} else {
    Write-Host "Nytwatch uninstalled successfully." -ForegroundColor Green
}
Write-Host ""
