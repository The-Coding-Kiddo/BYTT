@echo off
:: ============================================================
:: BYTT Sonar Viewer -- Windows Build Script
:: Run this from the project root (where bytt.spec lives).
:: Requirements: Python 3.11+, pip, internet access (first run).
:: ============================================================

setlocal enabledelayedexpansion

echo.
echo  ===================================================
echo   BYTT Sonar Viewer -- Windows Executable Builder
echo  ===================================================
echo.

:: ── 1. Check Python ──────────────────────────────────────────
where python >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found. Install Python 3.11+ from https://python.org
    echo         and make sure "Add Python to PATH" is checked during install.
    pause
    exit /b 1
)

for /f "tokens=2 delims= " %%v in ('python --version 2^>^&1') do set PYVER=%%v
echo [OK] Python %PYVER% found.

:: ── 2. Create / activate a venv ──────────────────────────────
if not exist ".venv" (
    echo [..] Creating virtual environment .venv ...
    python -m venv .venv
    if errorlevel 1 (
        echo [ERROR] Failed to create virtual environment.
        pause
        exit /b 1
    )
    echo [OK] Virtual environment created.
) else (
    echo [OK] Virtual environment .venv already exists.
)

call .venv\Scripts\activate.bat

:: ── 3. Install / upgrade dependencies ────────────────────────
echo [..] Installing / upgrading pip ...
python -m pip install --upgrade pip --quiet

echo [..] Installing project dependencies ...
pip install "PySide6>=6.6" "pyqtgraph>=0.13" "numpy>=1.26" "opencv-python>=4.9" --quiet
if errorlevel 1 (
    echo [ERROR] Failed to install project dependencies.
    pause
    exit /b 1
)

echo [..] Installing PyInstaller ...
pip install pyinstaller --quiet
if errorlevel 1 (
    echo [ERROR] Failed to install PyInstaller.
    pause
    exit /b 1
)
echo [OK] All dependencies installed.

:: ── 4. Install the bytt package itself (editable) ────────────
echo [..] Installing bytt package ...
pip install -e . --quiet
if errorlevel 1 (
    echo [ERROR] Failed to install bytt package.
    pause
    exit /b 1
)

:: ── 5. Clean previous build artefacts ────────────────────────
echo [..] Cleaning previous build outputs ...
if exist "build" rmdir /s /q "build"
if exist "dist"  rmdir /s /q "dist"

:: ── 6. Run PyInstaller ───────────────────────────────────────
echo [..] Building executable with PyInstaller ...
echo.
pyinstaller bytt.spec --noconfirm
if errorlevel 1 (
    echo.
    echo [ERROR] PyInstaller build failed. Check the output above for details.
    pause
    exit /b 1
)

:: ── 7. Report result ─────────────────────────────────────────
echo.
if exist "dist\BYTT_Sonar_Viewer\BYTT_Sonar_Viewer.exe" (
    echo  ===================================================
    echo   BUILD SUCCESSFUL
    echo  ===================================================
    echo.
    echo   Executable:
    echo     dist\BYTT_Sonar_Viewer\BYTT_Sonar_Viewer.exe
    echo.
    echo   To distribute to another Windows machine, zip and
    echo   send the entire  dist\BYTT_Sonar_Viewer\  folder.
    echo   The recipient double-clicks BYTT_Sonar_Viewer.exe
    echo   -- no Python installation required on their end.
    echo.
) else (
    echo [WARN] Build completed but exe not found in expected location.
    echo        Check dist\ folder manually.
)

pause
