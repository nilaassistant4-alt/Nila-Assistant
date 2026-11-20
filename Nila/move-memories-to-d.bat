@echo off
rem ==========================================================================
rem  Nila - put the MEMORIES on D: (the big drive), the safe way.
rem
rem  What the owner asked for, in his words: "go inside and move the memory to
rem  the D drive". This is that, and the order is not negotiable:
rem
rem    1. Nila must be closed. A copy taken while the database is open is a
rem       copy of a database being written to; this checks and refuses.
rem    2. COPY, never move. The old folder stays exactly where it is.
rem    3. Compare EVERY file by size and SHA-256. A size-only check is what
rem       loses a database.
rem    4. Only after an exact match, leave the pointer that every start reads.
rem    5. Then prove it: one command opens the memories from the new place.
rem
rem  Nothing is deleted, here or afterwards - deleting the old folder is the
rem  owner's decision, made later, by hand.
rem ==========================================================================
setlocal
chcp 65001 >nul 2>&1
cd /d "%~dp0"
call "%~dp0nila-env.bat"
call "%~dp0nila-common-check.bat"
if errorlevel 1 (
  pause
  exit /b 1
)

echo ==============================================
echo   Nila - memories to another drive
echo ==============================================
echo.
echo Vault now : %BRAIN_HOME%
echo.
echo Nila must be closed first. If a Nila window is still open (the dashboard,
echo the phone window), close it now.
echo.
pause

set DEST=
set /p DEST=New folder for the memories (for example D:\Nila-data): 
if "%DEST%"=="" (
  echo Nothing was typed, so nothing was changed.
  pause
  exit /b 1
)
echo.
echo Step 1 of 3 - what would be copied (this writes nothing):
"%NILA_PY%" "%~dp0tools\move_storage.py" --to "%DEST%" --dry-run
if errorlevel 1 (
  echo.
  echo The check stopped. Nothing was changed. The line above says why.
  pause
  exit /b 1
)
echo.
echo Step 2 of 3 - copy and compare every file
set GO=
set /p GO=Type YES to copy (the old folder is kept): 
if /i not "%GO%"=="YES" (
  echo Cancelled. Nothing was changed.
  pause
  exit /b 1
)
"%NILA_PY%" "%~dp0tools\move_storage.py" --to "%DEST%" --yes
if errorlevel 1 (
  echo.
  echo STOPPED. Your old folder is untouched and still in use. Read the line above.
  pause
  exit /b 1
)

echo.
echo Step 3 of 3 - prove the memories open from the new place:
"%NILA_PY%" brain.py come-from
if errorlevel 1 (
  echo.
  echo The memories did not open from the new place. NOTHING was deleted.
  echo Send me the text above.
  pause
  exit /b 1
)
echo.
echo ==============================================
echo   Done. From now on Nila uses: %DEST%
echo   Every start reads the pointer left in %BRAIN_HOME%
echo   The old folder is still there, untouched.
echo ==============================================
echo.
echo Next: double-click nila-start.bat as usual.
pause
