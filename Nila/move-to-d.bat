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
rem "ALL of it" (the vault folder) or "just the memories" (the archive): the
rem owner's request was the second one - the memories on D: - while this file
rem has always copied the vault. Right-clicking used to run THIS silently, so
rem the wrong one happened by default. Nothing is copied until 1 or 2 is typed.
echo What should go to the other drive?
echo   1  the whole vault folder   (the stronger move - everything on D:)
echo   2  only the memories file   (the right-click choice - memories on D:)
set /p WHICH=Type 1 or 2: 
if "%WHICH%"=="2" (
  call "%~dp0move-memories-to-d.bat"
  exit /b %ERRORLEVEL%
)
if not "%WHICH%"=="1" (
  echo Nothing was chosen, so nothing was changed.
  pause
  exit /b 1
)
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
echo Done. Start Nila with nila-start.bat - it reads the pointer the copy left,
echo so the vault on the other drive is the one that opens.
echo Your old folder was kept. Delete it yourself once you are sure.
pause
