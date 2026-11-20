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
  echo Run setup.bat first - it creates the vault and shows your recovery codes.
  pause
  exit /b 1
)
rem The password question, answered in the window instead of in a document:
rem after nila-first-run.bat this laptop remembers the key and nothing is asked.
"%NILA_PY%" brain.py lock-check 2>nul | findstr /C:"no-password" >nul
if errorlevel 1 (
  echo This laptop will ask for your passphrase ^(and your code, if you set one^).
  echo To stop that: double-click nila-first-run.bat once.
) else (
  echo Opening Nila. No passphrase needed on this laptop.
)
echo Close this window ^(or press Ctrl+C^) to stop Nila and lock the vault.
echo.
rem NOT `if (...) else (...)`: inside a parenthesised block cmd.exe expands
rem %NILA_CODE% when it PARSES the block, i.e. before the owner has typed it, so
rem the code was sent as an empty line. Labels run line by line instead.
if /i "%~1"=="recovery" goto :nila_recovery
"%NILA_PY%" brain.py dashboard
set RC=%ERRORLEVEL%
goto :nila_after

:nila_recovery
rem The code is typed here and PIPED in, never put on the command line: an
rem argument is visible to Task Manager, ps and any log that records argv.
set /p NILA_CODE=Type ONE recovery code: 
echo %NILA_CODE%| "%NILA_PY%" brain.py dashboard --recovery-stdin
set RC=%ERRORLEVEL%

:nila_after
echo.
if not "%RC%"=="0" (
  echo Nila stopped with code %RC%. The line just above says why.
  echo Run report.bat and send the text if you cannot read the reason.
) else (
  echo Nila stopped. Your vault is locked.
)
pause
