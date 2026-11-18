@echo off
rem Dated backup to another drive, then a real restore check. Never deletes anything.
rem Usage: double-click for D:\Nila-backups, or drag a folder? No - pass it:
rem   backup-to-d.bat "E:\Some folder"
setlocal
chcp 65001 >nul 2>&1
cd /d "%~dp0"
call "%~dp0nila-env.bat"
call "%~dp0nila-common-check.bat"
if errorlevel 1 (
  pause
  exit /b 1
)
set "DEST=%~1"
if "%DEST%"=="" set "DEST=D:\Nila-backups"
echo.
echo Backing up to: %DEST%
echo A backup that has not been restored is only a guess, so this restores it
echo into a temporary folder and counts the records.
echo.
"%NILA_PY%" "%~dp0tools\backup_check.py" --to "%DEST%" --yes
if errorlevel 1 (
  echo.
  echo BACKUP FAILED. Read the message above. Nothing was deleted.
  pause
  exit /b 1
)
echo.
echo Old backups in that folder (yours to remove):
"%NILA_PY%" "%~dp0tools\backup_check.py" --to "%DEST%" --list
echo.
pause
