@echo off
REM ============================================================
REM  Launch AI Voice Chat UI (VOICEVOX x Ollama / OpenRouter)
REM  - Make sure VOICEVOX is running first.
REM  - For OpenRouter, put your key in .env next to this script.
REM ============================================================

setlocal
set "SCRIPT_DIR=%~dp0"
set "APP=%SCRIPT_DIR%AI_voice_chat_ui.py"

set "PYEXE=python"
if exist "C:\ProgramData\Anaconda3\python.exe" set "PYEXE=C:\ProgramData\Anaconda3\python.exe"

if not exist "%APP%" (
    echo [ERROR] App not found: "%APP%"
    pause
    exit /b 1
)

if not exist "%SCRIPT_DIR%.env" (
    echo [WARN] .env not found. OpenRouter will be unavailable until you create it.
)

echo [INFO] Starting AI Voice Chat UI ...
echo [INFO] Python: %PYEXE%
"%PYEXE%" "%APP%"
if errorlevel 1 (
    echo.
    echo [ERROR] App exited with an error. Check the messages above.
    pause
)
endlocal
