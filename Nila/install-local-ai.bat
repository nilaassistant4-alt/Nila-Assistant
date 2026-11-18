@echo off
rem Installs a local AI (Ollama + a small model) so Nila can answer as a language model.
rem Nila already works without it - she just answers from her own rules and says so.
setlocal
chcp 65001 >nul 2>&1
cd /d "%~dp0"
call "%~dp0nila-env.bat"
call "%~dp0nila-common-check.bat"
if errorlevel 1 (
  pause
  exit /b 1
)
echo.
echo Local AI setup. This checks what is already on this PC before downloading
echo anything, and it is safe to run twice.
echo.
"%NILA_PY%" "%~dp0tools\install_local_ai.py" --install
echo.
echo What is present now:
"%NILA_PY%" "%~dp0tools\install_local_ai.py" --check
echo.
pause
