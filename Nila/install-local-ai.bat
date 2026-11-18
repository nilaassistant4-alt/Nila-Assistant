@echo off
rem Installs a local AI (Ollama + a small model) so Nila can answer as a language model.
rem Nila already works without it - she just answers from her own rules and says so.
setlocal
cd /d "%~dp0"
call "%~dp0nila-env.bat"
echo.
echo Local AI setup. This checks what is already on this PC before downloading anything.
echo.
python "%~dp0tools\install_local_ai.py" --install
echo.
python "%~dp0tools\install_local_ai.py" --check
echo.
pause
