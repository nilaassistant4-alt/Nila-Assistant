@echo off
rem Shared settings for EVERY Nila script. Your data lives OUTSIDE this folder,
rem so replacing or updating the program folder never touches your vault.
rem
rem Why this file exists: if each .bat set its own paths, one of them would
rem silently point at a different folder and the owner would see "no vault"
rem from a shortcut while the window shows a vault. One place, called by all.
if not defined BRAIN_HOME set "BRAIN_HOME=%LOCALAPPDATA%\Nila\vault"
rem UTF-8 everywhere: Tamil text in the console, and files written by Python.
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
rem The console code page is per-window, so every script sets it too; doing it
rem here as well means a plain `nila-cmd.bat` window does not print mojibake.
chcp 65001 >nul 2>&1
rem A single place that answers "which Python will run?" so a .bat that cannot
rem find Python says so in one sentence instead of failing with 'not recognized'.
set "NILA_PY=python"
where py >nul 2>&1 && set "NILA_PY=py -3"
