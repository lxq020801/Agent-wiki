import asyncio
import ipaddress
import json
import logging
import os
import re
import sys
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
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


DEFAULT_TASK_CONCURRENCY = 2
MIN_TASK_CONCURRENCY = 1
MAX_TASK_CONCURRENCY = 4
SENSITIVE_QUERY_KEYS = {
    'access_token',
    'private_token',
    'github_token',
    'token',
    'api_key',
    'apikey',
    'key',
    'secret',
    'client_secret',
    'signature',
    'sig',
}


def _redact_runtime_text(value):
    text = str(value or '')
    patterns = [
        (r"(?i)Bearer\s+[A-Za-z0-9._~+/=-]+", "Bearer [REDACTED]"),
        (r"(?i)(https?://)[^/\s:@]+:[^/\s@]+@", r"\1[REDACTED]@"),
        (r"\bresp-[A-Za-z0-9._-]+\b", "resp-[REDACTED]"),
        (r"\bghp_[A-Za-z0-9_]{20,}\b", "ghp_[REDACTED]"),
        (r"\bgithub_pat_[A-Za-z0-9_]{20,}\b", "github_pat_[REDACTED]"),
        (r"(?i)(access_token|private_token|github_token)=([^&\s]+)", r"\1=[REDACTED]"),
        (r"(?i)([?&][^=&#]*(token|key|secret|signature|sig)[^=&#]*=)[^&#\s]+", r"\1[REDACTED]"),
    ]
    for pattern, repl in patterns:
        text = re.sub(pattern, repl, text)
    return text


def _redact_runtime_value(value):
    if isinstance(value, dict):
        clean = {}
        for key, child in value.items():
            canonical = re.sub(r"[^a-z0-9]", "", str(key).lower())
            if canonical.endswith('apikey') or canonical in {'cookie', 'setcookie', 'authorization'}:
                continue
            clean[key] = _redact_runtime_value(child)
        return clean
    if isinstance(value, list):
        return [_redact_runtime_value(item) for item in value]
    if isinstance(value, str):
        return _redact_runtime_text(value)
    return value


def _normalize_task_concurrency(value, default=DEFAULT_TASK_CONCURRENCY):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(MIN_TASK_CONCURRENCY, min(MAX_TASK_CONCURRENCY, parsed))


def _normalize_chunk_concurrency(value, default=2):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(1, min(4, parsed))


def default_task_concurrency(runtime_root=None):
    raw = os.environ.get("OBSIDIAN_LIBRARIAN_TASK_CONCURRENCY")
    if raw:
        return _normalize_task_concurrency(raw)
    if runtime_root is not None:
        config_path = Path(runtime_root).expanduser() / "config.toml"
        raw = _simple_config_value(
            config_path,
            "server",
            "task_concurrency",
            str(DEFAULT_TASK_CONCURRENCY),
        )
        return _normalize_task_concurrency(raw)
    return DEFAULT_TASK_CONCURRENCY


PROVIDERS = {
    "doubao": {
        "label": "字节跳动火山方舟 API",
        "section": "ark",
        "api_key_fields": ("arkApiKey", "doubaoApiKey", "apiKey"),
        "endpoint_fields": ("arkEndpoint", "doubaoEndpoint", "endpoint"),
        "endpoint": "https://ark.cn-beijing.volces.com/api/v3",
        "model": "doubao-seed-2-0-lite-260428",
        "fallback": "doubao-seed-2-0-mini-260428",
    },
}
DEFAULT_PROVIDER = "doubao"
DEFAULT_ARK_ENDPOINT = PROVIDERS[DEFAULT_PROVIDER]["endpoint"]
TRUSTED_ARK_HOSTS = {"ark.cn-beijing.volces.com"}
DOUYIN_HOST_SUFFIXES = (
    "douyin.com",
    "iesdouyin.com",
)
INGEST_INTENTS = {"knowledge_ingest", "viral_breakdown"}
DEFAULT_INGEST_INTENT = "knowledge_ingest"
ALL_INGEST_INTENTS = ("knowledge_ingest", "viral_breakdown")
TASK_STAGES = {
    "queued": "排队中",
    "started": "已开始",
    "downloading": "下载中",
    "download": "下载中",
    "downloaded": "下载完成",
    "downloading_images": "下载图片",
    "downloaded_images": "图片下载完成",
    "probed_duration": "读取视频信息",
    "fps_decided": "计算抽帧",
    "chunking_plan": "规划切片",
    "overview_uploading": "上传全片概览",
    "overview_uploaded": "全片概览上传完成",
    "overview_chunking": "规划分片概览",
    "overview_chunk_uploading": "上传概览切片",
    "overview_chunk_uploaded": "概览切片上传完成",
    "analyzing_overview": "分析全片概览",
    "analyzing_overview_chunk": "分析概览切片",
    "overview_chunk_done": "概览切片完成",
    "synthesizing_overview_strategy": "合成精拆策略",
    "repairing_overview_strategy": "修复精拆策略",
    "overview_strategy_repaired": "精拆策略已修复",
    "overview_strategy_decided": "决定精拆策略",
    "chunk_uploading": "上传切片",
    "chunk_uploaded": "切片上传完成",
    "uploading": "上传中",
    "uploaded": "上传完成",
    "waiting_active": "等待预处理",
    "encoding_images": "编码图片",
    "analyzing": "分析中",
    "analyzing_chunk": "分析切片",
    "chunk_done": "切片分析完成",
    "synthesizing_chunks": "汇总切片",
    "synthesizing_done": "汇总完成",
    "analyzing_done": "分析完成",
    "analyzed": "分析完成",
    "derived_candidates_ready": "派生候选已生成",
    "resolving_target": "解析派生目标",
    "target_resolved": "派生目标已解析",
    "analyzing_derived_target": "分析派生目标",
    "writing_vault": "写入知识库",
    "done": "成功",
    "failed": "失败",
    "config_error": "配置错误",
    "task_invalid": "任务无效",
}
RESPONSE_PHASE_STAGES = {
    "analyzing",
    "analyzing_overview",
    "analyzing_overview_chunk",
    "synthesizing_overview_strategy",
    "repairing_overview_strategy",
    "analyzing_chunk",
    "synthesizing_chunks",
    "analyzing_derived_target",
}


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


def _write_json_atomic(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _normalize_provider(value):
    value = str(value or "").strip().lower()
    aliases = {
        "doubao_api": "doubao",
        "ark": "doubao",
        "ark_api": "doubao",
        "normal_ark": "doubao",
        # 旧 Agent Plan 名称统一回落，但旧 key 不会自动迁移。
        "agent_plan": "doubao",
        "agentplan": "doubao",
        "volcengine-agent-plan": "doubao",
        "volcengine_agent": "doubao",
        "ark_agent_plan": "doubao",
        "volcengine_agent_plan": "doubao",
    }
    value = aliases.get(value, value)
    return value if value in PROVIDERS else DEFAULT_PROVIDER


def _is_legacy_agent_plan_provider(value):
    raw = str(value or "").strip().lower()
    return raw in {
        "agent_plan",
        "agentplan",
        "volcengine-agent-plan",
        "volcengine_agent",
        "ark_agent_plan",
        "volcengine_agent_plan",
    }


def _provider_default(provider, key):
    return PROVIDERS[_normalize_provider(provider)][key]


def _provider_section(provider):
    return _provider_default(provider, "section")


def _section_api_key(config_path, provider):
    return _simple_config_value(config_path, _provider_section(provider), "api_key")


def _provider_api_key(config_path, provider):
    provider = _normalize_provider(provider)
    active_raw = _simple_config_value(config_path, "provider", "active", DEFAULT_PROVIDER)
    if _is_legacy_agent_plan_provider(active_raw):
        return ""
    return _section_api_key(config_path, provider)


def _provider_endpoint(config_path, provider):
    return _safe_ark_endpoint(
        _simple_config_value(config_path, _provider_section(provider), "endpoint"),
        provider,
    )


def _analysis_response_timeout(config_path):
    raw = _simple_config_value(config_path, "analysis", "response_timeout_sec", "900")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = 900
    return max(60, value)


def _first_config_value(config_data, fields):
    for field in fields:
        if field in config_data:
            return config_data.get(field)
    return None


def _nested_config_value(config_data, section, fields):
    nested = config_data.get(section)
    if not isinstance(nested, dict):
        return None
    return _first_config_value(nested, fields)


def _explicit_endpoint_values(config_data):
    fields = ('endpoint', 'arkEndpoint', 'doubaoEndpoint', 'agentPlanEndpoint', 'agent_plan_endpoint')
    for source in (config_data, config_data.get('llm') if isinstance(config_data.get('llm'), dict) else {}):
        if not isinstance(source, dict):
            continue
        for field in fields:
            if field in source and str(source.get(field) or '').strip():
                yield source.get(field)


def _incoming_api_key(config_data, provider, existing=""):
    fields = _provider_default(provider, "api_key_fields")
    if _is_legacy_agent_plan_provider(config_data.get("provider")):
        fields = tuple(field for field in fields if field != "apiKey")
    incoming = (
        _nested_config_value(config_data, "llm", fields)
        or _first_config_value(config_data, fields)
    )
    if incoming is None or str(incoming).strip() == "":
        return existing
    return str(incoming).strip()


def _incoming_endpoint(config_data, provider, existing=""):
    fields = _provider_default(provider, "endpoint_fields")
    incoming = _nested_config_value(config_data, "llm", fields) or _first_config_value(config_data, fields)
    if incoming is None or str(incoming).strip() == "":
        incoming = existing
    return _validate_ark_endpoint(incoming, provider)


def _safe_ark_endpoint(value, provider=None):
    expected = _provider_default(provider or DEFAULT_PROVIDER, "endpoint")
    try:
        return _validate_ark_endpoint(value or expected, provider)
    except ValueError:
        return expected


def _validate_ark_endpoint(value, provider=None):
    endpoint = (value or _provider_default(provider or DEFAULT_PROVIDER, "endpoint")).strip().rstrip("/")
    parsed = urllib.parse.urlparse(endpoint)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("Endpoint URL 必须是有效的 HTTPS 地址")
    if parsed.username or parsed.password:
        raise ValueError("Endpoint URL 不能包含账号密码")
    if parsed.hostname.lower() not in TRUSTED_ARK_HOSTS:
        raise ValueError("Endpoint URL 必须使用可信 Ark 官方域名")
    if _is_agent_plan_endpoint_text(endpoint):
        raise ValueError("Agent Plan endpoint 不能作为普通 Ark API 使用")
    return endpoint


def _is_agent_plan_endpoint_text(endpoint):
    return str(endpoint or "").rstrip("/").endswith("/api/plan/v3")


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

    扩展可以提交入库任务，但只作为辅助入口；下载、分析、写库仍由
    Agent 本地执行层调用 deps/douyin/scripts/ingest.py 完成。
    """
    
    def __init__(self, host='127.0.0.1', port=8765, *, enable_task_runner=True, task_concurrency=None):
        self.host = host
        self.port = port
        self.clients = set()  # 所有连接的扩展客户端
        self.config = None  # 当前配置
        self.cookie = None  # 当前 cookie
        self.runtime_root = default_runtime_root()
        self.enable_task_runner = enable_task_runner
        self.task_queue = None
        self.task_workers = set()
        self.retire_worker_tokens = 0
        self.task_concurrency = (
            default_task_concurrency(self.runtime_root)
            if task_concurrency is None
            else _normalize_task_concurrency(task_concurrency)
        )
        self.queued_task_files = set()
        self.running_task_ids = set()
        self.current_task_id = None
        
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
                    'extension_task_ingest',
                    'task_status',
                    'derived_task_action',
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
                        'error': type(e).__name__,
                        'message': str(e),
                    }, ensure_ascii=False))
                    
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
            try:
                await self.handle_config_update(msg.get('data', {}))
            except ValueError as exc:
                await websocket.send(json.dumps({
                    'type': 'config_rejected',
                    'error': 'config_invalid',
                    'message': str(exc),
                    'timestamp': datetime.now().isoformat()
                }, ensure_ascii=False))
            else:
                await websocket.send(json.dumps({
                    'type': 'config_synced',
                    'timestamp': datetime.now().isoformat()
                }))
            
        elif msg_type == 'cookie_update':
            status = await self.handle_cookie_update(msg.get('platform'), msg.get('data'))
            await websocket.send(json.dumps({
                'type': 'cookie_synced',
                'platform': msg.get('platform'),
                'status': status,
                'timestamp': datetime.now().isoformat()
            }, ensure_ascii=False))

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
            try:
                status = await self.check_model_health(msg.get('data') or {})
            except ValueError as exc:
                status = {
                    'ok': False,
                    'state': 'error',
                    'checkedAt': datetime.now().isoformat(),
                    'message': str(exc),
                }
            await websocket.send(json.dumps({
                'type': 'model_status',
                'status': status,
                'timestamp': datetime.now().isoformat()
            }, ensure_ascii=False))
            
        elif msg_type == 'task_request':
            reply = await self.handle_task_request(msg)
            await websocket.send(json.dumps(reply, ensure_ascii=False))
            if reply.get('type') == 'task_accepted':
                await self.broadcast({
                    'type': 'task_status_snapshot',
                    'tasks': await asyncio.to_thread(self.task_status_snapshot),
                    'timestamp': datetime.now().isoformat(),
                })

        elif msg_type == 'task_status_request':
            await websocket.send(json.dumps({
                'type': 'task_status_snapshot',
                'tasks': await asyncio.to_thread(self.task_status_snapshot),
                'timestamp': datetime.now().isoformat(),
            }, ensure_ascii=False))

        elif msg_type == 'derived_task_action':
            reply = await self.handle_derived_task_action(msg)
            await websocket.send(json.dumps(reply, ensure_ascii=False))
            await self.broadcast({
                'type': 'task_status_snapshot',
                'tasks': await asyncio.to_thread(self.task_status_snapshot),
                'timestamp': datetime.now().isoformat(),
            })
            
        else:
            log(f"[Server] 未知消息类型: {msg_type}")

    async def broadcast(self, payload):
        """向所有已连接扩展广播状态。断开的客户端会被清理。"""
        if not self.clients:
            return
        text = json.dumps(payload, ensure_ascii=False)
        dead = set()
        for client in list(self.clients):
            try:
                await client.send(text)
            except Exception as exc:
                if _is_connection_closed(exc):
                    dead.add(client)
                else:
                    log(f"[Server] 广播失败: {type(exc).__name__}")
        self.clients.difference_update(dead)

    def _task_id(self):
        return time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4]

    def _task_dirs(self):
        return {
            'inbox': self.runtime_root / 'inbox',
            'status': self.runtime_root / 'status',
            'logs': self.runtime_root / 'logs' / 'tasks',
            'derived_actions': self.runtime_root / 'derived-actions',
        }

    def _extract_task_url(self, msg):
        raw = msg.get('url')
        if raw is None and isinstance(msg.get('data'), dict):
            raw = msg['data'].get('url')
        text = str(raw or '').strip()
        if not text:
            return ''
        import re
        douyin_match = re.search(
            r"https?://(?:v\.douyin\.com/[A-Za-z0-9_-]+/?|"
            r"(?:www\.)?(?:douyin|iesdouyin)\.com/(?:video|share/video|note|share/note)/\d+"
            r"(?:[/?#][^\s\"'<>，。！？、；：）)]*)?)",
            text,
            re.IGNORECASE,
        )
        if douyin_match:
            return douyin_match.group(0)
        if text.startswith('http://') or text.startswith('https://'):
            return text
        match = re.search(r'https?://\S+', text)
        return match.group(0).rstrip('，。,.!！)）]】') if match else ''

    def _is_supported_douyin_url(self, url):
        try:
            parsed = urllib.parse.urlparse(url)
        except Exception:
            return False
        if parsed.scheme not in {'http', 'https'}:
            return False
        host = (parsed.hostname or '').lower()
        return any(host == suffix or host.endswith('.' + suffix) for suffix in DOUYIN_HOST_SUFFIXES)

    def _extract_ingest_intent(self, msg):
        raw = msg.get('ingest_intent') or msg.get('ingestIntent')
        if raw is None and isinstance(msg.get('data'), dict):
            raw = msg['data'].get('ingest_intent') or msg['data'].get('ingestIntent')
        intent = str(raw or '').strip() or DEFAULT_INGEST_INTENT
        return intent if intent in INGEST_INTENTS else ''

    def _extract_ingest_intents(self, msg):
        raw = msg.get('ingest_intents') or msg.get('ingestIntents')
        if raw is None and isinstance(msg.get('data'), dict):
            raw = msg['data'].get('ingest_intents') or msg['data'].get('ingestIntents')
        if raw is None:
            intent = self._extract_ingest_intent(msg)
            return [intent] if intent else []
        if isinstance(raw, str):
            text = raw.strip()
            if text in {'both', 'all', 'knowledge_and_viral'}:
                items = list(ALL_INGEST_INTENTS)
            else:
                items = [item.strip() for item in text.split(',')]
        elif isinstance(raw, (list, tuple)):
            items = list(raw)
        else:
            items = []
        out = []
        for item in items:
            intent = str(item or '').strip()
            if intent not in INGEST_INTENTS:
                return []
            if intent not in out:
                out.append(intent)
        return out or [DEFAULT_INGEST_INTENT]

    def _write_task_status(self, task_id, payload):
        status_dir = self._task_dirs()['status']
        status_dir.mkdir(parents=True, exist_ok=True)
        target = status_dir / f'{task_id}.json'
        _write_json_atomic(target, _redact_runtime_value(payload))
        return target

    def _finish_status_fields(self, status):
        finished = time.time()
        started = status.get('started_at')
        if not isinstance(started, (int, float)):
            started = finished
        elapsed = max(0, round(finished - started, 1))
        status.setdefault('finished_at', finished)
        status.setdefault('elapsed_sec', elapsed)
        status.setdefault('task_duration_sec', elapsed)
        status['updated_at'] = finished
        return status

    def _derived_actions_path(self, parent_task_id):
        return self._task_dirs()['derived_actions'] / f'{parent_task_id}.json'

    def _read_derived_actions(self, parent_task_id):
        data = _json_file(self._derived_actions_path(parent_task_id))
        if isinstance(data, dict):
            return data
        return {"parentTaskId": parent_task_id, "items": {}}

    def _write_derived_actions(self, parent_task_id, actions):
        actions["parentTaskId"] = parent_task_id
        actions["updatedAt"] = time.time()
        _write_json_atomic(self._derived_actions_path(parent_task_id), actions)

    def _derived_child_task_id(self, parent_task_id, candidate_id):
        safe = ''.join(ch if ch.isalnum() or ch in '-_' else '-' for ch in str(candidate_id or 'derived'))
        return f"{parent_task_id}-derive-{safe[:18]}"

    def _merge_derived_actions(self, parent_task_id, tasks):
        if not isinstance(tasks, list) or not parent_task_id:
            return tasks if isinstance(tasks, list) else []
        actions = self._read_derived_actions(parent_task_id).get("items", {})
        if not isinstance(actions, dict):
            return tasks
        merged = []
        for item in tasks:
            if not isinstance(item, dict):
                continue
            current = dict(item)
            action = actions.get(str(current.get("id") or ""))
            if isinstance(action, dict):
                if action.get("status"):
                    current["status"] = action["status"]
                    current["candidateStatus"] = action["status"]
                for key in ("childTaskId", "childStatus", "targetUrl", "ignoredAt", "confirmedAt", "error"):
                    if action.get(key):
                        current[key] = action[key]
                child_id = action.get("childTaskId")
                if child_id:
                    child_status = _json_file(self._task_dirs()['status'] / f"{child_id}.json") or {}
                    if child_status.get("ok") is True:
                        current["status"] = "done"
                        current["candidateStatus"] = "done"
                    elif child_status.get("ok") is False:
                        current["status"] = "failed"
                        current["candidateStatus"] = "failed"
                        current["error"] = child_status.get("error") or current.get("error") or ""
                    elif child_status:
                        current["status"] = "running" if child_status.get("stage") != "queued" else "queued"
                        current["candidateStatus"] = current["status"]
                    current["childStage"] = child_status.get("stage") or ""
                    current["childVaultPath"] = child_status.get("vault_path") or ""
            merged.append(current)
        return merged

    def _update_derived_action_item(self, parent_task_id, candidate_id, **fields):
        actions = self._read_derived_actions(parent_task_id)
        items = actions.setdefault("items", {})
        item = items.setdefault(str(candidate_id), {})
        item.update({key: value for key, value in fields.items() if value is not None})
        item["candidateId"] = str(candidate_id)
        item["updatedAt"] = time.time()
        self._write_derived_actions(parent_task_id, actions)
        return item

    def _find_derived_candidate(self, parent_status, candidate_id):
        for item in parent_status.get('derived_tasks') or []:
            if isinstance(item, dict) and str(item.get('id') or '') == str(candidate_id):
                return dict(item)
        return None

    def _parent_asset_path(self, parent_status, candidate_id=''):
        parent_assets = parent_status.get('assets') if isinstance(parent_status.get('assets'), list) else []
        if candidate_id:
            for asset in parent_assets:
                derived = asset.get('derived_tasks') if isinstance(asset, dict) else []
                if not isinstance(derived, list):
                    continue
                if any(isinstance(item, dict) and str(item.get('id') or '') == str(candidate_id) for item in derived):
                    path = str(asset.get('vault_path') or '').strip()
                    if path:
                        return path
        if parent_assets:
            path = str(parent_assets[0].get('vault_path') or '').strip()
            if path:
                return path
        return str(parent_status.get('vault_path') or '').strip()

    def _is_safe_external_url(self, value):
        parsed = urllib.parse.urlparse(str(value or '').strip())
        if parsed.scheme != 'https' or not parsed.netloc:
            return False
        if parsed.username or parsed.password:
            return False
        host = (parsed.hostname or '').lower()
        if host in {'localhost', '0.0.0.0'} or host.endswith('.local'):
            return False
        try:
            ip = ipaddress.ip_address(host.strip('[]'))
        except ValueError:
            return True
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            return False
        return True

    def _clean_external_url(self, value):
        if not self._is_safe_external_url(value):
            return ''
        parsed = urllib.parse.urlparse(str(value or '').strip())
        query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=False)
        clean_query = [
            (key, val)
            for key, val in query
            if key.lower() not in SENSITIVE_QUERY_KEYS
            and not any(marker in key.lower() for marker in ('token', 'secret', 'signature'))
        ]
        return urllib.parse.urlunparse((
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            parsed.path or '/',
            '',
            urllib.parse.urlencode(clean_query, doseq=True),
            '',
        ))

    async def handle_derived_task_action(self, msg):
        request_id = msg.get('requestId') or msg.get('request_id') or ''
        parent_task_id = str(msg.get('taskId') or msg.get('parentTaskId') or '').strip()
        candidate_id = str(msg.get('derivedTaskId') or msg.get('candidateId') or '').strip()
        action = str(msg.get('action') or '').strip()
        if action not in {'confirm', 'ignore'} or not parent_task_id or not candidate_id:
            return {
                'type': 'derived_task_action_rejected',
                'requestId': request_id,
                'parentTaskId': parent_task_id,
                'candidateId': candidate_id,
                'reason': 'invalid_action',
                'message': '派生操作参数无效',
                'timestamp': datetime.now().isoformat(),
            }
        parent_status = _json_file(self._task_dirs()['status'] / f'{parent_task_id}.json') or {}
        candidate = self._find_derived_candidate(parent_status, candidate_id)
        if not candidate:
            return {
                'type': 'derived_task_action_rejected',
                'requestId': request_id,
                'parentTaskId': parent_task_id,
                'candidateId': candidate_id,
                'reason': 'candidate_not_found',
                'message': '没有找到对应派生候选',
                'timestamp': datetime.now().isoformat(),
            }
        existing_action = self._read_derived_actions(parent_task_id).get('items', {}).get(candidate_id)
        if isinstance(existing_action, dict) and existing_action.get('childTaskId'):
            return {
                'type': 'derived_task_action_done',
                'requestId': request_id,
                'action': action,
                'parentTaskId': parent_task_id,
                'candidateId': candidate_id,
                'childTaskId': existing_action.get('childTaskId'),
                'message': '这个派生候选已经进入队列',
                'timestamp': datetime.now().isoformat(),
            }
        existing_action_status = existing_action.get('status') if isinstance(existing_action, dict) else ''
        if action == 'confirm' and existing_action_status == 'ignored':
            return {
                'type': 'derived_task_action_rejected',
                'requestId': request_id,
                'parentTaskId': parent_task_id,
                'candidateId': candidate_id,
                'reason': 'candidate_ignored',
                'message': '这个候选已被忽略',
                'timestamp': datetime.now().isoformat(),
            }
        if action == 'ignore':
            self._update_derived_action_item(
                parent_task_id, candidate_id,
                status='ignored', ignoredAt=time.time(),
            )
            return {
                'type': 'derived_task_action_done',
                'requestId': request_id,
                'action': action,
                'parentTaskId': parent_task_id,
                'candidateId': candidate_id,
                'timestamp': datetime.now().isoformat(),
            }
        candidate_status = str(candidate.get('status') or candidate.get('candidateStatus') or '').strip()
        candidate_decision = str(candidate.get('decision') or '').strip()
        allowed_statuses = {'candidate', 'auto_ready', 'needs_target'}
        if candidate_decision and candidate_decision != 'candidate':
            return {
                'type': 'derived_task_action_rejected',
                'requestId': request_id,
                'parentTaskId': parent_task_id,
                'candidateId': candidate_id,
                'reason': 'candidate_not_executable',
                'message': '这个派生候选当前不能执行',
                'timestamp': datetime.now().isoformat(),
            }
        if candidate_status and candidate_status not in allowed_statuses:
            return {
                'type': 'derived_task_action_rejected',
                'requestId': request_id,
                'parentTaskId': parent_task_id,
                'candidateId': candidate_id,
                'reason': 'candidate_status_not_executable',
                'message': '这个派生候选当前状态不能确认',
                'timestamp': datetime.now().isoformat(),
            }
        parent_asset_path = self._parent_asset_path(parent_status, candidate_id)
        if parent_status.get('ok') is not True or not parent_asset_path:
            return {
                'type': 'derived_task_action_rejected',
                'requestId': request_id,
                'parentTaskId': parent_task_id,
                'candidateId': candidate_id,
                'reason': 'parent_asset_not_ready',
                'message': '父资产还没有写入知识库，稍后再确认派生',
                'timestamp': datetime.now().isoformat(),
            }
        if not Path(parent_asset_path).expanduser().exists():
            return {
                'type': 'derived_task_action_rejected',
                'requestId': request_id,
                'parentTaskId': parent_task_id,
                'candidateId': candidate_id,
                'reason': 'parent_asset_missing',
                'message': '没有找到父资产文件，暂不能派生写库',
                'timestamp': datetime.now().isoformat(),
            }
        target_url = str(msg.get('targetUrl') or '').strip()
        if target_url:
            clean_url = self._clean_external_url(target_url)
            if not clean_url:
                return {
                    'type': 'derived_task_action_rejected',
                    'requestId': request_id,
                    'parentTaskId': parent_task_id,
                    'candidateId': candidate_id,
                    'reason': 'invalid_target_url',
                    'message': '派生目标 URL 必须是可信 HTTPS 外部链接',
                    'timestamp': datetime.now().isoformat(),
                }
            candidate['targetUrl'] = clean_url
        elif candidate.get('targetUrl') or candidate.get('target_url'):
            clean_url = self._clean_external_url(candidate.get('targetUrl') or candidate.get('target_url'))
            if not clean_url:
                return {
                    'type': 'derived_task_action_rejected',
                    'requestId': request_id,
                    'parentTaskId': parent_task_id,
                    'candidateId': candidate_id,
                    'reason': 'invalid_target_url',
                    'message': '派生目标 URL 必须是可信 HTTPS 外部链接',
                    'timestamp': datetime.now().isoformat(),
                }
            candidate['targetUrl'] = clean_url
        elif candidate.get('status') == 'needs_target':
            return {
                'type': 'derived_task_action_rejected',
                'requestId': request_id,
                'parentTaskId': parent_task_id,
                'candidateId': candidate_id,
                'reason': 'target_url_required',
                'message': '这个候选需要先补充目标 URL',
                'timestamp': datetime.now().isoformat(),
            }
        child_task = await self.enqueue_derived_candidate(
            parent_task_id, parent_status, candidate, source='derived_manual',
        )
        self._update_derived_action_item(
            parent_task_id, candidate_id,
            status='queued',
            childTaskId=child_task['id'],
            targetUrl=candidate.get('targetUrl') or '',
            confirmedAt=time.time(),
        )
        return {
            'type': 'derived_task_action_done',
            'requestId': request_id,
            'action': action,
            'parentTaskId': parent_task_id,
            'candidateId': candidate_id,
            'childTaskId': child_task['id'],
            'timestamp': datetime.now().isoformat(),
        }

    async def enqueue_derived_candidate(self, parent_task_id, parent_status, candidate, *, source='derived_auto'):
        candidate_id = str(candidate.get('id') or '').strip()
        child_id = self._derived_child_task_id(parent_task_id, candidate_id)
        dirs = self._task_dirs()
        dirs['inbox'].mkdir(parents=True, exist_ok=True)
        child_file = dirs['inbox'] / f'{child_id}.json'
        parent_title = parent_status.get('title') or ''
        parent_asset_path = self._parent_asset_path(parent_status, candidate_id)
        parent_title = parent_title or str((parent_status.get('meta') or {}).get('title') or parent_status.get('page_title') or '')
        task = {
            'id': child_id,
            'type': 'derived_ingest',
            'source': source,
            'created_at': datetime.now().isoformat(),
            'parent_task_id': parent_task_id,
            'parent_asset_path': parent_asset_path,
            'parent_title': parent_title,
            'parent_source_url': parent_status.get('source_url') or parent_status.get('url') or '',
            'candidate': candidate,
        }
        status_path = dirs['status'] / f'{child_id}.json'
        existing_status = _json_file(status_path)
        if isinstance(existing_status, dict) and existing_status.get('id') == child_id:
            if self.enable_task_runner and existing_status.get('ok') is None and child_file.exists():
                self.ensure_task_worker()
                await self.enqueue_task_file(child_file)
            return task
        if not child_file.exists():
            _write_json_atomic(child_file, task)
        self._write_task_status(child_id, {
            'id': child_id,
            'ok': None,
            'type': 'derived_ingest',
            'stage': 'queued',
            'stage_label': TASK_STAGES['queued'],
            'started_at': time.time(),
            'updated_at': time.time(),
            'progress': {},
            'source': source,
            'source_url': candidate.get('targetUrl') or candidate.get('target_url') or parent_status.get('source_url') or '',
            'ingest_intent': 'derived_ingest',
            'ingest_intents': ['derived_ingest'],
            'title': candidate.get('name') or '派生任务',
            'parent_task_id': parent_task_id,
            'derived_candidate_id': candidate_id,
            'derived_task': candidate,
        })
        if self.enable_task_runner:
            self.ensure_task_worker()
            await self.enqueue_task_file(child_file)
        return task

    async def enqueue_auto_derived_tasks(self, parent_task_id, parent_status):
        if parent_status.get('ok') is not True:
            return []
        if parent_status.get('type') == 'derived_ingest':
            return []
        tasks = parent_status.get('derived_tasks') if isinstance(parent_status.get('derived_tasks'), list) else []
        queued = []
        for candidate in tasks:
            if not isinstance(candidate, dict) or candidate.get('autoEligible') is not True:
                continue
            candidate = dict(candidate)
            if str(candidate.get('targetType') or candidate.get('target_type') or '') != 'github_project':
                continue
            if candidate.get('status') not in {'auto_ready', 'candidate'}:
                continue
            raw_target_url = candidate.get('targetUrl') or candidate.get('target_url') or ''
            if raw_target_url:
                clean_url = self._clean_external_url(raw_target_url)
                if not clean_url:
                    self._update_derived_action_item(
                        parent_task_id, candidate.get('id'),
                        status='needs_target',
                        error='invalid_target_url',
                    )
                    continue
                candidate['targetUrl'] = clean_url
            action = self._read_derived_actions(parent_task_id).get('items', {}).get(str(candidate.get('id') or ''))
            if isinstance(action, dict):
                if action.get('status') == 'ignored':
                    continue
                if action.get('childTaskId'):
                    continue
            child = await self.enqueue_derived_candidate(parent_task_id, parent_status, candidate, source='derived_auto')
            self._update_derived_action_item(
                parent_task_id, candidate.get('id'),
                status='queued',
                childTaskId=child['id'],
                targetUrl=candidate.get('targetUrl') or '',
                autoQueuedAt=time.time(),
            )
            queued.append(child)
        return queued

    def _read_task_file(self, task_file):
        try:
            return json.loads(Path(task_file).read_text(encoding='utf-8'))
        except Exception:
            return {}

    async def handle_task_request(self, msg):
        url = self._extract_task_url(msg)
        request_id = msg.get('requestId') or msg.get('request_id') or ''
        if not url or not self._is_supported_douyin_url(url):
            return {
                'type': 'task_rejected',
                'requestId': request_id,
                'reason': 'invalid_douyin_url',
                'message': '没有识别到可拆解的抖音链接',
                'timestamp': datetime.now().isoformat(),
            }
        ingest_intents = self._extract_ingest_intents(msg)
        if not ingest_intents:
            return {
                'type': 'task_rejected',
                'requestId': request_id,
                'reason': 'invalid_ingest_intent',
                'message': '未知入库类型，请选择“知识入库”或“爆款拆解”',
                'timestamp': datetime.now().isoformat(),
            }
        ingest_intent = ingest_intents[0]

        task_id = self._task_id()
        created_at = datetime.now().isoformat()
        dirs = self._task_dirs()
        dirs['inbox'].mkdir(parents=True, exist_ok=True)
        task_file = dirs['inbox'] / f'{task_id}.json'
        task = {
            'id': task_id,
            'url': url,
            'type': 'douyin_ingest',
            'ingest_intent': ingest_intent,
            'ingest_intents': ingest_intents,
            'source': msg.get('source') or 'extension',
            'page_title': msg.get('pageTitle') or '',
            'page_url': msg.get('pageUrl') or '',
            'aweme_id': msg.get('awemeId') or '',
            'detected_by': msg.get('detectedBy') or '',
            'created_at': created_at,
        }
        task_file.write_text(json.dumps(task, ensure_ascii=False, indent=2), encoding='utf-8')
        self._write_task_status(task_id, {
            'id': task_id,
            'ok': None,
            'stage': 'queued',
            'stage_label': TASK_STAGES['queued'],
            'started_at': time.time(),
            'updated_at': time.time(),
            'progress': {},
            'source': task['source'],
            'source_url': url,
            'ingest_intent': ingest_intent,
            'ingest_intents': ingest_intents,
            'page_url': task['page_url'],
            'page_title': task['page_title'],
            'aweme_id': task['aweme_id'],
            'detected_by': task['detected_by'],
            'created_at': created_at,
        })

        if self.enable_task_runner:
            self.ensure_task_worker()
            await self.enqueue_task_file(task_file)

        log(f"[Server] 任务已接收: {task_id} {url}")
        return {
            'type': 'task_accepted',
            'requestId': request_id,
            'task': {
                'id': task_id,
                'url': url,
                'stage': 'queued',
                'source': task['source'],
                'ingestIntent': ingest_intent,
                'ingestIntents': ingest_intents,
                'createdAt': created_at,
            },
            'message': '任务已进入队列',
            'timestamp': datetime.now().isoformat(),
        }

    def ensure_task_worker(self):
        if not self.enable_task_runner:
            return
        if self.task_queue is None:
            self.task_queue = asyncio.Queue()
        self.task_workers = {worker for worker in self.task_workers if not worker.done()}
        extra_workers = len(self.task_workers) - self.task_concurrency - self.retire_worker_tokens
        for _ in range(max(0, extra_workers)):
            self.retire_worker_tokens += 1
            self.task_queue.put_nowait(None)
        while len(self.task_workers) < self.task_concurrency:
            worker_index = len(self.task_workers) + 1
            self.task_workers.add(asyncio.create_task(self.task_worker_loop(worker_index)))

    async def enqueue_task_file(self, task_file):
        if self.task_queue is None:
            self.task_queue = asyncio.Queue()
        task_file = Path(task_file)
        key = str(task_file)
        if key in self.queued_task_files:
            return
        self.queued_task_files.add(key)
        await self.task_queue.put(task_file)

    async def enqueue_pending_tasks(self):
        if not self.enable_task_runner:
            return
        inbox = self._task_dirs()['inbox']
        if not inbox.exists():
            return
        for task_file in sorted(inbox.glob('*.json')):
            await self.enqueue_task_file(task_file)

    async def task_worker_loop(self, worker_index):
        while True:
            if self.task_queue is None:
                self.task_queue = asyncio.Queue()
            task_file = await self.task_queue.get()
            try:
                if task_file is None:
                    self.retire_worker_tokens = max(0, self.retire_worker_tokens - 1)
                    return
                self.queued_task_files.discard(str(task_file))
                await self.run_task_file(task_file)
            except Exception as exc:
                log(f"[Server] 任务执行器 {worker_index} 异常: {type(exc).__name__}: {exc}")
            finally:
                self.task_queue.task_done()
                await self.broadcast({
                    'type': 'task_status_snapshot',
                    'tasks': await asyncio.to_thread(self.task_status_snapshot),
                    'timestamp': datetime.now().isoformat(),
                })
                asyncio.get_running_loop().call_soon(self.ensure_task_worker)

    async def run_task_file(self, task_file):
        task_file = Path(task_file)
        task = self._read_task_file(task_file)
        task_id = task.get('id') or task_file.stem
        self.running_task_ids.add(task_id)
        self.current_task_id = sorted(self.running_task_ids)[0] if self.running_task_ids else None
        logs_dir = self._task_dirs()['logs']
        logs_dir.mkdir(parents=True, exist_ok=True)
        log_path = logs_dir / f'{task_id}.log'

        try:
            try:
                from install.bootstrap import select_runtime_python
                python = select_runtime_python()
            except Exception:
                python = Path(sys.executable)
            task_type = task.get('type') or 'douyin_ingest'
            if task_type == 'derived_ingest':
                script = PROJECT_ROOT / 'deps' / 'douyin' / 'scripts' / 'derive_executor.py'
            else:
                script = PROJECT_ROOT / 'deps' / 'douyin' / 'scripts' / 'ingest.py'

            log(f"[Server] 开始执行任务: {task_id}")
            with open(log_path, 'ab') as log_file:
                proc = await asyncio.create_subprocess_exec(
                    str(python),
                    str(script),
                    '--task',
                    str(task_file),
                    cwd=str(PROJECT_ROOT / 'deps' / 'douyin'),
                    stdout=log_file,
                    stderr=asyncio.subprocess.STDOUT,
                )
                timeout_monitor = asyncio.create_task(
                    self._monitor_task_timeout(task_id, proc)
                )
                code = await proc.wait()
                timeout_monitor.cancel()
                try:
                    await timeout_monitor
                except asyncio.CancelledError:
                    pass

            status_path = self._task_dirs()['status'] / f'{task_id}.json'
            status = _json_file(status_path) or {}
            if code != 0 and status.get('ok') is not False:
                status.update({
                    'id': task_id,
                    'ok': False,
                    'stage': 'failed',
                    'error': f'{script.name} exited with code {code}',
                    'log_path': str(log_path),
                })
                status = self._finish_status_fields(status)
                self._write_task_status(task_id, status)
            elif code == 0 and task_type != 'derived_ingest':
                queued = await self.enqueue_auto_derived_tasks(task_id, status)
                if queued:
                    log(f"[Server] 自动派生已入队: parent={task_id} count={len(queued)}")
            log(f"[Server] 任务结束: {task_id} exit={code}")
        finally:
            self.running_task_ids.discard(task_id)
            self.current_task_id = sorted(self.running_task_ids)[0] if self.running_task_ids else None

    def _latest_progress_stage(self, progress):
        if not isinstance(progress, dict):
            return ''
        latest = ''
        latest_at = -1
        for stage, info in progress.items():
            if not isinstance(info, dict):
                continue
            at = info.get('at') or 0
            if at > latest_at:
                latest = stage
                latest_at = at
        return latest

    def _task_response_phase_stage(self, task_id):
        status = _json_file(self._task_dirs()['status'] / f'{task_id}.json') or {}
        progress = status.get('progress') if isinstance(status.get('progress'), dict) else {}
        stage = self._latest_progress_stage(progress) or status.get('stage') or ''
        return stage if stage in RESPONSE_PHASE_STAGES else ''

    def _task_is_in_response_phase(self, task_id):
        return bool(self._task_response_phase_stage(task_id))

    def _task_stage_age_sec(self, task_id, stage):
        status = _json_file(self._task_dirs()['status'] / f'{task_id}.json') or {}
        progress = status.get('progress') if isinstance(status.get('progress'), dict) else {}
        info = progress.get(stage) if isinstance(progress.get(stage), dict) else {}
        at = info.get('at')
        if isinstance(at, (int, float)):
            return max(0, time.time() - at)
        updated = status.get('updated_at')
        if isinstance(updated, (int, float)):
            return max(0, time.time() - updated)
        return 0

    async def _monitor_task_timeout(self, task_id, proc):
        timeout_sec = _analysis_response_timeout(self.config_path())
        while proc.returncode is None:
            await asyncio.sleep(5)
            stage = self._task_response_phase_stage(task_id)
            if not stage:
                continue
            if self._task_stage_age_sec(task_id, stage) < timeout_sec:
                continue
            log(f"[Server] 任务分析超时，终止: {task_id} stage={stage} timeout={timeout_sec}s")
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=10)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
            status = _json_file(self._task_dirs()['status'] / f'{task_id}.json') or {}
            if status.get('ok') is not False:
                status.update({
                    'id': task_id,
                    'ok': False,
                    'stage': 'failed',
                    'error': f'Responses API analysis timed out after {timeout_sec}s',
                    'hint': '模型分析超时，可稍后重试或拆更短的视频',
                })
                status = self._finish_status_fields(status)
                self._write_task_status(task_id, status)
            return

    def _task_progress_percent(self, status):
        if status.get('ok') is True:
            return 100
        if status.get('ok') is False:
            return 100
        progress = status.get('progress') if isinstance(status.get('progress'), dict) else {}
        download = progress.get('download') if isinstance(progress.get('download'), dict) else {}
        image_download = progress.get('download_images') if isinstance(progress.get('download_images'), dict) else {}
        stage = self._latest_progress_stage(progress) or status.get('stage') or 'queued'
        if stage == 'download' and isinstance(download.get('pct'), (int, float)):
            return min(35, round(8 + float(download['pct']) * 0.27))
        if stage == 'download_images' and isinstance(image_download.get('pct'), (int, float)):
            return min(35, round(8 + float(image_download['pct']) * 0.27))
        return {
            'queued': 0,
            'started': 5,
            'downloading': 10,
            'downloading_images': 10,
            'downloaded': 38,
            'downloaded_images': 38,
            'probed_duration': 42,
            'fps_decided': 45,
            'chunking_plan': 47,
            'overview_uploading': 49,
            'overview_uploaded': 55,
            'analyzing_overview': 72,
            'repairing_overview_strategy': 73,
            'overview_strategy_repaired': 74,
            'overview_strategy_decided': 74,
            'resolving_target': 20,
            'target_resolved': 35,
            'analyzing_derived_target': 72,
            'chunk_uploading': 76,
            'chunk_uploaded': 78,
            'uploading': 52,
            'uploaded': 58,
            'waiting_active': 70,
            'encoding_images': 58,
            'analyzing': 74,
            'analyzing_chunk': 82,
            'chunk_done': 86,
            'synthesizing_chunks': 88,
            'synthesizing_done': 90,
            'analyzing_done': 90,
            'analyzed': 92,
            'derived_candidates_ready': 94,
            'writing_vault': 96,
        }.get(stage, 10)

    def _public_task_status(self, path):
        status = _json_file(path)
        if not isinstance(status, dict) or not status.get('id'):
            return None
        stage = self._latest_progress_stage(status.get('progress')) or status.get('stage') or 'queued'
        started = status.get('started_at') or status.get('created_at')
        updated = status.get('updated_at') or started
        elapsed = None
        if isinstance(status.get('elapsed_sec'), (int, float)):
            elapsed = max(0, round(float(status.get('elapsed_sec')), 1))
        elif isinstance(status.get('task_duration_sec'), (int, float)):
            elapsed = max(0, round(float(status.get('task_duration_sec')), 1))
        elif isinstance(started, (int, float)) and isinstance(updated, (int, float)):
            elapsed = max(0, round(updated - started, 1))
            if status.get('ok') is None:
                elapsed = max(0, round(time.time() - started, 1))
        meta = status.get('meta') if isinstance(status.get('meta'), dict) else {}
        assets = status.get('assets') if isinstance(status.get('assets'), list) else []
        asset_title = ''
        if assets and isinstance(assets[0], dict):
            asset_title = str(assets[0].get('title') or '').strip()
        page_title = str(status.get('page_title') or '').strip()
        if page_title.endswith('的抖音 - 抖音') or page_title.endswith('的抖音'):
            page_title = ''
        title = (
            meta.get('title')
            or asset_title
            or status.get('title')
            or page_title
            or status.get('source_url')
            or status.get('url')
            or status.get('id')
        )
        derived_tasks = self._merge_derived_actions(
            status.get('id'),
            status.get('derived_tasks') if isinstance(status.get('derived_tasks'), list) else [],
        )
        derived_summary = status.get('derived_summary') if isinstance(status.get('derived_summary'), dict) else {}
        if derived_tasks:
            derived_summary = {
                **derived_summary,
                'autoQueued': sum(1 for item in derived_tasks if item.get('status') == 'queued' and item.get('autoEligible')),
                'queued': sum(1 for item in derived_tasks if item.get('status') == 'queued'),
                'running': sum(1 for item in derived_tasks if item.get('status') == 'running'),
                'done': sum(1 for item in derived_tasks if item.get('status') == 'done'),
                'failed': sum(1 for item in derived_tasks if item.get('status') == 'failed'),
                'ignored': sum(1 for item in derived_tasks if item.get('status') == 'ignored'),
                'needsTarget': sum(1 for item in derived_tasks if item.get('status') == 'needs_target'),
            }
        return {
            'id': status.get('id'),
            'type': status.get('type') or '',
            'ok': status.get('ok'),
            'stage': status.get('stage') or 'queued',
            'displayStage': stage,
            'stageLabel': TASK_STAGES.get(stage, TASK_STAGES.get(status.get('stage'), stage)),
            'progressPercent': self._task_progress_percent(status),
            'title': str(title or '')[:120],
            'url': status.get('source_url') or status.get('url') or '',
            'source': status.get('source') or 'agent',
            'ingestIntent': status.get('ingest_intent') or DEFAULT_INGEST_INTENT,
            'ingestIntents': status.get('ingest_intents') or [status.get('ingest_intent') or DEFAULT_INGEST_INTENT],
            'assetFamily': status.get('asset_family') or '',
            'startedAt': started,
            'updatedAt': updated,
            'elapsedSec': elapsed,
            'error': status.get('error') or '',
            'hint': status.get('hint') or '',
            'vaultPath': status.get('vault_path') or '',
            'assets': assets,
            'derivedTasks': derived_tasks,
            'derivedSummary': derived_summary,
            'parentTaskId': status.get('parent_task_id') or '',
            'derivedCandidateId': status.get('derived_candidate_id') or '',
            'derivedTask': status.get('derived_task') if isinstance(status.get('derived_task'), dict) else {},
        }

    def task_status_snapshot(self, limit=20):
        status_dir = self._task_dirs()['status']
        status_dir.mkdir(parents=True, exist_ok=True)
        items = []
        for path in sorted(status_dir.glob('*.json'), key=lambda p: p.stat().st_mtime, reverse=True):
            if path.name == 'model_health.json':
                continue
            item = self._public_task_status(path)
            if item:
                items.append(item)
            if len(items) >= limit:
                break
        running = [item for item in items if item.get('ok') is None]
        failed = [item for item in items if item.get('ok') is False]
        done = [item for item in items if item.get('ok') is True]
        return {
            'items': items,
            'running': len(running),
            'failed': len(failed),
            'done': len(done),
            'currentTaskId': self.current_task_id,
            'currentTaskIds': sorted(self.running_task_ids),
            'taskConcurrency': self.task_concurrency,
        }
            
    async def handle_config_update(self, config_data):
        """处理配置更新"""
        log(f"[Server] 收到配置更新: {list(config_data.keys())}")
        self.config = config_data
        llm_config = config_data.get('llm') if isinstance(config_data.get('llm'), dict) else {}
        video_config = config_data.get('videoAnalysis') if isinstance(config_data.get('videoAnalysis'), dict) else {}
        server_config = config_data.get('server') if isinstance(config_data.get('server'), dict) else {}
        
        # 保存完整 TOML（供 config_loader.py / ingest.py 读取）
        config_path = self.runtime_root / 'config.toml'
        config_path.parent.mkdir(parents=True, exist_ok=True)

        previous_provider_raw = _simple_config_value(config_path, 'provider', 'active', DEFAULT_PROVIDER)
        previous_provider = _normalize_provider(previous_provider_raw)
        provider_raw = llm_config.get('provider') or config_data.get('provider') or previous_provider_raw
        provider = _normalize_provider(provider_raw)
        legacy_agent_plan_payload = _is_legacy_agent_plan_provider(provider_raw)
        for endpoint_hint in _explicit_endpoint_values(config_data):
            _validate_ark_endpoint(endpoint_hint, 'doubao')
        existing_doubao_api_key = _provider_api_key(config_path, 'doubao')
        existing_doubao_endpoint = _provider_endpoint(config_path, 'doubao')
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
        existing_strategy = _simple_config_value(
            config_path,
            'models',
            'strategy',
            _provider_default(provider, 'fallback'),
        )
        existing_vault_path = _simple_config_value(config_path, 'vault', 'path')
        existing_task_concurrency = _simple_config_value(
            config_path,
            'server',
            'task_concurrency',
            str(self.task_concurrency),
        )
        existing_chunk_concurrency = _simple_config_value(
            config_path,
            'analysis',
            'chunk_concurrency',
            '2',
        )

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
        model = None if legacy_agent_plan_payload else (
            video_config.get('analyzerModel')
            or config_data.get('model')
            or config_data.get('modelId')
        )
        if not model:
            model = existing_model if provider == previous_provider else _provider_default(provider, 'model')
        fallback_model = None if legacy_agent_plan_payload else config_data.get('fallbackModel')
        if not fallback_model:
            fallback_model = existing_fallback if provider == previous_provider else _provider_default(provider, 'fallback')
        strategy_model = None if legacy_agent_plan_payload else (
            video_config.get('strategyModel')
            or config_data.get('strategyModel')
        )
        if not strategy_model:
            strategy_model = existing_strategy if provider == previous_provider else _provider_default(provider, 'fallback')
        incoming_task_concurrency = (
            _first_config_value(server_config, ('taskConcurrency', 'task_concurrency', 'concurrency'))
            or _first_config_value(config_data, ('serverTaskConcurrency', 'taskConcurrency', 'task_concurrency', 'concurrency'))
        )
        task_concurrency = _normalize_task_concurrency(
            incoming_task_concurrency if incoming_task_concurrency is not None else existing_task_concurrency,
            default=self.task_concurrency,
        )
        incoming_chunk_concurrency = (
            _first_config_value(video_config, ('chunkConcurrency', 'chunk_concurrency'))
            or _first_config_value(config_data, ('videoChunkConcurrency', 'chunkConcurrency', 'chunk_concurrency'))
        )
        chunk_concurrency = _normalize_chunk_concurrency(
            incoming_chunk_concurrency if incoming_chunk_concurrency is not None else existing_chunk_concurrency,
            default=_normalize_chunk_concurrency(existing_chunk_concurrency),
        )

        previous_active_key = _provider_api_key(config_path, previous_provider)
        if legacy_agent_plan_payload:
            # 旧扩展可能把 Agent Plan key 放在通用 apiKey，不能自动写成普通 Ark key。
            doubao_api_key = (
                _first_config_value(config_data, ('arkApiKey', 'doubaoApiKey'))
                or existing_doubao_api_key
            )
        else:
            doubao_api_key = _incoming_api_key(config_data, 'doubao', existing_doubao_api_key)
        doubao_endpoint = _safe_ark_endpoint(
            _nested_config_value(config_data, 'llm', ('arkEndpoint', 'doubaoEndpoint', 'endpoint'))
            or _first_config_value(config_data, ('arkEndpoint', 'doubaoEndpoint'))
            or (config_data.get('endpoint') if not legacy_agent_plan_payload else None)
            or existing_doubao_endpoint,
            'doubao',
        )
        if not legacy_agent_plan_payload:
            doubao_endpoint = _incoming_endpoint(config_data, 'doubao', existing_doubao_endpoint)
        current_active_key = doubao_api_key
        active_key_changed = (
            provider != previous_provider
            or previous_active_key != current_active_key
        )
        config_text = f"""[ark]
api_key = "{_toml_escape(doubao_api_key)}"
endpoint = "{_toml_escape(doubao_endpoint)}"

[provider]
active = "{_toml_escape(provider)}"

[models]
analyzer = "{_toml_escape(model)}"
strategy = "{_toml_escape(strategy_model)}"
analyzer_fallback = "{_toml_escape(fallback_model)}"

[analysis]
default_quality = "{_toml_escape(quality)}"
balanced_target_frames = 240
quality_target_frames = 1250
fps_min = 0.2
fps_max = 5.0
file_active_timeout_sec = 120
response_timeout_sec = 900
chunk_concurrency = {int(chunk_concurrency)}

[douyin]
cookie_path = "{_toml_escape(str(self.runtime_root / 'cookie' / 'douyin.txt'))}"

[vault]
path = "{_toml_escape(vault_path)}"
relative_root = "知识资产/知识入库"

[server]
enabled = true
host = "{_toml_escape(self.host)}"
port = {int(self.port)}
task_concurrency = {int(task_concurrency)}
"""

        with open(config_path, 'w') as f:
            f.write(config_text)
        os.chmod(config_path, 0o600)
        if active_key_changed:
            try:
                self.status_path('model_health.json').unlink()
            except FileNotFoundError:
                pass
        if self.task_concurrency != task_concurrency:
            self.task_concurrency = task_concurrency
            self.ensure_task_worker()
            log(f"[Server] 任务并发数已更新: {self.task_concurrency}")
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
        strategy_model = _simple_config_value(config_path, 'models', 'strategy', _provider_default(provider, 'fallback'))
        endpoint = _provider_endpoint(config_path, provider)
        chunk_concurrency = _normalize_chunk_concurrency(
            _simple_config_value(config_path, 'analysis', 'chunk_concurrency', '2')
        )
        last = _json_file(self.status_path('model_health.json')) or {}
        if not api_key:
            return {
                'ok': False,
                'state': 'missing',
                'provider': provider,
                'providerLabel': _provider_default(provider, 'label'),
                'model': model,
                'strategyModel': strategy_model,
                'endpoint': endpoint,
                'taskConcurrency': self.task_concurrency,
                'chunkConcurrency': chunk_concurrency,
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
                'strategyModel': strategy_model,
                'endpoint': endpoint,
                'taskConcurrency': self.task_concurrency,
                'chunkConcurrency': chunk_concurrency,
                'checkedAt': '',
                'message': '已配置，等待检查',
            }
        return {
            'ok': bool(last.get('ok')),
            'state': last.get('state', 'error'),
            'provider': last.get('provider', provider),
            'providerLabel': _provider_default(last.get('provider', provider), 'label'),
            'model': last.get('model', model),
            'strategyModel': strategy_model,
            'endpoint': endpoint,
            'taskConcurrency': self.task_concurrency,
            'chunkConcurrency': chunk_concurrency,
            'checkedAt': last.get('checkedAt', ''),
            'message': last.get('message', '已配置，等待检查'),
        }

    def video_analysis_status(self):
        config_path = self.config_path()
        model = _simple_config_value(config_path, 'models', 'analyzer', _provider_default(DEFAULT_PROVIDER, 'model'))
        strategy_model = _simple_config_value(config_path, 'models', 'strategy', _provider_default(DEFAULT_PROVIDER, 'fallback'))
        chunk_concurrency = _normalize_chunk_concurrency(
            _simple_config_value(config_path, 'analysis', 'chunk_concurrency', '2')
        )
        preset = 'mini' if model == _provider_default(DEFAULT_PROVIDER, 'fallback') else 'lite'
        return {
            'ok': True,
            'state': 'ready',
            'modelPreset': preset,
            'analyzerModel': model,
            'strategyModel': strategy_model,
            'chunkConcurrency': chunk_concurrency,
            'taskConcurrency': self.task_concurrency,
        }

    def _douyin_cookie_health(self, cookie_path):
        names = set()
        count = 0
        if cookie_path.exists():
            for line in cookie_path.read_text(encoding='utf-8', errors='replace').splitlines():
                if not line.strip() or line.startswith('#'):
                    continue
                parts = line.split('\t')
                if len(parts) >= 7:
                    count += 1
                    names.add(parts[-2])

        updated_at = (
            datetime.fromtimestamp(cookie_path.stat().st_mtime).isoformat()
            if cookie_path.exists()
            else ''
        )
        useful_names = {
            'msToken', 'ttwid', 's_v_web_id', 'sessionid', 'sid_guard',
            'sid_tt', 'uid_tt', 'uid_tt_ss', 'passport_auth_status',
            'passport_auth_status_ss', 'passport_csrf_token', 'odin_tt',
        }
        if count > 0 and (count < 6 or not names.intersection(useful_names)):
            return {
                'ok': False,
                'state': 'incomplete',
                'platform': 'douyin',
                'updatedAt': updated_at,
                'cookieCount': count,
                'message': 'Cookie 不完整，请打开抖音网页版登录后重新抓取',
            }
        if count > 0:
            return {
                'ok': True,
                'state': 'ready',
                'platform': 'douyin',
                'updatedAt': updated_at,
                'cookieCount': count,
            }
        return {
            'ok': False,
            'state': 'missing',
            'platform': 'douyin',
            'updatedAt': '',
            'cookieCount': 0,
        }

    def cookie_status(self):
        cookie_path = self.runtime_root / 'cookie' / 'douyin.txt'
        return self._douyin_cookie_health(cookie_path)

    def status_snapshot(self):
        return {
            'vault': self.vault_status(),
            'model': self.model_config_status(),
            'llm': self.model_config_status(),
            'videoAnalysis': self.video_analysis_status(),
            'cookie': self.cookie_status(),
            'tasks': self.task_status_snapshot(),
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
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding='utf-8')
        except Exception as exc:
            log(f"[Server] 模型状态持久化失败: {type(exc).__name__}")
        return status

    def _check_model_health_sync(self, config_data):
        config_path = self.config_path()
        llm_config = config_data.get('llm') if isinstance(config_data.get('llm'), dict) else {}
        video_config = config_data.get('videoAnalysis') if isinstance(config_data.get('videoAnalysis'), dict) else {}
        provider = _normalize_provider(
            llm_config.get('provider')
            or config_data.get('provider')
            or _simple_config_value(config_path, 'provider', 'active', DEFAULT_PROVIDER)
        )
        api_key = _incoming_api_key(config_data, provider, _provider_api_key(config_path, provider))
        endpoint = _incoming_endpoint(config_data, provider, _provider_endpoint(config_path, provider))
        model = (
            video_config.get('analyzerModel')
            or config_data.get('model')
            or config_data.get('modelId')
            or _simple_config_value(config_path, 'models', 'analyzer', _provider_default(provider, 'model'))
        )
        strategy_model = (
            video_config.get('strategyModel')
            or config_data.get('strategyModel')
            or _simple_config_value(config_path, 'models', 'strategy', _provider_default(provider, 'fallback'))
        )
        chunk_concurrency = _normalize_chunk_concurrency(
            video_config.get('chunkConcurrency')
            or config_data.get('videoChunkConcurrency')
            or config_data.get('chunkConcurrency')
            or _simple_config_value(config_path, 'analysis', 'chunk_concurrency', '2')
        )
        checked_at = datetime.now().isoformat()

        if not api_key:
            return {
                'ok': False,
                'state': 'missing',
                'provider': provider,
                'providerLabel': _provider_default(provider, 'label'),
                'model': model,
                'strategyModel': strategy_model,
                'endpoint': endpoint,
                'taskConcurrency': self.task_concurrency,
                'chunkConcurrency': chunk_concurrency,
                'checkedAt': checked_at,
                'message': f"缺少 {_provider_default(provider, 'label')} API Key",
            }

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
                    'strategyModel': strategy_model,
                    'endpoint': endpoint,
                    'taskConcurrency': self.task_concurrency,
                    'chunkConcurrency': chunk_concurrency,
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
                'strategyModel': strategy_model,
                'endpoint': endpoint,
                'taskConcurrency': self.task_concurrency,
                'chunkConcurrency': chunk_concurrency,
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
                'strategyModel': strategy_model,
                'endpoint': endpoint,
                'taskConcurrency': self.task_concurrency,
                'chunkConcurrency': chunk_concurrency,
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
        return self._douyin_cookie_health(cookie_path)
        
    async def start(self):
        """启动服务器"""
        import websockets

        logging.getLogger("websockets").setLevel(logging.CRITICAL)
        logging.getLogger("websockets.server").setLevel(logging.CRITICAL)
        log(f"[Server] 启动 WebSocket 服务器: ws://{self.host}:{self.port}")
        log(f"[Server] 任务并发数: {self.task_concurrency}")
        self.ensure_task_worker()
        await self.enqueue_pending_tasks()
        
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
