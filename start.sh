#!/bin/bash

# ============================================
# QuantBase 启动脚本
# 用法: ./start.sh [--backend-only | --frontend-only]
# ============================================

set -euo pipefail

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
LOG_DIR="$SCRIPT_DIR/logs"
BACKEND_DIR="$SCRIPT_DIR/backend"
FRONTEND_DIR="$SCRIPT_DIR/frontend"

BACKEND_PORT=8889
FRONTEND_PORT=8888
BACKEND_LOG="$LOG_DIR/backend.log"
FRONTEND_LOG="$LOG_DIR/frontend.log"
BACKEND_PID_FILE="$LOG_DIR/backend.pid"
FRONTEND_PID_FILE="$LOG_DIR/frontend.pid"

START_BACKEND=true
START_FRONTEND=true

# ===== 参数解析 =====
for arg in "$@"; do
    case $arg in
        --backend-only)  START_FRONTEND=false ;;
        --frontend-only) START_BACKEND=false ;;
        -h|--help)
            echo "用法: ./start.sh [选项]"
            echo ""
            echo "选项:"
            echo "  --backend-only   只启动后端"
            echo "  --frontend-only  只启动前端"
            echo "  -h, --help       显示帮助"
            exit 0
            ;;
        *)
            echo -e "${RED}未知参数: $arg${NC}"
            echo "使用 -h 查看帮助"
            exit 1
            ;;
    esac
done

echo ""
echo -e "${BOLD}${CYAN}╔══════════════════════════════════════════╗${NC}"
echo -e "${BOLD}${CYAN}║          QuantBase 启动脚本                 ║${NC}"
echo -e "${BOLD}${CYAN}╚══════════════════════════════════════════╝${NC}"
echo ""

# ===== 环境检查 =====
echo -e "${BOLD}[环境检查]${NC}"

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

if [ "$START_BACKEND" = true ]; then
    check_command python3 || ENV_OK=false
    check_command pip3 || check_command pip || ENV_OK=false
fi

if [ "$START_FRONTEND" = true ]; then
    check_command node || ENV_OK=false
    check_command npm || ENV_OK=false
fi

if [ "$ENV_OK" = false ]; then
    echo -e "\n${RED}环境依赖不满足，请先安装缺失的工具${NC}"
    exit 1
fi

echo ""

# ===== 创建日志目录 =====
mkdir -p "$LOG_DIR"

# ===== 日志轮转 =====
rotate_log() {
    local log_file="$1"
    local max_size=$((10 * 1024 * 1024))  # 10MB
    local max_backups=5

    if [ -f "$log_file" ] && [ $(stat -f%z "$log_file" 2>/dev/null || stat -c%s "$log_file" 2>/dev/null || echo 0) -gt $max_size ]; then
        for i in $(seq $((max_backups - 1)) -1 1); do
            [ -f "${log_file}.$i" ] && mv "${log_file}.$i" "${log_file}.$((i + 1))"
        done
        mv "$log_file" "${log_file}.1"
        echo "  日志已轮转: $(basename $log_file)"
    fi
}

# ===== 端口检查 =====
check_port() {
    lsof -nP -iTCP:"$1" -sTCP:LISTEN -t >/dev/null 2>&1
}

if [ "$START_BACKEND" = true ] && check_port $BACKEND_PORT; then
    echo -e "${RED}✗ 端口 $BACKEND_PORT 被占用${NC}"
    echo -e "  占用进程: $(lsof -nP -iTCP:$BACKEND_PORT -sTCP:LISTEN -t | head -3 | xargs ps -p 2>/dev/null | tail -n +2 || echo '未知')"
    echo -e "  请先运行 ${YELLOW}./stop.sh${NC}"
    exit 1
fi

if [ "$START_FRONTEND" = true ] && check_port $FRONTEND_PORT; then
    echo -e "${RED}✗ 端口 $FRONTEND_PORT 被占用${NC}"
    echo -e "  占用进程: $(lsof -nP -iTCP:$FRONTEND_PORT -sTCP:LISTEN -t | head -3 | xargs ps -p 2>/dev/null | tail -n +2 || echo '未知')"
    echo -e "  请先运行 ${YELLOW}./stop.sh${NC}"
    exit 1
fi

# ===== 启动后端 =====
if [ "$START_BACKEND" = true ]; then
    echo -e "${BOLD}[启动后端]${NC}"

    cd "$BACKEND_DIR"

    # 虚拟环境
    if [ ! -d "venv" ]; then
        echo "  创建虚拟环境..."
        python3 -m venv venv
        source venv/bin/activate
        echo "  安装依赖..."
        pip install -r requirements.txt -q
    else
        source venv/bin/activate
    fi

    # .env 检查
    if [ ! -f ".env" ]; then
        echo -e "  ${YELLOW}⚠ 未找到 .env 文件，使用默认配置${NC}"
        echo -e "  ${YELLOW}  如需配置交易所API，请复制 .env.example → .env${NC}"
    fi

    # 日志轮转
    rotate_log "$BACKEND_LOG"

    # 启动 uvicorn
    nohup uvicorn app.main:app --host 0.0.0.0 --port $BACKEND_PORT > "$BACKEND_LOG" 2>&1 &
    BACKEND_PID=$!
    echo $BACKEND_PID > "$BACKEND_PID_FILE"

    # 等待就绪（交易所 load_markets 可能耗时较长，给足时间）
    BACKEND_TIMEOUT=60
    echo -n "  等待后端启动 (最长${BACKEND_TIMEOUT}s)"
    for i in $(seq 1 $BACKEND_TIMEOUT); do
        sleep 1
        # 每5秒打一个点，避免输出太长
        [ $((i % 3)) -eq 0 ] && echo -n "."
        if curl -s --max-time 2 http://127.0.0.1:$BACKEND_PORT/api/v1/health >/dev/null 2>&1; then
            echo ""
            echo -e "  ${GREEN}✓ 后端已启动 (PID: $BACKEND_PID, Port: $BACKEND_PORT, 耗时: ${i}s)${NC}"
            break
        fi
        if ! ps -p $BACKEND_PID > /dev/null 2>&1; then
            echo ""
            echo -e "  ${RED}✗ 后端进程已退出，请检查日志:${NC}"
            echo -e "  ${YELLOW}  tail -50 $BACKEND_LOG${NC}"
            exit 1
        fi
        if [ $i -eq $BACKEND_TIMEOUT ]; then
            echo ""
            echo -e "  ${YELLOW}⚠ 后端健康检查超时 (${BACKEND_TIMEOUT}s)，但进程仍在运行 (PID: $BACKEND_PID)${NC}"
            echo -e "  ${YELLOW}  可能是交易所连接较慢，服务可能稍后可用${NC}"
            echo -e "  ${YELLOW}  查看日志: tail -50 $BACKEND_LOG${NC}"
        fi
    done
    echo ""
fi

# ===== 启动前端 =====
if [ "$START_FRONTEND" = true ]; then
    echo -e "${BOLD}[启动前端]${NC}"

    cd "$FRONTEND_DIR"

    # 安装依赖
    if [ ! -d "node_modules" ]; then
        echo "  安装前端依赖..."
        npm install --silent
    fi

    # 日志轮转
    rotate_log "$FRONTEND_LOG"

    # 启动 Vite
    nohup npm run dev > "$FRONTEND_LOG" 2>&1 &
    FRONTEND_PID=$!
    echo $FRONTEND_PID > "$FRONTEND_PID_FILE"

    # 等待就绪
    echo -n "  等待前端启动"
    for i in $(seq 1 15); do
        sleep 1
        echo -n "."
        if curl -s http://127.0.0.1:$FRONTEND_PORT/ >/dev/null 2>&1; then
            echo ""
            echo -e "  ${GREEN}✓ 前端已启动 (PID: $FRONTEND_PID, Port: $FRONTEND_PORT)${NC}"
            break
        fi
        if ! ps -p $FRONTEND_PID > /dev/null 2>&1; then
            echo ""
            echo -e "  ${RED}✗ 前端进程已退出，请检查日志:${NC}"
            echo -e "  ${YELLOW}  tail -50 $FRONTEND_LOG${NC}"
            exit 1
        fi
        if [ $i -eq 15 ]; then
            echo ""
            echo -e "  ${RED}✗ 前端启动超时 (15s)${NC}"
            echo -e "  ${YELLOW}  tail -50 $FRONTEND_LOG${NC}"
            exit 1
        fi
    done
    echo ""
fi

# ===== 启动完成 =====
echo -e "${GREEN}╔══════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║          QuantBase 启动成功！               ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════╝${NC}"
echo ""
if [ "$START_FRONTEND" = true ]; then
    echo -e "  前端:     ${BOLD}http://localhost:$FRONTEND_PORT${NC}"
fi
if [ "$START_BACKEND" = true ]; then
    echo -e "  后端:     ${BOLD}http://localhost:$BACKEND_PORT${NC}"
    echo -e "  API文档:  ${BOLD}http://localhost:$BACKEND_PORT/docs${NC}"
fi
echo ""
echo -e "  日志目录: $LOG_DIR/"
echo -e "  查看状态: ${CYAN}./status.sh${NC}"
echo -e "  停止服务: ${CYAN}./stop.sh${NC}"
echo -e "  重启服务: ${CYAN}./restart.sh${NC}"
echo ""
