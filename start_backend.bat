@echo off
setlocal
cd /d "%~dp0backend"
if not exist "venv\Scripts\python.exe" (
    python -m venv venv
    if errorlevel 1 goto failed
    "venv\Scripts\python.exe" -m pip install -r requirements.txt
    if errorlevel 1 goto failed
)
"venv\Scripts\python.exe" -c "import lightgbm" >nul 2>&1
if errorlevel 1 (
    "venv\Scripts\python.exe" -m pip --isolated install --index-url https://pypi.org/simple --only-binary=:all: "lightgbm==4.7.0"
    if errorlevel 1 goto failed
    "venv\Scripts\python.exe" -c "import lightgbm"
    if errorlevel 1 goto failed
)
echo Backend: http://127.0.0.1:8000
"venv\Scripts\python.exe" -m uvicorn main:app --host 127.0.0.1 --port 8000
goto end
:failed
echo Installation fehlgeschlagen. Bitte Python und Netzwerkzugriff pruefen.
:end
pause
