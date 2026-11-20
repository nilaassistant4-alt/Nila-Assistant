@echo off
rem ==========================================================================
rem  Nila - the ONLY file you double-click after extracting the ZIP.
rem
rem  The owner's first problem, in his words: "I installed it, extracted it, then
rem  it asks for a password and it does not open". Three things made that happen,
rem  and this file exists to make all three impossible to hit by accident:
rem
rem    1. an OLD Nila asked for the passphrase at every start and remembered
rem       nothing. This build remembers the master key on this laptop once.
rem    2. the vault lives OUTSIDE this folder (%LOCALAPPDATA%\Nila\vault), so a
rem       fresh extract is supposed to find the old vault - and shows nothing.
rem       This file says which folder it found and when it was last used.
rem    3. a person who has forgotten the passphrase needs a way in that is not a
rem       guess. One printed recovery code opens the vault by itself.
rem
rem  It never deletes anything, never re-creates a vault that exists, and always
rem  stops with a message instead of closing the window.
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
echo   Nila - starting up
echo ==============================================
echo.
echo Where your memories live (it is NOT inside this folder):
echo   %BRAIN_HOME%
echo   Replacing or re-extracting this folder never touches that one.
echo.

rem ---- 1. what is actually on this laptop, in one screen ------------------
"%NILA_PY%" brain.py come-from
echo.

rem ---- 2. a vault that does not exist yet, or cannot be opened -----------
"%NILA_PY%" brain.py lock-check --json > "%TEMP%\nila-state.json" 2>nul
if errorlevel 1 (
  echo Could not read the vault state. Running the diagnosis again with details:
  "%NILA_PY%" brain.py come-from
  pause
  exit /b 1
)
findstr /C:"\"has_vault\": false" "%TEMP%\nila-state.json" >nul
if not errorlevel 1 (
  echo No vault on this laptop yet - setup runs now, ONCE.
  echo Choose a passphrase you can write down, and write the recovery codes down too.
  echo.
  call "%~dp0setup.bat"
  if errorlevel 1 (
    echo Setup stopped. Read the line above, fix it, and run THIS file again.
    pause
    exit /b 1
  )
  echo.
)
findstr /C:"\"remembered\": true" "%TEMP%\nila-state.json" >nul
if errorlevel 1 (
  echo This laptop does not remember the key yet - fixing that now, once.
  "%NILA_PY%" brain.py passphrase remember
  echo.
)

rem ---- 3. every situation this file can meet, and the honest answer ------
"%NILA_PY%" brain.py come-from --json > "%TEMP%\nila-state.json" 2>nul
findstr /C:"\"remembered\": true" "%TEMP%\nila-state.json" >nul
if not errorlevel 1 goto :start

echo This laptop still cannot open Nila on its own.
echo.
echo   If you have ONE recovery code (printed at setup), type it here.
echo   It opens the vault once, with no passphrase and no phone.
echo.
set /p NILA_CODE=Recovery code (or just press Enter to stop): 
if "%NILA_CODE%"=="" goto :stuck
rem The code is PIPED in, never put on the command line: an argument is visible
rem to Task Manager, ps, and any log that records argv. One code is spent by
rem this - that is the deal, and each one works exactly once. `dashboard` and
rem `start` both open the app; dashboard is the one the icons use.
echo %NILA_CODE%| "%NILA_PY%" brain.py dashboard --recovery-stdin
if errorlevel 1 (
  echo.
  echo That code did not open the vault, so nothing was changed. Try another one.
  goto :stuck
)
echo.
echo That code is now spent - the others still work. Choose a passphrase you will
echo remember (write it on paper; it is the only thing that opens a NEW laptop):
"%NILA_PY%" brain.py passphrase set
if errorlevel 1 goto :stuck
echo.
echo Remembering this laptop, so nothing is asked here again:
"%NILA_PY%" brain.py passphrase remember
if errorlevel 1 (
  echo The key could not be saved. Nila still opens with the passphrase you set.
)
echo.
echo Run this same file again from now on: it will not ask for anything.
pause
exit /b 0

:stuck
echo.
echo ==============================================
echo   Nila could not be opened - and nothing was changed
echo ==============================================
echo Your vault is untouched. Nothing was deleted or overwritten.
echo.
echo Send me the text of this window (copy all of it). It says which of these
echo it is, and I will answer exactly:
echo   1. a passphrase was never set or has been forgotten  -^> a recovery code,
echo      or `brain passphrase set` once anything opens the vault
echo   2. the authenticator app has a stale "Nila" entry      -^> the 6-digit code
echo   3. this is a NEW vault and your memories are in the OLD folder
echo.
echo report.bat writes the same thing to a file, with no secrets in it.
pause
exit /b 1

:start
rem ---- 4. one key: the icon, once ------------------------------------------
rem The owner asked for "the keyboard shortcut, not typing commands". Windows
rem owns the Shortcut key (no program may set it), so what this writes is the
rem Nila icon on the Desktop - one double-click - and Windows asks for the
rem keystroke in its own Properties box. Written once; a failure never stops
rem the start.
if not exist "%~dp0nila-start.vbs" (
  echo Making the Desktop icon (one double-click from now on)...
  "%NILA_PY%" brain.py hotkey --write >nul 2>&1
  if exist "%~dp0nila-start.vbs" (
    echo   done - you can also give that icon Ctrl+Alt+N once, in Properties.
  ) else (
    echo   the icon could not be written; starting Nila works anyway.
  )
  echo.
)
echo Opening Nila. Nothing will be asked on this laptop.
"%NILA_PY%" brain.py dashboard
set NILA_RC=%ERRORLEVEL%
if not "%NILA_RC%"=="0" (
  echo.
  echo Nila stopped with code %NILA_RC%. The line just above says why.
  echo report.bat writes a short, private summary.
  pause
)
exit /b %NILA_RC%

:done
echo.
echo Done. Next time just double-click this same file (or the Nila icon).
pause
exit /b 0
