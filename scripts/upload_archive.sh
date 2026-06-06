#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

SOURCE=""
TARGET=""
DRY_RUN=0

usage() {
  printf 'Usage: scripts/upload_archive.sh --source PATH --target TARGET [--dry-run]\n'
  printf 'Targets: local path, file:///path, s3://bucket/prefix, or rclone remote:path\n'
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --source)
      SOURCE="$2"
      shift 2
      ;;
    --target)
      TARGET="$2"
      shift 2
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      printf 'Unknown argument: %s\n' "$1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [ -z "$SOURCE" ] || [ -z "$TARGET" ]; then
  usage >&2
  exit 2
fi
if [ ! -e "$SOURCE" ]; then
  printf 'Archive source not found: %s\n' "$SOURCE" >&2
  exit 1
fi

copy_local() {
  local source="$1"
  local target="$2"
  target="${target#file://}"
  if [ "$DRY_RUN" -eq 1 ]; then
    printf 'DRY_RUN local_copy source=%s target=%s\n' "$source" "$target"
    return 0
  fi
  mkdir -p "$target"
  cp -R "$source" "$target/"
  printf 'archive_uploaded source=%s target=%s/%s\n' "$source" "$target" "$(basename "$source")"
}

copy_s3() {
  local source="$1"
  local target="$2"
  if ! command -v aws >/dev/null 2>&1; then
    printf 'aws CLI is required for s3 targets\n' >&2
    exit 1
  fi
  if [ "$DRY_RUN" -eq 1 ]; then
    printf 'DRY_RUN aws s3 cp --recursive %s %s\n' "$source" "$target"
    return 0
  fi
  if [ -d "$source" ]; then
    aws s3 cp --recursive "$source" "$target/$(basename "$source")"
  else
    aws s3 cp "$source" "$target/"
  fi
}

copy_rclone() {
  local source="$1"
  local target="$2"
  if ! command -v rclone >/dev/null 2>&1; then
    printf 'rclone is required for remote targets\n' >&2
    exit 1
  fi
  if [ "$DRY_RUN" -eq 1 ]; then
    printf 'DRY_RUN rclone copy %s %s\n' "$source" "$target"
    return 0
  fi
  rclone copy "$source" "$target"
}

case "$TARGET" in
  s3://*)
    copy_s3 "$SOURCE" "$TARGET"
    ;;
  file://*|/*|.*)
    copy_local "$SOURCE" "$TARGET"
    ;;
  *:*)
    copy_rclone "$SOURCE" "$TARGET"
    ;;
  *)
    copy_local "$SOURCE" "$TARGET"
    ;;
esac
