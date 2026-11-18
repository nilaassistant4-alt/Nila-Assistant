@echo off
rem Dated backup to another drive, then a real restore check. Never deletes anything.
setlocal
cd /d "%~dp0"
call "%~dp0nila-env.bat"
set DEST=%~1
if "%DEST%"=="" set DEST=D:\Nila-backups
echo.
echo Backing up to: %DEST%
echo A backup that has not been restored is only a guess, so this restores it
echo into a temporary folder and counts the records.
echo.
python "%~dp0tools\backup_check.py" --to "%DEST%" --yes
if errorlevel 1 (echo. & echo BACKUP FAILED. Read the message above. Nothing was deleted. & pause & exit /b 1)
echo.
echo Old backups in that folder (yours to remove):
python "%~dp0tools\backup_check.py" --to "%DEST%" --list
pause
