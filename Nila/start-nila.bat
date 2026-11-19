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
if not exist "%BRAIN_HOME%\identity.json" (
  echo No vault found at:
  echo   %BRAIN_HOME%
  echo Run setup.bat first - it creates the vault and shows your recovery codes.
  pause
  exit /b 1
)
rem The password question, answered in the window instead of in a document:
rem after nila-first-run.bat this laptop remembers the key and nothing is asked.
"%NILA_PY%" brain.py lock-check 2>nul | findstr /C:"no-password" >nul
if errorlevel 1 (
  echo This laptop will ask for your passphrase ^(and your code, if you set one^).
  echo To stop that: double-click nila-first-run.bat once.
) else (
  echo Opening Nila. No passphrase needed on this laptop.
)
echo Close this window ^(or press Ctrl+C^) to stop Nila and lock the vault.
echo.
if /i "%~1"=="recovery" (
  set /p RC=Type ONE recovery code: 
  "%NILA_PY%" brain.py dashboard --recovery %RC%
) else (
  "%NILA_PY%" brain.py dashboard
)
set RC=%ERRORLEVEL%
echo.
if not "%RC%"=="0" (
  echo Nila stopped with code %RC%. The line just above says why.
  echo Run report.bat and send the text if you cannot read the reason.
) else (
  echo Nila stopped. Your vault is locked.
)
pause
