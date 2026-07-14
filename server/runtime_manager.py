#!/usr/bin/env python3
"""Safe local service management and diagnostics for Agent-wiki.

The module deliberately does not discover or kill processes by name. A process
is signalled only when the private state record, PID file, process start token,
Python executable, and service entrypoint all agree.
"""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import shlex
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import time
import tomllib
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence, TextIO


SERVICE_ID = "agent-wiki-control-plane"
STATE_SCHEMA_VERSION = 2
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
CONTROL_MODULES = ("websockets",)
INGEST_MODULES = (
    "httpx",
    "yaml",
    "Cryptodome",
    "pydantic",
    "gmssl",
    "browser_cookie3",
    "qrcode",
    "rich",
    "importlib_resources",
    "openai",
)
DEPENDENCY_POLICIES: dict[str, dict[str, Any]] = {
    "websockets": {"distribution": "websockets", "minimum": (12, 0)},
    "httpx": {"distribution": "httpx", "prefix": (0, 27)},
    "yaml": {"distribution": "PyYAML", "minimum": (6, 0)},
    "Cryptodome": {"distribution": "pycryptodomex", "minimum": (3, 19)},
    "pydantic": {"distribution": "pydantic", "minimum": (2, 0)},
    "gmssl": {"distribution": "gmssl", "minimum": (3, 2)},
    "browser_cookie3": {"distribution": "browser-cookie3", "minimum": (0, 19)},
    "qrcode": {"distribution": "qrcode", "minimum": (7, 4)},
    "rich": {"distribution": "rich", "minimum": (13, 0)},
    "importlib_resources": {"distribution": "importlib_resources", "minimum": (6, 0)},
    "openai": {"distribution": "openai", "minimum": (1, 40)},
}
EXTENSION_IGNORES = {".DS_Store", "__pycache__"}


class RuntimeOperationError(RuntimeError):
    """A safe, user-facing runtime operation failure."""


class StateError(RuntimeOperationError):
    """Service state cannot be trusted enough for process management."""


@dataclass(frozen=True)
class ServerSettings:
    enabled: bool = True
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT


@dataclass(frozen=True)
class ProcessSnapshot:
    pid: int
    start_token: str
    command: str


@dataclass(frozen=True)
class ServiceState:
    schema_version: int
    service_id: str
    pid: int
    process_start_token: str
    project_root: str
    entrypoint: str
    python: str
    python_identity: str
    host: str
    port: int
    source_version: str
    source_commit: str
    source_dirty: bool
    started_at: str
    log_path: str

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "ServiceState":
        required = {
            "schema_version",
            "service_id",
            "pid",
            "process_start_token",
            "project_root",
            "entrypoint",
            "python",
            "python_identity",
            "host",
            "port",
            "source_version",
            "source_commit",
            "source_dirty",
            "started_at",
            "log_path",
        }
        if not required.issubset(data):
            raise StateError("service metadata is incomplete")
        try:
            state = cls(**{key: data[key] for key in required})
        except (TypeError, ValueError) as exc:
            raise StateError("service metadata has invalid field types") from exc
        if state.schema_version != STATE_SCHEMA_VERSION or state.service_id != SERVICE_ID:
            raise StateError("service metadata has an unknown owner or schema")
        if not isinstance(state.pid, int) or state.pid <= 1:
            raise StateError("service metadata contains an invalid PID")
        if not isinstance(state.port, int) or not 1 <= state.port <= 65535:
            raise StateError("service metadata contains an invalid port")
        if not all(
            isinstance(value, str) and value
            for value in (
                state.process_start_token,
                state.project_root,
                state.entrypoint,
                state.python,
                state.python_identity,
                state.started_at,
                state.log_path,
            )
        ):
            raise StateError("service metadata contains empty identity fields")
        return state


@dataclass(frozen=True)
class ServiceStatus:
    state: str
    message: str
    metadata: Optional[ServiceState] = None
    snapshot: Optional[ProcessSnapshot] = None

    @property
    def running(self) -> bool:
        return self.state in {"running", "running_other_source"}


@dataclass(frozen=True)
class OperationResult:
    code: int
    lines: tuple[str, ...]
    payload: Optional[Mapping[str, Any]] = None


@dataclass(frozen=True)
class DoctorCheck:
    key: str
    status: str
    message: str


@dataclass(frozen=True)
class CacheUsage:
    category: str
    files: int
    bytes: int
    errors: int = 0


def default_runtime_root() -> Path:
    raw = os.environ.get("AGENT_WIKI_HOME")
    return Path(raw).expanduser() if raw else Path.home() / ".agent-wiki"


def _path_is_symlink(path: Path) -> bool:
    try:
        return stat.S_ISLNK(path.lstat().st_mode)
    except FileNotFoundError:
        return False


def _ensure_private_dir(path: Path) -> None:
    if path.exists() or _path_is_symlink(path):
        if _path_is_symlink(path) or not path.is_dir():
            raise RuntimeOperationError(f"runtime path is not a safe directory: {path}")
    else:
        path.mkdir(parents=True, mode=0o700)
    os.chmod(path, 0o700)


def _atomic_private_write(path: Path, text: str) -> None:
    _ensure_private_dir(path.parent)
    if _path_is_symlink(path):
        raise RuntimeOperationError(f"refusing to replace symlinked runtime state: {path}")
    fd, raw_tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    tmp = Path(raw_tmp)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
        os.chmod(path, 0o600)
    finally:
        if tmp.exists():
            tmp.unlink()


def _read_toml(path: Path) -> dict[str, Any]:
    with path.open("rb") as handle:
        data = tomllib.load(handle)
    if not isinstance(data, dict):
        raise RuntimeOperationError("config.toml does not contain a TOML table")
    return data


def read_server_settings(config_path: Path) -> ServerSettings:
    if not config_path.exists():
        return ServerSettings()
    if _path_is_symlink(config_path) or not config_path.is_file():
        raise RuntimeOperationError("config.toml is not a safe regular file")
    try:
        data = _read_toml(config_path)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise RuntimeOperationError("config.toml cannot be parsed") from exc
    server = data.get("server", {})
    if not isinstance(server, dict):
        raise RuntimeOperationError("[server] must be a TOML table")
    enabled = server.get("enabled", True)
    host = server.get("host", DEFAULT_HOST)
    port = server.get("port", DEFAULT_PORT)
    if not isinstance(enabled, bool):
        raise RuntimeOperationError("[server].enabled must be true or false")
    if not isinstance(host, str) or not _is_loopback_host(host):
        raise RuntimeOperationError("[server].host must be a loopback address")
    if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
        raise RuntimeOperationError("[server].port must be an integer from 1 to 65535")
    return ServerSettings(enabled=enabled, host=host, port=port)


def _is_loopback_host(host: str) -> bool:
    if host.strip().lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host.strip()).is_loopback
    except ValueError:
        return False


def python_version_info(executable: Path) -> Optional[tuple[int, int, int]]:
    try:
        result = subprocess.run(
            [str(executable), "-c", "import sys; print('.'.join(map(str, sys.version_info[:3])))"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        parts = tuple(int(item) for item in result.stdout.strip().split("."))
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None
    return parts if result.returncode == 0 and len(parts) == 3 else None


def _absolute_path_without_resolving(path: Path) -> Path:
    """Return an absolute execution path while preserving its final symlink."""
    return Path(os.path.abspath(os.fspath(path.expanduser())))


def find_python311(project_root: Path) -> Optional[Path]:
    raw_candidates: list[Optional[str]] = [
        str(project_root / "deps" / "douyin" / ".venv" / "bin" / "python"),
        sys.executable,
        shutil.which("python3.13"),
        shutil.which("python3.12"),
        shutil.which("python3.11"),
        shutil.which("python3"),
    ]
    seen: set[Path] = set()
    for raw in raw_candidates:
        if not raw:
            continue
        candidate = _absolute_path_without_resolving(Path(raw))
        if not candidate.exists():
            continue
        identity = candidate.resolve()
        if identity in seen:
            continue
        version = python_version_info(candidate)
        if version:
            seen.add(identity)
        if version and version >= (3, 11, 0):
            return candidate
    return None


def missing_python_modules(executable: Path, modules: Sequence[str]) -> list[str]:
    script = (
        "import importlib.metadata, importlib.util, json, re\n"
        f"mods = {list(modules)!r}\n"
        f"policies = {DEPENDENCY_POLICIES!r}\n"
        "missing = []\n"
        "def version_tuple(value):\n"
        "    match = re.match(r'(\\d+(?:\\.\\d+)*)', value)\n"
        "    return tuple(int(part) for part in match.group(1).split('.')) if match else ()\n"
        "for name in mods:\n"
        "    try:\n"
        "        found = importlib.util.find_spec(name) is not None\n"
        "    except (ImportError, AttributeError, ValueError):\n"
        "        found = False\n"
        "    if not found:\n"
        "        missing.append(name)\n"
        "        continue\n"
        "    policy = policies.get(name, {})\n"
        "    if not policy:\n"
        "        continue\n"
        "    try:\n"
        "        current = version_tuple(importlib.metadata.version(policy['distribution']))\n"
        "    except importlib.metadata.PackageNotFoundError:\n"
        "        missing.append(name)\n"
        "        continue\n"
        "    minimum = tuple(policy.get('minimum', ()))\n"
        "    prefix = tuple(policy.get('prefix', ()))\n"
        "    if minimum and current < minimum:\n"
        "        missing.append(name + '>=' + '.'.join(map(str, minimum)))\n"
        "    elif prefix and current[:len(prefix)] != prefix:\n"
        "        missing.append(name + '==' + '.'.join(map(str, prefix)) + '.*')\n"
        "print(json.dumps(missing))\n"
    )
    try:
        result = subprocess.run(
            [str(executable), "-c", script],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        parsed = json.loads(result.stdout) if result.returncode == 0 else list(modules)
    except (OSError, json.JSONDecodeError, subprocess.TimeoutExpired):
        return list(modules)
    return [str(item) for item in parsed] if isinstance(parsed, list) else list(modules)


def process_snapshot(pid: int) -> Optional[ProcessSnapshot]:
    if pid <= 1:
        return None
    try:
        command_result = subprocess.run(
            ["ps", "-ww", "-p", str(pid), "-o", "command="],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        start_result = subprocess.run(
            ["ps", "-ww", "-p", str(pid), "-o", "lstart="],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    command = command_result.stdout.strip()
    start_token = " ".join(start_result.stdout.split())
    if command_result.returncode != 0 or start_result.returncode != 0 or not command or not start_token:
        return None
    return ProcessSnapshot(pid=pid, start_token=start_token, command=command)


def port_in_use(host: str, port: int) -> bool:
    target = "127.0.0.1" if host.lower() in {"localhost", "0.0.0.0"} else host
    try:
        with socket.create_connection((target, port), timeout=0.25):
            return True
    except OSError:
        return False


def source_version(project_root: Path) -> dict[str, Any]:
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
    description = git("describe", "--tags", "--always", "--dirty")
    dirty = bool(git("status", "--porcelain", "--untracked-files=normal"))
    return {
        "commit": commit or "unknown",
        "version": description or (commit[:12] if commit else "unknown"),
        "dirty": dirty,
    }


def spawn_service(command: Sequence[str], cwd: Path, env: Mapping[str, str], log: TextIO) -> subprocess.Popen[Any]:
    return subprocess.Popen(
        list(command),
        cwd=str(cwd),
        env=dict(env),
        stdin=subprocess.DEVNULL,
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
        close_fds=True,
    )


def _path_token_matches(raw: str, expected: Path) -> bool:
    try:
        return Path(raw).expanduser().resolve() == expected.resolve()
    except (OSError, RuntimeError):
        return False


def _python_token_matches(raw: str, state: ServiceState) -> bool:
    actual = _absolute_path_without_resolving(Path(raw))
    expected = _absolute_path_without_resolving(Path(state.python))
    if actual != expected:
        return False
    try:
        return actual.resolve() == Path(state.python_identity)
    except (OSError, RuntimeError):
        return False


def process_matches_state(state: ServiceState, snapshot: ProcessSnapshot) -> bool:
    if snapshot.pid != state.pid or snapshot.start_token != state.process_start_token:
        return False
    try:
        tokens = shlex.split(snapshot.command)
    except ValueError:
        return False
    if len(tokens) < 2:
        return False
    if not _python_token_matches(tokens[0], state):
        return False
    if not _path_token_matches(tokens[1], Path(state.entrypoint)):
        return False

    def option(name: str) -> Optional[str]:
        try:
            index = tokens.index(name, 2)
        except ValueError:
            return None
        return tokens[index + 1] if index + 1 < len(tokens) else None

    return option("--host") == state.host and option("--port") == str(state.port)


class ServiceController:
    def __init__(
        self,
        project_root: Path,
        runtime_root: Path,
        *,
        home: Optional[Path] = None,
        python_finder: Optional[Callable[[Path], Optional[Path]]] = None,
        module_checker: Optional[Callable[[Path, Sequence[str]], list[str]]] = None,
        inspector: Optional[Callable[[int], Optional[ProcessSnapshot]]] = None,
        port_probe: Optional[Callable[[str, int], bool]] = None,
        spawner: Optional[Callable[[Sequence[str], Path, Mapping[str, str], TextIO], Any]] = None,
        killer: Optional[Callable[[int, int], None]] = None,
        sleeper: Optional[Callable[[float], None]] = None,
        clock: Optional[Callable[[], float]] = None,
        version_reader: Optional[Callable[[Path], Mapping[str, Any]]] = None,
    ) -> None:
        self.project_root = project_root.resolve()
        self.runtime_root = runtime_root.expanduser().resolve()
        self.home = (home or Path.home()).expanduser().resolve()
        self.run_dir = self.runtime_root / "run"
        self.pid_path = self.run_dir / "control-plane.pid"
        self.state_path = self.run_dir / "control-plane.json"
        self.log_path = self.runtime_root / "logs" / "control-plane.log"
        self.config_path = self.runtime_root / "config.toml"
        self.entrypoint = (self.project_root / "server" / "service_entry.py").resolve()
        self.python_finder = python_finder or find_python311
        self.module_checker = module_checker or missing_python_modules
        self.inspector = inspector or process_snapshot
        self.port_probe = port_probe or port_in_use
        self.spawner = spawner or spawn_service
        self.killer = killer or os.kill
        self.sleeper = sleeper or time.sleep
        self.clock = clock or time.monotonic
        self.version_reader = version_reader or source_version

    def _load_state(self) -> Optional[ServiceState]:
        state_exists = self.state_path.exists() or _path_is_symlink(self.state_path)
        pid_exists = self.pid_path.exists() or _path_is_symlink(self.pid_path)
        if not state_exists and not pid_exists:
            return None
        if not state_exists or not pid_exists:
            raise StateError("service state is incomplete; refusing process management")
        if _path_is_symlink(self.state_path) or _path_is_symlink(self.pid_path):
            raise StateError("service state contains a symlink; refusing process management")
        if any((_safe_file_mode(path) or 0) & 0o077 for path in (self.state_path, self.pid_path)):
            raise StateError("service state permissions are too broad; refusing process management")
        try:
            if self.state_path.stat().st_size > 65536 or self.pid_path.stat().st_size > 64:
                raise StateError("service state is unexpectedly large; refusing process management")
        except OSError as exc:
            raise StateError("service state cannot be inspected; refusing process management") from exc
        try:
            raw = json.loads(self.state_path.read_text(encoding="utf-8"))
            pid = int(self.pid_path.read_text(encoding="ascii").strip())
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
            raise StateError("service state cannot be parsed; refusing process management") from exc
        if not isinstance(raw, dict):
            raise StateError("service metadata must be a JSON object")
        state = ServiceState.from_mapping(raw)
        if state.pid != pid:
            raise StateError("PID file and service metadata disagree")
        expected_entrypoint = Path(state.project_root).expanduser().resolve() / "server" / "service_entry.py"
        if Path(state.entrypoint).expanduser().resolve() != expected_entrypoint:
            raise StateError("service metadata entrypoint does not belong to its recorded source")
        if Path(state.log_path).expanduser().resolve() != self.log_path:
            raise StateError("service metadata log path does not belong to this runtime")
        python = _absolute_path_without_resolving(Path(state.python))
        if not Path(state.python).is_absolute() or python != Path(state.python):
            raise StateError("service metadata Python execution path is not canonical")
        try:
            if python.resolve() != Path(state.python_identity):
                raise StateError("service metadata Python identity does not match its execution path")
        except (OSError, RuntimeError) as exc:
            raise StateError("service metadata Python execution path cannot be verified") from exc
        if not _is_loopback_host(state.host):
            raise StateError("service metadata contains a non-loopback host")
        return state

    def _save_state(self, state: ServiceState) -> None:
        _atomic_private_write(
            self.state_path,
            json.dumps(asdict(state), ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        )
        _atomic_private_write(self.pid_path, f"{state.pid}\n")

    def _remove_state(self) -> None:
        if _path_is_symlink(self.run_dir):
            raise StateError("runtime run directory is a symlink; refusing state cleanup")
        for path in (self.pid_path, self.state_path):
            try:
                path.unlink()
            except FileNotFoundError:
                pass

    def status(self) -> ServiceStatus:
        try:
            state = self._load_state()
        except StateError as exc:
            return ServiceStatus("unsafe_state", str(exc))
        if state is None:
            return ServiceStatus("stopped", "service is not running")
        snapshot = self.inspector(state.pid)
        if snapshot is None:
            return ServiceStatus("stale", "service process is gone; state is stale", state)
        if not process_matches_state(state, snapshot):
            return ServiceStatus(
                "identity_mismatch",
                "PID belongs to an unverified process; no signal will be sent",
                state,
                snapshot,
            )
        state_name = "running" if Path(state.project_root).resolve() == self.project_root else "running_other_source"
        message = "service is running"
        if state_name == "running_other_source":
            message = "service is running from a different source checkout"
        return ServiceStatus(state_name, message, state, snapshot)

    def _preflight(self) -> tuple[Path, ServerSettings, tuple[str, ...]]:
        warnings: list[str] = []
        python = self.python_finder(self.project_root)
        if python is None:
            raise RuntimeOperationError("Python 3.11+ was not found")
        missing = self.module_checker(python, CONTROL_MODULES)
        if missing:
            raise RuntimeOperationError(
                f"control-plane dependencies are missing for {python}: {', '.join(missing)}; "
                f"run {python} -m pip install -r {self.project_root / 'requirements.txt'}"
            )
        ingest_python = self.project_root / "deps" / "douyin" / ".venv" / "bin" / "python"
        if not ingest_python.exists():
            warnings.append("Douyin venv is missing; control-plane configuration works, but ingest will not")
        else:
            ingest_version = python_version_info(ingest_python)
            ingest_missing = self.module_checker(ingest_python, INGEST_MODULES)
            if not ingest_version or ingest_version < (3, 11, 0) or ingest_missing:
                warnings.append("Douyin venv is incomplete; run bootstrap before ingest")
        settings = read_server_settings(self.config_path)
        if self.config_path.exists() and ((_safe_file_mode(self.config_path) or 0) & 0o077):
            raise RuntimeOperationError("config.toml permissions are too broad; run doctor for details")
        if not settings.enabled:
            raise RuntimeOperationError("the service is disabled by [server].enabled in config.toml")
        legacy_service = self.runtime_root / "service"
        if legacy_service.exists() or _path_is_symlink(legacy_service):
            warnings.append("legacy ~/.agent-wiki/service deployment detected; it was not modified")
        legacy_home = self.home / ".obsidian-librarian"
        if legacy_home.exists() or _path_is_symlink(legacy_home):
            warnings.append("legacy ~/.obsidian-librarian runtime detected; it was not modified")
        if os.environ.get("OBSIDIAN_LIBRARIAN_HOME"):
            warnings.append("legacy OBSIDIAN_LIBRARIAN_HOME is set and ignored")
        status = self.status()
        if status.state == "stale":
            self._remove_state()
            warnings.append("removed stale Agent-wiki service state")
        elif status.state in {"unsafe_state", "identity_mismatch"}:
            raise RuntimeOperationError(status.message)
        elif status.running:
            if status.state == "running_other_source":
                raise RuntimeOperationError(status.message)
            return python, settings, ("service is already running",)
        if self.port_probe(settings.host, settings.port):
            legacy_hint = f"; {'; '.join(warnings)}" if warnings else ""
            raise RuntimeOperationError(
                f"{settings.host}:{settings.port} is occupied by an unmanaged process; nothing was stopped"
                f"{legacy_hint}"
            )
        return python, settings, tuple(warnings)

    def start(self, *, ready_timeout: float = 8.0) -> OperationResult:
        try:
            python, settings, warnings = self._preflight()
        except RuntimeOperationError as exc:
            return OperationResult(2, (str(exc),))
        if warnings == ("service is already running",):
            current = self.status().metadata
            return OperationResult(0, warnings, _public_state(current) if current else None)

        try:
            _ensure_private_dir(self.runtime_root)
            _ensure_private_dir(self.run_dir)
            _ensure_private_dir(self.log_path.parent)
            if _path_is_symlink(self.log_path):
                raise RuntimeOperationError(f"refusing symlinked log path: {self.log_path}")
        except RuntimeOperationError as exc:
            return OperationResult(2, (str(exc),))
        command = [
            str(python),
            str(self.entrypoint),
            "--host",
            settings.host,
            "--port",
            str(settings.port),
        ]
        env = dict(os.environ)
        env["AGENT_WIKI_HOME"] = str(self.runtime_root)
        env["PYTHONUNBUFFERED"] = "1"
        try:
            with self.log_path.open("a", encoding="utf-8") as log:
                os.chmod(self.log_path, 0o600)
                process = self.spawner(command, self.project_root, env, log)
        except OSError as exc:
            return OperationResult(2, (f"could not start service: {type(exc).__name__}",))

        snapshot = self._wait_for_snapshot(process.pid, timeout=min(2.0, ready_timeout))
        if snapshot is None:
            _terminate_spawned_process(process)
            return OperationResult(2, ("service started but its process identity could not be recorded",))
        version = dict(self.version_reader(self.project_root))
        state = ServiceState(
            schema_version=STATE_SCHEMA_VERSION,
            service_id=SERVICE_ID,
            pid=int(process.pid),
            process_start_token=snapshot.start_token,
            project_root=str(self.project_root),
            entrypoint=str(self.entrypoint),
            python=str(_absolute_path_without_resolving(python)),
            python_identity=str(python.resolve()),
            host=settings.host,
            port=settings.port,
            source_version=str(version.get("version") or "unknown"),
            source_commit=str(version.get("commit") or "unknown"),
            source_dirty=bool(version.get("dirty")),
            started_at=datetime.now(timezone.utc).isoformat(),
            log_path=str(self.log_path),
        )
        if not process_matches_state(state, snapshot):
            _terminate_spawned_process(process)
            return OperationResult(2, ("spawned process command did not match the Agent-wiki entrypoint",))
        try:
            self._save_state(state)
        except RuntimeOperationError as exc:
            _terminate_spawned_process(process)
            try:
                self._remove_state()
            except RuntimeOperationError:
                pass
            return OperationResult(2, (str(exc),))

        deadline = self.clock() + ready_timeout
        while self.clock() < deadline:
            if self.port_probe(settings.host, settings.port):
                lines = [*warnings, f"service started (PID {state.pid})", f"log: {self.log_path}"]
                return OperationResult(0, tuple(lines), _public_state(state))
            if self.inspector(state.pid) is None:
                self._remove_state()
                return OperationResult(2, (f"service exited during startup; inspect {self.log_path}",))
            self.sleeper(0.1)

        stopped = self.stop(timeout=2.0)
        return OperationResult(
            2,
            (f"service did not listen on {settings.host}:{settings.port}; inspect {self.log_path}", *stopped.lines),
        )

    def _wait_for_snapshot(self, pid: int, *, timeout: float) -> Optional[ProcessSnapshot]:
        deadline = self.clock() + timeout
        while True:
            snapshot = self.inspector(pid)
            if snapshot is not None:
                return snapshot
            if self.clock() >= deadline:
                return None
            self.sleeper(0.05)

    def stop(self, *, timeout: float = 5.0) -> OperationResult:
        status = self.status()
        if status.state == "stopped":
            return OperationResult(0, (status.message,))
        if status.state == "stale":
            try:
                self._remove_state()
            except StateError as exc:
                return OperationResult(2, (str(exc),))
            return OperationResult(0, ("removed stale service state; no process was signalled",))
        if not status.running or not status.metadata or not status.snapshot:
            return OperationResult(2, (status.message,))

        state = status.metadata
        current_snapshot = self.inspector(state.pid)
        if current_snapshot is None or not process_matches_state(state, current_snapshot):
            return OperationResult(2, ("service identity changed before stop; no signal was sent",))
        try:
            self.killer(state.pid, signal.SIGTERM)
        except (OSError, PermissionError) as exc:
            return OperationResult(2, (f"could not stop verified service: {type(exc).__name__}",))
        if self._wait_for_exit(state, timeout):
            self._remove_state()
            return OperationResult(0, (f"service stopped (PID {state.pid})",))

        snapshot = self.inspector(state.pid)
        if snapshot is None or not process_matches_state(state, snapshot):
            self._remove_state()
            return OperationResult(0, ("service stopped; reused or changed PID was not signalled",))
        try:
            self.killer(state.pid, signal.SIGKILL)
        except (OSError, PermissionError) as exc:
            return OperationResult(2, (f"verified service ignored SIGTERM and SIGKILL failed: {type(exc).__name__}",))
        if not self._wait_for_exit(state, 2.0):
            return OperationResult(2, ("verified service did not exit; state was retained",))
        self._remove_state()
        return OperationResult(0, (f"service force-stopped after timeout (PID {state.pid})",))

    def _wait_for_exit(self, state: ServiceState, timeout: float) -> bool:
        deadline = self.clock() + max(timeout, 0.0)
        while True:
            snapshot = self.inspector(state.pid)
            if snapshot is None or not process_matches_state(state, snapshot):
                return True
            if self.clock() >= deadline:
                return False
            self.sleeper(0.05)

    def restart(self) -> OperationResult:
        stopped = self.stop()
        if stopped.code != 0:
            return stopped
        started = self.start()
        return OperationResult(started.code, (*stopped.lines, *started.lines), started.payload)

    def foreground(self) -> OperationResult:
        try:
            python, settings, warnings = self._preflight()
        except RuntimeOperationError as exc:
            return OperationResult(2, (str(exc),))
        if warnings == ("service is already running",):
            return OperationResult(2, warnings)
        command = [
            str(python),
            str(self.entrypoint),
            "--host",
            settings.host,
            "--port",
            str(settings.port),
        ]
        env = dict(os.environ)
        env["AGENT_WIKI_HOME"] = str(self.runtime_root)
        lines = (*warnings, f"running control plane at ws://{settings.host}:{settings.port}")
        for line in lines:
            print(line)
        try:
            code = subprocess.call(command, cwd=str(self.project_root), env=env)
        except KeyboardInterrupt:
            code = 0
        return OperationResult(code, ())


def _terminate_spawned_process(process: Any) -> None:
    try:
        process.terminate()
        process.wait(timeout=2)
    except (AttributeError, OSError, subprocess.TimeoutExpired):
        try:
            process.kill()
        except (AttributeError, OSError):
            pass


def _public_state(state: Optional[ServiceState]) -> Optional[dict[str, Any]]:
    if state is None:
        return None
    return {
        "state": "running",
        "pid": state.pid,
        "host": state.host,
        "port": state.port,
        "project_root": state.project_root,
        "source_version": state.source_version,
        "source_commit": state.source_commit,
        "source_dirty": state.source_dirty,
        "started_at": state.started_at,
        "log_path": state.log_path,
    }


def _safe_file_mode(path: Path) -> Optional[int]:
    try:
        return stat.S_IMODE(path.lstat().st_mode)
    except OSError:
        return None


def _permission_check(key: str, label: str, path: Path) -> DoctorCheck:
    mode = _safe_file_mode(path)
    if mode is None:
        return DoctorCheck(key, "FAIL", f"{label} is not accessible")
    if mode & 0o077:
        return DoctorCheck(key, "FAIL", f"{label} permissions are too broad ({mode:04o}; expected 0600)")
    return DoctorCheck(key, "PASS", f"{label} permissions are private ({mode:04o})")


def _tree_manifest(root: Path) -> tuple[dict[str, str], int]:
    manifest: dict[str, str] = {}
    errors = 0
    if not root.is_dir() or _path_is_symlink(root):
        return manifest, errors
    for current, dirs, files in os.walk(root, followlinks=False):
        base = Path(current)
        dirs[:] = [name for name in dirs if name not in EXTENSION_IGNORES and not _path_is_symlink(base / name)]
        for name in files:
            path = base / name
            if name in EXTENSION_IGNORES or _path_is_symlink(path):
                errors += 1
                continue
            try:
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                manifest[str(path.relative_to(root))] = digest
            except OSError:
                errors += 1
    return manifest, errors


def extension_copy_check(project_root: Path, runtime_root: Path) -> DoctorCheck:
    source = project_root / "chrome-extension"
    destination = runtime_root / "extension"
    if not destination.exists():
        return DoctorCheck("extension.copy", "WARN", "runtime extension copy is missing; run bootstrap")
    source_manifest, source_errors = _tree_manifest(source)
    destination_manifest, destination_errors = _tree_manifest(destination)
    if source_errors or destination_errors:
        return DoctorCheck("extension.copy", "WARN", "extension copy contains unreadable files or symlinks")
    missing = len(source_manifest.keys() - destination_manifest.keys())
    extra = len(destination_manifest.keys() - source_manifest.keys())
    changed = sum(
        1
        for name in source_manifest.keys() & destination_manifest.keys()
        if source_manifest[name] != destination_manifest[name]
    )
    if missing or extra or changed:
        return DoctorCheck(
            "extension.copy",
            "WARN",
            f"runtime extension differs from source (missing {missing}, changed {changed}, extra {extra})",
        )
    return DoctorCheck("extension.copy", "PASS", f"runtime extension matches source ({len(source_manifest)} files)")


class Doctor:
    def __init__(
        self,
        controller: ServiceController,
        *,
        tool_finder: Optional[Callable[[str], Optional[str]]] = None,
        version_reader: Optional[Callable[[Path], Optional[tuple[int, int, int]]]] = None,
    ) -> None:
        self.controller = controller
        self.tool_finder = tool_finder or shutil.which
        self.version_reader = version_reader or python_version_info

    def run(self) -> list[DoctorCheck]:
        checks: list[DoctorCheck] = []
        python = self.controller.python_finder(self.controller.project_root)
        if python is None:
            checks.append(DoctorCheck("python", "FAIL", "Python 3.11+ was not found"))
        else:
            version = self.version_reader(python)
            rendered = ".".join(str(part) for part in version) if version else "unknown"
            checks.append(DoctorCheck("python", "PASS", f"Python {rendered} available"))
            missing = self.controller.module_checker(python, CONTROL_MODULES)
            checks.append(
                DoctorCheck(
                    "dependencies.control",
                    "FAIL" if missing else "PASS",
                    f"control-plane dependency issues: {', '.join(missing)}"
                    if missing
                    else "control-plane dependencies available",
                )
            )

        ingest_python = self.controller.project_root / "deps" / "douyin" / ".venv" / "bin" / "python"
        if not ingest_python.exists():
            checks.append(DoctorCheck("dependencies.ingest", "WARN", "Douyin venv is missing; run bootstrap"))
        else:
            version = self.version_reader(ingest_python)
            missing = (
                self.controller.module_checker(ingest_python, INGEST_MODULES)
                if version and version >= (3, 11, 0)
                else list(INGEST_MODULES)
            )
            if not version or version < (3, 11, 0):
                checks.append(DoctorCheck("dependencies.ingest", "FAIL", "Douyin venv does not use Python 3.11+"))
            elif missing:
                checks.append(
                    DoctorCheck(
                        "dependencies.ingest",
                        "FAIL",
                        f"Douyin venv dependency issues: {', '.join(missing)}",
                    )
                )
            else:
                checks.append(DoctorCheck("dependencies.ingest", "PASS", "Douyin venv dependencies available"))

        for tool in ("ffmpeg", "ffprobe"):
            checks.append(
                DoctorCheck(
                    f"tool.{tool}",
                    "PASS" if self.tool_finder(tool) else "FAIL",
                    f"{tool} available" if self.tool_finder(tool) else f"{tool} not found",
                )
            )
        checks.extend(self._config_checks())
        checks.append(extension_copy_check(self.controller.project_root, self.controller.runtime_root))
        checks.extend(self._service_checks())
        checks.extend(self._legacy_checks())
        return checks

    def _config_checks(self) -> list[DoctorCheck]:
        path = self.controller.config_path
        if not path.exists() or _path_is_symlink(path) or not path.is_file():
            return [DoctorCheck("config", "FAIL", "config.toml is missing or is not a safe regular file")]
        checks = [_permission_check("config.permissions", "config.toml", path)]
        try:
            data = _read_toml(path)
            settings = read_server_settings(path)
        except RuntimeOperationError:
            return [*checks, DoctorCheck("config.syntax", "FAIL", "config.toml is invalid")]
        except (OSError, tomllib.TOMLDecodeError):
            return [*checks, DoctorCheck("config.syntax", "FAIL", "config.toml is invalid")]
        checks.append(DoctorCheck("config.syntax", "PASS", "config.toml parses successfully"))
        ark = data.get("ark", {})
        configured = isinstance(ark, dict) and bool(str(ark.get("api_key", "")).strip())
        checks.append(
            DoctorCheck(
                "config.credentials",
                "PASS" if configured else "WARN",
                "Ark credential is configured" if configured else "Ark credential is not configured",
            )
        )
        checks.extend(self._cookie_checks(data))
        checks.extend(self._vault_checks(data))
        checks.append(
            DoctorCheck(
                "config.server",
                "PASS" if settings.enabled else "WARN",
                f"server settings valid ({settings.host}:{settings.port})"
                if settings.enabled
                else "service is disabled in config.toml",
            )
        )
        return checks

    def _cookie_checks(self, data: Mapping[str, Any]) -> list[DoctorCheck]:
        douyin = data.get("douyin", {})
        raw = douyin.get("cookie_path", "") if isinstance(douyin, dict) else ""
        cookie_path = Path(str(raw)).expanduser() if raw else self.controller.runtime_root / "cookie" / "douyin.txt"
        if not cookie_path.exists() or _path_is_symlink(cookie_path) or not cookie_path.is_file():
            return [DoctorCheck("cookie", "WARN", "Douyin Cookie file is missing or is not a safe regular file")]
        return [
            DoctorCheck("cookie", "PASS", "Douyin Cookie file exists (content not read)"),
            _permission_check("cookie.permissions", "Douyin Cookie file", cookie_path),
        ]

    def _vault_checks(self, data: Mapping[str, Any]) -> list[DoctorCheck]:
        vault = data.get("vault", {})
        raw = vault.get("path", "") if isinstance(vault, dict) else ""
        if not isinstance(raw, str) or not raw.strip():
            return [DoctorCheck("vault", "WARN", "vault path is not configured")]
        path = Path(raw).expanduser()
        if not path.is_dir():
            return [DoctorCheck("vault", "FAIL", "configured vault directory does not exist")]
        markers = {
            ".obsidian": (path / ".obsidian").is_dir(),
            "SCHEMA.md": (path / "SCHEMA.md").is_file(),
            "index.md": (path / "index.md").is_file(),
            "knowledge directory": (path / "知识资产").is_dir(),
        }
        present = [name for name, exists in markers.items() if exists]
        status = "PASS" if len(present) >= 2 else "WARN"
        return [
            DoctorCheck(
                "vault",
                status,
                "vault top-level markers present: "
                f"{', '.join(present) if present else 'none'}; .obsidian contents were not read",
            )
        ]

    def _service_checks(self) -> list[DoctorCheck]:
        status = self.controller.status()
        checks: list[DoctorCheck] = []
        if status.running and status.metadata:
            checks.append(
                DoctorCheck(
                    "service.process",
                    "PASS",
                    f"managed service running (PID {status.metadata.pid})",
                )
            )
            source_status = "PASS" if status.state == "running" else "WARN"
            checks.append(
                DoctorCheck(
                    "service.source",
                    source_status,
                    f"running source: {status.metadata.project_root} ({status.metadata.source_version})",
                )
            )
            listening = self.controller.port_probe(status.metadata.host, status.metadata.port)
            checks.append(
                DoctorCheck(
                    "service.port",
                    "PASS" if listening else "FAIL",
                    f"managed service port {status.metadata.host}:{status.metadata.port} is listening"
                    if listening
                    else "managed service process exists but its port is not listening",
                )
            )
            return checks
        if status.state in {"unsafe_state", "identity_mismatch"}:
            checks.append(DoctorCheck("service.process", "FAIL", status.message))
        elif status.state == "stale":
            checks.append(DoctorCheck("service.process", "WARN", status.message))
        else:
            checks.append(DoctorCheck("service.process", "PASS", "managed service is stopped"))
        try:
            settings = read_server_settings(self.controller.config_path)
        except RuntimeOperationError:
            settings = ServerSettings()
        occupied = self.controller.port_probe(settings.host, settings.port)
        checks.append(
            DoctorCheck(
                "service.port",
                "WARN" if occupied else "PASS",
                f"{settings.host}:{settings.port} is occupied by an unmanaged process"
                if occupied
                else f"{settings.host}:{settings.port} is available",
            )
        )
        return checks

    def _legacy_checks(self) -> list[DoctorCheck]:
        checks: list[DoctorCheck] = []
        legacy_service = self.controller.runtime_root / "service"
        checks.append(
            DoctorCheck(
                "legacy.service",
                "WARN" if legacy_service.exists() or _path_is_symlink(legacy_service) else "PASS",
                "legacy ~/.agent-wiki/service deployment exists and was not inspected or modified"
                if legacy_service.exists() or _path_is_symlink(legacy_service)
                else "legacy ~/.agent-wiki/service deployment not found",
            )
        )
        legacy_home = self.controller.home / ".obsidian-librarian"
        old_env = bool(os.environ.get("OBSIDIAN_LIBRARIAN_HOME"))
        exists = legacy_home.exists() or _path_is_symlink(legacy_home)
        checks.append(
            DoctorCheck(
                "legacy.runtime",
                "WARN" if exists or old_env else "PASS",
                "legacy runtime path or environment variable detected; nothing was modified"
                if exists or old_env
                else "legacy runtime path and environment variable not found",
            )
        )
        return checks


def _scan_usage(root: Path, category: str) -> CacheUsage:
    files = 0
    total = 0
    errors = 0
    if not root.exists():
        return CacheUsage(category, 0, 0, 0)
    if _path_is_symlink(root) or not root.is_dir():
        return CacheUsage(category, 0, 0, 1)
    for current, dirs, names in os.walk(root, followlinks=False):
        base = Path(current)
        safe_dirs: list[str] = []
        for name in dirs:
            candidate = base / name
            if _path_is_symlink(candidate):
                errors += 1
            else:
                safe_dirs.append(name)
        dirs[:] = safe_dirs
        for name in names:
            path = base / name
            try:
                info = path.lstat()
            except OSError:
                errors += 1
                continue
            if not stat.S_ISREG(info.st_mode):
                errors += 1
                continue
            files += 1
            total += info.st_size
    return CacheUsage(category, files, total, errors)


def cache_report(runtime_root: Path) -> list[CacheUsage]:
    return [
        _scan_usage(runtime_root / "cache", "cache"),
        _scan_usage(runtime_root / "run-artifacts", "run-artifacts"),
        _scan_usage(runtime_root / "responses-memory", "responses-memory"),
    ]


def format_bytes(size: int) -> str:
    value = float(size)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024 or unit == "TiB":
            return f"{int(value)} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


def _status_payload(status: ServiceStatus) -> dict[str, Any]:
    payload: dict[str, Any] = {"state": status.state, "message": status.message}
    if status.metadata:
        payload.update(_public_state(status.metadata) or {})
        payload["state"] = status.state
    return payload


def _render_result(result: OperationResult, *, as_json: bool = False) -> int:
    if as_json:
        print(json.dumps(result.payload or {"messages": list(result.lines)}, ensure_ascii=False, indent=2))
    else:
        for line in result.lines:
            print(line)
    return result.code


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage and diagnose the Agent-wiki local control plane")
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("foreground", help="run in the foreground (default)")
    commands.add_parser("start", help="start the managed background service")
    commands.add_parser("stop", help="stop only a verified managed service")
    commands.add_parser("restart", help="restart the verified managed service")
    status = commands.add_parser("status", help="show managed service status")
    status.add_argument("--json", action="store_true", help="emit machine-readable output")
    doctor = commands.add_parser("doctor", help="run read-only environment diagnostics")
    doctor.add_argument("--json", action="store_true", help="emit machine-readable output")
    cache = commands.add_parser("cache", help="report cache usage or preview cleanup")
    cache_commands = cache.add_subparsers(dest="cache_command")
    cache_report_parser = cache_commands.add_parser("report", help="report cache usage (default)")
    cache_report_parser.add_argument("--json", action="store_true", help="emit machine-readable output")
    cache_clean = cache_commands.add_parser("clean", help="preview cleanup; deletion is not implemented")
    cache_clean.add_argument("--dry-run", action="store_true", required=True, help="required; preview cache cleanup")
    cache_clean.add_argument("--json", action="store_true", help="emit machine-readable output")
    return parser


def main(
    argv: Optional[list[str]] = None,
    *,
    project_root: Optional[Path] = None,
    runtime_root: Optional[Path] = None,
) -> int:
    root = (project_root or Path(__file__).resolve().parents[1]).resolve()
    runtime = (runtime_root or default_runtime_root()).expanduser()
    args = build_parser().parse_args(argv)
    command = args.command or "foreground"
    controller = ServiceController(root, runtime)

    if command == "foreground":
        return _render_result(controller.foreground())
    if command == "start":
        return _render_result(controller.start())
    if command == "stop":
        return _render_result(controller.stop())
    if command == "restart":
        return _render_result(controller.restart())
    if command == "status":
        status = controller.status()
        payload = _status_payload(status)
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(status.message)
            if status.metadata:
                print(f"PID: {status.metadata.pid}")
                print(f"source: {status.metadata.project_root} ({status.metadata.source_version})")
                print(f"log: {status.metadata.log_path}")
        return 0 if status.running else 3
    if command == "doctor":
        checks = Doctor(controller).run()
        if args.json:
            print(json.dumps([asdict(check) for check in checks], ensure_ascii=False, indent=2))
        else:
            for check in checks:
                print(f"[{check.status}] {check.key}: {check.message}")
        return 1 if any(check.status == "FAIL" for check in checks) else 0
    if command == "cache":
        usage = cache_report(controller.runtime_root)
        as_json = bool(getattr(args, "json", False))
        clean = args.cache_command == "clean"
        payload = {
            "mode": "dry-run" if clean else "report",
            "deletion_performed": False,
            "categories": [asdict(item) for item in usage],
        }
        if as_json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            if clean:
                cache_usage = usage[0]
                print(
                    f"DRY RUN: would remove {cache_usage.files} regular files "
                    f"({format_bytes(cache_usage.bytes)}) under cache/"
                )
                if cache_usage.errors:
                    print(f"Skipped {cache_usage.errors} non-regular, symlinked, or unreadable entries.")
                print("No files were deleted. run-artifacts/ and responses-memory/ are report-only.")
            else:
                for item in usage:
                    suffix = f", {item.errors} skipped entries" if item.errors else ""
                    print(f"{item.category}: {item.files} files, {format_bytes(item.bytes)}{suffix}")
                print("Report only. No files were deleted.")
        return 0
    raise AssertionError(f"unhandled command: {command}")


if __name__ == "__main__":
    raise SystemExit(main())
