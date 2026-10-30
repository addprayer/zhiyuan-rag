@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHONPATH=%~dp0;%~dp0.pylibs;%PYTHONPATH%"
where py >nul 2>nul
if not errorlevel 1 (
    py -3 launcher.py %*
) else (
    python launcher.py %*
)
if errorlevel 1 pause
endlocal
