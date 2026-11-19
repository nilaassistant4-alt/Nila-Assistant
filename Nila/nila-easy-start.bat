@echo off
rem ==========================================================================
rem  Nila - EVERYDAY start. Double-click this and she is here.
rem
rem  What the owner asked for, in his words:
rem    "the moment I press the key Nila must come - if not, then my voice"
rem    "it must not keep asking me for the password again and again"
rem
rem  So this file does the only two things that make that true, and then shows
rem  the machine's own answer to "will it ask me again?":
rem     1. hotkey + shortcut   (one key, or the desktop icon)
rem     2. voice              (say "Nila" in a window, or press once and talk)
rem     3. the check          (brain lock-check - the plain yes/no)
rem
rem  Flags:
rem     nila-easy-start.bat --setup    first-time setup, then remember this laptop
rem     nila-easy-start.bat --forget   make Nila ask for the password again
rem     nila-easy-start.bat --voice    listen for the name now (say "Nila")
rem
rem  Nothing here opens the microphone permanently, watches your keyboard, or
rem  runs as a service. Every piece is a file you can see and delete.
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

if /i "%~1"=="--setup" (
  call "%~dp0nila-first-run.bat"
  exit /b %ERRORLEVEL%
)

if /i "%~1"=="--forget" (
  "%NILA_PY%" brain.py passphrase forget
  echo.
  echo Nila will ask for the passphrase from now on. Your memories are untouched.
  pause
  exit /b 0
)

if /i "%~1"=="--voice" (
  echo Say "Nila" now - the microphone is open for two minutes and then stops.
  "%NILA_PY%" brain.py wake --window 120
  pause
  exit /b %ERRORLEVEL%
)

if not exist "%BRAIN_HOME%\identity.json" (
  echo There is no vault yet on this laptop.
  echo Double-click nila-first-run.bat once - it sets everything up and remembers
  echo this laptop so the password is asked exactly once.
  pause
  exit /b 1
)

echo ==============================================
echo   Nila - everyday start
echo ==============================================
echo.
echo [1/2] The key and the icon...
"%NILA_PY%" brain.py hotkey --write
if errorlevel 1 (
  echo.
  echo The shortcut could not be written. Nila still starts with start-nila.bat.
)
echo.
echo [2/2] The voice...
"%NILA_PY%" brain.py hotkey --voice
echo.
echo ----------------------------------------------
"%NILA_PY%" brain.py lock-check
echo ----------------------------------------------
echo.
echo Ways to bring Nila up from now on:
echo   the Nila icon on the desktop          (double-click)
echo   the shortcut key you set on that icon (Windows asks you once, in Properties)
echo   nila-easy-start.bat --voice           (say "Nila", two-minute window)
echo   start-nila.bat                        (the plain start)
echo.
echo Now starting Nila...
"%NILA_PY%" brain.py dashboard
set RC=%ERRORLEVEL%
echo.
if not "%RC%"=="0" (
  echo Nila stopped with code %RC%. The line above says why.
  echo report.bat prints a short, private summary you can send me.
)
pause
