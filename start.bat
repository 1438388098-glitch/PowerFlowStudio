@echo off
setlocal
cd /d "%~dp0"
title PowerFlowStudio Launcher

rem ------------------------------------------------------------
rem PowerFlowStudio one-click launcher (double-click to run)
rem   1. first run  : create .venv automatically (Python 3.10+ needed in PATH)
rem   2. dependency : auto install / repair from Tsinghua PyPI mirror
rem   3. start      : launch GUI without keeping a console window open
rem   crash log     : %TEMP%\PowerFlowStudio.log
rem ------------------------------------------------------------

if not exist ".venv\Scripts\python.exe" (
    echo [PowerFlowStudio] First run detected. Creating virtual environment ...
    where python >nul 2>nul
    if errorlevel 1 (
        echo.
        echo [ERROR] Python not found in PATH.
        echo         Please install Python 3.10 or newer from python.org,
        echo         tick "Add Python to PATH" during setup, then rerun this file.
        echo.
        pause
        exit /b 1
    )
    python -m venv .venv
    if errorlevel 1 (
        echo [ERROR] Failed to create virtual environment .venv
        pause
        exit /b 1
    )
)

.venv\Scripts\python.exe -c "import pandapower, PyQt5" >nul 2>nul
if errorlevel 1 (
    echo [PowerFlowStudio] Installing dependencies via Tsinghua mirror, please wait ...
    .venv\Scripts\python.exe -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
    if errorlevel 1 (
        echo.
        echo [ERROR] Dependency installation failed. Check your network, or run manually:
        echo         .venv\Scripts\pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
        echo.
        pause
        exit /b 1
    )
)

echo [PowerFlowStudio] Starting GUI ...
start "PowerFlowStudio" ".venv\Scripts\pythonw.exe" app.py
endlocal
