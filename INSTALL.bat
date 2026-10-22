@echo off
setlocal
cd /d "%~dp0"
set "PYTHON_CMD="
where python >nul 2>&1
if not errorlevel 1 (
  python -c "import sys; assert sys.version_info >= (3, 11)" >nul 2>&1
  if not errorlevel 1 set "PYTHON_CMD=python"
)
if not defined PYTHON_CMD (
  where py >nul 2>&1
  if not errorlevel 1 (
    py -3 -c "import sys; assert sys.version_info >= (3, 11)" >nul 2>&1
    if not errorlevel 1 set "PYTHON_CMD=py -3"
  )
)
if not exist ".venv\Scripts\python.exe" (
  if not defined PYTHON_CMD (
    echo Python 3.11 or newer was not found. Install Python from https://www.python.org/downloads/
    pause
    exit /b 1
  )
  echo Creating the virtual environment...
  %PYTHON_CMD% -m venv ".venv"
  if errorlevel 1 (echo Failed to create virtual environment.& pause& exit /b 1)
)
echo Installing Activity Rewards requirements...
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (echo Installation failed.& pause& exit /b 1)
echo Installation complete.
pause
