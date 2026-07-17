#!/usr/bin/env python3
"""Focused tests for the unified runtime onboarding commands."""

from __future__ import annotations

import contextlib
import io
import os
import plistlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from install import bootstrap as bootstrap_module  # noqa: E402
from install.autostart import (  # noqa: E402
    AutostartManager,
    AutostartResult,
    LAUNCHD_LABEL,
    MANAGED_MARKER,
)
from install import runtime_cli  # noqa: E402
from server.runtime_manager import OperationResult, redact_diagnostic_value  # noqa: E402


class FakeLaunchctl:
    def __init__(self, *, loaded: bool = False) -> None:
        self.loaded = loaded
        self.commands: list[list[str]] = []
        self.job_text = ""

    def __call__(self, command):
        args = list(command)
        self.commands.append(args)
        if args[0] == "print":
            code = 0 if self.loaded else 113
        elif args[0] == "bootstrap":
            payload = plistlib.loads(Path(args[2]).read_bytes())
            self.job_text = "\n".join([
                *payload["ProgramArguments"],
                payload["EnvironmentVariables"]["AGENT_WIKI_HOME"],
            ])
            self.loaded = True
            code = 0
        elif args[0] == "bootout":
            self.loaded = False
            code = 0
        else:
            code = 2
        return subprocess.CompletedProcess(args, code, self.job_text if self.loaded else "", "")


class AutostartTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.project = self.root / "source tree with spaces"
        self.runtime = self.root / "runtime home"
        self.home = self.root / "user home"
        (self.project / "server").mkdir(parents=True)
        (self.project / "server" / "launcher.py").write_text("# launcher\n", encoding="utf-8")
        self.home.mkdir()
        self.python = Path(sys.executable).resolve()
        self.launchctl = FakeLaunchctl()

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def manager(self, *, version: str = "v0.4-test", commit: str = "a" * 40) -> AutostartManager:
        return AutostartManager(
            self.project,
            self.runtime,
            home=self.home,
            platform="darwin",
            uid=501,
            runner=self.launchctl,
            python_finder=lambda _root: self.python,
            version_reader=lambda _root: {"version": version, "commit": commit},
        )

    def test_enable_is_structured_path_safe_and_idempotent_then_disable(self) -> None:
        manager = self.manager()

        first = manager.enable()
        second = manager.enable()
        status = manager.status()

        self.assertEqual(first.code, 0, first.lines)
        self.assertEqual(second.code, 0, second.lines)
        self.assertEqual(status.code, 0, status.lines)
        payload = plistlib.loads(manager.plist_path.read_bytes())
        self.assertEqual(payload["Label"], LAUNCHD_LABEL)
        self.assertEqual(payload["AgentWikiManaged"], MANAGED_MARKER)
        self.assertEqual(
            payload["ProgramArguments"],
            [str(self.python), str(manager.project_root / "server" / "launcher.py"), "start"],
        )
        self.assertEqual(payload["WorkingDirectory"], str(manager.project_root))
        self.assertEqual(payload["AgentWikiListenHost"], "127.0.0.1")
        self.assertEqual(payload["AgentWikiListenPort"], 8765)
        self.assertEqual(
            [command[0] for command in self.launchctl.commands].count("bootstrap"),
            1,
        )

        disabled = manager.disable()

        self.assertEqual(disabled.code, 0, disabled.lines)
        self.assertFalse(manager.plist_path.exists())
        self.assertFalse(self.launchctl.loaded)

    def test_corrupt_plist_is_never_overwritten(self) -> None:
        manager = self.manager()
        manager.launch_agents_dir.mkdir(parents=True)
        original = b"not a plist; token=super-secret"
        manager.plist_path.write_bytes(original)

        result = manager.enable()

        self.assertEqual(result.code, 2)
        self.assertEqual(manager.plist_path.read_bytes(), original)
        self.assertNotIn("bootstrap", [command[0] for command in self.launchctl.commands])

    def test_unknown_plist_and_unknown_loaded_job_are_not_managed(self) -> None:
        manager = self.manager()
        manager.launch_agents_dir.mkdir(parents=True)
        manager.plist_path.write_bytes(plistlib.dumps({"Label": LAUNCHD_LABEL, "RunAtLoad": True}))

        unknown_file = manager.disable()

        self.assertEqual(unknown_file.code, 2)
        self.assertTrue(manager.plist_path.exists())
        manager.plist_path.unlink()
        self.launchctl.loaded = True
        self.launchctl.job_text = "/tmp/unknown-python\n/tmp/unknown-service"

        unknown_job = manager.disable()

        self.assertEqual(unknown_job.code, 2)
        self.assertTrue(self.launchctl.loaded)
        self.assertNotIn("bootout", [command[0] for command in self.launchctl.commands])

    def test_loaded_job_with_different_program_is_not_unloaded(self) -> None:
        manager = self.manager()
        self.assertEqual(manager.enable().code, 0)
        self.launchctl.job_text = "/tmp/unknown-python\n/tmp/unknown-service\nstart"

        status = manager.status()
        disabled = manager.disable()

        self.assertEqual(status.code, 2)
        self.assertEqual(status.payload["state"], "unknown_loaded")
        self.assertEqual(disabled.code, 2)
        self.assertTrue(self.launchctl.loaded)
        self.assertTrue(manager.plist_path.exists())

    def test_owned_marker_with_extra_launchd_behavior_is_refused(self) -> None:
        manager = self.manager()
        self.assertEqual(manager.enable().code, 0)
        payload = plistlib.loads(manager.plist_path.read_bytes())
        payload["KeepAlive"] = True
        manager.plist_path.write_bytes(plistlib.dumps(payload))

        result = manager.disable()

        self.assertEqual(result.code, 2)
        self.assertTrue(manager.plist_path.exists())
        self.assertTrue(self.launchctl.loaded)
        self.assertNotIn("bootout", [command[0] for command in self.launchctl.commands])

    def test_invalid_server_config_prevents_enable(self) -> None:
        manager = self.manager()
        self.runtime.mkdir()
        (self.runtime / "config.toml").write_text(
            '[server]\nhost = "0.0.0.0"\nport = 8765\n',
            encoding="utf-8",
        )

        result = manager.enable()

        self.assertEqual(result.code, 2)
        self.assertEqual(result.payload["state"], "config_invalid")
        self.assertFalse(manager.plist_path.exists())

    def test_source_move_is_diagnosed_and_owned_plist_can_be_disabled(self) -> None:
        manager = self.manager()
        self.assertEqual(manager.enable().code, 0)
        moved = self.root / "moved source"
        self.project.rename(moved)

        status = manager.status()
        disabled = manager.disable()

        self.assertEqual(status.code, 2)
        self.assertEqual(status.payload["state"], "mismatch")
        self.assertFalse(status.payload["source_exists"])
        self.assertEqual(disabled.code, 0, disabled.lines)
        self.assertFalse(manager.plist_path.exists())

    def test_version_mismatch_fails_without_overwrite(self) -> None:
        original_manager = self.manager(version="v0.4-old", commit="a" * 40)
        self.assertEqual(original_manager.enable().code, 0)
        original = original_manager.plist_path.read_bytes()
        current_manager = self.manager(version="v0.4-new", commit="b" * 40)

        status = current_manager.status()
        enabled = current_manager.enable()

        self.assertEqual(status.code, 2)
        self.assertEqual(status.payload["state"], "mismatch")
        self.assertEqual(enabled.code, 2)
        self.assertEqual(current_manager.plist_path.read_bytes(), original)


class UnifiedCliTests(unittest.TestCase):
    def test_root_cli_help_uses_public_brand_for_every_command(self) -> None:
        internal_script_names = (
            "runtime_cli.py",
            "bootstrap.py",
            "runtime_manager.py",
            "autostart.py",
        )
        commands = (
            "install",
            "start",
            "stop",
            "restart",
            "status",
            "doctor",
            "uninstall",
            "autostart",
        )

        with tempfile.TemporaryDirectory() as directory:
            temporary_root = Path(directory)
            env = os.environ.copy()
            env.update(
                HOME=str(temporary_root / "home"),
                AGENT_WIKI_HOME=str(temporary_root / "runtime"),
            )
            for command in commands:
                with self.subTest(command=command):
                    result = subprocess.run(
                        [str(ROOT / "agent-wiki"), command, "--help"],
                        cwd=ROOT,
                        env=env,
                        text=True,
                        capture_output=True,
                        check=False,
                    )
                    output = result.stdout + result.stderr
                    self.assertEqual(result.returncode, 0, output)
                    self.assertIn(f"usage: agent-wiki {command}", output)
                    for script_name in internal_script_names:
                        self.assertNotIn(script_name, output)

    def test_cli_routes_install_and_service_commands_to_existing_implementations(self) -> None:
        with mock.patch("install.bootstrap.main", return_value=7) as install_main:
            self.assertEqual(runtime_cli.main(["install", "--skip-install-deps"]), 7)
        install_main.assert_called_once_with(["--skip-install-deps"], prog="agent-wiki install")

        with mock.patch("server.runtime_manager.main", return_value=9) as runtime_main:
            self.assertEqual(runtime_cli.main(["status", "--json"]), 9)
        runtime_main.assert_called_once_with(
            ["status", "--json"],
            project_root=ROOT,
            runtime_root=runtime_cli._runtime_root(),
            prog="agent-wiki",
        )

    def test_missing_dependencies_give_actionable_advice_without_installing_system_tools(self) -> None:
        result = bootstrap_module.CheckResult()
        with mock.patch.object(bootstrap_module, "_find_python", return_value=None):
            bootstrap_module.ensure_douyin_venv(result, install_deps=True)

        self.assertFalse(result.ok)
        self.assertIn("Python 3.11+", result.warnings[0])
        self.assertIn("不会修改系统 Python", result.warnings[0])

        ffmpeg_result = bootstrap_module.CheckResult()
        with mock.patch.object(bootstrap_module.shutil, "which", return_value=None):
            bootstrap_module.check_ffmpeg(ffmpeg_result)
        self.assertIn("ffmpeg, ffprobe", ffmpeg_result.warnings[0])
        self.assertIn("不会静默安装 Homebrew", ffmpeg_result.warnings[0])

    def test_uninstall_preserves_runtime_data_and_never_purges(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = root / "runtime"
            runtime.mkdir()
            secret = runtime / "config.toml"
            secret.write_text('api_key = "keep-me"\n', encoding="utf-8")

            controller = mock.Mock()
            controller.stop.return_value = OperationResult(0, ("service is not running",))
            autostart = mock.Mock()
            autostart.disable.return_value = AutostartResult(
                0,
                ("disabled",),
                {"state": "disabled"},
            )
            output = io.StringIO()
            with (
                mock.patch.dict(os.environ, {"AGENT_WIKI_HOME": str(runtime)}),
                mock.patch("server.runtime_manager.ServiceController", return_value=controller),
                mock.patch("install.autostart.AutostartManager", return_value=autostart),
                contextlib.redirect_stdout(output),
            ):
                code = runtime_cli.uninstall([], project_root=ROOT)

            self.assertEqual(code, 0)
            self.assertEqual(secret.read_text(encoding="utf-8"), 'api_key = "keep-me"\n')
            self.assertIn("不会静默清理", output.getvalue())
            controller.stop.assert_called_once_with()
            autostart.disable.assert_called_once_with()

    def test_diagnostic_redaction_removes_url_queries_and_secret_assignments(self) -> None:
        secret = "https://example.test/callback?token=secret-value&code=auth-code"
        rendered = redact_diagnostic_value(
            {
                "url": secret,
                "path": "/tmp/source token=another-secret",
                "access_token": "structured-secret",
                "safe": "127.0.0.1:8765",
            }
        )

        text = str(rendered)
        self.assertNotIn("secret-value", text)
        self.assertNotIn("auth-code", text)
        self.assertNotIn("another-secret", text)
        self.assertNotIn("structured-secret", text)
        self.assertIn("127.0.0.1:8765", text)


if __name__ == "__main__":
    unittest.main()
