# WebSocket 服务器设置与验证

> Agent 端 WebSocket 服务器的启动、验证和常见问题。

---

## 启动方式

### 方式 1：自动启动器（推荐）

```bash
cd ~/.hermes/skills/agent-wiki
python3 server/launcher.py
```

自动完成：
1. 检查虚拟环境（不存在则创建）
2. 安装依赖（requirements.txt）
3. 创建必要目录
4. 启动 WebSocket 服务器

### 方式 2：手动启动

```bash
cd ~/.hermes/skills/agent-wiki
python3 server/websocket_server.py
```

要求：
- `websockets` 已安装 (`pip install websockets`)
- 端口 8765 未被占用

---

## 验证连接

### Python 客户端测试

```python
import asyncio
import websockets
import json

async def test():
    async with websockets.connect('ws://127.0.0.1:8765') as ws:
        # 等待 agent_ready
        msg = await ws.recv()
        print(f"收到: {msg}")
        
        # 发送握手
        await ws.send(json.dumps({
            'type': 'handshake',
            'client': 'test',
            'version': '0.1'
        }))
        
        # 发送配置
        await ws.send(json.dumps({
            'type': 'config_update',
            'data': {'apiKey': 'test', 'vaultPath': '/test'}
        }))
        msg = await ws.recv()
        print(f"收到: {msg}")

asyncio.run(test())
```

### 扩展端验证

1. 打开 Chrome 扩展
2. 查看状态灯：
   - 🔴 红色 = Agent 未连接
   - 🟢 绿色 = Agent 已连接
3. 打开 Chrome DevTools → Console → 查看 `[Librarian]` 日志

---

## 常见问题

### 端口被占用

```bash
# 检查端口占用
lsof -i :8765

# 杀掉占用进程
kill -9 <PID>
```

### 防火墙拦截

macOS 可能会弹窗询问是否允许 Python 接受传入连接。点击「允许」。

### 扩展显示「Agent 未连接」

排查步骤：
1. 服务器是否运行？`lsof -i :8765`
2. 扩展是否重新加载？Chrome → 扩展 → 刷新
3. 查看扩展 Console 日志：`chrome-extension://<id>/popup/popup.html`

---

## 作为系统服务（macOS launchd）

### 创建 plist

```bash
cat > ~/Library/LaunchAgents/com.agent-wiki.plist << 'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.agent-wiki</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/bin/python3</string>
        <string>~/.hermes/skills/agent-wiki/server/launcher.py</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>~/.agent-wiki/logs/server.log</string>
    <key>StandardErrorPath</key>
    <string>~/.agent-wiki/logs/server.error.log</string>
</dict>
</plist>
EOF
```

### 加载服务

```bash
launchctl load ~/Library/LaunchAgents/com.agent-wiki.plist
launchctl start com.agent-wiki
```

### 查看状态

```bash
launchctl list | grep agent-wiki
```

---

## 日志位置

- 服务器日志：`~/.agent-wiki/logs/server.log`
- 错误日志：`~/.agent-wiki/logs/server.error.log`
- 扩展日志：Chrome DevTools → Console

---

## 协议速查

| 消息 | 方向 | 用途 |
|------|------|------|
| `handshake` | 扩展 → Agent | 连接握手 |
| `agent_ready` | Agent → 扩展 | Agent 就绪 |
| `config_update` | 扩展 → Agent | 发送配置 |
| `config_synced` | Agent → 扩展 | 配置已同步 |
| `cookie_update` | 扩展 → Agent | 发送 cookie |
| `cookie_synced` | Agent → 扩展 | cookie 已同步 |
| `task_request` | 扩展 → Agent | 请求拆解 |
| `task_update` | Agent → 扩展 | 任务进度 |
| `task_complete` | Agent → 扩展 | 任务完成 |
| `task_error` | Agent → 扩展 | 任务失败 |

详见 `docs/websocket-protocol.md`
