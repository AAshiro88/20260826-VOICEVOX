@echo off
REM ============================================================
REM  Build AI Voice Chat UI into a standalone .exe (PyInstaller)
REM
REM  IMPORTANT:
REM  - Config files (.env) and user data (chats\) are NOT bundled
REM    into the exe. Keep them NEXT TO the exe at runtime:
REM        dist\AI_VoiceChat_UI.exe
REM        dist\.env              <-- copy manually, never committed
REM        dist\chats\            <-- created automatically
REM  - The app locates .env and chats\ in the exe folder when frozen,
REM    so nothing sensitive is packed inside the executable.
REM ============================================================

setlocal
set "SCRIPT_DIR=%~dp0"
set "APP_NAME=AI_voice_chat_ui"
set "DIST_NAME=AI_VoiceChat_UI"

set "PYEXE=python"
if exist "C:\ProgramData\Anaconda3\python.exe" set "PYEXE=C:\ProgramData\Anaconda3\python.exe"

echo [INFO] Using Python: %PYEXE%
"%PYEXE%" --version || goto :fail

"%PYEXE%" -m PyInstaller --version >nul 2>nul
if errorlevel 1 (
    echo [INFO] PyInstaller not found. Installing...
    "%PYEXE%" -m pip install --disable-pip-version-check pyinstaller || goto :fail
)

cd /d "%SCRIPT_DIR%"

echo [INFO] Cleaning previous build artifacts...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
if exist "%APP_NAME%.spec" del /q "%APP_NAME%.spec"

echo [INFO] Building standalone executable (this may take a few minutes)...
"%PYEXE%" -m PyInstaller ^
    --noconfirm ^
    --clean ^
    --onefile ^
    --windowed ^
    --name "%DIST_NAME%" ^
    "%APP_NAME%.py" || goto :fail

echo.
echo [OK ] Build finished: %SCRIPT_DIR%dist\%DIST_NAME%.exe
echo [NOTE] Config is NOT inside the exe. Before running, copy to dist\:
echo        - .env   (your OPENROUTER_API_KEY)
echo        VOICEVOX must be running on this machine when you use the exe.
pause
exit /b 0

:fail
echo.
echo [ERROR] Build failed. Read the messages above.
pause
exit /b 1
