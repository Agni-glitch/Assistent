@echo off
rem Builds Fox.exe and Fox.lnk in this folder. Options: -SkipTests -NoShortcut -Desktop -StartMenu
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\build.ps1" %*
if errorlevel 1 (
    echo.
    echo Build failed.
    exit /b 1
)
