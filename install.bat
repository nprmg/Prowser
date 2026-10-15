@echo off
cd /d "%~dp0"
echo === J.A.R.V.I.S. setup ===

set "PY=python"
where py >nul 2>nul && set "PY=py -3"
%PY% --version >nul 2>nul
if errorlevel 1 (
    echo Python not found. Install Python 3.11-3.13 from https://www.python.org/downloads/
    echo and tick "Add python.exe to PATH" during installation.
    pause
    exit /b 1
)

if not exist venv\Scripts\python.exe (
    echo Creating virtual environment...
    %PY% -m venv venv
    if errorlevel 1 goto :error
)

echo Installing dependencies...
venv\Scripts\python.exe -m pip install --upgrade pip
venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 goto :error

if not exist .env copy .env.example .env >nul

echo.
echo Done! Run start.bat - on first start you will be asked for your Gemini API key.
pause
exit /b 0

:error
echo.
echo Installation failed. See the messages above.
pause
exit /b 1
