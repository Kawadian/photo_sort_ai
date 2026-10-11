@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Run install.bat first.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" -m photo_sort_ai --config "%~dp0config.json" web %*
if errorlevel 1 pause
