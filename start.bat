@echo off
chcp 65001 > nul
title Humanoid Bot - 启动器
color 0B

echo ========================================
echo   Humanoid Bot - 一键启动脚本
echo ========================================
echo.

REM 检查 Python 是否安装
python --version > nul 2>&1
if errorlevel 1 (
    echo [错误] 未检测到 Python！
    echo 请先安装 Python 3.8 或更高版本
    echo 下载地址: https://www.python.org/downloads/
    pause
    exit /b 1
)

echo [1/5] 检查虚拟环境...
if not exist "venv" (
    echo [*] 虚拟环境不存在，正在创建...
    python -m venv venv
    if errorlevel 1 (
        echo [错误] 创建虚拟环境失败！
        pause
        exit /b 1
    )
    echo [✓] 虚拟环境创建成功
) else (
    echo [✓] 虚拟环境已存在
)

echo.
echo [2/5] 激活虚拟环境...
call venv\Scripts\activate.bat
if errorlevel 1 (
    echo [错误] 激活虚拟环境失败！
    pause
    exit /b 1
)
echo [✓] 虚拟环境已激活

echo.
echo [3/5] 检查依赖包...
python -c "import discord" > nul 2>&1
if errorlevel 1 (
    echo [*] 依赖包未安装，正在安装...
    python -m pip install --upgrade pip
    pip install -r requirements.txt
    if errorlevel 1 (
        echo [错误] 依赖包安装失败！
        pause
        exit /b 1
    )
    echo [✓] 依赖包安装成功
) else (
    echo [✓] 依赖包已安装
)

echo.
echo [4/5] 检查配置文件...
if not exist "config\config.yaml" (
    echo [警告] 配置文件不存在！
    if exist "config\config.example.yaml" (
        echo [*] 正在从模板创建配置文件...
        copy config\config.example.yaml config\config.yaml > nul
        echo [✓] 配置文件已创建
        echo.
        echo ========================================
        echo   首次运行提示
        echo ========================================
        echo 请先编辑 config\config.yaml 文件
        echo 填写你的 Bot Token 和其他配置
        echo.
        echo 编辑完成后，再次运行此脚本启动 Bot
        echo ========================================
        pause
        exit /b 0
    ) else (
        echo [错误] 找不到配置模板文件！
        pause
        exit /b 1
    )
) else (
    echo [✓] 配置文件已存在
)

echo.
echo [5/5] 启动 Bot...
echo.
echo ========================================
echo   Bot 正在启动...
echo   按 Ctrl+C 停止 Bot
echo ========================================
echo.

python bot.py

if errorlevel 1 (
    echo.
    echo ========================================
    echo   Bot 运行出错
    echo ========================================
    pause
)

deactivate

