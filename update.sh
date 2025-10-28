#!/bin/bash

# 颜色定义
GREEN='\033[0;32m'
BLUE='\033[0;34m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo "========================================"
echo "  Humanoid Bot - 更新依赖脚本"
echo "========================================"
echo ""

if [ ! -d "venv" ]; then
    echo -e "${RED}[错误] 虚拟环境不存在！${NC}"
    echo "请先运行 ./install.sh 进行安装"
    exit 1
fi

echo -e "${YELLOW}[*] 激活虚拟环境...${NC}"
source venv/bin/activate

echo ""
echo -e "${YELLOW}[*] 升级 pip...${NC}"
python -m pip install --upgrade pip

echo ""
echo -e "${YELLOW}[*] 更新依赖包...${NC}"
pip install --upgrade -r requirements.txt

if [ $? -ne 0 ]; then
    echo -e "${RED}[错误] 更新失败！${NC}"
    deactivate
    exit 1
fi

echo ""
echo "========================================"
echo "  更新完成！"
echo "========================================"

deactivate

