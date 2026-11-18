@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
call "%~dp0nila-env.bat"
if not exist "%BRAIN_HOME%\identity.json" (
  echo No vault found. Run setup.bat first.
  pause
  exit /b 1
)
echo Starting Nila. Your browser opens when you are logged in.
echo Close this window (or press Ctrl+C) to stop Nila and lock the vault.
echo.
if /i "%~1"=="recovery" (
  set /p RC=Type ONE recovery code: 
  python brain.py serve --open --recovery %RC%
) else (
  python brain.py serve --open
)
echo.
pause
