@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
call "%~dp0nila-env.bat"
python brain.py report
echo.
pause
