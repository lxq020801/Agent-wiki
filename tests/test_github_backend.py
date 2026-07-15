#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from server.github_asset_pipeline import (
    GitHubAssetPipeline,
    GitHubAssetPipelineError,
    clean_readme,
    truncate_summary,
)
from server.github_service import GitHubService, GitHubServiceError
from server.github_tasks import GitHubTaskStore


def repository(repository_id: int = 101, full_name: str = "openai/example") -> dict:
    owner, name = full_name.split("/", 1)
    return {
        "id": repository_id,
        "name": name,
        "full_name": full_name,
        "owner": {"login": owner},
        "html_url": f"https://github.com/{full_name}",
        "description": "Agent workflow API with MCP and RAG support",
        "language": "Python",
        "stargazers_count": 120,
        "forks_count": 12,
        "open_issues_count": 4,
        "license": {"spdx_id": "Apache-2.0"},
        "archived": False,
        "private": False,
        "default_branch": "main",
        "pushed_at": "2026-07-14T00:00:00Z",
        "updated_at": "2026-07-14T00:00:00Z",
    }


def material(repo: dict | None = None, *, readme: str = "# Example\n\nUseful README.") -> dict:
    raw = repo or repository()
    license_info = raw.get("license") if isinstance(raw.get("license"), dict) else {}
    public = {
        "id": int(raw.get("id") or 0),
        "name": str(raw.get("name") or ""),
        "fullName": str(raw.get("full_name") or ""),
        "owner": str((raw.get("owner") or {}).get("login") or ""),
        "url": str(raw.get("html_url") or ""),
        "description": str(raw.get("description") or ""),
        "language": str(raw.get("language") or ""),
        "stars": int(raw.get("stargazers_count") or 0),
        "forks": int(raw.get("forks_count") or 0),
        "openIssues": int(raw.get("open_issues_count") or 0),
        "license": str(license_info.get("spdx_id") or ""),
        "archived": bool(raw.get("archived")),
        "private": bool(raw.get("private")),
        "defaultBranch": str(raw.get("default_branch") or ""),
        "pushedAt": str(raw.get("pushed_at") or ""),
        "updatedAt": str(raw.get("updated_at") or ""),
    }
    return {
        "repository": raw,
        "public": public,
        "readme": readme,
        "version": "v1.0.0",
        "snapshot": {
            "readmeSha256": "fake",
            "version": "v1.0.0",
            "license": public["license"],
            "archived": public["archived"],
            "pushedAt": public["pushedAt"],
            "defaultBranch": public["defaultBranch"],
            "fullName": public["fullName"],
        },
    }


class MemoryTokenStore:
    def __init__(self, token: str = "mock-token") -> None:
        self.token = token

    def get(self) -> str:
        return self.token

    def set(self, token: str) -> None:
        self.token = token

    def delete(self) -> None:
        self.token = ""


class MockGitHubAPI:
    def __init__(self) -> None:
        self.repo = repository()
        self.search_items = [self.repo]
        self.star_error: GitHubServiceError | None = None
        self.calls: list[tuple[str, str]] = []

    def request(self, method: str, path: str, *, token: str, **kwargs):
        self.calls.append((method, path))
        if path == "/search/repositories":
            return {"total_count": len(self.search_items), "items": list(self.search_items)}, {}
        if path.startswith("/repositories/") or (
            path.startswith("/repos/") and not path.endswith(("/readme", "/releases/latest"))
        ):
            return dict(self.repo), {}
        if path.endswith("/readme"):
            return "# Example\n\nMock README content.", {}
        if path.endswith("/releases/latest"):
            return {"tag_name": "v1.0.0"}, {}
        if method == "PUT" and path.startswith("/user/starred/"):
            if self.star_error:
                raise self.star_error
            return {}, {}
        if path == "/user":
            return {"login": "mock-user"}, {}
        raise AssertionError(f"unexpected mock GitHub API request: {method} {path}")


def mock_analysis(_material: dict, cleaned_readme: str, _intent: str) -> str:
    return (
        "## 简洁概括\n"
        "Agent Workflow Orchestration Framework provides APIs for MCP and RAG based automation workflows.\n\n"
        "## 完整内容整理\n"
        "来源将它描述为一个 API 驱动的智能体工作流项目。\n\n"
        f"{cleaned_readme}\n\n"
        "## AI 分析\n"
        "> 以下内容由 AI 生成，仅依据当前 GitHub 来源。\n\n"
        "基于当前来源，它可能适合需要 MCP 与 RAG 组合的自动化场景；是否适合生产环境仍需自行验证。"
    )


class GitHubAssetPipelineTests(unittest.TestCase):
    def test_readme_cleanup_asset_sections_tags_summary_and_index_are_unified(self) -> None:
        noisy = """# Example

[![build](https://img.shields.io/badge/build-pass-green)](https://example.com)

## Table of Contents

[Install](#install) | [Usage](#usage) | [API](#api)

## Overview

This framework coordinates agent API calls and MCP tools.

## Installation

Run the documented package installation command.

## Sponsors

Donate here and join our Discord chat.
"""
        captured: list[str] = []

        def analyze(source: dict, cleaned: str, intent: str) -> str:
            captured.append(cleaned)
            return mock_analysis(source, cleaned, intent)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            vault = root / "vault"
            vault.mkdir()
            (vault / "index.md").write_text(
                "# 知识库索引\n> 最后更新：2026-07-01 | 资产总数：1\n\n"
                "## GitHub项目\n- [[legacy-github|Legacy]] — old `#github`\n\n"
                "## GitHub项目 / 网页剪藏 / 代码模块\n"
                "- [[legacy-github|Legacy]] — duplicate `#github`\n",
                encoding="utf-8",
            )
            pipeline = GitHubAssetPipeline(config_path=root / "config.toml", analyzer=analyze)
            result = pipeline.write(material(readme=noisy), vault_path=vault, ingest_intent="manual")

            self.assertEqual(len(captured), 1)
            self.assertNotIn("shields.io", captured[0])
            self.assertNotIn("Table of Contents", captured[0])
            self.assertNotIn("Sponsors", captured[0])
            self.assertIn("Installation", captured[0])
            text = result.asset_path.read_text(encoding="utf-8")
            self.assertEqual(text.count("## 简洁概括"), 1)
            self.assertEqual(text.count("## 完整内容整理"), 1)
            self.assertEqual(text.count("## AI 分析"), 1)
            self.assertNotIn("## README 原文", text)
            self.assertIn("api-design", result.tags)
            self.assertIn("mcp", result.tags)
            self.assertIn("rag", result.tags)
            self.assertIn("github", result.tags)
            self.assertGreater(len(result.tags), 3)
            index = (vault / "index.md").read_text(encoding="utf-8")
            self.assertEqual(index.count("## GitHub项目 / 网页剪藏 / 代码模块"), 1)
            self.assertNotIn("\n## GitHub项目\n", index)
            self.assertEqual(index.count("[[legacy-github|"), 1)

    def test_summary_does_not_split_english_word(self) -> None:
        source = "A practical orchestration framework for extraordinarilylongrepositoryword and agent workflows"
        summary = truncate_summary(source, limit=64)
        self.assertLessEqual(len(summary), 64)
        self.assertNotIn("extraordinar...", summary)
        self.assertTrue(summary.endswith("..."))

    def test_pipeline_rejects_missing_source_analysis_boundaries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            vault = root / "vault"
            vault.mkdir()
            pipeline = GitHubAssetPipeline(
                config_path=root / "config.toml",
                analyzer=lambda *_args: "## 项目介绍\n只有一段",
            )
            with self.assertRaisesRegex(GitHubAssetPipelineError, "简洁概括"):
                pipeline.write(material(), vault_path=vault, ingest_intent="manual")

    def test_clean_readme_uses_semantic_budget_instead_of_line_slice(self) -> None:
        sections = [
            f"## Changelog {index}\n\n" + ("historical note " * 80)
            for index in range(30)
        ]
        sections.append("## API\n\nImportant API contract and endpoint behavior.")
        cleaned = clean_readme("\n\n".join(sections), limit=2_000)
        self.assertIn("Important API contract", cleaned)
        self.assertLessEqual(len(cleaned), 2_000)


class GitHubTaskStoreTests(unittest.TestCase):
    def test_batch_is_idempotent_and_recovers_running_parent_and_child(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "tasks"
            store = GitHubTaskStore(root)
            selected = [
                {"id": 1, "fullName": "openai/one"},
                {"id": 2, "fullName": "openai/two"},
            ]
            batch, created = store.create_batch(selected, request_key="request-1")
            self.assertTrue(created)
            same, same_created = store.create_batch(list(reversed(selected)), request_key="request-2")
            self.assertFalse(same_created)
            self.assertEqual(same["id"], batch["id"])
            renamed, renamed_created = store.create_batch([
                {"id": 1, "fullName": "openai/one-renamed"},
                {"id": 2, "fullName": "openai/two"},
            ])
            self.assertFalse(renamed_created)
            self.assertEqual(renamed["id"], batch["id"])
            store.begin_batch(batch["id"])
            first = store.queued_items(batch["id"])[0]
            store.begin_item(batch["id"], first["taskId"])

            recovered = GitHubTaskStore(root)
            snapshot = recovered.get_batch(batch["id"])
            self.assertEqual(snapshot["state"], "queued")
            self.assertEqual(snapshot["items"][0]["state"], "queued")
            self.assertEqual(recovered.pending_batch_ids(), [batch["id"]])

    def test_cancel_persists_terminal_child_states(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "tasks"
            store = GitHubTaskStore(root)
            batch, _ = store.create_batch([
                {"id": 1, "fullName": "openai/one"},
                {"id": 2, "fullName": "openai/two"},
            ])
            store.begin_batch(batch["id"])
            first = store.queued_items(batch["id"])[0]
            store.begin_item(batch["id"], first["taskId"])
            cancelled = store.cancel_batch(batch["id"])
            self.assertEqual(cancelled["state"], "running")
            self.assertEqual(cancelled["cancelled"], 1)
            restored = GitHubTaskStore(root).get_batch(batch["id"])
            self.assertEqual(restored["state"], "cancelled")
            self.assertEqual({item["state"] for item in restored["items"]}, {"cancelled"})

    def test_asset_event_is_idempotent_across_store_instances(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "tasks"
            first_store = GitHubTaskStore(root)
            second_store = GitHubTaskStore(root)
            event, created = first_store.ensure_asset_event(
                {"id": 101, "fullName": "openai/example"},
                "知识资产/GitHub项目/example.md",
                source="manual",
                auto_star_enabled=True,
            )
            duplicate, duplicate_created = second_store.ensure_asset_event(
                {"id": 101, "fullName": "openai/example-renamed"},
                "知识资产/GitHub项目/example.md",
                source="derived_ingest",
                auto_star_enabled=True,
            )
            self.assertTrue(created)
            self.assertFalse(duplicate_created)
            self.assertEqual(duplicate["id"], event["id"])

    def test_batch_persists_all_terminal_item_states(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "tasks"
            store = GitHubTaskStore(root)
            batch, _ = store.create_batch([
                {"id": index, "fullName": f"openai/repo-{index}"}
                for index in range(1, 5)
            ])
            store.begin_batch(batch["id"])
            queued = store.queued_items(batch["id"])
            store.begin_item(batch["id"], queued[0]["taskId"])
            store.complete_item(batch["id"], queued[0]["taskId"], {
                "ok": True,
                "state": "created",
                "repository": queued[0]["repository"],
                "assetPath": "created.md",
            })
            store.begin_item(batch["id"], queued[1]["taskId"])
            store.complete_item(batch["id"], queued[1]["taskId"], {
                "ok": True,
                "state": "existing",
                "repository": queued[1]["repository"],
                "assetPath": "existing.md",
            })
            store.begin_item(batch["id"], queued[2]["taskId"])
            store.fail_item(
                batch["id"],
                queued[2]["taskId"],
                code="not_found",
                message="mock missing",
                repository=queued[2]["repository"],
            )
            final = store.cancel_batch(batch["id"])
            self.assertEqual(final["state"], "cancelled")
            self.assertEqual(final["completed"], 4)
            self.assertEqual(final["succeeded"], 1)
            self.assertEqual(final["existing"], 1)
            self.assertEqual(final["failed"], 1)
            self.assertEqual(final["cancelled"], 1)
            restored = GitHubTaskStore(root).get_batch(batch["id"])
            self.assertEqual(
                {item["state"] for item in restored["items"]},
                {"succeeded", "existing", "failed", "cancelled"},
            )


class GitHubBackendServiceTests(unittest.TestCase):
    def make_service(self, root: Path, api: MockGitHubAPI | None = None) -> tuple[GitHubService, MockGitHubAPI, Path]:
        vault = root / "vault"
        vault.mkdir(exist_ok=True)
        config = root / "config.toml"
        config.write_text(f'[vault]\npath = "{vault}"\n', encoding="utf-8")
        mock_api = api or MockGitHubAPI()
        pipeline = GitHubAssetPipeline(config_path=config, analyzer=mock_analysis)
        service = GitHubService(
            runtime_root=root / "runtime",
            config_path=config,
            client_id="Iv1MockClient",
            token_store=MemoryTokenStore(),
            api=mock_api,
            asset_pipeline=pipeline,
        )
        return service, mock_api, vault

    def test_official_api_name_resolution_requires_one_exact_match(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service, api, _vault = self.make_service(Path(tmp))
            api.search_items = [repository(11, "one/toolx"), repository(22, "two/other")]
            api.repo = api.search_items[0]
            resolved = service._fetch_repository({"name": "ToolX"})
            self.assertEqual(resolved["id"], 11)
            self.assertIn(("GET", "/search/repositories"), api.calls)

            api.search_items = [repository(11, "one/tool-x"), repository(22, "two/toolx")]
            with self.assertRaises(GitHubServiceError) as caught:
                service._fetch_repository({"name": "ToolX"})
            self.assertEqual(caught.exception.code, "repository_ambiguous")
            self.assertTrue(caught.exception.details["confirmationRequired"])
            self.assertEqual(len(caught.exception.details["candidates"]), 2)

    def test_registry_compacts_duplicate_ids_without_merging_different_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service, _api, _vault = self.make_service(Path(tmp))
            service.registry_path.parent.mkdir(parents=True)
            service.registry_path.write_text(json.dumps({
                "version": 1,
                "repositories": [
                    {"repositoryId": 101, "fullName": "old/name", "assetPath": "old.md"},
                    {"repositoryId": 101, "fullName": "new/name", "assetPath": "new.md"},
                    {"repositoryId": 202, "fullName": "new/name", "assetPath": "other.md"},
                ],
            }), encoding="utf-8")
            records = service._registry()["repositories"]
            self.assertEqual(len(records), 2)
            by_id = {item["repositoryId"]: item for item in records}
            self.assertEqual(by_id[101]["fullName"], "new/name")
            self.assertEqual(by_id[202]["assetPath"], "other.md")

    def test_auto_star_failure_is_nonblocking_visible_persistent_and_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            api = MockGitHubAPI()
            api.star_error = GitHubServiceError("github_api_error", "mock permission denied")
            service, _api, vault = self.make_service(root, api)
            self.assertFalse(service.settings()["autoStar"])
            service.update_settings(auto_star=True)
            created = service.ingest_repository({"id": 101, "fullName": "openai/example"})
            self.assertTrue(created["ok"])
            self.assertTrue((vault / created["assetPath"]).exists())
            self.assertFalse(created["autoStar"]["ok"])
            self.assertEqual(api.calls.count(("PUT", "/user/starred/openai/example")), 1)

            reopened, _api, _vault = self.make_service(root, api)
            status = reopened.status(validate=False)
            event = status["recentTasks"][0]
            self.assertEqual(event["kind"], "github_asset_created")
            self.assertEqual(event["state"], "succeeded")
            self.assertEqual(event["autoStar"]["state"], "failed")
            existing = reopened.ingest_repository({"id": 101, "fullName": "openai/example"})
            self.assertEqual(existing["state"], "existing")
            self.assertEqual(api.calls.count(("PUT", "/user/starred/openai/example")), 1)

    def test_pending_post_create_star_resumes_with_idempotent_put(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            service, api, _vault = self.make_service(root)
            event, created = service.task_store.ensure_asset_event(
                {"id": 101, "fullName": "openai/example"},
                "知识资产/GitHub项目/example.md",
                source="manual",
                auto_star_enabled=True,
            )
            self.assertTrue(created)
            resumed = service.resume_pending_asset_events()
            self.assertEqual(len(resumed), 1)
            self.assertEqual(resumed[0]["id"], event["id"])
            self.assertEqual(resumed[0]["autoStar"]["state"], "succeeded")
            self.assertEqual(api.calls.count(("PUT", "/user/starred/openai/example")), 1)
            self.assertEqual(service.resume_pending_asset_events(), [])


if __name__ == "__main__":
    unittest.main()
