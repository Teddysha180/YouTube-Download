@echo off
echo ========================================
echo YouTube Downloader Bot - Local Test
echo ========================================
echo.

REM Check if Python is installed
python --version >nul 2>&1
if errorlevel 1 (
    echo Python is not installed! Please install Python first.
    pause
    exit /b
)

REM Install requirements
echo Installing requirements...
pip install -r requirements.txt

REM Run the bot
echo.
echo Starting bot...
python bot.py

pause
