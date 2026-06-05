#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

PYTHON_BIN="${PYTHON_BIN:-python3}"
VENV_DIR="${VENV_DIR:-.venv}"

if [ ! -x "$VENV_DIR/bin/python" ]; then
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

"$VENV_DIR/bin/python" -m pip install --upgrade pip
"$VENV_DIR/bin/python" -m pip install -r requirements.txt

mkdir -p generated_proto
"$VENV_DIR/bin/python" -m grpc_tools.protoc \
  -I proto \
  --python_out=generated_proto \
  --grpc_python_out=generated_proto \
  proto/rpc.proto \
  proto/messages.proto

"$VENV_DIR/bin/python" watchtower.py --version
printf 'Bootstrap complete: venv=%s generated_proto=ready\n' "$VENV_DIR"
