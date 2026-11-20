@echo off
REM Double-click to start the finintel dashboard (http://127.0.0.1:8800).
REM Keep this window open while you use it; close it to stop the dashboard.
title finintel dashboard
cd /d "%~dp0"
where uv >nul 2>nul || set "PATH=%USERPROFILE%\.local\bin;%PATH%"
uv run python -m fin app
echo.
echo Dashboard stopped. Press any key to close this window.
pause >nul
