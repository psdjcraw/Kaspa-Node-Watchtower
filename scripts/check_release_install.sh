#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

REPO="${KASPA_WATCHTOWER_GITHUB_REPO:-psdjcraw/Kaspa-Node-Watchtower}"
TAG="${KASPA_WATCHTOWER_RELEASE_TAG:-v0.8.3}"
VERSION="${TAG#v}"
PYTHON_BIN="${PYTHON_BIN:-python3}"

if ! command -v gh >/dev/null 2>&1; then
  printf 'FAIL gh CLI is required for release install check\n' >&2
  exit 2
fi

tmp_dir="$(mktemp -d)"
trap 'rm -rf "$tmp_dir"' EXIT

release_json="$(gh release view "$TAG" --repo "$REPO" --json isDraft,isPrerelease,url,assets)"
asset_name="$(printf '%s\n' "$release_json" | "$PYTHON_BIN" -c '
import json
import sys

version = sys.argv[1]
payload = json.load(sys.stdin)
if payload.get("isDraft"):
    raise SystemExit("release is draft")
if payload.get("isPrerelease"):
    raise SystemExit("release is prerelease")
for asset in payload.get("assets") or []:
    name = asset.get("name") or ""
    if name.startswith(f"kaspa-node-watchtower-{version}-") and name.endswith(".tar.gz"):
        print(name)
        break
else:
    raise SystemExit("release tarball asset missing")
' "$VERSION")"

checksum_asset="${asset_name}.sha256"
gh release download "$TAG" --repo "$REPO" --pattern "$asset_name" --dir "$tmp_dir" --clobber >/dev/null
gh release download "$TAG" --repo "$REPO" --pattern "$checksum_asset" --dir "$tmp_dir" --clobber >/dev/null

expected_sha="$(awk '{print $1}' "$tmp_dir/$checksum_asset")"
actual_sha="$(shasum -a 256 "$tmp_dir/$asset_name" | awk '{print $1}')"
if [ "$expected_sha" != "$actual_sha" ]; then
  printf 'FAIL release checksum mismatch expected=%s actual=%s\n' "$expected_sha" "$actual_sha" >&2
  exit 1
fi

mkdir "$tmp_dir/extract"
tar -xzf "$tmp_dir/$asset_name" -C "$tmp_dir/extract"
package_root="$(find "$tmp_dir/extract" -mindepth 1 -maxdepth 1 -type d | head -n 1)"
version_output="$("$PYTHON_BIN" "$package_root/watchtower.py" --version)"
case "$version_output" in
  *" $VERSION") ;;
  *)
    printf 'FAIL release version mismatch: %s\n' "$version_output" >&2
    exit 1
    ;;
esac

ruby -c packaging/homebrew/kaspa-node-watchtower.rb >/dev/null
if ! grep -q "releases/download/$TAG/$asset_name" packaging/homebrew/kaspa-node-watchtower.rb; then
  printf 'FAIL Homebrew formula URL does not point at %s\n' "$asset_name" >&2
  exit 1
fi
if ! grep -q "version \"$VERSION\"" packaging/homebrew/kaspa-node-watchtower.rb; then
  printf 'FAIL Homebrew formula version is not %s\n' "$VERSION" >&2
  exit 1
fi
if ! grep -q "sha256 \"$expected_sha\"" packaging/homebrew/kaspa-node-watchtower.rb; then
  printf 'FAIL Homebrew formula sha256 is not %s\n' "$expected_sha" >&2
  exit 1
fi

printf 'OK release install check: %s %s\n' "$TAG" "$asset_name"
printf 'OK checksum: %s\n' "$actual_sha"
printf 'OK package version: %s\n' "$version_output"
printf 'OK Homebrew formula: version=%s sha256=%s\n' "$VERSION" "$expected_sha"
