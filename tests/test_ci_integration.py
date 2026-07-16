#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import importlib
import json
import os
import socket
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def file_snapshot(root: Path) -> dict[str, tuple[str, int]]:
    return {
        str(path.relative_to(root)): (
            hashlib.sha256(path.read_bytes()).hexdigest(),
            path.stat().st_mode & 0o777,
        )
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def unused_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class BootstrapIntegrationTests(unittest.TestCase):
    def test_explicit_vault_never_falls_back_to_obsidian_discovery(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            sandbox = Path(directory)
            runtime = sandbox / "runtime"
            home = sandbox / "home"
            home.mkdir()
            vault = sandbox / "explicit-vault"
            vault.mkdir()
            with mock.patch.dict(
                os.environ,
                {"AGENT_WIKI_HOME": str(runtime), "HOME": str(home)},
            ):
                import install.bootstrap as bootstrap_module

                bootstrap_module = importlib.reload(bootstrap_module)

            with (
                mock.patch.object(bootstrap_module, "ensure_douyin_venv"),
                mock.patch.object(bootstrap_module, "check_ffmpeg"),
                mock.patch.object(
                    bootstrap_module,
                    "discover_vault",
                    side_effect=AssertionError("explicit vault must not auto-discover"),
                ),
            ):
                result = bootstrap_module.bootstrap(
                    install_deps=False,
                    vault_path=vault,
                    verify_websocket=False,
                )

            self.assertTrue(result.ok)
            self.assertIn(f"explicit vault configured: {vault.resolve()}", result.actions)
            self.assertIn(f"vault selected by explicit config: {vault.resolve()}", result.actions)
            self.assertIn("WebSocket health check skipped by explicit isolation option", result.actions)
            self.assertTrue((vault / "知识资产" / "知识入库").is_dir())
            self.assertTrue((vault / "index.md").is_file())

    def test_invalid_explicit_vault_reports_without_discovery(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            sandbox = Path(directory)
            runtime = sandbox / "runtime"
            home = sandbox / "home"
            home.mkdir()
            runtime.mkdir()
            config = runtime / "config.toml"
            config.write_text(
                f'[vault]\npath = "{sandbox / "missing-vault"}"\n',
                encoding="utf-8",
            )
            with mock.patch.dict(
                os.environ,
                {"AGENT_WIKI_HOME": str(runtime), "HOME": str(home)},
            ):
                import install.bootstrap as bootstrap_module

                bootstrap_module = importlib.reload(bootstrap_module)

            result = bootstrap_module.CheckResult()
            with mock.patch.object(
                bootstrap_module,
                "discover_vault",
                side_effect=AssertionError("invalid explicit vault must not auto-discover"),
            ):
                bootstrap_module.check_vault(result)

            self.assertTrue(any(
                "automatic discovery was not used" in item
                for item in result.missing_user_actions
            ))

    def test_configured_obsidian_internal_path_is_rejected_before_scoring(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            sandbox = Path(directory)
            runtime = sandbox / "runtime"
            home = sandbox / "home"
            home.mkdir()
            internal = sandbox / "vault" / ".ObSiDiAn" / "looks-like-vault"
            (internal / "知识资产").mkdir(parents=True)
            (internal / "index.md").write_text("# 知识库索引\n", encoding="utf-8")
            runtime.mkdir()
            (runtime / "config.toml").write_text(
                f'[vault]\npath = "{internal}"\n',
                encoding="utf-8",
            )
            with mock.patch.dict(
                os.environ,
                {"AGENT_WIKI_HOME": str(runtime), "HOME": str(home)},
            ):
                import install.bootstrap as bootstrap_module

                bootstrap_module = importlib.reload(bootstrap_module)

            result = bootstrap_module.CheckResult()
            with mock.patch.object(
                bootstrap_module,
                "discover_vault",
                side_effect=AssertionError(".obsidian path must not auto-discover"),
            ):
                bootstrap_module.check_vault(result)

            self.assertTrue(any(
                "points inside .obsidian" in item
                for item in result.missing_user_actions
            ))
            self.assertFalse((internal / "templates").exists())
            self.assertFalse((internal / "系统记录").exists())

    def test_empty_cli_vault_argument_is_rejected_before_bootstrap(self) -> None:
        with self.assertRaises(SystemExit) as caught:
            bootstrap_module = importlib.import_module("install.bootstrap")
            bootstrap_module.main(["--vault", ""])

        self.assertEqual(caught.exception.code, 2)

    def test_bootstrap_is_idempotent_in_temporary_agent_wiki_home(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            sandbox = Path(directory)
            runtime = sandbox / "runtime"
            home = sandbox / "home"
            home.mkdir()

            with mock.patch.dict(
                os.environ,
                {"AGENT_WIKI_HOME": str(runtime), "HOME": str(home)},
            ):
                import install.bootstrap as bootstrap_module

                bootstrap_module = importlib.reload(bootstrap_module)

            self.assertEqual(bootstrap_module.RUNTIME_ROOT, runtime)
            self.assertEqual(bootstrap_module.CONFIG_PATH, runtime / "config.toml")
            self.assertEqual(bootstrap_module.EXTENSION_DEST, runtime / "extension")

            isolated_checks = (
                mock.patch.object(bootstrap_module, "ensure_douyin_venv"),
                mock.patch.object(bootstrap_module, "check_ffmpeg"),
                mock.patch.object(bootstrap_module, "check_websocket"),
                mock.patch.object(bootstrap_module, "check_vault"),
            )
            with isolated_checks[0], isolated_checks[1], isolated_checks[2], isolated_checks[3]:
                first = bootstrap_module.bootstrap(install_deps=False)
                first_snapshot = file_snapshot(runtime)
                config_mtime = bootstrap_module.CONFIG_PATH.stat().st_mtime_ns
                second = bootstrap_module.bootstrap(install_deps=False)

            self.assertTrue(first.ok)
            self.assertTrue(second.ok)
            self.assertEqual(file_snapshot(runtime), first_snapshot)
            self.assertEqual(bootstrap_module.CONFIG_PATH.stat().st_mtime_ns, config_mtime)
            self.assertEqual(bootstrap_module.CONFIG_PATH.stat().st_mode & 0o777, 0o600)

            config_text = bootstrap_module.CONFIG_PATH.read_text(encoding="utf-8")
            self.assertIn(f'cookie_path = "{runtime / "cookie" / "douyin.txt"}"', config_text)
            self.assertNotIn("~/.agent-wiki", config_text)
            self.assertIn(f"config exists: {bootstrap_module.CONFIG_PATH}", second.actions)
            self.assertTrue(any("updated 0, removed 0" in action for action in second.actions))

            for relative in (
                "inbox",
                "status",
                "archive",
                "failed",
                "cookie",
                "cache/videos",
                "handshake",
                "logs",
            ):
                self.assertTrue((runtime / relative).is_dir(), relative)
            self.assertEqual(
                (runtime / "extension" / "manifest.json").read_bytes(),
                (ROOT / "chrome-extension" / "manifest.json").read_bytes(),
            )


class WebSocketIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        import websockets

        self.websockets = websockets
        self.sandbox = tempfile.TemporaryDirectory()
        sandbox = Path(self.sandbox.name)
        self.runtime = sandbox / "runtime"
        home = sandbox / "home"
        home.mkdir()
        self.env_patch = mock.patch.dict(
            os.environ,
            {"AGENT_WIKI_HOME": str(self.runtime), "HOME": str(home)},
        )
        self.env_patch.start()

        sys.modules.pop("server.websocket_server", None)
        websocket_module = importlib.import_module("server.websocket_server")
        self.port = unused_loopback_port()
        self.server = websocket_module.LibrarianServer(
            host="127.0.0.1",
            port=self.port,
            enable_task_runner=False,
        )
        self.server_task = asyncio.create_task(self.server.start())
        self.websocket = await self._connect_when_ready()

    async def _connect_when_ready(self):
        uri = f"ws://127.0.0.1:{self.port}"
        for _ in range(100):
            if self.server_task.done():
                await self.server_task
            try:
                return await self.websockets.connect(uri, open_timeout=0.2)
            except (OSError, asyncio.TimeoutError):
                await asyncio.sleep(0.02)
        self.fail(f"WebSocket service did not start: {uri}")

    async def asyncTearDown(self) -> None:
        await self.websocket.close()
        self.server_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self.server_task
        self.env_patch.stop()
        self.sandbox.cleanup()

    async def receive_json(self) -> dict[str, object]:
        message = await asyncio.wait_for(self.websocket.recv(), timeout=2)
        return json.loads(message)

    async def test_live_server_rejects_ordinary_web_origin(self) -> None:
        uri = f"ws://127.0.0.1:{self.port}"
        hostile = await self.websockets.connect(
            uri,
            origin="https://untrusted.example",
            open_timeout=1,
        )
        try:
            with self.assertRaises(self.websockets.exceptions.ConnectionClosed):
                await asyncio.wait_for(hostile.recv(), timeout=1)
            self.assertEqual(hostile.close_code, 1008)
        finally:
            await hostile.close()

    async def test_live_status_and_removed_viral_request_contract(self) -> None:
        ready = await self.receive_json()
        initial_status = await self.receive_json()

        self.assertEqual(ready["type"], "agent_ready")
        self.assertEqual(ready["version"], "0.3.1")
        self.assertEqual(ready["runtime"]["product"], "agent-wiki")
        self.assertEqual(ready["runtime"]["productVersion"], "0.3.1")
        self.assertEqual(ready["runtime"]["protocolVersion"], 1)
        self.assertTrue(
            {"config_sync", "extension_task_ingest", "task_status"}
            <= set(ready["capabilities"])
        )
        self.assertEqual(initial_status["type"], "status_snapshot")
        self.assertIn("tasks", initial_status["status"])
        self.assertEqual(self.server.runtime_root, self.runtime)

        inbox_before = sorted((self.runtime / "inbox").glob("*.json"))
        status_before = sorted((self.runtime / "status").glob("*.json"))
        await self.websocket.send(json.dumps({
            "type": "task_request",
            "requestId": "integration-before-handshake",
            "source": "extension_popup",
            "taskType": "douyin_ingest",
            "url": "https://www.douyin.com/video/7390000000000000000",
        }))
        blocked = await self.receive_json()
        self.assertEqual(blocked["type"], "protocol_rejected")
        self.assertEqual(blocked["reason"], "handshake_required")
        self.assertEqual(sorted((self.runtime / "inbox").glob("*.json")), inbox_before)
        self.assertEqual(sorted((self.runtime / "status").glob("*.json")), status_before)

        await self.websocket.send(json.dumps({
            "type": "handshake",
            "client": "agent-wiki-background",
            "product": "agent-wiki",
            "version": "0.3.1",
            "protocolVersion": 1,
        }))
        handshake_ack = await self.receive_json()
        self.assertEqual(handshake_ack["type"], "handshake_ack")
        self.assertEqual(handshake_ack["compatibility"]["state"], "compatible")
        self.assertTrue(handshake_ack["compatibility"]["canOperate"])

        await self.websocket.send(json.dumps({"type": "status_request"}))
        requested_status = await self.receive_json()
        self.assertEqual(requested_status["type"], "status_snapshot")
        self.assertIn("vault", requested_status["status"])
        self.assertIn("llm", requested_status["status"])
        self.assertNotIn("api_key", json.dumps(requested_status).lower())

        await self.websocket.send(json.dumps({
            "type": "task_request",
            "requestId": "integration-viral-rejected",
            "source": "extension_popup",
            "taskType": "douyin_ingest",
            "ingest_intent": "viral_breakdown",
            "url": "https://www.douyin.com/video/7390000000000000000",
        }))
        rejected = await self.receive_json()
        self.assertEqual(rejected["type"], "task_rejected")
        self.assertEqual(rejected["requestId"], "integration-viral-rejected")
        self.assertEqual(rejected["reason"], "invalid_ingest_intent")
        self.assertEqual(sorted((self.runtime / "inbox").glob("*.json")), inbox_before)
        self.assertEqual(sorted((self.runtime / "status").glob("*.json")), status_before)


if __name__ == "__main__":
    unittest.main()
