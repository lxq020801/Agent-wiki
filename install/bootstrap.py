#!/usr/bin/env python3
"""
Agent bootstrap for Agent-wiki.

This is the Scheme C entrypoint: every Agent-facing workflow can run this first.
It prepares what can be prepared automatically and reports only the user actions
that cannot be automated, such as loading the Chrome extension.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

try:
    from install.vault_discovery import discover_vault
    from install.vault_lifecycle import (
        VaultLifecycleError,
        VaultLifecycleManager,
        inspect_vault_identity,
    )
except ImportError:
    from vault_discovery import discover_vault
    from vault_lifecycle import (
        VaultLifecycleError,
        VaultLifecycleManager,
        inspect_vault_identity,
    )


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOT = Path(os.environ.get("AGENT_WIKI_HOME", "~/.agent-wiki")).expanduser()
DOUYIN_DIR = PROJECT_ROOT / "deps" / "douyin"
DOUYIN_VENV = DOUYIN_DIR / ".venv"
DOUYIN_REQ = DOUYIN_DIR / "requirements.txt"
ROOT_REQ = PROJECT_ROOT / "requirements.txt"
EXTENSION_SRC = PROJECT_ROOT / "chrome-extension"
EXTENSION_DEST = RUNTIME_ROOT / "extension"
CONFIG_PATH = RUNTIME_ROOT / "config.toml"
DEFAULT_PROVIDER = "doubao"
PROVIDER_KEY_SECTIONS = {
    "doubao": "ark",
}
EXTENSION_COPY_IGNORES = {".DS_Store", "__pycache__"}

BOOTSTRAP_CONFIG_TEMPLATE = """\
# Agent-wiki runtime config
# Fill these fields through the Chrome extension control console.

[provider]
# 固定使用普通豆包 / 火山方舟 API
active = "doubao"

[github]
# GitHub App Device Flow client ID（非 secret）；也可用 AGENT_WIKI_GITHUB_CLIENT_ID
client_id = ""

[ark]
# 普通方舟 API Key（provider.active = "doubao" 时使用）
api_key = ""
endpoint = "https://ark.cn-beijing.volces.com/api/v3"

[models]
analyzer = "doubao-seed-2-0-lite-260428"
strategy = "doubao-seed-2-0-mini-260428"
analyzer_fallback = "doubao-seed-2-0-mini-260428"

[analysis]
video_fps_mode = "auto"
default_quality = "quality"
balanced_target_frames = 240
quality_target_frames = 1250
fps_min = 2.0
fps_max = 5.0
file_active_timeout_sec = 120

[douyin]
cookie_path = "__AGENT_WIKI_COOKIE_PATH__"

[vault]
path = ""
relative_root = "知识资产/知识入库"

[server]
enabled = true
host = "127.0.0.1"
port = 8765
"""


@dataclass
class CheckResult:
    ok: bool = True
    actions: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    missing_user_actions: list[str] = field(default_factory=list)

    def add_warning(self, msg: str, *, fatal: bool = False) -> None:
        if fatal:
            self.ok = False
        self.warnings.append(msg)


def _run(cmd: list[str], *, cwd: Optional[Path] = None, check: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        check=check,
    )


def _find_python() -> Optional[str]:
    for cand in ["python3.13", "python3.12", "python3.11", sys.executable, "python3"]:
        try:
            exe = cand if os.sep in cand else shutil.which(cand)
            if not exe:
                continue
            result = _run([exe, "-c", "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)"])
        except FileNotFoundError:
            continue
        if result.returncode == 0:
            return str(Path(exe).resolve())
    return None


def _find_python_with_module(module: str) -> Optional[str]:
    seen: set[str] = set()
    for cand in ["python3.13", "python3.12", "python3.11", sys.executable, "python3"]:
        exe = cand if os.sep in cand else shutil.which(cand)
        if not exe:
            continue
        resolved = str(Path(exe).resolve())
        if resolved in seen:
            continue
        seen.add(resolved)
        result = _run([resolved, "-c", f"import {module}"])
        if result.returncode == 0:
            return resolved
    return None


def douyin_venv_python() -> Optional[Path]:
    for name in ["python", "python3"]:
        python = DOUYIN_VENV / "bin" / name
        if python.exists():
            return python
    return None


def _venv_python_is_usable(path: Path) -> bool:
    python = douyin_venv_python() if path == DOUYIN_VENV else path / "bin" / "python"
    if not python.exists():
        return False
    proc = _run([
        str(python),
        "-c",
        "import encodings, sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)",
    ])
    return proc.returncode == 0


def select_runtime_python() -> Path:
    """Return the preferred interpreter for Agent runtime commands."""
    if DOUYIN_VENV.exists() and _venv_python_is_usable(DOUYIN_VENV):
        python = douyin_venv_python()
        if python:
            return python
    host = _find_python()
    if host:
        return Path(host)
    return Path(sys.executable)


def ensure_runtime_dirs(result: CheckResult) -> None:
    for rel in [
        "inbox",
        "status",
        "archive",
        "failed",
        "cookie",
        "cache/videos",
        "handshake",
        "logs",
        "github",
    ]:
        path = RUNTIME_ROOT / rel
        path.mkdir(parents=True, exist_ok=True)
    result.actions.append(f"runtime directories ready: {RUNTIME_ROOT}")


def ensure_config_template(result: CheckResult) -> None:
    if CONFIG_PATH.exists():
        result.actions.append(f"config exists: {CONFIG_PATH}")
        return

    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    cookie_path = str(RUNTIME_ROOT / "cookie" / "douyin.txt")
    cookie_path = cookie_path.replace("\\", "\\\\").replace('"', '\\"')
    config_text = BOOTSTRAP_CONFIG_TEMPLATE.replace("__AGENT_WIKI_COOKIE_PATH__", cookie_path)
    CONFIG_PATH.write_text(config_text, encoding="utf-8")
    os.chmod(CONFIG_PATH, 0o600)
    result.actions.append(f"config template created: {CONFIG_PATH}")
    result.missing_user_actions.append(
        "打开扩展并通过“选择知识库”选择一个文件夹。"
    )
    result.missing_user_actions.append(
        "Configure AGENT_WIKI_GITHUB_CLIENT_ID before using GitHub Device Flow."
    )


def _is_ignored_extension_path(path: Path) -> bool:
    return any(part in EXTENSION_COPY_IGNORES for part in path.parts)


def _same_file_content(src: Path, dest: Path) -> bool:
    if not dest.exists() or not dest.is_file():
        return False
    if src.stat().st_size != dest.stat().st_size:
        return False
    return src.read_bytes() == dest.read_bytes()


def _sync_extension_tree(src_root: Path, dest_root: Path) -> tuple[int, int, int]:
    copied = 0
    removed = 0
    unchanged = 0
    expected_files: set[Path] = set()
    expected_dirs: set[Path] = {Path(".")}

    for src in src_root.rglob("*"):
        rel = src.relative_to(src_root)
        if _is_ignored_extension_path(rel):
            continue
        dest = dest_root / rel
        if src.is_dir():
            expected_dirs.add(rel)
            dest.mkdir(parents=True, exist_ok=True)
            continue
        if not src.is_file():
            continue
        expected_files.add(rel)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if _same_file_content(src, dest):
            unchanged += 1
            continue
        shutil.copy2(src, dest)
        copied += 1

    if dest_root.exists():
        for dest in sorted(dest_root.rglob("*"), key=lambda p: len(p.parts), reverse=True):
            rel = dest.relative_to(dest_root)
            if _is_ignored_extension_path(rel):
                if dest.is_dir():
                    shutil.rmtree(dest)
                else:
                    dest.unlink()
                removed += 1
                continue
            if dest.is_file() and rel not in expected_files:
                dest.unlink()
                removed += 1
            elif dest.is_dir() and rel not in expected_dirs:
                try:
                    dest.rmdir()
                    removed += 1
                except OSError:
                    pass

    return copied, removed, unchanged


def ensure_extension_copy(result: CheckResult) -> None:
    if not EXTENSION_SRC.exists():
        result.add_warning(f"extension source missing: {EXTENSION_SRC}")
        return
    EXTENSION_DEST.mkdir(parents=True, exist_ok=True)
    copied, removed, unchanged = _sync_extension_tree(EXTENSION_SRC, EXTENSION_DEST)
    result.actions.append(
        f"extension prepared: {EXTENSION_DEST} "
        f"(updated {copied}, removed {removed}, unchanged {unchanged})"
    )
    result.missing_user_actions.append(
        "Load the extension once: chrome://extensions/ -> Developer mode -> Load unpacked -> "
        f"{EXTENSION_DEST}"
    )


def ensure_douyin_venv(result: CheckResult, *, install_deps: bool) -> None:
    python = _find_python()
    if not python:
        result.add_warning(
            "未找到 Python 3.11+。请从 https://www.python.org/downloads/ 安装后重新运行 "
            "./agent-wiki install；不会修改系统 Python。",
            fatal=True,
        )
        return
    if DOUYIN_VENV.exists() and not _venv_python_is_usable(DOUYIN_VENV):
        shutil.rmtree(DOUYIN_VENV)
        result.actions.append(f"removed broken douyin venv: {DOUYIN_VENV}")

    if not DOUYIN_VENV.exists():
        proc = _run([python, "-m", "venv", str(DOUYIN_VENV)], cwd=DOUYIN_DIR)
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout).strip().splitlines()[-1:] or ["unknown error"]
            result.add_warning(
                f"could not create douyin venv with {python}: {detail[0]}. "
                "Install a standard Python 3.11+ or run dependencies in an existing environment."
            )
            return
        result.actions.append(f"douyin venv created with {python}: {DOUYIN_VENV}")
    else:
        result.actions.append(f"douyin venv exists: {DOUYIN_VENV}")

    if not _venv_python_is_usable(DOUYIN_VENV):
        result.add_warning(f"douyin venv is not usable: {DOUYIN_VENV}")
        return

    if install_deps:
        runtime_python = select_runtime_python()
        for req in [ROOT_REQ, DOUYIN_REQ]:
            if not req.exists():
                continue
            proc = _run([str(runtime_python), "-m", "pip", "install", "-r", str(req)], cwd=PROJECT_ROOT)
            if proc.returncode != 0:
                result.add_warning(
                    f"dependency install failed for {req.name}; rerun bootstrap after Python/network issues are fixed"
                )
                return
        result.actions.append("runtime dependencies installed")


def check_ffmpeg(result: CheckResult) -> None:
    missing = [tool for tool in ("ffmpeg", "ffprobe") if not shutil.which(tool)]
    for tool in ("ffmpeg", "ffprobe"):
        if tool not in missing:
            result.actions.append(f"{tool} available")
    if missing:
        result.add_warning(
            f"缺少系统命令：{', '.join(missing)}。请自行安装 FFmpeg（例如先安装 Homebrew，"
            "再运行 brew install ffmpeg），然后重新运行 ./agent-wiki doctor；"
            "Agent-wiki 不会静默安装 Homebrew。"
        )


def check_websocket(result: CheckResult, host: str = "127.0.0.1", port: int = 8765) -> None:
    python = _find_python_with_module("websockets")
    if not python:
        result.add_warning(
            f"No local Python can verify WebSocket health. Install dependencies or start manually: "
            f"python3 {PROJECT_ROOT / 'server' / 'launcher.py'}"
        )
        return

    script = f"""
import asyncio
import json
import sys
import websockets

async def main():
    try:
        async with websockets.connect('ws://{host}:{port}', open_timeout=1) as ws:
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=1))
            if msg.get('type') != 'agent_ready':
                raise SystemExit('unexpected first message: ' + repr(msg))
    except OSError:
        sys.exit(3)

asyncio.run(main())
"""
    proc = _run([python, "-c", script])
    if proc.returncode == 0:
        result.actions.append(f"WebSocket healthy: ws://{host}:{port}")
    elif proc.returncode == 3:
        result.missing_user_actions.append(
            f"Start Agent WebSocket when using the extension: python3 {PROJECT_ROOT / 'server' / 'launcher.py'}"
        )
    else:
        detail = (proc.stderr or proc.stdout).strip().splitlines()[-1:] or ["unknown handshake error"]
        result.add_warning(
            f"WebSocket port is occupied but not healthy: ws://{host}:{port} ({detail[0]}). "
            "Stop stale Hermes/Codex servers and restart the current Codex server."
        )


def _simple_config_value(section: str, key: str) -> str:
    """Read a string value from the runtime config without importing TOML deps."""
    if not CONFIG_PATH.exists():
        return ""
    current = ""
    for raw in CONFIG_PATH.read_text(encoding="utf-8", errors="ignore").splitlines():
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
    return ""


def _normalize_provider(value: str) -> str:
    raw = str(value or "").strip().lower()
    aliases = {
        "ark": "doubao",
        "ark_api": "doubao",
        "doubao_api": "doubao",
        "agent_plan": "doubao",
        "agentplan": "doubao",
        "volcengine-agent-plan": "doubao",
        "volcengine_agent_plan": "doubao",
    }
    normalized = aliases.get(raw, raw)
    return normalized if normalized in PROVIDER_KEY_SECTIONS else DEFAULT_PROVIDER


def _active_provider() -> str:
    return _normalize_provider(_simple_config_value("provider", "active") or DEFAULT_PROVIDER)


def _active_api_key() -> str:
    provider = _active_provider()
    return _simple_config_value(PROVIDER_KEY_SECTIONS[provider], "api_key")


def _active_key_label() -> str:
    return "Ark API Key"


def _safe_explicit_vault(value: str) -> Path | None:
    if not str(value or "").strip():
        return None
    path = Path(value).expanduser()
    if not path.exists() or not path.is_dir():
        return None
    resolved = path.resolve()
    if any(part.casefold() == ".obsidian" for part in resolved.parts):
        return None
    return resolved


def _explicit_vault_arg(value: str) -> Path:
    if not str(value or "").strip():
        raise argparse.ArgumentTypeError("vault path must not be empty")
    return Path(value)


def check_vault(result: CheckResult) -> None:
    if not CONFIG_PATH.exists():
        return

    api_key = _active_api_key()
    vault_raw = _simple_config_value("vault", "path")
    if not vault_raw:
        result.missing_user_actions.append(
            "请在扩展中点击“选择知识库”；普通 Obsidian 目录不会自动连接。"
        )
        if not api_key:
            result.missing_user_actions.append(
                f"Complete extension model config: {_active_key_label()}."
            )
        return

    if not api_key:
        result.missing_user_actions.append(
            f"Complete extension model config: {_active_key_label()}."
        )

    vault = _safe_explicit_vault(vault_raw)
    if vault is None:
        result.missing_user_actions.append(
            "The configured vault path is missing, not a directory, or points inside .obsidian. "
            "Fix [vault].path; automatic discovery was not used."
        )
        return
    identity_state, _identity = inspect_vault_identity(vault)
    if identity_state != "valid":
        result.missing_user_actions.append(
            "配置目录没有有效的 Agent-wiki 身份标记，请在扩展中重新选择知识库。"
        )
        return
    lifecycle = VaultLifecycleManager(runtime_root=RUNTIME_ROOT, config_path=CONFIG_PATH)
    switched = lifecycle.switch(vault_path=vault)
    if not switched.get("ok"):
        result.missing_user_actions.append(switched.get("message") or "请在扩展中选择 Agent-wiki 知识库。")
        return
    result.actions.append(f"vault selected by explicit config: {vault}")
    result.actions.append(f"vault structure ready: {vault}")


def bootstrap(
    *,
    install_deps: bool = True,
    vault_path: Optional[Path] = None,
    verify_websocket: bool = True,
) -> CheckResult:
    result = CheckResult()
    ensure_runtime_dirs(result)
    ensure_douyin_venv(result, install_deps=install_deps)
    check_ffmpeg(result)
    ensure_config_template(result)
    if vault_path is not None:
        selected = _safe_explicit_vault(str(vault_path))
        if selected is None:
            result.add_warning(
                "explicit vault must be an existing directory outside .obsidian",
                fatal=True,
            )
        else:
            lifecycle = VaultLifecycleManager(runtime_root=RUNTIME_ROOT, config_path=CONFIG_PATH)
            try:
                initialized = lifecycle.initialize_explicit_empty_vault(selected)
            except VaultLifecycleError as exc:
                result.add_warning(str(exc), fatal=True)
            else:
                if initialized.get("ok"):
                    result.actions.append(f"explicit vault configured: {selected}")
                else:
                    result.add_warning(
                        initialized.get("message") or "explicit vault initialization failed",
                        fatal=True,
                    )
    ensure_extension_copy(result)
    if verify_websocket:
        check_websocket(result)
    else:
        result.actions.append("WebSocket health check skipped by explicit isolation option")
    if result.ok:
        check_vault(result)
    return result


def main(argv: Optional[list[str]] = None, *, prog: Optional[str] = None) -> int:
    parser = argparse.ArgumentParser(prog=prog, description="Prepare Agent-wiki runtime")
    parser.add_argument("--skip-install-deps", action="store_true", help="skip Python dependency installation")
    parser.add_argument(
        "--vault",
        type=_explicit_vault_arg,
        help="use this explicit vault and never fall back to automatic discovery",
    )
    parser.add_argument(
        "--skip-websocket-check",
        action="store_true",
        help="do not inspect the default control-plane port",
    )
    args = parser.parse_args(argv)

    result = bootstrap(
        install_deps=not args.skip_install_deps,
        vault_path=args.vault,
        verify_websocket=not args.skip_websocket_check,
    )
    print("Agent-wiki bootstrap")
    print(f"project: {PROJECT_ROOT}")
    print(f"runtime: {RUNTIME_ROOT}")
    for item in result.actions:
        print(f"✓ {item}")
    for item in result.warnings:
        print(f"⚠ {item}")
    if result.missing_user_actions:
        print("\nUser actions still needed:")
        seen = set()
        for item in result.missing_user_actions:
            if item in seen:
                continue
            seen.add(item)
            print(f"- {item}")
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
