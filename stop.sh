#!/bin/bash

# ============================================
# QuantBase 停止脚本
# 用法: ./stop.sh [--backend-only | --frontend-only | --force]
# ============================================

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
LOG_DIR="$SCRIPT_DIR/logs"
BACKEND_PORT=8889
FRONTEND_PORT=8888
BACKEND_PID_FILE="$LOG_DIR/backend.pid"
FRONTEND_PID_FILE="$LOG_DIR/frontend.pid"

STOP_BACKEND=true
STOP_FRONTEND=true
FORCE_KILL=false

# ===== 参数解析 =====
for arg in "$@"; do
    case $arg in
        --backend-only)  STOP_FRONTEND=false ;;
        --frontend-only) STOP_BACKEND=false ;;
        --force|-f)      FORCE_KILL=true ;;
        -h|--help)
            echo "用法: ./stop.sh [选项]"
            echo ""
            echo "选项:"
            echo "  --backend-only   只停止后端"
            echo "  --frontend-only  只停止前端"
            echo "  --force, -f      强制杀掉进程 (kill -9)"
            echo "  -h, --help       显示帮助"
            exit 0
            ;;
        *)
            echo -e "${RED}未知参数: $arg${NC}"
            exit 1
            ;;
    esac
done

echo ""
echo -e "${BOLD}${CYAN}╔══════════════════════════════════════════╗${NC}"
echo -e "${BOLD}${CYAN}║          QuantBase 停止脚本                 ║${NC}"
echo -e "${BOLD}${CYAN}╚══════════════════════════════════════════╝${NC}"
echo ""

stop_process() {
    local name="$1"
    local pid_file="$2"
    local pattern="$3"
    local port="$4"

    echo -e "${BOLD}[停止${name}]${NC}"

    local stopped=false

    # 方式1: 通过 PID 文件
    if [ -f "$pid_file" ]; then
        local pid=$(cat "$pid_file")
        if ps -p "$pid" > /dev/null 2>&1; then
            if [ "$FORCE_KILL" = true ]; then
                kill -9 "$pid" 2>/dev/null
            else
                kill "$pid" 2>/dev/null
            fi
            echo -e "  PID $pid 已发送终止信号"
            stopped=true
        else
            echo -e "  PID $pid 进程已不存在"
        fi
        rm -f "$pid_file"
    fi

    # 方式2: 通过进程名匹配
    local pids=$(pgrep -f "$pattern" 2>/dev/null || true)
    if [ -n "$pids" ]; then
        for pid in $pids; do
            if [ "$FORCE_KILL" = true ]; then
                kill -9 "$pid" 2>/dev/null || true
            else
                kill "$pid" 2>/dev/null || true
            fi
        done
        echo -e "  通过进程名清理: $pids"
        stopped=true
    fi

    # 方式3: 通过端口清理
    local port_pids=$(lsof -ti :"$port" 2>/dev/null || true)
    if [ -n "$port_pids" ]; then
        for pid in $port_pids; do
            kill -9 "$pid" 2>/dev/null || true
        done
        echo -e "  通过端口 $port 清理: $port_pids"
        stopped=true
    fi

    # 等待端口释放
    for i in $(seq 1 5); do
        if ! lsof -ti :"$port" >/dev/null 2>&1; then
            echo -e "  ${GREEN}✓ ${name}已停止 (端口 $port 已释放)${NC}"
            return 0
        fi
        sleep 1
    done

    # 最后兜底 — 强杀端口
    local remaining=$(lsof -ti :"$port" 2>/dev/null || true)
    if [ -n "$remaining" ]; then
        kill -9 $remaining 2>/dev/null || true
        sleep 1
        echo -e "  ${YELLOW}⚠ 已强制清理端口 $port 残留进程${NC}"
    fi

    if [ "$stopped" = false ]; then
        echo -e "  ${YELLOW}${name}未在运行${NC}"
    fi
}

# ===== 执行停止 =====
if [ "$STOP_BACKEND" = true ]; then
    stop_process "后端" "$BACKEND_PID_FILE" "uvicorn app.main:app" "$BACKEND_PORT"
    echo ""
fi

if [ "$STOP_FRONTEND" = true ]; then
    stop_process "前端" "$FRONTEND_PID_FILE" "node.*vite" "$FRONTEND_PORT"
    echo ""
fi

echo -e "${GREEN}✓ QuantBase 已停止${NC}"
echo ""
