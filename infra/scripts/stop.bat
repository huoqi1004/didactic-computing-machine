@echo off
title ERP Digital Brain - Stop
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0desktop-stop.ps1"
echo.
echo Script finished. Press any key to close...
pause >nul
