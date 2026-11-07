@echo off
setlocal
cd /d "%~dp0"
echo Starting the local demo. Open the URL printed below in Edge or Chrome.
call "%~dp0打开城配续航.cmd" --no-browser %*
