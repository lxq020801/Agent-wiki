#!/usr/bin/env python3
from __future__ import annotations

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
                "vault_create",
                "vault_switch",
                "vault_candidate_confirm",
                "vault_migration_preview",
                "vault_migration_execute",
                "vault_migration_rollback",
            },
        )
        self.assertEqual(VAULT_LIFECYCLE_CONTRACT["contractVersion"], 1)

    def test_scan_distinguishes_roots_existing_vaults_and_identity_markers(self) -> None:
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

            result = manager.scan(user_name="Alice")

            self.assert_contract_fields(result)
            self.assertEqual(result["state"], "root_ready")
            self.assertEqual(len(result["obsidianRoots"]), 1)
            root_candidate = result["obsidianRoots"][0]
            self.assertEqual(root_candidate["kind"], "obsidian_root")
            self.assertEqual(root_candidate["obsidianRoot"], str(obsidian_root.resolve()))
            self.assertEqual(root_candidate["suggestedVaultPath"], str((obsidian_root / "Alice").resolve()))
            self.assertEqual(len(result["vaultCandidates"]), 1)
            vault_candidate = result["vaultCandidates"][0]
            self.assertEqual(vault_candidate["kind"], "existing_obsidian_vault")
            self.assertEqual(vault_candidate["identityState"], "missing")
            self.assertEqual(vault_candidate["supportedActions"], ["migrate"])
            self.assertEqual(
                (legacy / ".obsidian" / "private-state.json").read_text(encoding="utf-8"),
                "do-not-read",
            )

    def test_scan_requires_parent_selection_when_no_root_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            manager = self.make_manager(Path(tmp))

            result = dispatch_vault_lifecycle(manager, "vault_scan", {"userName": "Alice"})

            self.assertEqual(result["state"], "root_selection_required")
            self.assertTrue(result["requiresUserAction"])
            self.assertEqual(result["obsidianRoots"], [])

    def test_create_uses_root_user_name_and_only_blank_agent_wiki_structure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            obsidian_root = root / "Obsidian"
            obsidian_root.mkdir()
            manager = self.make_manager(root, obsidian_roots=[obsidian_root])

            result = dispatch_vault_lifecycle(manager, "vault_create", {
                "userName": " Alice ",
                "obsidianRoot": str(obsidian_root),
            })

            self.assert_contract_fields(result)
            self.assertTrue(result["ok"])
            self.assertEqual(result["state"], "created")
            vault = obsidian_root / "Alice"
            self.assertEqual(result["activeVault"]["vaultPath"], str(vault.resolve()))
            self.assertTrue((vault / "index.md").is_file())
            self.assertTrue((vault / "raw").is_dir())
            self.assertTrue((vault / "知识资产" / "知识入库").is_dir())
            self.assertTrue((vault / VAULT_IDENTITY_FILENAME).is_file())
            for forbidden in (".git", ".obsidian", "rules", "templates", "SCHEMA.md"):
                self.assertFalse((vault / forbidden).exists(), forbidden)

            identity_state, identity = inspect_vault_identity(vault)
            self.assertEqual(identity_state, "valid")
            self.assertEqual(identity["userName"], "Alice")
            registry = json.loads((root / "runtime" / "vault-registry.json").read_text(encoding="utf-8"))
            self.assertEqual(registry["activeVaultId"], identity["vaultId"])
            config = (root / "runtime" / "config.toml").read_text(encoding="utf-8")
            self.assertIn(f'path = "{vault.resolve()}"', config)

    def test_create_never_overwrites_an_existing_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            obsidian_root = root / "Obsidian"
            target = obsidian_root / "Alice"
            target.mkdir(parents=True)
            original = target / "personal.md"
            original.write_text("keep", encoding="utf-8")
            manager = self.make_manager(root)

            result = manager.create(user_name="Alice", obsidian_root=obsidian_root)

            self.assertEqual(result["state"], "target_conflict")
            self.assertEqual(original.read_text(encoding="utf-8"), "keep")
            self.assertFalse((target / VAULT_IDENTITY_FILENAME).exists())
            self.assertFalse((root / "runtime" / "config.toml").exists())

    def test_switch_requires_a_valid_identity_marker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old_vault = root / "old-vault"
            old_vault.mkdir()
            (old_vault / "note.md").write_text("legacy", encoding="utf-8")
            manager = self.make_manager(root)

            result = manager.switch(vault_path=old_vault)

            self.assertEqual(result["state"], "migration_required")
            self.assertFalse((root / "runtime" / "config.toml").exists())
            self.assertFalse((old_vault / VAULT_IDENTITY_FILENAME).exists())

    def test_moved_vault_reconnects_only_with_matching_name_and_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            obsidian_root = root / "Obsidian"
            obsidian_root.mkdir()
            manager = self.make_manager(root, obsidian_roots=[obsidian_root])
            created = manager.create(user_name="Alice", obsidian_root=obsidian_root)
            old_path = Path(created["activeVault"]["vaultPath"])
            moved = obsidian_root / "Alice Moved"
            old_path.rename(moved)

            before = manager.status()
            result = manager.scan(user_name="Alice")

            self.assertEqual(before["state"], "disconnected")
            self.assertEqual(result["state"], "reconnected")
            self.assertEqual(result["activeVault"]["vaultPath"], str(moved.resolve()))
            config = (root / "runtime" / "config.toml").read_text(encoding="utf-8")
            self.assertIn(f'path = "{moved.resolve()}"', config)

    def test_duplicate_identity_is_ambiguous_until_candidate_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            obsidian_root = root / "Obsidian"
            obsidian_root.mkdir()
            manager = self.make_manager(root, obsidian_roots=[obsidian_root])
            created = manager.create(user_name="Alice", obsidian_root=obsidian_root)
            original = Path(created["activeVault"]["vaultPath"])
            first = obsidian_root / "Alice Copy 1"
            second = obsidian_root / "Alice Copy 2"
            shutil.copytree(original, first)
            shutil.copytree(original, second)
            shutil.rmtree(original)

            result = manager.scan(user_name="Alice")

            self.assertEqual(result["state"], "ambiguous")
            exact = [item for item in result["vaultCandidates"] if item["matchState"] == "active_identity"]
            self.assertEqual(len(exact), 2)
            config_before = (root / "runtime" / "config.toml").read_text(encoding="utf-8")
            self.assertIn(str(original), config_before)

            confirmed = manager.confirm_candidate(
                candidate_id=exact[0]["candidateId"],
                action="switch",
            )
            self.assertEqual(confirmed["state"], "switched")
            self.assertEqual(confirmed["activeVault"]["vaultPath"], exact[0]["vaultPath"])

    def test_candidate_confirmation_can_create_under_a_scanned_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            obsidian_root = root / "Obsidian"
            obsidian_root.mkdir()
            manager = self.make_manager(root, obsidian_roots=[obsidian_root])
            scanned = manager.scan(user_name="Alice")

            created = manager.confirm_candidate(
                candidate_id=scanned["obsidianRoots"][0]["candidateId"],
                action="create",
                user_name="Alice",
            )

            self.assertEqual(created["state"], "created")
            self.assertEqual(created["activeVault"]["vaultPath"], str((obsidian_root / "Alice").resolve()))

    def test_migration_copies_then_validates_excludes_private_dirs_and_preserves_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            obsidian_root = root / "Obsidian"
            source = root / "Legacy Vault"
            obsidian_root.mkdir()
            (source / "notes").mkdir(parents=True)
            (source / "notes" / "one.md").write_text("knowledge", encoding="utf-8")
            (source / "templates").mkdir()
            (source / "templates" / "personal.md").write_text("user template", encoding="utf-8")
            (source / ".obsidian").mkdir()
            (source / ".obsidian" / "workspace.json").write_text("private", encoding="utf-8")
            (source / ".git").mkdir()
            (source / ".git" / "config").write_text("history", encoding="utf-8")
            (source / "nested" / ".obsidian").mkdir(parents=True)
            (source / "nested" / ".obsidian" / "state").write_text("private nested", encoding="utf-8")
            manager = self.make_manager(root)

            preview = manager.preview_migration(
                source_path=source,
                user_name="Alice",
                obsidian_root=obsidian_root,
            )

            self.assertTrue(preview["ok"])
            self.assertEqual(preview["state"], "migration_ready")
            migration = preview["migration"]
            self.assertTrue(migration["canExecute"])
            self.assertTrue(migration["sourcePreserved"])
            self.assertFalse((obsidian_root / "Alice").exists())
            self.assertEqual(manager.status()["state"], "first_use")

            completed = manager.execute_migration(migration_id=migration["migrationId"])

            self.assertEqual(completed["state"], "migrated")
            target = obsidian_root / "Alice"
            self.assertEqual((target / "notes" / "one.md").read_text(encoding="utf-8"), "knowledge")
            self.assertEqual((target / "templates" / "personal.md").read_text(encoding="utf-8"), "user template")
            self.assertFalse((target / ".obsidian").exists())
            self.assertFalse((target / ".git").exists())
            self.assertFalse((target / "nested" / ".obsidian").exists())
            self.assertTrue(source.exists())
            self.assertEqual((source / ".obsidian" / "workspace.json").read_text(encoding="utf-8"), "private")
            source_identity_state, _ = inspect_vault_identity(source)
            target_identity_state, target_identity = inspect_vault_identity(target)
            self.assertEqual(source_identity_state, "missing")
            self.assertEqual(target_identity_state, "valid")
            self.assertEqual(target_identity["userName"], "Alice")
            self.assertEqual(completed["activeVault"]["vaultPath"], str(target.resolve()))

            rolled_back = manager.rollback_migration(migration_id=migration["migrationId"])
            self.assertEqual(rolled_back["state"], "rolled_back")
            self.assertIsNone(rolled_back["activeVault"])
            self.assertTrue(source.exists())
            self.assertTrue(target.exists())
            self.assertIn('path = ""', (root / "runtime" / "config.toml").read_text(encoding="utf-8"))

    def test_migration_preview_reports_target_conflicts_without_copying_or_switching(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            obsidian_root = root / "Obsidian"
            source = root / "Legacy"
            target = obsidian_root / "Alice"
            source.mkdir()
            (source / "note.md").write_text("source", encoding="utf-8")
            target.mkdir(parents=True)
            (target / "note.md").write_text("target", encoding="utf-8")
            manager = self.make_manager(root)

            preview = manager.preview_migration(
                source_path=source,
                user_name="Alice",
                obsidian_root=obsidian_root,
            )

            self.assertFalse(preview["ok"])
            self.assertEqual(preview["state"], "migration_conflict")
            self.assertFalse(preview["migration"]["canExecute"])
            self.assertIn("target_exists", {item["code"] for item in preview["migration"]["conflicts"]})
            self.assertEqual((target / "note.md").read_text(encoding="utf-8"), "target")
            self.assertEqual(manager.status()["state"], "first_use")

    def test_migration_refuses_when_source_changes_after_preview(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            obsidian_root = root / "Obsidian"
            source = root / "Legacy"
            obsidian_root.mkdir()
            source.mkdir()
            note = source / "note.md"
            note.write_text("before", encoding="utf-8")
            manager = self.make_manager(root)
            preview = manager.preview_migration(
                source_path=source,
                user_name="Alice",
                obsidian_root=obsidian_root,
            )
            note.write_text("after", encoding="utf-8")

            completed = manager.execute_migration(
                migration_id=preview["migration"]["migrationId"],
            )

            self.assertEqual(completed["state"], "migration_stale")
            self.assertFalse((obsidian_root / "Alice").exists())
            self.assertEqual(manager.status()["state"], "first_use")

    def test_rollback_restores_a_previous_active_vault_by_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            obsidian_root = root / "Obsidian"
            obsidian_root.mkdir()
            manager = self.make_manager(root)
            current = manager.create(user_name="Current", obsidian_root=obsidian_root)
            source = root / "Legacy"
            source.mkdir()
            (source / "note.md").write_text("legacy", encoding="utf-8")
            preview = manager.preview_migration(
                source_path=source,
                user_name="Alice",
                obsidian_root=obsidian_root,
            )
            manager.execute_migration(migration_id=preview["migration"]["migrationId"])

            rolled_back = manager.rollback_migration(
                migration_id=preview["migration"]["migrationId"],
            )

            self.assertEqual(rolled_back["state"], "rolled_back")
            self.assertEqual(
                rolled_back["activeVault"]["vaultId"],
                current["activeVault"]["vaultId"],
            )
            self.assertEqual(manager.status()["state"], "ready")

    def test_explicit_isolation_initializes_only_an_empty_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            vault = root / "isolated-vault"
            vault.mkdir()
            manager = self.make_manager(root)

            initialized = manager.initialize_explicit_empty_vault(vault)

            self.assertEqual(initialized["state"], "created")
            self.assertFalse((vault / ".git").exists())
            self.assertFalse((vault / ".obsidian").exists())
            self.assertFalse((vault / "templates").exists())
            self.assertEqual(inspect_vault_identity(vault)[0], "valid")

            nonempty = root / "legacy"
            nonempty.mkdir()
            (nonempty / "note.md").write_text("keep", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "migration workflow"):
                manager.initialize_explicit_empty_vault(nonempty)
            self.assertFalse((nonempty / VAULT_IDENTITY_FILENAME).exists())

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
                "existing Obsidian vaults are never adopted automatically" in item
                for item in result.missing_user_actions
            ))
            self.assertIn('path = ""', (runtime / "config.toml").read_text(encoding="utf-8"))
            self.assertFalse((runtime / "vault-registry.json").exists())


if __name__ == "__main__":
    unittest.main()
