#!/bin/bash

# ============================================
# QuantBase 重启脚本
# 用法: ./restart.sh [--backend-only | --frontend-only | --force]
# ============================================

set -euo pipefail

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

STOP_ARGS=()
START_ARGS=()

for arg in "$@"; do
    case "$arg" in
        --backend-only|--frontend-only)
            STOP_ARGS+=("$arg")
            START_ARGS+=("$arg")
            ;;
        --force|-f)
            STOP_ARGS+=("--force")
            ;;
        -h|--help)
            echo "用法: ./restart.sh [选项]"
            echo ""
            echo "选项:"
            echo "  --backend-only   只重启后端"
            echo "  --frontend-only  只重启前端"
            echo "  --force, -f      停止时强制杀掉进程"
            echo "  -h, --help       显示帮助"
            exit 0
            ;;
        *)
            echo "未知参数: $arg"
            echo "使用 -h 查看帮助"
            exit 1
            ;;
    esac
done

"$SCRIPT_DIR/stop.sh" "${STOP_ARGS[@]}"
"$SCRIPT_DIR/start.sh" "${START_ARGS[@]}"
