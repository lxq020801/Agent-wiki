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
from server.github_service import GitHubService, GitHubServiceError


class FakeWebSocket:
    def __init__(self) -> None:
        self.messages = []

    async def send(self, text: str) -> None:
        self.messages.append(json.loads(text))


class ScriptedWebSocket(FakeWebSocket):
    def __init__(self, incoming) -> None:
        super().__init__()
        self.incoming = iter(json.dumps(item) for item in incoming)
        self.remote_address = ("127.0.0.1", 12345)
        self.request_headers = {}

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self.incoming)
        except StopIteration as exc:
            raise StopAsyncIteration from exc


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

    def import_batch(self, batch_id):
        return {
            "id": batch_id,
            "state": "completed",
            "total": 1,
            "completed": 1,
            "succeeded": 1,
            "failed": 0,
            "existing": 0,
            "cancelled": 0,
            "items": [],
        }


class BackgroundAuthGitHubService(FakeGitHubService):
    def __init__(self, *, transient_failure=False) -> None:
        super().__init__()
        self.poll_calls = 0
        self.transient_failure = transient_failure

    def start_authorization(self):
        return {
            "ok": True,
            "state": "waiting_for_user",
            "flowId": "flow-1",
            "userCode": "ABCD-EFGH",
            "verificationUri": "https://github.com/login/device",
            "expiresAt": 9999999999,
            "interval": 5,
        }

    def poll_authorization(self, flow_id):
        self.poll_calls += 1
        if self.transient_failure and self.poll_calls == 1:
            raise GitHubServiceError("network_error", "temporary network error")
        return {
            "ok": True,
            "state": "ready",
            "flowId": flow_id,
            "authenticated": True,
            "account": {"login": "octocat"},
        }


class SilentWriteLossTokenStore:
    def __init__(self) -> None:
        self.deleted = False

    def get(self):
        return ""

    def set(self, token):
        return None

    def delete(self):
        self.deleted = True


class CompletedDeviceFlowAPI:
    def __init__(self) -> None:
        self.form_calls = 0
        self.user_calls = 0

    def form_post(self, url, values):
        self.form_calls += 1
        if self.form_calls == 1:
            return {
                "device_code": "device-secret",
                "user_code": "ABCD-EFGH",
                "verification_uri": "https://github.com/login/device",
                "expires_in": 900,
                "interval": 5,
            }
        return {"access_token": "oauth-secret-token", "token_type": "bearer"}

    def request(self, method, path, *, token, **kwargs):
        self.user_calls += 1
        return {"login": "octocat", "name": "The Octocat"}, {}


class ActiveAuthorizationGitHubService(FakeGitHubService):
    def status(self, *, validate=False):
        return {
            "ok": False,
            "state": "logged_out",
            "configured": {"configured": True},
            "authenticated": False,
            "account": None,
            "settings": {"autoStar": False},
            "activeAuthorization": {
                "state": "waiting_for_user",
                "flowId": "flow-secret",
                "userCode": "ABCD-EFGH",
                "verificationUri": "https://github.com/login/device",
                "expiresAt": 9999999999,
                "interval": 5,
            },
        }


class BatchGitHubService(FakeGitHubService):
    def __init__(self, *, cancel_after_first=False) -> None:
        super().__init__()
        self.cancel_after_first = cancel_after_first
        self.calls = 0
        self.cancel_requested = False

    def begin_import_batch(self, batch_id):
        batch = self.import_batches[batch_id]
        batch["state"] = "running"
        return self.public_batch(batch)

    def queued_import_items(self, batch_id):
        return [
            {"taskId": item["taskId"], "repository": item["repository"]}
            for item in self.import_batches[batch_id]["items"]
            if item["state"] == "queued"
        ]

    def begin_import_item(self, batch_id, task_id):
        if self.cancel_requested:
            return None
        for item in self.import_batches[batch_id]["items"]:
            if item["taskId"] == task_id and item["state"] == "queued":
                item["state"] = "running"
                return item
        return None

    def ingest_repository(self, identity, *, ingest_intent):
        self.calls += 1
        if self.cancel_after_first and self.calls == 1:
            self.cancel_requested = True
        if identity["id"] == 2:
            raise GitHubServiceError("not_found", "仓库不存在")
        return {
            "ok": True,
            "state": "created",
            "repository": {"id": identity["id"], "fullName": identity["fullName"]},
        }

    def _recount(self, batch):
        batch["completed"] = sum(item["state"] in {"succeeded", "failed", "existing", "cancelled"} for item in batch["items"])
        batch["succeeded"] = sum(item["state"] == "succeeded" for item in batch["items"])
        batch["failed"] = sum(item["state"] == "failed" for item in batch["items"])
        batch["cancelled"] = sum(item["state"] == "cancelled" for item in batch["items"])

    def complete_import_item(self, batch_id, task_id, result):
        batch = self.import_batches[batch_id]
        for item in batch["items"]:
            if item["taskId"] == task_id:
                item["state"] = "succeeded"
                item["result"] = result
        batch["results"].append(result)
        if self.cancel_requested:
            for item in batch["items"]:
                if item["state"] == "queued":
                    item["state"] = "cancelled"
        self._recount(batch)
        return self.public_batch(batch)

    def fail_import_item(self, batch_id, task_id, *, code, message, repository):
        batch = self.import_batches[batch_id]
        result = {"ok": False, "code": code, "message": message, "repository": repository}
        for item in batch["items"]:
            if item["taskId"] == task_id:
                item["state"] = "failed"
                item["result"] = result
        batch["results"].append(result)
        self._recount(batch)
        return self.public_batch(batch)

    def finish_import_batch(self, batch_id):
        batch = self.import_batches[batch_id]
        batch["state"] = "cancelled" if self.cancel_requested else "completed"
        self._recount(batch)
        return self.public_batch(batch)

    def public_batch(self, batch):
        return dict(batch)


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
                {"taskId": "item-1", "state": "queued", "repository": {"id": 1, "fullName": "openai/one"}},
                {"taskId": "item-2", "state": "queued", "repository": {"id": 2, "fullName": "openai/missing"}},
                {"taskId": "item-3", "state": "queued", "repository": {"id": 3, "fullName": "openai/three"}},
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

    def test_import_status_returns_durable_batch_snapshot(self) -> None:
        server = LibrarianServer(enable_task_runner=False, github_service=FakeGitHubService())
        socket = FakeWebSocket()
        server.clients.add(socket)
        server.client_compatibility[socket] = {"state": "compatible", "canOperate": True}
        asyncio.run(server.handle_message(socket, {
            "type": "github_import_status",
            "batchId": "batch-persisted",
            "requestId": "status-1",
        }))
        payload = socket.messages[-1]
        self.assertEqual(payload["type"], "github_import_progress")
        self.assertEqual(payload["result"]["id"], "batch-persisted")
        self.assertEqual(payload["result"]["state"], "completed")

    def test_authorization_polling_continues_after_popup_disconnects(self) -> None:
        async def scenario() -> None:
            service = BackgroundAuthGitHubService()
            server = LibrarianServer(enable_task_runner=False, github_service=service)
            socket = FakeWebSocket()
            server.clients.add(socket)
            server.client_compatibility[socket] = {"state": "compatible", "canOperate": True}

            with mock.patch(
                "server.websocket_server.asyncio.sleep",
                new=mock.AsyncMock(return_value=None),
            ):
                await server.handle_message(socket, {
                    "type": "github_auth_start",
                    "requestId": "auth-start",
                })
                task = server.github_auth_tasks["flow-1"]
                server.clients.clear()
                await task

            self.assertEqual(service.poll_calls, 1)
            self.assertTrue(task.done())
            self.assertEqual(socket.messages[-1]["type"], "github_auth_state")
            self.assertEqual(socket.messages[-1]["result"]["state"], "waiting_for_user")

        asyncio.run(scenario())

    def test_authorization_polling_retries_transient_network_errors(self) -> None:
        async def scenario() -> None:
            service = BackgroundAuthGitHubService(transient_failure=True)
            server = LibrarianServer(enable_task_runner=False, github_service=service)
            socket = FakeWebSocket()
            server.clients.add(socket)
            server.client_compatibility[socket] = {"state": "compatible", "canOperate": True}

            with mock.patch(
                "server.websocket_server.asyncio.sleep",
                new=mock.AsyncMock(return_value=None),
            ):
                await server.handle_message(socket, {
                    "type": "github_auth_start",
                    "requestId": "auth-start",
                })
                task = server.github_auth_tasks["flow-1"]
                server.clients.clear()
                await task

            self.assertEqual(service.poll_calls, 2)
            self.assertTrue(task.done())

        asyncio.run(scenario())

    def test_background_keychain_failure_survives_popup_disconnect_and_reopen(self) -> None:
        async def scenario() -> None:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                store = SilentWriteLossTokenStore()
                api = CompletedDeviceFlowAPI()
                service = GitHubService(
                    runtime_root=root / "runtime",
                    config_path=root / "missing-config.toml",
                    client_id="Iv1Example123",
                    token_store=store,
                    api=api,
                )
                server = LibrarianServer(enable_task_runner=False, github_service=service)
                socket = FakeWebSocket()
                server.clients.add(socket)
                server.client_compatibility[socket] = {"state": "compatible", "canOperate": True}

                with mock.patch(
                    "server.websocket_server.asyncio.sleep",
                    new=mock.AsyncMock(return_value=None),
                ):
                    await server.handle_message(socket, {
                        "type": "github_auth_start",
                        "requestId": "auth-start",
                    })
                    task = server.github_auth_tasks[next(iter(server.github_auth_tasks))]
                    server.clients.clear()
                    await task

                self.assertTrue(task.done())
                self.assertTrue(store.deleted)
                self.assertEqual(api.user_calls, 0)

                reopened = FakeWebSocket()
                server.clients.add(reopened)
                server.client_compatibility[reopened] = {"state": "compatible", "canOperate": True}
                await server.handle_message(reopened, {
                    "type": "github_status_request",
                    "requestId": "status-after-reopen",
                })

                result = reopened.messages[-1]["result"]
                self.assertEqual(reopened.messages[-1]["type"], "github_status")
                self.assertEqual(result["state"], "authorization_failed")
                self.assertFalse(result["authenticated"])
                self.assertIsNone(result["account"])
                self.assertEqual(result["lastAuthorizationError"]["code"], "secure_store_failed")
                self.assertEqual(result["lastAuthorizationError"]["stage"], "keychain_readback")
                serialized = json.dumps(result)
                self.assertNotIn("oauth-secret-token", serialized)
                self.assertNotIn("device-secret", serialized)
                self.assertNotIn("deviceCode", serialized)

        asyncio.run(scenario())

    def test_initial_status_hides_active_authorization_until_handshake(self) -> None:
        server = LibrarianServer(
            enable_task_runner=False,
            github_service=ActiveAuthorizationGitHubService(),
        )
        socket = ScriptedWebSocket([
            {"type": "status_request"},
            {
                "type": "handshake",
                "client": "agent-wiki-extension",
                "product": "agent-wiki",
                "version": "0.3.1",
                "protocolVersion": 1,
            },
            {"type": "status_request"},
        ])

        asyncio.run(server.handle_client(socket))

        statuses = [item["status"]["github"] for item in socket.messages if item["type"] == "status_snapshot"]
        self.assertEqual(len(statuses), 3)
        self.assertNotIn("activeAuthorization", statuses[0])
        self.assertNotIn("activeAuthorization", statuses[1])
        self.assertEqual(statuses[2]["activeAuthorization"]["userCode"], "ABCD-EFGH")

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

    def test_path_only_config_is_never_reported_as_connected(self) -> None:
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

            self.assertFalse(status["ok"])
            self.assertEqual(status["state"], "selection_required")
            self.assertEqual(status["path"], "")
            self.assertEqual(status["reasons"], ["legacy_config_unverified"])

    def test_obsidian_internal_status_and_config_update_never_adopt_incoming_path(self) -> None:
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
                asyncio.run(server.handle_config_update({"vaultPath": str(root / "missing")}))

            self.assertEqual(status["state"], "selection_required")
            self.assertEqual(status["path"], "")
            self.assertIn(str(internal), config.read_text(encoding="utf-8"))
            self.assertNotIn(str(root / "missing"), config.read_text(encoding="utf-8"))

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
        self.assertEqual(socket.messages[-1]["operationId"], "github-import-batch-1")
        self.assertEqual(socket.messages[-1]["taskId"], "batch-1")
        self.assertEqual(socket.messages[-1]["parentId"], "")

    def test_import_runner_cancels_only_unstarted_items(self) -> None:
        service = BatchGitHubService(cancel_after_first=True)
        batch = self.make_batch(service)
        server = LibrarianServer(enable_task_runner=False, github_service=service)

        asyncio.run(server._run_github_import(batch["id"]))

        self.assertEqual(batch["state"], "cancelled")
        self.assertEqual(batch["completed"], 3)
        self.assertEqual(batch["succeeded"], 1)
        self.assertEqual(batch["cancelled"], 2)
        self.assertEqual(service.calls, 1)


if __name__ == "__main__":
    unittest.main()
