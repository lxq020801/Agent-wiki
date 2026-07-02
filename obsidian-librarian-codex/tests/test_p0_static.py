#!/usr/bin/env python3
from __future__ import annotations

import os
import json
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "deps" / "douyin" / "scripts"


def test_config_loads(tmp: Path) -> None:
    os.environ["OBSIDIAN_LIBRARIAN_HOME"] = str(tmp / "runtime")
    import sys

    sys.path.insert(0, str(SCRIPTS))
    from config_loader import load_config

    vault = tmp / "config-vault"
    vault.mkdir()
    config = tmp / "runtime" / "config.toml"
    config.parent.mkdir(parents=True)
    config.write_text(
        f"""
[ark]
api_key = "test-key"
endpoint = "https://ark.cn-beijing.volces.com/api/v3"

[models]
analyzer = "doubao-seed-2-0-lite-260428"
analyzer_fallback = "doubao-seed-2-0-mini-260428"

[analysis]
default_quality = "balanced"
balanced_target_frames = 240
quality_target_frames = 1250
fps_min = 0.2
fps_max = 5.0
file_active_timeout_sec = 120

[douyin]
cookie_path = "{tmp / 'runtime' / 'cookie' / 'douyin.txt'}"

[vault]
path = "{vault}"
relative_root = "知识资产/视频分析"

[server]
enabled = true
host = "127.0.0.1"
port = 8765
""",
        encoding="utf-8",
    )
    cfg = load_config(config)
    assert cfg.vault_path == vault.resolve()
    assert cfg.vault_relative_root == "知识资产/视频分析"
    assert cfg.default_quality == "quality"


def test_netscape_cookie_conversion(tmp: Path) -> None:
    import sys

    sys.path.insert(0, str(SCRIPTS))
    from downloader import _read_cookie

    cookie = tmp / "douyin.txt"
    cookie.write_text(
        ".douyin.com\tTRUE\t/\tTRUE\t0\tcookie_a\tabc\n"
        ".douyin.com\tTRUE\t/\tTRUE\t0\tcookie_b\tdef\n",
        encoding="utf-8",
    )
    assert _read_cookie(cookie) == "cookie_a=abc; cookie_b=def"


@dataclass
class FakeMeta:
    aweme_id: str = "1234567890123456789"
    title: str = "A Douyin Test Video"
    author: str = "Tester"
    author_sec_uid: str = "sec"
    duration_sec: float = 61
    cover_url: str = ""
    source_url: str = "https://v.douyin.com/test/"


@dataclass
class FakeResult:
    text: str = "这是一个测试视频，展示 Agent 入库流程。"
    file_id: str = "file-test"
    fps_used: float = 1.0
    quality: str = "quality"
    model: str = "doubao-seed-2-0-lite-260428"
    target_frames: int = 1250
    actual_frames_estimate: int = 61
    truncated: bool = False


def test_vault_write_schema(tmp: Path) -> None:
    import sys

    sys.path.insert(0, str(SCRIPTS))
    from config_loader import Config
    from ingest import write_to_vault

    vault = tmp / "schema-vault"
    vault.mkdir()
    runtime = tmp / "schema-runtime"
    runtime.mkdir()
    video = tmp / "video.mp4"
    video.write_bytes(b"fake-video")

    cfg = Config(
        ark_api_key="test",
        ark_endpoint="https://ark.cn-beijing.volces.com/api/v3",
        analyzer_model="doubao-seed-2-0-lite-260428",
        analyzer_fallback="doubao-seed-2-0-mini-260428",
        default_quality="quality",
        balanced_target_frames=240,
        quality_target_frames=1250,
        fps_min=0.2,
        fps_max=5.0,
        file_active_timeout_sec=120,
        cookie_path=runtime / "cookie" / "douyin.txt",
        vault_path=vault,
        vault_relative_root="知识资产/视频分析",
        server_enabled=True,
        server_host="127.0.0.1",
        server_port=8765,
        config_file=runtime / "config.toml",
    )
    md_path, git_status = write_to_vault(
        cfg,
        FakeMeta(),
        video,
        FakeResult(),
        {"input_tokens": 1, "output_tokens": 2, "total_tokens": 3, "cost_rmb_estimate": 0.1},
    )
    assert md_path.exists()
    assert "知识资产/视频分析" in str(md_path)
    text = md_path.read_text(encoding="utf-8")
    assert re.search(r'^id: "?\d{8}-video-\d{3}"?$', text, re.MULTILINE)
    assert "type: video_analysis" in text
    assert "source_url:" in text
    assert "tags: [douyin, video-analysis, case-study]" in text
    index = vault / "index.md"
    assert index.exists()
    assert "[[" in index.read_text(encoding="utf-8")
    assert git_status in {"committed", "no changes to commit"}
    assert (vault / ".git").exists()


def test_websocket_config_writer(tmp: Path) -> None:
    import asyncio
    import sys

    os.environ["OBSIDIAN_LIBRARIAN_HOME"] = str(tmp / "ws-runtime")
    sys.path.insert(0, str(ROOT / "server"))
    from websocket_server import LibrarianServer
    from config_loader import load_config

    vault = tmp / "ws-vault"
    vault.mkdir()
    (vault / ".obsidian").mkdir()
    (vault / "index.md").write_text("# 知识库索引\n", encoding="utf-8")
    server = LibrarianServer()
    asyncio.run(server.handle_config_update({
        "apiKey": "test-key",
        "vaultPath": str(vault),
        "model": "doubao-seed-2-0-lite-260428",
        "quality": "balanced",
        "qualityTargetFrames": 1,
        "fpsMin": 5.0,
        "fpsMax": 5.0,
    }))
    cfg = load_config(tmp / "ws-runtime" / "config.toml")
    assert cfg.ark_api_key == "test-key"
    assert cfg.vault_path == vault.resolve()
    assert cfg.vault_relative_root == "知识资产/视频分析"
    assert cfg.default_quality == "quality"
    assert cfg.quality_target_frames == 1250
    assert cfg.fps_min == 0.2
    assert cfg.fps_max == 5.0
    assert oct((tmp / "ws-runtime" / "config.toml").stat().st_mode & 0o777) == "0o600"

    asyncio.run(server.handle_cookie_update("douyin", ".douyin.com\tTRUE\t/\tTRUE\t0\ta\tb"))
    cookie_path = tmp / "ws-runtime" / "cookie" / "douyin.txt"
    assert cookie_path.exists()
    assert oct(cookie_path.stat().st_mode & 0o777) == "0o600"


def test_websocket_config_writer_supports_agent_plan(tmp: Path) -> None:
    import asyncio
    import sys

    os.environ["OBSIDIAN_LIBRARIAN_HOME"] = str(tmp / "ws-runtime-plan")
    sys.path.insert(0, str(ROOT / "server"))
    from websocket_server import LibrarianServer
    from config_loader import load_config

    vault = tmp / "ws-vault-plan"
    vault.mkdir()
    (vault / ".obsidian").mkdir()
    (vault / "index.md").write_text("# 知识库索引\n", encoding="utf-8")

    server = LibrarianServer()
    asyncio.run(server.handle_config_update({
        "provider": "volcengine_agent_plan",
        "agentPlanApiKey": "plan-key",
        "agentPlanEndpoint": "https://ark.cn-beijing.volces.com/api/plan/v3",
        "model": "doubao-seed-2.0-lite",
        "vaultPath": str(vault),
    }))

    cfg = load_config(tmp / "ws-runtime-plan" / "config.toml")
    assert cfg.provider == "volcengine_agent_plan"
    assert cfg.ark_api_key == "plan-key"
    assert cfg.ark_endpoint == "https://ark.cn-beijing.volces.com/api/plan/v3"
    assert cfg.analyzer_model == "doubao-seed-2.0-lite"
    assert cfg.default_quality == "quality"


def test_model_health_check_supports_agent_plan(tmp: Path) -> None:
    import sys

    os.environ["OBSIDIAN_LIBRARIAN_HOME"] = str(tmp / "health-plan-runtime")
    sys.path.insert(0, str(ROOT / "server"))
    import websocket_server
    from websocket_server import LibrarianServer

    calls = []

    class FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    def fake_urlopen(request, timeout):
        calls.append((request, timeout))
        return FakeResponse()

    old_urlopen = websocket_server.urllib.request.urlopen
    websocket_server.urllib.request.urlopen = fake_urlopen
    try:
        server = LibrarianServer()
        status = server._check_model_health_sync({
            "provider": "volcengine_agent_plan",
            "apiKey": "plan-health-key",
            "model": "doubao-seed-2.0-lite",
            "endpoint": "https://ark.cn-beijing.volces.com/api/plan/v3",
        })
    finally:
        websocket_server.urllib.request.urlopen = old_urlopen

    assert status["ok"] is True
    assert status["state"] == "ready"
    assert calls
    request, timeout = calls[0]
    assert request.full_url == "https://ark.cn-beijing.volces.com/api/plan/v3/responses"
    assert timeout == 10
    assert b'"ping"' in request.data
    assert "plan-health-key" in request.headers.get("Authorization", "")


def test_vault_discovery_is_strict(tmp: Path) -> None:
    import sys

    sys.path.insert(0, str(ROOT))
    from install.vault_discovery import discover_vault, score_vault

    schema_only = tmp / "schema-only"
    schema_only.mkdir()
    (schema_only / "SCHEMA.md").write_text("# 知识库宪法 (SCHEMA.md)\n", encoding="utf-8")
    assert score_vault(schema_only, source="test") is None

    skill_pkg = tmp / "skill-package"
    skill_pkg.mkdir()
    (skill_pkg / "SKILL.md").write_text("---\nname: obsidian-librarian\n---\n", encoding="utf-8")
    (skill_pkg / "chrome-extension").mkdir()
    (skill_pkg / "server").mkdir()
    (skill_pkg / "install").mkdir()
    (skill_pkg / "SCHEMA.md").write_text("# 知识库宪法 (SCHEMA.md)\n", encoding="utf-8")
    (skill_pkg / "index.md").write_text("# 知识库索引\n", encoding="utf-8")
    assert score_vault(skill_pkg, source="test") is None

    vault = tmp / "real-vault"
    vault.mkdir()
    (vault / ".obsidian").mkdir()
    (vault / "index.md").write_text("# 知识库索引\n", encoding="utf-8")
    (vault / "SCHEMA.md").write_text("# 知识库宪法 (SCHEMA.md)\n", encoding="utf-8")
    (vault / "知识资产").mkdir()
    (vault / "templates").mkdir()
    (vault / "raw").mkdir()
    (vault / "系统记录").mkdir()
    result = discover_vault(cwd=vault, runtime_root=tmp / "runtime")
    assert result.selected
    assert Path(result.selected.path) == vault.resolve()


def test_analyzer_ark_file_protocol(tmp: Path) -> None:
    import asyncio
    import sys

    sys.path.insert(0, str(SCRIPTS))
    import analyzer

    video = tmp / "video.mp4"
    video.write_bytes(b"fake-video")

    class FakeFiles:
        def __init__(self) -> None:
            self.create_kwargs = None

        def create(self, **kwargs):
            self.create_kwargs = kwargs
            return SimpleNamespace(id="file-uploaded", status="processing")

    class FakeClient:
        def __init__(self) -> None:
            self.files = FakeFiles()

    client = FakeClient()
    result = asyncio.run(
        analyzer._upload_with_preprocess(
            client,
            video,
            fps=0.3,
            model="doubao-seed-2-1-pro-260628",
        )
    )
    assert result.id == "file-uploaded"
    assert client.files.create_kwargs["purpose"] == "user_data"
    preprocess = client.files.create_kwargs["preprocess_configs"]["video"]
    assert preprocess == {"fps": 0.3, "model": "doubao-seed-2-1-pro-260628"}

    class FallbackFiles:
        def __init__(self) -> None:
            self.calls = 0
            self.create_kwargs = None

        def create(self, **kwargs):
            self.calls += 1
            if "preprocess_configs" in kwargs:
                raise TypeError("old SDK")
            self.create_kwargs = kwargs
            return SimpleNamespace(id="file-fallback", status="processing")

    fallback_client = SimpleNamespace(files=FallbackFiles())
    fallback_result = asyncio.run(
        analyzer._upload_with_preprocess(
            fallback_client,
            video,
            fps=0.5,
            model="doubao-seed-2-1-pro-260628",
        )
    )
    assert fallback_result.id == "file-fallback"
    assert fallback_client.files.calls == 2
    fallback_preprocess = fallback_client.files.create_kwargs["extra_body"]["preprocess_configs"]["video"]
    assert fallback_preprocess == {"fps": 0.5, "model": "doubao-seed-2-1-pro-260628"}

    fifty_one_mb = tmp / "51mb.mp4"
    with fifty_one_mb.open("wb") as f:
        f.truncate(51 * 1024 * 1024)
    assert analyzer._check_size(fifty_one_mb) == 51 * 1024 * 1024

    too_large = tmp / "513mb.mp4"
    with too_large.open("wb") as f:
        f.truncate(513 * 1024 * 1024)
    try:
        analyzer._check_size(too_large)
    except analyzer.FileTooLargeError:
        pass
    else:
        raise AssertionError("expected FileTooLargeError for >512MB video")


def test_analyzer_wait_and_stream_protocol(tmp: Path) -> None:
    import asyncio
    import sys

    sys.path.insert(0, str(SCRIPTS))
    import analyzer

    active_client = SimpleNamespace(
        files=SimpleNamespace(
            retrieve=lambda file_id: SimpleNamespace(id=file_id, status="active")
        )
    )
    active = asyncio.run(
        analyzer._wait_for_active(
            active_client,
            "file-ready",
            timeout_sec=1,
            on_progress=None,
        )
    )
    assert active.id == "file-ready"

    failed_client = SimpleNamespace(
        files=SimpleNamespace(
            retrieve=lambda file_id: SimpleNamespace(id=file_id, status="failed")
        )
    )
    try:
        asyncio.run(
            analyzer._wait_for_active(
                failed_client,
                "file-bad",
                timeout_sec=1,
                on_progress=None,
            )
        )
    except analyzer.APIError:
        pass
    else:
        raise AssertionError("expected APIError for failed file status")

    class Usage:
        def model_dump(self) -> dict:
            return {"input_tokens": 1, "output_tokens": 2, "total_tokens": 3}

    class FakeResponses:
        def __init__(self) -> None:
            self.kwargs = None

        def create(self, **kwargs):
            self.kwargs = kwargs
            return iter([
                SimpleNamespace(type="response.reasoning_summary.delta", delta="不要进入正文"),
                SimpleNamespace(type="response.output_text.delta", delta="第一段"),
                SimpleNamespace(type="response.output_text.delta", delta="第二段"),
                SimpleNamespace(type="response.completed", response=SimpleNamespace(usage=Usage())),
            ])

    responses = FakeResponses()
    stream_client = SimpleNamespace(responses=responses)
    text, usage = asyncio.run(
        analyzer._stream_responses(
            stream_client,
            model="doubao-seed-2-1-pro-260628",
            file_id="file-ready",
            prompt="请拆解视频",
            on_progress=None,
        )
    )
    assert text == "第一段第二段"
    assert usage["total_tokens"] == 3
    content = responses.kwargs["input"][0]["content"]
    assert content[0] == {"type": "input_video", "file_id": "file-ready"}
    assert content[1] == {"type": "input_text", "text": "请拆解视频"}

    class FinalOnlyResponses:
        def create(self, **kwargs):
            return iter([
                SimpleNamespace(
                    type="response.completed",
                    response={
                        "output": [{
                            "content": [{
                                "type": "output_text",
                                "text": "最终文本",
                            }],
                        }],
                        "usage": {"total_tokens": 2},
                    },
                )
            ])

    final_text, final_usage = asyncio.run(
        analyzer._stream_responses(
            SimpleNamespace(responses=FinalOnlyResponses()),
            model="doubao-seed-2-1-pro-260628",
            file_id="file-ready",
            prompt="请拆解视频",
            on_progress=None,
        )
    )
    assert final_text == "最终文本"
    assert final_usage == {"total_tokens": 2}


def test_model_health_check_redacts_secret(tmp: Path) -> None:
    import sys

    os.environ["OBSIDIAN_LIBRARIAN_HOME"] = str(tmp / "health-runtime")
    sys.path.insert(0, str(ROOT / "server"))
    import websocket_server
    from websocket_server import LibrarianServer

    calls = []

    class FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    def fake_urlopen(request, timeout):
        calls.append((request, timeout))
        return FakeResponse()

    old_urlopen = websocket_server.urllib.request.urlopen
    websocket_server.urllib.request.urlopen = fake_urlopen
    try:
        server = LibrarianServer()
        status = server._check_model_health_sync({
            "provider": "doubao",
            "apiKey": "secret-health-key",
            "model": "doubao-seed-2-0-lite-260428",
            "endpoint": "https://evil.example.invalid/api/v3",
        })
    finally:
        websocket_server.urllib.request.urlopen = old_urlopen

    assert status["ok"] is True
    assert status["state"] == "ready"
    assert "secret-health-key" not in str(status)
    assert calls
    request, timeout = calls[0]
    assert request.full_url == "https://ark.cn-beijing.volces.com/api/v3/tokenization"
    assert timeout == 10
    assert b'"text": "ping"' in request.data
    assert "secret-health-key" in request.headers.get("Authorization", "")


def test_model_health_status_persists(tmp: Path) -> None:
    import sys

    runtime = tmp / "health-persist-runtime"
    os.environ["OBSIDIAN_LIBRARIAN_HOME"] = str(runtime)
    sys.path.insert(0, str(ROOT / "server"))
    from websocket_server import LibrarianServer

    runtime.mkdir(parents=True)
    (runtime / "config.toml").write_text(
        """
[ark]
api_key = "test-key"
endpoint = "https://ark.cn-beijing.volces.com/api/v3"

[models]
analyzer = "doubao-seed-2-1-pro-260628"
""",
        encoding="utf-8",
    )
    status_dir = runtime / "status"
    status_dir.mkdir()
    (status_dir / "model_health.json").write_text(
        json.dumps({
            "ok": True,
            "state": "ready",
            "provider": "doubao",
            "model": "doubao-seed-2-1-pro-260628",
            "checkedAt": "2026-07-02T12:00:00",
            "message": "模型连通正常",
        }, ensure_ascii=False),
        encoding="utf-8",
    )

    model = LibrarianServer().status_snapshot()["model"]
    assert model["ok"] is True
    assert model["state"] == "ready"
    assert model["model"] == "doubao-seed-2-1-pro-260628"
    assert model["checkedAt"] == "2026-07-02T12:00:00"
    assert "test-key" not in str(model)


def test_codex_handoff_is_marked_archived() -> None:
    for path in (ROOT / "codex-handoff").glob("*.md"):
        head = path.read_text(encoding="utf-8")[:500]
        assert "历史归档" in head, path
        assert "非权威" in head, path


def test_websocket_rejects_task_request(tmp: Path) -> None:
    import asyncio
    import json
    import sys

    os.environ["OBSIDIAN_LIBRARIAN_HOME"] = str(tmp / "ws-runtime-reject")
    sys.path.insert(0, str(ROOT / "server"))
    from websocket_server import LibrarianServer

    class FakeSocket:
        def __init__(self) -> None:
            self.sent: list[str] = []

        async def send(self, payload: str) -> None:
            self.sent.append(payload)

    server = LibrarianServer()
    socket = FakeSocket()
    asyncio.run(server.handle_message(socket, {
        "type": "task_request",
        "url": "https://v.douyin.com/test/",
    }))
    assert socket.sent
    reply = json.loads(socket.sent[-1])
    assert reply["type"] == "task_rejected"
    assert reply["reason"] == "extension_task_trigger_deferred"


def main() -> int:
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        test_config_loads(tmp)
        test_netscape_cookie_conversion(tmp)
        test_vault_write_schema(tmp)
        test_websocket_config_writer(tmp)
        test_vault_discovery_is_strict(tmp)
        test_analyzer_ark_file_protocol(tmp)
        test_analyzer_wait_and_stream_protocol(tmp)
        test_model_health_check_redacts_secret(tmp)
        test_model_health_status_persists(tmp)
        test_codex_handoff_is_marked_archived()
        test_websocket_rejects_task_request(tmp)
    print("P0 static checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
