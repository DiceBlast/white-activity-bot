@echo off
setlocal
cd /d "%~dp0"
set "GITHUB_REPOSITORY=https://github.com/DiceBlast/white-activity-bot.git"
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
echo Configuring GitHub updates...
where git >nul 2>&1
if errorlevel 1 (
  echo Git was not found. The bot will work, but GitHub dashboard updates need Git for Windows.
  echo Install it with: winget install --id Git.Git -e --source winget
) else (
  if not exist ".git" git init >nul 2>&1
  git remote get-url origin >nul 2>&1
  if errorlevel 1 (
    git remote add origin "%GITHUB_REPOSITORY%" >nul 2>&1
  ) else (
    git remote set-url origin "%GITHUB_REPOSITORY%" >nul 2>&1
  )
  git fetch --prune origin
  if errorlevel 1 (
    echo GitHub connection could not be verified. If the repository is private, sign in to Git and run INSTALL.bat again.
  ) else (
    git reset --hard origin/main >nul 2>&1
    git branch -M main >nul 2>&1
    git branch --set-upstream-to=origin/main main >nul 2>&1
    echo GitHub updates are connected and ready.
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
