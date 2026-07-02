#!/usr/bin/env bash
# vendor-sync.sh
# 用途：从 ~/Downloads/Douyin_TikTok_Download_API-main 同步最新源码到 vendor/
# 触发：上游 main 分支有反风控更新，或抖音风控失效
#
# 使用前请确保已下载最新 zip 到 ~/Downloads/Douyin_TikTok_Download_API-main/
#
# 设计：只覆盖 vendor 范围内的源文件，不动 __init__.py 和 README.md

set -e

SKILL_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENDOR="$SKILL_ROOT/vendor/crawlers"
SRC="$HOME/Downloads/Douyin_TikTok_Download_API-main/crawlers"

if [ ! -d "$SRC" ]; then
  echo "❌ 源码目录不存在: $SRC"
  echo "请从 https://github.com/Evil0ctal/Douyin_TikTok_Download_API 下载最新 zip 解压到 ~/Downloads/"
  exit 1
fi

echo "==> 同步 vendor"
echo "    源: $SRC"
echo "    目标: $VENDOR"

# 复制 douyin/web/
cp "$SRC/douyin/web/"*.py        "$VENDOR/douyin/web/"
cp "$SRC/douyin/web/config.yaml" "$VENDOR/douyin/web/"

# 复制 base_crawler 和 utils/
cp "$SRC/base_crawler.py" "$VENDOR/"
cp "$SRC/utils/"*.py      "$VENDOR/utils/"

# 确保 __init__.py 存在（不覆盖）
touch "$VENDOR/__init__.py"
touch "$VENDOR/douyin/__init__.py"
touch "$VENDOR/douyin/web/__init__.py"
touch "$VENDOR/utils/__init__.py"

echo "==> 同步完成"
echo "==> 记得更新 vendor/README.md 的快照日期"
date "+    今日: %Y-%m-%d"
