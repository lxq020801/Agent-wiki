# WebSocket 服务器实现记录

## 实现时间
2026-06-27

## 核心代码

### 服务器框架

```python
import asyncio
import websockets
import json

class LibrarianServer:
    def __init__(self):
        self.clients = set()
        self.config = {}
        self.cookie = {}
        self.tasks = {}
    
    async def handle_client(self, websocket, path):
        self.clients.add(websocket)
        try:
            async for message in websocket:
                data = json.loads(message)
                await self.route_message(websocket, data)
        finally:
            self.clients.remove(websocket)
    
    async def route_message(self, websocket, data):
        msg_type = data.get('type')
        if msg_type == 'handshake':
            await websocket.send(json.dumps({
                'type': 'agent_ready',
                'version': '0.1.0'
            }))
        elif msg_type == 'config_update':
            await self.handle_config_update(data['data'])
            await websocket.send(json.dumps({'type': 'config_synced'}))
        elif msg_type == 'cookie_update':
            await self.handle_cookie_update(data['platform'], data['data'])
            await websocket.send(json.dumps({'type': 'cookie_synced'}))
    
    async def handle_config_update(self, config_data):
        # 保存到文件
        config_path = os.path.expanduser('~/.agent-wiki/config.toml')
        # 注意：这里曾用 import toml，但 toml 包未安装导致崩溃
        # 已降级为字符串写入
        config_text = f"""[ark]
api_key = "{config_data.get('apiKey', '')}"
model = "{config_data.get('model', 'doubao-seed-2-0-lite-260428')}"
quality = "{config_data.get('quality', 'balanced')}"
"""
        with open(config_path, 'w') as f:
            f.write(config_text)
```

## 已知 Bug

### toml 导入崩溃
- **现象**: 收到 config_update 时服务器崩溃（1011 internal error）
- **根因**: `import toml` 失败（Python 3.11 无 toml 包，只有 tomllib 只读）
- **修复**: 降级为字符串拼接写入
- **状态**: 代码已改，未验证

## 验证命令

```bash
# 启动服务器
python3 server/websocket_server.py

# 测试连接
python3 -c "
import asyncio, websockets, json

async def test():
    async with websockets.connect('ws://127.0.0.1:8765') as ws:
        await ws.send(json.dumps({'type': 'handshake'}))
        print(await ws.recv())
        
        await ws.send(json.dumps({
            'type': 'config_update',
            'data': {'apiKey': 'test', 'vaultPath': '/test'}
        }))
        print(await ws.recv())

asyncio.run(test())
"
```

## 后续优化

1. 添加全局异常捕获，防止单条消息崩溃导致连接断开
2. 使用 `tomli_w` 替代字符串拼接（更健壮）
3. 添加心跳检测，防止僵尸连接
4. 实现任务队列，异步处理拆解请求
