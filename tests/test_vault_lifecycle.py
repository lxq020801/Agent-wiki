#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import importlib
import json
import os
import shutil
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from install.vault_lifecycle import (
    VAULT_IDENTITY_FILENAME,
    VAULT_LIFECYCLE_CONTRACT,
    VAULT_LIFECYCLE_REQUEST_TYPES,
    VAULT_LIFECYCLE_RESPONSE_TYPE,
    VaultLifecycleManager,
    dispatch_vault_lifecycle,
    inspect_vault_identity,
)


class FakeSocket:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, payload: str) -> None:
        self.sent.append(payload)


class UUIDSequence:
    def __init__(self) -> None:
        self.value = 0

    def __call__(self) -> uuid.UUID:
        self.value += 1
        return uuid.UUID(int=self.value)


class VaultLifecycleTests(unittest.TestCase):
    def make_manager(
        self,
        root: Path,
        *,
        registered: list[Path] | None = None,
        obsidian_roots: list[Path] | None = None,
    ) -> VaultLifecycleManager:
        return VaultLifecycleManager(
            runtime_root=root / "runtime",
            config_path=root / "runtime" / "config.toml",
            registry_vault_provider=lambda: list(registered or []),
            obsidian_root_provider=lambda: list(obsidian_roots or []),
            uuid_factory=UUIDSequence(),
        )

    def assert_contract_fields(self, result: dict[str, object]) -> None:
        for field in VAULT_LIFECYCLE_CONTRACT["resultFields"]:
            self.assertIn(field, result)

    def test_contract_lists_all_ui_operations_and_one_response_type(self) -> None:
        self.assertEqual(VAULT_LIFECYCLE_RESPONSE_TYPE, "vault_lifecycle_status")
        self.assertEqual(
            VAULT_LIFECYCLE_REQUEST_TYPES,
            {
                "vault_scan",
                "vault_select_folder",
                "vault_select_confirm",
            },
        )
        self.assertEqual(VAULT_LIFECYCLE_CONTRACT["contractVersion"], 1)

    def test_scan_ignores_unmarked_legacy_vaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            obsidian_root = root / "Obsidian"
            legacy = obsidian_root / "Legacy"
            (legacy / ".obsidian").mkdir(parents=True)
            (legacy / ".obsidian" / "private-state.json").write_text("do-not-read", encoding="utf-8")
            manager = self.make_manager(
                root,
                registered=[legacy],
                obsidian_roots=[obsidian_root],
            )

            result = manager.scan()

            self.assert_contract_fields(result)
            self.assertEqual(result["state"], "selection_required")
            self.assertEqual(len(result["obsidianRoots"]), 1)
            root_candidate = result["obsidianRoots"][0]
            self.assertEqual(root_candidate["kind"], "obsidian_root")
            self.assertEqual(root_candidate["obsidianRoot"], str(obsidian_root.resolve()))
            self.assertEqual(result["vaultCandidates"], [])
            self.assertEqual(
                (legacy / ".obsidian" / "private-state.json").read_text(encoding="utf-8"),
                "do-not-read",
            )

    def test_first_scan_requires_folder_selection_when_no_root_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = self.make_manager(Path(tmp))

            result = dispatch_vault_lifecycle(manager, "vault_scan", {})

            self.assertEqual(result["state"], "selection_required")
            self.assertTrue(result["requiresUserAction"])
            self.assertEqual(result["obsidianRoots"], [])

    def test_first_scan_reconnects_only_one_valid_identity_vault(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            obsidian_root = root / "Obsidian"
            obsidian_root.mkdir()
            creator_runtime = root / "creator-runtime"
            creator = VaultLifecycleManager(
                runtime_root=creator_runtime,
                config_path=creator_runtime / "config.toml",
                registry_vault_provider=lambda: [],
                obsidian_root_provider=lambda: [],
                uuid_factory=UUIDSequence(),
            )
            vault = obsidian_root / "唯一知识库"
            vault.mkdir()
            created = creator.select_folder(vault_path=vault)
            vault = Path(created["activeVault"]["vaultPath"])
            runtime = root / "fresh-runtime"
            scanner = VaultLifecycleManager(
                runtime_root=runtime,
                config_path=runtime / "config.toml",
                registry_vault_provider=lambda: [vault],
                obsidian_root_provider=lambda: [obsidian_root],
                uuid_factory=UUIDSequence(),
            )

            result = scanner.scan()

            self.assertTrue(result["ok"])
            self.assertEqual(result["state"], "reconnected")
            self.assertEqual(result["activeVault"]["vaultPath"], str(vault.resolve()))
            self.assertIn(str(vault.resolve()), (runtime / "config.toml").read_text(encoding="utf-8"))

    def test_first_scan_does_not_choose_between_duplicate_valid_identities(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            obsidian_root = root / "Obsidian"
            obsidian_root.mkdir()
            creator = self.make_manager(root)
            original = obsidian_root / "Shared"
            original.mkdir()
            created = creator.select_folder(vault_path=original)
            original = Path(created["activeVault"]["vaultPath"])
            duplicate = obsidian_root / "Shared Copy"
            shutil.copytree(original, duplicate)
            runtime = root / "fresh-runtime"
            scanner = VaultLifecycleManager(
                runtime_root=runtime,
                config_path=runtime / "config.toml",
                registry_vault_provider=lambda: [original, duplicate],
                obsidian_root_provider=lambda: [obsidian_root],
                uuid_factory=UUIDSequence(),
            )

            result = scanner.scan()

            self.assertFalse(result["ok"])
            self.assertEqual(result["state"], "selection_required")
            self.assertEqual(len(result["vaultCandidates"]), 2)
            self.assertFalse((runtime / "config.toml").exists())

    def test_select_empty_folder_initializes_in_place_without_git(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            vault = root / "My Vault With Spaces"
            vault.mkdir()
            manager = self.make_manager(root)

            result = dispatch_vault_lifecycle(
                manager,
                "vault_select_folder",
                {"selectedPath": str(vault)},
            )

            self.assertTrue(result["ok"])
            self.assertEqual(result["state"], "initialized")
            self.assertEqual(result["activeVault"]["vaultPath"], str(vault.resolve()))
            self.assertTrue((vault / "index.md").is_file())
            self.assertTrue((vault / "raw").is_dir())
            self.assertTrue((vault / "知识资产").is_dir())
            self.assertFalse((vault / "知识资产" / "知识入库").exists())
            self.assertEqual(inspect_vault_identity(vault)[0], "valid")
            self.assertFalse((vault / ".git").exists())
            self.assertFalse((vault / ".obsidian").exists())

    def test_select_nonempty_folder_requires_confirmation_and_preserves_every_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            vault = root / "Existing Notes"
            vault.mkdir()
            note = vault / "personal.md"
            index = vault / "index.md"
            note.write_text("keep personal note", encoding="utf-8")
            index.write_text("# Existing index\n", encoding="utf-8")
            manager = self.make_manager(root)

            selected = manager.select_folder(vault_path=vault)

            self.assertEqual(selected["state"], "confirmation_required")
            self.assertTrue(selected["requiresUserAction"])
            self.assertEqual(note.read_text(encoding="utf-8"), "keep personal note")
            self.assertEqual(index.read_text(encoding="utf-8"), "# Existing index\n")
            self.assertFalse((vault / VAULT_IDENTITY_FILENAME).exists())
            self.assertFalse((root / "runtime" / "config.toml").exists())

            confirmed = manager.confirm_selection(
                selection_id=selected["selection"]["selectionId"],
            )

            self.assertTrue(confirmed["ok"])
            self.assertEqual(confirmed["state"], "initialized")
            self.assertEqual(note.read_text(encoding="utf-8"), "keep personal note")
            self.assertEqual(index.read_text(encoding="utf-8"), "# Existing index\n")
            self.assertFalse((vault / ".git").exists())
            self.assertEqual(inspect_vault_identity(vault)[0], "valid")

    def test_select_valid_identity_vault_activates_without_modifying_contents(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            obsidian_root = root / "Obsidian"
            obsidian_root.mkdir()
            first = self.make_manager(root)
            vault = obsidian_root / "Stable"
            vault.mkdir()
            created = first.select_folder(vault_path=vault)
            vault = Path(created["activeVault"]["vaultPath"])
            note = vault / "personal.md"
            note.write_text("unchanged", encoding="utf-8")
            second_runtime = root / "second-runtime"
            second = VaultLifecycleManager(
                runtime_root=second_runtime,
                config_path=second_runtime / "config.toml",
                registry_vault_provider=lambda: [],
                obsidian_root_provider=lambda: [],
                uuid_factory=UUIDSequence(),
            )

            selected = second.select_folder(vault_path=vault)

            self.assertEqual(selected["state"], "selected")
            self.assertEqual(note.read_text(encoding="utf-8"), "unchanged")
            self.assertEqual(
                selected["activeVault"]["vaultId"],
                created["activeVault"]["vaultId"],
            )

    def test_icloud_obsidian_container_is_filtered_but_documents_child_is_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            outer = root / "Library" / "Mobile Documents" / "iCloud~md~obsidian"
            documents = outer / "Documents"
            documents.mkdir(parents=True)
            manager = self.make_manager(root, obsidian_roots=[outer, documents])

            scanned = manager.scan()
            rejected = dispatch_vault_lifecycle(
                manager,
                "vault_select_folder",
                {"selectedPath": str(outer)},
            )

            self.assertEqual(
                [item["obsidianRoot"] for item in scanned["obsidianRoots"]],
                [str(documents.resolve())],
            )
            self.assertFalse(rejected["ok"])
            self.assertEqual(rejected["errorCode"], "selected_folder_invalid")
            self.assertFalse((outer / VAULT_IDENTITY_FILENAME).exists())

    def test_selected_vault_state_recovers_after_service_restart(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            vault = root / "Restart Vault"
            vault.mkdir()
            first = self.make_manager(root)
            selected = first.select_folder(vault_path=vault)

            restarted = self.make_manager(root)
            status = restarted.status()

            self.assertEqual(selected["state"], "initialized")
            self.assertTrue(status["ok"])
            self.assertEqual(status["state"], "ready")
            self.assertEqual(status["activeVault"]["vaultPath"], str(vault.resolve()))

    def test_identity_marker_symlink_is_rejected_without_following_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            vault = root / "vault"
            private = root / ".obsidian" / "private.json"
            vault.mkdir()
            private.parent.mkdir()
            private.write_text("not an identity", encoding="utf-8")
            (vault / VAULT_IDENTITY_FILENAME).symlink_to(private)

            identity_state, identity = inspect_vault_identity(vault)

            self.assertEqual(identity_state, "invalid")
            self.assertIsNone(identity)

    def test_moved_vault_reconnects_only_with_matching_name_and_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            obsidian_root = root / "Obsidian"
            obsidian_root.mkdir()
            manager = self.make_manager(root, obsidian_roots=[obsidian_root])
            old_path = obsidian_root / "Alice"
            old_path.mkdir()
            created = manager.select_folder(vault_path=old_path)
            old_path = Path(created["activeVault"]["vaultPath"])
            moved = obsidian_root / "Alice Moved"
            old_path.rename(moved)

            before = manager.status()
            result = manager.scan()

            self.assertEqual(before["state"], "disconnected")
            self.assertEqual(result["state"], "reconnected")
            self.assertEqual(result["activeVault"]["vaultPath"], str(moved.resolve()))
            config = (root / "runtime" / "config.toml").read_text(encoding="utf-8")
            self.assertIn(f'path = "{moved.resolve()}"', config)

    def test_bootstrap_first_use_never_auto_adopts_a_discovered_vault(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = root / "runtime"
            home = root / "home"
            home.mkdir()
            with mock.patch.dict(os.environ, {
                "AGENT_WIKI_HOME": str(runtime),
                "HOME": str(home),
            }):
                import install.bootstrap as bootstrap_module

                bootstrap_module = importlib.reload(bootstrap_module)
                result = bootstrap_module.CheckResult()
                bootstrap_module.ensure_config_template(result)
                with mock.patch.object(
                    bootstrap_module,
                    "discover_vault",
                    side_effect=AssertionError("first use must not discover an existing vault"),
                ):
                    bootstrap_module.check_vault(result)

            self.assertTrue(any(
                "普通 Obsidian 目录不会自动连接" in item
                for item in result.missing_user_actions
            ))
            self.assertIn('path = ""', (runtime / "config.toml").read_text(encoding="utf-8"))
            self.assertFalse((runtime / "vault-registry.json").exists())


class VaultLifecycleWebSocketTests(unittest.TestCase):
    def make_server(self, runtime: Path):
        from server import websocket_server

        server = websocket_server.LibrarianServer(
            enable_task_runner=False,
            github_service=object(),
        )
        server.runtime_root = runtime
        return websocket_server, server

    def common_result(self, operation: str) -> dict[str, object]:
        return {
            "contractVersion": 1,
            "ok": True,
            "operation": operation,
            "state": "ready",
            "requiresUserAction": False,
            "message": "ok",
            "activeVault": None,
            "obsidianRoots": [],
            "vaultCandidates": [],
            "selection": None,
        }

    def test_all_lifecycle_messages_use_one_gated_response_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            websocket_module, server = self.make_server(Path(tmp) / "runtime")
            socket = FakeSocket()
            server.clients.add(socket)
            server.client_compatibility[socket] = {"state": "compatible", "canOperate": True}
            manager = object()
            server.vault_lifecycle_manager = lambda: manager

            for message_type in sorted(VAULT_LIFECYCLE_REQUEST_TYPES):
                socket.sent.clear()
                data = {"sentinel": message_type}
                expected = self.common_result(message_type.removeprefix("vault_"))
                selected_path = str(Path(tmp) / "Folder With Spaces")
                picker_patch = mock.patch.object(
                    server,
                    "pick_vault_folder",
                    return_value={"ok": True, "state": "selected", "path": selected_path},
                )
                with picker_patch, mock.patch.object(
                        websocket_module,
                        "dispatch_vault_lifecycle",
                        return_value=expected,
                    ) as dispatch:
                    asyncio.run(server.handle_message(socket, {
                        "type": message_type,
                        "requestId": f"request-{message_type}",
                        "data": data,
                    }))

                expected_data = (
                    {"selectedPath": selected_path}
                    if message_type == "vault_select_folder"
                    else data
                )
                dispatch.assert_called_once_with(manager, message_type, expected_data)
                reply = json.loads(socket.sent[-1])
                self.assertEqual(reply["type"], "vault_lifecycle_status")
                self.assertEqual(reply["requestId"], f"request-{message_type}")
                self.assertEqual(reply["result"], expected)
                for field in VAULT_LIFECYCLE_CONTRACT["resultFields"]:
                    self.assertIn(field, reply["result"])

    def test_lifecycle_mutation_is_rejected_before_compatible_handshake(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            websocket_module, server = self.make_server(Path(tmp) / "runtime")
            socket = FakeSocket()
            server.clients.add(socket)
            server.client_compatibility[socket] = {
                "state": "handshake_required",
                "canOperate": False,
                "message": "handshake required",
            }
            with mock.patch.object(
                websocket_module,
                "dispatch_vault_lifecycle",
                side_effect=AssertionError("blocked lifecycle request must not dispatch"),
            ):
                asyncio.run(server.handle_message(socket, {
                    "type": "vault_select_confirm",
                    "requestId": "blocked",
                    "data": {
                        "selectionId": "selection-blocked",
                    },
                }))

            reply = json.loads(socket.sent[-1])
            self.assertEqual(reply["type"], "protocol_rejected")
            self.assertEqual(reply["reason"], "handshake_required")
            self.assertFalse((Path(tmp) / "runtime").exists())

    def test_folder_picker_cancel_is_chinese_and_has_no_side_effect(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = root / "runtime"
            websocket_module, server = self.make_server(runtime)
            socket = FakeSocket()
            server.clients.add(socket)
            server.client_compatibility[socket] = {"state": "compatible", "canOperate": True}
            with mock.patch.object(server, "pick_vault_folder", return_value={
                "ok": False,
                "state": "cancelled",
                "path": "",
                "message": "已取消选择，知识库保持不变。",
            }):
                asyncio.run(server.handle_message(socket, {
                    "type": "vault_select_folder",
                    "operationId": "op-vault-cancel",
                    "data": {"selectedPath": "/should/not/be/accepted"},
                }))

            reply = json.loads(socket.sent[-1])["result"]
            self.assertTrue(reply["ok"])
            self.assertEqual(reply["state"], "cancelled")
            self.assertIn("取消", reply["message"])
            self.assertFalse((runtime / "config.toml").exists())
            self.assertFalse((runtime / "vault-registry.json").exists())
            audit = server.audit_store.get("op-vault-cancel")
            self.assertIsNotNone(audit)
            self.assertEqual(audit["summary"]["state"], "cancelled")

    def test_native_folder_picker_success_cancel_and_error(self) -> None:
        from types import SimpleNamespace

        with tempfile.TemporaryDirectory() as tmp:
            selected = Path(tmp) / "Folder With Spaces"
            selected.mkdir()
            _module, server = self.make_server(Path(tmp) / "runtime")
            with mock.patch("server.websocket_server.sys.platform", "darwin"):
                with mock.patch("server.websocket_server.subprocess.run", return_value=SimpleNamespace(
                    returncode=0,
                    stdout=f"{selected}\n",
                    stderr="",
                )) as run:
                    success = server.pick_vault_folder()
                command = run.call_args.args[0]
                self.assertEqual(command[:2], ["/usr/bin/osascript", "-e"])
                self.assertNotIn(str(selected), command[2])

                with mock.patch("server.websocket_server.subprocess.run", return_value=SimpleNamespace(
                    returncode=0,
                    stdout="__AGENT_WIKI_FOLDER_SELECTION_CANCELLED__\n",
                    stderr="",
                )):
                    cancelled = server.pick_vault_folder()

                with mock.patch("server.websocket_server.subprocess.run", return_value=SimpleNamespace(
                    returncode=1,
                    stdout="",
                    stderr="mock error",
                )):
                    failed = server.pick_vault_folder()

            self.assertTrue(success["ok"])
            self.assertEqual(success["path"], str(selected.resolve()))
            self.assertEqual(cancelled["state"], "cancelled")
            self.assertIn("取消", cancelled["message"])
            self.assertEqual(failed["state"], "error")
            self.assertEqual(failed["errorCode"], "folder_picker_failed")

    def test_empty_config_update_and_legacy_discovery_never_adopt_a_vault(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = root / "runtime"
            websocket_module, server = self.make_server(runtime)
            manager = VaultLifecycleManager(
                runtime_root=runtime,
                registry_vault_provider=lambda: [],
                obsidian_root_provider=lambda: [],
                uuid_factory=UUIDSequence(),
            )
            server.vault_lifecycle_manager = lambda: manager

            legacy_vault = root / "legacy-config-vault"
            legacy_vault.mkdir()
            asyncio.run(server.handle_config_update({
                "llm": {"provider": "doubao"},
                "vaultPath": str(legacy_vault),
            }))
            config = (runtime / "config.toml").read_text(encoding="utf-8")
            self.assertIn('path = ""', config)
            legacy = server.discover_and_persist_vault("")
            self.assertEqual(legacy["state"], "lifecycle_required")
            self.assertIn('path = ""', (runtime / "config.toml").read_text(encoding="utf-8"))
            self.assertEqual(server.vault_status()["state"], "first_use")

            (runtime / "config.toml").write_text(
                f'[vault]\npath = "{legacy_vault}"\n',
                encoding="utf-8",
            )
            legacy_status = server.vault_status()
            self.assertFalse(legacy_status["ok"])
            self.assertEqual(legacy_status["state"], "selection_required")
            self.assertEqual(legacy_status["path"], "")
            self.assertEqual(legacy_status["reasons"], ["legacy_config_unverified"])


if __name__ == "__main__":
    unittest.main()
