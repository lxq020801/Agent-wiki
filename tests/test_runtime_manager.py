#!/usr/bin/env python3
"""Isolated tests for service management, doctor, and cache reporting."""

from __future__ import annotations

import contextlib
import io
import json
import os
import signal
import stat
import subprocess
import sys
import tempfile
import unittest
from dataclasses import asdict, replace
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from server.runtime_manager import (  # noqa: E402
    SERVICE_ID,
    STATE_SCHEMA_VERSION,
    Doctor,
    ProcessSnapshot,
    ServiceController,
    ServiceState,
    cache_report,
    find_python311,
    main,
    missing_python_modules,
    process_matches_state,
    read_server_settings,
)
from server.service_entry import main as service_entry_main  # noqa: E402


class FakeProcess:
    def __init__(self, pid: int) -> None:
        self.pid = pid
        self.terminated = False
        self.killed = False

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True

    def wait(self, timeout: float | None = None) -> int:
        return 0


class RuntimeManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.project = self.root / "project"
        self.runtime = self.root / "runtime"
        self.home = self.root / "home"
        (self.project / "server").mkdir(parents=True)
        (self.project / "server" / "service_entry.py").write_text("# test entry\n", encoding="utf-8")
        (self.project / "requirements.txt").write_text("websockets>=12\n", encoding="utf-8")
        self.home.mkdir()
        self.python = Path(sys.executable).resolve()

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def controller(self, **overrides) -> ServiceController:
        defaults = {
            "home": self.home,
            "python_finder": lambda _root: self.python,
            "module_checker": lambda _python, _modules: [],
            "port_probe": lambda _host, _port: False,
        }
        defaults.update(overrides)
        return ServiceController(self.project, self.runtime, **defaults)

    def state(self, controller: ServiceController, *, pid: int = 43210) -> ServiceState:
        return ServiceState(
            schema_version=STATE_SCHEMA_VERSION,
            service_id=SERVICE_ID,
            pid=pid,
            process_start_token="Mon Jul 14 10:00:00 2026",
            project_root=str(self.project.resolve()),
            entrypoint=str(controller.entrypoint),
            python=str(self.python),
            python_identity=str(self.python.resolve()),
            host="127.0.0.1",
            port=8765,
            source_version="v0.1.0-test",
            source_commit="a" * 40,
            source_dirty=False,
            started_at="2026-07-14T10:00:00+00:00",
            log_path=str(controller.log_path),
        )

    @staticmethod
    def snapshot_for(state: ServiceState) -> ProcessSnapshot:
        command = f"{state.python} {state.entrypoint} --host {state.host} --port {state.port}"
        return ProcessSnapshot(state.pid, state.process_start_token, command)

    def test_start_records_private_pid_log_and_source_version(self) -> None:
        port_results = iter([False, True])
        spawned: list[list[str]] = []
        fake_process = FakeProcess(43210)
        execution_python = self.root / "control-venv" / "bin" / "python"
        execution_python.parent.mkdir(parents=True)
        execution_python.symlink_to(self.python)

        def spawn(command, _cwd, _env, _log):
            spawned.append(list(command))
            return fake_process

        def inspect(pid: int):
            command = " ".join(spawned[0])
            return ProcessSnapshot(pid, "Mon Jul 14 10:00:00 2026", command)

        controller = self.controller(
            python_finder=lambda _root: execution_python,
            port_probe=lambda _host, _port: next(port_results),
            spawner=spawn,
            inspector=inspect,
            version_reader=lambda _root: {
                "version": "v0.1.0-3-gabcdef",
                "commit": "b" * 40,
                "dirty": False,
            },
        )
        result = controller.start(ready_timeout=1)

        self.assertEqual(result.code, 0, result.lines)
        metadata = json.loads(controller.state_path.read_text(encoding="utf-8"))
        self.assertEqual(metadata["pid"], 43210)
        self.assertEqual(metadata["source_version"], "v0.1.0-3-gabcdef")
        self.assertEqual(metadata["source_commit"], "b" * 40)
        self.assertEqual(metadata["python"], str(execution_python))
        self.assertEqual(metadata["python_identity"], str(self.python))
        self.assertEqual(controller.pid_path.read_text(encoding="ascii"), "43210\n")
        self.assertEqual(stat.S_IMODE(controller.state_path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(controller.pid_path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(controller.log_path.stat().st_mode), 0o600)
        self.assertFalse(fake_process.terminated)
        self.assertTrue(controller.status().running)
        state = ServiceState.from_mapping(metadata)
        base_command = (
            f"{execution_python.resolve()} {state.entrypoint} "
            f"--host {state.host} --port {state.port}"
        )
        self.assertFalse(
            process_matches_state(
                state,
                ProcessSnapshot(state.pid, state.process_start_token, base_command),
            )
        )

    def test_find_python311_preserves_real_symlink_venv_and_its_site_packages(self) -> None:
        venv_root = self.project / "deps" / "douyin" / ".venv"
        subprocess.run(
            [sys.executable, "-m", "venv", "--without-pip", "--symlinks", str(venv_root)],
            check=True,
            capture_output=True,
            text=True,
        )
        venv_python = venv_root / "bin" / "python"
        self.assertTrue(venv_python.is_symlink())

        site_packages = Path(
            subprocess.run(
                [str(venv_python), "-c", "import site; print(site.getsitepackages()[0])"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
        )
        module_name = "agent_wiki_venv_probe"
        (site_packages / f"{module_name}.py").write_text("VALUE = 'venv-site-packages'\n", encoding="utf-8")
        distribution = site_packages / "agent_wiki_venv_probe-1.0.dist-info"
        distribution.mkdir()
        (distribution / "METADATA").write_text(
            "Metadata-Version: 2.1\nName: agent-wiki-venv-probe\nVersion: 1.0\n",
            encoding="utf-8",
        )

        found = find_python311(self.project)

        self.assertEqual(found, venv_python.absolute())
        self.assertNotEqual(found, venv_python.resolve())
        self.assertEqual(missing_python_modules(found, [module_name]), [])
        result = subprocess.run(
            [
                str(found),
                "-c",
                f"import importlib.metadata, json, sys, {module_name}; "
                f"print(json.dumps([sys.prefix, {module_name}.VALUE, "
                "importlib.metadata.version('agent-wiki-venv-probe')]))",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        prefix, value, distribution_version = json.loads(result.stdout)
        self.assertEqual(Path(prefix), venv_root)
        self.assertEqual(value, "venv-site-packages")
        self.assertEqual(distribution_version, "1.0")

        resolved_result = subprocess.run(
            [str(found.resolve()), "-c", f"import {module_name}"],
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(resolved_result.returncode, 0)

    def test_stop_refuses_pid_that_belongs_to_unrelated_process(self) -> None:
        signals: list[tuple[int, int]] = []
        entrypoint = self.project / "server" / "service_entry.py"
        controller = self.controller(
            inspector=lambda pid: ProcessSnapshot(
                pid,
                "Mon Jul 14 10:00:00 2026",
                f"{self.python} /tmp/unrelated_server.py {entrypoint} --host 127.0.0.1 --port 8765",
            ),
            killer=lambda pid, sig: signals.append((pid, sig)),
        )
        state = self.state(controller)
        controller._save_state(state)

        result = controller.stop(timeout=0)

        self.assertEqual(result.code, 2)
        self.assertIn("no signal", result.lines[0])
        self.assertEqual(signals, [])
        self.assertTrue(controller.state_path.exists())

    def test_stop_signals_only_confirmed_process_and_cleans_state(self) -> None:
        signals: list[tuple[int, int]] = []
        snapshots: list[ProcessSnapshot | None] = []
        controller = self.controller(
            inspector=lambda _pid: snapshots.pop(0),
            killer=lambda pid, sig: signals.append((pid, sig)),
        )
        state = self.state(controller)
        snapshots.extend([self.snapshot_for(state), self.snapshot_for(state), None])
        controller._save_state(state)

        result = controller.stop(timeout=1)

        self.assertEqual(result.code, 0, result.lines)
        self.assertEqual(signals, [(state.pid, signal.SIGTERM)])
        self.assertFalse(controller.state_path.exists())
        self.assertFalse(controller.pid_path.exists())

    def test_start_refuses_managed_service_from_other_source_checkout(self) -> None:
        other_project = self.root / "other-project"
        (other_project / "server").mkdir(parents=True)
        other_entrypoint = other_project / "server" / "service_entry.py"
        other_entrypoint.write_text("# other source\n", encoding="utf-8")
        spawned: list[bool] = []
        snapshots: list[ProcessSnapshot] = []
        controller = self.controller(
            inspector=lambda _pid: snapshots[0],
            spawner=lambda *_args: spawned.append(True),
        )
        state = replace(
            self.state(controller),
            project_root=str(other_project.resolve()),
            entrypoint=str(other_entrypoint.resolve()),
        )
        snapshots.append(self.snapshot_for(state))
        controller._save_state(state)

        result = controller.start()

        self.assertEqual(result.code, 2)
        self.assertIn("different source checkout", result.lines[0])
        self.assertEqual(spawned, [])

    def test_stop_refuses_state_with_broad_permissions(self) -> None:
        signals: list[tuple[int, int]] = []
        controller = self.controller(killer=lambda pid, sig: signals.append((pid, sig)))
        state = self.state(controller)
        controller._save_state(state)
        os.chmod(controller.pid_path, 0o644)

        result = controller.stop()

        self.assertEqual(result.code, 2)
        self.assertIn("permissions are too broad", result.lines[0])
        self.assertEqual(signals, [])

    def test_start_refuses_occupied_unmanaged_port_without_spawning(self) -> None:
        spawned: list[bool] = []
        controller = self.controller(
            port_probe=lambda _host, _port: True,
            spawner=lambda *_args: spawned.append(True),
        )

        result = controller.start()

        self.assertEqual(result.code, 2)
        self.assertIn("occupied by an unmanaged process", result.lines[0])
        self.assertIn("nothing was stopped", result.lines[0])
        self.assertEqual(spawned, [])

    def test_start_does_not_report_ready_after_managed_process_exits(self) -> None:
        spawned: list[list[str]] = []
        fake_process = FakeProcess(43210)
        inspections = 0

        def spawn(command, _cwd, _env, _log):
            spawned.append(list(command))
            return fake_process

        def inspect(pid: int):
            nonlocal inspections
            inspections += 1
            if inspections == 1:
                return ProcessSnapshot(
                    pid,
                    "Mon Jul 14 10:00:00 2026",
                    " ".join(spawned[0]),
                )
            return None

        port_results = iter([False, True])
        controller = self.controller(
            spawner=spawn,
            inspector=inspect,
            port_probe=lambda _host, _port: next(port_results),
        )

        result = controller.start(ready_timeout=1)

        self.assertEqual(result.code, 2)
        self.assertIn("exited during startup", result.lines[0])
        self.assertFalse(controller.state_path.exists())
        self.assertFalse(controller.pid_path.exists())

    def test_server_settings_reject_non_loopback_host(self) -> None:
        self.runtime.mkdir()
        config = self.runtime / "config.toml"
        config.write_text('[server]\nhost = "0.0.0.0"\nport = 8765\n', encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "loopback"):
            read_server_settings(config)

    def test_service_entry_rejects_non_loopback_host(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as caught:
                service_entry_main(["--host", "0.0.0.0", "--port", "8765"])
        self.assertEqual(caught.exception.code, 2)

    def test_doctor_uses_only_metadata_and_does_not_emit_secrets(self) -> None:
        (self.project / "chrome-extension").mkdir()
        (self.project / "chrome-extension" / "manifest.json").write_text('{"name":"test"}\n', encoding="utf-8")
        (self.runtime / "extension").mkdir(parents=True)
        (self.runtime / "extension" / "manifest.json").write_text('{"name":"test"}\n', encoding="utf-8")
        cookie = self.runtime / "cookie" / "douyin.txt"
        cookie.parent.mkdir()
        cookie_secret = "cookie-secret-must-not-appear"
        cookie.write_text(cookie_secret, encoding="utf-8")
        os.chmod(cookie, 0o600)
        vault = self.root / "vault"
        (vault / ".obsidian").mkdir(parents=True)
        (vault / ".obsidian" / "private-state.json").write_text("obsidian-secret", encoding="utf-8")
        (vault / "知识资产").mkdir()
        (vault / "index.md").write_text("# test\n", encoding="utf-8")
        config_secret = "CONFIG_SECRET_SENTINEL_MUST_NOT_APPEAR"
        config = self.runtime / "config.toml"
        config.write_text(
            "\n".join(
                [
                    "[ark]",
                    f'api_key = "{config_secret}"',
                    "[douyin]",
                    f"cookie_path = {json.dumps(str(cookie))}",
                    "[vault]",
                    f"path = {json.dumps(str(vault))}",
                    "[server]",
                    "enabled = true",
                    'host = "127.0.0.1"',
                    "port = 18765",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        os.chmod(config, 0o600)
        controller = self.controller()
        checks = Doctor(
            controller,
            tool_finder=lambda _name: "/test/tool",
            version_reader=lambda _python: (3, 11, 9),
        ).run()
        output = json.dumps([asdict(check) for check in checks], ensure_ascii=False)

        self.assertNotIn(config_secret, output)
        self.assertNotIn(cookie_secret, output)
        self.assertNotIn("obsidian-secret", output)
        by_key = {check.key: check for check in checks}
        self.assertEqual(by_key["config.permissions"].status, "PASS")
        self.assertEqual(by_key["cookie.permissions"].status, "PASS")
        self.assertEqual(by_key["vault"].status, "PASS")
        self.assertEqual(by_key["extension.copy"].status, "PASS")

    def test_doctor_flags_broad_secret_file_permissions(self) -> None:
        self.runtime.mkdir()
        config = self.runtime / "config.toml"
        config.write_text("[server]\nport = 8765\n", encoding="utf-8")
        os.chmod(config, 0o644)
        controller = self.controller()
        checks = Doctor(
            controller,
            tool_finder=lambda _name: "/test/tool",
            version_reader=lambda _python: (3, 11, 9),
        ).run()
        by_key = {check.key: check for check in checks}
        self.assertEqual(by_key["config.permissions"].status, "FAIL")
        self.assertIn("0644", by_key["config.permissions"].message)

    def test_cache_report_and_dry_run_never_delete_or_follow_symlinks(self) -> None:
        cache = self.runtime / "cache"
        (cache / "nested").mkdir(parents=True)
        (cache / "a.bin").write_bytes(b"abc")
        (cache / "nested" / "b.bin").write_bytes(b"12345")
        artifacts = self.runtime / "run-artifacts"
        artifacts.mkdir()
        (artifacts / "audit.json").write_bytes(b"1234567")
        outside = self.root / "outside-secret.bin"
        outside.write_bytes(b"x" * 100)
        (cache / "outside-link").symlink_to(outside)

        usage = {item.category: item for item in cache_report(self.runtime)}
        self.assertEqual(usage["cache"].files, 2)
        self.assertEqual(usage["cache"].bytes, 8)
        self.assertEqual(usage["cache"].errors, 1)
        self.assertEqual(usage["run-artifacts"].bytes, 7)

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(
                ["cache", "clean", "--dry-run"],
                project_root=self.project,
                runtime_root=self.runtime,
            )
        self.assertEqual(code, 0)
        self.assertIn("No files were deleted", output.getvalue())
        self.assertTrue((cache / "a.bin").exists())
        self.assertTrue((cache / "nested" / "b.bin").exists())
        self.assertEqual(outside.read_bytes(), b"x" * 100)

    def test_cache_clean_has_no_non_dry_run_path(self) -> None:
        cache = self.runtime / "cache"
        cache.mkdir(parents=True)
        sentinel = cache / "keep.bin"
        sentinel.write_bytes(b"keep")

        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as caught:
                main(
                    ["cache", "clean"],
                    project_root=self.project,
                    runtime_root=self.runtime,
                )

        self.assertEqual(caught.exception.code, 2)
        self.assertEqual(sentinel.read_bytes(), b"keep")


if __name__ == "__main__":
    unittest.main()
