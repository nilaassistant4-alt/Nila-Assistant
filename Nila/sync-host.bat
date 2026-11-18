@echo off
setlocal
chcp 65001 >nul 2>&1
cd /d "%~dp0"
call "%~dp0nila-env.bat"
call "%~dp0nila-common-check.bat"
if errorlevel 1 (
  pause
  exit /b 1
)
echo Sharing this PC's memory with your other PC.
echo Nothing goes to the internet: both PCs must be on the same Wi-Fi.
echo Keep this window open while the other PC is syncing.
echo.
"%NILA_PY%" tools\sync_lan.py start
set RC=%ERRORLEVEL%
echo.
if not "%RC%"=="0" (
  echo The sharing window stopped with code %RC%. The line above says why.
)
pause
