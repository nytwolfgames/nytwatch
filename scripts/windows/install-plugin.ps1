# Nytwatch - NytwatchAgent Plugin Installer (Windows)
# Lists configured projects and installs the UE5 plugin into selected ones.
# No administrator privileges required.

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# Catch any unhandled terminating error and keep the window open
trap {
    try { [Console]::CursorVisible = $true } catch {}
    Write-Host "`n   UNEXPECTED ERROR: $_" -ForegroundColor Red
    Read-Host "`nPress Enter to close"
    exit 1
}

function Write-Step($msg) { Write-Host "`n>> $msg" -ForegroundColor Cyan }
function Write-OK($msg)   { Write-Host "   OK   $msg" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "   WARN $msg" -ForegroundColor Yellow }
function Write-Fail($msg) { Write-Host "`n   ERROR $msg" -ForegroundColor Red; Read-Host "`nPress Enter to close"; exit 1 }
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
# 3. Interactive project selection menu
# ---------------------------------------------------------------------------
function Render-MenuRow {
    param($Project, [bool]$IsCursor, [bool]$IsSelected, [int]$Width)
    $check  = if ($IsSelected) { "[x]" } else { "[ ]" }
    $line1  = if ($IsCursor) { " ► $check  $($Project.name)" } else { "   $check  $($Project.name)" }
    $line2  = "         $($Project.repo_path)"
    $pad1   = " " * [Math]::Max(0, $Width - $line1.Length)
    $pad2   = " " * [Math]::Max(0, $Width - $line2.Length)
    if ($IsCursor) {
        Write-Host ($line1 + $pad1) -ForegroundColor Cyan
    } else {
        Write-Host ($line1 + $pad1)
    }
    Write-Host ($line2 + $pad2) -ForegroundColor Gray
}

Write-Host ""
Write-Host "   Use " -NoNewline
Write-Host "↑↓" -ForegroundColor Cyan -NoNewline
Write-Host " to navigate,  " -NoNewline
Write-Host "Space" -ForegroundColor Cyan -NoNewline
Write-Host " to select,  " -NoNewline
Write-Host "Enter" -ForegroundColor Cyan -NoNewline
Write-Host " to confirm,  " -NoNewline
Write-Host "A" -ForegroundColor Cyan -NoNewline
Write-Host " to toggle all."
Write-Host ""

$menuCount    = $projects.Count
$menuCursor   = 0
$menuSelected = @{}
$winWidth     = try { $Host.UI.RawUI.WindowSize.Width } catch { 80 }

try { [Console]::CursorVisible = $false } catch {}
$menuStartRow = try { [Console]::CursorTop } catch { 0 }

for ($i = 0; $i -lt $menuCount; $i++) {
    Render-MenuRow $projects[$i] ($i -eq $menuCursor) ($menuSelected.ContainsKey($i)) $winWidth
}

$done = $false
while (-not $done) {
    $key = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")

    switch ($key.VirtualKeyCode) {
        38 { if ($menuCursor -gt 0)             { $menuCursor-- } }          # Up
        40 { if ($menuCursor -lt $menuCount-1)  { $menuCursor++ } }          # Down
        32 { # Space - toggle
            if ($menuSelected.ContainsKey($menuCursor)) { $menuSelected.Remove($menuCursor) }
            else { $menuSelected[$menuCursor] = $true }
        }
        65 { # A - toggle all
            if ($menuSelected.Count -eq $menuCount) { $menuSelected = @{} }
            else { for ($i = 0; $i -lt $menuCount; $i++) { $menuSelected[$i] = $true } }
        }
        13 { if ($menuSelected.Count -gt 0) { $done = $true } }              # Enter
    }

    if (-not $done) {
        try { [Console]::SetCursorPosition(0, $menuStartRow) } catch {}
        for ($i = 0; $i -lt $menuCount; $i++) {
            Render-MenuRow $projects[$i] ($i -eq $menuCursor) ($menuSelected.ContainsKey($i)) $winWidth
        }
    }
}

try { [Console]::CursorVisible = $true } catch {}
Write-Host ""

$selectedIndices = $menuSelected.Keys | Sort-Object

# ---------------------------------------------------------------------------
# 4. Install into each selected project
# ---------------------------------------------------------------------------
$failed = @()

foreach ($idx in $selectedIndices) {
    $p = $projects[$idx]
    Write-Step "Installing into: $($p.name)"
    Write-Info $p.repo_path

    & $nywCmd install-plugin --project $p.repo_path --force
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
    Write-Host "     3. Start the Nytwatch server and arm systems from the Tracker page"
} else {
    Write-Host "Completed with errors." -ForegroundColor Yellow
    Write-Host ""
    Write-Host "   Failed:" -ForegroundColor Red
    foreach ($f in $failed) { Write-Host "     - $f" -ForegroundColor Red }
    Write-Host ""
    Write-Host "   Re-run this script for failed projects after resolving any issues."
}
Write-Host ""
Read-Host "Press Enter to close"
