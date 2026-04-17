@echo off
REM ======================================================================
REM  Register the bot to start automatically when this user logs in.
REM  Uses Windows Task Scheduler (no admin required for per-user tasks).
REM
REM  Run once. To remove, run:  schtasks /delete /tn JDawgBot /f
REM ======================================================================
cd /d %~dp0
set TASK_NAME=JDawgBot
set SCRIPT_PATH=%~dp0run_forever.bat

echo Registering scheduled task "%TASK_NAME%" → %SCRIPT_PATH%
schtasks /create ^
    /tn "%TASK_NAME%" ^
    /tr "\"%SCRIPT_PATH%\"" ^
    /sc onlogon ^
    /rl limited ^
    /f

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo Failed to register the task. You may need to run this from an
    echo elevated command prompt or check Task Scheduler manually.
    pause
    exit /b 1
)

echo.
echo Done. The bot will start automatically next time you log in.
echo To start it now, double-click run_forever.bat.
echo To remove autostart later: schtasks /delete /tn %TASK_NAME% /f
echo.
pause
