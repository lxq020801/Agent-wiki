#!/usr/bin/env python3
"""Safe, opt-in launchd management for Agent-wiki."""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence


LAUNCHD_LABEL = "com.agent-wiki.control-plane"
PLIST_NAME = f"{LAUNCHD_LABEL}.plist"
MANAGED_MARKER = "agent-wiki-autostart-v1"
MANAGED_KEYS = {
    "Label",
    "ProgramArguments",
    "WorkingDirectory",
    "EnvironmentVariables",
    "RunAtLoad",
    "ProcessType",
    "StandardOutPath",
    "StandardErrorPath",
    "AgentWikiManaged",
    "AgentWikiProjectRoot",
    "AgentWikiPython",
    "AgentWikiSourceCommit",
    "AgentWikiSourceVersion",
    "AgentWikiListenHost",
    "AgentWikiListenPort",
}


@dataclass(frozen=True)
class AutostartResult:
    code: int
    lines: tuple[str, ...]
    payload: Mapping[str, Any]


def _run_launchctl(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["launchctl", *command],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )


def _source_version(project_root: Path) -> dict[str, Any]:
    def git(*args: str) -> str:
        try:
            result = subprocess.run(
                ["git", "-C", str(project_root), *args],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return ""
        return result.stdout.strip() if result.returncode == 0 else ""

    commit = git("rev-parse", "HEAD")
    version = git("describe", "--tags", "--always", "--dirty")
    return {
        "commit": commit or "unknown",
        "version": version or (commit[:12] if commit else "unknown"),
    }


def _python_version(executable: Path) -> Optional[tuple[int, int, int]]:
    try:
        result = subprocess.run(
            [str(executable), "-c", "import sys; print('.'.join(map(str, sys.version_info[:3])))"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        version = tuple(int(part) for part in result.stdout.strip().split("."))
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None
    return version if result.returncode == 0 and len(version) == 3 else None


def find_python311(project_root: Path) -> Optional[Path]:
    candidates = [
        project_root / "deps" / "douyin" / ".venv" / "bin" / "python",
        Path(sys.executable),
    ]
    for name in ("python3.13", "python3.12", "python3.11", "python3"):
        found = shutil.which(name)
        if found:
            candidates.append(Path(found))
    seen: set[Path] = set()
    for candidate in candidates:
        absolute = Path(os.path.abspath(os.fspath(candidate.expanduser())))
        if not absolute.exists():
            continue
        try:
            identity = absolute.resolve()
        except OSError:
            continue
        if identity in seen:
            continue
        seen.add(identity)
        version = _python_version(absolute)
        if version and version >= (3, 11, 0):
            return absolute
    return None


class AutostartManager:
    def __init__(
        self,
        project_root: Path,
        runtime_root: Path,
        *,
        home: Optional[Path] = None,
        platform: Optional[str] = None,
        uid: Optional[int] = None,
        runner: Optional[Callable[[Sequence[str]], subprocess.CompletedProcess[str]]] = None,
        python_finder: Optional[Callable[[Path], Optional[Path]]] = None,
        version_reader: Optional[Callable[[Path], Mapping[str, Any]]] = None,
    ) -> None:
        self.project_root = project_root.expanduser().resolve()
        self.runtime_root = runtime_root.expanduser().resolve()
        self.home = (home or Path.home()).expanduser().resolve()
        self.platform = platform or sys.platform
        self.uid = os.getuid() if uid is None else uid
        self.runner = runner or _run_launchctl
        self.python_finder = python_finder or find_python311
        self.version_reader = version_reader or _source_version
        self.launch_agents_dir = self.home / "Library" / "LaunchAgents"
        self.plist_path = self.launch_agents_dir / PLIST_NAME
        self.domain = f"gui/{self.uid}"

    def _job_probe(self, expected: Optional[Mapping[str, Any]] = None) -> tuple[bool, bool]:
        try:
            result = self.runner(["print", f"{self.domain}/{LAUNCHD_LABEL}"])
        except (OSError, subprocess.TimeoutExpired):
            return False, False
        if result.returncode != 0:
            return False, False
        if expected is None:
            return True, False
        arguments = expected.get("ProgramArguments", [])
        environment = expected.get("EnvironmentVariables", {})
        identity_values = [
            *arguments,
            environment.get("AGENT_WIKI_HOME") if isinstance(environment, dict) else None,
        ]
        matches = all(
            isinstance(value, str) and value and value in result.stdout
            for value in identity_values
        )
        return True, matches

    def _desired(self, python: Path) -> dict[str, Any]:
        from server.runtime_manager import read_server_settings

        version = dict(self.version_reader(self.project_root))
        settings = read_server_settings(self.runtime_root / "config.toml")
        launcher = self.project_root / "server" / "launcher.py"
        log_path = self.runtime_root / "logs" / "autostart.log"
        return {
            "Label": LAUNCHD_LABEL,
            "ProgramArguments": [str(python), str(launcher), "start"],
            "WorkingDirectory": str(self.project_root),
            "EnvironmentVariables": {"AGENT_WIKI_HOME": str(self.runtime_root)},
            "RunAtLoad": True,
            "ProcessType": "Background",
            "StandardOutPath": str(log_path),
            "StandardErrorPath": str(log_path),
            "AgentWikiManaged": MANAGED_MARKER,
            "AgentWikiProjectRoot": str(self.project_root),
            "AgentWikiPython": str(python),
            "AgentWikiSourceCommit": str(version.get("commit") or "unknown"),
            "AgentWikiSourceVersion": str(version.get("version") or "unknown"),
            "AgentWikiListenHost": settings.host,
            "AgentWikiListenPort": settings.port,
        }

    def _read_owned(self) -> tuple[Optional[dict[str, Any]], Optional[str]]:
        path = self.plist_path
        if not path.exists() and not path.is_symlink():
            return None, None
        if path.is_symlink() or not path.is_file():
            return None, "启动项路径不是安全的普通文件"
        try:
            if path.stat().st_size > 65536:
                return None, "启动项文件异常过大"
            raw = plistlib.loads(path.read_bytes())
        except (OSError, plistlib.InvalidFileException, ValueError):
            return None, "启动项 plist 已损坏，无法确认归属"
        if not isinstance(raw, dict):
            return None, "启动项 plist 结构无效"
        if raw.get("Label") != LAUNCHD_LABEL or raw.get("AgentWikiManaged") != MANAGED_MARKER:
            return None, "发现同名但不属于 Agent-wiki 当前管理格式的启动项"
        if set(raw) != MANAGED_KEYS:
            return None, "启动项字段集合不属于当前 Agent-wiki 管理格式"
        arguments = raw.get("ProgramArguments")
        project = raw.get("AgentWikiProjectRoot")
        python = raw.get("AgentWikiPython")
        environment = raw.get("EnvironmentVariables")
        runtime = environment.get("AGENT_WIKI_HOME") if isinstance(environment, dict) else None
        host = raw.get("AgentWikiListenHost")
        port = raw.get("AgentWikiListenPort")
        try:
            loopback = isinstance(host, str) and ipaddress.ip_address(host).is_loopback
        except ValueError:
            loopback = host == "localhost"
        if not (
            isinstance(arguments, list)
            and len(arguments) == 3
            and all(isinstance(item, str) and item for item in arguments)
            and arguments[2] == "start"
            and isinstance(project, str)
            and Path(project).is_absolute()
            and isinstance(python, str)
            and Path(python).is_absolute()
            and arguments[0] == python
            and arguments[1] == str(Path(project) / "server" / "launcher.py")
            and raw.get("WorkingDirectory") == project
            and isinstance(environment, dict)
            and set(environment) == {"AGENT_WIKI_HOME"}
            and isinstance(runtime, str)
            and Path(runtime).is_absolute()
            and raw.get("RunAtLoad") is True
            and raw.get("ProcessType") == "Background"
            and raw.get("StandardOutPath") == str(Path(runtime) / "logs" / "autostart.log")
            and raw.get("StandardErrorPath") == str(Path(runtime) / "logs" / "autostart.log")
            and isinstance(raw.get("AgentWikiSourceCommit"), str)
            and isinstance(raw.get("AgentWikiSourceVersion"), str)
            and loopback
            and isinstance(port, int)
            and not isinstance(port, bool)
            and 1 <= port <= 65535
        ):
            return None, "启动项字段不完整或被修改，无法安全管理"
        return raw, None

    def _write(self, payload: Mapping[str, Any]) -> None:
        if self.launch_agents_dir.is_symlink():
            raise OSError("LaunchAgents 目录是符号链接")
        self.launch_agents_dir.mkdir(parents=True, exist_ok=True)
        if self.plist_path.is_symlink():
            raise OSError("拒绝覆盖符号链接启动项")
        fd, raw_tmp = tempfile.mkstemp(prefix=f".{PLIST_NAME}.", dir=str(self.launch_agents_dir))
        tmp = Path(raw_tmp)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb") as handle:
                plistlib.dump(dict(payload), handle, fmt=plistlib.FMT_XML, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, self.plist_path)
            os.chmod(self.plist_path, 0o600)
        finally:
            if tmp.exists():
                tmp.unlink()

    def _ensure_runtime_logs(self) -> None:
        for path in (self.runtime_root, self.runtime_root / "logs"):
            if path.is_symlink() or (path.exists() and not path.is_dir()):
                raise OSError(f"不安全的运行目录：{path}")
            path.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.chmod(path, 0o700)

    def status(self) -> AutostartResult:
        if self.platform != "darwin":
            return AutostartResult(
                0,
                ("[警告] 当前系统不支持 macOS launchd 开机启动。",),
                {"state": "unsupported", "label": LAUNCHD_LABEL},
            )
        owned, error = self._read_owned()
        loaded, loaded_matches = self._job_probe(owned)
        if error:
            return AutostartResult(
                2,
                (f"[失败] {error}；未修改文件或服务。", "恢复建议：人工检查该 plist，确认后移走，再重新启用。"),
                {"state": "unsafe", "label": LAUNCHD_LABEL, "loaded": loaded, "reason": error},
            )
        if owned is None:
            if loaded:
                message = "launchd 中存在同名服务，但没有可验证的 Agent-wiki plist"
                return AutostartResult(
                    2,
                    (f"[失败] {message}；不会卸载未知服务。",),
                    {"state": "unknown_loaded", "label": LAUNCHD_LABEL, "loaded": True},
                )
            return AutostartResult(
                0,
                ("[通过] 开机启动未启用（默认状态）。",),
                {"state": "disabled", "label": LAUNCHD_LABEL, "loaded": False},
            )
        if loaded and not loaded_matches:
            message = "launchd 中的同名服务与已验证 plist 的程序参数不一致"
            return AutostartResult(
                2,
                (f"[失败] {message}；不会卸载或覆盖未知服务。",),
                {"state": "unknown_loaded", "label": LAUNCHD_LABEL, "loaded": True},
            )
        python = self.python_finder(self.project_root)
        config_error = False
        try:
            desired = self._desired(python) if python else None
        except RuntimeError:
            desired = None
            config_error = True
        current_project = Path(str(owned["AgentWikiProjectRoot"]))
        source_exists = current_project.is_dir() and (current_project / "server" / "launcher.py").is_file()
        matches = desired is not None and owned == desired
        state = "enabled" if loaded and matches and source_exists else "mismatch"
        lines = [
            f"[{'通过' if state == 'enabled' else '警告'}] 开机启动：{state}",
            f"label: {LAUNCHD_LABEL}",
            f"plist: {self.plist_path}",
            f"source: {owned['AgentWikiProjectRoot']}",
            f"python: {owned['AgentWikiPython']}",
            f"version: {owned.get('AgentWikiSourceVersion', 'unknown')} ({owned.get('AgentWikiSourceCommit', 'unknown')})",
            f"runtime: {owned['EnvironmentVariables']['AGENT_WIKI_HOME']}",
            f"listen: {owned.get('AgentWikiListenHost', 'unknown')}:{owned.get('AgentWikiListenPort', 'unknown')}",
            f"loaded: {'yes' if loaded else 'no'}",
        ]
        if not source_exists:
            lines.append("下一步：源码目录已移动或缺失；运行 autostart disable 清理旧接线，再从新目录 enable。")
        elif config_error:
            lines.append("下一步：当前 config.toml 无法解析或监听配置不安全；修复后重新检查。")
        elif not matches:
            lines.append("下一步：Python、源码路径或版本与当前目录不一致；先 disable，再 enable。")
        elif not loaded:
            lines.append("下一步：plist 未加载；运行 autostart disable 后重新 enable。")
        return AutostartResult(
            0 if state == "enabled" else 2,
            tuple(lines),
            {
                "state": state,
                "label": LAUNCHD_LABEL,
                "loaded": loaded,
                "plist": str(self.plist_path),
                "source": str(owned["AgentWikiProjectRoot"]),
                "python": str(owned["AgentWikiPython"]),
                "version": str(owned.get("AgentWikiSourceVersion", "unknown")),
                "commit": str(owned.get("AgentWikiSourceCommit", "unknown")),
                "runtime": str(owned["EnvironmentVariables"]["AGENT_WIKI_HOME"]),
                "host": str(owned.get("AgentWikiListenHost", "unknown")),
                "port": owned.get("AgentWikiListenPort", "unknown"),
                "source_exists": source_exists,
            },
        )

    def enable(self) -> AutostartResult:
        if self.platform != "darwin":
            return AutostartResult(2, ("[失败] autostart 仅支持 macOS launchd。",), {"state": "unsupported"})
        if not (self.project_root / "server" / "launcher.py").is_file():
            return AutostartResult(2, ("[失败] 当前源码目录缺少 server/launcher.py。",), {"state": "source_missing"})
        python = self.python_finder(self.project_root)
        if python is None:
            return AutostartResult(
                2,
                ("[失败] 未找到 Python 3.11+；请先安装后重试。",),
                {"state": "python_missing"},
            )
        try:
            desired = self._desired(python)
        except RuntimeError:
            return AutostartResult(
                2,
                ("[失败] config.toml 无法解析或监听地址不安全；请先运行 ./agent-wiki doctor。",),
                {"state": "config_invalid"},
            )
        existing, error = self._read_owned()
        loaded, loaded_matches = self._job_probe(existing)
        if error:
            return AutostartResult(
                2,
                (f"[失败] {error}；不会覆盖。", "恢复建议：人工检查并移走未知 plist 后重试。"),
                {"state": "unsafe", "reason": error},
            )
        if existing is None and loaded:
            return AutostartResult(
                2,
                ("[失败] launchd 已存在未知同名服务；不会覆盖或卸载。",),
                {"state": "unknown_loaded"},
            )
        if existing is not None and loaded and not loaded_matches:
            return AutostartResult(
                2,
                ("[失败] launchd 中的同名服务与已验证 plist 参数不一致；不会覆盖或卸载。",),
                {"state": "unknown_loaded"},
            )
        if existing is not None and existing != desired:
            return AutostartResult(
                2,
                ("[失败] 已有 Agent-wiki 启动项指向不同源码、Python、运行目录或版本；不会覆盖。", "下一步：先运行 ./agent-wiki autostart disable，再重新 enable。"),
                {"state": "mismatch"},
            )
        created = existing is None
        try:
            self._ensure_runtime_logs()
        except OSError as exc:
            return AutostartResult(
                2,
                (f"[失败] 无法安全准备运行日志目录：{type(exc).__name__}",),
                {"state": "runtime_unsafe"},
            )
        if created:
            try:
                self._write(desired)
            except OSError as exc:
                return AutostartResult(2, (f"[失败] 无法安全写入启动项：{type(exc).__name__}",), {"state": "write_failed"})
        if not loaded:
            try:
                result = self.runner(["bootstrap", self.domain, str(self.plist_path)])
            except (OSError, subprocess.TimeoutExpired) as exc:
                result = subprocess.CompletedProcess([], 1, "", type(exc).__name__)
            if result.returncode != 0:
                if created:
                    try:
                        self.plist_path.unlink()
                    except OSError:
                        pass
                return AutostartResult(
                    2,
                    ("[失败] launchctl 未能加载启动项；未启用开机启动。", "下一步：运行 autostart status 检查同名服务。"),
                    {"state": "load_failed"},
                )
        return AutostartResult(
            0,
            ("[通过] 已显式启用 macOS 开机启动。", f"label: {LAUNCHD_LABEL}", f"plist: {self.plist_path}"),
            {"state": "enabled", "label": LAUNCHD_LABEL, "plist": str(self.plist_path)},
        )

    def disable(self) -> AutostartResult:
        if self.platform != "darwin":
            return AutostartResult(0, ("[通过] 当前系统没有 macOS launchd 接线需要移除。",), {"state": "unsupported"})
        owned, error = self._read_owned()
        loaded, loaded_matches = self._job_probe(owned)
        if error:
            return AutostartResult(
                2,
                (f"[失败] {error}；不会删除或卸载未知启动项。",),
                {"state": "unsafe", "reason": error},
            )
        if owned is None:
            if loaded:
                return AutostartResult(
                    2,
                    ("[失败] launchd 存在未知同名服务；不会卸载。",),
                    {"state": "unknown_loaded"},
                )
            return AutostartResult(0, ("[通过] 开机启动已处于禁用状态。",), {"state": "disabled"})
        if loaded and not loaded_matches:
            return AutostartResult(
                2,
                ("[失败] launchd 中的同名服务与已验证 plist 参数不一致；不会卸载。",),
                {"state": "unknown_loaded"},
            )
        if loaded:
            try:
                result = self.runner(["bootout", self.domain, str(self.plist_path)])
            except (OSError, subprocess.TimeoutExpired) as exc:
                result = subprocess.CompletedProcess([], 1, "", type(exc).__name__)
            if result.returncode != 0 and self._job_probe(owned)[0]:
                return AutostartResult(
                    2,
                    ("[失败] launchctl 未能卸载已验证启动项；plist 已保留。",),
                    {"state": "unload_failed"},
                )
        try:
            self.plist_path.unlink()
        except OSError as exc:
            return AutostartResult(
                2,
                (f"[失败] 已卸载服务，但无法移除 plist：{type(exc).__name__}",),
                {"state": "remove_failed"},
            )
        return AutostartResult(
            0,
            ("[通过] 已禁用开机启动并移除已验证的 Agent-wiki plist。",),
            {"state": "disabled", "label": LAUNCHD_LABEL},
        )


def _render(result: AutostartResult, *, as_json: bool) -> int:
    from server.runtime_manager import redact_diagnostic_value

    if as_json:
        print(json.dumps(redact_diagnostic_value(dict(result.payload)), ensure_ascii=False, indent=2))
    else:
        for line in result.lines:
            print(redact_diagnostic_value(line))
    return result.code


def main(
    argv: Optional[list[str]] = None,
    *,
    project_root: Optional[Path] = None,
    runtime_root: Optional[Path] = None,
    home: Optional[Path] = None,
    prog: Optional[str] = None,
) -> int:
    parser = argparse.ArgumentParser(prog=prog, description="管理 Agent-wiki 的 macOS 开机启动")
    parser.add_argument("command", choices=("enable", "disable", "status"))
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    args = parser.parse_args(argv)
    root = (project_root or Path(__file__).resolve().parents[1]).resolve()
    runtime = (runtime_root or Path(os.environ.get("AGENT_WIKI_HOME", "~/.agent-wiki"))).expanduser()
    manager = AutostartManager(root, runtime, home=home)
    result = getattr(manager, args.command)()
    return _render(result, as_json=args.json)


if __name__ == "__main__":
    raise SystemExit(main())
