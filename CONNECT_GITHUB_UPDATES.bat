@echo off
setlocal
cd /d "%~dp0"
echo Activity Rewards - Connect a legacy ZIP installation to GitHub updates
echo.
echo Shut down the running bot first. This tool backs up token, data, logs, backups,
echo and runtime files before connecting the selected installation to GitHub.
echo.
echo Drag the EXISTING bot folder into this window, then press Enter.
set /p "TARGET=Existing bot folder: "
if not defined TARGET (echo No folder selected.& pause& exit /b 1)
set "PYTHON_CMD=python"
where python >nul 2>&1
if errorlevel 1 set "PYTHON_CMD=py -3"
%PYTHON_CMD% "%~dp0connect_github_updates.py" "%TARGET%"
if errorlevel 1 (echo Connection FAILED. Review the message above.& pause& exit /b 1)
pause
