@echo off
cd /d "%~dp0"
call "%~dp0final\打开城配续航.cmd" %*
if errorlevel 1 pause
