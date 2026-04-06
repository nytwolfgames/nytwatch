# Nytwatch - NytwatchAgent Plugin Installer (Windows)
# Lists configured projects and installs the UE5 plugin into selected ones.
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

$nywCmd = ""
$nywFound = Get-Command nytwatch -ErrorAction SilentlyContinue
if ($nywFound) {
    $nywCmd = $nywFound.Source
} else {
    try {
        $scriptsDir = & python -c "import sysconfig; print(sysconfig.get_path('scripts'))"
        $candidate  = Join-Path $scriptsDir "nytwatch.exe"
        if (Test-Path $candidate) { $nywCmd = $candidate }
    } catch {}
}

if (-not $nywCmd) {
    Write-Fail "nytwatch not found on PATH. Run install.ps1 first."
}
Write-OK "Found: $nywCmd"

# ---------------------------------------------------------------------------
# 2. Load configured projects
# ---------------------------------------------------------------------------
Write-Step "Loading configured projects..."

$projectsJson = & $nywCmd list-projects 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Fail "Failed to list projects: $projectsJson"
}

$projects = $projectsJson | ConvertFrom-Json

if ($projects.Count -eq 0) {
    Write-Fail "No projects configured in Nytwatch yet. Set up a project first via the Nytwatch dashboard."
}

# ---------------------------------------------------------------------------
# 3. Display project menu and collect selection
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "   Available projects:" -ForegroundColor White
Write-Host ""

for ($i = 0; $i -lt $projects.Count; $i++) {
    $p = $projects[$i]
    Write-Host ("   [{0}]  {1}" -f ($i + 1), $p.name) -ForegroundColor White
    Write-Host ("         {0}" -f $p.repo_path) -ForegroundColor Gray
}

Write-Host ""
Write-Host "   Enter project number(s) to install into, separated by spaces." -ForegroundColor White
Write-Host "   Example: 1   or   1 3   or   all" -ForegroundColor Gray
Write-Host ""

$raw = Read-Host "   Selection"
$raw = $raw.Trim().ToLower()

$selectedIndices = @()

if ($raw -eq "all") {
    $selectedIndices = 0..($projects.Count - 1)
} else {
    foreach ($token in ($raw -split '\s+')) {
        $n = 0
        if ([int]::TryParse($token, [ref]$n) -and $n -ge 1 -and $n -le $projects.Count) {
            $selectedIndices += ($n - 1)
        } else {
            Write-Warn "Ignoring invalid selection: $token"
        }
    }
}

if ($selectedIndices.Count -eq 0) {
    Write-Fail "No valid projects selected."
}

# Deduplicate
$selectedIndices = $selectedIndices | Sort-Object -Unique

# ---------------------------------------------------------------------------
# 4. Install into each selected project
# ---------------------------------------------------------------------------
$failed = @()

foreach ($idx in $selectedIndices) {
    $p = $projects[$idx]
    Write-Step "Installing into: $($p.name)"
    Write-Info $p.repo_path

    & $nywCmd install-plugin --project $p.repo_path
    if ($LASTEXITCODE -ne 0) {
        Write-Warn "Installation failed for: $($p.name)"
        $failed += $p.name
    } else {
        Write-OK "Done: $($p.name)"
    }
}

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
Write-Host ""
if ($failed.Count -eq 0) {
    Write-Host "All selected projects updated successfully." -ForegroundColor Green
    Write-Host ""
    Write-Host "   Next steps:"
    Write-Host "     1. Open each project in the Unreal Editor"
    Write-Host "     2. Recompile when prompted"
    Write-Host "     3. Start the Nytwatch server and arm systems from the Sessions page"
} else {
    Write-Host "Completed with errors." -ForegroundColor Yellow
    Write-Host ""
    Write-Host "   Failed:" -ForegroundColor Red
    foreach ($f in $failed) { Write-Host "     - $f" -ForegroundColor Red }
    Write-Host ""
    Write-Host "   Re-run this script for failed projects after resolving any issues."
}
Write-Host ""
