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
echo Writing a report. It contains no passphrase, no key and no message text.
echo It is saved next to this file and printed below.
echo.
"%NILA_PY%" brain.py report
if errorlevel 1 (
  echo.
  echo The report failed. Copy the text above - it says which part could not run.
  pause
  exit /b 1
)
echo.
pause
