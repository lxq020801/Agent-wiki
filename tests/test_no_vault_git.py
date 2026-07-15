#!/usr/bin/env python3
from __future__ import annotations

import ast
import asyncio
import contextlib
import json
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
DOUYIN_SCRIPTS = ROOT / "deps" / "douyin" / "scripts"
for import_root in (ROOT, DOUYIN_SCRIPTS):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))


ASSET_WRITE_SOURCES = (
    ROOT / "server" / "vault_writer.py",
    ROOT / "server" / "github_service.py",
    ROOT / "install" / "bootstrap.py",
    ROOT / "scripts" / "ingest_url.py",
    DOUYIN_SCRIPTS / "ingest.py",
    DOUYIN_SCRIPTS / "derive_executor.py",
)


def _command_tokens(command: object) -> list[str]:
    if isinstance(command, bytes):
        command = command.decode("utf-8", errors="replace")
    if isinstance(command, str):
        try:
            return shlex.split(command)
        except ValueError:
            return [command]
    if isinstance(command, (list, tuple)):
        return [str(item) for item in command]
    return []


def _is_git_command(command: object) -> bool:
    tokens = _command_tokens(command)
    return bool(tokens) and Path(tokens[0]).name.casefold() in {"git", "git.exe"}


class GitCommandTrap:
    """Reject process-based Git calls while allowing unrelated mocked work."""

    def __init__(self) -> None:
        self.attempts: list[list[str]] = []

    def _wrap(self, original):
        def guarded(command, *args, **kwargs):
            if _is_git_command(command):
                tokens = _command_tokens(command)
                self.attempts.append(tokens)
                raise AssertionError(f"knowledge asset path attempted Git: {tokens}")
            return original(command, *args, **kwargs)

        return guarded

    @contextlib.contextmanager
    def active(self):
        with contextlib.ExitStack() as stack:
            for name in ("run", "call", "check_call", "check_output", "Popen"):
                original = getattr(subprocess, name)
                stack.enter_context(mock.patch.object(subprocess, name, self._wrap(original)))
            stack.enter_context(mock.patch.object(os, "system", self._wrap(os.system)))
            yield self


@contextlib.contextmanager
def _reject_git_processes():
    trap = GitCommandTrap()
    with trap.active():
        yield trap
    if trap.attempts:
        raise AssertionError(f"unexpected Git calls: {trap.attempts}")


def _seed_existing_git(vault: Path) -> dict[str, bytes]:
    git_dir = vault / ".git"
    (git_dir / "refs" / "heads").mkdir(parents=True)
    (git_dir / "objects" / "aa").mkdir(parents=True)
    (git_dir / "HEAD").write_text("ref: refs/heads/user-history\n", encoding="utf-8")
    (git_dir / "refs" / "heads" / "user-history").write_text("a" * 40 + "\n", encoding="ascii")
    (git_dir / "index").write_bytes(b"user-owned-index\x00")
    (git_dir / "objects" / "aa" / ("b" * 38)).write_bytes(b"user-owned-object")
    return _git_snapshot(vault)


def _git_snapshot(vault: Path) -> dict[str, bytes]:
    git_dir = vault / ".git"
    if not git_dir.exists():
        return {}
    return {
        str(path.relative_to(git_dir)): path.read_bytes()
        for path in sorted(git_dir.rglob("*"))
        if path.is_file()
    }


def _source_result() -> SimpleNamespace:
    return SimpleNamespace(text=(
        "## Concise summary\nA mocked source for vault write regression.\n\n"
        "## Complete content\nAll source material in this test is synthetic.\n\n"
        "## AI analysis\nThe mocked source exercises only local file writes."
    ))


def _source_meta(source_id: str) -> SimpleNamespace:
    return SimpleNamespace(
        aweme_id=source_id,
        source_url=f"https://example.invalid/mock/{source_id}",
        title=f"Mock source {source_id}",
        author="Test Double",
        duration_sec=12.0,
    )


def _source_config(vault: Path) -> SimpleNamespace:
    return SimpleNamespace(vault_path=vault, vault_relative_root="知识资产/知识入库")


def _derive_config(root: Path, vault: Path):
    from config_loader import Config

    return Config(
        ark_api_key="mock-key",
        ark_endpoint="https://ark.cn-beijing.volces.com/api/v3",
        analyzer_model="mock-analyzer",
        analyzer_fallback="mock-fallback",
        strategy_model="mock-strategy",
        default_quality="quality",
        balanced_target_frames=1,
        quality_target_frames=1,
        fps_min=1.0,
        fps_max=1.0,
        file_active_timeout_sec=1,
        cookie_path=root / "mock-cookie.txt",
        vault_path=vault,
        vault_relative_root="知识资产/知识入库",
        server_enabled=False,
        server_host="127.0.0.1",
        server_port=8765,
        config_file=root / "runtime" / "config.toml",
    )


def _derived_task() -> dict:
    return {
        "id": "mock-derived-task",
        "candidate": {
            "id": "mock-github-candidate",
            "name": "Mock Repository",
            "targetType": "github_project",
            "relationType": "reference",
            "reason": "Synthetic regression candidate",
            "evidence": ["mock evidence"],
        },
    }


def _derived_target():
    import derive_executor

    return derive_executor.ResolvedTarget(
        url="https://github.com/example/mock-repository",
        title="Mock Repository",
        kind="github_project",
        confidence=1.0,
        evidence=["mock resolver"],
        raw={
            "repo": {
                "id": 101,
                "full_name": "example/mock-repository",
                "description": "Synthetic repository metadata",
                "language": "Python",
                "stargazers_count": 1,
                "forks_count": 0,
                "open_issues_count": 0,
                "license": {"spdx_id": "MIT"},
                "pushed_at": "2026-01-01T00:00:00Z",
                "html_url": "https://github.com/example/mock-repository",
            },
            "readme": "Synthetic README",
        },
    )


class _StatusWriterDouble:
    def __init__(self) -> None:
        self.updates: list[dict] = []

    def update(self, **fields) -> None:
        self.updates.append(fields)


class _MemoryTokenStore:
    def __init__(self) -> None:
        self.token = "mock-token"

    def get(self) -> str:
        return self.token

    def set(self, token: str) -> None:
        self.token = token

    def delete(self) -> None:
        self.token = ""


def _repository(repository_id: int, full_name: str) -> dict:
    owner, name = full_name.split("/", 1)
    return {
        "id": repository_id,
        "name": name,
        "full_name": full_name,
        "owner": {"login": owner},
        "html_url": f"https://github.com/{full_name}",
        "description": "Synthetic GitHub repository",
        "language": "Python",
        "stargazers_count": 10,
        "forks_count": 2,
        "open_issues_count": 1,
        "license": {"spdx_id": "MIT"},
        "archived": False,
        "private": False,
        "default_branch": "main",
        "pushed_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
    }


class _GitHubAPIDouble:
    def __init__(self) -> None:
        self.repositories = {101: _repository(101, "example/mock-one")}
        self.readme = "Synthetic README v1"
        self.version = "v1.0.0"
        self.fail_star = False

    def request(self, method: str, path: str, *, token: str, **_kwargs):
        from server.github_service import GitHubServiceError

        if path.startswith("/repositories/"):
            repository_id = int(path.rsplit("/", 1)[1])
            if repository_id not in self.repositories:
                raise GitHubServiceError("not_found", "synthetic repository missing")
            return dict(self.repositories[repository_id]), {}
        if path.endswith("/readme"):
            return self.readme, {}
        if path.endswith("/releases/latest"):
            return {"tag_name": self.version}, {}
        if method == "PUT" and path.startswith("/user/starred/"):
            if self.fail_star:
                raise GitHubServiceError("github_api_error", "synthetic star failure")
            return {}, {}
        raise AssertionError(f"unexpected mocked GitHub request: {method} {path}")


def _github_service(root: Path, vault: Path, api: _GitHubAPIDouble):
    from server.github_asset_pipeline import GitHubAssetPipeline
    from server.github_service import GitHubService

    config = root / "github-config.toml"
    config.write_text(f'[vault]\npath = "{vault}"\n', encoding="utf-8")
    pipeline = GitHubAssetPipeline(
        config_path=config,
        analyzer=lambda material, cleaned_readme, _intent: (
            f"## 简洁概括\n{material['public']['fullName']} 是一个合成测试仓库。\n\n"
            f"## 完整内容整理\n{cleaned_readme or '仓库没有 README。'}\n\n"
            "## AI 分析\n> 以下内容由 AI 生成。\n该内容仅用于本地写入回归测试。"
        ),
    )
    return GitHubService(
        runtime_root=root / "github-runtime",
        config_path=config,
        client_id="mock-client-id",
        token_store=_MemoryTokenStore(),
        api=api,
        asset_pipeline=pipeline,
    )


def _literal_git_calls(path: Path) -> list[int]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    lines: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        try:
            command = ast.literal_eval(node.args[0])
        except (ValueError, TypeError, SyntaxError):
            continue
        if _is_git_command(command):
            lines.append(node.lineno)
    return lines


class VaultGitRegressionTests(unittest.TestCase):
    def test_source_ingest_success_preserves_existing_git(self) -> None:
        import ingest
        from server.vault_writer import VAULT_GIT_STATUS

        for source_kind in ("video", "image_post"):
            with self.subTest(source_kind=source_kind), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                vault = root / f"{source_kind}-vault"
                vault.mkdir()
                before = _seed_existing_git(vault)
                source = root / ("mock.mp4" if source_kind == "video" else "mock.jpg")
                source.write_bytes(b"synthetic-source")
                with _reject_git_processes():
                    if source_kind == "video":
                        _path, status = ingest.write_to_vault(
                            _source_config(vault), _source_meta("video-success"), source,
                            _source_result(), {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
                        )
                    else:
                        _path, status = ingest.write_image_post_to_vault(
                            _source_config(vault), _source_meta("image-success"), [source],
                            _source_result(), {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
                        )
                self.assertEqual(status, VAULT_GIT_STATUS)
                self.assertEqual(_git_snapshot(vault), before)

    def test_source_ingest_index_failure_never_stages_or_commits(self) -> None:
        import ingest

        for source_kind in ("video", "image_post"):
            with self.subTest(source_kind=source_kind), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                vault = root / f"{source_kind}-failure-vault"
                vault.mkdir()
                before = _seed_existing_git(vault)
                source = root / ("failure.mp4" if source_kind == "video" else "failure.jpg")
                source.write_bytes(b"synthetic-failure-source")
                with (
                    _reject_git_processes(),
                    mock.patch.object(ingest, "_update_index", side_effect=OSError("mock index failure")),
                    self.assertRaisesRegex(OSError, "mock index failure"),
                ):
                    if source_kind == "video":
                        ingest.write_to_vault(
                            _source_config(vault), _source_meta("video-failure"), source,
                            _source_result(), {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
                        )
                    else:
                        ingest.write_image_post_to_vault(
                            _source_config(vault), _source_meta("image-failure"), [source],
                            _source_result(), {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
                        )
                self.assertEqual(_git_snapshot(vault), before)

    def test_derived_write_and_post_write_failure_preserve_existing_git(self) -> None:
        import derive_executor
        from server.vault_writer import VAULT_GIT_STATUS

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            vault = root / "derive-vault"
            vault.mkdir()
            before = _seed_existing_git(vault)
            with (
                mock.patch.dict(os.environ, {"AGENT_WIKI_HOME": str(root / "runtime")}),
                _reject_git_processes(),
                mock.patch.object(derive_executor, "resolve_target", return_value=_derived_target()),
                mock.patch.object(
                    derive_executor,
                    "_call_lite_model",
                    return_value=(
                        "## 简洁概括\nSynthetic model output from a test double.\n\n"
                        "## 完整内容整理\nSynthetic README and repository metadata only.\n\n"
                        "## AI 分析\n> 以下内容由 AI 生成。\nNo real model was called.",
                        {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
                    ),
                ),
                mock.patch.object(
                    derive_executor,
                    "register_derived_repository",
                    side_effect=RuntimeError("mock GitHub hook failure"),
                ),
            ):
                summary = derive_executor.execute_derived_task(
                    _derived_task(), _derive_config(root, vault), _StatusWriterDouble(),
                )
            self.assertEqual(summary["git_status"], VAULT_GIT_STATUS)
            self.assertFalse(summary["github_integration"]["ok"])
            self.assertTrue(Path(summary["vault_path"]).exists())
            self.assertEqual(_git_snapshot(vault), before)

    def test_derived_resolution_failure_never_initializes_git(self) -> None:
        import derive_executor

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            vault = root / "derive-failure-vault"
            vault.mkdir()
            before = _seed_existing_git(vault)
            with (
                mock.patch.dict(os.environ, {"AGENT_WIKI_HOME": str(root / "runtime")}),
                _reject_git_processes(),
                mock.patch.object(
                    derive_executor,
                    "resolve_target",
                    side_effect=derive_executor.DeriveError(
                        "mock_resolution_failure", "synthetic failure"
                    ),
                ),
                self.assertRaisesRegex(derive_executor.DeriveError, "synthetic failure"),
            ):
                derive_executor.execute_derived_task(
                    _derived_task(), _derive_config(root, vault), _StatusWriterDouble(),
                )
            self.assertEqual(_git_snapshot(vault), before)

    def test_github_stars_batch_success_and_failure_never_touch_vault_git(self) -> None:
        from server.websocket_server import LibrarianServer

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            vault = root / "stars-vault"
            vault.mkdir()
            before = _seed_existing_git(vault)
            service = _github_service(root, vault, _GitHubAPIDouble())
            batch = service.create_import_batch([
                {"id": 101, "fullName": "example/mock-one"},
                {"id": 202, "fullName": "example/mock-missing"},
            ])
            server = LibrarianServer(
                enable_task_runner=False,
                task_concurrency=1,
                runtime_identity={"testDouble": True},
                github_service=service,
            )
            with _reject_git_processes():
                asyncio.run(server._run_github_import(batch["id"]))
            stored = service.import_batch(batch["id"])
            self.assertEqual(stored["state"], "completed")
            self.assertEqual(stored["succeeded"], 1)
            self.assertEqual(stored["failed"], 1)
            self.assertEqual(stored["results"][1]["code"], "not_found")
            self.assertEqual(_git_snapshot(vault), before)

    def test_github_refresh_failure_never_stages_or_commits(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            vault = root / "refresh-vault"
            vault.mkdir()
            before = _seed_existing_git(vault)
            api = _GitHubAPIDouble()
            service = _github_service(root, vault, api)
            with _reject_git_processes():
                service.ingest_repository({"id": 101, "fullName": "example/mock-one"})
                api.readme = "Synthetic README v2"
                refresh = service.check_refresh({"id": 101, "fullName": "example/mock-one"})
                with (
                    mock.patch.object(
                        service.asset_pipeline, "write", side_effect=OSError("mock refresh index failure")
                    ),
                    self.assertRaisesRegex(OSError, "mock refresh index failure"),
                ):
                    service.confirm_refresh(refresh["refreshId"])
            self.assertEqual(_git_snapshot(vault), before)

    def test_derived_auto_star_failure_never_touches_vault_git(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            vault = root / "auto-star-vault"
            vault.mkdir()
            before = _seed_existing_git(vault)
            api = _GitHubAPIDouble()
            api.fail_star = True
            service = _github_service(root, vault, api)
            with _reject_git_processes():
                service.update_settings(auto_star=True)
                result = service.ingest_repository(
                    {"id": 101, "fullName": "example/mock-one"},
                    ingest_intent="derived_ingest",
                )
            self.assertTrue(result["ok"])
            self.assertTrue(result["autoStar"]["attempted"])
            self.assertFalse(result["autoStar"]["ok"])
            self.assertEqual(result["autoStar"]["code"], "github_api_error")
            self.assertEqual(result["autoStar"]["message"], "synthetic star failure")
            self.assertEqual(_git_snapshot(vault), before)

    def test_first_initialization_never_creates_or_changes_git(self) -> None:
        import install.bootstrap as bootstrap_module

        for existing_git in (False, True):
            with self.subTest(existing_git=existing_git), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                runtime = root / "bootstrap-runtime"
                vault = root / "bootstrap-vault"
                vault.mkdir()
                if existing_git:
                    (vault / "raw").mkdir()
                    (vault / "知识资产" / "知识入库").mkdir(parents=True)
                    (vault / "index.md").write_text("# Existing managed vault\n", encoding="utf-8")
                    (vault / ".agent-wiki-vault.json").write_text(json.dumps({
                        "schemaVersion": 1,
                        "product": "agent-wiki",
                        "vaultId": str(uuid.uuid4()),
                        "userName": "bootstrap-vault",
                        "createdAt": "2026-07-15T00:00:00",
                    }), encoding="utf-8")
                before = _seed_existing_git(vault) if existing_git else {}
                config = runtime / "config.toml"
                config.parent.mkdir(parents=True)
                config.write_text(
                    '[ark]\napi_key = "mock-key"\n\n[vault]\npath = ""\n',
                    encoding="utf-8",
                )
                with (
                    _reject_git_processes(),
                    mock.patch.object(bootstrap_module, "RUNTIME_ROOT", runtime),
                    mock.patch.object(bootstrap_module, "CONFIG_PATH", config),
                    mock.patch.object(bootstrap_module, "EXTENSION_DEST", runtime / "extension"),
                    mock.patch.object(bootstrap_module, "ensure_douyin_venv"),
                    mock.patch.object(bootstrap_module, "check_ffmpeg"),
                    mock.patch.object(bootstrap_module, "ensure_extension_copy"),
                ):
                    result = bootstrap_module.bootstrap(
                        install_deps=False,
                        vault_path=vault,
                        verify_websocket=False,
                    )
                self.assertTrue(result.ok)
                self.assertTrue((vault / "知识资产" / "知识入库").is_dir())
                self.assertTrue((vault / "index.md").is_file())
                if existing_git:
                    self.assertEqual(_git_snapshot(vault), before)
                else:
                    self.assertFalse((vault / ".git").exists())

    def test_asset_write_sources_have_no_literal_git_process_commands(self) -> None:
        for source in ASSET_WRITE_SOURCES:
            with self.subTest(source=source):
                self.assertEqual(_literal_git_calls(source), [])

    def test_source_repository_git_audit_remains_available_with_mocked_runner(self) -> None:
        from scripts import release_audit

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            completed = subprocess.CompletedProcess(
                ["git", "status", "--short"], 0, stdout=b"", stderr=b""
            )
            with mock.patch.object(
                release_audit.subprocess, "run", return_value=completed
            ) as runner:
                result = release_audit.run_git(root, ["status", "--short"])
            self.assertIs(result, completed)
            runner.assert_called_once_with(
                ["git", "status", "--short"],
                cwd=root,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
            )


if __name__ == "__main__":
    unittest.main()
