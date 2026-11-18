@echo off
rem Called by the scripts that cannot do anything useful without Python 3.10+.
rem Sets ERRORLEVEL 1 with one readable sentence when it is missing, so the
rem owner is never left guessing whether the .bat or Nila is broken.
"%NILA_PY%" -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1
if errorlevel 1 (
  echo.
  echo Python 3.10 or newer was not found on this PC.
  echo Install it from https://www.python.org/downloads/ and TICK
  echo "Add python.exe to PATH" on the first screen, then run this file again.
  echo Already installed? Open setup.bat once - it explains what is missing.
  exit /b 1
)
exit /b 0
