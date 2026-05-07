@echo off
setlocal

set "REPO_ROOT=%~dp0..\.."
set "SCRIPT=%REPO_ROOT%\scripts\api\restart-edge-api.ps1"

powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT%"

echo.
echo Press any key to close this window.
pause >nul
