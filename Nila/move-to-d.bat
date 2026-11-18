@echo off
rem Moves Nila's data to another drive - by COPYING, checking every file, then switching.
rem Nothing is deleted. The old folder is kept as vault-old until YOU delete it.
setlocal
chcp 65001 >nul 2>&1
cd /d "%~dp0"
call "%~dp0nila-env.bat"
call "%~dp0nila-common-check.bat"
if errorlevel 1 (
  pause
  exit /b 1
)
echo.
echo This copies your vault to another drive, compares every file, and only then
echo switches Nila to the new place. Nothing is deleted.
echo.
set /p DEST=New data folder (for example D:\Nila-data): 
if "%DEST%"=="" (
  echo Nothing typed, so nothing was changed.
  pause
  exit /b 1
)
echo.
echo Step 1 of 2 - check (writes nothing):
"%NILA_PY%" "%~dp0tools\move_storage.py" --to "%DEST%" --dry-run
if errorlevel 1 (
  echo.
  echo The check failed. Nothing was changed.
  pause
  exit /b 1
)
echo.
set /p OK=Type YES to copy and switch: 
if /i not "%OK%"=="YES" (
  echo Cancelled. Nothing was changed.
  pause
  exit /b 1
)
echo.
echo Step 2 of 2 - copy, verify, switch:
"%NILA_PY%" "%~dp0tools\move_storage.py" --to "%DEST%" --yes
if errorlevel 1 (
  echo.
  echo STOPPED. Read the message above; your old folder is untouched.
  pause
  exit /b 1
)
echo.
echo Done. Start Nila with start-nila.bat and run: "%NILA_PY%" brain.py doctor
pause
