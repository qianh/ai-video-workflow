#!/usr/bin/env bash
set -euo pipefail

script_dir=$(cd "$(dirname "$0")" && pwd)
repo_root=$(cd "$script_dir/.." && pwd)
sidecar_root="$repo_root/services/sidecar"
target_triple="${1:-aarch64-apple-darwin}"
output_dir="$repo_root/apps/desktop/src-tauri/binaries"

case "$target_triple" in
  aarch64-apple-darwin|x86_64-apple-darwin) ;;
  *)
    echo "Unsupported sidecar target: $target_triple" >&2
    exit 2
    ;;
esac

mkdir -p "$output_dir"
cd "$sidecar_root"
uv sync --extra build
uv run --extra build pyinstaller \
  --noconfirm \
  --clean \
  --onefile \
  --name workflow-sidecar \
  --paths src \
  scripts/pyinstaller_entry.py

cp "$sidecar_root/dist/workflow-sidecar" "$output_dir/workflow-sidecar-$target_triple"
chmod 755 "$output_dir/workflow-sidecar-$target_triple"
echo "$output_dir/workflow-sidecar-$target_triple"
