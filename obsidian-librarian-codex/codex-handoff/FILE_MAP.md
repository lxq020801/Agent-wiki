# FILE_MAP.md — 核心文件和目录（历史归档，非权威）

> **历史归档 / 非权威资料**
>
> 本文件描述旧迁移时点的文件状态，可能包含已修复 bug 和旧架构口径。
> 当前结构以仓库实际文件、根目录 `SKILL.md` 和 `docs/CODEX_PROJECT_DIRECTION.md` 为准。

## 项目根目录

```
obsidian-librarian/
├── chrome-extension/     # Chrome 扩展（控制塔）
├── server/               # Agent 服务
├── deps/                 # 依赖工具
│   └── douyin/           # 视频拆解
├── docs/                 # 用户文档
├── references/           # 设计决策/踩坑记录
├── templates/            # Markdown 模板
├── rules/                # 知识库规则
├── SKILL.md              # 项目主文档
├── SCHEMA.md             # 数据 schema
├── setup-extension.sh    # 扩展安装脚本
└── codex-handoff/        # 本交接包
```

## 核心文件清单

### 扩展（chrome-extension/）

| 文件 | 用途 | 状态 | 需关注 |
|------|------|------|--------|
| `manifest.json` | 扩展配置（权限、入口） | ✅ | 新增权限需改这里 |
| `background.js` | 后台服务（WebSocket 连接） | ✅ | 断线重连逻辑 |
| `popup/popup.html` | 配置面板 + 状态看板 | ✅ | UI 调整 |
| `popup/popup.css` | 暗色主题样式 | ✅ | 样式调整 |
| `popup/popup.js` | 扩展主逻辑（WebSocket 客户端） | ✅ | **核心文件** |
| `icons/` | 扩展图标（16/32/48/128） | ✅ | 一般不动 |

### 服务器（server/）

| 文件 | 用途 | 状态 | 需关注 |
|------|------|------|--------|
| `websocket_server.py` | WebSocket 服务器 | ⚠️ **有 bug** | **最优先修复** |
| `launcher.py` | 自动启动器（环境检查） | ✅ | 一般不动 |

### 视频拆解（deps/douyin/scripts/）

| 文件 | 用途 | 状态 | 需关注 |
|------|------|------|--------|
| `ingest.py` | 主入口（编排下载+分析） | ✅ | 调整流程 |
| `downloader.py` | 视频下载（vendor + cookie） | ✅ | 一般不动 |
| `analyzer.py` | 视频分析（火山 Files + Responses） | ✅ | 模型/参数调整 |
| `config_loader.py` | 配置加载 | ✅ | 一般不动 |
| `status_writer.py` | 状态写入 | ✅ | 添加 WebSocket 推送 |
| `cost_estimator.py` | 成本估算 | ✅ | 校准公式 |
| `prompts/video_analysis.md` | 拆解 prompt | ✅ | 优化 prompt |

### 文档（docs/）

| 文件 | 用途 | 状态 | 需关注 |
|------|------|------|--------|
| `websocket-protocol.md` | 通信协议规范 | ✅ | 协议变更时更新 |
| `2026-06-26-afternoon-chat-timeline.md` | 方案 C 定稿时间线 | ✅ | 只读 |

### 参考（references/）

| 文件 | 用途 | 状态 | 需关注 |
|------|------|------|--------|
| `architecture-decisions.md` | 架构决策 | ✅ | 只读 |
| `chrome-extension-pitfalls.md` | 扩展踩坑记录 | ✅ | 只读 |
| `douyin-ingest-implementation.md` | 视频拆解实现细节 | ✅ | 只读 |
| `websocket-server-setup.md` | 服务器设置指南 | ✅ | 只读 |

### 模板（templates/）

| 文件 | 用途 | 状态 | 需关注 |
|------|------|------|--------|
| `video_analysis.md` | 视频拆解输出模板 | ✅ | 调整格式 |
| `web_clip.md` | 网页提取模板 | ✅ | 未实现 |
| `github_project.md` | GitHub 项目模板 | ✅ | 未实现 |
| `code_module.md` | 代码模块模板 | ✅ | 未实现 |

## 文件状态图例

| 符号 | 含义 |
|------|------|
| ✅ | 已完成，一般不需修改 |
| ⚠️ | 有 bug 或待完善 |
| 🔄 | 需随功能迭代更新 |
| ⏳ | 未实现 |

## Codex 最应该关注的文件

1. **`server/websocket_server.py`** — 有 bug，最优先修复
2. **`chrome-extension/popup/popup.js`** — 扩展主逻辑，需联调
3. **`deps/douyin/scripts/ingest.py`** — 视频拆解入口，需添加状态推送
4. **`docs/websocket-protocol.md`** — 通信协议，需对照实现
