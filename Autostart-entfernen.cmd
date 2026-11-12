@echo off
powershell.exe -NoProfile -File "%~dp0tools\Install-Autostart.ps1" -Remove
echo.
pause
