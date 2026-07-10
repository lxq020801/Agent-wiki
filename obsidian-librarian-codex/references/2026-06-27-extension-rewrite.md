# 扩展重写记录（2026-06-27）

> 历史资料：记录从文件桥/旧扩展口径向当前 WebSocket 方案过渡的过程，不是当前主规范。

## 背景

用户指出扩展开发偏离了昨天下午（6.26）定稿的方案 C。经过对齐，确认方案 C 核心：
- Agent 自动检测/配置环境
- 扩展只负责抓 cookie + 写配置
- 扩展通过 Downloads API 写文件，Agent 自动扫描处理
- 不需要 bridge_poller（已删除）

## 变更内容

### 1. manifest.json
- 保留 downloads 权限（Chrome 扩展唯一能写本地文件的方式）
- 保留 cookies、storage、activeTab、scripting 权限
- 保留 host_permissions（抖音域名）

### 2. popup.js（重写）
- 保留 writeBridgeFile 函数（通过 Downloads API 写文件）
- 保留 grabCookie 函数（多域名尝试策略 + 详细日志）
- 保留 saveConfig 函数（保存配置到 storage + Downloads）
- 移除直接调用 ingest.py 的逻辑
- 状态看板从 storage 读取（简化版）

### 3. background.js（简化）
- 监听下载完成事件，确认文件桥写入成功
- 安装时初始化 storage
- 不处理文件，Agent 自动扫描

### 4. bridge_poller.py（已删除）
- 原职责：轮询 Downloads 目录，处理扩展写入的文件
- 删除原因：Agent 自动扫描，不需要独立脚本

## 方案 C 实现

**扩展职责：**
1. 配置面板 → 保存到 storage + 写 Downloads 文件
2. Cookie 抓取 → 写 Downloads 文件
3. 状态看板 → 显示 Agent 连接状态

**Agent 职责：**
1. 自动检测/配置环境（venv、依赖、目录）
2. 自动扫描 Downloads 目录，发现扩展写入的文件
3. 自动移动到 ~/.agent-wiki/
4. 执行拆解任务

## 文件清单

```
chrome-extension/
├── manifest.json          # 扩展配置（保留 downloads 权限）
├── background.js          # 后台服务（简化）
├── popup/
│   ├── popup.html         # 配置面板 + 状态看板（未变）
│   ├── popup.css          # 暗色主题（未变）
│   └── popup.js           # 配置读写 + Cookie 抓取（重写）
└── icons/                 # 图标（未变）
```

## 验证

- manifest.json：保留 downloads 权限，包含 cookies/storage
- popup.js：包含 writeBridgeFile、grabCookie、saveConfig，使用 chrome.downloads.download
- background.js：监听下载事件，识别 agent-wiki 文件，包含方案 C 注释
- bridge_poller.py：已删除

## 后续工作

1. 写 Agent 自动扫描 Downloads 的代码（替代 bridge_poller）
2. 用户手动安装扩展测试
3. 端到端验证：扩展写文件 → Agent 扫描 → 处理 → 拆解
