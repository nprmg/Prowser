@echo off
cd /d "%~dp0"
chcp 65001 >nul
if not exist venv\Scripts\python.exe (
    echo Run install.bat first.
    pause
    exit /b 1
)
venv\Scripts\python.exe main.py --mode chat %*
pause
