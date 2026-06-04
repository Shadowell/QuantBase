#!/bin/bash

# ============================================
# QuantBase 状态检查脚本
# 用法: ./status.sh [--json]
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

JSON_MODE=false
for arg in "$@"; do
    case $arg in
        --json) JSON_MODE=true ;;
        -h|--help)
            echo "用法: ./status.sh [选项]"
            echo ""
            echo "选项:"
            echo "  --json    以 JSON 格式输出（方便脚本调用）"
            echo "  -h        显示帮助"
            exit 0
            ;;
    esac
done

# ===== 检查函数 =====
check_service() {
    local name="$1"
    local port="$2"
    local pid_file="$3"
    local health_url="$4"

    local pid=""
    local pid_running=false
    local port_open=false
    local health_ok=false
    local cpu=""
    local mem=""
    local uptime_str=""

    # PID 检查
    if [ -f "$pid_file" ]; then
        pid=$(cat "$pid_file")
        if ps -p "$pid" > /dev/null 2>&1; then
            pid_running=true
            # 获取 CPU/内存 (macOS 兼容)
            cpu=$(ps -p "$pid" -o %cpu= 2>/dev/null | xargs)
            mem=$(ps -p "$pid" -o %mem= 2>/dev/null | xargs)
            # 获取进程运行时间
            uptime_str=$(ps -p "$pid" -o etime= 2>/dev/null | xargs)
        fi
    fi

    # 端口检查
    if lsof -ti :"$port" >/dev/null 2>&1; then
        port_open=true
        if [ -z "$pid" ] || [ "$pid_running" = false ]; then
            pid=$(lsof -ti :"$port" 2>/dev/null | head -1)
            pid_running=true
            cpu=$(ps -p "$pid" -o %cpu= 2>/dev/null | xargs)
            mem=$(ps -p "$pid" -o %mem= 2>/dev/null | xargs)
            uptime_str=$(ps -p "$pid" -o etime= 2>/dev/null | xargs)
        fi
    fi

    # 健康检查
    if [ -n "$health_url" ]; then
        if curl -s --max-time 3 "$health_url" >/dev/null 2>&1; then
            health_ok=true
        fi
    else
        health_ok=$port_open
    fi

    # 输出结果
    if [ "$JSON_MODE" = true ]; then
        echo "\"$name\": {\"status\": \"$([ "$health_ok" = true ] && echo "running" || echo "stopped")\", \"pid\": \"${pid:-null}\", \"port\": $port, \"cpu\": \"${cpu:-0}\", \"mem\": \"${mem:-0}\", \"uptime\": \"${uptime_str:-N/A}\"}"
    else
        echo -e "${BOLD}  $name${NC}"
        if [ "$health_ok" = true ]; then
            echo -e "    状态:    ${GREEN}● 运行中${NC}"
        elif [ "$port_open" = true ]; then
            echo -e "    状态:    ${YELLOW}● 端口开放但健康检查失败${NC}"
        else
            echo -e "    状态:    ${RED}● 已停止${NC}"
        fi
        echo -e "    端口:    $port"
        echo -e "    PID:     ${pid:-N/A}"
        if [ "$pid_running" = true ]; then
            echo -e "    CPU:     ${cpu:-0}%"
            echo -e "    内存:    ${mem:-0}%"
            echo -e "    运行时间: ${uptime_str:-N/A}"
        fi
    fi

    [ "$health_ok" = true ] && return 0 || return 1
}

if [ "$JSON_MODE" = true ]; then
    echo "{"
    check_service "backend" $BACKEND_PORT "$BACKEND_PID_FILE" "http://127.0.0.1:$BACKEND_PORT/api/v1/health"
    echo ","
    check_service "frontend" $FRONTEND_PORT "$FRONTEND_PID_FILE" ""
    echo "}"
    exit 0
fi

echo ""
echo -e "${BOLD}${CYAN}╔══════════════════════════════════════════╗${NC}"
echo -e "${BOLD}${CYAN}║          QuantBase 运行状态                 ║${NC}"
echo -e "${BOLD}${CYAN}╚══════════════════════════════════════════╝${NC}"
echo ""
echo -e "  时间: $(date '+%Y-%m-%d %H:%M:%S')"
echo ""

BACKEND_OK=false
FRONTEND_OK=false

check_service "后端 (Backend)" $BACKEND_PORT "$BACKEND_PID_FILE" "http://127.0.0.1:$BACKEND_PORT/api/v1/health" && BACKEND_OK=true
echo ""
check_service "前端 (Frontend)" $FRONTEND_PORT "$FRONTEND_PID_FILE" "" && FRONTEND_OK=true
echo ""

# ===== 日志信息 =====
echo -e "${BOLD}  日志文件${NC}"
if [ -f "$LOG_DIR/backend.log" ]; then
    local_size=$(du -h "$LOG_DIR/backend.log" 2>/dev/null | cut -f1 | xargs)
    echo -e "    后端日志: $LOG_DIR/backend.log ($local_size)"
else
    echo -e "    后端日志: ${YELLOW}不存在${NC}"
fi
if [ -f "$LOG_DIR/frontend.log" ]; then
    local_size=$(du -h "$LOG_DIR/frontend.log" 2>/dev/null | cut -f1 | xargs)
    echo -e "    前端日志: $LOG_DIR/frontend.log ($local_size)"
else
    echo -e "    前端日志: ${YELLOW}不存在${NC}"
fi
echo ""

# ===== 数据库信息 =====
DB_FILE=$(find "$SCRIPT_DIR/backend" -name "*.db" -o -name "*.sqlite" 2>/dev/null | head -1)
if [ -n "$DB_FILE" ]; then
    db_size=$(du -h "$DB_FILE" 2>/dev/null | cut -f1 | xargs)
    echo -e "${BOLD}  数据库${NC}"
    echo -e "    路径: $DB_FILE"
    echo -e "    大小: $db_size"
    echo ""
fi

# ===== 磁盘空间 =====
echo -e "${BOLD}  磁盘使用${NC}"
project_size=$(du -sh "$SCRIPT_DIR" 2>/dev/null | cut -f1 | xargs)
echo -e "    项目总大小: $project_size"
disk_avail=$(df -h "$SCRIPT_DIR" 2>/dev/null | tail -1 | awk '{print $4}')
echo -e "    磁盘可用:   $disk_avail"
echo ""

# ===== 访问地址 =====
echo -e "${BOLD}  访问地址${NC}"
if [ "$FRONTEND_OK" = true ]; then
    echo -e "    前端:    ${GREEN}http://localhost:$FRONTEND_PORT${NC}"
else
    echo -e "    前端:    ${RED}未运行${NC}"
fi
if [ "$BACKEND_OK" = true ]; then
    echo -e "    后端:    ${GREEN}http://localhost:$BACKEND_PORT${NC}"
    echo -e "    API文档: ${GREEN}http://localhost:$BACKEND_PORT/docs${NC}"
else
    echo -e "    后端:    ${RED}未运行${NC}"
fi
echo ""

# ===== 快捷操作 =====
if [ "$BACKEND_OK" = false ] || [ "$FRONTEND_OK" = false ]; then
    echo -e "${YELLOW}提示: 部分服务未运行，使用以下命令启动:${NC}"
    if [ "$BACKEND_OK" = false ] && [ "$FRONTEND_OK" = false ]; then
        echo -e "  ${CYAN}./start.sh${NC}"
    elif [ "$BACKEND_OK" = false ]; then
        echo -e "  ${CYAN}./start.sh --backend-only${NC}"
    elif [ "$FRONTEND_OK" = false ]; then
        echo -e "  ${CYAN}./start.sh --frontend-only${NC}"
    fi
    echo ""
fi
