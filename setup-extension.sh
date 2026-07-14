#!/usr/bin/env bash
# setup-extension.sh — 扩展安装指引

set -e

RUNTIME_EXT_DIR="${AGENT_WIKI_HOME:-$HOME/.agent-wiki}/extension"
SOURCE_EXT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/chrome-extension"
EXT_DIR="$RUNTIME_EXT_DIR"

if [ ! -d "$EXT_DIR" ]; then
    EXT_DIR="$SOURCE_EXT_DIR"
fi

echo "═══════════════════════════════════════════════════"
echo "  Agent-wiki — Chrome 扩展安装"
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
echo "   推荐使用运行目录 ~/.agent-wiki/extension；如不存在，先运行 python3 install/bootstrap.py --skip-install-deps 同步。"
echo ""
echo "═══════════════════════════════════════════════════"
echo ""
echo "扩展功能："
echo "  • 配置面板：填 API Key、Vault 路径、模型选择"
echo "  • Cookie 抓取：在抖音网页版登录后点击抓取"
echo "  • 入库入口：将当前抖音内容或分享链接提交为知识资产"
echo "  • 状态显示：Agent、API、Cookie、知识库和任务进度"
echo ""
echo "WebSocket 说明："
echo "  扩展通过 ws://127.0.0.1:8765 同步配置、Cookie 和入库任务"
echo "  真正下载、分析、写库仍由本地 Agent 工具链执行"
echo ""
echo "═══════════════════════════════════════════════════"
