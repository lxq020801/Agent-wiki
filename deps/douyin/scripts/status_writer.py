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
        (r"\bresp-[A-Za-z0-9._-]+\b", "resp-[REDACTED]"),
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

    def __init__(self, task_id: str, status_dir: Path):
        self.task_id = task_id
        self.status_dir = Path(status_dir).expanduser()
        self.status_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.status_dir / f"{task_id}.json"
        self._state: dict[str, Any] = {
            "id": task_id,
            "ok": None,           # None=进行中, True=完成, False=失败
            "stage": "queued",
            "started_at": time.time(),
            "updated_at": time.time(),
            "progress": {},       # 各阶段进度细节
        }
        self._write()

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
        self._state["updated_at"] = now
        self._write()

    def progress(self, sub_stage: str, info: dict[str, Any]) -> None:
        """记录细粒度进度（嵌进 progress dict 里）。"""
        now = time.time()
        safe_info = _redact_status_value(info)
        self._state["stage"] = sub_stage
        progress_item = {
            **safe_info,
            "at": now,
        }
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
