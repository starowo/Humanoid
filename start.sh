#!/bin/bash

# 颜色定义
GREEN='\033[0;32m'
BLUE='\033[0;34m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo "========================================"
echo "  Humanoid Bot - 一键启动脚本"
echo "========================================"
echo ""

# 检查 Python 是否安装
if ! command -v python3 &> /dev/null; then
    echo -e "${RED}[错误] 未检测到 Python3！${NC}"
    echo "请先安装 Python 3.8 或更高版本"
    exit 1
fi

echo -e "${BLUE}[1/5] 检查虚拟环境...${NC}"
if [ ! -d "venv" ]; then
    echo -e "${YELLOW}[*] 虚拟环境不存在，正在创建...${NC}"
    python3 -m venv venv
    if [ $? -ne 0 ]; then
        echo -e "${RED}[错误] 创建虚拟环境失败！${NC}"
        exit 1
    fi
    echo -e "${GREEN}[✓] 虚拟环境创建成功${NC}"
else
    echo -e "${GREEN}[✓] 虚拟环境已存在${NC}"
fi

echo ""
echo -e "${BLUE}[2/5] 激活虚拟环境...${NC}"
source venv/bin/activate
if [ $? -ne 0 ]; then
    echo -e "${RED}[错误] 激活虚拟环境失败！${NC}"
    exit 1
fi
echo -e "${GREEN}[✓] 虚拟环境已激活${NC}"

echo ""
echo -e "${BLUE}[3/5] 检查依赖包...${NC}"
python -c "import discord" &> /dev/null
if [ $? -ne 0 ]; then
    echo -e "${YELLOW}[*] 依赖包未安装，正在安装...${NC}"
    python -m pip install --upgrade pip
    pip install -r requirements.txt
    if [ $? -ne 0 ]; then
        echo -e "${RED}[错误] 依赖包安装失败！${NC}"
        deactivate
        exit 1
    fi
    echo -e "${GREEN}[✓] 依赖包安装成功${NC}"
else
    echo -e "${GREEN}[✓] 依赖包已安装${NC}"
fi

echo ""
echo -e "${BLUE}[4/5] 检查配置文件...${NC}"
if [ ! -f "config/config.yaml" ]; then
    echo -e "${YELLOW}[警告] 配置文件不存在！${NC}"
    if [ -f "config/config.example.yaml" ]; then
        echo -e "${YELLOW}[*] 正在从模板创建配置文件...${NC}"
        cp config/config.example.yaml config/config.yaml
        echo -e "${GREEN}[✓] 配置文件已创建${NC}"
        echo ""
        echo "========================================"
        echo "  首次运行提示"
        echo "========================================"
        echo "请先编辑 config/config.yaml 文件"
        echo "填写你的 Bot Token 和其他配置"
        echo ""
        echo "编辑完成后，再次运行此脚本启动 Bot"
        echo "========================================"
        deactivate
        exit 0
    else
        echo -e "${RED}[错误] 找不到配置模板文件！${NC}"
        deactivate
        exit 1
    fi
else
    echo -e "${GREEN}[✓] 配置文件已存在${NC}"
fi

echo ""
echo -e "${BLUE}[5/5] 启动 Bot...${NC}"
echo ""
echo "========================================"
echo "  Bot 正在启动..."
echo "  按 Ctrl+C 停止 Bot"
echo "========================================"
echo ""

python bot.py

exit_code=$?
if [ $exit_code -ne 0 ]; then
    echo ""
    echo "========================================"
    echo "  Bot 运行出错"
    echo "========================================"
fi

deactivate
exit $exit_code

