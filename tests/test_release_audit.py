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

    def test_json_cli_is_machine_readable(self) -> None:
        proc = subprocess.run(
            [sys.executable, str(MODULE_PATH), "--json"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        payload = json.loads(proc.stdout)
        self.assertTrue(payload["ok"])
        self.assertFalse(payload["history_scanned"])
        self.assertGreater(payload["tracked_files"], 0)


if __name__ == "__main__":
    unittest.main()
