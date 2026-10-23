@echo off
setlocal
cd /d "%~dp0"
echo Activity Rewards v4.0.10 - SAFE CODE-ONLY UPGRADE
 echo.
echo Use Shutdown All on the existing bot before upgrading.
echo Drag the EXISTING bot folder into this window, then press Enter.
set /p "TARGET=Existing bot folder: "
if not defined TARGET (echo No folder selected.& pause& exit /b 1)
set "PYTHON_CMD=python"
where python >nul 2>&1
if errorlevel 1 set "PYTHON_CMD=py -3"
%PYTHON_CMD% "%~dp0safe_upgrade.py" "%TARGET%"
if errorlevel 1 (echo Upgrade FAILED. Review message above.& pause& exit /b 1)
echo Upgrade completed. Run INSTALL.bat in the existing bot folder if dependencies need updating.
pause
