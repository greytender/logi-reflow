@echo off
setlocal
cd /d "%~dp0"
echo Starting the local demo. Open the URL printed below in Edge or Chrome.
echo This console stays open even if the service stops or startup fails.
"%ComSpec%" /d /k ""%~dp0start-demo.cmd" --no-browser %*"
