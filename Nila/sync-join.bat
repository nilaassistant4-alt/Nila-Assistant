@echo off
setlocal
chcp 65001 >nul 2>&1
cd /d "%~dp0"
call "%~dp0nila-env.bat"
call "%~dp0nila-common-check.bat"
if errorlevel 1 (
  pause
  exit /b 1
)
echo Joining this PC to the other one.
echo You need two things from the OTHER PC's window ^(sync-host.bat^):
echo   the address, for example 192.168.1.5
echo   the code, six groups of four like 6DU8-8Y88-6NHT-CUKX-283K-4WYA
echo.
set /p ADDR=Address of the other PC: 
if "%ADDR%"=="" (
  echo No address typed, so nothing was changed.
  pause
  exit /b 1
)
set /p CODE=Join code: 
if "%CODE%"=="" (
  echo No code typed, so nothing was changed.
  pause
  exit /b 1
)
echo.
"%NILA_PY%" tools\sync_lan.py join %ADDR% --code %CODE%
set RC=%ERRORLEVEL%
echo.
if not "%RC%"=="0" (
  echo Could not join. The line above says why.
) else (
  echo Now run sync-now.bat to bring the other PC's memory into this one.
)
pause
