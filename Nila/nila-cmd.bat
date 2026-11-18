@echo off
rem Opens a command window with Nila's settings loaded, for commands like:
rem   python brain.py config set agent.online_daily_requests 200
rem   ollama pull llama3.2:3b
cd /d "%~dp0"
call "%~dp0nila-env.bat"
echo Nila command window. Your data folder: %BRAIN_HOME%
cmd /k
