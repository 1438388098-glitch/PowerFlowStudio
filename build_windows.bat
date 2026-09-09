@echo off
REM ============================================================
REM PowerFlowStudio Windows build script
REM Double-click this file in the project root. Outputs:
REM   dist\PowerFlowStudio.exe
REM ============================================================

REM Switch console to UTF-8 so any non-ASCII chars render correctly
chcp 65001 > nul

echo === PowerFlowStudio Windows build script ===
echo.

REM --- Check Python ---
where python >nul 2>&1
if errorlevel 1 (
    echo [ERROR] python not found in PATH.
    echo Install Python 3.10+ and ensure it is on PATH:
    echo   https://www.python.org/downloads/
    pause
    exit /b 1
)

echo [1/5] Python version:
python --version
echo.

REM --- Probe whether venv already exists ---
set HAS_VENV=0
if exist ".venv\Scripts\python.exe" set HAS_VENV=1

if "%HAS_VENV%"=="0" (
    echo [2/5] Creating virtual environment .venv ...
    python -m venv .venv
    if errorlevel 1 (
        echo [ERROR] Failed to create virtual environment.
        pause
        exit /b 1
    )
) else (
    echo [2/5] Virtual environment already exists, skipping creation.
)
echo.

REM --- Activate venv and install deps ---
echo [3/5] Installing dependencies: PyQt5 pyqtgraph pandapower numpy pyinstaller ...
call .venv\Scripts\activate.bat
if errorlevel 1 (
    echo [ERROR] Failed to activate virtual environment.
    pause
    exit /b 1
)
python -m pip install --upgrade pip --quiet
python -m pip install PyQt5 pyqtgraph pandapower numpy pyinstaller --quiet
if errorlevel 1 (
    echo [ERROR] Failed to install dependencies.
    pause
    exit /b 1
)
echo.

REM --- Sanity check the imports before packaging ---
echo [4/5] Sanity-checking imports ...
python -c "import PyQt5, pyqtgraph, pandapower, numpy; print('  PyQt5', PyQt5.QtCore.PYQT_VERSION_STR); print('  pyqtgraph', pyqtgraph.__version__); print('  pandapower', pandapower.__version__); print('  numpy', numpy.__version__)"
if errorlevel 1 (
    echo [ERROR] Imports failed.
    pause
    exit /b 1
)
echo.

REM --- Run pyinstaller ---
echo [5/5] Running pyinstaller (this takes 1-3 minutes on first run) ...
pyinstaller --onefile --windowed --name PowerFlowStudio --noconfirm app.py
if errorlevel 1 (
    echo [ERROR] pyinstaller failed.
    pause
    exit /b 1
)

echo.
echo === Build complete ===
echo Output: dist\PowerFlowStudio.exe
echo.
echo Double-click it to run, or drag to desktop to make a shortcut.
pause
endlocal
