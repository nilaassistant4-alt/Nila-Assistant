@echo off
rem Shared settings for every Nila script. Your data lives OUTSIDE this folder,
rem so replacing or updating the program folder never touches your vault.
if not defined BRAIN_HOME set "BRAIN_HOME=%LOCALAPPDATA%\Nila\vault"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
