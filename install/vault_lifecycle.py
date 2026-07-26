#!/usr/bin/env python3
"""Safe folder selection, initialization, and identity-based reconnection."""
from __future__ import annotations

import json
import os
import re
import time
import unicodedata
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

try:
    from install.vault_discovery import (
        obsidian_registry_vault_paths,
        write_vault_path_to_config,
    )
except ImportError:
    from vault_discovery import (
        obsidian_registry_vault_paths,
        write_vault_path_to_config,
    )


CONTRACT_VERSION = 1
VAULT_IDENTITY_FILENAME = ".agent-wiki-vault.json"
VAULT_REGISTRY_FILENAME = "vault-registry.json"
VAULT_CANDIDATES_FILENAME = "vault-lifecycle-candidates.json"
VAULT_IDENTITY_SCHEMA_VERSION = 1
VAULT_REGISTRY_SCHEMA_VERSION = 1
PRODUCT_ID = "agent-wiki"
DEFAULT_OBSIDIAN_ROOT_NAME = "Obsidian"
MINIMAL_VAULT_DIRECTORIES = ("raw", "知识资产")
SCAN_EXCLUDED_NAMES = frozenset({
    ".git",
    ".obsidian",
})
CANDIDATE_TTL_SECONDS = 15 * 60

VAULT_LIFECYCLE_REQUEST_TYPES = frozenset({
    "vault_scan",
    "vault_select_folder",
    "vault_select_confirm",
})
VAULT_LIFECYCLE_RESPONSE_TYPE = "vault_lifecycle_status"

# This constant is intentionally machine-readable so UI and protocol tests can
# pin the wire-level operation names and common result fields in one place.
VAULT_LIFECYCLE_CONTRACT = {
    "contractVersion": CONTRACT_VERSION,
    "responseType": VAULT_LIFECYCLE_RESPONSE_TYPE,
    "requests": {
        "vault_scan": {"required": [], "optional": ["parentHints"]},
        "vault_select_folder": {"required": [], "optional": []},
        "vault_select_confirm": {"required": ["selectionId"], "optional": []},
    },
    "resultFields": [
        "contractVersion",
        "ok",
        "operation",
        "state",
        "requiresUserAction",
        "message",
        "activeVault",
        "obsidianRoots",
        "vaultCandidates",
        "selection",
    ],
}


class VaultLifecycleError(ValueError):
    """A stable, user-actionable lifecycle failure."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _contains_protected_component(path: Path) -> bool:
    return any(part.casefold() == ".obsidian" for part in path.parts)


def _is_obsidian_icloud_container(path: Path) -> bool:
    parts = tuple(part.casefold() for part in path.parts)
    return parts[-3:] == ("library", "mobile documents", "icloud~md~obsidian")


def _existing_directory(value: Path | str, field: str) -> Path:
    raw = str(value or "").strip()
    if not raw:
        raise VaultLifecycleError(f"{field}_required", "请选择一个文件夹。")
    path = Path(raw).expanduser()
    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise VaultLifecycleError(f"{field}_invalid", "所选文件夹不存在或无法访问。") from exc
    if (
        not resolved.is_dir()
        or _contains_protected_component(resolved)
        or _is_obsidian_icloud_container(resolved)
    ):
        raise VaultLifecycleError(
            f"{field}_invalid",
            "请选择知识库文件夹，不能选择 .obsidian 内部或 iCloud 的 Obsidian 应用容器层。",
        )
    return resolved


def normalize_user_name(value: str) -> str:
    name = unicodedata.normalize("NFC", str(value or "").strip())
    if not name:
        raise VaultLifecycleError("user_name_required", "所选文件夹必须有可用的名称。")
    if name in {".", ".."} or len(name) > 80:
        raise VaultLifecycleError("user_name_invalid", "文件夹名称长度必须在 1 到 80 个字符之间。")
    if any(char in name for char in "/\\:\0") or any(ord(char) < 32 for char in name):
        raise VaultLifecycleError("user_name_invalid", "文件夹名称包含不支持的字符。")
    return name


def _default_obsidian_roots() -> list[Path]:
    home = Path.home()
    roots = [
        home / DEFAULT_OBSIDIAN_ROOT_NAME,
        home / "Documents" / DEFAULT_OBSIDIAN_ROOT_NAME,
        home / "Library/Mobile Documents/iCloud~md~obsidian/Documents",
        home / "Library/Mobile Documents/com~apple~CloudDocs" / DEFAULT_OBSIDIAN_ROOT_NAME,
    ]
    cloud_storage = home / "Library/CloudStorage"
    if cloud_storage.exists() and cloud_storage.is_dir():
        try:
            roots.extend(path / DEFAULT_OBSIDIAN_ROOT_NAME for path in cloud_storage.iterdir() if path.is_dir())
        except OSError:
            pass
    return [path for path in roots if path.exists() and path.is_dir()]


def _atomic_json_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _atomic_json_create(path: Path, payload: dict[str, Any]) -> None:
    """Create a JSON file once without replacing an entry that appears concurrently."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    descriptor: Optional[int] = None
    try:
        descriptor = os.open(path, flags, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            descriptor = None
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(path, 0o600)
    except FileExistsError as exc:
        raise VaultLifecycleError(
            "identity_marker_conflict",
            "文件夹中的知识库身份标记已发生变化，请重新选择。",
        ) from exc
    except Exception:
        if descriptor is not None:
            os.close(descriptor)
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        raise


def _read_json(path: Path) -> Optional[dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    return payload if isinstance(payload, dict) else None


def inspect_vault_identity(vault_path: Path | str) -> tuple[str, Optional[dict[str, Any]]]:
    try:
        vault = _existing_directory(vault_path, "vault_path")
    except VaultLifecycleError:
        return "missing_vault", None
    marker = vault / VAULT_IDENTITY_FILENAME
    if marker.is_symlink():
        return "invalid", None
    if not marker.is_file():
        return "missing", None
    payload = _read_json(marker)
    if not payload:
        return "invalid", None
    vault_id = str(payload.get("vaultId") or "").strip()
    user_name = str(payload.get("userName") or "").strip()
    try:
        normalized_vault_id = str(uuid.UUID(vault_id))
    except (ValueError, AttributeError):
        normalized_vault_id = ""
    if (
        payload.get("schemaVersion") != VAULT_IDENTITY_SCHEMA_VERSION
        or payload.get("product") != PRODUCT_ID
        or not normalized_vault_id
    ):
        return "invalid", None
    try:
        normalized_name = normalize_user_name(user_name)
    except VaultLifecycleError:
        return "invalid", None
    return "valid", {
        "schemaVersion": VAULT_IDENTITY_SCHEMA_VERSION,
        "product": PRODUCT_ID,
        "vaultId": normalized_vault_id,
        "userName": normalized_name,
        "createdAt": str(payload.get("createdAt") or ""),
    }


def _write_vault_identity(
    vault_path: Path,
    *,
    vault_id: str,
    user_name: str,
    created_at: str,
    overwrite: bool = True,
) -> dict[str, Any]:
    payload = {
        "schemaVersion": VAULT_IDENTITY_SCHEMA_VERSION,
        "product": PRODUCT_ID,
        "vaultId": vault_id.lower(),
        "userName": normalize_user_name(user_name),
        "createdAt": created_at,
    }
    marker = vault_path / VAULT_IDENTITY_FILENAME
    if overwrite:
        _atomic_json_write(marker, payload)
    else:
        _atomic_json_create(marker, payload)
    return payload


def _ensure_minimal_vault_structure(
    vault_path: Path,
    *,
    identity: dict[str, Any],
    create_marker: bool = True,
    overwrite_marker: bool = True,
) -> None:
    for relative in MINIMAL_VAULT_DIRECTORIES:
        (vault_path / relative).mkdir(parents=True, exist_ok=True)
    index = vault_path / "index.md"
    if not index.exists():
        date_text = str(identity.get("createdAt") or _now_iso())[:10]
        index.write_text(
            f"# 知识库索引\n> 最后更新：{date_text} | 资产总数：0\n",
            encoding="utf-8",
        )
    if create_marker:
        _write_vault_identity(
            vault_path,
            vault_id=str(identity["vaultId"]),
            user_name=str(identity["userName"]),
            created_at=str(identity["createdAt"]),
            overwrite=overwrite_marker,
        )


def _validate_in_place_initialization_target(vault_path: Path) -> None:
    marker = vault_path / VAULT_IDENTITY_FILENAME
    if os.path.lexists(marker):
        raise VaultLifecycleError(
            "identity_marker_conflict",
            "文件夹中已有无效或冲突的知识库身份标记，未进行任何覆盖。",
        )
    index = vault_path / "index.md"
    if index.exists() and not index.is_file():
        raise VaultLifecycleError(
            "vault_index_conflict",
            "文件夹中的 index.md 不是普通文件，无法安全初始化。",
        )
    for relative in MINIMAL_VAULT_DIRECTORIES:
        target = vault_path / relative
        if target.exists() and not target.is_dir():
            raise VaultLifecycleError(
                "vault_directory_conflict",
                f"文件夹中的 {relative} 不是目录，无法安全初始化。",
            )


def _validate_minimal_vault(vault_path: Path, expected_identity: dict[str, Any]) -> None:
    if not (vault_path / "index.md").is_file():
        raise VaultLifecycleError("vault_validation_failed", "未能安全创建知识库索引。")
    for relative in MINIMAL_VAULT_DIRECTORIES:
        if not (vault_path / relative).is_dir():
            raise VaultLifecycleError("vault_validation_failed", f"未能安全创建知识库目录：{relative}")
    state, identity = inspect_vault_identity(vault_path)
    if (
        state != "valid"
        or not identity
        or identity["vaultId"] != str(expected_identity["vaultId"]).lower()
        or identity["userName"] != expected_identity["userName"]
    ):
        raise VaultLifecycleError("vault_validation_failed", "知识库身份标记校验失败。")


def _empty_registry() -> dict[str, Any]:
    return {
        "schemaVersion": VAULT_REGISTRY_SCHEMA_VERSION,
        "activeVaultId": "",
        "vaults": {},
    }


class VaultLifecycleManager:
    """Stateful lifecycle facade with injectable discovery inputs for isolation."""

    def __init__(
        self,
        *,
        runtime_root: Path,
        config_path: Optional[Path] = None,
        registry_vault_provider: Optional[Callable[[], Iterable[Path]]] = None,
        obsidian_root_provider: Optional[Callable[[], Iterable[Path]]] = None,
        uuid_factory: Optional[Callable[[], Any]] = None,
    ) -> None:
        self.runtime_root = runtime_root.expanduser().resolve()
        self.config_path = (config_path or self.runtime_root / "config.toml").expanduser()
        self.registry_vault_provider = registry_vault_provider or obsidian_registry_vault_paths
        self.obsidian_root_provider = obsidian_root_provider or _default_obsidian_roots
        self.uuid_factory = uuid_factory or uuid.uuid4

    @property
    def registry_path(self) -> Path:
        return self.runtime_root / VAULT_REGISTRY_FILENAME

    @property
    def candidates_path(self) -> Path:
        return self.runtime_root / "status" / VAULT_CANDIDATES_FILENAME

    def _new_uuid(self) -> str:
        return str(self.uuid_factory()).lower()

    def _registry(self) -> dict[str, Any]:
        payload = _read_json(self.registry_path)
        if (
            not payload
            or payload.get("schemaVersion") != VAULT_REGISTRY_SCHEMA_VERSION
            or not isinstance(payload.get("vaults"), dict)
        ):
            return _empty_registry()
        return payload

    def _write_registry(self, registry: dict[str, Any]) -> None:
        _atomic_json_write(self.registry_path, registry)

    def _base_result(self, operation: str, **values: Any) -> dict[str, Any]:
        result = {
            "contractVersion": CONTRACT_VERSION,
            "ok": False,
            "operation": operation,
            "state": "error",
            "requiresUserAction": True,
            "message": "",
            "activeVault": None,
            "obsidianRoots": [],
            "vaultCandidates": [],
            "selection": None,
        }
        result.update(values)
        return result

    def error_result(self, operation: str, error: VaultLifecycleError) -> dict[str, Any]:
        return self._base_result(
            operation,
            state="error",
            errorCode=error.code,
            message=str(error),
        )

    def _entry_payload(
        self,
        *,
        identity: dict[str, Any],
        path: Path,
        origin: str,
    ) -> dict[str, Any]:
        return {
            "vaultId": identity["vaultId"],
            "userName": identity["userName"],
            "vaultPath": str(path),
            "identityMarker": VAULT_IDENTITY_FILENAME,
            "origin": origin,
            "updatedAt": _now_iso(),
        }

    def _activate(
        self,
        *,
        identity: dict[str, Any],
        path: Path,
        origin: str,
    ) -> dict[str, Any]:
        path = _existing_directory(path, "vault_path")
        state, actual = inspect_vault_identity(path)
        if state != "valid" or not actual or actual["vaultId"] != identity["vaultId"]:
            raise VaultLifecycleError("identity_mismatch", "知识库身份在连接前发生变化，请重新选择。")
        previous = self._registry()
        updated = json.loads(json.dumps(previous))
        updated["vaults"][identity["vaultId"]] = self._entry_payload(
            identity=actual,
            path=path,
            origin=origin,
        )
        updated["activeVaultId"] = identity["vaultId"]
        self._write_registry(updated)
        try:
            write_vault_path_to_config(self.config_path, path)
        except Exception:
            self._write_registry(previous)
            raise
        return updated["vaults"][identity["vaultId"]]

    def status(self) -> dict[str, Any]:
        registry = self._registry()
        active_id = str(registry.get("activeVaultId") or "")
        if not active_id:
            return self._base_result(
                "status",
                state="first_use",
                message="请选择一个文件夹作为 Agent-wiki 知识库。",
            )
        entry = registry["vaults"].get(active_id)
        if not isinstance(entry, dict):
            return self._base_result(
                "status",
                state="registry_invalid",
                errorCode="active_vault_missing",
                message="当前知识库记录不完整，请重新选择知识库。",
            )
        active = {
            "vaultId": active_id,
            "userName": str(entry.get("userName") or ""),
            "vaultPath": str(entry.get("vaultPath") or ""),
            "identityMarker": VAULT_IDENTITY_FILENAME,
        }
        identity_state, identity = inspect_vault_identity(active["vaultPath"])
        if identity_state == "missing_vault":
            return self._base_result(
                "status",
                state="disconnected",
                message="原知识库位置已变化，正在按稳定身份重新扫描。",
                activeVault=active,
            )
        if (
            identity_state != "valid"
            or not identity
            or identity["vaultId"] != active_id
            or identity["userName"] != active["userName"]
        ):
            return self._base_result(
                "status",
                state="identity_mismatch",
                errorCode="identity_mismatch",
                message="当前路径的知识库身份与保存记录不一致，请重新选择。",
                activeVault=active,
            )
        active["identityState"] = "valid"
        return self._base_result(
            "status",
            ok=True,
            state="ready",
            requiresUserAction=False,
            message="Agent-wiki 知识库已就绪。",
            activeVault=active,
        )

    def _root_candidates(
        self,
        *,
        parent_hints: Iterable[Path | str] = (),
    ) -> tuple[list[dict[str, Any]], list[Path]]:
        registry_vaults: list[Path] = []
        for raw in self.registry_vault_provider():
            try:
                registry_vaults.append(_existing_directory(raw, "registry_vault"))
            except VaultLifecycleError:
                continue

        roots_by_path: dict[str, dict[str, Any]] = {}

        def add_root(raw: Path | str, source: str) -> None:
            try:
                root = _existing_directory(raw, "obsidian_root")
            except VaultLifecycleError:
                return
            key = str(root)
            candidate = roots_by_path.setdefault(key, {
                "kind": "obsidian_root",
                "obsidianRoot": key,
                "sources": [],
                "writable": os.access(root, os.W_OK),
            })
            if source not in candidate["sources"]:
                candidate["sources"].append(source)

        for root in self.obsidian_root_provider():
            add_root(root, "common_obsidian_root")
        for vault in registry_vaults:
            add_root(vault.parent, "obsidian_registry_parent")
        for hint in parent_hints:
            add_root(hint, "user_parent_hint")

        roots = sorted(roots_by_path.values(), key=lambda item: item["obsidianRoot"])
        return roots, registry_vaults

    def _vault_candidates(
        self,
        roots: list[dict[str, Any]],
        registry_vaults: list[Path],
    ) -> list[dict[str, Any]]:
        registry = self._registry()
        active_id = str(registry.get("activeVaultId") or "")
        active_entry = registry.get("vaults", {}).get(active_id) or {}
        active_name = str(active_entry.get("userName") or "")
        paths: dict[str, tuple[Path, bool]] = {}

        for vault in registry_vaults:
            paths[str(vault)] = (vault, True)
        for root_item in roots:
            root = Path(root_item["obsidianRoot"])
            try:
                children = sorted(root.iterdir())
            except OSError:
                continue
            for child in children:
                if (
                    child.name.casefold() in SCAN_EXCLUDED_NAMES
                    or child.name.startswith(".agent-wiki-")
                    or not child.is_dir()
                ):
                    continue
                marker = child / VAULT_IDENTITY_FILENAME
                if marker.is_file():
                    paths.setdefault(str(child.resolve()), (child.resolve(), False))

        candidates: list[dict[str, Any]] = []
        for path, _from_registry in sorted(paths.values(), key=lambda item: str(item[0])):
            identity_state, identity = inspect_vault_identity(path)
            if identity_state == "valid" and identity:
                match_state = "none"
                if identity["vaultId"] == active_id and identity["userName"] == active_name:
                    match_state = "active_identity"
                elif active_name and identity["userName"] == active_name:
                    match_state = "name_only"
                candidates.append({
                    "kind": "agent_wiki_vault",
                    "vaultPath": str(path),
                    "vaultId": identity["vaultId"],
                    "userName": identity["userName"],
                    "identityMarker": VAULT_IDENTITY_FILENAME,
                    "identityState": "valid",
                    "matchState": match_state,
                })
        return candidates

    def scan(
        self,
        *,
        parent_hints: Iterable[Path | str] = (),
    ) -> dict[str, Any]:
        roots, registry_vaults = self._root_candidates(parent_hints=parent_hints)
        vaults = self._vault_candidates(roots, registry_vaults)

        current = self.status()
        exact = [item for item in vaults if item.get("matchState") == "active_identity"]
        if current["state"] == "disconnected" and len(exact) == 1:
            selected = exact[0]
            identity_state, identity = inspect_vault_identity(selected["vaultPath"])
            if identity_state == "valid" and identity:
                active = self._activate(
                    identity=identity,
                    path=Path(selected["vaultPath"]),
                    origin="reconnected",
                )
                current = self._base_result(
                    "scan",
                    ok=True,
                    state="reconnected",
                    requiresUserAction=False,
                    message="已通过稳定身份重新连接移动后的知识库。",
                    activeVault=active,
                )
        elif current["state"] == "disconnected" and len(exact) > 1:
            current = self._base_result(
                "scan",
                state="ambiguous",
                message="扫描到多个相同身份的知识库，未自动连接，请手动选择。",
                activeVault=current.get("activeVault"),
            )
        elif current["state"] == "first_use" and len(vaults) == 1:
            selected = vaults[0]
            identity_state, identity = inspect_vault_identity(selected["vaultPath"])
            if identity_state == "valid" and identity:
                active = self._activate(
                    identity=identity,
                    path=Path(selected["vaultPath"]),
                    origin="auto_reconnected",
                )
                current = self._base_result(
                    "scan",
                    ok=True,
                    state="reconnected",
                    requiresUserAction=False,
                    message="已自动重新连接唯一匹配的 Agent-wiki 知识库。",
                    activeVault=active,
                )
        elif current["state"] == "first_use":
            current = self._base_result(
                "scan",
                state="selection_required",
                message=(
                    "扫描到多个有效身份的知识库，未自动连接，请点击“选择知识库”确认。"
                    if len(vaults) > 1
                    else "未自动连接知识库，请点击“选择知识库”选择一个文件夹。"
                ),
            )
        else:
            current["operation"] = "scan"

        current["obsidianRoots"] = roots
        current["vaultCandidates"] = vaults
        return current

    def _cache_selected_folder(self, vault: Path) -> dict[str, Any]:
        selection_id = f"selection-{self._new_uuid()}"
        selected = {
            "candidateId": selection_id,
            "kind": "selected_folder",
            "vaultPath": str(vault),
            "folderName": vault.name,
        }
        _atomic_json_write(self.candidates_path, {
            "contractVersion": CONTRACT_VERSION,
            "createdAtEpoch": time.time(),
            "expiresAtEpoch": time.time() + CANDIDATE_TTL_SECONDS,
            "items": {selection_id: selected},
        })
        return selected

    def selection_interrupted(
        self,
        *,
        state: str,
        message: str,
        error_code: str = "",
    ) -> dict[str, Any]:
        values: dict[str, Any] = {
            "ok": state == "cancelled",
            "state": state,
            "requiresUserAction": False,
            "message": message,
        }
        if error_code:
            values["errorCode"] = error_code
        return self._base_result("select_folder", **values)

    def _initialize_selected_folder(self, vault: Path) -> dict[str, Any]:
        _validate_in_place_initialization_target(vault)
        identity = {
            "vaultId": self._new_uuid(),
            "userName": normalize_user_name(vault.name),
            "createdAt": _now_iso(),
        }
        _ensure_minimal_vault_structure(
            vault,
            identity=identity,
            overwrite_marker=False,
        )
        _validate_minimal_vault(vault, identity)
        active = self._activate(identity=identity, path=vault, origin="selected_initialized")
        return self._base_result(
            "select_folder",
            ok=True,
            state="initialized",
            requiresUserAction=False,
            message="已在所选文件夹中补齐 Agent-wiki 必要结构，已有内容保持不变。",
            activeVault=active,
        )

    def select_folder(self, *, vault_path: Path | str) -> dict[str, Any]:
        vault = _existing_directory(vault_path, "selected_folder")
        if not os.access(vault, os.W_OK):
            raise VaultLifecycleError("selected_folder_not_writable", "所选文件夹不可写，请选择其他文件夹。")
        identity_state, identity = inspect_vault_identity(vault)
        if identity_state == "valid" and identity:
            active = self._activate(identity=identity, path=vault, origin="selected")
            return self._base_result(
                "select_folder",
                ok=True,
                state="selected",
                requiresUserAction=False,
                message="已选择并连接 Agent-wiki 知识库。",
                activeVault=active,
            )
        if identity_state == "invalid":
            raise VaultLifecycleError(
                "identity_marker_invalid",
                "所选文件夹中的 Agent-wiki 身份标记无效，未修改任何内容。",
            )
        try:
            non_empty = next(vault.iterdir(), None) is not None
        except OSError as exc:
            raise VaultLifecycleError("selected_folder_unreadable", "无法读取所选文件夹。") from exc
        if not non_empty:
            return self._initialize_selected_folder(vault)
        selected = self._cache_selected_folder(vault)
        return self._base_result(
            "select_folder",
            ok=True,
            state="confirmation_required",
            requiresUserAction=True,
            message="所选文件夹已有内容。确认后只会补齐缺失的 Agent-wiki 必要结构，不会覆盖、复制、迁移或删除任何已有文件。",
            selection={
                "selectionId": selected["candidateId"],
                "folderName": selected["folderName"],
                "nonEmpty": True,
                "identityState": "missing",
            },
        )

    def confirm_selection(self, *, selection_id: str) -> dict[str, Any]:
        payload = _read_json(self.candidates_path) or {}
        if float(payload.get("expiresAtEpoch") or 0) < time.time():
            raise VaultLifecycleError("selection_expired", "选择记录已过期，请重新选择文件夹。")
        candidate = (payload.get("items") or {}).get(str(selection_id or ""))
        if not isinstance(candidate, dict):
            raise VaultLifecycleError("selection_not_found", "未找到选择记录，请重新选择文件夹。")
        if candidate.get("kind") != "selected_folder":
            raise VaultLifecycleError("selection_invalid", "选择记录无效，请重新选择文件夹。")
        vault = _existing_directory(candidate.get("vaultPath", ""), "selected_folder")
        identity_state, identity = inspect_vault_identity(vault)
        if identity_state == "valid" and identity:
            active = self._activate(identity=identity, path=vault, origin="selected_confirmed")
            return self._base_result(
                "select_confirm",
                ok=True,
                state="selected",
                requiresUserAction=False,
                message="已选择并连接 Agent-wiki 知识库。",
                activeVault=active,
            )
        if identity_state == "invalid":
            raise VaultLifecycleError(
                "identity_marker_invalid",
                "确认前知识库身份标记已发生变化，未修改任何内容。",
            )
        initialized = self._initialize_selected_folder(vault)
        initialized["operation"] = "select_confirm"
        return initialized


def dispatch_vault_lifecycle(
    manager: VaultLifecycleManager,
    message_type: str,
    data: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Dispatch a stable wire operation without coupling it to WebSocket code."""
    payload = data if isinstance(data, dict) else {}
    operation = str(message_type or "").removeprefix("vault_")
    try:
        if message_type == "vault_scan":
            return manager.scan(
                parent_hints=payload.get("parentHints") or (),
            )
        if message_type == "vault_select_folder":
            return manager.select_folder(vault_path=payload.get("selectedPath", ""))
        if message_type == "vault_select_confirm":
            return manager.confirm_selection(selection_id=payload.get("selectionId", ""))
        raise VaultLifecycleError("operation_unsupported", "Unsupported vault lifecycle operation")
    except VaultLifecycleError as error:
        return manager.error_result(operation, error)
