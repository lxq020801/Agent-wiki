"""Durable, redacted operation timelines shared by every Agent-wiki entrypoint.

The audit store intentionally keeps only compact diagnostic summaries. Large
prompts, model responses, and media artifacts stay in their owning
``run-artifacts`` directory and are referenced from the timeline.
"""
from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import re
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


SCHEMA_VERSION = 1
TERMINAL_STATES = frozenset({"succeeded", "failed", "cancelled"})
EVENT_STATES = frozenset({"started", *TERMINAL_STATES})
DEFAULT_OPERATION_TYPE = "system.operation"
MAX_TEXT_LENGTH = 1200
MAX_COLLECTION_ITEMS = 40
MAX_DEPTH = 6

_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()
_SECRET_KEY_MARKERS = (
    "authorization",
    "cookie",
    "setcookie",
    "apikey",
    "accesstoken",
    "refreshtoken",
    "githubtoken",
    "privatetoken",
    "clientsecret",
    "devicecode",
    "usercode",
    "password",
    "passwd",
    "credential",
    "secret",
    "rawauth",
    "authresponse",
    "responseid",
    "previousresponseid",
)
_SENSITIVE_QUERY_MARKERS = (
    "token",
    "key",
    "secret",
    "signature",
    "sig",
    "code",
    "authorization",
)
_SECRET_PATTERNS = (
    (re.compile(r"(?i)Bearer\s+[A-Za-z0-9._~+/=-]+"), "Bearer [REDACTED]"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{12,}\b"), "github_[REDACTED]"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{12,}\b"), "github_pat_[REDACTED]"),
    (re.compile(r"\bresp[-_][A-Za-z0-9._-]+\b"), "resp_[REDACTED]"),
    (
        re.compile(
            r"(?i)(authorization|cookie|set-cookie|api[_-]?key|access[_-]?token|"
            r"refresh[_-]?token|device[_-]?code|user[_-]?code)\s*[:=]\s*"
            r"(?:\"[^\"]*\"|'[^']*'|[^,\s}\]]+)"
        ),
        "sensitive=[REDACTED]",
    ),
)

# The titles are the acceptance matrix supplied for this implementation. Tests
# bind every category to stage markers in executable modules, not documentation.
AUDIT_COVERAGE_MATRIX: dict[int, dict[str, Any]] = {
    1: {
        "title": "扩展与控制面：扩展启动、握手、版本兼容、状态刷新、设置保存、Cookie 同步、用户提交/取消/重试以及服务回复。",
        "modules": ("server/websocket_server.py", "chrome-extension/background.js", "chrome-extension/popup/popup.js"),
        "stages": ("extension_connected", "extension_request_received", "handshake_compatibility_checked", "config_file_written", "cookie_file_written", "service_reply_sent", "task_cancel", "task_retry"),
    },
    2: {
        "title": "通用任务系统：任务接收、入队、排队、并发调度、worker 启动、阶段切换、超时、重试、取消、完成、失败和服务重启恢复。",
        "modules": ("server/websocket_server.py", "server/operation_audit.py", "deps/douyin/scripts/status_writer.py"),
        "stages": ("task_queued", "task_enqueued", "worker_started", "subprocess_started", "task_timeout", "retry_queued", "task_cancelled", "service_restart_recovery"),
    },
    3: {
        "title": "来源获取：URL/来源识别、元数据读取、下载、Cookie 可用性结果、ffprobe/媒体探测、文件校验；不记录 Cookie 本身。",
        "modules": ("deps/douyin/scripts/ingest.py", "deps/douyin/scripts/analyzer.py"),
        "stages": ("source_identified", "cookie_availability_checked", "source_metadata_read", "download_file_validated", "probed_duration"),
    },
    4: {
        "title": "视频拆解：预扫描、画面变化、自动/固定 FPS 决策、分段、实际抽帧、上传/模型请求、响应元数据、Token 与成本、汇总和失败兜底。",
        "modules": ("deps/douyin/scripts/analyzer.py", "deps/douyin/scripts/ingest.py", "deps/douyin/scripts/status_writer.py"),
        "stages": ("prescanning_started", "fps_decided", "chunking_plan", "chunk_uploading", "analyzing", "analysis_retrying", "cost_estimated", "synthesizing_chunks"),
    },
    5: {
        "title": "图文/网页/GitHub 等其他来源：来源抓取、清洗、统一模型处理和结果验证。",
        "modules": ("deps/douyin/scripts/ingest.py", "deps/douyin/scripts/derive_executor.py", "server/websocket_server.py"),
        "stages": ("downloaded_images", "resolving_target", "analyzing_derived_target", "derived_output_validated", "github_repository_search_completed"),
    },
    6: {
        "title": "知识资产生成：简洁概括、完整整理、AI 分析的生成阶段，结构解析/校验，标题、标签、文件命名、文件写入、索引更新；大型 prompt/完整响应继续放现有 run-artifacts，只在统一时间线保存摘要与引用。",
        "modules": ("deps/douyin/scripts/ingest.py", "server/operation_audit.py"),
        "stages": ("concise_summary_generated", "complete_content_generated", "ai_analysis_generated", "asset_structure_parsed", "asset_fields_validated", "asset_title_selected", "asset_tags_selected", "asset_filename_selected", "asset_file_written", "asset_index_updated"),
    },
    7: {
        "title": "派生策略全流程：候选产生、证据、筛选/保留/忽略原因、GitHub 官方 API 目标解析、歧义待确认、子任务创建、派生执行、父子关系、已有资产去重、成功/失败。",
        "modules": ("deps/douyin/scripts/derive_strategy.py", "deps/douyin/scripts/derive_executor.py", "server/websocket_server.py"),
        "stages": ("derived_candidates_ready", "resolving_target", "target_resolved", "derived_task_created", "derived_output_validated", "derived_parent_child_linked", "existing_asset_linked"),
    },
    8: {
        "title": "GitHub 登录、Stars、资产、自动 Star、刷新。",
        "modules": ("server/websocket_server.py", "server/github_service.py", "server/github_tasks.py"),
        "stages": ("github_auth_start_completed", "github_stars_request_completed", "batch_item_completed", "autoStar", "github_refresh_confirm_completed"),
    },
    9: {
        "title": "知识库扫描、新建、切换、迁移和回退。",
        "modules": ("server/websocket_server.py", "install/vault_lifecycle.py"),
        "stages": ("vault_scan", "vault_create", "vault_switch", "vault_migration_preview", "vault_migration_execute", "vault_migration_rollback", "vault_lifecycle_completed"),
    },
}


def default_runtime_root() -> Path:
    raw = os.environ.get("AGENT_WIKI_HOME")
    return Path(raw).expanduser() if raw else Path.home() / ".agent-wiki"


def new_operation_id(prefix: str = "op") -> str:
    clean = re.sub(r"[^a-z0-9_-]+", "-", str(prefix or "op").lower()).strip("-")
    return f"{clean or 'op'}-{uuid.uuid4().hex}"


def normalize_identifier(value: Any, *, prefix: str = "op", generate: bool = False) -> str:
    clean = re.sub(r"[^A-Za-z0-9._:-]+", "-", str(value or "").strip())[:160].strip("-.:_")
    if clean:
        return clean
    return new_operation_id(prefix) if generate else ""


def _canonical_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def _is_secret_key(value: Any) -> bool:
    canonical = _canonical_key(value)
    return any(marker in canonical for marker in _SECRET_KEY_MARKERS)


def _clean_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return value
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return value
    hostname = parsed.hostname or ""
    try:
        port_number = parsed.port
    except ValueError:
        port_number = None
    port = f":{port_number}" if port_number else ""
    netloc = f"{hostname}{port}"
    query = [
        (key, child)
        for key, child in parse_qsl(parsed.query, keep_blank_values=True)
        if not any(marker in key.lower() for marker in _SENSITIVE_QUERY_MARKERS)
    ]
    return urlunsplit((parsed.scheme.lower(), netloc, parsed.path, urlencode(query), ""))


def redact_text(value: Any, *, limit: int = MAX_TEXT_LENGTH) -> str:
    text = str(value or "")
    if text.startswith(("http://", "https://")):
        text = _clean_url(text)
    text = re.sub(r"(?i)(https?://)[^/\s:@]+:[^/\s@]+@", r"\1[REDACTED]@", text)
    for pattern, replacement in _SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    text = re.sub(
        r"(?i)([?&][^=&#]*(?:token|key|secret|signature|sig|code)[^=&#]*=)[^&#\s]+",
        r"\1[REDACTED]",
        text,
    )
    if len(text) > limit:
        digest = hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:16]
        text = f"{text[:limit]}...[truncated sha256:{digest} chars:{len(text)}]"
    return text


def redact_value(value: Any, *, _depth: int = 0) -> Any:
    """Return a compact diagnostic projection with strict deny-list redaction."""
    if _depth >= MAX_DEPTH:
        return "[MAX_DEPTH]"
    if isinstance(value, Mapping):
        clean: dict[str, Any] = {}
        for index, (key, child) in enumerate(value.items()):
            if index >= MAX_COLLECTION_ITEMS:
                clean["_truncated"] = len(value) - MAX_COLLECTION_ITEMS
                break
            name = redact_text(key, limit=120)
            clean[name] = "[REDACTED]" if _is_secret_key(key) else redact_value(child, _depth=_depth + 1)
        return clean
    if isinstance(value, (list, tuple, set, frozenset)):
        items = list(value)
        projected = [redact_value(item, _depth=_depth + 1) for item in items[:MAX_COLLECTION_ITEMS]]
        if len(items) > MAX_COLLECTION_ITEMS:
            projected.append({"_truncated": len(items) - MAX_COLLECTION_ITEMS})
        return projected
    if isinstance(value, Path):
        return redact_text(str(value))
    if isinstance(value, str):
        return redact_text(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return redact_text(type(value).__name__)


def error_payload(
    error: Any = None,
    *,
    code: str = "operation_failed",
    stage: str = "",
    retryable: bool = False,
    error_type: str = "",
) -> dict[str, Any]:
    if isinstance(error, Mapping):
        raw = dict(error)
        return {
            "code": redact_text(raw.get("code") or code, limit=120),
            "type": redact_text(raw.get("type") or raw.get("errorType") or error_type or "Error", limit=120),
            "stage": redact_text(raw.get("stage") or stage, limit=160),
            "message": redact_text(raw.get("message") or raw.get("error") or code),
            "retryable": bool(raw.get("retryable", raw.get("recoverable", retryable))),
        }
    return {
        "code": redact_text(code, limit=120),
        "type": redact_text(error_type or (type(error).__name__ if isinstance(error, BaseException) else "Error"), limit=120),
        "stage": redact_text(stage, limit=160),
        "message": redact_text(str(error or code)),
        "retryable": bool(retryable),
    }


def artifact_references(value: Any) -> list[dict[str, Any]]:
    """Normalize artifact metadata without reading or embedding artifact content."""
    if not value:
        return []
    if isinstance(value, Mapping) and any(key in value for key in ("path", "ref", "dir")):
        candidates = [value]
    elif isinstance(value, Mapping):
        candidates = [{"kind": key, "ref": child} for key, child in value.items()]
    elif isinstance(value, (list, tuple, set, frozenset)):
        candidates = list(value)
    else:
        candidates = [value]
    refs: list[dict[str, Any]] = []
    for candidate in candidates[:MAX_COLLECTION_ITEMS]:
        if isinstance(candidate, Mapping):
            ref = candidate.get("ref") or candidate.get("path") or candidate.get("dir")
            if isinstance(ref, Mapping):
                ref = ref.get("path") or ref.get("ref") or ""
            item = {
                "kind": redact_text(candidate.get("kind") or candidate.get("type") or "artifact", limit=80),
                "ref": redact_text(ref or "", limit=600),
            }
            if candidate.get("sha256"):
                item["sha256"] = redact_text(candidate.get("sha256"), limit=80)
            if isinstance(candidate.get("bytes"), int):
                item["bytes"] = max(0, int(candidate["bytes"]))
        else:
            item = {"kind": "artifact", "ref": redact_text(candidate, limit=600)}
        if item["ref"]:
            refs.append(item)
    return refs


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _thread_lock(path: Path) -> threading.RLock:
    key = str(path)
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, threading.RLock())


def _ensure_private_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink() or not path.is_dir():
        raise OSError(f"unsafe diagnostics directory: {path}")
    os.chmod(path, 0o700)


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    _ensure_private_dir(path.parent)
    if path.is_symlink():
        raise OSError(f"refusing to replace symlinked diagnostics file: {path}")
    fd, raw_tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    tmp = Path(raw_tmp)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
        os.chmod(path, 0o600)
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    return payload if isinstance(payload, dict) else None


def _ensure_jsonl_boundary(path: Path) -> None:
    try:
        with path.open("rb+") as handle:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                return
            handle.seek(-1, os.SEEK_END)
            if handle.read(1) == b"\n":
                return
            handle.seek(0, os.SEEK_END)
            handle.write(b"\n")
            handle.flush()
            os.fsync(handle.fileno())
    except FileNotFoundError:
        return


def _read_timeline(path: Path, operation_id: str = "") -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    events: list[dict[str, Any]] = []
    for line in lines:
        try:
            event = json.loads(line)
            sequence = int(event.get("sequence") or 0) if isinstance(event, dict) else 0
        except (ValueError, TypeError):
            continue
        if not isinstance(event, dict) or sequence <= 0:
            continue
        if operation_id and normalize_identifier(event.get("operationId")) != operation_id:
            continue
        events.append(event)
    events.sort(key=lambda event: (int(event.get("sequence") or 0), str(event.get("timestamp") or "")))
    return events


def _nonnegative_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError, OverflowError):
        return 0


class OperationAuditStore:
    """Append-only per-operation timelines with a rebuildable public index."""

    def __init__(self, runtime_root: Path | str | None = None) -> None:
        self.runtime_root = Path(runtime_root or default_runtime_root()).expanduser()
        self.root = self.runtime_root / "operations"
        self.operations_root = self.root / "by-id"
        self.index_path = self.root / "index.jsonl"
        self.lock_path = self.root / ".operations.lock"
        _ensure_private_dir(self.operations_root)

    @contextlib.contextmanager
    def _transaction(self) -> Iterator[None]:
        _ensure_private_dir(self.root)
        with _thread_lock(self.lock_path):
            with self.lock_path.open("a+", encoding="utf-8") as handle:
                os.chmod(self.lock_path, 0o600)
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def operation_dir(self, operation_id: str) -> Path:
        clean = normalize_identifier(operation_id)
        if not clean:
            raise ValueError("operation_id is required")
        return self.operations_root / clean

    def diagnostics_ref(self, operation_id: str) -> dict[str, Any]:
        clean = normalize_identifier(operation_id)
        relative = f"operations/by-id/{clean}" if clean else "operations"
        return {
            "operationId": clean,
            "root": str(self.root),
            "index": str(self.index_path),
            "timeline": str(self.runtime_root / relative / "timeline.jsonl") if clean else "",
            "summary": str(self.runtime_root / relative / "summary.json") if clean else "",
        }

    def _summary_from_events(self, operation_id: str, events: list[dict[str, Any]]) -> dict[str, Any] | None:
        if not events:
            return None
        first = events[0]
        last = events[-1]
        state = "started"
        result: dict[str, Any] = {}
        error: dict[str, Any] = {}
        related: dict[str, list[Any]] = {"tasks": [], "assets": [], "batches": []}
        artifacts: list[dict[str, Any]] = []
        params: dict[str, Any] = {}
        for event in events:
            event_state = str(event.get("state") or "started")
            if not (state in TERMINAL_STATES and event_state == "started"):
                state = event_state if event_state in EVENT_STATES else "started"
            if not params and isinstance(event.get("params"), dict) and event["params"]:
                params = dict(event["params"])
            if isinstance(event.get("result"), dict) and event["result"]:
                result = dict(event["result"])
            if isinstance(event.get("error"), dict) and event["error"]:
                error = dict(event["error"])
            elif event_state in {"succeeded", "cancelled"}:
                error = {}
            event_related = event.get("related") if isinstance(event.get("related"), dict) else {}
            for key in related:
                values = event_related.get(key)
                for value in values if isinstance(values, list) else ([values] if values else []):
                    if value not in related[key]:
                        related[key].append(value)
            event_artifacts = event.get("artifacts") if isinstance(event.get("artifacts"), list) else []
            for ref in event_artifacts:
                if isinstance(ref, dict) and ref not in artifacts:
                    artifacts.append(ref)
        return {
            "schemaVersion": SCHEMA_VERSION,
            "operationId": operation_id,
            "operationType": first.get("operationType") or DEFAULT_OPERATION_TYPE,
            "taskId": normalize_identifier(first.get("taskId")),
            "parentId": normalize_identifier(first.get("parentId")),
            "state": state,
            "stage": redact_text(last.get("stage"), limit=160),
            "startedAt": str(first.get("timestamp") or _now_iso()),
            "updatedAt": str(last.get("timestamp") or _now_iso()),
            "durationMs": _nonnegative_int(last.get("durationMs")),
            "eventCount": len(events),
            "lastSequence": max(int(event.get("sequence") or 0) for event in events),
            "params": params,
            "result": result,
            "error": error,
            "related": {key: values[:MAX_COLLECTION_ITEMS] for key, values in related.items()},
            "artifacts": artifacts[:MAX_COLLECTION_ITEMS],
            "diagnostics": self.diagnostics_ref(operation_id),
        }

    @staticmethod
    def _summary_matches_events(summary: Mapping[str, Any], events: list[dict[str, Any]]) -> bool:
        try:
            if not events:
                return int(summary.get("eventCount") or 0) == 0
            return (
                int(summary.get("eventCount") or 0) == len(events)
                and int(summary.get("lastSequence") or 0) == int(events[-1].get("sequence") or 0)
            )
        except (TypeError, ValueError):
            return False

    def _load_or_repair_summary_locked(
        self,
        operation_id: str,
        op_dir: Path,
    ) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
        events = _read_timeline(op_dir / "timeline.jsonl", operation_id)
        summary_path = op_dir / "summary.json"
        summary = _read_json(summary_path)
        if events and (not summary or not self._summary_matches_events(summary, events)):
            summary = self._summary_from_events(operation_id, events)
            if summary:
                _atomic_json(summary_path, summary)
        return summary, events

    def ensure_operation(
        self,
        operation_id: str | None = None,
        *,
        operation_type: str = DEFAULT_OPERATION_TYPE,
        task_id: str = "",
        parent_id: str = "",
        params: Mapping[str, Any] | None = None,
        stage: str = "accepted",
    ) -> dict[str, Any]:
        operation_id = normalize_identifier(operation_id, prefix="op", generate=True)
        op_dir = self.operation_dir(operation_id)
        summary_path = op_dir / "summary.json"
        with self._transaction():
            existing, _events = self._load_or_repair_summary_locked(operation_id, op_dir)
            if existing:
                changed = False
                if task_id and not existing.get("taskId"):
                    existing["taskId"] = normalize_identifier(task_id)
                    changed = True
                if parent_id and not existing.get("parentId"):
                    existing["parentId"] = normalize_identifier(parent_id)
                    changed = True
                if operation_type and existing.get("operationType") == DEFAULT_OPERATION_TYPE:
                    existing["operationType"] = redact_text(operation_type, limit=160)
                    changed = True
                if changed:
                    existing["updatedAt"] = _now_iso()
                    _atomic_json(summary_path, existing)
                return existing
            now = _now_iso()
            summary = {
                "schemaVersion": SCHEMA_VERSION,
                "operationId": operation_id,
                "operationType": redact_text(operation_type or DEFAULT_OPERATION_TYPE, limit=160),
                "taskId": normalize_identifier(task_id),
                "parentId": normalize_identifier(parent_id),
                "state": "started",
                "stage": redact_text(stage, limit=160),
                "startedAt": now,
                "updatedAt": now,
                "durationMs": 0,
                "eventCount": 0,
                "lastSequence": 0,
                "params": redact_value(params or {}),
                "result": {},
                "error": {},
                "related": {"tasks": [], "assets": [], "batches": []},
                "artifacts": [],
                "diagnostics": self.diagnostics_ref(operation_id),
            }
            _atomic_json(summary_path, summary)
        return self.record_event(
            operation_id,
            stage=stage,
            state="started",
            params=params or {},
        )["summary"]

    def record_event(
        self,
        operation_id: str,
        *,
        stage: str,
        state: str = "started",
        params: Mapping[str, Any] | None = None,
        result: Mapping[str, Any] | None = None,
        error: Any = None,
        error_code: str = "operation_failed",
        retryable: bool = False,
        related: Mapping[str, Any] | None = None,
        artifacts: Any = None,
        timestamp: str | None = None,
        duration_ms: int | float | None = None,
    ) -> dict[str, Any]:
        state = state if state in EVENT_STATES else "started"
        operation_id = normalize_identifier(operation_id)
        if not operation_id:
            raise ValueError("operation_id is required")
        op_dir = self.operation_dir(operation_id)
        summary_path = op_dir / "summary.json"
        timeline_path = op_dir / "timeline.jsonl"
        with self._transaction():
            summary = _read_json(summary_path)
            if not summary:
                summary, _events = self._load_or_repair_summary_locked(operation_id, op_dir)
            if not summary:
                raise KeyError(f"unknown operation: {operation_id}")
            sequence = int(summary.get("lastSequence") or 0) + 1
            now = timestamp or _now_iso()
            started = summary.get("startedAt") or now
            if duration_ms is None:
                try:
                    started_dt = datetime.fromisoformat(str(started).replace("Z", "+00:00"))
                    now_dt = datetime.fromisoformat(str(now).replace("Z", "+00:00"))
                    duration_ms = max(0, round((now_dt - started_dt).total_seconds() * 1000))
                except ValueError:
                    duration_ms = max(0, round((time.time() - time.time()) * 1000))
            safe_error = error_payload(
                error,
                code=error_code,
                stage=stage,
                retryable=retryable,
            ) if error is not None or state == "failed" else {}
            safe_related = redact_value(related or {})
            refs = artifact_references(artifacts)
            event = {
                "schemaVersion": SCHEMA_VERSION,
                "eventId": uuid.uuid4().hex,
                "sequence": sequence,
                "timestamp": now,
                "operationId": operation_id,
                "operationType": summary.get("operationType") or DEFAULT_OPERATION_TYPE,
                "taskId": summary.get("taskId") or "",
                "parentId": summary.get("parentId") or "",
                "stage": redact_text(stage, limit=160),
                "state": state,
                "durationMs": max(0, round(float(duration_ms or 0))),
                "params": redact_value(params or {}),
                "result": redact_value(result or {}),
                "error": safe_error,
                "related": safe_related,
                "artifacts": refs,
            }
            _ensure_private_dir(op_dir)
            _ensure_jsonl_boundary(timeline_path)
            with timeline_path.open("a", encoding="utf-8") as handle:
                os.chmod(timeline_path, 0o600)
                handle.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            previous_state = str(summary.get("state") or "started")
            summary_state = previous_state if previous_state in TERMINAL_STATES and state == "started" else state
            summary.update({
                "state": summary_state,
                "stage": event["stage"],
                "updatedAt": now,
                "durationMs": event["durationMs"],
                "eventCount": int(summary.get("eventCount") or 0) + 1,
                "lastSequence": sequence,
            })
            if event["result"]:
                summary["result"] = event["result"]
            if safe_error:
                summary["error"] = safe_error
            elif state in {"succeeded", "cancelled"}:
                summary["error"] = {}
            if safe_related:
                merged = dict(summary.get("related") or {})
                for key in ("tasks", "assets", "batches"):
                    values = safe_related.get(key) if isinstance(safe_related, dict) else None
                    if values:
                        current = list(merged.get(key) or [])
                        for value in values if isinstance(values, list) else [values]:
                            if value not in current:
                                current.append(value)
                        merged[key] = current[:MAX_COLLECTION_ITEMS]
                summary["related"] = merged
            if refs:
                existing_refs = list(summary.get("artifacts") or [])
                for ref in refs:
                    if ref not in existing_refs:
                        existing_refs.append(ref)
                summary["artifacts"] = existing_refs[:MAX_COLLECTION_ITEMS]
            _atomic_json(summary_path, summary)
            index_entry = {
                "timestamp": now,
                "operationId": operation_id,
                "operationType": summary.get("operationType"),
                "taskId": summary.get("taskId") or "",
                "parentId": summary.get("parentId") or "",
                "stage": summary.get("stage"),
                "state": summary.get("state"),
                "sequence": sequence,
                "summary": str(summary_path),
            }
            _ensure_jsonl_boundary(self.index_path)
            with self.index_path.open("a", encoding="utf-8") as handle:
                os.chmod(self.index_path, 0o600)
                handle.write(json.dumps(index_entry, ensure_ascii=False, separators=(",", ":")) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
        return {"event": event, "summary": summary}

    def finish(
        self,
        operation_id: str,
        *,
        stage: str = "completed",
        state: str = "succeeded",
        result: Mapping[str, Any] | None = None,
        error: Any = None,
        error_code: str = "operation_failed",
        retryable: bool = False,
        related: Mapping[str, Any] | None = None,
        artifacts: Any = None,
    ) -> dict[str, Any]:
        if state not in TERMINAL_STATES:
            raise ValueError("terminal state must be succeeded, failed, or cancelled")
        return self.record_event(
            operation_id,
            stage=stage,
            state=state,
            result=result,
            error=error,
            error_code=error_code,
            retryable=retryable,
            related=related,
            artifacts=artifacts,
        )["summary"]

    def get(self, operation_id: str, *, include_events: bool = True) -> dict[str, Any] | None:
        try:
            op_dir = self.operation_dir(operation_id)
        except ValueError:
            return None
        with self._transaction():
            summary, events = self._load_or_repair_summary_locked(
                normalize_identifier(operation_id),
                op_dir,
            )
        if not summary:
            return None
        payload = {"summary": summary, "diagnostics": self.diagnostics_ref(operation_id)}
        if include_events:
            payload["events"] = events
        return payload

    def recover_incomplete(self, *, reason: str = "service_restart") -> list[str]:
        """Persist a recovery node for started operations without ending them."""
        recovered: list[str] = []
        for op_dir in sorted(path for path in self.operations_root.glob("*") if path.is_dir()):
            operation_id = normalize_identifier(op_dir.name)
            payload = self.get(operation_id, include_events=True)
            summary = payload.get("summary") if payload else None
            if not summary or summary.get("state") != "started":
                continue
            last_stage = str(summary.get("stage") or "")
            if last_stage == "service_restart_recovery":
                continue
            self.record_event(
                operation_id,
                stage="service_restart_recovery",
                state="started",
                result={"recovered": True, "reason": reason, "previousStage": last_stage},
                related={"tasks": [summary.get("taskId")]} if summary.get("taskId") else {},
            )
            recovered.append(operation_id)
        return recovered


class OperationWebSocket:
    """Inject correlation fields into replies and retain the final reply shape."""

    def __init__(self, websocket: Any, *, operation_id: str, task_id: str = "", parent_id: str = "") -> None:
        self._websocket = websocket
        self.operation_id = normalize_identifier(operation_id)
        self.task_id = normalize_identifier(task_id)
        self.parent_id = normalize_identifier(parent_id)
        self.sent_payloads: list[dict[str, Any]] = []

    def __getattr__(self, name: str) -> Any:
        return getattr(self._websocket, name)

    async def send(self, data: Any) -> Any:
        outgoing = data
        if isinstance(data, str):
            try:
                payload = json.loads(data)
            except (ValueError, TypeError):
                payload = None
            if isinstance(payload, dict):
                payload.setdefault("operationId", self.operation_id)
                payload.setdefault("taskId", self.task_id)
                payload.setdefault("parentId", self.parent_id)
                self.sent_payloads.append(payload)
                outgoing = json.dumps(payload, ensure_ascii=False)
        return await self._websocket.send(outgoing)
