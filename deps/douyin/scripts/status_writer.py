"""
status_writer.py — 写 ~/.agent-wiki/status/{id}.json

原子写入（临时文件 + rename），避免 Agent/调试工具读到半个文件。
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any

try:
    from server.operation_audit import OperationAuditStore, artifact_references
except ImportError:  # pragma: no cover - standalone compatibility
    OperationAuditStore = None  # type: ignore[assignment,misc]
    artifact_references = lambda _value: []  # type: ignore[assignment]


_REDACTED_KEYS = {
    "authorization",
    "bearer",
    "cookie",
    "setcookie",
    "responseid",
    "previousresponseid",
    "githubtoken",
    "accesstoken",
    "privatetoken",
}
_MAX_AUDIT_EVENTS = 2000


def _canonical_secret_key(key: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(key).lower())


def _is_sensitive_key(key: Any) -> bool:
    canonical = _canonical_secret_key(key)
    return canonical in _REDACTED_KEYS or canonical.endswith("apikey")


def _redact_status_text(text: str) -> str:
    cleaned = str(text)
    patterns = [
        (r"(?i)Bearer\s+[A-Za-z0-9._~+/=-]+", "Bearer [REDACTED]"),
        (
            r"(?i)[\"']?(api[_-]?key|ark[_-]?api[_-]?key|agent[_-]?plan[_-]?api[_-]?key|"
            r"arkApiKey|agentPlanApiKey|response[_-]?id|previous[_-]?response[_-]?id|"
            r"responseId|previousResponseId)[\"']?\s*[:=]\s*"
            r"(\"[^\"]*\"|'[^']*'|[^,\s}\]\n\r]+)",
            "sensitive=[REDACTED]",
        ),
        (
            r"(?i)[\"']?(authorization|cookie|set-cookie)[\"']?\s*[:=]\s*[^\n\r]+",
            "sensitive=[REDACTED]",
        ),
        (r"(?i)(https?://)[^/\s:@]+:[^/\s@]+@", r"\1[REDACTED]@"),
        (r"\bresp[-_][A-Za-z0-9._-]+\b", "resp_[REDACTED]"),
        (r"\bghp_[A-Za-z0-9_]{20,}\b", "ghp_[REDACTED]"),
        (r"\bgithub_pat_[A-Za-z0-9_]{20,}\b", "github_pat_[REDACTED]"),
        (r"(?i)(access_token|private_token|github_token)=([^&\s]+)", r"\1=[REDACTED]"),
        (r"(?i)([?&][^=&#]*(token|key|secret|signature|sig)[^=&#]*=)[^&#\s]+", r"\1[REDACTED]"),
    ]
    for pattern, repl in patterns:
        cleaned = re.sub(pattern, repl, cleaned)
    return cleaned


def _redact_status_value(value: Any) -> Any:
    """Remove transient secrets from local task status files."""
    if isinstance(value, dict):
        clean: dict[str, Any] = {}
        for key, child in value.items():
            if _is_sensitive_key(key):
                continue
            else:
                clean[key] = _redact_status_value(child)
        return clean
    if isinstance(value, list):
        return [_redact_status_value(item) for item in value]
    if isinstance(value, str):
        return _redact_status_text(value)
    return value


class StatusWriter:
    """单个任务的 status 写手。

    每次更新都是写整个 JSON 覆盖。P0 主要供 Agent 诊断；
    扩展任务状态面板留到后续版本。
    """

    def __init__(
        self,
        task_id: str,
        status_dir: Path,
        *,
        operation_id: str | None = None,
        parent_id: str | None = None,
        operation_type: str | None = None,
    ):
        self.task_id = task_id
        self.status_dir = Path(status_dir).expanduser()
        self.status_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.status_dir / f"{task_id}.json"
        self.operation_id = str(
            operation_id
            or os.environ.get("AGENT_WIKI_OPERATION_ID")
            or f"task-{task_id}"
        )
        self.parent_id = str(parent_id or os.environ.get("AGENT_WIKI_PARENT_OPERATION_ID") or "")
        self.operation_type = str(
            operation_type
            or os.environ.get("AGENT_WIKI_OPERATION_TYPE")
            or "task.ingest"
        )
        self.audit_store = None
        if OperationAuditStore is not None:
            try:
                self.audit_store = OperationAuditStore(self.status_dir.parent)
                self.audit_store.ensure_operation(
                    self.operation_id,
                    operation_type=self.operation_type,
                    task_id=task_id,
                    parent_id=self.parent_id,
                    params={"source": "status_writer"},
                    stage="task_process_started",
                )
                self.audit_store.record_event(
                    self.operation_id,
                    stage="task_process_started",
                    state="started",
                    result={"statusPath": str(self.path)},
                    related={"tasks": [task_id]},
                )
            except Exception:
                self.audit_store = None
        self._state: dict[str, Any] = {
            "id": task_id,
            "operation_id": self.operation_id,
            "parent_id": self.parent_id,
            "diagnostics": (
                self.audit_store.diagnostics_ref(self.operation_id)
                if self.audit_store is not None else {}
            ),
            "ok": None,           # None=进行中, True=完成, False=失败
            "stage": "queued",
            "started_at": time.time(),
            "updated_at": time.time(),
            "progress": {},       # 各阶段进度细节
            "audit_events": [],   # 按发生顺序保留，可完整回放一次运行
            "audit_event_count": 0,
        }
        self._append_event("queued", {}, self._state["started_at"])
        self._write()

    def _append_event(self, stage: str, info: dict[str, Any], at: float) -> None:
        safe_info = _redact_status_value(info)
        sequence = int(self._state.get("audit_event_count") or 0) + 1
        self._state["audit_event_count"] = sequence
        events = self._state.setdefault("audit_events", [])
        events.append({
            "sequence": sequence,
            "stage": str(stage),
            "at": at,
            "info": safe_info,
        })
        if len(events) > _MAX_AUDIT_EVENTS:
            del events[:len(events) - _MAX_AUDIT_EVENTS]

    def update(self, *, stage: str | None = None, ok: bool | None = None,
               error: str | None = None, **fields: Any) -> None:
        """更新状态。任意 kwargs 会合并进 state。"""
        now = time.time()
        if stage is not None:
            self._state["stage"] = stage
        if ok is not None:
            self._state["ok"] = ok
            if ok is True or ok is False:
                self._state.setdefault("finished_at", now)
                elapsed = max(0.0, now - float(self._state.get("started_at") or now))
                self._state["elapsed_sec"] = round(elapsed, 1)
                self._state["task_duration_sec"] = round(elapsed, 1)
        if error is not None:
            self._state["error"] = _redact_status_text(error)
        for k, v in fields.items():
            if _is_sensitive_key(k):
                continue
            else:
                self._state[k] = _redact_status_value(v)
        event_stage = stage
        if event_stage is None and "cost_estimate" in fields:
            event_stage = "cost_estimated"
        if event_stage is not None:
            event_info = dict(fields)
            if ok is not None:
                event_info["ok"] = ok
            if error is not None:
                event_info["error"] = error
            self._append_event(event_stage, event_info, now)
        self._state["updated_at"] = now
        self._write()
        if self.audit_store is not None and event_stage is not None:
            event_state = "succeeded" if ok is True else "failed" if ok is False else "started"
            audit_error = error if event_state == "failed" else None
            artifact_input = fields.get("audit_artifacts") or fields.get("derived_audit_artifacts")
            related_assets = fields.get("assets") if isinstance(fields.get("assets"), list) else []
            direct_asset = fields.get("vault_path") or fields.get("assetPath")
            try:
                self.audit_store.record_event(
                    self.operation_id,
                    stage=str(event_stage),
                    state=event_state,
                    result={key: value for key, value in fields.items() if key not in {"audit_artifacts", "derived_audit_artifacts"}},
                    error=audit_error,
                    error_code=str(fields.get("error_kind") or "task_failed"),
                    retryable=bool(fields.get("recoverable")),
                    related={
                        "tasks": [self.task_id],
                        "assets": [
                            item.get("vault_path") or item.get("assetPath")
                            for item in related_assets
                            if isinstance(item, dict) and (item.get("vault_path") or item.get("assetPath"))
                        ] + ([direct_asset] if direct_asset else []),
                    },
                    artifacts=artifact_references(artifact_input),
                )
            except Exception:
                pass

    def progress(self, sub_stage: str, info: dict[str, Any]) -> None:
        """记录细粒度进度（嵌进 progress dict 里）。"""
        now = time.time()
        safe_info = _redact_status_value(info)
        self._state["stage"] = sub_stage
        progress_item = {
            **safe_info,
            "at": now,
        }
        self._append_event(sub_stage, safe_info, now)
        self._state.setdefault("progress", {})[sub_stage] = progress_item
        part_index = safe_info.get("part_index") if isinstance(safe_info, dict) else None
        if part_index is not None:
            key = str(part_index)
            chunk_progress = self._state.setdefault("chunk_progress", {})
            chunk_item = chunk_progress.setdefault(key, {"part_index": part_index})
            chunk_item[sub_stage] = progress_item
            chunk_item["updated_at"] = now
        self._state["updated_at"] = now
        self._write()
        if self.audit_store is not None:
            artifact_input = safe_info.get("audit_artifacts") if isinstance(safe_info, dict) else None
            try:
                self.audit_store.record_event(
                    self.operation_id,
                    stage=str(sub_stage),
                    state="started",
                    result=safe_info,
                    related={"tasks": [self.task_id]},
                    artifacts=artifact_references(artifact_input),
                )
            except Exception:
                pass

    def _write(self) -> None:
        """原子写：tmp 文件 + rename。"""
        # 同目录建 tmp 保证 rename 原子（跨设备 rename 不原子）
        fd, tmp = tempfile.mkstemp(
            prefix=f".{self.task_id}.", suffix=".json.tmp",
            dir=str(self.status_dir),
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self._state, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
        except Exception:
            try:
                os.unlink(tmp)
            except FileNotFoundError:
                pass
            raise

    @property
    def state(self) -> dict[str, Any]:
        return dict(self._state)


def write_terminal(task_id: str, status_dir: Path, payload: dict[str, Any]) -> None:
    """直接覆盖写一个终态（用于初始化失败等场景，没有 writer 实例）。"""
    sw = StatusWriter(task_id, status_dir)
    sw.update(**payload)
