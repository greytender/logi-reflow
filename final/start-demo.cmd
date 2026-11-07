@echo off
setlocal
cd /d "%~dp0"
title LogiReflow - Local Demo
if not exist "%~dp0python\python.exe" goto system_python
"%~dp0python\python.exe" -B "%~dp0launch.py" %*
goto finished
:system_python
where python >nul 2>nul
if errorlevel 1 goto missing
python -B "%~dp0launch.py" %*
goto finished
:missing
echo Python runtime is missing. Extract the COMPLETE ZIP before starting.
echo Use the Windows x64 offline demo package.
pause
exit /b 1
:finished
if errorlevel 1 (
echo Startup failed. Please keep this error message for troubleshooting.
pause
exit /b 1
)
