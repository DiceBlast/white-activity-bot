@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (echo Run INSTALL.bat first.& pause& exit /b 1)
".venv\Scripts\python.exe" multi_window_dashboard.py
if errorlevel 1 pause
