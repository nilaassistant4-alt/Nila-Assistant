@echo off
rem ==========================================================================
rem  Nila - "I cannot open it, start me over" - safely, from one double-click.
rem
rem  This is the owner's own recovery step, done for him:
rem    "rename the vault folder to vault_old and run setup again".
rem
rem  The trap in that advice is the word RENAME: it leaves exactly one copy, and
rem  if anything goes wrong during the new setup there is no second chance. So
rem  this file does not rename and does not delete. It runs `brain new-vault`,
rem  which
rem    1. COPIES the whole old vault to a folder beside it,
rem    2. compares every file (size + SHA-256) and stops if they differ,
rem    3. only then makes the NEW vault, with a passphrase YOU type now,
rem    4. prints NEW recovery codes and a NEW authenticator secret, once.
rem
rem  The old vault stays where it is, still opening with its own passphrase.
rem  Nothing is removed - ever - by this file.
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
echo   Nila - start over with a new vault
echo ==============================================
echo.
echo Your vault folder: %BRAIN_HOME%
if exist "%BRAIN_HOME%\identity.json" (
  echo   it exists, and it stays: a COPY is made beside it first.
) else (
  echo   there is no vault there yet - this simply creates the first one.
)
echo.
echo Use this only when the passphrase is lost AND no recovery code works.
echo If you still have one recovery code, close this and run nila-start.bat
echo instead - it opens the vault without losing anything.
echo.
echo Nothing is deleted. On paper you will need: the NEW passphrase, the NEW
echo recovery codes, and the authenticator secret - all shown once.
echo.
set NILA_GO=
set /p NILA_GO=Type START to begin, or press Enter to stop: 
if /i not "%NILA_GO%"=="START" (
  echo Nothing was chosen, so nothing was changed.
  pause
  exit /b 1
)
echo.
"%NILA_PY%" brain.py new-vault
if errorlevel 1 (
  echo.
  echo The new vault was not created. The old one is untouched and still used.
  echo Read the line above, then run report.bat and send me the text.
  pause
  exit /b 1
)
echo.
echo ==============================================
echo   New vault ready.
echo   Next: double-click nila-start.bat
echo   Your OLD vault is still in the folder printed above (vault-old-...).
echo ==============================================
pause
