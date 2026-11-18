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
echo This gets ffmpeg, which finds the cut points and the silences in a video.
echo It downloads about 150 MB from github.com, unpacks two files into tools^,
echo and installs nothing else. Your memory and your files are not touched.
echo.
"%NILA_PY%" tools\getffmpeg.py
set RC=%ERRORLEVEL%
echo.
if not "%RC%"=="0" (
  echo ffmpeg was not installed. The line above says why.
  echo Nila still finds scene cuts from the video's own index without it.
) else (
  echo Done. Now `brain media scenes your-video.mp4` works fully.
)
pause
