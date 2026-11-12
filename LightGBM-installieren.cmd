@echo off
setlocal
cd /d "%~dp0"
"backend\venv\Scripts\python.exe" -m pip --isolated install --index-url https://pypi.org/simple --only-binary=:all: "lightgbm==4.7.0"
if errorlevel 1 goto failed
"backend\venv\Scripts\python.exe" tools\verify_lightgbm.py
if errorlevel 1 goto failed
echo LightGBM ist bereit. Backend neu starten, dann in der Website Daten aktualisieren und trainieren.
goto end
:failed
echo Installation oder echter Trainingstest fehlgeschlagen. Details stehen oben.
:end
pause
