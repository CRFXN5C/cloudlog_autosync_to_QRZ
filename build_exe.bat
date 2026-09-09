@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
title QRZ CloudLog Sync - Build EXE (bundled Chromium)

echo ============================================================
echo   QRZ CloudLog 同步器 - 一键打包 EXE（内置 Chromium）
echo ============================================================

cd /d "%~dp0"
set "VENVPY=%cd%\.venv\Scripts\python.exe"

REM ---- 0. 可选：递增版本号 ----
set "BUMPSET="
if /i "%~1"=="bump"    set "BUMPSET=patch"
if /i "%~1"=="patch"   set "BUMPSET=patch"
if /i "%~1"=="minor"   set "BUMPSET=minor"
if /i "%~1"=="major"   set "BUMPSET=major"

REM ---- 1. 确保 Python 可用 ----
set "PY=python"
where python >nul 2>nul
if %errorlevel% neq 0 (
    echo [INFO] 未检测到 Python，尝试用 winget 安装 Python 3.12（仅当前用户）...
    winget install -e --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements
    if !errorlevel! neq 0 (
        echo [ERROR] Python 安装失败。请手动安装 Python 3.10+ 后重新运行本脚本。
        echo        下载地址: https://www.python.org/downloads/
        pause
        exit /b 1
    )
    set "PY=py -3"
)

REM ---- 2. 创建虚拟环境并安装依赖 + Playwright 浏览器 ----
echo [INFO] Python 版本:
%PY% --version
echo [INFO] 创建虚拟环境 .venv ...
if not exist ".venv" (
    %PY% -m venv .venv
)
echo [INFO] 激活虚拟环境并安装依赖 ...
call ".venv\Scripts\activate.bat"
python -m pip install --upgrade pip >nul
python -m pip install -r requirements.txt
python -m pip install pyinstaller
echo [INFO] 下载 Playwright 自带的 Chromium（首次约 150MB，可能较慢）...
python -m playwright install chromium

REM ---- 3. 处理版本号 ----
if not defined BUMPSET (
    echo [INFO] 使用当前版本号，不递增。
) else (
    echo [INFO] 递增版本（%BUMPSET%）...
    for /f %%V in ('"%VENVPY%" -c "from app.version import bump; print(bump('%BUMPSET%'))"') do set "NEWVER=%%V"
    echo [INFO] 新版本号: !NEWVER!
)
for /f %%V in ('"%VENVPY%" -c "from app.version import VERSION; print(VERSION)"') do set "VER=%%V"
if not defined VER (
    echo [ERROR] 无法读取版本号。
    pause
    exit /b 1
)
echo [INFO] 打包版本: v%VER%

REM ---- 4. 定位 Playwright 的 Chromium 目录 ----
echo [INFO] 定位内置 Chromium ...
for /f %%D in ('"%VENVPY%" -c "import os,glob; base=os.path.join(os.environ['LOCALAPPDATA'],'ms-playwright'); ds=sorted(glob.glob(os.path.join(base,'chromium-*'))); print(ds[0] if ds else '')"') do set "CHROMIUM_DIR=%%D"
if not defined CHROMIUM_DIR (
    echo [ERROR] 未找到 Playwright Chromium，请先运行: python -m playwright install chromium
    pause
    exit /b 1
)
echo [INFO] Chromium 目录: !CHROMIUM_DIR!

REM ---- 5. 打包（含 Chromium，体积较大）----
echo [INFO] 开始打包（单文件，内置浏览器，请耐心等待）...
python -m PyInstaller --noconfirm --clean --onefile --windowed ^
    --name "QRZCloudlogSync_v%VER%" ^
    --icon "assets\qrz.ico" ^
    --collect-all playwright ^
    --collect-all urllib3 ^
    --add-data "app;app" ^
    --add-data "assets;assets" ^
    --add-data "!CHROMIUM_DIR!;chromium" ^
    main.py

if !errorlevel! neq 0 (
    echo [ERROR] 打包失败，请检查上方日志。
    pause
    exit /b 1
)

echo QRZCloudlogSync_v%VER% > "dist\VERSION.txt"
echo build: v%VER% | bundled Chromium | Playwright | QRZ icon > "dist\BUILD_INFO.txt"

echo.
echo ============================================================
echo   打包完成！版本 v%VER%（含内置 Chromium，体型较大）
echo   EXE: %cd%\dist\QRZCloudlogSync_v%VER%.exe
echo ============================================================
echo 用法:
echo    build_exe.bat        使用当前版本打包
echo    build_exe.bat minor  递增次版本并打包
echo    build_exe.bat major  递增主版本并打包
pause
