"""
status_writer.py — 写 ~/.obsidian-librarian/status/{id}.json

原子写入（临时文件 + rename），避免 Agent/调试工具读到半个文件。
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any


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
        if stage is not None:
            self._state["stage"] = stage
        if ok is not None:
            self._state["ok"] = ok
        if error is not None:
            self._state["error"] = error
        for k, v in fields.items():
            self._state[k] = v
        self._state["updated_at"] = time.time()
        self._write()

    def progress(self, sub_stage: str, info: dict[str, Any]) -> None:
        """记录细粒度进度（嵌进 progress dict 里）。"""
        self._state.setdefault("progress", {})[sub_stage] = {
            **info,
            "at": time.time(),
        }
        self._state["updated_at"] = time.time()
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
