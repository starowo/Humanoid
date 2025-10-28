@echo off
chcp 65001 > nul
title Humanoid Bot - 安装脚本
color 0B

echo ========================================
echo   Humanoid Bot - 安装脚本
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

echo [*] 检测到 Python 版本:
python --version
echo.

echo [1/4] 创建虚拟环境...
if exist "venv" (
    echo [*] 虚拟环境已存在，跳过创建
) else (
    python -m venv venv
    if errorlevel 1 (
        echo [错误] 创建虚拟环境失败！
        pause
        exit /b 1
    )
    echo [✓] 虚拟环境创建成功
)

echo.
echo [2/4] 激活虚拟环境...
call venv\Scripts\activate.bat
echo [✓] 虚拟环境已激活

echo.
echo [3/4] 安装依赖包...
echo [*] 升级 pip...
python -m pip install --upgrade pip

echo [*] 安装项目依赖...
pip install -r requirements.txt
if errorlevel 1 (
    echo [错误] 依赖包安装失败！
    pause
    exit /b 1
)
echo [✓] 依赖包安装成功

echo.
echo [4/4] 创建配置文件...
if exist "config\config.yaml" (
    echo [*] 配置文件已存在，跳过创建
) else (
    if exist "config\config.example.yaml" (
        copy config\config.example.yaml config\config.yaml > nul
        echo [✓] 配置文件已创建
    ) else (
        echo [警告] 找不到配置模板文件
    )
)

echo.
echo ========================================
echo   安装完成！
echo ========================================
echo.
echo 接下来的步骤:
echo 1. 编辑 config\config.yaml 文件
echo 2. 填写你的 Bot Token 和配置
echo 3. 运行 start.bat 启动 Bot
echo.
echo ========================================

deactivate
pause

