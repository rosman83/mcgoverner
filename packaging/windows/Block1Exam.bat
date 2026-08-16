@echo off
rem Double-click entry point. Hands off to launch.ps1 in a hidden PowerShell
rem window immediately, so this console closes right away instead of staying
rem open for the whole run - matches the Mac .app's "no visible terminal" feel
rem as closely as Windows allows without a compiled binary.
start "" /min powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0launch.ps1"
exit
