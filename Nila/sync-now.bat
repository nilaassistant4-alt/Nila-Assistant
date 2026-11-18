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
echo Syncing now. Nila asks for your passphrase and does not show it on screen.
"%NILA_PY%" brain.py sync
set RC=%ERRORLEVEL%
echo.
if not "%RC%"=="0" (
  echo Sync did not finish. The line above says why.
) else (
  echo Done. Both PCs now have the same memory.
)
pause
