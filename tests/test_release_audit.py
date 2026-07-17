#!/usr/bin/env python3
"""Tests for the read-only open-source release audit."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "release_audit.py"
SPEC = importlib.util.spec_from_file_location("release_audit", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("could not load release_audit module")
release_audit = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = release_audit
SPEC.loader.exec_module(release_audit)


def temp_git(root: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout.strip()


def write_manifest(root: Path, version: str) -> None:
    manifest = root / "chrome-extension" / "manifest.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps({"version": version}) + "\n", encoding="utf-8")


def commit_all(root: Path, message: str) -> None:
    temp_git(root, "add", "--all")
    temp_git(root, "commit", "--quiet", "-m", message)


def init_version_repo(root: Path, version: str) -> None:
    temp_git(root, "init", "--quiet")
    temp_git(root, "config", "user.name", "Release Audit Test")
    temp_git(root, "config", "user.email", "release-audit@example.invalid")
    (root / "LICENSE").write_text("Apache License\nVersion 2.0\n", encoding="utf-8")
    write_manifest(root, version)
    commit_all(root, f"version {version}")


class ReleaseAuditTests(unittest.TestCase):
    def test_secret_finding_does_not_include_secret_value(self) -> None:
        secret = "github_pat_" + ("A" * 30)
        findings = release_audit.scan_text("fixture.txt", f"token={secret}\n")

        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].path, "fixture.txt")
        self.assertNotIn(secret, findings[0].detail)

    def test_placeholder_assignment_is_allowed(self) -> None:
        text = 'api_key = "placeholder_for_local_setup"\n'
        self.assertEqual(release_audit.scan_text("example.toml", text), [])

    def test_requirement_inventory_is_parsed_from_both_manifests(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "deps" / "douyin").mkdir(parents=True)
            (root / "requirements.txt").write_text(
                'websockets>=12.0\ntomli>=2.0; python_version < "3.11"\n',
                encoding="utf-8",
            )
            (root / "deps" / "douyin" / "requirements.txt").write_text(
                "httpx==0.27.*\nimportlib_resources>=6.0\n",
                encoding="utf-8",
            )

            dependencies = release_audit.parse_requirements(root)

        self.assertEqual(
            {item.canonical_name for item in dependencies},
            {"websockets", "tomli", "httpx", "importlib-resources"},
        )

    def test_current_repository_passes_default_audit(self) -> None:
        result = release_audit.audit(ROOT)
        details = "\n".join(
            f"{item.check} {item.path}:{item.line or ''} {item.detail}"
            for item in result.findings
        )
        self.assertTrue(result.ok, details)

    def test_prerelease_head_may_advance_manifest_beyond_nearest_release(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            init_version_repo(root, "0.1.0")
            temp_git(root, "tag", "v0.1.0")
            write_manifest(root, "0.1.1")
            commit_all(root, "prepare 0.1.1")

            findings, nearest, exact = release_audit.check_license_and_version(root)

        self.assertEqual(findings, [])
        self.assertEqual(nearest, "v0.1.0")
        self.assertEqual(exact, ())

    def test_public_version_surfaces_must_match_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            init_version_repo(root, "0.4.0")
            (root / "README.md").write_text("当前版本为 **v9.9.9**\n", encoding="utf-8")
            (root / "server").mkdir()
            (root / "server" / "github_service.py").write_text(
                'USER_AGENT = "Agent-wiki/9.9.9"\n',
                encoding="utf-8",
            )
            (root / "docs").mkdir()
            (root / "docs" / "websocket-protocol.md").write_text(
                "当前产品版本：`9.9.9`\n",
                encoding="utf-8",
            )

            findings, _nearest, _exact = release_audit.check_license_and_version(root)

        self.assertEqual(
            {item.path for item in findings},
            {"README.md", "server/github_service.py", "docs/websocket-protocol.md"},
        )
        self.assertTrue(all(item.check == "version" for item in findings))

    def test_exact_head_release_tag_must_match_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            init_version_repo(root, "0.1.1")
            temp_git(root, "tag", "-a", "v0.1.0", "-m", "wrong release")

            findings, nearest, exact = release_audit.check_license_and_version(root)

        self.assertEqual(nearest, "v0.1.0")
        self.assertEqual(exact, ("v0.1.0",))
        self.assertEqual(len(findings), 1)
        self.assertIn("exact HEAD release tag(s) v0.1.0", findings[0].detail)

    def test_exact_head_release_tag_matching_manifest_passes(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            init_version_repo(root, "0.1.1")
            temp_git(root, "tag", "-a", "v0.1.1", "-m", "release 0.1.1")

            findings, nearest, exact = release_audit.check_license_and_version(root)

        self.assertEqual(findings, [])
        self.assertEqual(nearest, "v0.1.1")
        self.assertEqual(exact, ("v0.1.1",))

    def test_nearer_annotated_archive_tag_does_not_replace_release_tag(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            init_version_repo(root, "0.1.0")
            temp_git(root, "tag", "v0.1.0")
            (root / "archive-marker.txt").write_text("archive\n", encoding="utf-8")
            commit_all(root, "archive checkpoint")
            temp_git(
                root,
                "tag",
                "-a",
                "archive/pre-v0.1.1",
                "-m",
                "archive checkpoint",
            )
            write_manifest(root, "0.1.1")
            commit_all(root, "prepare 0.1.1")

            findings, nearest, exact = release_audit.check_license_and_version(root)

        self.assertEqual(findings, [])
        self.assertEqual(nearest, "v0.1.0")
        self.assertEqual(exact, ())

    def test_json_cli_is_machine_readable(self) -> None:
        proc = subprocess.run(
            [sys.executable, str(MODULE_PATH), "--json"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        payload = json.loads(proc.stdout)
        expected_nearest, expected_exact = release_audit.release_tag_state(ROOT)
        self.assertTrue(payload["ok"])
        self.assertFalse(payload["history_scanned"])
        self.assertGreater(payload["tracked_files"], 0)
        self.assertEqual(payload["nearest_release_tag"], expected_nearest)
        self.assertEqual(payload["exact_head_release_tags"], list(expected_exact))

    def test_ci_fetches_full_history_without_persisting_credentials(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

        self.assertIn("fetch-depth: 0", workflow)
        self.assertIn("persist-credentials: false", workflow)
        self.assertIn("python scripts/release_audit.py --history", workflow)


if __name__ == "__main__":
    unittest.main()
