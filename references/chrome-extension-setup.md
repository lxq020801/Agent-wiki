# Chrome 扩展安装与文件桥通信指南

> 2026-06-27 定稿。扩展是用户唯一入口，文件桥（Downloads API）是扩展与 Python 端的通信方式。

## 扩展功能

- **配置面板**：填 API Key、Vault 路径、模型选择（豆包 Lite/Mini）、质量档位（均衡/质量）
- **Cookie 抓取**：在抖音网页版登录后，一键抓取 cookie 写入文件桥
- **状态看板**：查看任务队列、进度、成功/失败列表
- **成本统计**：今日拆解花费估算

## 文件结构

```
chrome-extension/
├── manifest.json          # v3 权限声明
├── popup/
│   ├── popup.html         # 主面板（配置 + 状态）
│   ├── popup.css          # 暗色主题样式
│   └── popup.js           # 逻辑：读写文件桥
├── background.js          # 监听下载完成事件
└── icons/
    └── icon-*.png         # 16/32/48/128px（PIL 生成红色圆点）
```

## 关键技术：文件桥通信

扩展**不能直接写文件系统**。走 `chrome.downloads.download` 写到 Downloads 目录，Python 端轮询识别。

### 扩展端（popup.js）

```javascript
const BRIDGE_PREFIX = 'agent-wiki.';  // 注意：不能以 . 开头，Chrome 拒绝

async function writeBridgeFile(filename, content) {
  const blob = new Blob([content], { type: 'text/plain' });
  const url = URL.createObjectURL(blob);
  await chrome.downloads.download({
    url: url,
    filename: filename,           // 如 'agent-wiki.config.json'
    conflictAction: 'overwrite',
    saveAs: false
  });
  URL.revokeObjectURL(url);
}
```

### Python 端（bridge_poller.py）

```python
BRIDGE_PREFIX = "agent-wiki."
DOWNLOADS = Path.home() / "Downloads"

# 轮询 Downloads 目录
for f in DOWNLOADS.iterdir():
    if f.name.startswith(BRIDGE_PREFIX):
        # 识别类型 → 移到 ~/.agent-wiki/
        # 处理成功后删除源文件
```

### 文件名约定

| 扩展写入 | Python 识别 | 目标位置 |
|----------|-------------|----------|
| `agent-wiki.config.json` | `config` | `~/.agent-wiki/config.toml` |
| `agent-wiki.cookie.douyin.txt` | `cookie` | `~/.agent-wiki/cookie/douyin.txt` |
| `agent-wiki.task.{id}.json` | `task` | `~/.agent-wiki/inbox/{id}.json` |

## manifest.json 关键配置

```json
{
  "manifest_version": 3,
  "permissions": [
    "storage",
    "downloads",
    "cookies",        // ← 必须声明，否则 chrome.cookies.getAll 报错
    "activeTab",
    "scripting"
  ],
  "host_permissions": [
    "https://www.douyin.com/*",
    "https://v.douyin.com/*"
  ]
}
```

**注意**：`host_permissions` 必须独立声明，不能放在 `permissions` 数组里。

## 常见安装错误

| 错误 | 原因 | 修复 |
|------|------|------|
| `Could not load icon 'icons/icon-16.png'` | 图标文件缺失 | 用 PIL 生成 4 个尺寸 PNG |
| `Cannot read properties of undefined (reading 'getAll')` | 未声明 `cookies` 权限 | manifest.json 加 `"cookies"` |
| `Invalid filename` | 文件名以 `.` 开头 | 前缀改为 `agent-wiki.` |
| 扩展刷新后配置丢失 | 正常——配置在 storage 里，文件桥是持久化手段 | 点「保存配置」触发文件桥写入 |

## 图标生成（macOS/Linux）

```python
from PIL import Image, ImageDraw

for size in [16, 32, 48, 128]:
    img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    padding = size // 8
    draw.ellipse([padding, padding, size-padding, size-padding], fill=(233, 69, 96, 255))
    if size >= 32:
        highlight = size // 4
        draw.ellipse([highlight, highlight, highlight+size//6, highlight+size//6], fill=(255, 255, 255, 180))
    img.save(f'icon-{size}.png')
```

## 安装步骤

1. Chrome 地址栏输入：`chrome://extensions/`
2. 右上角打开「开发者模式」
3. 点击「加载已解压的扩展程序」
4. 选择：`<repo-root>/chrome-extension/`
5. 刷新扩展（🔄）应用代码更新

## 扩展刷新 vs 重装

- **改代码后**：点扩展卡片上的 🔄 刷新按钮即可，不需要删除重装
- **manifest.json 变更后**：有时需要禁用再启用，或刷新两次
- **完全重装**：删除扩展 → 重新拖目录

## 与 Python 端配合

```
用户点「保存配置」
  → 扩展写 chrome.storage.local（临时）
  → 扩展调 chrome.downloads.download → <download-dir>/agent-wiki.config.json
  → Python bridge_poller.py 轮询 → 识别 → 移到 ~/.agent-wiki/config.toml
  → 删除下载目录里的源文件

用户点「抓取 Cookie」
  → 扩展调 chrome.cookies.getAll({domain: '.douyin.com'})
  → 扩展写 <download-dir>/agent-wiki.cookie.douyin.txt
  → Python bridge_poller.py 处理 → 移到 ~/.agent-wiki/cookie/douyin.txt
```

## 状态看板数据来源

当前版本从 `chrome.storage.local` 读任务历史（简化）。v0.x 应改为读取 `~/.agent-wiki/status/` 下的 JSON 文件，与 Python 端共享状态。

## 架构澄清（2026-06-27 最终对齐）

**扩展角色 = 配置工具**，不直接触发拆解：
- 抓 cookie → 写到 `~/.agent-wiki/cookie/douyin.txt`
- 填配置 → 写到 `~/.agent-wiki/config.toml`
- **不直接调 ingest.py**，拆解由 Agent 触发

**用户流程**：
```
用户发链接给 Agent → Agent 读 SKILL.md → 调 ingest.py → 拆解入库
```

扩展只是准备环境（cookie + 配置），Agent 是触发器。
