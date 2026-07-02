# HANDOFF.md — Obsidian Librarian 项目交接（历史归档，非权威）

> **历史归档 / 非权威资料**
>
> 本文件保留 Hermes 迁移当时的背景，包含已废弃的扩展触发、旧协议、旧 P0 和旧 bug 描述。
> 当前权威入口是项目根目录 `SKILL.md`、`SCHEMA.md`、`docs/CODEX_PROJECT_DIRECTION.md`、
> `docs/websocket-protocol.md`、`docs/ark-video-understanding.md`、`deps/douyin/SKILL.md`。

## 项目目标

Agent-native Knowledge System：浏览器扩展把视频/网页存入 Obsidian 知识库，AI 召回使用。

一句话：扩展点「存」→ WebSocket 实时通信 → Agent 自动处理 → 视频拆解/网页提取 → 写 vault Markdown → 扩展弹「✓ 已入库」。

## 背景

用户 lixinqi，核心场景是爆款短视频拆解复刻。要求「像电灯一样」——安装扩展即全部，零用户操作，不接触命令行/配置文件/路径。

## 当前架构

```
Chrome 扩展 ←——WebSocket——→ Agent 服务器 ←——调用——→ 视频拆解工具
     ↑                                                    ↓
     └──────────── 状态推送 ←────────────────────── 任务完成
```

### 组件

| 组件 | 文件 | 状态 |
|------|------|------|
| Chrome 扩展 | `chrome-extension/` | 代码完整，未端到端测试 |
| WebSocket 服务器 | `server/websocket_server.py` | 有 bug，config_update 时崩溃 |
| 自动启动器 | `server/launcher.py` | 可用 |
| 视频拆解 | `deps/douyin/scripts/` | 完整可用 |

### 通信

- **地址**: `ws://127.0.0.1:8765`
- **协议**: JSON 消息（handshake/config_update/cookie_update/task_request/task_update/task_complete）

## 当前进度

### 已完成
- 视频拆解核心（下载 + 分析 + 入库）
- WebSocket 服务器框架（连接管理 + 消息路由）
- 扩展控制塔（配置面板 + 状态看板 + Cookie 抓取）
- 自动启动器（环境检查 + 依赖安装）
- 通信协议文档

### 最后卡住的问题

**WebSocket 服务器 toml 导入 bug**
- 位置: `server/websocket_server.py` 第 98 行 `handle_config_update`
- 现象: 收到 config_update 时崩溃（1011 internal error），连接断开
- 原因: `import toml` 失败（未安装 toml 包）
- 修复尝试: 已改为字符串写入 + 降级处理，但未验证是否生效
- 验证方法: 启动服务器 → 用测试脚本发送 config_update → 看是否还崩溃

### 下一步建议

1. **修复并验证 WebSocket 服务器**（P0）
   - 确认 toml 问题是否已修复
   - 如果不修复，改用 `tomli`（Python 3.11 内置）或纯字符串写入

2. **扩展-服务器端到端测试**（P0）
   - 启动服务器
   - 加载扩展
   - 验证连接状态变绿
   - 验证 Cookie 抓取和同步

3. **任务状态实时推送**（P1）
   - Agent 拆解时推送进度到扩展

4. **系统通知**（P1）
   - 任务完成/失败时 macOS 通知

---

## 当前 git 状态

```
42edd35 feat: 实现 WebSocket 实时通信 + 扩展控制塔 + 自动启动服务
```

- 有未提交变更: `SKILL.md`（修改中）
- 未跟踪文件: `chrome-extension/.DS_Store`, `references/project-archive-guide.md`, `references/websocket-server-implementation.md`

## 最近修改过的文件

- `SKILL.md` — 重写为 v0.1 架构
- `server/websocket_server.py` — 新增 WebSocket 服务器
- `server/launcher.py` — 新增自动启动器
- `chrome-extension/` — 新增扩展代码
- `docs/websocket-protocol.md` — 新增协议文档

## 哪些文件不应该迁移

- `.DS_Store` — 系统文件
- `__pycache__/` — 编译缓存
- `deps/douyin/.venv/` — Python 虚拟环境（133M，需重新创建）
- `deps/douyin/logs/` — 运行时日志
- 任何包含 cookie/API key/token 的文件

## 哪些文件不应该提交到 git

- `.DS_Store`
- `__pycache__/`（已在 .gitignore）
- `.venv/`（已在 .gitignore）
- `logs/`（已在 .gitignore）
- 敏感配置文件

## Codex 接手后最应该先看的 3-5 个文件

1. `HANDOFF.md` — 本文档
2. `server/websocket_server.py` — 有 bug 需修复
3. `chrome-extension/popup/popup.js` — 扩展主逻辑
4. `deps/douyin/scripts/ingest.py` — 视频拆解入口
5. `docs/websocket-protocol.md` — 通信协议

## Codex 接手后第一步应该运行的命令

```bash
# 1. 进入项目目录
cd ~/.hermes/skills/obsidian-librarian

# 2. 启动 WebSocket 服务器（看是否还崩溃）
python3 server/websocket_server.py

# 3. 另一个终端，测试连接
python3 -c "
import asyncio, websockets, json

async def test():
    async with websockets.connect('ws://127.0.0.1:8765') as ws:
        await ws.send(json.dumps({'type': 'handshake'}))
        print('handshake:', await ws.recv())
        
        await ws.send(json.dumps({
            'type': 'config_update',
            'data': {'apiKey': 'test', 'vaultPath': '/test'}
        }))
        print('config:', await ws.recv())

asyncio.run(test())
"

# 4. 检查配置文件是否写入
cat ~/.obsidian-librarian/config.toml
```

如果 config_update 不崩溃且文件写入成功，bug 已修复。

---

> 交接时间: 2026-06-27
> 交接者: Hermes Agent
> 项目路径: /Users/lixinqi/.hermes/skills/obsidian-librarian/
