@echo off
REM Double-click this to start the bot. Leave the window open.
cd /d %~dp0

REM First-run setup: install deps into a local venv
if not exist .venv (
    echo [setup] creating virtual environment...
    python -m venv .venv
    call .venv\Scripts\activate.bat
    echo [setup] installing dependencies...
    pip install --upgrade pip
    pip install -r requirements.txt
) else (
    call .venv\Scripts\activate.bat
)

REM Check .env exists
if not exist .env (
    echo.
    echo ERROR: .env file not found.
    echo Copy .env.example to .env and fill in your Telegram token and chat ID.
    echo.
    pause
    exit /b 1
)

echo.
echo ============================================
echo  J-Dawg Bot starting. Press Ctrl+C to stop.
echo ============================================
echo.

python main.py
pause
