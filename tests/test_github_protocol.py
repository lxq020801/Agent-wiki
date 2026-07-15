#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from server.websocket_server import LibrarianServer
from server.github_service import GitHubServiceError


class FakeWebSocket:
    def __init__(self) -> None:
        self.messages = []

    async def send(self, text: str) -> None:
        self.messages.append(json.loads(text))


class FakeGitHubService:
    def __init__(self) -> None:
        self.import_batches = {}

    def status(self, *, validate=False):
        return {
            "ok": True,
            "state": "ready",
            "configured": {"configured": True},
            "authenticated": True,
            "account": {"login": "octocat"},
            "settings": {"autoStar": False},
        }

    def search_repositories(self, query, *, page, per_page):
        return {
            "ok": True,
            "query": query,
            "page": page,
            "perPage": per_page,
            "repositories": [{"id": 1, "fullName": "openai/example"}],
        }

    def public_batch(self, batch):
        return {key: value for key, value in batch.items() if key not in {"items", "cancelled"}}


class BatchGitHubService(FakeGitHubService):
    def __init__(self, *, cancel_after_first=False) -> None:
        super().__init__()
        self.cancel_after_first = cancel_after_first
        self.calls = 0

    def ingest_repository(self, identity, *, ingest_intent):
        self.calls += 1
        batch = next(iter(self.import_batches.values()))
        if self.cancel_after_first and self.calls == 1:
            batch["cancelled"] = True
        if identity["id"] == 2:
            raise GitHubServiceError("not_found", "仓库不存在")
        return {
            "ok": True,
            "state": "created",
            "repository": {"id": identity["id"], "fullName": identity["fullName"]},
        }


class GitHubProtocolTests(unittest.TestCase):
    @staticmethod
    def make_batch(service: BatchGitHubService) -> dict:
        batch = {
            "id": "batch-1",
            "state": "queued",
            "total": 3,
            "completed": 0,
            "succeeded": 0,
            "failed": 0,
            "cancelled": False,
            "items": [
                {"id": 1, "fullName": "openai/one"},
                {"id": 2, "fullName": "openai/missing"},
                {"id": 3, "fullName": "openai/three"},
            ],
            "results": [],
        }
        service.import_batches[batch["id"]] = batch
        return batch

    def test_github_messages_require_compatible_handshake(self) -> None:
        server = LibrarianServer(enable_task_runner=False, github_service=FakeGitHubService())
        socket = FakeWebSocket()
        server.clients.add(socket)
        server.client_compatibility[socket] = {"state": "handshake_required", "canOperate": False}
        asyncio.run(server.handle_message(socket, {"type": "github_repository_search", "query": "example"}))
        self.assertEqual(socket.messages[-1]["type"], "protocol_rejected")

    def test_repository_search_response_has_no_token_fields(self) -> None:
        server = LibrarianServer(enable_task_runner=False, github_service=FakeGitHubService())
        socket = FakeWebSocket()
        server.clients.add(socket)
        server.client_compatibility[socket] = {"state": "compatible", "canOperate": True}
        asyncio.run(server.handle_message(socket, {
            "type": "github_repository_search",
            "query": "example",
            "page": 2,
            "perPage": 10,
        }))
        payload = socket.messages[-1]
        self.assertEqual(payload["type"], "github_repository_results")
        serialized = json.dumps(payload).lower()
        self.assertNotIn("access_token", serialized)
        self.assertNotIn("authorization", serialized)
        self.assertEqual(payload["result"]["page"], 2)

    def test_general_config_update_preserves_github_client_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            vault = root / "vault"
            vault.mkdir()
            server = LibrarianServer(enable_task_runner=False, github_service=FakeGitHubService())
            server.runtime_root = root / "runtime"
            server.runtime_root.mkdir()
            config = server.runtime_root / "config.toml"
            config.write_text(
                '[github]\nclient_id = "Iv1Example123"\n\n[analysis]\nvideo_fps_mode = "3"\n\n'
                '[vault]\npath = "' + str(vault) + '"\n',
                encoding="utf-8",
            )
            asyncio.run(server.handle_config_update({"vaultPath": str(vault)}))
            updated = config.read_text(encoding="utf-8")
            self.assertIn('[github]\nclient_id = "Iv1Example123"', updated)
            self.assertIn("fps_min = 2.0", updated)
            self.assertIn('video_fps_mode = "fixed_3"', updated)

    def test_explicit_vault_status_never_falls_back_to_discovery(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            vault = root / "empty-vault"
            vault.mkdir()
            server = LibrarianServer(enable_task_runner=False, github_service=FakeGitHubService())
            server.runtime_root = root / "runtime"
            server.runtime_root.mkdir()
            (server.runtime_root / "config.toml").write_text(
                f'[vault]\npath = "{vault}"\n',
                encoding="utf-8",
            )

            with mock.patch(
                "server.websocket_server.discover_vault",
                side_effect=AssertionError("explicit config must not auto-discover"),
            ):
                status = server.vault_status()

            self.assertTrue(status["ok"])
            self.assertEqual(status["path"], str(vault.resolve()))
            self.assertEqual(status["reasons"], ["explicit_config"])

    def test_obsidian_internal_status_and_invalid_update_never_discover(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            internal = root / ".ObSiDiAn" / "looks-like-vault"
            (internal / "知识资产").mkdir(parents=True)
            (internal / "index.md").write_text("# 知识库索引\n", encoding="utf-8")
            server = LibrarianServer(enable_task_runner=False, github_service=FakeGitHubService())
            server.runtime_root = root / "runtime"
            server.runtime_root.mkdir()
            config = server.runtime_root / "config.toml"
            config.write_text(f'[vault]\npath = "{internal}"\n', encoding="utf-8")

            with mock.patch(
                "server.websocket_server.discover_vault",
                side_effect=AssertionError("invalid explicit path must not auto-discover"),
            ):
                status = server.vault_status()
                with self.assertRaisesRegex(ValueError, "outside .obsidian"):
                    asyncio.run(server.handle_config_update({"vaultPath": str(root / "missing")}))

            self.assertEqual(status["state"], "invalid")
            self.assertIn(str(internal), config.read_text(encoding="utf-8"))

    def test_folder_picker_never_discovers_after_explicit_invalid_selection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            internal = root / ".ObSiDiAn" / "selected"
            internal.mkdir(parents=True)
            server = LibrarianServer(enable_task_runner=False, github_service=FakeGitHubService())
            server.runtime_root = root / "runtime"
            server.runtime_root.mkdir()

            with (
                mock.patch("server.websocket_server.sys.platform", "darwin"),
                mock.patch(
                    "server.websocket_server.subprocess.run",
                    return_value=SimpleNamespace(returncode=0, stdout=str(internal), stderr=""),
                ),
                mock.patch(
                    "server.websocket_server.discover_vault",
                    side_effect=AssertionError("folder selection must not auto-discover"),
                ),
            ):
                status = server.pick_vault_folder()

            self.assertEqual(status["state"], "invalid")
            self.assertFalse((server.runtime_root / "config.toml").exists())

    def test_import_runner_reports_each_failure_and_continues(self) -> None:
        service = BatchGitHubService()
        batch = self.make_batch(service)
        server = LibrarianServer(enable_task_runner=False, github_service=service)
        socket = FakeWebSocket()
        server.clients.add(socket)

        asyncio.run(server._run_github_import(batch["id"]))

        self.assertEqual(batch["state"], "completed")
        self.assertEqual(batch["completed"], 3)
        self.assertEqual(batch["succeeded"], 2)
        self.assertEqual(batch["failed"], 1)
        self.assertEqual(batch["results"][1]["code"], "not_found")
        self.assertEqual(socket.messages[-1]["result"]["state"], "completed")

    def test_import_runner_cancels_only_unstarted_items(self) -> None:
        service = BatchGitHubService(cancel_after_first=True)
        batch = self.make_batch(service)
        server = LibrarianServer(enable_task_runner=False, github_service=service)

        asyncio.run(server._run_github_import(batch["id"]))

        self.assertEqual(batch["state"], "cancelled")
        self.assertEqual(batch["completed"], 1)
        self.assertEqual(batch["succeeded"], 1)
        self.assertEqual(service.calls, 1)


if __name__ == "__main__":
    unittest.main()
