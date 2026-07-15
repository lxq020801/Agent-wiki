#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))

import websocket_server


class FakeSocket:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, payload: str) -> None:
        self.sent.append(payload)


class RuntimeVersionProtocolTests(unittest.TestCase):
    def test_matching_handshake_allows_control_messages(self) -> None:
        server = websocket_server.LibrarianServer(enable_task_runner=False)
        socket = FakeSocket()
        server.clients.add(socket)
        server.client_compatibility[socket] = {
            "state": "handshake_required",
            "canOperate": False,
        }

        asyncio.run(server.handle_message(socket, {
            "type": "handshake",
            "client": "agent-wiki-extension",
            "product": websocket_server.PRODUCT_ID,
            "version": websocket_server.PRODUCT_VERSION,
            "protocolVersion": websocket_server.PROTOCOL_VERSION,
        }))

        reply = json.loads(socket.sent[-1])
        self.assertEqual(reply["type"], "handshake_ack")
        self.assertEqual(reply["compatibility"]["state"], "compatible")
        self.assertTrue(reply["compatibility"]["canOperate"])
        self.assertTrue(server.client_compatibility[socket]["canOperate"])

    def test_mismatched_protocol_is_rejected_before_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runtime = Path(tmp) / "runtime"
            old_runtime = websocket_server.default_runtime_root
            websocket_server.default_runtime_root = lambda: runtime
            try:
                server = websocket_server.LibrarianServer(enable_task_runner=False)
            finally:
                websocket_server.default_runtime_root = old_runtime
            socket = FakeSocket()
            server.clients.add(socket)
            server.client_compatibility[socket] = websocket_server._handshake_compatibility({
                "product": websocket_server.PRODUCT_ID,
                "version": websocket_server.PRODUCT_VERSION,
                "protocolVersion": websocket_server.PROTOCOL_VERSION + 1,
            })

            asyncio.run(server.handle_message(socket, {
                "type": "task_request",
                "requestId": "blocked",
                "url": "https://www.douyin.com/video/7390000000000000000",
            }))

            reply = json.loads(socket.sent[-1])
            self.assertEqual(reply["type"], "protocol_rejected")
            self.assertEqual(reply["reason"], "protocol_mismatch")
            self.assertFalse((runtime / "inbox").exists())

    def test_missing_version_fields_are_treated_as_legacy_client(self) -> None:
        compatibility = websocket_server._handshake_compatibility({
            "type": "handshake",
            "client": "agent-wiki-extension",
            "version": websocket_server.PRODUCT_VERSION,
        })
        self.assertEqual(compatibility["state"], "legacy_client")
        self.assertFalse(compatibility["canOperate"])

        mismatch = websocket_server._handshake_compatibility({
            "type": "handshake",
            "client": "agent-wiki-extension",
            "product": websocket_server.PRODUCT_ID,
            "version": "0.0.9",
            "protocolVersion": websocket_server.PROTOCOL_VERSION,
        })
        self.assertEqual(mismatch["state"], "version_mismatch")
        self.assertFalse(mismatch["canOperate"])

    def test_runtime_identity_is_comparable_and_does_not_leak_paths(self) -> None:
        with tempfile.TemporaryDirectory(prefix="api-key-secret-") as tmp:
            project_root = Path(tmp) / "private-user" / "Agent-wiki"
            extension_dir = project_root / "chrome-extension"
            extension_dir.mkdir(parents=True)
            (extension_dir / "manifest.json").write_text(
                json.dumps({"version": "0.2.1", "apiKey": "should-not-leak"}),
                encoding="utf-8",
            )
            source = project_root / "server.py"
            source.write_text("TOKEN = 'should-not-leak'\n", encoding="utf-8")

            identity = websocket_server.build_runtime_identity(project_root, source)
            serialized = json.dumps(identity, ensure_ascii=False)

            self.assertEqual(identity["product"], "agent-wiki")
            self.assertEqual(identity["protocolVersion"], 1)
            self.assertRegex(identity["buildId"], r"^src-[0-9a-f]{16}$")
            self.assertNotIn(str(project_root), serialized)
            self.assertNotIn("private-user", serialized)
            self.assertNotIn("should-not-leak", serialized)
            self.assertNotIn("api-key-secret", serialized)

    def test_legacy_path_is_reported_without_exposing_the_path(self) -> None:
        identity = websocket_server.build_runtime_identity(
            Path("/tmp/obsidian-librarian-codex"),
            ROOT / "server" / "websocket_server.py",
        )
        self.assertEqual(identity["deployment"], {
            "state": "legacy_path",
            "code": "legacy_source_path",
        })
        self.assertNotIn("obsidian-librarian-codex", json.dumps(identity))

    def test_default_runtime_identity_detects_real_legacy_symlink_launch(self) -> None:
        probe = (
            "import json, runpy, sys\n"
            "namespace = runpy.run_path(sys.argv[1], run_name='agent_wiki_symlink_probe')\n"
            "print(json.dumps(namespace['build_runtime_identity'](), sort_keys=True))\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            for legacy_name in ("obsidian-librarian", "obsidian-librarian-codex"):
                with self.subTest(legacy_name=legacy_name):
                    legacy_link = Path(tmp) / legacy_name
                    legacy_link.symlink_to(ROOT, target_is_directory=True)
                    linked_server = legacy_link / "server" / "websocket_server.py"
                    result = subprocess.run(
                        [sys.executable, "-c", probe, str(linked_server)],
                        capture_output=True,
                        text=True,
                        check=True,
                    )
                    identity = json.loads(result.stdout.strip().splitlines()[-1])
                    serialized = json.dumps(identity, ensure_ascii=False)

                    self.assertEqual(identity["productVersion"], "0.2.1")
                    self.assertEqual(identity["deployment"], {
                        "state": "legacy_path",
                        "code": "legacy_source_path",
                    })
                    self.assertRegex(identity["sourceRevision"], r"^[0-9a-f]{7,40}$")
                    self.assertRegex(identity["buildId"], r"^src-[0-9a-f]{16}$")
                    self.assertNotIn(str(legacy_link), serialized)
                    self.assertNotIn(legacy_name, serialized)

    def test_status_snapshot_exposes_same_runtime_identity(self) -> None:
        runtime = {
            "product": "agent-wiki",
            "productVersion": "0.2.1",
            "protocolVersion": 1,
            "sourceRevision": "abcdef123456",
            "buildId": "src-1234567890abcdef",
            "deployment": {"state": "current", "code": "source_checkout"},
        }
        with tempfile.TemporaryDirectory() as tmp:
            previous = os.environ.get("AGENT_WIKI_HOME")
            os.environ["AGENT_WIKI_HOME"] = str(Path(tmp) / "runtime")
            try:
                server = websocket_server.LibrarianServer(
                    enable_task_runner=False,
                    runtime_identity=runtime,
                )
                self.assertEqual(server.status_snapshot()["runtime"], runtime)
            finally:
                if previous is None:
                    os.environ.pop("AGENT_WIKI_HOME", None)
                else:
                    os.environ["AGENT_WIKI_HOME"] = previous

    def test_agent_ready_and_status_use_the_same_runtime_identity(self) -> None:
        runtime = {
            "product": "agent-wiki",
            "productVersion": "0.2.1",
            "protocolVersion": 1,
            "sourceRevision": "abcdef123456",
            "buildId": "src-1234567890abcdef",
            "deployment": {"state": "current", "code": "source_checkout"},
        }

        class ConnectedSocket(FakeSocket):
            remote_address = ("127.0.0.1", 12345)
            request_headers = {}

            def __aiter__(self):
                return self

            async def __anext__(self):
                raise StopAsyncIteration

        server = websocket_server.LibrarianServer(
            enable_task_runner=False,
            runtime_identity=runtime,
        )
        server.status_snapshot = lambda: {"runtime": runtime}
        socket = ConnectedSocket()
        asyncio.run(server.handle_client(socket))

        ready = json.loads(socket.sent[0])
        snapshot = json.loads(socket.sent[1])
        self.assertEqual(ready["runtime"], runtime)
        self.assertEqual(snapshot["status"]["runtime"], runtime)
        self.assertNotIn(socket, server.clients)
        self.assertNotIn(socket, server.client_compatibility)

    def test_extension_contains_only_knowledge_ingest_entry(self) -> None:
        extension_text = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (ROOT / "chrome-extension").rglob("*")
            if path.is_file() and path.suffix in {".js", ".html", ".json"}
        )
        self.assertNotIn("viral_breakdown", extension_text)
        self.assertNotIn("爆款拆解", extension_text)
        self.assertIn("知识入库", extension_text)


if __name__ == "__main__":
    unittest.main()
