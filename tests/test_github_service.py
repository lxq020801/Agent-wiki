#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from server.github_service import (
    DEFAULT_CLIENT_ID,
    GITHUB_ACCESS_TOKEN_URL,
    GITHUB_DEVICE_CODE_URL,
    GitHubAPI,
    GitHubService,
    GitHubServiceError,
    HTTPResponse,
    MacOSKeychainTokenStore,
    register_derived_repository,
)


class MemoryTokenStore:
    def __init__(self, token: str = "") -> None:
        self.token = token
        self.deleted = False
        self.get_calls = 0

    def get(self) -> str:
        self.get_calls += 1
        return self.token

    def set(self, token: str) -> None:
        self.token = token

    def delete(self) -> None:
        self.deleted = True
        self.token = ""


def repository(*, repository_id: int = 101, full_name: str = "openai/example", archived: bool = False) -> dict:
    owner, name = full_name.split("/", 1)
    return {
        "id": repository_id,
        "name": name,
        "full_name": full_name,
        "owner": {"login": owner},
        "html_url": f"https://github.com/{full_name}",
        "description": "Example repository",
        "language": "Python",
        "stargazers_count": 42,
        "forks_count": 7,
        "open_issues_count": 3,
        "license": {"spdx_id": "Apache-2.0"},
        "archived": archived,
        "private": False,
        "default_branch": "main",
        "pushed_at": "2026-07-14T00:00:00Z",
        "updated_at": "2026-07-14T00:00:00Z",
    }


class FakeAPI:
    def __init__(self, repo: dict | None = None) -> None:
        self.repo = repo or repository()
        self.readme = "# Example\n\nCurrent README."
        self.version = "v1.0.0"
        self.calls: list[tuple[str, str]] = []
        self.form_responses: list[dict] = []
        self.star_error: GitHubServiceError | None = None

    def form_post(self, url: str, values: dict) -> dict:
        self.calls.append(("POST", url))
        if self.form_responses:
            return self.form_responses.pop(0)
        raise AssertionError(f"unexpected form request: {url}")

    def request(self, method: str, path: str, *, token: str, **kwargs):
        self.calls.append((method, path))
        if path == "/user":
            return {"login": "octocat", "name": "The Octocat"}, {}
        if path.startswith("/repositories/") or path.startswith("/repos/") and not path.endswith(("/readme", "/releases/latest")):
            return dict(self.repo), {}
        if path.endswith("/readme"):
            return self.readme, {}
        if path.endswith("/releases/latest"):
            return {"tag_name": self.version}, {}
        if path == "/search/repositories":
            return {"total_count": 1, "items": [dict(self.repo)]}, {}
        if path == "/user/starred":
            return [dict(self.repo)], {"link": '<https://api.github.com/user/starred?page=2>; rel="next"'}
        if method == "PUT" and path.startswith("/user/starred/"):
            if self.star_error:
                raise self.star_error
            return {}, {}
        raise AssertionError(f"unexpected API request: {method} {path}")


class BlockingAuthorizationAPI(FakeAPI):
    def __init__(self) -> None:
        super().__init__()
        self.token_request_started = threading.Event()
        self.release_token_response = threading.Event()

    def form_post(self, url: str, values: dict) -> dict:
        self.calls.append(("POST", url))
        if url == GITHUB_DEVICE_CODE_URL:
            return {
                "device_code": "device-secret",
                "user_code": "ABCD-EFGH",
                "verification_uri": "https://github.com/login/device",
                "expires_in": 900,
                "interval": 5,
            }
        if url == GITHUB_ACCESS_TOKEN_URL:
            self.token_request_started.set()
            if not self.release_token_response.wait(timeout=2):
                raise AssertionError("token response was not released")
            return {"access_token": "oauth-secret-token", "token_type": "bearer"}
        raise AssertionError(f"unexpected form request: {url}")


class BlockingDeviceCodeAPI(FakeAPI):
    def __init__(self) -> None:
        super().__init__()
        self.device_request_started = threading.Event()
        self.second_device_request_started = threading.Event()
        self.release_device_response = threading.Event()
        self._device_request_count = 0
        self._count_lock = threading.Lock()

    def form_post(self, url: str, values: dict) -> dict:
        if url != GITHUB_DEVICE_CODE_URL:
            return super().form_post(url, values)
        with self._count_lock:
            self._device_request_count += 1
            request_number = self._device_request_count
        self.calls.append(("POST", url))
        if request_number > 1:
            self.second_device_request_started.set()
            return {
                "device_code": "second-device-secret",
                "user_code": "IJKL-MNOP",
                "verification_uri": "https://github.com/login/device",
                "expires_in": 900,
                "interval": 5,
            }
        self.device_request_started.set()
        if not self.release_device_response.wait(timeout=2):
            raise AssertionError("device-code response was not released")
        return {
            "device_code": "device-secret",
            "user_code": "ABCD-EFGH",
            "verification_uri": "https://github.com/login/device",
            "expires_in": 900,
            "interval": 5,
        }


class BlockingAccountVerificationAPI(FakeAPI):
    def __init__(self) -> None:
        super().__init__()
        self.account_request_started = threading.Event()
        self.release_account_response = threading.Event()
        self.form_responses = [
            {
                "device_code": "device-secret",
                "user_code": "ABCD-EFGH",
                "verification_uri": "https://github.com/login/device",
                "expires_in": 900,
                "interval": 5,
            },
            {"access_token": "oauth-secret-token", "token_type": "bearer"},
        ]

    def request(self, method: str, path: str, *, token: str, **kwargs):
        if path == "/user":
            self.calls.append((method, path))
            self.account_request_started.set()
            if not self.release_account_response.wait(timeout=2):
                raise AssertionError("account response was not released")
            return {"login": "octocat", "name": "The Octocat"}, {}
        return super().request(method, path, token=token, **kwargs)


class TransientAuthorizationAPI(FakeAPI):
    def __init__(self) -> None:
        super().__init__()
        self.token_attempts = 0

    def form_post(self, url: str, values: dict) -> dict:
        self.calls.append(("POST", url))
        if url == GITHUB_DEVICE_CODE_URL:
            return {
                "device_code": "device-secret",
                "user_code": "ABCD-EFGH",
                "verification_uri": "https://github.com/login/device",
                "expires_in": 900,
                "interval": 5,
            }
        if url == GITHUB_ACCESS_TOKEN_URL:
            self.token_attempts += 1
            if self.token_attempts == 1:
                raise GitHubServiceError("network_error", "temporary network error")
            return {"access_token": "oauth-secret-token", "token_type": "bearer"}
        raise AssertionError(f"unexpected form request: {url}")


class QueueTransport:
    def __init__(self, responses: list[HTTPResponse]) -> None:
        self.responses = list(responses)

    def request(self, *args, **kwargs) -> HTTPResponse:
        return self.responses.pop(0)


class GitHubServiceTests(unittest.TestCase):
    def make_service(self, root: Path, *, api: FakeAPI | None = None, token: str = "token-test"):
        vault = root / "vault"
        vault.mkdir()
        config = root / "config.toml"
        config.write_text(f'[vault]\npath = "{vault}"\n', encoding="utf-8")
        store = MemoryTokenStore(token)
        service = GitHubService(
            runtime_root=root / "runtime",
            config_path=config,
            client_id="Iv1Example123",
            token_store=store,
            api=api or FakeAPI(),
        )
        return service, store, vault

    def test_official_client_id_is_available_without_local_setup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            os.environ,
            {"AGENT_WIKI_GITHUB_CLIENT_ID": ""},
        ):
            root = Path(tmp)
            service = GitHubService(
                runtime_root=root / "runtime",
                config_path=root / "missing-config.toml",
                token_store=MemoryTokenStore(),
                api=FakeAPI(),
            )

            self.assertEqual(service.client_id(), DEFAULT_CLIENT_ID)
            self.assertEqual(service.configuration_status()["source"], "official_default")

    def test_device_flow_keeps_token_out_of_responses_and_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            api = FakeAPI()
            api.form_responses = [
                {
                    "device_code": "device-secret",
                    "user_code": "ABCD-EFGH",
                    "verification_uri": "https://github.com/login/device",
                    "expires_in": 900,
                    "interval": 5,
                },
                {"access_token": "oauth-secret-token", "token_type": "bearer"},
            ]
            service, store, _vault = self.make_service(root, api=api, token="")
            started = service.start_authorization()
            self.assertNotIn("deviceCode", started)
            self.assertNotIn("device-secret", json.dumps(started))
            completed = service.poll_authorization(started["flowId"])
            self.assertTrue(completed["authenticated"])
            self.assertNotIn("access_token", completed)
            self.assertNotIn("oauth-secret-token", json.dumps(completed))
            self.assertEqual(store.token, "oauth-secret-token")
            self.assertIsNone(service.active_authorization())
            for path in (root / "runtime").glob("**/*"):
                if path.is_file():
                    self.assertNotIn("oauth-secret-token", path.read_text(encoding="utf-8", errors="ignore"))

    def test_device_flow_is_restored_without_requesting_a_second_code(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            api = FakeAPI()
            api.form_responses = [{
                "device_code": "device-secret",
                "user_code": "ABCD-EFGH",
                "verification_uri": "https://github.com/login/device",
                "expires_in": 900,
                "interval": 5,
            }]
            service, _store, _vault = self.make_service(Path(tmp), api=api, token="")

            started = service.start_authorization()
            reopened = service.start_authorization()
            status = service.status(validate=True)

            self.assertEqual(reopened, started)
            self.assertEqual(status["activeAuthorization"], started)
            self.assertEqual(
                api.calls.count(("POST", GITHUB_DEVICE_CODE_URL)),
                1,
            )
            serialized = json.dumps(status)
            self.assertNotIn("device-secret", serialized)
            self.assertNotIn("deviceCode", serialized)

    def test_concurrent_login_requests_reuse_one_device_code(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            api = BlockingDeviceCodeAPI()
            service, _store, _vault = self.make_service(Path(tmp), api=api, token="")
            second_call_started = threading.Event()

            with ThreadPoolExecutor(max_workers=2) as pool:
                first = pool.submit(service.start_authorization)
                self.assertTrue(api.device_request_started.wait(timeout=1))

                def start_second_authorization():
                    second_call_started.set()
                    return service.start_authorization()

                second = pool.submit(start_second_authorization)
                self.assertTrue(second_call_started.wait(timeout=1))
                self.assertFalse(api.second_device_request_started.wait(timeout=0.1))
                api.release_device_response.set()
                results = [first.result(timeout=2), second.result(timeout=2)]

            self.assertEqual(results[0], results[1])
            self.assertEqual(api.calls.count(("POST", GITHUB_DEVICE_CODE_URL)), 1)

    def test_cancel_during_token_exchange_never_saves_token(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            api = BlockingAuthorizationAPI()
            service, store, _vault = self.make_service(Path(tmp), api=api, token="")
            started = service.start_authorization()

            with ThreadPoolExecutor(max_workers=1) as pool:
                polling = pool.submit(service.poll_authorization, started["flowId"])
                self.assertTrue(api.token_request_started.wait(timeout=1))
                cancelled = service.cancel_authorization(started["flowId"])
                api.release_token_response.set()
                with self.assertRaises(GitHubServiceError) as caught:
                    polling.result(timeout=2)

            self.assertEqual(cancelled["state"], "cancelled")
            self.assertEqual(caught.exception.code, "authorization_missing")
            self.assertEqual(store.token, "")
            self.assertFalse(service.status(validate=True)["authenticated"])

    def test_cancel_while_account_verification_is_running_removes_token(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            api = BlockingAccountVerificationAPI()
            service, store, _vault = self.make_service(Path(tmp), api=api, token="")
            started = service.start_authorization()
            cancel_started = threading.Event()

            with ThreadPoolExecutor(max_workers=2) as pool:
                polling = pool.submit(service.poll_authorization, started["flowId"])
                self.assertTrue(api.account_request_started.wait(timeout=1))
                self.assertEqual(store.token, "oauth-secret-token")

                def cancel_authorization():
                    cancel_started.set()
                    return service.cancel_authorization(started["flowId"])

                cancelling = pool.submit(cancel_authorization)
                self.assertTrue(cancel_started.wait(timeout=1))
                api.release_account_response.set()
                self.assertEqual(polling.result(timeout=2)["state"], "ready")
                self.assertEqual(cancelling.result(timeout=2)["state"], "cancelled")

            self.assertEqual(store.token, "")
            self.assertFalse(service.status(validate=True)["authenticated"])

    def test_transient_token_error_resets_polling_and_can_recover(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            now = [1000.0]
            api = TransientAuthorizationAPI()
            store = MemoryTokenStore()
            service = GitHubService(
                runtime_root=root / "runtime",
                config_path=root / "missing-config.toml",
                client_id="Iv1Example123",
                token_store=store,
                api=api,
                clock=lambda: now[0],
            )
            started = service.start_authorization()

            with self.assertRaises(GitHubServiceError) as caught:
                service.poll_authorization(started["flowId"])
            self.assertEqual(caught.exception.code, "network_error")
            self.assertFalse(service.pending_flows[started["flowId"]]["polling"])

            now[0] += 5
            completed = service.poll_authorization(started["flowId"])

            self.assertEqual(completed["state"], "ready")
            self.assertEqual(api.token_attempts, 2)
            self.assertEqual(store.token, "oauth-secret-token")

    def test_passive_status_does_not_access_keychain(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service, store, _vault = self.make_service(Path(tmp))
            passive = service.status(validate=False)
            self.assertEqual(store.get_calls, 0)
            self.assertFalse(passive["authenticated"])
            active = service.status(validate=True)
            self.assertEqual(store.get_calls, 1)
            self.assertTrue(active["authenticated"])

    def test_device_flow_denied_is_explicit_and_clears_pending_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            api = FakeAPI()
            api.form_responses = [
                {
                    "device_code": "device-secret",
                    "user_code": "ABCD-EFGH",
                    "verification_uri": "https://github.com/login/device",
                    "expires_in": 900,
                    "interval": 5,
                },
                {"error": "access_denied"},
            ]
            service, _store, _vault = self.make_service(Path(tmp), api=api, token="")
            started = service.start_authorization()
            with self.assertRaisesRegex(GitHubServiceError, "拒绝") as caught:
                service.poll_authorization(started["flowId"])
            self.assertEqual(caught.exception.code, "authorization_denied")
            self.assertFalse(service.pending_flows)

    def test_device_flow_timeout_is_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            vault = root / "vault"
            vault.mkdir()
            config = root / "config.toml"
            config.write_text(f'[vault]\npath = "{vault}"\n', encoding="utf-8")
            api = FakeAPI()
            api.form_responses = [{
                "device_code": "device-secret",
                "user_code": "ABCD-EFGH",
                "verification_uri": "https://github.com/login/device",
                "expires_in": 1,
                "interval": 5,
            }]
            now = [1000.0]
            service = GitHubService(
                runtime_root=root / "runtime",
                config_path=config,
                client_id="Iv1Example123",
                token_store=MemoryTokenStore(),
                api=api,
                clock=lambda: now[0],
            )
            started = service.start_authorization()
            now[0] = 1002.0
            with self.assertRaises(GitHubServiceError) as caught:
                service.poll_authorization(started["flowId"])
            self.assertEqual(caught.exception.code, "authorization_expired")
            self.assertFalse(service.pending_flows)

    def test_search_and_stars_are_paginated_and_annotated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service, _store, _vault = self.make_service(Path(tmp))
            search = service.search_repositories("example", page=2, per_page=10)
            self.assertEqual(search["page"], 2)
            self.assertFalse(search["repositories"][0]["ingested"])
            stars = service.starred_repositories(page=1, per_page=25)
            self.assertTrue(stars["hasNext"])
            self.assertEqual(stars["repositories"][0]["id"], 101)

    def test_rate_limit_response_exposes_retry_without_credentials(self) -> None:
        response = HTTPResponse(
            403,
            {"x-ratelimit-remaining": "0", "retry-after": "17"},
            b'{"message":"API rate limit exceeded"}',
        )
        api = GitHubAPI(QueueTransport([response]))
        with self.assertRaises(GitHubServiceError) as caught:
            api.request("GET", "/user", token="secret-token")
        self.assertEqual(caught.exception.code, "rate_limited")
        self.assertEqual(caught.exception.retry_after, 17)
        self.assertNotIn("secret-token", json.dumps(caught.exception.public_payload()))

    def test_repository_id_dedupes_rename_and_refresh_requires_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            api = FakeAPI()
            service, _store, vault = self.make_service(root, api=api)
            first = service.ingest_repository({"id": 101, "fullName": "openai/example"})
            self.assertEqual(first["state"], "created")
            self.assertFalse((vault / ".git").exists())
            asset = vault / first["assetPath"]
            original = asset.read_text(encoding="utf-8")
            unchanged = service.check_refresh({"id": 101, "fullName": "openai/example"})
            self.assertEqual(unchanged["state"], "no_changes")

            api.repo = repository(repository_id=101, full_name="openai/renamed")
            api.readme = "# Renamed\n\nNew README."
            api.version = "v2.0.0"
            duplicate = service.ingest_repository({"id": 101, "fullName": "openai/renamed"})
            self.assertEqual(duplicate["state"], "existing")
            self.assertTrue(duplicate["deduplicated"])
            self.assertTrue(duplicate["refreshAvailable"])
            self.assertEqual(asset.read_text(encoding="utf-8"), original)
            self.assertEqual(len(list((vault / "知识资产" / "GitHub项目").glob("*.md"))), 1)

            check = service.check_refresh({"id": 101, "fullName": "openai/renamed"})
            self.assertEqual(check["state"], "confirmation_required")
            self.assertIn("仓库路径", {item["label"] for item in check["changes"]})
            self.assertEqual(asset.read_text(encoding="utf-8"), original)
            applied = service.confirm_refresh(check["refreshId"])
            self.assertEqual(applied["state"], "updated")
            self.assertFalse((vault / ".git").exists())
            updated = asset.read_text(encoding="utf-8")
            self.assertIn('repository_full_name: "openai/renamed"', updated)
            self.assertIn("New README", updated)
            registry = json.loads(service.registry_path.read_text(encoding="utf-8"))
            self.assertEqual(registry["repositories"][0]["fullName"], "openai/renamed")
            self.assertEqual(
                service.check_refresh({"id": 101, "fullName": "openai/renamed"})["state"],
                "no_changes",
            )

    def test_import_batch_keeps_only_selected_unique_repositories_and_can_cancel(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service, _store, _vault = self.make_service(Path(tmp))
            batch = service.create_import_batch([
                {"id": 101, "fullName": "openai/example"},
                {"id": 101, "fullName": "openai/example"},
                {"id": 202, "fullName": "openai/second"},
            ])
            self.assertEqual(batch["total"], 2)
            cancelled = service.cancel_import_batch(batch["id"])
            self.assertEqual(cancelled["state"], "cancelled")

    def test_import_batch_accepts_all_loaded_stars_over_one_hundred(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service, _store, _vault = self.make_service(Path(tmp))
            repositories = [
                {"id": index, "fullName": f"owner/repo-{index}"}
                for index in range(1, 122)
            ]

            batch = service.create_import_batch(repositories)

            self.assertEqual(batch["total"], 121)

    def test_slug_collision_does_not_overwrite_another_repository(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            api = FakeAPI(repository(repository_id=101, full_name="foo/a.b"))
            service, _store, vault = self.make_service(root, api=api)
            first = service.ingest_repository({"id": 101, "fullName": "foo/a.b"})
            first_path = vault / first["assetPath"]
            first_text = first_path.read_text(encoding="utf-8")

            api.repo = repository(repository_id=202, full_name="foo/a-b")
            second = service.ingest_repository({"id": 202, "fullName": "foo/a-b"})
            second_path = vault / second["assetPath"]

            self.assertNotEqual(first_path, second_path)
            self.assertEqual(first_path.read_text(encoding="utf-8"), first_text)
            self.assertIn('repository_id: 202', second_path.read_text(encoding="utf-8"))
            self.assertEqual(len(list((vault / "知识资产" / "GitHub项目").glob("*.md"))), 2)

    def test_concurrent_ingest_creates_one_asset(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service, _store, vault = self.make_service(Path(tmp))
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(
                    lambda _item: service.ingest_repository({"id": 101, "fullName": "openai/example"}),
                    range(2),
                ))
            self.assertEqual({item["state"] for item in results}, {"created", "existing"})
            self.assertEqual(len(list((vault / "知识资产" / "GitHub项目").glob("*.md"))), 1)
            self.assertFalse((vault / ".git").exists())

    def test_core_and_github_concurrent_writes_keep_both_index_entries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            service, _store, vault = self.make_service(root)
            scripts = ROOT / "deps" / "douyin" / "scripts"
            sys.path.insert(0, str(scripts))
            from ingest import write_to_vault

            video = root / "source.mp4"
            video.write_bytes(b"video")
            config = SimpleNamespace(
                vault_path=vault,
                vault_relative_root="知识资产/知识入库",
            )
            meta = SimpleNamespace(
                aweme_id="concurrent-source-1",
                source_url="https://www.douyin.com/video/concurrent-source-1",
                title="并发来源入库",
                author="测试",
                duration_sec=12.0,
            )
            result = SimpleNamespace(text=(
                "## 简洁概括\n并发写入验证。\n\n"
                "## 完整内容整理\n验证来源资产与 GitHub 资产并发写入。\n\n"
                "## AI 分析\n> 以下内容由 AI 生成。\n并发索引不能丢失。"
            ))

            with ThreadPoolExecutor(max_workers=2) as pool:
                core_future = pool.submit(
                    write_to_vault,
                    config,
                    meta,
                    video,
                    result,
                    {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
                )
                github_future = pool.submit(
                    service.ingest_repository,
                    {"id": 101, "fullName": "openai/example"},
                )
                core_path, core_status = core_future.result()
                github_result = github_future.result()

            index = (vault / "index.md").read_text(encoding="utf-8")
            github_path = vault / github_result["assetPath"]
            self.assertIn(f"[[{core_path.stem}|", index)
            self.assertIn(f"[[{github_path.stem}|", index)
            self.assertEqual(core_status, "not_managed")
            self.assertFalse((vault / ".git").exists())

    def test_existing_vault_asset_is_migrated_without_duplicate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            service, _store, vault = self.make_service(root)
            asset_dir = vault / "知识资产" / "GitHub项目"
            asset_dir.mkdir(parents=True)
            existing = asset_dir / "20260701-example.md"
            existing.write_text(
                '---\nid: "old-id"\ntitle: "openai/example"\nsource_url: "https://github.com/openai/example"\n'
                'repo: "https://github.com/openai/example"\n---\n\n# Existing\n',
                encoding="utf-8",
            )
            result = service.ingest_repository(
                {"id": 101, "fullName": "openai/example"},
                ingest_intent="derived_ingest",
            )
            self.assertEqual(result["state"], "existing")
            self.assertEqual(len(list(asset_dir.glob("*.md"))), 1)
            registry = json.loads(service.registry_path.read_text(encoding="utf-8"))
            self.assertEqual(registry["repositories"][0]["repositoryId"], 101)

    def test_refresh_preserves_unmanaged_derived_analysis_and_relationships(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            api = FakeAPI()
            service, _store, vault = self.make_service(root, api=api)
            asset_dir = vault / "知识资产" / "GitHub项目"
            asset_dir.mkdir(parents=True)
            asset = asset_dir / "20260701-derived-example.md"
            asset.write_text(
                '---\nid: "derived-id"\ntype: github_project\ningest_intent: derived_ingest\n'
                'title: "openai/example"\nsource_url: "https://github.com/openai/example"\n'
                'repo: "https://github.com/openai/example"\nrepository_id: 101\n'
                'derived_from: ["[[parent|Parent]]"]\nrelated: ["[[parent|Parent]]"]\n---\n\n'
                '# openai/example\n\n## AI 分析\n\n这段分析必须保留。\n',
                encoding="utf-8",
            )
            migrated = service.ingest_repository(
                {"id": 101, "fullName": "openai/example"},
                ingest_intent="derived_ingest",
            )
            self.assertEqual(migrated["state"], "existing")
            api.readme = "# Updated README"
            api.version = "v2.0.0"
            check = service.check_refresh({"id": 101, "fullName": "openai/example"})
            service.confirm_refresh(check["refreshId"])
            refreshed = asset.read_text(encoding="utf-8")
            self.assertIn("这段分析必须保留。", refreshed)
            self.assertIn('derived_from: ["[[parent|Parent]]"]', refreshed)
            self.assertIn("## README 原文", refreshed)
            self.assertIn("Updated README", refreshed)
            self.assertFalse((vault / ".git").exists())

    def test_github_service_source_has_no_automatic_git_commands(self) -> None:
        source = (ROOT / "server" / "github_service.py").read_text(encoding="utf-8")
        for command in ('["git", "init"]', '["git", "add"', '["git", "commit"'):
            self.assertNotIn(command, source)

    def test_auto_star_failure_does_not_fail_successful_ingest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            api = FakeAPI()
            api.star_error = GitHubServiceError("github_api_error", "Starring permission denied")
            service, _store, vault = self.make_service(Path(tmp), api=api)
            service.update_settings(auto_star=True)
            result = service.ingest_repository(
                {"id": 101, "fullName": "openai/example"},
                ingest_intent="derived_ingest",
            )
            self.assertTrue(result["ok"])
            self.assertTrue((vault / result["assetPath"]).exists())
            self.assertTrue(result["autoStar"]["attempted"])
            self.assertFalse(result["autoStar"]["ok"])

    def test_stars_import_does_not_repeat_star_and_derived_hook_does(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            api = FakeAPI()
            service, store, vault = self.make_service(root, api=api)
            service.update_settings(auto_star=True)
            imported = service.ingest_repository({"id": 101, "fullName": "openai/example"})
            self.assertFalse(imported["autoStar"]["attempted"])
            self.assertNotIn(("PUT", "/user/starred/openai/example"), api.calls)

            asset = vault / imported["assetPath"]
            hooked = register_derived_repository(
                api.repo,
                asset,
                vault,
                runtime_root=root / "runtime",
                token_store=store,
                api=api,
            )
            self.assertTrue(hooked["autoStar"]["attempted"])
            self.assertIn(("PUT", "/user/starred/openai/example"), api.calls)
            self.assertFalse((vault / ".git").exists())

    def test_auth_expiry_deletes_keychain_token(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            api = FakeAPI()

            def expired(*args, **kwargs):
                raise GitHubServiceError("auth_expired", "expired")

            api.request = expired  # type: ignore[method-assign]
            service, store, _vault = self.make_service(Path(tmp), api=api)
            with self.assertRaises(GitHubServiceError):
                service.search_repositories("example")
            self.assertTrue(store.deleted)
            self.assertEqual(store.token, "")

    def test_keychain_adapter_uses_security_and_never_writes_a_file(self) -> None:
        calls = []

        def runner(args, **kwargs):
            calls.append(args)
            stdout = "keychain-token\n" if "find-generic-password" in args else ""
            return subprocess.CompletedProcess(args, 0, stdout=stdout, stderr="")

        store = MacOSKeychainTokenStore(runner=runner, platform="darwin")
        self.assertEqual(store.get(), "keychain-token")
        store.set("new-token")
        store.delete()
        self.assertEqual([call[1] for call in calls], [
            "find-generic-password",
            "add-generic-password",
            "delete-generic-password",
        ])
        self.assertIn("-U", calls[1])
        self.assertNotIn("new-token", calls[1])


if __name__ == "__main__":
    unittest.main()
