#!/usr/bin/env python3
"""Safe first-use, switching, reconnection, and migration for Agent-wiki vaults."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
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
VAULT_MIGRATIONS_DIRNAME = "vault-migrations"
VAULT_IDENTITY_SCHEMA_VERSION = 1
VAULT_REGISTRY_SCHEMA_VERSION = 1
PRODUCT_ID = "agent-wiki"
DEFAULT_OBSIDIAN_ROOT_NAME = "Obsidian"
MINIMAL_VAULT_DIRECTORIES = ("raw", "知识资产/知识入库")
MIGRATION_EXCLUDED_NAMES = frozenset({
    ".git",
    ".obsidian",
    VAULT_IDENTITY_FILENAME.casefold(),
})
CANDIDATE_TTL_SECONDS = 15 * 60

VAULT_LIFECYCLE_REQUEST_TYPES = frozenset({
    "vault_scan",
    "vault_create",
    "vault_switch",
    "vault_candidate_confirm",
    "vault_migration_preview",
    "vault_migration_execute",
    "vault_migration_rollback",
})
VAULT_LIFECYCLE_RESPONSE_TYPE = "vault_lifecycle_status"

# This constant is intentionally machine-readable so UI and protocol tests can
# pin the wire-level operation names and common result fields in one place.
VAULT_LIFECYCLE_CONTRACT = {
    "contractVersion": CONTRACT_VERSION,
    "responseType": VAULT_LIFECYCLE_RESPONSE_TYPE,
    "requests": {
        "vault_scan": {"required": [], "optional": ["userName", "parentHints"]},
        "vault_create": {
            "required": ["userName"],
            "oneOf": ["obsidianRoot", "parentDirectory"],
        },
        "vault_switch": {"required": ["vaultPath"], "optional": ["expectedVaultId"]},
        "vault_candidate_confirm": {
            "required": ["candidateId", "action"],
            "actions": ["create", "switch", "migrate"],
        },
        "vault_migration_preview": {
            "required": ["sourcePath", "userName"],
            "oneOf": ["obsidianRoot", "parentDirectory"],
        },
        "vault_migration_execute": {"required": ["migrationId"]},
        "vault_migration_rollback": {"required": ["migrationId"]},
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
        "migration",
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


def _existing_directory(value: Path | str, field: str) -> Path:
    raw = str(value or "").strip()
    if not raw:
        raise VaultLifecycleError(f"{field}_required", f"{field} is required")
    path = Path(raw).expanduser()
    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise VaultLifecycleError(f"{field}_invalid", f"{field} must be an existing directory") from exc
    if not resolved.is_dir() or _contains_protected_component(resolved):
        raise VaultLifecycleError(
            f"{field}_invalid",
            f"{field} must be an existing directory outside .obsidian",
        )
    return resolved


def normalize_user_name(value: str) -> str:
    name = unicodedata.normalize("NFC", str(value or "").strip())
    if not name:
        raise VaultLifecycleError("user_name_required", "userName is required")
    if name in {".", ".."} or len(name) > 80:
        raise VaultLifecycleError("user_name_invalid", "userName must contain 1 to 80 safe characters")
    if any(char in name for char in "/\\:\0") or any(ord(char) < 32 for char in name):
        raise VaultLifecycleError("user_name_invalid", "userName must not contain path separators or control characters")
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
) -> dict[str, Any]:
    payload = {
        "schemaVersion": VAULT_IDENTITY_SCHEMA_VERSION,
        "product": PRODUCT_ID,
        "vaultId": vault_id.lower(),
        "userName": normalize_user_name(user_name),
        "createdAt": created_at,
    }
    _atomic_json_write(vault_path / VAULT_IDENTITY_FILENAME, payload)
    return payload


def _ensure_minimal_vault_structure(
    vault_path: Path,
    *,
    identity: dict[str, Any],
    create_marker: bool = True,
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
        )


def _validate_minimal_vault(vault_path: Path, expected_identity: dict[str, Any]) -> None:
    if not (vault_path / "index.md").is_file():
        raise VaultLifecycleError("vault_validation_failed", "vault index was not created")
    for relative in MINIMAL_VAULT_DIRECTORIES:
        if not (vault_path / relative).is_dir():
            raise VaultLifecycleError("vault_validation_failed", f"missing vault directory: {relative}")
    state, identity = inspect_vault_identity(vault_path)
    if (
        state != "valid"
        or not identity
        or identity["vaultId"] != str(expected_identity["vaultId"]).lower()
        or identity["userName"] != expected_identity["userName"]
    ):
        raise VaultLifecycleError("vault_validation_failed", "vault identity marker validation failed")


def _empty_registry() -> dict[str, Any]:
    return {
        "schemaVersion": VAULT_REGISTRY_SCHEMA_VERSION,
        "activeVaultId": "",
        "vaults": {},
    }


def _clear_vault_path_in_config(config_path: Path) -> None:
    if not config_path.exists():
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text('[vault]\npath = ""\n', encoding="utf-8")
        os.chmod(config_path, 0o600)
        return
    text = config_path.read_text(encoding="utf-8")
    if "[vault]" not in text:
        text = text.rstrip() + '\n\n[vault]\npath = ""\nrelative_root = "知识资产/知识入库"\n'
    else:
        lines = text.splitlines()
        output: list[str] = []
        in_vault = False
        replaced = False
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("[") and stripped.endswith("]"):
                if in_vault and not replaced:
                    output.append('path = ""')
                    replaced = True
                in_vault = stripped == "[vault]"
            if in_vault and stripped.startswith("path") and "=" in stripped:
                output.append('path = ""')
                replaced = True
                continue
            output.append(line)
        if in_vault and not replaced:
            output.append('path = ""')
        text = "\n".join(output) + "\n"
    config_path.write_text(text, encoding="utf-8")
    os.chmod(config_path, 0o600)


def _candidate_id(kind: str, path: Path, vault_id: str = "") -> str:
    digest = hashlib.sha256(f"{kind}\0{path}\0{vault_id}".encode("utf-8")).hexdigest()[:20]
    return f"{kind}-{digest}"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _source_manifest(source: Path) -> tuple[list[str], list[dict[str, Any]], list[dict[str, str]]]:
    directories: list[str] = []
    files: list[dict[str, Any]] = []
    conflicts: list[dict[str, str]] = []
    for current_text, dir_names, file_names in os.walk(source, topdown=True, followlinks=False):
        current = Path(current_text)
        kept_dirs: list[str] = []
        for name in sorted(dir_names):
            child = current / name
            relative = child.relative_to(source)
            if name.casefold() in MIGRATION_EXCLUDED_NAMES:
                continue
            if child.is_symlink():
                conflicts.append({
                    "code": "unsupported_symlink",
                    "relativePath": relative.as_posix(),
                })
                continue
            kept_dirs.append(name)
            directories.append(relative.as_posix())
        dir_names[:] = kept_dirs
        for name in sorted(file_names):
            path = current / name
            relative = path.relative_to(source)
            if name.casefold() in MIGRATION_EXCLUDED_NAMES:
                continue
            if path.is_symlink():
                conflicts.append({
                    "code": "unsupported_symlink",
                    "relativePath": relative.as_posix(),
                })
                continue
            try:
                mode = path.stat().st_mode
            except OSError:
                conflicts.append({"code": "unreadable_source", "relativePath": relative.as_posix()})
                continue
            if not stat.S_ISREG(mode):
                conflicts.append({"code": "unsupported_file", "relativePath": relative.as_posix()})
                continue
            try:
                digest = _sha256_file(path)
            except OSError:
                conflicts.append({"code": "unreadable_source", "relativePath": relative.as_posix()})
                continue
            files.append({
                "relativePath": relative.as_posix(),
                "size": path.stat().st_size,
                "sha256": digest,
            })
    directories.sort()
    files.sort(key=lambda item: item["relativePath"])
    return directories, files, conflicts


def _manifest_digest(directories: list[str], files: list[dict[str, Any]]) -> str:
    payload = {
        "directories": directories,
        "files": [
            [item["relativePath"], item["size"], item["sha256"]]
            for item in files
        ],
    }
    return hashlib.sha256(json.dumps(payload, separators=(",", ":")).encode("utf-8")).hexdigest()


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

    @property
    def migrations_root(self) -> Path:
        return self.runtime_root / VAULT_MIGRATIONS_DIRNAME

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
            "migration": None,
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
        migration_source: str = "",
    ) -> dict[str, Any]:
        return {
            "vaultId": identity["vaultId"],
            "userName": identity["userName"],
            "vaultPath": str(path),
            "identityMarker": VAULT_IDENTITY_FILENAME,
            "origin": origin,
            "migrationSource": migration_source,
            "updatedAt": _now_iso(),
        }

    def _activate(
        self,
        *,
        identity: dict[str, Any],
        path: Path,
        origin: str,
        migration_source: str = "",
    ) -> dict[str, Any]:
        path = _existing_directory(path, "vault_path")
        state, actual = inspect_vault_identity(path)
        if state != "valid" or not actual or actual["vaultId"] != identity["vaultId"]:
            raise VaultLifecycleError("identity_mismatch", "vault identity changed before activation")
        previous = self._registry()
        updated = json.loads(json.dumps(previous))
        updated["vaults"][identity["vaultId"]] = self._entry_payload(
            identity=actual,
            path=path,
            origin=origin,
            migration_source=migration_source,
        )
        updated["activeVaultId"] = identity["vaultId"]
        self._write_registry(updated)
        try:
            write_vault_path_to_config(self.config_path, path)
        except Exception:
            self._write_registry(previous)
            raise
        return updated["vaults"][identity["vaultId"]]

    def _deactivate(self) -> None:
        previous = self._registry()
        updated = json.loads(json.dumps(previous))
        updated["activeVaultId"] = ""
        self._write_registry(updated)
        try:
            _clear_vault_path_in_config(self.config_path)
        except Exception:
            self._write_registry(previous)
            raise

    def status(self) -> dict[str, Any]:
        registry = self._registry()
        active_id = str(registry.get("activeVaultId") or "")
        if not active_id:
            return self._base_result(
                "status",
                state="first_use",
                message="Create a new Agent-wiki vault before ingesting content.",
            )
        entry = registry["vaults"].get(active_id)
        if not isinstance(entry, dict):
            return self._base_result(
                "status",
                state="registry_invalid",
                errorCode="active_vault_missing",
                message="The active vault registry entry is missing.",
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
                message="The saved vault path moved. Scan roots to reconnect by name and identity.",
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
                message="The path exists but its vault identity does not match the saved vault.",
                activeVault=active,
            )
        active["identityState"] = "valid"
        return self._base_result(
            "status",
            ok=True,
            state="ready",
            requiresUserAction=False,
            message="The Agent-wiki vault is ready.",
            activeVault=active,
        )

    def _root_candidates(
        self,
        *,
        user_name: str = "",
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
                "candidateId": _candidate_id("root", root),
                "kind": "obsidian_root",
                "obsidianRoot": key,
                "sources": [],
                "writable": os.access(root, os.W_OK),
                "suggestedVaultPath": str(root / user_name) if user_name else "",
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
                    child.name.casefold() in MIGRATION_EXCLUDED_NAMES
                    or child.name.startswith(".agent-wiki-")
                    or not child.is_dir()
                ):
                    continue
                marker = child / VAULT_IDENTITY_FILENAME
                if marker.is_file():
                    paths.setdefault(str(child.resolve()), (child.resolve(), False))

        candidates: list[dict[str, Any]] = []
        for path, from_registry in sorted(paths.values(), key=lambda item: str(item[0])):
            identity_state, identity = inspect_vault_identity(path)
            if identity_state == "valid" and identity:
                match_state = "none"
                if identity["vaultId"] == active_id and identity["userName"] == active_name:
                    match_state = "active_identity"
                elif active_name and identity["userName"] == active_name:
                    match_state = "name_only"
                candidates.append({
                    "candidateId": _candidate_id("vault", path, identity["vaultId"]),
                    "kind": "agent_wiki_vault",
                    "vaultPath": str(path),
                    "vaultId": identity["vaultId"],
                    "userName": identity["userName"],
                    "identityMarker": VAULT_IDENTITY_FILENAME,
                    "identityState": "valid",
                    "matchState": match_state,
                    "supportedActions": ["switch"],
                })
            elif from_registry:
                candidates.append({
                    "candidateId": _candidate_id("existing", path),
                    "kind": "existing_obsidian_vault",
                    "vaultPath": str(path),
                    "vaultId": "",
                    "userName": "",
                    "identityMarker": VAULT_IDENTITY_FILENAME,
                    "identityState": identity_state,
                    "matchState": "none",
                    "supportedActions": ["migrate"],
                })
        return candidates

    def _cache_candidates(
        self,
        roots: list[dict[str, Any]],
        vaults: list[dict[str, Any]],
    ) -> None:
        items = {
            item["candidateId"]: item
            for item in [*roots, *vaults]
        }
        _atomic_json_write(self.candidates_path, {
            "contractVersion": CONTRACT_VERSION,
            "createdAtEpoch": time.time(),
            "expiresAtEpoch": time.time() + CANDIDATE_TTL_SECONDS,
            "items": items,
        })

    def scan(
        self,
        *,
        user_name: str = "",
        parent_hints: Iterable[Path | str] = (),
    ) -> dict[str, Any]:
        normalized_name = normalize_user_name(user_name) if str(user_name or "").strip() else ""
        roots, registry_vaults = self._root_candidates(
            user_name=normalized_name,
            parent_hints=parent_hints,
        )
        vaults = self._vault_candidates(roots, registry_vaults)
        self._cache_candidates(roots, vaults)

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
                    message="The moved vault was reconnected by matching user name and vault identity.",
                    activeVault=active,
                )
        elif current["state"] == "disconnected" and len(exact) > 1:
            current = self._base_result(
                "scan",
                state="ambiguous",
                message="Multiple paths have the same user name and vault identity. Confirm one candidate.",
                activeVault=current.get("activeVault"),
            )
        elif current["state"] == "first_use":
            current = self._base_result(
                "scan",
                state="root_ready" if roots else "root_selection_required",
                message=(
                    "Choose an Obsidian root and create a new Agent-wiki vault."
                    if roots
                    else "No Obsidian root was found. Select the parent directory for the new vault."
                ),
            )
        else:
            current["operation"] = "scan"

        current["obsidianRoots"] = roots
        current["vaultCandidates"] = vaults
        return current

    def _resolve_target(
        self,
        *,
        user_name: str,
        obsidian_root: Path | str = "",
        parent_directory: Path | str = "",
    ) -> tuple[str, Path, Path]:
        name = normalize_user_name(user_name)
        if bool(str(obsidian_root or "").strip()) == bool(str(parent_directory or "").strip()):
            raise VaultLifecycleError(
                "target_parent_invalid",
                "Provide exactly one of obsidianRoot or parentDirectory",
            )
        source = obsidian_root or parent_directory
        root = _existing_directory(source, "obsidian_root")
        if not os.access(root, os.W_OK):
            raise VaultLifecycleError("obsidian_root_not_writable", "The selected parent directory is not writable")
        return name, root, root / name

    def create(
        self,
        *,
        user_name: str,
        obsidian_root: Path | str = "",
        parent_directory: Path | str = "",
    ) -> dict[str, Any]:
        name, root, target = self._resolve_target(
            user_name=user_name,
            obsidian_root=obsidian_root,
            parent_directory=parent_directory,
        )
        if os.path.lexists(target):
            return self._base_result(
                "create",
                state="target_conflict",
                errorCode="target_exists",
                message="The target vault directory already exists. Choose another name or switch explicitly.",
                obsidianRoot=str(root),
                targetVaultPath=str(target),
            )
        identity = {
            "vaultId": self._new_uuid(),
            "userName": name,
            "createdAt": _now_iso(),
        }
        staging = root / f".agent-wiki-create-{identity['vaultId']}"
        if os.path.lexists(staging):
            raise VaultLifecycleError("staging_conflict", "A create staging directory already exists")
        try:
            staging.mkdir(mode=0o700)
            _ensure_minimal_vault_structure(staging, identity=identity)
            _validate_minimal_vault(staging, identity)
            os.replace(staging, target)
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        active = self._activate(identity=identity, path=target, origin="created")
        return self._base_result(
            "create",
            ok=True,
            state="created",
            requiresUserAction=False,
            message="A new empty Agent-wiki vault was created and activated.",
            activeVault=active,
            obsidianRoot=str(root),
            targetVaultPath=str(target),
        )

    def initialize_explicit_empty_vault(self, vault_path: Path | str) -> dict[str, Any]:
        """Initialize an existing empty directory used by isolated CLI/test workflows."""
        vault = _existing_directory(vault_path, "vault_path")
        state, identity = inspect_vault_identity(vault)
        if state == "valid" and identity:
            active = self._activate(identity=identity, path=vault, origin="explicit")
            return self._base_result(
                "initialize_explicit",
                ok=True,
                state="ready",
                requiresUserAction=False,
                message="The explicit Agent-wiki vault is ready.",
                activeVault=active,
            )
        try:
            entries = list(vault.iterdir())
        except OSError as exc:
            raise VaultLifecycleError("vault_path_invalid", "The explicit vault directory is not readable") from exc
        if entries:
            raise VaultLifecycleError(
                "migration_required",
                "A non-empty unmarked directory must use the migration workflow",
            )
        identity = {
            "vaultId": self._new_uuid(),
            "userName": normalize_user_name(vault.name),
            "createdAt": _now_iso(),
        }
        _ensure_minimal_vault_structure(vault, identity=identity)
        _validate_minimal_vault(vault, identity)
        active = self._activate(identity=identity, path=vault, origin="explicit_empty")
        return self._base_result(
            "initialize_explicit",
            ok=True,
            state="created",
            requiresUserAction=False,
            message="The explicit empty Agent-wiki vault was initialized.",
            activeVault=active,
        )

    def switch(
        self,
        *,
        vault_path: Path | str,
        expected_vault_id: str = "",
    ) -> dict[str, Any]:
        try:
            vault = _existing_directory(vault_path, "vault_path")
        except VaultLifecycleError as error:
            return self.error_result("switch", error)
        identity_state, identity = inspect_vault_identity(vault)
        if identity_state == "missing":
            return self._base_result(
                "switch",
                state="migration_required",
                errorCode="identity_marker_missing",
                message="This existing vault is not managed by Agent-wiki. Preview a migration instead of switching directly.",
            )
        if identity_state != "valid" or not identity:
            return self._base_result(
                "switch",
                state="identity_invalid",
                errorCode="identity_marker_invalid",
                message="The vault identity marker is invalid.",
            )
        expected = str(expected_vault_id or "").strip().lower()
        if expected and identity["vaultId"] != expected:
            return self._base_result(
                "switch",
                state="identity_mismatch",
                errorCode="identity_mismatch",
                message="The selected vault identity no longer matches the confirmed candidate.",
            )
        active = self._activate(identity=identity, path=vault, origin="switched")
        return self._base_result(
            "switch",
            ok=True,
            state="switched",
            requiresUserAction=False,
            message="The active Agent-wiki vault was switched.",
            activeVault=active,
        )

    def _cached_candidate(self, candidate_id: str) -> dict[str, Any]:
        payload = _read_json(self.candidates_path) or {}
        if float(payload.get("expiresAtEpoch") or 0) < time.time():
            raise VaultLifecycleError("candidate_expired", "The candidate expired. Scan again before confirming.")
        candidate = (payload.get("items") or {}).get(str(candidate_id or ""))
        if not isinstance(candidate, dict):
            raise VaultLifecycleError("candidate_not_found", "The candidate was not found. Scan again.")
        return candidate

    def confirm_candidate(
        self,
        *,
        candidate_id: str,
        action: str,
        user_name: str = "",
        obsidian_root: Path | str = "",
        parent_directory: Path | str = "",
    ) -> dict[str, Any]:
        candidate = self._cached_candidate(candidate_id)
        normalized_action = str(action or "").strip().lower()
        if normalized_action not in {"create", "switch", "migrate"}:
            raise VaultLifecycleError("candidate_action_invalid", "action must be create, switch, or migrate")
        if normalized_action == "create" and candidate.get("kind") == "obsidian_root":
            return self.create(
                user_name=user_name,
                obsidian_root=candidate["obsidianRoot"],
            )
        if normalized_action == "switch" and candidate.get("kind") == "agent_wiki_vault":
            return self.switch(
                vault_path=candidate["vaultPath"],
                expected_vault_id=candidate["vaultId"],
            )
        if normalized_action == "migrate" and candidate.get("kind") == "existing_obsidian_vault":
            return self.preview_migration(
                source_path=candidate["vaultPath"],
                user_name=user_name,
                obsidian_root=obsidian_root,
                parent_directory=parent_directory,
            )
        raise VaultLifecycleError("candidate_action_invalid", "The action is not supported by this candidate type")

    def _migration_path(self, migration_id: str) -> Path:
        safe_id = str(migration_id or "").strip().lower()
        if not re.fullmatch(r"migration-[0-9a-f-]{32,36}", safe_id):
            raise VaultLifecycleError("migration_id_invalid", "migrationId is invalid")
        return self.migrations_root / safe_id / "plan.json"

    def _migration_public(self, plan: dict[str, Any]) -> dict[str, Any]:
        return {
            "migrationId": plan["migrationId"],
            "state": plan["state"],
            "sourceVault": {
                "vaultPath": plan["sourcePath"],
                "identityState": plan["sourceIdentityState"],
                "vaultId": (plan.get("sourceIdentity") or {}).get("vaultId", ""),
                "userName": (plan.get("sourceIdentity") or {}).get("userName", ""),
            },
            "targetVault": {
                "obsidianRoot": plan["obsidianRoot"],
                "vaultPath": plan["targetPath"],
                "vaultId": plan["targetIdentity"]["vaultId"],
                "userName": plan["targetIdentity"]["userName"],
                "identityMarker": VAULT_IDENTITY_FILENAME,
            },
            "copyMode": "copy",
            "sourcePreserved": True,
            "targetPreservedOnRollback": True,
            "fileCount": len(plan["files"]),
            "directoryCount": len(plan["directories"]),
            "totalBytes": sum(int(item["size"]) for item in plan["files"]),
            "sourceDigest": plan["sourceDigest"],
            "excludedNames": sorted(MIGRATION_EXCLUDED_NAMES),
            "conflicts": plan["conflicts"],
            "canExecute": plan["state"] == "preview_ready",
            "rollbackAvailable": plan["state"] in {"completed", "rolled_back"},
        }

    def preview_migration(
        self,
        *,
        source_path: Path | str,
        user_name: str,
        obsidian_root: Path | str = "",
        parent_directory: Path | str = "",
    ) -> dict[str, Any]:
        source = _existing_directory(source_path, "source_path")
        name, root, target = self._resolve_target(
            user_name=user_name,
            obsidian_root=obsidian_root,
            parent_directory=parent_directory,
        )
        conflicts: list[dict[str, str]] = []
        if source == target or _is_within(target, source) or _is_within(source, target):
            conflicts.append({"code": "source_target_overlap", "relativePath": "."})
        if os.path.lexists(target):
            conflicts.append({"code": "target_exists", "relativePath": "."})
            if target.is_dir() and not target.is_symlink():
                try:
                    for child in sorted(target.iterdir())[:20]:
                        conflicts.append({
                            "code": "target_entry_exists",
                            "relativePath": child.name,
                        })
                except OSError:
                    conflicts.append({"code": "target_unreadable", "relativePath": "."})

        directories, files, source_conflicts = _source_manifest(source)
        conflicts.extend(source_conflicts)
        source_identity_state, source_identity = inspect_vault_identity(source)
        migration_id = f"migration-{self._new_uuid()}"
        plan = {
            "schemaVersion": 1,
            "migrationId": migration_id,
            "state": "preview_ready" if not conflicts else "conflict",
            "createdAt": _now_iso(),
            "sourcePath": str(source),
            "sourceIdentityState": source_identity_state,
            "sourceIdentity": source_identity,
            "obsidianRoot": str(root),
            "targetPath": str(target),
            "targetIdentity": {
                "vaultId": self._new_uuid(),
                "userName": name,
                "createdAt": _now_iso(),
            },
            "directories": directories,
            "files": files,
            "sourceDigest": _manifest_digest(directories, files),
            "conflicts": conflicts,
            "previousActive": self.status().get("activeVault"),
        }
        _atomic_json_write(self._migration_path(migration_id), plan)
        public = self._migration_public(plan)
        return self._base_result(
            "migration_preview",
            ok=not conflicts,
            state="migration_ready" if not conflicts else "migration_conflict",
            requiresUserAction=bool(conflicts),
            message=(
                "Migration preview is ready. Execute it to copy and validate before switching."
                if not conflicts
                else "Migration conflicts must be resolved before execution."
            ),
            migration=public,
        )

    def _load_migration(self, migration_id: str) -> tuple[Path, dict[str, Any]]:
        path = self._migration_path(migration_id)
        plan = _read_json(path)
        if not plan or plan.get("migrationId") != migration_id:
            raise VaultLifecycleError("migration_not_found", "The migration preview was not found")
        return path, plan

    def execute_migration(self, *, migration_id: str) -> dict[str, Any]:
        plan_path, plan = self._load_migration(migration_id)
        if plan.get("state") == "completed":
            return self._base_result(
                "migration_execute",
                ok=True,
                state="migrated",
                requiresUserAction=False,
                message="The migration was already completed.",
                activeVault=self.status().get("activeVault"),
                migration=self._migration_public(plan),
            )
        if plan.get("state") != "preview_ready":
            return self._base_result(
                "migration_execute",
                state="migration_conflict",
                errorCode="migration_not_executable",
                message="Create a conflict-free migration preview before execution.",
                migration=self._migration_public(plan),
            )

        source = _existing_directory(plan["sourcePath"], "source_path")
        root = _existing_directory(plan["obsidianRoot"], "obsidian_root")
        target = Path(plan["targetPath"])
        if os.path.lexists(target):
            plan["state"] = "conflict"
            plan["conflicts"] = [{"code": "target_exists", "relativePath": "."}]
            _atomic_json_write(plan_path, plan)
            return self._base_result(
                "migration_execute",
                state="migration_conflict",
                errorCode="target_exists",
                message="The migration target appeared after preview. Preview again.",
                migration=self._migration_public(plan),
            )

        directories, files, source_conflicts = _source_manifest(source)
        digest = _manifest_digest(directories, files)
        if source_conflicts or digest != plan["sourceDigest"]:
            plan["state"] = "stale"
            plan["conflicts"] = source_conflicts or [{"code": "source_changed", "relativePath": "."}]
            _atomic_json_write(plan_path, plan)
            return self._base_result(
                "migration_execute",
                state="migration_stale",
                errorCode="source_changed",
                message="The source changed after preview. Preview the migration again.",
                migration=self._migration_public(plan),
            )

        staging = root / f".agent-wiki-migration-{migration_id.removeprefix('migration-')}"
        if os.path.lexists(staging):
            raise VaultLifecycleError("staging_conflict", "A migration staging directory already exists")
        identity = plan["targetIdentity"]
        try:
            staging.mkdir(mode=0o700)
            for relative in directories:
                (staging / relative).mkdir(parents=True, exist_ok=True)
            for item in files:
                source_file = source / item["relativePath"]
                target_file = staging / item["relativePath"]
                target_file.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source_file, target_file)
            _ensure_minimal_vault_structure(staging, identity=identity)
            for item in files:
                copied = staging / item["relativePath"]
                if not copied.is_file() or _sha256_file(copied) != item["sha256"]:
                    raise VaultLifecycleError(
                        "migration_validation_failed",
                        f"Copied file validation failed: {item['relativePath']}",
                    )
            _validate_minimal_vault(staging, identity)
            os.replace(staging, target)
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise

        active = self._activate(
            identity=identity,
            path=target,
            origin="migrated",
            migration_source=str(source),
        )
        plan["state"] = "completed"
        plan["completedAt"] = _now_iso()
        plan["sourcePreserved"] = source.exists()
        _atomic_json_write(plan_path, plan)
        return self._base_result(
            "migration_execute",
            ok=True,
            state="migrated",
            requiresUserAction=False,
            message="The source was copied and validated before the active vault switched.",
            activeVault=active,
            migration=self._migration_public(plan),
        )

    def rollback_migration(self, *, migration_id: str) -> dict[str, Any]:
        plan_path, plan = self._load_migration(migration_id)
        if plan.get("state") not in {"completed", "rolled_back"}:
            return self._base_result(
                "migration_rollback",
                state="rollback_unavailable",
                errorCode="migration_not_completed",
                message="Rollback is available only after a completed migration.",
                migration=self._migration_public(plan),
            )
        previous = plan.get("previousActive")
        active = None
        if isinstance(previous, dict) and previous.get("vaultPath") and previous.get("vaultId"):
            identity_state, identity = inspect_vault_identity(previous["vaultPath"])
            if (
                identity_state != "valid"
                or not identity
                or identity["vaultId"] != previous["vaultId"]
                or identity["userName"] != previous.get("userName")
            ):
                return self._base_result(
                    "migration_rollback",
                    state="rollback_blocked",
                    errorCode="rollback_identity_mismatch",
                    message="The previous active vault no longer matches its saved identity.",
                    migration=self._migration_public(plan),
                )
            active = self._activate(identity=identity, path=Path(previous["vaultPath"]), origin="rollback")
        else:
            self._deactivate()
        plan["state"] = "rolled_back"
        plan["rolledBackAt"] = _now_iso()
        _atomic_json_write(plan_path, plan)
        return self._base_result(
            "migration_rollback",
            ok=True,
            state="rolled_back",
            requiresUserAction=False,
            message="The active selection was rolled back; source and migrated target were both preserved.",
            activeVault=active,
            migration=self._migration_public(plan),
        )


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
                user_name=payload.get("userName", ""),
                parent_hints=payload.get("parentHints") or (),
            )
        if message_type == "vault_create":
            return manager.create(
                user_name=payload.get("userName", ""),
                obsidian_root=payload.get("obsidianRoot", ""),
                parent_directory=payload.get("parentDirectory", ""),
            )
        if message_type == "vault_switch":
            return manager.switch(
                vault_path=payload.get("vaultPath", ""),
                expected_vault_id=payload.get("expectedVaultId", ""),
            )
        if message_type == "vault_candidate_confirm":
            return manager.confirm_candidate(
                candidate_id=payload.get("candidateId", ""),
                action=payload.get("action", ""),
                user_name=payload.get("userName", ""),
                obsidian_root=payload.get("obsidianRoot", ""),
                parent_directory=payload.get("parentDirectory", ""),
            )
        if message_type == "vault_migration_preview":
            return manager.preview_migration(
                source_path=payload.get("sourcePath", ""),
                user_name=payload.get("userName", ""),
                obsidian_root=payload.get("obsidianRoot", ""),
                parent_directory=payload.get("parentDirectory", ""),
            )
        if message_type == "vault_migration_execute":
            return manager.execute_migration(migration_id=payload.get("migrationId", ""))
        if message_type == "vault_migration_rollback":
            return manager.rollback_migration(migration_id=payload.get("migrationId", ""))
        raise VaultLifecycleError("operation_unsupported", "Unsupported vault lifecycle operation")
    except VaultLifecycleError as error:
        return manager.error_result(operation, error)
