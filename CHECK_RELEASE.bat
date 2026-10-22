@echo off
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" "release_preflight.py"
) else (
    python "release_preflight.py"
)
echo.
echo Check complete. Review any CHECK lines above.
pause
