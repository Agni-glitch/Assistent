<#
.SYNOPSIS
  Builds Fox.exe and its _internal folder in the project's root folder, and creates Fox.lnk beside them.
.PARAMETER SkipTests
  Don't run the test suite first.
.PARAMETER NoShortcut
  Don't create any shortcut.
.PARAMETER Desktop
  Also put a shortcut on the Desktop.
.PARAMETER StartMenu
  Also add Fox to the Start menu.
#>
param([switch]$SkipTests, [switch]$NoShortcut, [switch]$Desktop, [switch]$StartMenu)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function Run-Step([string]$title, [scriptblock]$command) {
    Write-Host "`n== $title" -ForegroundColor Cyan
    & $command
    if ($LASTEXITCODE -ne 0) {
        throw "$title failed (exit code $LASTEXITCODE)."
    }
}

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw "uv isn't installed. Get it from https://docs.astral.sh/uv/getting-started/installation/"
}

$exe = Join-Path $root "Fox.exe"
$internal = Join-Path $root "_internal"
function Assert-FoxClosed {
    $running = Get-Process -Name Fox -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq $exe }
    if ($running) {
        throw "Fox is open. Close its window, then build again."
    }
}
Assert-FoxClosed

Run-Step "Installing dependencies" { uv sync --all-extras --group build }
if (-not $SkipTests) {
    Run-Step "Running tests" { uv run --no-sync pytest -q }
}
Run-Step "Drawing the icon" { uv run --no-sync python scripts\make_icon.py }
Run-Step "Building Fox.exe (this takes a few minutes)" {
    uv run --no-sync pyinstaller fox.spec --noconfirm --clean --distpath build\out --workpath build\work --log-level WARN
}

Write-Host "`n== Checking the build" -ForegroundColor Cyan
$secrets = Get-ChildItem build\out\Fox -Recurse -Force -Include ".env", "fox.db", "api_token.txt", "permissions.json"
if ($secrets) {
    throw "The build contains private files and must not be shared: $($secrets.FullName -join ', ')"
}

# Antivirus and file indexers briefly lock new files, so removing and copying them needs retries.
function Remove-WithRetry([string]$path) {
    for ($attempt = 1; Test-Path $path; $attempt++) {
        try {
            Remove-Item $path -Recurse -Force
        } catch {
            if ($attempt -ge 10) { throw }
            Start-Sleep -Seconds 2
        }
    }
}

# Only these two are replaced. Fox.exe goes in last, so an unfinished copy can't be started.
Write-Host "Installing into $root"
Assert-FoxClosed  # it may have been opened while the build was running
Remove-WithRetry $exe
Remove-WithRetry $internal
robocopy build\out\Fox\_internal $internal /E /R:10 /W:2 /NFL /NDL /NJH /NJS /NP | Out-Null
if ($LASTEXITCODE -ge 8) {
    throw "Could not copy _internal into place (robocopy exit code $LASTEXITCODE)."
}
$LASTEXITCODE = 0
Copy-Item build\out\Fox\Fox.exe $exe
try {
    Remove-WithRetry build
} catch {
    Write-Warning "Could not remove the build folder. It's safe to delete it yourself: $_"
}

# Fox.exe has no console, so it writes its result to a file. A crash shows a dialog, hence the time limit.
$report = Join-Path $env:TEMP "fox-self-check.txt"
Remove-Item $report -ErrorAction SilentlyContinue
$check = Start-Process -FilePath $exe -ArgumentList "--self-check", "`"$report`"" -PassThru
$null = $check.Handle  # needed to read the exit code afterwards
if (-not $check.WaitForExit(180000)) {
    $check.Kill()
    throw "Fox.exe didn't finish its self check within 3 minutes."
}
if ($check.ExitCode -ne 0) {
    $details = if (Test-Path $report) { Get-Content $report -Raw } else { "It stopped before writing a report." }
    throw "Fox.exe failed its self check:`n$details"
}
$size = ((Get-Item $exe).Length + (Get-ChildItem $internal -Recurse -File | Measure-Object Length -Sum).Sum) / 1MB
Write-Host ("Self check passed. Build size: {0:N0} MB" -f $size)

if (-not $NoShortcut) {
    Write-Host "`n== Creating the shortcut" -ForegroundColor Cyan
    & (Join-Path $PSScriptRoot "create_shortcut.ps1") -Desktop:$Desktop -StartMenu:$StartMenu
}

Write-Host "`nDone: $exe" -ForegroundColor Green
