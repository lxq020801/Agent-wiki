# RUNBOOK.md — 本地运行、调试、验证指南（历史归档，非权威）

> **历史归档 / 非权威资料**
>
> 本文件是旧交接包里的运行手册，包含已过期命令和旧协议示例。
> 当前运行方式以根目录 `SKILL.md`、`deps/douyin/SKILL.md` 和当前 `docs/` 为准。

## 环境要求

- macOS（用户环境）
- Python 3.11+
- Chrome 浏览器
- ffmpeg（视频时长检测）

## 1. 启动服务

### 方式 A：自动启动（推荐）

```bash
cd ~/.hermes/skills/obsidian-librarian
python3 server/launcher.py
```

会自动：
1. 检查虚拟环境（不存在则创建）
2. 安装依赖（requirements.txt）
3. 创建必要目录
4. 启动 WebSocket 服务器

### 方式 B：手动启动

```bash
cd ~/.hermes/skills/obsidian-librarian
python3 server/websocket_server.py
```

输出：
```
[Server] 启动 WebSocket 服务器: ws://127.0.0.1:8765
[Server] 服务器已启动，等待连接...
```

---

## 2. 加载扩展

1. 打开 Chrome → 设置 → 扩展程序
2. 开启「开发者模式」（右上角）
3. 点击「加载未打包的扩展程序」
4. 选择 `~/.hermes/skills/obsidian-librarian/chrome-extension/` 目录
5. 扩展图标应出现在工具栏

---

## 3. 测试连接

### 测试 1：WebSocket 连接

```bash
python3 -c "
import asyncio, websockets, json

async def test():
    async with websockets.connect('ws://127.0.0.1:8765') as ws:
        # 发送握手
        await ws.send(json.dumps({'type': 'handshake'}))
        msg = await ws.recv()
        print('收到:', msg)

asyncio.run(test())
"
```

期望输出：
```json
{"type": "agent_ready", "version": "0.1.0", "capabilities": ["download", "analyze", "index"]}
```

### 测试 2：配置更新（关键！验证 bug 是否修复）

```bash
python3 -c "
import asyncio, websockets, json

async def test():
    async with websockets.connect('ws://127.0.0.1:8765') as ws:
        await ws.send(json.dumps({'type': 'handshake'}))
        await ws.recv()
        
        # 发送配置更新
        await ws.send(json.dumps({
            'type': 'config_update',
            'data': {
                'apiKey': 'test-key-123',
                'vaultPath': '/test/path',
                'model': 'doubao-seed-2-0-lite-260428',
                'quality': 'balanced'
            }
        }))
        msg = await ws.recv()
        print('收到:', msg)
        
        # 检查文件是否写入
        import os
        config_path = os.path.expanduser('~/.obsidian-librarian/config.toml')
        if os.path.exists(config_path):
            print('✓ 配置文件已写入')
            with open(config_path) as f:
                print(f.read())
        else:
            print('✗ 配置文件未写入')

asyncio.run(test())
"
```

期望：不崩溃，收到 config_synced，文件写入成功。

### 测试 3：Cookie 更新

```bash
python3 -c "
import asyncio, websockets, json

async def test():
    async with websockets.connect('ws://127.0.0.1:8765') as ws:
        await ws.send(json.dumps({'type': 'handshake'}))
        await ws.recv()
        
        await ws.send(json.dumps({
            'type': 'cookie_update',
            'platform': 'douyin',
            'data': 'test_cookie_data'
        }))
        msg = await ws.recv()
        print('收到:', msg)

asyncio.run(test())
"
```

---

## 4. 扩展端到端测试

1. **启动服务器**（见上文）
2. **加载扩展**（见上文）
3. **点击扩展图标**
   - 期望：状态灯显示 🟢 Agent 已连接
4. **填写配置**
   - API Key: 填写你的火山方舟 key
   - Vault 路径: 填写 Obsidian 目录
   - 点击「保存配置」
   - 期望：显示「✓ 配置已同步」
5. **抓取 Cookie**
   - 登录抖音网页版
   - 点击扩展「抓取抖音 Cookie」
   - 期望：显示「✓ Cookie 已同步」
6. **检查文件**
   ```bash
   cat ~/.obsidian-librarian/config.toml
   cat ~/.obsidian-librarian/cookie/douyin.txt
   ```

---

## 5. 视频拆解测试

```bash
cd ~/.hermes/skills/obsidian-librarian/deps/douyin
source .venv/bin/activate

# 测试拆解（需要真实 API key 和 cookie）
python3 scripts/ingest.py "https://v.douyin.com/xxxxx"
```

---

## 6. 检查日志

### 服务器日志

```bash
# 前台启动时直接看输出
python3 server/websocket_server.py

# 后台启动时看日志文件
tail -f ~/.obsidian-librarian/logs/server.log
```

### 扩展日志

1. 右键扩展图标 → 检查弹出内容 → Console
2. 或：chrome://extensions/ → 详情 → 检查背景页 → Console

### 任务日志

```bash
ls ~/.obsidian-librarian/logs/tasks/
cat ~/.obsidian-librarian/logs/tasks/2026-06-27-xxxxx.log
```

---

## 7. 复现问题

### 问题：WebSocket 服务器崩溃

复现步骤：
1. 启动服务器：`python3 server/websocket_server.py`
2. 运行测试 2（配置更新）
3. 观察是否输出 `1011 (internal error)`

修复验证：
1. 修改 `server/websocket_server.py`
2. 重新启动服务器
3. 再次运行测试 2
4. 确认不崩溃且配置文件写入

---

## 8. 常用命令速查

| 命令 | 用途 |
|------|------|
| `python3 server/launcher.py` | 自动启动服务 |
| `python3 server/websocket_server.py` | 手动启动服务器 |
| `tail -f ~/.obsidian-librarian/logs/server.log` | 查看服务器日志 |
| `cat ~/.obsidian-librarian/config.toml` | 查看配置 |
| `cat ~/.obsidian-librarian/cookie/douyin.txt` | 查看 cookie |
| `ls ~/.obsidian-librarian/status/` | 查看任务状态 |
| `chrome://extensions/` | 管理扩展 |

---

## 9. 调试技巧

### 扩展调试
- 右键扩展图标 → 检查弹出内容 → Console
- 过滤日志：搜索 `[Librarian]`
- 背景页：chrome://extensions/ → 详情 → 检查背景页

### 服务器调试
- 前台启动看实时输出
- 添加 `print()` 或 `logging`
- 使用 `pdb` 断点调试

### 网络调试
- 使用 `websockets` 客户端测试
- 检查端口占用：`lsof -i :8765`
- 检查防火墙
