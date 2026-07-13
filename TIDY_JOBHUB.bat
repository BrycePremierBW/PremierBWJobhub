@echo off
setlocal
title Premier Brushworks JobHub Cleanup
cd /d "%~dp0"

echo.
echo Premier Brushworks JobHub Cleanup
echo ==================================
echo.
echo This creates a timestamped backup, tidies the interface,
echo and restores the original automatically if compile checking fails.
echo.

where py >nul 2>nul
if %errorlevel%==0 (
    py tidy_jobhub.py pb_jobhub_app.py
) else (
    python tidy_jobhub.py pb_jobhub_app.py
)

echo.
pause
