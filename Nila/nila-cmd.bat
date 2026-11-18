@echo off
rem Opens a command window with Nila's settings loaded, for commands like:
rem   python brain.py config set agent.online_daily_requests 200
rem   python brain.py doctor
rem Close the window to lock the vault.
chcp 65001 >nul 2>&1
cd /d "%~dp0"
call "%~dp0nila-env.bat"
call "%~dp0nila-common-check.bat"
if errorlevel 1 (
  pause
  exit /b 1
)
echo Nila command window. Your data folder is:
echo   %BRAIN_HOME%
echo Type commands like:  python brain.py doctor
echo Close this window when you are done.
echo (This window cannot be paused for you: it is a live command prompt.)
echo.
cmd /k
