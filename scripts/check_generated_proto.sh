#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

PYTHON_BIN="${PYTHON_BIN:-python3}"
if [ -x ".venv/bin/python" ]; then
  PYTHON_BIN=".venv/bin/python"
fi

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

"$PYTHON_BIN" -m grpc_tools.protoc \
  -I proto \
  --python_out="$TMP_DIR" \
  --grpc_python_out="$TMP_DIR" \
  proto/rpc.proto \
  proto/messages.proto

for generated in messages_pb2.py messages_pb2_grpc.py rpc_pb2.py rpc_pb2_grpc.py; do
  diff -u "generated_proto/$generated" "$TMP_DIR/$generated" >/dev/null
done

printf 'OK generated protobuf files are current\n'
