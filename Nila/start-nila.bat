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
echo Starting Nila. It asks for your passphrase, then your authenticator
echo code, and then opens the dashboard in your browser.
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
