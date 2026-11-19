@echo off
rem ==========================================================================
rem  Nila - FIRST RUN on this laptop. Double-click this, once.
rem
rem  Why this file exists: the owner unzipped Nila, pressed setup, typed a
rem  passphrase, and then every single start asked for it again - and he could
rem  not remember what it was. His words: "type it once at first run and after a
rem  restart it must not ask again". That is exactly what this does, and it is
rem  the ONLY thing in Nila that is new here.
rem
rem  It runs in three steps, and stops with a readable message if any step fails:
rem     1. setup.bat          - Python, packages, the vault (only if there is none)
rem     2. brain lock-check   - the one question he had: "will it ask me again?"
rem     3. brain passphrase remember - save the device key in Windows' own store
rem
rem  After this, start-nila.bat opens Nila with no password prompt on THIS
rem  Windows account. `nila-easy-start.bat --setup` does the same thing later,
rem  and `brain passphrase forget` undoes it.
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
echo   Nila - first run on this laptop
echo ==============================================
echo.

if not exist "%BRAIN_HOME%\identity.json" (
  echo [1/3] No vault here yet, so setup runs now.
  echo       You will be asked to CHOOSE a passphrase. Choose one you can write
  echo       down, and write it down - this is the only copy that opens Nila on a
  echo       NEW laptop. On this laptop you will not need it again.
  echo.
  call "%~dp0setup.bat"
  if errorlevel 1 (
    echo.
    echo Setup stopped. Read the line above, fix it, and run this file again.
    pause
    exit /b 1
  )
) else (
  echo [1/3] A vault already exists at:
  echo       %BRAIN_HOME%
  echo       Nothing is recreated, and nothing is deleted.
)
echo.

echo [2/3] Checking how Nila opens on this laptop...
"%NILA_PY%" brain.py lock-check
echo.

echo [3/3] Remembering the key on this Windows account...
rem Setup already did this when it could; asking twice would be the very thing
rem the owner complained about, so the status is checked first.
"%NILA_PY%" brain.py passphrase status | findstr /C:"remembered on this laptop: YES" >nul
if not errorlevel 1 (
  echo       Already remembered during setup - nothing more to do.
  goto :after_remember
)
"%NILA_PY%" brain.py passphrase remember
if errorlevel 1 (
  echo.
  echo The key could not be saved. Nila still works - it will simply ask for the
  echo passphrase each time. The line above says why.
  echo   Fix: "%NILA_PY%" -m pip install keyring
  pause
  exit /b 1
)
:after_remember
echo.
"%NILA_PY%" brain.py passphrase status
echo.
echo ==============================================
echo   Done. From now on:
echo     start-nila.bat        open Nila (no password prompt)
echo     nila-easy-start.bat   same, plus a hotkey and voice (optional)
echo ==============================================
pause
