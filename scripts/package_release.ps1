<#
.SYNOPSIS
  Zips the built Fox.exe and _internal into dist\Fox-v<version>-windows-x64.zip for a GitHub release.
  Run build.bat first. Refuses if a private file is inside, and prints the zip's SHA-256.
#>
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$exe = Join-Path $root "Fox.exe"
$internal = Join-Path $root "_internal"
if (-not (Test-Path $exe) -or -not (Test-Path $internal)) {
    throw "Fox.exe or _internal is missing. Run build.bat first."
}

$version = (Select-String -Path pyproject.toml -Pattern '^version = "(.+)"').Matches[0].Groups[1].Value
$built = (Get-Item $exe).LastWriteTime
$changed = (Get-ChildItem fox -Recurse -File | Measure-Object LastWriteTime -Maximum).Maximum
if ($changed -gt $built) {
    throw "The code changed after Fox.exe was built. Run build.bat again."
}

# The same private files the build checks for, plus models you added.
$private = Get-ChildItem $internal -Recurse -Force -Include ".env", "fox.db", "api_token.txt", "permissions.json", "models.json", "gui.json", "control.json"
if ($private) {
    throw "The build contains private files and must not be shared: $($private.FullName -join ', ')"
}
$ownPlugins = Get-ChildItem (Join-Path $internal "defaults\plugins") -File -ErrorAction SilentlyContinue | Where-Object { $_.Name -notlike "_example*" }
if ($ownPlugins) {
    throw "The build contains your own plugins and must not be shared: $($ownPlugins.Name -join ', '). Rebuild with build.bat."
}

$dist = Join-Path $root "dist"
New-Item -ItemType Directory -Force $dist | Out-Null
$zip = Join-Path $dist "Fox-v$version-windows-x64.zip"
Remove-Item $zip -ErrorAction SilentlyContinue

# A folder named Fox inside the zip, so unzipping doesn't scatter files.
$stage = Join-Path ([IO.Path]::GetTempPath()) "fox-release-$version"
Remove-Item $stage -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force (Join-Path $stage "Fox") | Out-Null
Copy-Item $exe (Join-Path $stage "Fox")
robocopy $internal (Join-Path $stage "Fox\_internal") /E /NFL /NDL /NJH /NJS /NP | Out-Null
if ($LASTEXITCODE -ge 8) {
    throw "Could not copy _internal (robocopy exit code $LASTEXITCODE)."
}
$LASTEXITCODE = 0
Copy-Item LICENSE (Join-Path $stage "Fox\LICENSE.txt")

Compress-Archive -Path (Join-Path $stage "Fox") -DestinationPath $zip -CompressionLevel Optimal
Remove-Item $stage -Recurse -Force

$hash = (Get-FileHash $zip -Algorithm SHA256).Hash.ToLower()
Write-Host ("Created {0} ({1:N0} MB)" -f $zip, ((Get-Item $zip).Length / 1MB))
Write-Host "SHA-256: $hash"
