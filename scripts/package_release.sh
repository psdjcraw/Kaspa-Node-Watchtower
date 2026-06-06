#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

DIST_DIR="dist"
PACKAGE_LABEL=""

while [ "$#" -gt 0 ]; do
  case "$1" in
    --dist-dir)
      DIST_DIR="$2"
      shift 2
      ;;
    --label)
      PACKAGE_LABEL="$2"
      shift 2
      ;;
    -h|--help)
      printf 'Usage: scripts/package_release.sh [--dist-dir DIR] [--label LABEL]\n'
      exit 0
      ;;
    *)
      printf 'Unknown argument: %s\n' "$1" >&2
      exit 2
      ;;
  esac
done

PYTHON_BIN="python3"
if [ -x ".venv/bin/python" ]; then
  PYTHON_BIN=".venv/bin/python"
fi

VERSION="$("$PYTHON_BIN" watchtower.py --version | awk '{print $2}')"
REVISION="$(git rev-parse --short HEAD)"
if [ -n "$PACKAGE_LABEL" ]; then
  SAFE_LABEL="$(printf '%s' "$PACKAGE_LABEL" | tr -cs 'A-Za-z0-9_.-' '-' | sed 's/^-//; s/-$//')"
else
  SAFE_LABEL="$VERSION-$REVISION"
fi
if [ -z "$SAFE_LABEL" ]; then
  SAFE_LABEL="$VERSION-$REVISION"
fi

PACKAGE_NAME="kaspa-node-watchtower-$SAFE_LABEL"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

STAGING_DIR="$TMP_DIR/$PACKAGE_NAME"
mkdir -p "$STAGING_DIR" "$DIST_DIR"

git ls-files | while IFS= read -r file; do
  mkdir -p "$STAGING_DIR/$(dirname "$file")"
  cp -p "$file" "$STAGING_DIR/$file"
done

"$PYTHON_BIN" - "$STAGING_DIR" "$VERSION" "$REVISION" "$PACKAGE_NAME" <<'PY'
import datetime as dt
import json
import sys
from pathlib import Path

staging_dir = Path(sys.argv[1])
version = sys.argv[2]
revision = sys.argv[3]
package_name = sys.argv[4]
files = sorted(
    str(path.relative_to(staging_dir))
    for path in staging_dir.rglob("*")
    if path.is_file() and path.name != "PACKAGE-MANIFEST.json"
)
manifest = {
    "package_name": package_name,
    "version": version,
    "git_revision": revision,
    "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    "file_count": len(files),
    "files": files,
    "notes": [
        "Generated from tracked repository files only.",
        "Local config.json, state files, virtualenvs, and diagnostics are intentionally excluded.",
    ],
}
(staging_dir / "PACKAGE-MANIFEST.json").write_text(
    json.dumps(manifest, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)
PY

ARCHIVE_PATH="$DIST_DIR/$PACKAGE_NAME.tar.gz"
tar -C "$TMP_DIR" -czf "$ARCHIVE_PATH" "$PACKAGE_NAME"
shasum -a 256 "$ARCHIVE_PATH" > "$ARCHIVE_PATH.sha256"

printf 'release_package=%s\n' "$ARCHIVE_PATH"
printf 'release_checksum=%s.sha256\n' "$ARCHIVE_PATH"
