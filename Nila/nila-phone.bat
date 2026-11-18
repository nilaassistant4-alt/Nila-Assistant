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
  echo Run setup.bat first.
  pause
  exit /b 1
)
echo Nila - phone window.
echo While this window is open, you can message your bot from Telegram and Nila
echo answers: /help, /due, /list, /done 2, /snooze 2 10, /remind in 30 min ...
echo Close this window to stop answering; the vault locks with it.
echo.
echo Nila will ask for the passphrase, then your authenticator code.
echo.
"%NILA_PY%" brain.py telegram status
if errorlevel 1 (
  echo.
  echo Not set up yet. The five steps are:
  echo   python brain.py telegram how
  pause
  exit /b 1
)
echo.
"%NILA_PY%" brain.py telegram listen
set RC=%ERRORLEVEL%
echo.
if not "%RC%"=="0" (
  echo The phone window stopped with code %RC%.
  echo Not set up yet? Run:  "%NILA_PY%" brain.py telegram how
)
pause
