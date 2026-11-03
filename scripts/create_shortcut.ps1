<#
.SYNOPSIS
  Creates Fox.lnk in the project's root folder, pointing at Fox.exe beside it.
.PARAMETER Desktop
  Also put a shortcut on the Desktop.
.PARAMETER StartMenu
  Also add Fox to the Start menu.
#>
param([switch]$Desktop, [switch]$StartMenu)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$target = Join-Path $root "Fox.exe"
if (-not (Test-Path $target)) {
    throw "$target doesn't exist yet. Run build.bat first."
}

$folders = @($root)
if ($Desktop) {
    $folders += [Environment]::GetFolderPath("Desktop")
}
if ($StartMenu) {
    $folders += [Environment]::GetFolderPath("Programs")
}

$shell = New-Object -ComObject WScript.Shell
foreach ($folder in $folders) {
    $path = Join-Path $folder "Fox.lnk"
    $shortcut = $shell.CreateShortcut($path)
    $shortcut.TargetPath = $target
    $shortcut.WorkingDirectory = $root
    $shortcut.IconLocation = "$target,0"
    $shortcut.Description = "Fox personal assistant"
    $shortcut.Save()
    Write-Host "Shortcut created: $path"
}
