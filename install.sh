#!/bin/bash

# 颜色定义
GREEN='\033[0;32m'
BLUE='\033[0;34m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo "========================================"
echo "  Humanoid Bot - 安装脚本"
echo "========================================"
echo ""

# 检查 Python 是否安装
if ! command -v python3 &> /dev/null; then
    echo -e "${RED}[错误] 未检测到 Python3！${NC}"
    echo "请先安装 Python 3.8 或更高版本"
    exit 1
fi

echo -e "${YELLOW}[*] 检测到 Python 版本:${NC}"
python3 --version
echo ""

echo -e "${BLUE}[1/4] 创建虚拟环境...${NC}"
if [ -d "venv" ]; then
    echo -e "${YELLOW}[*] 虚拟环境已存在，跳过创建${NC}"
else
    python3 -m venv venv
    if [ $? -ne 0 ]; then
        echo -e "${RED}[错误] 创建虚拟环境失败！${NC}"
        exit 1
    fi
    echo -e "${GREEN}[✓] 虚拟环境创建成功${NC}"
fi

echo ""
echo -e "${BLUE}[2/4] 激活虚拟环境...${NC}"
source venv/bin/activate
echo -e "${GREEN}[✓] 虚拟环境已激活${NC}"

echo ""
echo -e "${BLUE}[3/4] 安装依赖包...${NC}"
echo -e "${YELLOW}[*] 升级 pip...${NC}"
python -m pip install --upgrade pip

echo -e "${YELLOW}[*] 安装项目依赖...${NC}"
pip install -r requirements.txt
if [ $? -ne 0 ]; then
    echo -e "${RED}[错误] 依赖包安装失败！${NC}"
    deactivate
    exit 1
fi
echo -e "${GREEN}[✓] 依赖包安装成功${NC}"

echo ""
echo -e "${BLUE}[4/4] 创建配置文件...${NC}"
if [ -f "config/config.yaml" ]; then
    echo -e "${YELLOW}[*] 配置文件已存在，跳过创建${NC}"
else
    if [ -f "config/config.example.yaml" ]; then
        cp config/config.example.yaml config/config.yaml
        echo -e "${GREEN}[✓] 配置文件已创建${NC}"
    else
        echo -e "${YELLOW}[警告] 找不到配置模板文件${NC}"
    fi
fi

echo ""
echo "========================================"
echo "  安装完成！"
echo "========================================"
echo ""
echo "接下来的步骤:"
echo "1. 编辑 config/config.yaml 文件"
echo "2. 填写你的 Bot Token 和配置"
echo "3. 运行 ./start.sh 启动 Bot"
echo ""
echo "========================================"

deactivate

