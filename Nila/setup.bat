@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
call "%~dp0nila-env.bat"
echo.
echo ==============================================
echo   Nila setup   (one time, about 5 minutes)
echo ==============================================
echo.

python -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1
if errorlevel 1 (
  echo Python 3.10 or newer was not found.
  echo Install it from python.org, tick "Add python.exe to PATH", then run setup.bat again.
  pause
  exit /b 1
)

echo [1/4] Your private data will be stored here, outside this folder:
echo       %BRAIN_HOME%
echo.

echo [2/4] Installing the encryption helper (recommended)...
python -m pip install --user --quiet cryptography
if errorlevel 1 (
  echo       Could not install it. Nila will use its built-in fallback. You can retry later.
) else (
  echo       Done.
)
echo.

if exist "%BRAIN_HOME%\identity.json" (
  echo [3/4] A vault already exists here, so it was not recreated.
) else (
  echo [3/4] Creating your vault.
  echo       Choose a LONG passphrase. WRITE DOWN the recovery codes it shows and keep them
  echo       OFF this computer. If you lose the passphrase, your data cannot be recovered.
  echo.
  rem The authenticator is linked inside init now, so start-nila.bat works
  rem on the first run instead of refusing until a second command is typed.
  python brain.py init --label "%COMPUTERNAME%"
  if errorlevel 1 (
    echo.
    echo Setup stopped. Read the message above, fix it, and run setup.bat again.
    pause
    exit /b 1
  )
  echo.
  echo [4/4] Your authenticator key was shown above, once.
  echo       If you have not added it to Google Authenticator yet, run this now
  echo       and type the 6-digit code it asks for:
  echo           python brain.py totp
  echo       Nila will not open without that code, which is the point.
)

echo.
echo Setup finished. Start Nila with  start-nila.bat
echo Read START_HERE.md for Ollama (local AI) and Gemini (online AI).
pause
