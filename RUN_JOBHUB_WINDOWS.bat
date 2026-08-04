@echo off
title Premier Brushworks JobHub v5
cd /d "%~dp0"
echo.
echo Premier Brushworks JobHub - Complete Replacement v5
echo =====================================================
echo.
py -m pip install -r requirements.txt
if %ERRORLEVEL% NEQ 0 python -m pip install -r requirements.txt
echo.
echo Starting JobHub...
py -m streamlit run pb_jobhub_app.py
if %ERRORLEVEL% NEQ 0 python -m streamlit run pb_jobhub_app.py
PAUSE
