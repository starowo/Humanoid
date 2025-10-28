@echo off
chcp 65001 > nul
title Humanoid Bot - 更新依赖
color 0B

echo ========================================
echo   Humanoid Bot - 更新依赖脚本
echo ========================================
echo.

if not exist "venv" (
    echo [错误] 虚拟环境不存在！
    echo 请先运行 install.bat 进行安装
    pause
    exit /b 1
)

echo [*] 激活虚拟环境...
call venv\Scripts\activate.bat

echo.
echo [*] 升级 pip...
python -m pip install --upgrade pip

echo.
echo [*] 更新依赖包...
pip install --upgrade -r requirements.txt

if errorlevel 1 (
    echo [错误] 更新失败！
    pause
    exit /b 1
)

echo.
echo ========================================
echo   更新完成！
echo ========================================

deactivate
pause

