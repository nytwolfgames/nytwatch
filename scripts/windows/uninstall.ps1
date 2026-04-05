# Nytwatch - Windows Uninstaller
# Clean uninstall of nytwatch. No knowledge of legacy code-auditor.
# No administrator privileges required.

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$PACKAGE_NAME = "nytwatch"
$CONFIG_DIR   = "$env:USERPROFILE\.nytwatch"

function Write-Step($msg) { Write-Host "`n>> $msg" -ForegroundColor Cyan }
function Write-OK($msg)   { Write-Host "   OK   $msg" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "   WARN $msg" -ForegroundColor Yellow }
function Write-Fail($msg) { Write-Host "`n   ERROR $msg" -ForegroundColor Red; exit 1 }

# ---------------------------------------------------------------------------
# 1. Check nytwatch is installed
# ---------------------------------------------------------------------------
Write-Step "Checking installation..."

$installed = & python -m pip show $PACKAGE_NAME 2>&1 | Select-String "^Name:"
if (-not $installed) {
    Write-Warn "nytwatch does not appear to be installed via pip. Nothing to uninstall."
    exit 0
}
Write-OK "Found: nytwatch"

# ---------------------------------------------------------------------------
# 2. Uninstall the package
# ---------------------------------------------------------------------------
Write-Step "Uninstalling nytwatch..."

& python -m pip uninstall $PACKAGE_NAME -y --quiet
if ($LASTEXITCODE -ne 0) { Write-Fail "pip uninstall failed." }
Write-OK "nytwatch uninstalled."

# ---------------------------------------------------------------------------
# 3. Remove PATH entry
# ---------------------------------------------------------------------------
Write-Step "Removing PATH entry..."

$scriptsDir  = & python -c "import sysconfig; print(sysconfig.get_path('scripts'))"
$currentPath = [Environment]::GetEnvironmentVariable("PATH", "User")
$pathEntries = $currentPath -split ";" | Where-Object { $_ -ne "" -and $_ -ne $scriptsDir }
[Environment]::SetEnvironmentVariable("PATH", ($pathEntries -join ";"), "User")
Write-OK "PATH entry removed."

# ---------------------------------------------------------------------------
# 4. Optionally remove config and data directory
# ---------------------------------------------------------------------------
Write-Step "Handling data directory..."

if (Test-Path $CONFIG_DIR) {
    $answer = Read-Host "   Remove config and data directory ($CONFIG_DIR)? This will delete your database and settings. [y/N]"
    if ($answer -match "^[Yy]$") {
        Remove-Item -Recurse -Force $CONFIG_DIR
        Write-OK "Removed: $CONFIG_DIR"
    } else {
        Write-OK "Data directory preserved at $CONFIG_DIR"
    }
}

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "Nytwatch uninstalled successfully." -ForegroundColor Green
Write-Host ""
