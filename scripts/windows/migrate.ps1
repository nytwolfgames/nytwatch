# Nytwatch - Windows Migration Script
# Migrates from a legacy code-auditor installation to Nytwatch.
# Steps: install Nytwatch, copy data from ~/.code-auditor, uninstall code-auditor, remove legacy folders.
# No administrator privileges required.

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$LEGACY_NAME  = "code-auditor"
$LEGACY_DIR   = "$env:USERPROFILE\.code-auditor"
$NYTWATCH_DIR = "$env:USERPROFILE\.nytwatch"

function Write-Step($msg) { Write-Host "`n>> $msg" -ForegroundColor Cyan }
function Write-OK($msg)   { Write-Host "   OK   $msg" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "   WARN $msg" -ForegroundColor Yellow }
function Write-Fail($msg) { Write-Host "`n   ERROR $msg" -ForegroundColor Red; exit 1 }

function Get-PipVersion($packageName) {
    $packages = & python -m pip list --format=json | ConvertFrom-Json
    $pkg = $packages | Where-Object { $_.name -ieq $packageName }
    if ($pkg) { return $pkg.version }
    return ""
}

# ---------------------------------------------------------------------------
# 1. Confirm a legacy installation exists
# ---------------------------------------------------------------------------
Write-Step "Checking for legacy code-auditor installation..."

$legacyVer = Get-PipVersion $LEGACY_NAME
$legacyDir = Test-Path $LEGACY_DIR

if (-not $legacyVer -and -not $legacyDir) {
    Write-Warn "No legacy code-auditor installation or data directory found."
    Write-Host "   If you are doing a fresh install, run 'scripts\windows\install.ps1' instead." -ForegroundColor Yellow
    exit 0
}

if ($legacyVer) { Write-OK "Found: code-auditor $legacyVer (pip)" }
if ($legacyDir) { Write-OK "Found: legacy data at $LEGACY_DIR" }

# ---------------------------------------------------------------------------
# 2. Install Nytwatch
# ---------------------------------------------------------------------------
Write-Step "Installing Nytwatch..."

$scriptRoot = Split-Path -Parent $PSCommandPath
powershell.exe -ExecutionPolicy Bypass -File "$scriptRoot\install.ps1"
if ($LASTEXITCODE -ne 0) { Write-Fail "Nytwatch install failed. Aborting migration." }

# ---------------------------------------------------------------------------
# 3. Migrate data from ~/.code-auditor to ~/.nytwatch
# ---------------------------------------------------------------------------
Write-Step "Migrating data from $LEGACY_DIR to $NYTWATCH_DIR..."

if (Test-Path $LEGACY_DIR) {
    # Copy project YAML config files
    $yamlFiles = Get-ChildItem -Path $LEGACY_DIR -Filter "*.yaml" -ErrorAction SilentlyContinue
    foreach ($f in $yamlFiles) {
        $dest = Join-Path $NYTWATCH_DIR $f.Name
        if (-not (Test-Path $dest)) {
            Copy-Item $f.FullName $dest
            Write-OK "Copied config: $($f.Name)"
        } else {
            Write-Warn "Skipped (already exists): $($f.Name)"
        }
    }

    # Copy project database directories. Rename auditor.db -> nytwatch.db so the new CLI finds it.
    $subDirs = Get-ChildItem -Path $LEGACY_DIR -Directory -ErrorAction SilentlyContinue
    foreach ($d in $subDirs) {
        $destDir = Join-Path $NYTWATCH_DIR $d.Name
        if (-not (Test-Path $destDir)) {
            Copy-Item -Recurse $d.FullName $destDir
            $legacyDb = Join-Path $destDir "auditor.db"
            $newDb    = Join-Path $destDir "nytwatch.db"
            if ((Test-Path $legacyDb) -and -not (Test-Path $newDb)) {
                Rename-Item $legacyDb $newDb
                Write-OK "Renamed auditor.db -> nytwatch.db in $($d.Name)"
            }
            Write-OK "Copied project data: $($d.Name)"
        } else {
            Write-Warn "Skipped (already exists): $($d.Name)"
        }
    }

    # Copy .active pointer if present
    $activeFile = Join-Path $LEGACY_DIR ".active"
    $activeTarget = Join-Path $NYTWATCH_DIR ".active"
    if ((Test-Path $activeFile) -and -not (Test-Path $activeTarget)) {
        Copy-Item $activeFile $activeTarget
        Write-OK "Copied .active pointer"
    }

    Write-OK "Data migration complete."
    Write-Host "   Note: Database schema will be updated automatically on first 'nytwatch serve'." -ForegroundColor Yellow
} else {
    Write-Warn "No legacy data directory found - skipping data migration."
}

# ---------------------------------------------------------------------------
# 4. Uninstall code-auditor
# ---------------------------------------------------------------------------
Write-Step "Uninstalling code-auditor..."

if ($legacyVer) {
    & python -m pip uninstall $LEGACY_NAME -y --quiet
    if ($LASTEXITCODE -ne 0) { Write-Fail "pip uninstall code-auditor failed." }
    Write-OK "code-auditor uninstalled."
} else {
    Write-Warn "code-auditor was not installed via pip - skipping pip uninstall."
}

# ---------------------------------------------------------------------------
# 5. Remove legacy PATH entry
# ---------------------------------------------------------------------------
Write-Step "Removing legacy PATH entries..."

$currentPath = [Environment]::GetEnvironmentVariable("PATH", "User")
$pathEntries = $currentPath -split ";" | Where-Object { $_ -ne "" -and $_ -notmatch "code-auditor" }
[Environment]::SetEnvironmentVariable("PATH", ($pathEntries -join ";"), "User")
Write-OK "Legacy PATH entries removed."

# ---------------------------------------------------------------------------
# 6. Remove legacy data directory
# ---------------------------------------------------------------------------
Write-Step "Removing legacy data directory..."

if (Test-Path $LEGACY_DIR) {
    Remove-Item -Recurse -Force $LEGACY_DIR
    Write-OK "Removed: $LEGACY_DIR"
} else {
    Write-OK "Already gone: $LEGACY_DIR"
}

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "Migration complete." -ForegroundColor Green
Write-Host ""
Write-Host "   Nytwatch is installed and your previous project data has been migrated."
Write-Host "   Run 'nytwatch serve' to start. The database schema will update automatically."
Write-Host "   Note: Open a new terminal window for PATH changes to take effect."
Write-Host ""
