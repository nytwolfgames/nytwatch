# Nytwatch - NytwatchAgent Plugin Installer (Windows)
# Interactively installs the UE5 plugin into one or more game projects.
# No administrator privileges required.

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Write-Step($msg) { Write-Host "`n>> $msg" -ForegroundColor Cyan }
function Write-OK($msg)   { Write-Host "   OK   $msg" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "   WARN $msg" -ForegroundColor Yellow }
function Write-Fail($msg) { Write-Host "`n   ERROR $msg" -ForegroundColor Red; exit 1 }
function Write-Info($msg) { Write-Host "   $msg" }

# ---------------------------------------------------------------------------
# 1. Locate nytwatch CLI
# ---------------------------------------------------------------------------
Write-Step "Locating Nytwatch..."

$nytwatch = Get-Command nytwatch -ErrorAction SilentlyContinue
if (-not $nytwatch) {
    # Try to find it via Python sysconfig (handles editable installs in venvs)
    try {
        $scriptsDir = & python -c "import sysconfig; print(sysconfig.get_path('scripts'))"
        $candidate  = Join-Path $scriptsDir "nytwatch.exe"
        if (Test-Path $candidate) {
            $nytwatch = $candidate
        }
    } catch {}
}

if (-not $nytwatch) {
    Write-Fail "nytwatch not found on PATH. Run install.ps1 first."
}

$nywCmd = if ($nytwatch -is [string]) { $nytwatch } else { $nytwatch.Source }
Write-OK "Found: $nywCmd"

# ---------------------------------------------------------------------------
# 2. Collect project paths interactively
# ---------------------------------------------------------------------------
Write-Step "Project selection"
Write-Host ""
Write-Host "   Enter the full path to each Unreal Engine project root." -ForegroundColor White
Write-Host "   The directory must contain a .uproject file." -ForegroundColor Gray
Write-Host "   Press Enter on a blank line when done." -ForegroundColor Gray
Write-Host ""

$projects = @()

while ($true) {
    $raw = Read-Host "   Project path"
    $raw = $raw.Trim()
    if ([string]::IsNullOrEmpty($raw)) {
        if ($projects.Count -eq 0) {
            Write-Warn "No projects entered. Please provide at least one path."
            continue
        }
        break
    }

    $expanded = [System.Environment]::ExpandEnvironmentVariables($raw)
    $resolved = $null
    try { $resolved = Resolve-Path $expanded -ErrorAction Stop } catch {}

    if (-not $resolved) {
        Write-Warn "Path not found: $expanded — skipping."
        continue
    }

    $uprojectFiles = Get-ChildItem -Path $resolved -Filter "*.uproject" -ErrorAction SilentlyContinue
    if (-not $uprojectFiles) {
        Write-Warn "No .uproject file found in: $resolved — skipping."
        continue
    }

    $projects += $resolved.Path
    Write-OK "Queued: $($resolved.Path)"
}

Write-Host ""
Write-Host "   Installing into $($projects.Count) project(s)..." -ForegroundColor White

# ---------------------------------------------------------------------------
# 3. Install into each project
# ---------------------------------------------------------------------------
$failed = @()

foreach ($proj in $projects) {
    Write-Step "Installing into: $proj"
    & $nywCmd install-plugin --project $proj
    if ($LASTEXITCODE -ne 0) {
        Write-Warn "Installation failed for: $proj"
        $failed += $proj
    } else {
        Write-OK "Done: $proj"
    }
}

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
Write-Host ""
if ($failed.Count -eq 0) {
    Write-Host "All projects updated successfully." -ForegroundColor Green
    Write-Host ""
    Write-Host "   Next steps:"
    Write-Host "     1. Open each project in the Unreal Editor"
    Write-Host "     2. Recompile when prompted"
    Write-Host "     3. Start the Nytwatch server and arm systems from Settings"
} else {
    Write-Host "Completed with errors." -ForegroundColor Yellow
    Write-Host ""
    Write-Host "   Failed projects:"
    foreach ($f in $failed) { Write-Host "     - $f" -ForegroundColor Red }
    Write-Host ""
    Write-Host "   Successful projects can be used immediately."
    Write-Host "   Re-run this script for failed projects after resolving any issues."
}
Write-Host ""
