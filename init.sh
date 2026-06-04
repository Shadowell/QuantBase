#!/bin/bash

# ============================================
# QuantBase 初始化脚本
# 用途: 克隆仓库后首次运行，完成环境搭建和种子数据导入
# 用法: ./init.sh [--force-seed]
#
# 步骤:
#   1. 环境检查 (python3, node, npm)
#   2. 后端虚拟环境 + 依赖安装
#   3. 前端依赖安装
#   4. .env 配置检查
#   5. 数据库初始化 (建表)
#   6. 导入种子策略 (10 个内置 demo 策略)
# ============================================

set -euo pipefail

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
BACKEND_DIR="$SCRIPT_DIR/backend"
FRONTEND_DIR="$SCRIPT_DIR/frontend"

FORCE_SEED=false
for arg in "$@"; do
    case $arg in
        --force-seed) FORCE_SEED=true ;;
        -h|--help)
            echo "用法: ./init.sh [选项]"
            echo ""
            echo "选项:"
            echo "  --force-seed  覆盖已有的同名策略"
            echo "  -h, --help    显示帮助"
            exit 0
            ;;
    esac
done

echo ""
echo -e "${BOLD}${CYAN}╔══════════════════════════════════════════╗${NC}"
echo -e "${BOLD}${CYAN}║        QuantBase 初始化脚本                 ║${NC}"
echo -e "${BOLD}${CYAN}╚══════════════════════════════════════════╝${NC}"
echo ""

# ===== 1. 环境检查 =====
echo -e "${BOLD}[1/6] 环境检查${NC}"

check_command() {
    if ! command -v "$1" &> /dev/null; then
        echo -e "  ${RED}✗ $1 未安装${NC}"
        return 1
    fi
    local ver=$($1 --version 2>&1 | head -1)
    echo -e "  ${GREEN}✓${NC} $1 ($ver)"
    return 0
}

ENV_OK=true
check_command python3 || ENV_OK=false
check_command pip3 || check_command pip || ENV_OK=false
check_command node || ENV_OK=false
check_command npm || ENV_OK=false

if [ "$ENV_OK" = false ]; then
    echo -e "\n${RED}环境依赖不满足，请先安装:${NC}"
    echo -e "  Python 3.11+: https://www.python.org/"
    echo -e "  Node.js 18+:  https://nodejs.org/"
    exit 1
fi
echo ""

# ===== 2. 后端依赖 =====
echo -e "${BOLD}[2/6] 安装后端依赖${NC}"
cd "$BACKEND_DIR"

if [ ! -d "venv" ]; then
    echo "  创建虚拟环境..."
    python3 -m venv venv
fi

source venv/bin/activate
echo "  安装 Python 依赖 (pip install -r requirements.txt)..."
pip install -r requirements.txt -q
echo -e "  ${GREEN}✓ 后端依赖就绪${NC}"
echo ""

# ===== 3. 前端依赖 =====
echo -e "${BOLD}[3/6] 安装前端依赖${NC}"
cd "$FRONTEND_DIR"

if [ ! -d "node_modules" ]; then
    echo "  安装 npm 依赖..."
    npm install --silent
else
    echo "  node_modules 已存在，跳过"
fi
echo -e "  ${GREEN}✓ 前端依赖就绪${NC}"
echo ""

# ===== 4. .env 配置 =====
echo -e "${BOLD}[4/6] 配置检查${NC}"
cd "$BACKEND_DIR"

if [ ! -f ".env" ]; then
    if [ -f ".env.example" ]; then
        cp .env.example .env
        echo -e "  ${YELLOW}⚠ 已从 .env.example 创建 .env，请编辑填入你的 API Key:${NC}"
        echo -e "  ${YELLOW}  $BACKEND_DIR/.env${NC}"
    else
        echo -e "  ${YELLOW}⚠ 未找到 .env 和 .env.example，使用默认配置启动${NC}"
    fi
else
    echo -e "  ${GREEN}✓ .env 已存在${NC}"
fi
echo ""

# ===== 5. 数据库初始化 =====
echo -e "${BOLD}[5/6] 初始化数据库${NC}"
cd "$SCRIPT_DIR"
mkdir -p data

# 通过 Python 调用 FastAPI 的 init_db 创建所有表
python3 -c "
import sys
sys.path.insert(0, 'backend')
from app.db.local_db import LocalDatabase
db = LocalDatabase()
db.init_db()
print('  数据库表已创建')
"
echo -e "  ${GREEN}✓ 数据库就绪${NC}"
echo ""

# ===== 6. 导入种子策略 =====
echo -e "${BOLD}[6/6] 导入种子策略${NC}"
cd "$SCRIPT_DIR"

SEED_ARGS=""
if [ "$FORCE_SEED" = true ]; then
    SEED_ARGS="--force"
    echo -e "  ${YELLOW}(--force-seed 模式)${NC}"
fi

# 确保 venv 仍然激活
source "$BACKEND_DIR/venv/bin/activate"
python3 scripts/seed_strategies.py $SEED_ARGS

echo ""

# ===== 完成 =====
echo -e "${GREEN}╔══════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║        QuantBase 初始化完成！               ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════╝${NC}"
echo ""
echo -e "  ${BOLD}下一步:${NC}"
echo -e "  1. 编辑后端配置:  ${CYAN}vim backend/.env${NC}"
echo -e "     (填入 OKX API Key、Proxy 等配置)"
echo -e ""
echo -e "  2. 启动服务:      ${CYAN}./start.sh${NC}"
echo -e ""
echo -e "  3. 访问前端:      ${CYAN}http://localhost:8888${NC}"
echo -e "     API文档:       ${CYAN}http://localhost:8889/docs${NC}"
echo ""
echo -e "  更多命令:"
echo -e "  ./status.sh       查看运行状态"
echo -e "  ./stop.sh         停止服务"
echo -e "  ./restart.sh      重启服务"
echo ""
