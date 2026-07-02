import asyncio
import json
import logging
import os
import sys
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

# 添加项目路径
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "deps" / "douyin" / "scripts"))

from install.vault_discovery import (
    discover_vault,
    score_vault,
    write_vault_path_to_config,
)


def _toml_escape(value):
    return str(value or "").replace("\\", "\\\\").replace('"', '\\"')


def log(message):
    try:
        print(message, flush=True)
    except BrokenPipeError:
        pass


def _is_connection_closed(exc):
    return exc.__class__.__name__ in {
        "ConnectionClosed",
        "ConnectionClosedOK",
        "ConnectionClosedError",
    }


def default_runtime_root():
    raw = os.environ.get("OBSIDIAN_LIBRARIAN_HOME")
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".obsidian-librarian"


PROVIDERS = {
    "doubao": {
        "label": "豆包 / 火山方舟 API",
        "section": "ark",
        "api_key_fields": ("arkApiKey", "doubaoApiKey", "apiKey"),
        "endpoint_fields": ("arkEndpoint", "doubaoEndpoint", "endpoint"),
        "endpoint": "https://ark.cn-beijing.volces.com/api/v3",
        "model": "doubao-seed-2-0-lite-260428",
        "fallback": "doubao-seed-2-0-mini-260428",
    },
    "volcengine_agent_plan": {
        "label": "火山 Agent Plan",
        "section": "agent_plan",
        "api_key_fields": ("agentPlanApiKey", "agent_plan_api_key", "planApiKey", "apiKey"),
        "endpoint_fields": ("agentPlanEndpoint", "agent_plan_endpoint", "planEndpoint", "endpoint"),
        "endpoint": "https://ark.cn-beijing.volces.com/api/plan/v3",
        "model": "doubao-seed-2.0-lite",
        "fallback": "doubao-seed-2.0-mini",
    },
}
DEFAULT_PROVIDER = "doubao"
DEFAULT_ARK_ENDPOINT = PROVIDERS[DEFAULT_PROVIDER]["endpoint"]
TRUSTED_ARK_HOSTS = {"ark.cn-beijing.volces.com"}


def _simple_config_value(config_path, section, key, default=""):
    if not config_path.exists():
        return default
    current = ""
    for raw in config_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            current = line.strip("[]").strip()
            continue
        if current != section or "=" not in line:
            continue
        name, value = line.split("=", 1)
        if name.strip() != key:
            continue
        value = value.strip().split(" #", 1)[0].strip()
        if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
            value = value[1:-1]
        return value
    return default


def _json_file(path):
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _normalize_provider(value):
    value = str(value or "").strip().lower()
    aliases = {
        "doubao_api": "doubao",
        "ark": "doubao",
        "ark_api": "doubao",
        "normal_ark": "doubao",
        "agent_plan": "volcengine_agent_plan",
        "agentplan": "volcengine_agent_plan",
        "volcengine-agent-plan": "volcengine_agent_plan",
        "volcengine_agent": "volcengine_agent_plan",
        "ark_agent_plan": "volcengine_agent_plan",
    }
    value = aliases.get(value, value)
    return value if value in PROVIDERS else DEFAULT_PROVIDER


def _provider_default(provider, key):
    return PROVIDERS[_normalize_provider(provider)][key]


def _provider_section(provider):
    return _provider_default(provider, "section")


def _provider_api_key(config_path, provider):
    return _simple_config_value(config_path, _provider_section(provider), "api_key")


def _provider_endpoint(config_path, provider):
    return _safe_ark_endpoint(
        _simple_config_value(config_path, _provider_section(provider), "endpoint"),
        provider,
    )


def _first_config_value(config_data, fields):
    for field in fields:
        if field in config_data:
            return config_data.get(field)
    return None


def _incoming_api_key(config_data, provider, existing=""):
    incoming = _first_config_value(config_data, _provider_default(provider, "api_key_fields"))
    return existing if incoming is None else incoming


def _incoming_endpoint(config_data, provider, existing=""):
    incoming = _first_config_value(config_data, _provider_default(provider, "endpoint_fields"))
    return _safe_ark_endpoint(incoming if incoming is not None else existing, provider)


def _safe_ark_endpoint(value, provider=None):
    expected = _provider_default(provider or DEFAULT_PROVIDER, "endpoint")
    endpoint = value or expected
    parsed = urllib.parse.urlparse(endpoint)
    if parsed.scheme != "https" or parsed.hostname not in TRUSTED_ARK_HOSTS:
        return expected
    normalized = endpoint.rstrip("/")
    if provider and normalized not in {
        PROVIDERS["doubao"]["endpoint"],
        PROVIDERS["volcengine_agent_plan"]["endpoint"],
    }:
        return expected
    return normalized


def _candidate_payload(candidate):
    return {
        'score': candidate.score,
        'path': candidate.path,
        'source': candidate.source,
        'reasons': candidate.reasons,
    }


def _origin_allowed(websocket):
    headers = getattr(websocket, "request_headers", None)
    if not headers:
        return True
    origin = headers.get("Origin")
    if not origin:
        return True
    return origin.startswith("chrome-extension://")


class LibrarianServer:
    """Obsidian Librarian WebSocket 服务器
    
    职责：
      1. 接收扩展发送的配置和 cookie
      2. 写入 Agent 工具链可直接读取的运行时文件
      3. 维护与扩展的控制面长连接

    P0 不从扩展触发入库任务。用户发送抖音链接给 Agent 后，由
    scripts/ingest_url.py 调用 deps/douyin/scripts/ingest.py 完成业务链路。
    """
    
    def __init__(self, host='127.0.0.1', port=8765):
        self.host = host
        self.port = port
        self.clients = set()  # 所有连接的扩展客户端
        self.config = None  # 当前配置
        self.cookie = None  # 当前 cookie
        self.runtime_root = default_runtime_root()
        
    async def handle_client(self, websocket):
        """处理单个客户端连接"""
        if not _origin_allowed(websocket):
            log("[Server] 拒绝非扩展 Origin 的 WebSocket 连接")
            await websocket.close(code=1008, reason="origin_not_allowed")
            return

        self.clients.add(websocket)
        client_info = f"{websocket.remote_address}"
        log(f"[Server] 客户端连接: {client_info}")
        
        try:
            # 发送就绪消息
            await websocket.send(json.dumps({
                'type': 'agent_ready',
                'version': '0.1.0',
                'capabilities': [
                    'config_sync',
                    'cookie_sync',
                    'vault_discovery',
                    'model_health_check',
                ]
            }))
            await websocket.send(json.dumps({
                'type': 'status_snapshot',
                'status': await asyncio.to_thread(self.status_snapshot),
                'timestamp': datetime.now().isoformat()
            }, ensure_ascii=False))
            
            # 持续接收消息
            async for message in websocket:
                try:
                    msg = json.loads(message)
                    await self.handle_message(websocket, msg)
                except json.JSONDecodeError:
                    log(f"[Server] 收到无效 JSON，长度: {len(message)}")
                    await websocket.send(json.dumps({
                        'type': 'error',
                        'error': 'Invalid JSON'
                    }))
                except Exception as e:
                    if _is_connection_closed(e):
                        break
                    log(f"[Server] 消息处理失败: {type(e).__name__}: {e}")
                    await websocket.send(json.dumps({
                        'type': 'error',
                        'error': type(e).__name__
                    }))
                    
        except Exception as exc:
            if not _is_connection_closed(exc):
                raise
        finally:
            log(f"[Server] 客户端断开: {client_info}")
            self.clients.discard(websocket)
            
    async def handle_message(self, websocket, msg):
        """处理客户端消息"""
        msg_type = msg.get('type')
        
        if msg_type == 'handshake':
            log(f"[Server] 握手: {msg.get('client')} v{msg.get('version')}")
            
        elif msg_type == 'config_update':
            await self.handle_config_update(msg.get('data', {}))
            await websocket.send(json.dumps({
                'type': 'config_synced',
                'timestamp': datetime.now().isoformat()
            }))
            
        elif msg_type == 'cookie_update':
            await self.handle_cookie_update(msg.get('platform'), msg.get('data'))
            await websocket.send(json.dumps({
                'type': 'cookie_synced',
                'platform': msg.get('platform'),
                'timestamp': datetime.now().isoformat()
            }))

        elif msg_type == 'status_request':
            status = await asyncio.to_thread(self.status_snapshot)
            await websocket.send(json.dumps({
                'type': 'status_snapshot',
                'status': status,
                'timestamp': datetime.now().isoformat()
            }, ensure_ascii=False))

        elif msg_type == 'vault_discover':
            status = await asyncio.to_thread(self.discover_and_persist_vault, msg.get('hint', ''))
            await websocket.send(json.dumps({
                'type': 'vault_status',
                'status': status,
                'timestamp': datetime.now().isoformat()
            }, ensure_ascii=False))

        elif msg_type == 'vault_pick':
            status = await asyncio.to_thread(self.pick_vault_folder)
            await websocket.send(json.dumps({
                'type': 'vault_status',
                'status': status,
                'timestamp': datetime.now().isoformat()
            }, ensure_ascii=False))

        elif msg_type == 'model_check':
            status = await self.check_model_health(msg.get('data') or {})
            await websocket.send(json.dumps({
                'type': 'model_status',
                'status': status,
                'timestamp': datetime.now().isoformat()
            }, ensure_ascii=False))
            
        elif msg_type == 'task_request':
            await websocket.send(json.dumps({
                'type': 'task_rejected',
                'reason': 'extension_task_trigger_deferred',
                'message': 'P0 ingest runs from Agent via scripts/ingest_url.py',
                'timestamp': datetime.now().isoformat()
            }))
            
        else:
            log(f"[Server] 未知消息类型: {msg_type}")
            
    async def handle_config_update(self, config_data):
        """处理配置更新"""
        log(f"[Server] 收到配置更新: {list(config_data.keys())}")
        self.config = config_data
        
        # 保存完整 TOML（供 config_loader.py / ingest.py 读取）
        config_path = self.runtime_root / 'config.toml'
        config_path.parent.mkdir(parents=True, exist_ok=True)

        previous_provider = _normalize_provider(
            _simple_config_value(config_path, 'provider', 'active', DEFAULT_PROVIDER)
        )
        provider = _normalize_provider(
            config_data.get('provider')
            or previous_provider
        )
        existing_doubao_api_key = _provider_api_key(config_path, 'doubao')
        existing_agent_plan_api_key = _provider_api_key(config_path, 'volcengine_agent_plan')
        existing_doubao_endpoint = _provider_endpoint(config_path, 'doubao')
        existing_agent_plan_endpoint = _provider_endpoint(config_path, 'volcengine_agent_plan')
        existing_model = _simple_config_value(
            config_path,
            'models',
            'analyzer',
            _provider_default(provider, 'model'),
        )
        existing_fallback = _simple_config_value(
            config_path,
            'models',
            'analyzer_fallback',
            _provider_default(provider, 'fallback'),
        )
        existing_vault_path = _simple_config_value(config_path, 'vault', 'path')

        incoming_vault = config_data.get('vaultPath') or config_data.get('vault_path') or ''
        vault_path = existing_vault_path or ''
        if incoming_vault:
            candidate = score_vault(Path(incoming_vault).expanduser(), source='config_update')
            if candidate:
                vault_path = candidate.path
            else:
                discovery = discover_vault(
                    config_path=config_path,
                    user_hint=incoming_vault,
                    cwd=PROJECT_ROOT,
                    runtime_root=self.runtime_root,
                )
                if discovery.selected:
                    vault_path = discovery.selected.path
        if not vault_path:
            discovery = discover_vault(
                config_path=config_path,
                cwd=PROJECT_ROOT,
                runtime_root=self.runtime_root,
            )
            if discovery.selected:
                vault_path = discovery.selected.path

        quality = 'quality'
        model = config_data.get('model') or config_data.get('modelId')
        if not model:
            model = existing_model if provider == previous_provider else _provider_default(provider, 'model')
        fallback_model = config_data.get('fallbackModel')
        if not fallback_model:
            fallback_model = existing_fallback if provider == previous_provider else _provider_default(provider, 'fallback')

        doubao_api_key = _incoming_api_key(
            config_data,
            'doubao',
            existing_doubao_api_key,
        ) if provider == 'doubao' else (
            _first_config_value(config_data, ('arkApiKey', 'doubaoApiKey')) or existing_doubao_api_key
        )
        agent_plan_api_key = _incoming_api_key(
            config_data,
            'volcengine_agent_plan',
            existing_agent_plan_api_key,
        ) if provider == 'volcengine_agent_plan' else (
            _first_config_value(config_data, ('agentPlanApiKey', 'agent_plan_api_key', 'planApiKey'))
            or existing_agent_plan_api_key
        )
        doubao_endpoint = _safe_ark_endpoint(
            _first_config_value(config_data, ('arkEndpoint', 'doubaoEndpoint'))
            or (config_data.get('endpoint') if provider == 'doubao' else None)
            or existing_doubao_endpoint,
            'doubao',
        )
        agent_plan_endpoint = _safe_ark_endpoint(
            _first_config_value(config_data, ('agentPlanEndpoint', 'agent_plan_endpoint', 'planEndpoint'))
            or (config_data.get('endpoint') if provider == 'volcengine_agent_plan' else None)
            or existing_agent_plan_endpoint,
            'volcengine_agent_plan',
        )
        config_text = f"""[ark]
api_key = "{_toml_escape(doubao_api_key)}"
endpoint = "{_toml_escape(doubao_endpoint)}"

[agent_plan]
api_key = "{_toml_escape(agent_plan_api_key)}"
endpoint = "{_toml_escape(agent_plan_endpoint)}"

[provider]
active = "{_toml_escape(provider)}"

[models]
analyzer = "{_toml_escape(model)}"
analyzer_fallback = "{_toml_escape(fallback_model)}"

[analysis]
default_quality = "{_toml_escape(quality)}"
balanced_target_frames = 240
quality_target_frames = 1250
fps_min = 0.2
fps_max = 5.0
file_active_timeout_sec = 120

[douyin]
cookie_path = "{_toml_escape(str(self.runtime_root / 'cookie' / 'douyin.txt'))}"

[vault]
path = "{_toml_escape(vault_path)}"
relative_root = "知识资产/视频分析"

[server]
enabled = true
host = "{_toml_escape(self.host)}"
port = {int(self.port)}
"""

        with open(config_path, 'w') as f:
            f.write(config_text)
        os.chmod(config_path, 0o600)
        log(f"[Server] 配置已保存到 {config_path}")

    def config_path(self):
        return self.runtime_root / 'config.toml'

    def status_path(self, name):
        return self.runtime_root / 'status' / name

    def vault_status(self):
        config_path = self.config_path()
        configured = _simple_config_value(config_path, 'vault', 'path')
        if configured:
            candidate = score_vault(Path(configured).expanduser(), source='config.toml')
            if candidate:
                return {
                    'ok': True,
                    'state': 'ready',
                    'path': candidate.path,
                    'source': candidate.source,
                    'score': candidate.score,
                    'reasons': candidate.reasons,
                }

        discovery = discover_vault(
            config_path=config_path,
            cwd=PROJECT_ROOT,
            runtime_root=self.runtime_root,
        )
        if discovery.selected:
            write_vault_path_to_config(config_path, discovery.selected.path_obj)
            return {
                'ok': True,
                'state': 'ready',
                'path': discovery.selected.path,
                'source': discovery.selected.source,
                'score': discovery.selected.score,
                'reasons': discovery.selected.reasons,
            }
        return {
            'ok': False,
            'state': 'missing',
            'path': configured or '',
            'source': '',
            'score': 0,
            'reasons': [],
        }

    def model_config_status(self):
        config_path = self.config_path()
        provider = _normalize_provider(_simple_config_value(config_path, 'provider', 'active', DEFAULT_PROVIDER))
        api_key = _provider_api_key(config_path, provider)
        model = _simple_config_value(config_path, 'models', 'analyzer', _provider_default(provider, 'model'))
        endpoint = _provider_endpoint(config_path, provider)
        last = _json_file(self.status_path('model_health.json')) or {}
        if not api_key:
            return {
                'ok': False,
                'state': 'missing',
                'provider': provider,
                'providerLabel': _provider_default(provider, 'label'),
                'model': model,
                'endpoint': endpoint,
                'checkedAt': '',
                'message': f"缺少 {_provider_default(provider, 'label')} API Key",
            }
        last_matches = (
            last
            and _normalize_provider(last.get('provider', provider)) == provider
            and last.get('model', model) == model
            and last.get('endpoint', endpoint) == endpoint
        )
        if not last_matches:
            return {
                'ok': False,
                'state': 'configured',
                'provider': provider,
                'providerLabel': _provider_default(provider, 'label'),
                'model': model,
                'endpoint': endpoint,
                'checkedAt': '',
                'message': '已配置，等待检查',
            }
        return {
            'ok': bool(last.get('ok')),
            'state': last.get('state', 'error'),
            'provider': last.get('provider', provider),
            'providerLabel': _provider_default(last.get('provider', provider), 'label'),
            'model': last.get('model', model),
            'endpoint': endpoint,
            'checkedAt': last.get('checkedAt', ''),
            'message': last.get('message', '已配置，等待检查'),
        }

    def cookie_status(self):
        cookie_path = self.runtime_root / 'cookie' / 'douyin.txt'
        if cookie_path.exists() and cookie_path.stat().st_size > 0:
            return {
                'ok': True,
                'state': 'ready',
                'platform': 'douyin',
                'updatedAt': datetime.fromtimestamp(cookie_path.stat().st_mtime).isoformat(),
            }
        return {
            'ok': False,
            'state': 'missing',
            'platform': 'douyin',
            'updatedAt': '',
        }

    def status_snapshot(self):
        return {
            'vault': self.vault_status(),
            'model': self.model_config_status(),
            'cookie': self.cookie_status(),
        }

    def discover_and_persist_vault(self, hint=''):
        discovery = discover_vault(
            config_path=self.config_path(),
            user_hint=hint or '',
            cwd=PROJECT_ROOT,
            runtime_root=self.runtime_root,
        )
        if discovery.selected:
            write_vault_path_to_config(self.config_path(), discovery.selected.path_obj)
            return {
                'ok': True,
                'state': 'ready',
                'path': discovery.selected.path,
                'source': discovery.selected.source,
                'score': discovery.selected.score,
                'reasons': discovery.selected.reasons,
                'candidates': [_candidate_payload(c) for c in discovery.candidates[:5]],
            }
        return {
            'ok': False,
            'state': 'missing',
            'path': '',
            'source': '',
            'score': 0,
            'reasons': [],
            'candidates': [_candidate_payload(c) for c in discovery.candidates[:5]],
        }

    def pick_vault_folder(self):
        if sys.platform != 'darwin':
            return {
                'ok': False,
                'state': 'unsupported',
                'path': '',
                'message': '当前系统暂不支持从扩展打开文件夹选择器',
            }
        script = 'POSIX path of (choose folder with prompt "选择 Obsidian 知识库文件夹")'
        try:
            proc = subprocess.run(
                ['osascript', '-e', script],
                capture_output=True,
                text=True,
                timeout=120,
            )
        except Exception as exc:
            return {
                'ok': False,
                'state': 'error',
                'path': '',
                'message': type(exc).__name__,
            }
        if proc.returncode != 0:
            return {
                'ok': False,
                'state': 'cancelled',
                'path': '',
                'message': '用户取消选择',
            }
        selected = Path(proc.stdout.strip()).expanduser()
        candidate = score_vault(selected, source='user_selected')
        if candidate:
            write_vault_path_to_config(self.config_path(), candidate.path_obj)
            return {
                'ok': True,
                'state': 'ready',
                'path': candidate.path,
                'source': candidate.source,
                'score': candidate.score,
                'reasons': candidate.reasons,
            }
        if selected.exists() and selected.is_dir() and (selected / '.obsidian').is_dir():
            resolved = selected.resolve()
            write_vault_path_to_config(self.config_path(), resolved)
            return {
                'ok': True,
                'state': 'ready',
                'path': str(resolved),
                'source': 'user_selected',
                'score': 35,
                'reasons': ['.obsidian', 'user_selected'],
            }
        return self.discover_and_persist_vault(str(selected))

    async def check_model_health(self, config_data):
        status = await asyncio.to_thread(self._check_model_health_sync, config_data)
        target = self.status_path('model_health.json')
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding='utf-8')
        return status

    def _check_model_health_sync(self, config_data):
        config_path = self.config_path()
        provider = _normalize_provider(
            config_data.get('provider')
            or _simple_config_value(config_path, 'provider', 'active', DEFAULT_PROVIDER)
        )
        api_key = _incoming_api_key(config_data, provider, _provider_api_key(config_path, provider))
        endpoint = _incoming_endpoint(config_data, provider, _provider_endpoint(config_path, provider))
        model = (
            config_data.get('model')
            or config_data.get('modelId')
            or _simple_config_value(config_path, 'models', 'analyzer', _provider_default(provider, 'model'))
        )
        checked_at = datetime.now().isoformat()

        if not api_key:
            return {
                'ok': False,
                'state': 'missing',
                'provider': provider,
                'providerLabel': _provider_default(provider, 'label'),
                'model': model,
                'endpoint': endpoint,
                'checkedAt': checked_at,
                'message': f"缺少 {_provider_default(provider, 'label')} API Key",
            }

        if provider == 'volcengine_agent_plan':
            url = endpoint.rstrip('/') + '/responses'
            body = json.dumps({
                'model': model,
                'input': [{
                    'role': 'user',
                    'content': [{'type': 'input_text', 'text': 'ping'}],
                }],
                'max_output_tokens': 8,
                'stream': False,
            }).encode('utf-8')
        else:
            url = endpoint.rstrip('/') + '/tokenization'
            body = json.dumps({'model': model, 'text': 'ping'}).encode('utf-8')

        request = urllib.request.Request(
            url,
            data=body,
            headers={
                'Authorization': f'Bearer {api_key}',
                'Content-Type': 'application/json',
            },
            method='POST',
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as resp:
                ok = 200 <= resp.status < 300
                return {
                    'ok': ok,
                    'state': 'ready' if ok else 'error',
                    'provider': provider,
                    'providerLabel': _provider_default(provider, 'label'),
                    'model': model,
                    'endpoint': endpoint,
                    'checkedAt': checked_at,
                    'message': '模型连通正常' if ok else f'HTTP {resp.status}',
                }
        except urllib.error.HTTPError as exc:
            message = f'HTTP {exc.code}'
            if exc.code in {401, 403}:
                message = 'API Key 无效或无权限'
            elif exc.code == 404:
                message = '模型 ID 或端点不存在'
            return {
                'ok': False,
                'state': 'error',
                'provider': provider,
                'providerLabel': _provider_default(provider, 'label'),
                'model': model,
                'endpoint': endpoint,
                'checkedAt': checked_at,
                'message': message,
            }
        except Exception as exc:
            return {
                'ok': False,
                'state': 'error',
                'provider': provider,
                'providerLabel': _provider_default(provider, 'label'),
                'model': model,
                'endpoint': endpoint,
                'checkedAt': checked_at,
                'message': type(exc).__name__,
            }
        
    async def handle_cookie_update(self, platform, cookie_data):
        """处理 cookie 更新"""
        if platform != 'douyin':
            raise ValueError('unsupported platform')
        if not isinstance(cookie_data, str) or not cookie_data.strip():
            raise ValueError('empty cookie data')

        log(f"[Server] 收到 {platform} cookie 更新")
        self.cookie = cookie_data
        
        # 保存到文件
        cookie_dir = self.runtime_root / 'cookie'
        cookie_dir.mkdir(parents=True, exist_ok=True)

        cookie_path = cookie_dir / f'{platform}.txt'
        with open(cookie_path, 'w') as f:
            f.write(cookie_data)
        os.chmod(cookie_path, 0o600)
        log(f"[Server] Cookie 已保存到 {cookie_path}")
        
    async def start(self):
        """启动服务器"""
        import websockets

        logging.getLogger("websockets").setLevel(logging.CRITICAL)
        logging.getLogger("websockets.server").setLevel(logging.CRITICAL)
        log(f"[Server] 启动 WebSocket 服务器: ws://{self.host}:{self.port}")
        
        async with websockets.serve(self.handle_client, self.host, self.port):
            log(f"[Server] 服务器已启动，等待连接...")
            await asyncio.Future()  # 永远运行
            

def main():
    """入口函数"""
    try:
        from install.bootstrap import bootstrap
        bootstrap(install_deps=False)
    except Exception as exc:
        log(f"[Server] bootstrap warning: {type(exc).__name__}: {exc}")

    server = LibrarianServer()
    
    try:
        asyncio.run(server.start())
    except KeyboardInterrupt:
        log("\n[Server] 收到中断信号，关闭服务器")
        

if __name__ == '__main__':
    main()
