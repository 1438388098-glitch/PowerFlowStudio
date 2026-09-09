@echo off
REM ============================================================
REM PowerFlowStudio Windows 一键打包脚本
REM 在项目根目录双击运行即可, 产出 dist\PowerFlowStudio.exe
REM ============================================================

setlocal

echo === PowerFlowStudio Windows 打包脚本 ===
echo.

REM 检查 Python
where python >nul 2>&1
if errorlevel 1 (
    echo [错误] 未找到 python, 请先安装 Python 3.10+ 并加入 PATH
    echo 下载: https://www.python.org/downloads/
    pause
    exit /b 1
)

echo [1/4] Python 版本:
python --version
echo.

REM 探测 venv 是否已存在
set HAS_VENV=0
if exist ".venv\Scripts\python.exe" set HAS_VENV=1

if "%HAS_VENV%"=="0" (
    echo [2/4] 创建虚拟环境 .venv ...
    python -m venv .venv
    if errorlevel 1 (
        echo [错误] 虚拟环境创建失败
        pause
        exit /b 1
    )
) else (
    echo [2/4] 虚拟环境已存在, 跳过创建
)
echo.

REM 激活虚拟环境 + 装依赖
echo [3/4] 安装依赖 ^(PyQt5 pyqtgraph pandapower numpy pyinstaller^) ...
call .venv\Scripts\activate.bat
if errorlevel 1 (
    echo [错误] 虚拟环境激活失败
    pause
    exit /b 1
)
python -m pip install --upgrade pip --quiet
python -m pip install PyQt5 pyqtgraph pandapower numpy pyinstaller --quiet
if errorlevel 1 (
    echo [错误] 依赖安装失败
    pause
    exit /b 1
)
echo.

REM 打包
echo [4/4] 运行 pyinstaller ^(这一步会比较慢, 通常 1-3 分钟^) ...
pyinstaller --onefile --windowed --name PowerFlowStudio --noconfirm app.py
if errorlevel 1 (
    echo [错误] 打包失败
    pause
    exit /b 1
)

echo.
echo === 打包完成 ===
echo 产物: dist\PowerFlowStudio.exe
echo.
echo 双击运行, 或拖到桌面创建快捷方式.
pause
endlocal
