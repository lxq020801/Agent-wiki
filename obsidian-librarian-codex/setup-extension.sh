#!/usr/bin/env bash
# setup-extension.sh — 扩展安装指引

set -e

EXT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/chrome-extension"

echo "═══════════════════════════════════════════════════"
echo "  Obsidian Librarian — Chrome 扩展安装"
echo "═══════════════════════════════════════════════════"
echo ""

if [ ! -d "$EXT_DIR" ]; then
    echo "✗ 扩展目录不存在: $EXT_DIR"
    exit 1
fi

echo "安装步骤："
echo ""
echo "1. 打开 Chrome，地址栏输入: chrome://extensions/"
echo "2. 右上角打开「开发者模式」"
echo "3. 点击「加载已解压的扩展程序」"
echo "4. 选择目录: $EXT_DIR"
echo ""
echo "═══════════════════════════════════════════════════"
echo ""
echo "扩展功能："
echo "  • 配置面板：填 API Key、Vault 路径、模型选择"
echo "  • Cookie 抓取：在抖音网页版登录后点击抓取"
echo "  • 状态显示：Agent 连接、配置同步、Cookie 同步"
echo ""
echo "WebSocket 说明："
echo "  扩展通过 ws://127.0.0.1:8765 同步配置和 Cookie"
echo "  抖音链接入库由 Agent 会话触发，不在扩展里触发"
echo ""
echo "═══════════════════════════════════════════════════"
