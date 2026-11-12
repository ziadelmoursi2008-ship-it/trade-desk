@echo off
setlocal
cd /d "%~dp0frontend"
node -e "const [a,b]=process.versions.node.split('.').map(Number);process.exit(a>22 || a===22 && b>=18 ? 0 : 1)"
if errorlevel 1 (
    echo Bitte Node.js 24 LTS oder mindestens Version 22.18 installieren.
    goto end
)
if not exist "node_modules" (
    call npm.cmd ci
    if errorlevel 1 goto end
)
call npm.cmd run dev -- --host 127.0.0.1 --port 5173 --strictPort
:end
pause
