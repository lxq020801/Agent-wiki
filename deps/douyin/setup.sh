#!/usr/bin/env bash
# setup.sh — 抖音视频拆解工具的环境初始化
#
# 做什么：
#   1. 检查 Python 3.11+
#   2. 创建独立 venv（与宿主环境隔离，避免 httpx 等依赖版本冲突）
#   3. 安装 requirements.txt
#   4. 检查 ffmpeg
#   5. 初始化 ~/.agent-wiki/ 目录结构 + config.toml 模板
#
# 用户视角：跑一次就完。Agent 首次激活也会自动跑这个。

set -euo pipefail

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$SKILL_DIR/.venv"
BRIDGE="$HOME/.agent-wiki"

echo "[1/5] 检查 Python..."
# 找一个 3.11+ 的 python
PYTHON=""
for cand in python3.13 python3.12 python3.11; do
    if command -v "$cand" >/dev/null 2>&1; then
        PYTHON="$cand"
        break
    fi
done
if [ -z "$PYTHON" ]; then
    echo "✗ 找不到 Python 3.11+。"
    echo "  macOS: brew install python@3.11"
    exit 1
fi
echo "  ✓ 使用 $($PYTHON --version) ($PYTHON)"

echo "[2/5] 创建独立 venv..."
if [ ! -d "$VENV" ]; then
    "$PYTHON" -m venv "$VENV"
    echo "  ✓ 创建 $VENV"
else
    echo "  · 已存在 $VENV"
fi

echo "[3/5] 安装依赖..."
"$VENV/bin/pip" install -q --upgrade pip
"$VENV/bin/pip" install -q -r "$SKILL_DIR/requirements.txt"
echo "  ✓ 依赖就绪"

echo "[4/5] 检查 ffmpeg..."
if ! command -v ffprobe >/dev/null 2>&1; then
    echo "  ✗ 未找到 ffprobe（视频时长检测要用）"
    if command -v brew >/dev/null 2>&1; then
        echo "    正在 brew install ffmpeg..."
        brew install ffmpeg
    else
        echo "    请安装 ffmpeg："
        echo "      macOS:  brew install ffmpeg"
        echo "      Linux:  sudo apt install ffmpeg"
        exit 1
    fi
fi
echo "  ✓ $(ffprobe -version 2>&1 | head -1)"

echo "[5/5] 初始化 ~/.agent-wiki/..."
mkdir -p "$BRIDGE"/{inbox,status,archive,failed,cookie,cache/videos,handshake}
if [ ! -f "$BRIDGE/config.toml" ]; then
    "$VENV/bin/python" "$SKILL_DIR/scripts/config_loader.py" init
fi
echo "  ✓ 桥接目录就绪：$BRIDGE"

echo ""
echo "═══════════════════════════════════════════════════"
echo "✓ 环境就绪"
echo ""
echo "下一步："
echo "  1. 编辑 $BRIDGE/config.toml"
echo "     在 [ark].api_key 填入火山 API Key"
echo "     在 [vault].path 填入 Obsidian 仓库路径"
echo "  2. 把 cookie 放到 $BRIDGE/cookie/douyin.txt（或等扩展抓取）"
echo "  3. 测试：$VENV/bin/python $SKILL_DIR/scripts/ingest.py --url <抖音链接>"
echo "═══════════════════════════════════════════════════"
