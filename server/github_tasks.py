"""Durable GitHub batch, item, and post-create task state."""
from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any


TERMINAL_ITEM_STATES = {"succeeded", "failed", "existing", "cancelled"}
ACTIVE_BATCH_STATES = {"queued", "running"}
_EVENT_PROCESS_LOCK = threading.RLock()


def _now() -> str:
    return datetime.now().isoformat()


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(path)


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _selection_fingerprint(repositories: list[dict[str, Any]]) -> str:
    identities = sorted(
        (
            f"id:{int(item.get('id') or 0)}"
            if int(item.get("id") or 0)
            else f"name:{str(item.get('fullName') or '').lower()}"
        )
        for item in repositories
    )
    return hashlib.sha256("\n".join(identities).encode("utf-8")).hexdigest()


def _event_key(repository: dict[str, Any], asset_path: str) -> str:
    repository_id = int(repository.get("id") or 0)
    identity = str(repository_id) if repository_id else str(repository.get("fullName") or "").lower()
    identity = f"{identity}:{asset_path}"
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


class GitHubTaskStore:
    """Small JSON task store designed for popup disconnects and service restarts."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)
        self.batch_root = self.root / "batches"
        self.event_root = self.root / "asset-events"
        self._lock = threading.RLock()
        self.batches: dict[str, dict[str, Any]] = {}
        self.events: dict[str, dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        recovered: list[dict[str, Any]] = []
        for path in sorted(self.batch_root.glob("*.json")):
            batch = _read_json(path)
            if not batch or not str(batch.get("id") or ""):
                continue
            changed = False
            if batch.get("state") == "running":
                batch["state"] = "queued"
                batch["recoveredAt"] = _now()
                changed = True
            for item in batch.get("items") or []:
                if isinstance(item, dict) and item.get("state") == "running":
                    item["state"] = "queued"
                    item["recoveredAt"] = _now()
                    changed = True
                if (
                    isinstance(item, dict)
                    and batch.get("cancelRequested")
                    and item.get("state") == "queued"
                ):
                    item["state"] = "cancelled"
                    item["result"] = {
                        "ok": False,
                        "state": "cancelled",
                        "repository": dict(item.get("repository") or {}),
                    }
                    changed = True
            self._recount(batch)
            if batch.get("cancelRequested") and not any(
                isinstance(item, dict) and item.get("state") in {"queued", "running"}
                for item in batch.get("items") or []
            ):
                batch["state"] = "cancelled"
            self.batches[str(batch["id"])] = batch
            if changed:
                recovered.append(batch)
        for batch in recovered:
            self._write_batch(batch)
        for path in sorted(self.event_root.glob("*.json")):
            event = _read_json(path)
            if event and str(event.get("id") or ""):
                self.events[str(event["id"])] = event

    def _write_batch(self, batch: dict[str, Any]) -> None:
        batch["updatedAt"] = _now()
        _atomic_json(self.batch_root / f"{batch['id']}.json", batch)

    def _write_event(self, event: dict[str, Any]) -> None:
        event["updatedAt"] = _now()
        _atomic_json(self.event_root / f"{event['id']}.json", event)

    @contextlib.contextmanager
    def _event_transaction(self):
        self.root.mkdir(parents=True, exist_ok=True)
        lock_path = self.root / "asset-events.lock"
        with _EVENT_PROCESS_LOCK:
            with lock_path.open("a+", encoding="utf-8") as lock_file:
                os.chmod(lock_path, 0o600)
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    @staticmethod
    def _recount(batch: dict[str, Any]) -> None:
        items = [item for item in batch.get("items") or [] if isinstance(item, dict)]
        batch["total"] = len(items)
        batch["completed"] = sum(item.get("state") in TERMINAL_ITEM_STATES for item in items)
        batch["succeeded"] = sum(item.get("state") == "succeeded" for item in items)
        batch["existing"] = sum(item.get("state") == "existing" for item in items)
        batch["failed"] = sum(item.get("state") == "failed" for item in items)
        batch["cancelled"] = sum(item.get("state") == "cancelled" for item in items)
        batch["warningCount"] = sum(
            isinstance(item.get("autoStar"), dict)
            and bool(item["autoStar"].get("attempted"))
            and not bool(item["autoStar"].get("ok"))
            for item in items
        )

    @staticmethod
    def public_batch(batch: dict[str, Any]) -> dict[str, Any]:
        items = []
        results = []
        for stored in batch.get("items") or []:
            if not isinstance(stored, dict):
                continue
            item = {
                "taskId": str(stored.get("taskId") or ""),
                "operationId": str(stored.get("operationId") or ""),
                "parentId": str(stored.get("parentId") or ""),
                "state": str(stored.get("state") or "queued"),
                "repository": dict(stored.get("repository") or {}),
                "assetPath": str(stored.get("assetPath") or ""),
                "autoStar": dict(stored.get("autoStar") or {}),
            }
            error = stored.get("error")
            if isinstance(error, dict):
                item["error"] = dict(error)
            result = stored.get("result")
            if isinstance(result, dict):
                item["result"] = dict(result)
                if stored.get("state") in TERMINAL_ITEM_STATES:
                    results.append(dict(result))
            items.append(item)
        return {
            "id": str(batch.get("id") or ""),
            "operationId": str(batch.get("operationId") or ""),
            "parentId": str(batch.get("parentId") or ""),
            "kind": str(batch.get("kind") or "stars_import"),
            "state": str(batch.get("state") or "queued"),
            "total": int(batch.get("total") or 0),
            "completed": int(batch.get("completed") or 0),
            "succeeded": int(batch.get("succeeded") or 0),
            "existing": int(batch.get("existing") or 0),
            "failed": int(batch.get("failed") or 0),
            "cancelled": int(batch.get("cancelled") or 0),
            "warningCount": int(batch.get("warningCount") or 0),
            "cancelRequested": bool(batch.get("cancelRequested")),
            "createdAt": str(batch.get("createdAt") or ""),
            "updatedAt": str(batch.get("updatedAt") or ""),
            "items": items,
            "results": results,
        }

    def create_batch(
        self,
        repositories: list[dict[str, Any]],
        *,
        request_key: str = "",
        operation_id: str = "",
        parent_id: str = "",
    ) -> tuple[dict[str, Any], bool]:
        fingerprint = _selection_fingerprint(repositories)
        clean_request_key = str(request_key or "")[:256]
        with self._lock:
            for batch in self.batches.values():
                same_request = bool(clean_request_key and batch.get("requestKey") == clean_request_key)
                same_selection = batch.get("selectionFingerprint") == fingerprint
                if same_request or same_selection:
                    return self.public_batch(batch), False
            batch_id = uuid.uuid4().hex
            created_at = _now()
            batch = {
                "schemaVersion": 1,
                "id": batch_id,
                "operationId": str(operation_id or f"github-import-{batch_id}"),
                "parentId": str(parent_id or ""),
                "kind": "stars_import",
                "state": "queued",
                "requestKey": clean_request_key,
                "selectionFingerprint": fingerprint,
                "cancelRequested": False,
                "createdAt": created_at,
                "updatedAt": created_at,
                "items": [
                    {
                        "taskId": uuid.uuid4().hex,
                        "operationId": f"github-item-{uuid.uuid4().hex}",
                        "parentId": str(operation_id or f"github-import-{batch_id}"),
                        "state": "queued",
                        "repository": dict(repository),
                        "assetPath": "",
                        "autoStar": {},
                        "createdAt": created_at,
                        "updatedAt": created_at,
                    }
                    for repository in repositories
                ],
            }
            self._recount(batch)
            self.batches[batch_id] = batch
            self._write_batch(batch)
            return self.public_batch(batch), True

    def get_batch(self, batch_id: str, *, public: bool = True) -> dict[str, Any] | None:
        with self._lock:
            batch = self.batches.get(str(batch_id or ""))
            if not batch:
                return None
            return self.public_batch(batch) if public else batch

    def recent_batches(self, limit: int = 10) -> list[dict[str, Any]]:
        with self._lock:
            batches = sorted(
                self.batches.values(),
                key=lambda item: str(item.get("updatedAt") or item.get("createdAt") or ""),
                reverse=True,
            )
            return [self.public_batch(batch) for batch in batches[: max(1, min(50, limit))]]

    def pending_batch_ids(self) -> list[str]:
        with self._lock:
            return [
                str(batch["id"])
                for batch in self.batches.values()
                if batch.get("state") in ACTIVE_BATCH_STATES
            ]

    def begin_batch(self, batch_id: str) -> dict[str, Any] | None:
        with self._lock:
            batch = self.batches.get(str(batch_id or ""))
            if not batch:
                return None
            if batch.get("state") == "queued":
                batch["state"] = "running"
                self._write_batch(batch)
            return self.public_batch(batch)

    def queued_items(self, batch_id: str) -> list[dict[str, Any]]:
        with self._lock:
            batch = self.batches.get(str(batch_id or ""))
            if not batch:
                return []
            return [
                {
                    "taskId": str(item.get("taskId") or ""),
                    "operationId": str(item.get("operationId") or ""),
                    "parentId": str(item.get("parentId") or batch.get("operationId") or ""),
                    "repository": dict(item.get("repository") or {}),
                }
                for item in batch.get("items") or []
                if isinstance(item, dict) and item.get("state") == "queued"
            ]

    def begin_item(self, batch_id: str, task_id: str) -> dict[str, Any] | None:
        with self._lock:
            batch = self.batches.get(str(batch_id or ""))
            if not batch or batch.get("cancelRequested"):
                return None
            for item in batch.get("items") or []:
                if item.get("taskId") == task_id and item.get("state") == "queued":
                    item["state"] = "running"
                    item["updatedAt"] = _now()
                    batch["state"] = "running"
                    self._recount(batch)
                    self._write_batch(batch)
                    return {
                        "taskId": task_id,
                        "operationId": str(item.get("operationId") or ""),
                        "parentId": str(item.get("parentId") or batch.get("operationId") or ""),
                        "repository": dict(item.get("repository") or {}),
                    }
            return None

    def complete_item(self, batch_id: str, task_id: str, result: dict[str, Any]) -> dict[str, Any] | None:
        with self._lock:
            batch = self.batches.get(str(batch_id or ""))
            if not batch:
                return None
            for item in batch.get("items") or []:
                if item.get("taskId") != task_id:
                    continue
                state = "existing" if result.get("state") == "existing" else "succeeded"
                item.update({
                    "state": state,
                    "result": dict(result),
                    "assetPath": str(result.get("assetPath") or ""),
                    "autoStar": dict(result.get("autoStar") or {}),
                    "updatedAt": _now(),
                })
                break
            self._recount(batch)
            self._write_batch(batch)
            return self.public_batch(batch)

    def fail_item(
        self,
        batch_id: str,
        task_id: str,
        *,
        code: str,
        message: str,
        repository: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        with self._lock:
            batch = self.batches.get(str(batch_id or ""))
            if not batch:
                return None
            for item in batch.get("items") or []:
                if item.get("taskId") != task_id:
                    continue
                public_repository = dict(repository or item.get("repository") or {})
                error = {"code": str(code or "import_failed"), "message": str(message or "GitHub 入库失败。")}
                result = {"ok": False, "state": "failed", "repository": public_repository, **error}
                item.update({"state": "failed", "error": error, "result": result, "updatedAt": _now()})
                break
            self._recount(batch)
            self._write_batch(batch)
            return self.public_batch(batch)

    def cancel_batch(self, batch_id: str) -> dict[str, Any] | None:
        with self._lock:
            batch = self.batches.get(str(batch_id or ""))
            if not batch:
                return None
            batch["cancelRequested"] = True
            for item in batch.get("items") or []:
                if item.get("state") == "queued":
                    item["state"] = "cancelled"
                    item["result"] = {
                        "ok": False,
                        "state": "cancelled",
                        "repository": dict(item.get("repository") or {}),
                    }
                    item["updatedAt"] = _now()
            self._finalize_locked(batch)
            self._write_batch(batch)
            return self.public_batch(batch)

    def finalize_batch(self, batch_id: str) -> dict[str, Any] | None:
        with self._lock:
            batch = self.batches.get(str(batch_id or ""))
            if not batch:
                return None
            self._finalize_locked(batch)
            self._write_batch(batch)
            return self.public_batch(batch)

    def _finalize_locked(self, batch: dict[str, Any]) -> None:
        self._recount(batch)
        active = any(
            isinstance(item, dict) and item.get("state") in {"queued", "running"}
            for item in batch.get("items") or []
        )
        if active:
            batch["state"] = "running"
        elif batch.get("cancelRequested"):
            batch["state"] = "cancelled"
        else:
            batch["state"] = "completed"

    def ensure_asset_event(
        self,
        repository: dict[str, Any],
        asset_path: str,
        *,
        source: str,
        auto_star_enabled: bool,
    ) -> tuple[dict[str, Any], bool]:
        event_id = _event_key(repository, asset_path)
        with self._lock:
            with self._event_transaction():
                existing = self.events.get(event_id) or _read_json(self.event_root / f"{event_id}.json")
                if existing:
                    self.events[event_id] = existing
                    return dict(existing), False
                created_at = _now()
                event = {
                    "schemaVersion": 1,
                    "id": event_id,
                    "kind": "github_asset_created",
                    "state": "succeeded",
                    "source": str(source or "direct"),
                    "repository": dict(repository),
                    "assetPath": str(asset_path),
                    "autoStar": {
                        "enabled": bool(auto_star_enabled),
                        "state": "pending" if auto_star_enabled else "disabled",
                        "attempted": False,
                        "ok": True,
                    },
                    "createdAt": created_at,
                    "updatedAt": created_at,
                }
                self.events[event_id] = event
                self._write_event(event)
                return dict(event), True

    def finish_asset_event_star(self, event_id: str, result: dict[str, Any]) -> dict[str, Any] | None:
        with self._lock:
            with self._event_transaction():
                event_id = str(event_id or "")
                event = _read_json(self.event_root / f"{event_id}.json") or self.events.get(event_id)
                if not event:
                    return None
                auto_star = dict(result)
                auto_star["enabled"] = True
                auto_star["state"] = "succeeded" if auto_star.get("ok") else "failed"
                event["autoStar"] = auto_star
                self.events[event_id] = event
                self._write_event(event)
                return dict(event)

    def recent_events(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            values = sorted(
                self.events.values(),
                key=lambda item: str(item.get("updatedAt") or item.get("createdAt") or ""),
                reverse=True,
            )
            return [dict(item) for item in values[: max(1, min(100, limit))]]

    def pending_star_events(self) -> list[dict[str, Any]]:
        with self._lock:
            return [
                dict(event)
                for event in self.events.values()
                if isinstance(event.get("autoStar"), dict)
                and event["autoStar"].get("state") == "pending"
            ]
