@echo off
rem Double-click entry point. Runs launch.ps1 IN THIS VISIBLE WINDOW on purpose -
rem it used to hand off to a fully hidden PowerShell process, which meant any
rem failure before our own error-dialog code ran (most commonly: PowerShell
rem script execution blocked by school/IT Group Policy) vanished with zero
rem feedback - the window just closed. This window now stays open for the
rem whole run: closing it stops McGoverner, and if PowerShell itself refuses
rem to run the script, that error prints right here instead of disappearing.
title McGoverner
cd /d "%~dp0"
echo Starting McGoverner...
echo This window stays open while McGoverner is running - closing it stops the app.
echo.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0launch.ps1"
echo.
echo McGoverner has stopped. If that was unexpected, screenshot this window and send it to Rashid or Eshan.
pause
