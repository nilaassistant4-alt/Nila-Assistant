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
rem this - that is the deal, and each one works exactly once.
rem
rem AFTER it opens, the app KEEPS RUNNING: `brain dashboard` is a server. Asking
rem for a new passphrase from this window while it runs would be a second unlock
rem of a vault that is already open (and the session file would be overwritten
rem under it). So the window says what to do next and leaves the app alone.
echo %NILA_CODE%| "%NILA_PY%" brain.py dashboard --recovery-stdin
set NILA_RC=%ERRORLEVEL%
if not "%NILA_RC%"=="0" (
  echo.
  echo Nothing was changed. Run this file again and type another code.
  pause
  exit /b %NILA_RC%
)
echo.
echo (Nila was running until you closed it - the window above is now yours again.)
echo Choose a passphrase you will remember with:
echo     "%NILA_PY%" brain.py passphrase set
pause
exit /b 0

:stuck
echo.
echo ==============================================
echo   Nila could not be opened
echo ==============================================
echo Your vault is safe: nothing has been deleted or overwritten.
echo.
echo You have three ways in, and only these three. Read them once:
echo.
echo   1. THE PASSPHRASE - the long word YOU chose at setup.
echo      It is typed where the window says "passphrase". Nothing appears on
echo      the screen while you type - that is normal - so press Enter after it.
echo      On paper: wherever you wrote it at setup. Nowhere else. It is not in
echo      any file on this PC, and I cannot look it up for you.
echo.
echo   2. A RECOVERY CODE - a word like  abcde-fghij  in groups of 5 letters,
echo      8 of them, shown ONCE at setup. That is the only place they exist.
echo      To use one: close this window, open this file again, and when it asks
echo      "Recovery code (or just press Enter to stop)" type one code.
echo      It opens the vault once, with no passphrase and no phone.
echo.
echo   3. START A NEW VAULT - if you have neither 1 nor 2, this is the way out.
echo      Your OLD vault is COPIED to a folder beside it and kept, and a NEW
echo      vault is made with a NEW passphrase that YOU type now. Nothing is
echo      deleted. This is the honest fix, and it is also the safe one.
echo.
echo ==============================================
echo   Type a RECOVERY CODE and press Enter to try one now,
echo   or type  NEW  to start a new vault, or press Enter to stop.
echo ==============================================
set NILA_CHOICE=
set /p NILA_CHOICE=Recovery code, NEW, or Enter: 
rem LABELS, not if(...) blocks: cmd.exe expands %NILA_CHOICE% when it PARSES a
rem block, i.e. before anything is typed. That exact shape once sent an empty
rem recovery code forever (stage 17), so this file does not use it any more.
if "%NILA_CHOICE%"=="" goto :nila_stopped
if /i "%NILA_CHOICE%"=="NEW" goto :nila_new_vault
echo Trying that code...
echo %NILA_CHOICE%| "%NILA_PY%" brain.py lock-check --recovery-stdin >nul 2>&1
if errorlevel 1 goto :nila_bad_code
echo The code opened the vault. Now choose a passphrase you will remember:
"%NILA_PY%" brain.py passphrase set
"%NILA_PY%" brain.py passphrase remember
echo.
echo From now on this laptop opens by itself. Write the passphrase on paper.
pause
exit /b 0

:nila_bad_code
echo.
echo That code did not open the vault. Nothing was changed. Run this file again
echo and type another code, or type NEW.
pause
exit /b 1

:nila_stopped
echo.
echo Stopped. Nothing was changed. Your vault is exactly as it was.
echo report.bat writes the same thing to a file, with no secrets in it.
pause
exit /b 1

:nila_new_vault
echo.
echo Starting a NEW vault. The old one is copied first and kept.
rem The new vault is created, this laptop remembers it, and then the app is
rem STARTED HERE, once: opening it twice (this line plus :start) left two servers
rem racing for the same port, and the owner saw "Address already in use".
"%NILA_PY%" brain.py new-vault
if errorlevel 1 (
  echo.
  echo The new vault was not created. The old one is untouched. Send me the
  echo text above.
  pause
  exit /b 1
)
echo.
echo Opening Nila with the new vault...
"%NILA_PY%" brain.py dashboard
set NILA_RC=%ERRORLEVEL%
if not "%NILA_RC%"=="0" (
  echo.
  echo Nila stopped with code %NILA_RC%. The line above says why.
  pause
)
exit /b %NILA_RC%

:start
rem ---- 4. the icon, and only when it is missing ---------------------------
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
rem ---- 5. the Desktop icon and the sign-in start: once ------------------
rem The owner's plan: "extract once, then only ever double-click the shortcut".
rem Both are files he can see and delete: the icon on the Desktop, and a .vbs in
rem his OWN Startup folder (not a service, no registry). Written once, and a
rem failure never stops the start.
if not exist "%~dp0nila-start.vbs" (
  "%NILA_PY%" brain.py launcher --add-startup >nul 2>&1
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
