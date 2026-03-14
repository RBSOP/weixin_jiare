@echo off
chcp 65001 >nul
title 微信加热平台 - 采集数据

cd /d "%~dp0"

where python >nul 2>&1
if errorlevel 1 (
    echo 未找到 Python，请先安装 Python 3.10+
    pause
    exit /b 1
)

echo 正在启动...
python main.py

if errorlevel 1 (
    echo.
    echo 启动失败，请检查：
    echo 1. 是否已执行 pip install -r requirements.txt
    echo 2. 是否已执行 playwright install chromium
    echo.
    pause
)
