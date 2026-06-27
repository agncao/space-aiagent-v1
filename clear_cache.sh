#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "开始清理缓存目录: $ROOT_DIR"

remove_if_exists() {
  local path="$1"
  if [ -e "$path" ]; then
    rm -rf "$path"
    echo "已删除: $path"
  fi
}

remove_if_exists "$ROOT_DIR/build"
remove_if_exists "$ROOT_DIR/dist"

find "$ROOT_DIR" -type d -name "*.egg-info" -prune -print0 | while IFS= read -r -d '' dir; do
  rm -rf "$dir"
  echo "已删除: $dir"
done

find "$ROOT_DIR" -type d -name "__pycache__" -prune -print0 | while IFS= read -r -d '' dir; do
  rm -rf "$dir"
  echo "已删除: $dir"
done

echo "清理完成"