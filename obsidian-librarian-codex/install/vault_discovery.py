#!/usr/bin/env python3
"""Fast, bounded Obsidian vault discovery for agent-wiki.

This module deliberately avoids full-disk scans. It only inspects high-signal
locations: existing runtime config, current working directory ancestry,
Obsidian's local vault registry, iCloud Obsidian folders, and a small set of
common user document roots.
"""
from __future__ import annotations

import json
import os
import platform
import re
import subprocess
import time
from dataclasses import asdict, dataclass, field
from difflib import get_close_matches
from pathlib import Path
from typing import Iterable, Optional


MIN_SCORE = 55
MAX_DISCOVERY_SECONDS = 4.0
MAX_SCAN_DEPTH = 4
MAX_SCAN_NODES = 2500
SAFE_AUTOSELECT_SOURCES = {
    "config.toml",
    "cwd",
    "obsidian_registry",
    "obsidian_registry_child",
    "obsidian_cli",
    "common_root",
    "user_hint_exact",
}


@dataclass(order=True)
class VaultCandidate:
    score: int
    path: str = field(compare=False)
    source: str = field(compare=False)
    reasons: list[str] = field(default_factory=list, compare=False)

    @property
    def path_obj(self) -> Path:
        return Path(self.path)


@dataclass
class VaultDiscoveryResult:
    ok: bool
    selected: Optional[VaultCandidate] = None
    candidates: list[VaultCandidate] = field(default_factory=list)
    searched_roots: list[str] = field(default_factory=list)
    message: str = ""

    def to_json(self) -> dict:
        return {
            "ok": self.ok,
            "selected": asdict(self.selected) if self.selected else None,
            "candidates": [asdict(c) for c in self.candidates],
            "searched_roots": self.searched_roots,
            "message": self.message,
        }


def _clean_path_text(text: str) -> str:
    cleaned = (text or "").strip()
    cleaned = cleaned.strip("\"'`“”‘’")
    cleaned = cleaned.replace("：", ":").replace("／", "/")
    cleaned = re.sub(r"^file://", "", cleaned)
    cleaned = cleaned.replace("\\ ", " ")
    cleaned = re.sub(r"\s+", " ", cleaned)
    if cleaned.startswith("/User/"):
        cleaned = "/Users/" + cleaned.removeprefix("/User/")
    return cleaned


def _safe_resolve(path: Path) -> Optional[Path]:
    try:
        return path.expanduser().resolve()
    except Exception:
        return None


def _read_text_prefix(path: Path, limit: int = 4096) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")[:limit]
    except Exception:
        return ""


def _looks_like_skill_package(path: Path) -> bool:
    if not (
        (path / "SKILL.md").is_file()
        and (path / "chrome-extension").is_dir()
        and (path / "server").is_dir()
        and (path / "install").is_dir()
    ):
        return False
    text = _read_text_prefix(path / "SKILL.md")
    return "name: agent-wiki" in text or "agent-wiki" in text


def _has_required_signal_combo(reasons: list[str]) -> bool:
    if "looks_like_skill_package" in reasons:
        return False

    has_obsidian = ".obsidian" in reasons
    strong_markers = {
        "SCHEMA.md:agent-wiki",
        "index.md:知识库索引",
        "知识资产",
        "知识资产（Knowledge Assets）",
    }
    soft_markers = {
        "SCHEMA.md",
        "index.md",
        "templates",
        "raw",
        "系统记录",
        "系统记录（System Records）",
    }
    strong_count = len(strong_markers.intersection(reasons))
    marker_count = len((strong_markers | soft_markers).intersection(reasons))

    # A normal Obsidian vault plus one librarian/knowledge marker is safe enough.
    if has_obsidian and marker_count >= 1:
        return True

    # A newly initialized agent-wiki vault may not have .obsidian yet,
    # but it should have at least two independent project markers.
    return strong_count >= 1 and marker_count >= 2


def score_vault(path: Path, *, source: str) -> Optional[VaultCandidate]:
    resolved = _safe_resolve(path)
    if not resolved or not resolved.exists() or not resolved.is_dir():
        return None
    if _looks_like_skill_package(resolved):
        return None

    score = 0
    reasons: list[str] = []

    if (resolved / ".obsidian").is_dir():
        score += 35
        reasons.append(".obsidian")

    schema = resolved / "SCHEMA.md"
    if schema.is_file():
        text = _read_text_prefix(schema)
        if "知识库宪法" in text or "SCHEMA.md" in text:
            score += 35
            reasons.append("SCHEMA.md:agent-wiki")
        else:
            score += 20
            reasons.append("SCHEMA.md")

    index = resolved / "index.md"
    if index.is_file():
        text = _read_text_prefix(index)
        score += 20
        reasons.append("index.md")
        if "知识库索引" in text:
            score += 20
            reasons.append("index.md:知识库索引")

    for folder, points in [
        ("知识资产", 20),
        ("知识资产（Knowledge Assets）", 12),
        ("templates", 12),
        ("raw", 12),
        ("系统记录", 12),
        ("系统记录（System Records）", 8),
    ]:
        if (resolved / folder).is_dir():
            score += points
            reasons.append(folder)

    if (resolved / ".git").is_dir():
        score += 6
        reasons.append(".git")

    if score < MIN_SCORE or not _has_required_signal_combo(reasons):
        return None
    return VaultCandidate(score=score, path=str(resolved), source=source, reasons=reasons)


def _candidate_paths_from_hint(raw_hint: str) -> list[tuple[Path, str]]:
    hint = _clean_path_text(raw_hint)
    if not hint:
        return []
    p = Path(hint).expanduser()
    paths: list[tuple[Path, str]] = [(p, "exact")]

    resolved = _safe_resolve(p)
    if resolved:
        paths.append((resolved, "exact"))
        paths.extend((parent, "parent") for parent in list(resolved.parents)[:4])
        if resolved.exists() and resolved.is_dir():
            try:
                paths.extend((child, "child") for child in sorted(resolved.iterdir()) if child.is_dir())
            except Exception:
                pass
        elif resolved.parent.exists():
            try:
                names = [child.name for child in resolved.parent.iterdir() if child.is_dir()]
                for name in get_close_matches(resolved.name, names, n=5, cutoff=0.68):
                    paths.append((resolved.parent / name, "fuzzy"))
            except Exception:
                pass
    return _dedupe_hint_paths(paths)


def _read_config_vault_path(config_path: Optional[Path]) -> str:
    if not config_path or not config_path.exists():
        return ""
    current = ""
    for raw in _read_text_prefix(config_path, limit=20000).splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            current = line.strip("[]").strip()
            continue
        if current == "vault" and line.startswith("path") and "=" in line:
            value = line.split("=", 1)[1].strip().split(" #", 1)[0].strip()
            return value.strip("\"'")
    return ""


def _obsidian_registry_candidates() -> list[Path]:
    paths: list[Path] = []
    system = platform.system()
    if system == "Darwin":
        registry = Path.home() / "Library/Application Support/obsidian/obsidian.json"
    elif system == "Windows":
        appdata = os.environ.get("APPDATA", "")
        registry = Path(appdata) / "obsidian/obsidian.json" if appdata else Path()
    else:
        registry = Path.home() / ".config/obsidian/obsidian.json"

    if not registry.exists():
        return paths
    try:
        data = json.loads(registry.read_text(encoding="utf-8"))
    except Exception:
        return paths

    vaults = data.get("vaults", {})
    if isinstance(vaults, dict):
        for item in vaults.values():
            if isinstance(item, dict) and item.get("path"):
                paths.append(Path(str(item["path"])))
    return paths


def _obsidian_cli_candidates(timeout_sec: float = 1.5) -> list[Path]:
    exe = shutil_which("obsidian")
    if not exe:
        return []
    commands = [
        [exe, "vaults", "format=json"],
        [exe, "vault:list", "format=json"],
    ]
    found: list[Path] = []
    for cmd in commands:
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_sec)
        except Exception:
            continue
        if proc.returncode != 0 or not proc.stdout.strip():
            continue
        text = proc.stdout.strip()
        try:
            data = json.loads(text)
        except Exception:
            data = text.splitlines()
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict) and item.get("path"):
                    found.append(Path(str(item["path"])))
                elif isinstance(item, str) and "/" in item:
                    found.append(Path(item.strip()))
    return found


def shutil_which(name: str) -> Optional[str]:
    for folder in os.environ.get("PATH", "").split(os.pathsep):
        path = Path(folder) / name
        if path.exists() and os.access(path, os.X_OK):
            return str(path)
    return None


def _common_roots() -> list[Path]:
    home = Path.home()
    cloud_storage = home / "Library/CloudStorage"
    cloud_roots = sorted(cloud_storage.glob("*")) if cloud_storage.exists() else []
    roots = [
        home / "Library/Mobile Documents/iCloud~md~obsidian/Documents",
        home / "Library/Mobile Documents/com~apple~CloudDocs",
        *cloud_roots,
        home / "Documents",
        home / "Desktop",
        home / "Obsidian",
        home / "Dropbox",
        home / "Google Drive",
        home / "OneDrive",
    ]
    return [p for p in roots if p.exists() and p.is_dir()]


def _dedupe_paths(paths: Iterable[Path]) -> list[Path]:
    result: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        resolved = _safe_resolve(path)
        if not resolved:
            continue
        key = str(resolved)
        if key in seen:
            continue
        seen.add(key)
        result.append(resolved)
    return result


def _dedupe_hint_paths(paths: Iterable[tuple[Path, str]]) -> list[tuple[Path, str]]:
    result: list[tuple[Path, str]] = []
    seen: set[str] = set()
    for path, kind in paths:
        resolved = _safe_resolve(path)
        if not resolved:
            continue
        key = str(resolved)
        if key in seen:
            continue
        seen.add(key)
        result.append((resolved, kind))
    return result


def _bounded_scan(root: Path, *, started_at: float, max_depth: int) -> Iterable[Path]:
    root = root.expanduser()
    if not root.exists() or not root.is_dir():
        return

    base_depth = len(root.parts)
    stack = [root]
    visited = 0
    while stack:
        if time.monotonic() - started_at > MAX_DISCOVERY_SECONDS or visited >= MAX_SCAN_NODES:
            return
        current = stack.pop()
        visited += 1
        yield current
        if len(current.parts) - base_depth >= max_depth:
            continue
        try:
            children = sorted(child for child in current.iterdir() if child.is_dir())
        except Exception:
            continue
        for child in children:
            name = child.name
            if name in {".git", "node_modules", ".venv", "__pycache__", "Library"}:
                continue
            stack.append(child)


def discover_vault(
    *,
    config_path: Optional[Path] = None,
    cwd: Optional[Path] = None,
    user_hint: str = "",
    runtime_root: Optional[Path] = None,
) -> VaultDiscoveryResult:
    started_at = time.monotonic()
    candidates: list[VaultCandidate] = []
    searched_roots: list[str] = []

    def add(path: Path, source: str) -> None:
        candidate = score_vault(path, source=source)
        if candidate:
            candidates.append(candidate)

    config_vault = _read_config_vault_path(config_path)
    for path, kind in _candidate_paths_from_hint(config_vault):
        if kind == "exact":
            add(path, "config.toml")
    for path, kind in _candidate_paths_from_hint(user_hint):
        add(path, f"user_hint_{kind}")

    cwd_path = (cwd or Path.cwd()).expanduser()
    for path in [cwd_path, *list(cwd_path.parents)[:5]]:
        add(path, "cwd")

    for path in _obsidian_registry_candidates():
        candidate = score_vault(path, source="obsidian_registry")
        if candidate:
            candidates.append(candidate)
            if ".obsidian" in candidate.reasons:
                continue
        try:
            for child in sorted(path.iterdir()):
                if child.is_dir():
                    add(child, "obsidian_registry_child")
        except Exception:
            pass

    for path in _obsidian_cli_candidates():
        add(path, "obsidian_cli")

    for root in _common_roots():
        searched_roots.append(str(root))
        for path in _bounded_scan(root, started_at=started_at, max_depth=MAX_SCAN_DEPTH):
            add(path, "common_root")

    by_path: dict[str, VaultCandidate] = {}
    for candidate in candidates:
        existing = by_path.get(candidate.path)
        if not existing or candidate.score > existing.score:
            by_path[candidate.path] = candidate

    ranked = sorted(by_path.values(), reverse=True)
    selected = next((c for c in ranked if c.source in SAFE_AUTOSELECT_SOURCES), None)
    result = VaultDiscoveryResult(
        ok=bool(selected),
        selected=selected,
        candidates=ranked[:10],
        searched_roots=searched_roots,
        message="已识别知识库" if selected else "未找到符合协议的知识库",
    )
    if runtime_root:
        save_discovery_cache(result, runtime_root)
    return result


def save_discovery_cache(result: VaultDiscoveryResult, runtime_root: Path) -> Path:
    target = runtime_root.expanduser() / "status" / "vault_discovery.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result.to_json(), ensure_ascii=False, indent=2), encoding="utf-8")
    return target


def write_vault_path_to_config(config_path: Path, vault_path: Path) -> None:
    config_path = config_path.expanduser()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    path_text = str(vault_path.expanduser().resolve()).replace("\\", "\\\\").replace('"', '\\"')
    if not config_path.exists():
        config_path.write_text(f'[vault]\npath = "{path_text}"\n', encoding="utf-8")
        os.chmod(config_path, 0o600)
        return

    text = config_path.read_text(encoding="utf-8")
    if "[vault]" not in text:
        text = text.rstrip() + f'\n\n[vault]\npath = "{path_text}"\nrelative_root = "知识资产/知识入库"\n'
    else:
        lines = text.splitlines()
        out: list[str] = []
        in_vault = False
        replaced = False
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("[") and stripped.endswith("]"):
                if in_vault and not replaced:
                    out.append(f'path = "{path_text}"')
                    replaced = True
                in_vault = stripped == "[vault]"
            if in_vault and stripped.startswith("path") and "=" in stripped:
                out.append(f'path = "{path_text}"')
                replaced = True
                continue
            out.append(line)
        if in_vault and not replaced:
            out.append(f'path = "{path_text}"')
        text = "\n".join(out) + "\n"
    config_path.write_text(text, encoding="utf-8")
    os.chmod(config_path, 0o600)


if __name__ == "__main__":
    result = discover_vault()
    print(json.dumps(result.to_json(), ensure_ascii=False, indent=2))
