@echo off
setlocal
chcp 65001 >nul 2>&1
cd /d "%~dp0"
call "%~dp0nila-env.bat"
echo.
echo ==============================================
echo   Nila setup   (one time, about 5 minutes)
echo ==============================================
echo.
call "%~dp0nila-common-check.bat"
if errorlevel 1 (
  pause
  exit /b 1
)
echo [1/4] Your private data will be stored here, outside this folder:
echo       %BRAIN_HOME%
echo.
echo [2/4] Installing the encryption helper (recommended)...
"%NILA_PY%" -m pip install --user --quiet cryptography
if errorlevel 1 (
  echo       Could not install it. Nila will use its built-in fallback. You can retry later.
) else (
  echo       Done.
)
echo.
if exist "%BRAIN_HOME%\identity.json" (
  echo [3/4] A vault already exists here, so it was not recreated.
  echo [4/4] Nothing else to do.
) else (
  echo [3/4] Creating your vault.
  echo       Choose a LONG passphrase and write it on paper. The recovery codes it shows
  echo       matter too - keep them off this computer: ONE of them opens the vault on its
  echo       own if you forget the passphrase (run: start-nila.bat recovery).
  echo.
  rem The authenticator is linked inside init now, so start-nila.bat works
  rem on the first run instead of refusing until a second command is typed.
  "%NILA_PY%" brain.py init --label "%COMPUTERNAME%"
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
  echo           "%NILA_PY%" brain.py totp
  echo       Nila will not open without that code, which is the point.
)
echo.
echo Setup finished.
echo   start-nila.bat   start Nila (the dashboard opens in your browser)
echo   nila-phone.bat   answer Telegram messages from your phone
echo   report.bat       what is wrong, if something is wrong
echo   START_HERE.md    Ollama (local AI) and Gemini (online AI), in order
pause
