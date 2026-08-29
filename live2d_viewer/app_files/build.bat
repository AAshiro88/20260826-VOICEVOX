@echo off
REM ============================================================
REM  Build the Live2D viewer frontend
REM  (web\viewer_main.js -> web\viewer.bundle.js, using esbuild)
REM  - Requires Node.js and npm.
REM  - First run installs esbuild under app_files (downloads it).
REM ============================================================

setlocal
set "SCRIPT_DIR=%~dp0"
set "ROOT=%SCRIPT_DIR%..\..\"
set "ENTRY=%ROOT%live2d_viewer\web\viewer_main.js"
set "OUT=%ROOT%live2d_viewer\web\viewer.bundle.js"

echo.
echo Building Live2D viewer frontend ...
echo   Input : %ENTRY%
echo   Output: %OUT%
echo.

REM Check Node / npm availability
where node >nul 2>nul
if errorlevel 1 (
    echo [ERROR] node not found. Please install Node.js first.
    goto :fail
)
where npm >nul 2>nul
if errorlevel 1 (
    echo [ERROR] npm not found. Please install Node.js, which bundles npm.
    goto :fail
)

pushd "%SCRIPT_DIR%"

REM Install esbuild on first use (from package.json)
if not exist "node_modules\esbuild" (
    echo [INFO] First run: installing esbuild ...
    call npm install
    if errorlevel 1 (
        popd
        echo [ERROR] Failed to install esbuild. Check your network or npm config.
        goto :fail
    )
)

echo [INFO] Building viewer.bundle.js ...
call npx esbuild "%ENTRY%" --bundle --outfile="%OUT%" --sourcemap --format=iife --log-level=info
if errorlevel 1 (
    popd
    echo [ERROR] Build failed. See the error messages above.
    goto :fail
)
popd

echo.
echo [OK] Build command finished.
if exist "%OUT%" (
    for %%F in ("%OUT%") do (
        echo [RESULT] File  : %%~fF
        echo [RESULT] Size  : %%~zF bytes
        echo [RESULT] Time  : %%~tF
        echo [RESULT] Status: SUCCESS
    )
) else (
    echo [RESULT] Status: FAILED - output file was not created.
)
echo.
pause
exit /b 0

:fail
echo.
echo [RESULT] Status: FAILED
pause
exit /b 1