@echo off
REM ======================================================================
REM  J-Dawg Bot — 24/7 supervisor.
REM  Auto-restarts the bot if it crashes. Logs everything to logs\bot.log.
REM  Close the window or press Ctrl+C twice to stop.
REM ======================================================================
cd /d %~dp0

if not exist logs mkdir logs

REM Bootstrap venv on first run
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

if not exist .env (
    echo.
    echo ERROR: .env file not found.
    echo Copy .env.example to .env and fill in TELEGRAM_TOKEN / TELEGRAM_CHAT_ID.
    echo.
    pause
    exit /b 1
)

set RESTARTS=0

:loop
echo.
echo ============================================
echo  J-Dawg Bot starting  (restart #%RESTARTS%)
echo  %DATE% %TIME%
echo ============================================
echo.

REM Tee output to log file AND console via PowerShell
powershell -NoProfile -Command "python -u main.py 2>&1 | Tee-Object -FilePath logs\bot.log -Append"

set EXITCODE=%ERRORLEVEL%
echo.
echo [supervisor] bot exited with code %EXITCODE% at %DATE% %TIME% — restarting in 10s...
echo [supervisor] bot exited with code %EXITCODE% at %DATE% %TIME% >> logs\bot.log
set /a RESTARTS=%RESTARTS%+1
timeout /t 10 /nobreak >nul
goto loop
