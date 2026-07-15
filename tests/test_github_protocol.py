#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from server.websocket_server import LibrarianServer


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


class GitHubProtocolTests(unittest.TestCase):
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
                '[github]\nclient_id = "Iv1Example123"\n\n[vault]\npath = "' + str(vault) + '"\n',
                encoding="utf-8",
            )
            asyncio.run(server.handle_config_update({"vaultPath": str(vault)}))
            updated = config.read_text(encoding="utf-8")
            self.assertIn('[github]\nclient_id = "Iv1Example123"', updated)


if __name__ == "__main__":
    unittest.main()
