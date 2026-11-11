@echo off
REM ============================================================
REM  Just One API Collector - Windows EXE Build Script
REM  Builds a distributable GUI application using PyInstaller.
REM ============================================================
setlocal EnableDelayedExpansion

set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"

echo ============================================================
echo  Just One API Collector - EXE Build
echo ============================================================
echo.

REM -- Step 1: Verify Python is available
echo [1/5] Checking Python environment...
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python is not installed or not in PATH.
    echo Please install Python 3.10+ from https://www.python.org/downloads/
    pause
    exit /b 1
)
for /f "tokens=*" %%v in ('python --version 2^>^&1') do set "PY_VER=%%v"
echo OK: %PY_VER%
echo.

REM -- Step 2: Install/upgrade dependencies
echo [2/5] Installing dependencies...
python -m pip install --upgrade pip >nul 2>&1
python -m pip install -r requirements.txt pyinstaller --upgrade
if errorlevel 1 (
    echo ERROR: Failed to install dependencies.
    pause
    exit /b 1
)
echo OK: Dependencies installed.
echo.

REM -- Step 3: Verify the package can be imported
echo [3/5] Verifying package import...
python -c "import gui_qt.app; print('OK: GUI module loaded successfully')"
if errorlevel 1 (
    echo ERROR: Package import failed. Check for syntax errors.
    pause
    exit /b 1
)
echo.

REM -- Step 4: Clean previous build artifacts
echo [4/5] Cleaning previous build artifacts...
if exist "build" rmdir /s /q "build"
if exist "dist" rmdir /s /q "dist"
if exist "JustOneAPI数据采集工具.spec.bak" del /q "JustOneAPI数据采集工具.spec.bak"
echo OK: Cleaned.
echo.

REM -- Step 5: Build with PyInstaller
echo [5/5] Building EXE with PyInstaller...
echo This may take several minutes. Please wait...
echo.
python -m PyInstaller justoneapi_collector.spec --noconfirm --log-level WARN
if errorlevel 1 (
    echo ERROR: Build failed. See output above for details.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo  BUILD SUCCESSFUL!
echo ============================================================
echo.
echo Output location:
echo   %SCRIPT_DIR%dist\流量捕手\
echo.
echo Executable:
echo   %SCRIPT_DIR%dist\流量捕手\流量捕手.exe
echo.
echo To distribute: zip the entire "JustOneAPI数据采集工具" folder.
echo End users only need to double-click the .exe file to run.
echo.
pause
