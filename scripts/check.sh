#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON_BIN="python3"

if [ -x "$ROOT_DIR/backend/venv/bin/python" ]; then
  PYTHON_BIN="$ROOT_DIR/backend/venv/bin/python"
fi

echo "[check] repository root: $ROOT_DIR"

run_if_present() {
  local description="$1"
  local path="$2"
  shift 2

  if [ -e "$path" ]; then
    echo "[check] $description"
    (
      cd "$(dirname "$path")"
      "$@"
    )
  fi
}

run_if_present "frontend build" "$ROOT_DIR/frontend/package.json" npm run build
run_if_present "frontend lint" "$ROOT_DIR/frontend/package.json" npm run lint

if [ -f "$ROOT_DIR/pyproject.toml" ]; then
  echo "[check] python project detected via pyproject.toml"
elif [ -d "$ROOT_DIR/backend" ]; then
  echo "[check] compiling backend python sources"
  "$PYTHON_BIN" -m compileall -q "$ROOT_DIR/backend/app"
fi

if [ -f "$ROOT_DIR/voice_gen.py" ]; then
  echo "[check] compiling standalone python entrypoints"
  "$PYTHON_BIN" -m compileall "$ROOT_DIR/voice_gen.py"
fi

echo "[check] public boundary and static tests"
PYTHONPATH="$ROOT_DIR/backend" "$PYTHON_BIN" -m pytest -q \
  "$ROOT_DIR/tests/test_project_disclaimers.py" \
  "$ROOT_DIR/tests/test_auth_frontend_static.py" \
  "$ROOT_DIR/tests/test_ai_lab_orbit_auto_post_static.py" \
  "$ROOT_DIR/tests/test_page_design_docs.py" \
  "$ROOT_DIR/tests/test_auth_service.py" \
  "$ROOT_DIR/tests/test_mcp_client_tools.py" \
  "$ROOT_DIR/tests/test_live_execution_center_api.py::test_live_execution_mutations_are_disabled_by_default"

echo "[check] done"
