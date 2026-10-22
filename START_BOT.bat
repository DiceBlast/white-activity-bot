@echo off
setlocal
cd /d "%~dp0"
title Activity Rewards - Bot Supervisor
if not exist ".venv\Scripts\python.exe" (
 echo ERROR: Run INSTALL.bat first.
 pause
 exit /b 1
)
if not exist "local_credentials.json" (
 echo ERROR: Run SETUP.bat first.
 pause
 exit /b 1
)
"%~dp0.venv\Scripts\python.exe" "%~dp0bot_supervisor.py"
exit /b %errorlevel%
