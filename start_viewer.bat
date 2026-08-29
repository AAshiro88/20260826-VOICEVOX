@echo off
REM ============================================================
REM  Start the Live2D 3D viewer server (opens the browser)
REM  - Run this alongside the AI voice chat app so the 3D
REM    character can be controlled by the AI.
REM ============================================================

setlocal
set "SCRIPT_DIR=%~dp0"
set "SERVER=%SCRIPT_DIR%live2d_viewer\server.py"

set "PYEXE=python"
if exist "C:\ProgramData\Anaconda3\python.exe" set "PYEXE=C:\ProgramData\Anaconda3\python.exe"

if not exist "%SERVER%" (
    echo [ERROR] Server script not found: %SERVER%
    pause
    exit /b 1
)

if not exist "%SCRIPT_DIR%live2d_viewer\web\viewer.bundle.js" (
    echo [WARN] viewer.bundle.js is missing.
    echo        Run live2d_viewer\app_files\build.bat first.
)

echo [INFO] Starting the Live2D viewer server ...
echo [INFO] Python: %PYEXE%
echo [INFO] Browser will open at http://127.0.0.1:8767/
echo [INFO] Press Ctrl+C to stop the server.
echo.
"%PYEXE%" "%SERVER%"
if errorlevel 1 (
    echo.
    echo [ERROR] The server exited with an error. See the messages above.
    pause
)
endlocal