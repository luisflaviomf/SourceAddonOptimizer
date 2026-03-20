@echo off
setlocal
cd /d "%~dp0"

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0pyinstaller\build.ps1"
set "BUILD_RC=%ERRORLEVEL%"

if not "%BUILD_RC%"=="0" (
    echo.
    echo Build failed with exit code %BUILD_RC%.
    exit /b %BUILD_RC%
)

echo.
echo Build completed successfully.
echo Output: dist\GModAddonOptimizer\
exit /b 0
