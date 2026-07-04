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
response_timeout_sec = 900

[douyin]
cookie_path = "{tmp / 'runtime' / 'cookie' / 'douyin.txt'}"

[vault]
path = "{vault}"
relative_root = "知识资产/知识入库"

[server]
enabled = true
host = "127.0.0.1"
port = 8765
""",
        encoding="utf-8",
    )
    cfg = load_config(config)
    assert cfg.vault_path == vault.resolve()
    assert cfg.vault_relative_root == "知识资产/知识入库"
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


def test_douyin_share_text_url_extraction() -> None:
    import sys

    sys.path.insert(0, str(SCRIPTS))
    from downloader import extract_url

    share_text = (
        "1.28 U@y.GI :8pm qeO:/ 07/08 Codex剪辑视频成片效果？耗时多久？"
        "消耗多少token？ 测试下来很多人抱怨的Codex剪辑慢+消耗Token多，"
        "主要是走的computer use方案，通过截图分析剪映界面再点击拖慢了节奏，"
        "增加了Token消耗，其实codex完全可以用插件和代码自己剪视频..."
        "# Codex剪视频 https://v.douyin.com/lo0FabXJhtk/ 复制此链接，"
        "打开Dou音搜索，直接观看视频！"
    )

    assert extract_url(share_text) == "https://v.douyin.com/lo0FabXJhtk/"
    assert extract_url(
        "图文分享 https://www.douyin.com/share/note/7654771261239701883/?foo=bar 复制"
    ) == "https://www.douyin.com/share/note/7654771261239701883/?foo=bar"


def test_ingest_url_preserves_share_text_argument() -> None:
    import importlib.util
    import subprocess
    import sys

    sys.path.insert(0, str(ROOT))
    import install.bootstrap as bootstrap_module

    share_text = (
        "1.28 U@y.GI :8pm qeO:/ 07/08 Codex剪辑视频成片效果？"
        " https://v.douyin.com/lo0FabXJhtk/ 复制此链接，打开Dou音搜索"
    )
    calls = []

    def fake_bootstrap(*args, **kwargs):
        return SimpleNamespace(actions=[], warnings=[], missing_user_actions=[])

    def fake_select_runtime_python():
        return Path("/usr/bin/python3")

    def fake_run(cmd, cwd):
        calls.append((cmd, cwd))
        return SimpleNamespace(returncode=0)

    spec = importlib.util.spec_from_file_location(
        "ingest_url_for_test",
        ROOT / "scripts" / "ingest_url.py",
    )
    assert spec and spec.loader
    ingest_url = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ingest_url)

    old_bootstrap = bootstrap_module.bootstrap
    old_select = bootstrap_module.select_runtime_python
    old_run = subprocess.run
    try:
        bootstrap_module.bootstrap = fake_bootstrap
        bootstrap_module.select_runtime_python = fake_select_runtime_python
        subprocess.run = fake_run
        assert ingest_url.main([share_text]) == 0
    finally:
        bootstrap_module.bootstrap = old_bootstrap
        bootstrap_module.select_runtime_python = old_select
        subprocess.run = old_run

    assert calls
    cmd, cwd = calls[0]
    assert cmd[3] == share_text
    assert cmd[4:] == ["--quality", "quality", "--intent", "knowledge_ingest"]
    assert cwd == ROOT / "deps" / "douyin"


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


@dataclass
class FakeImageResult:
    text: str = "这组图文介绍了一个 AI 求职工具，突出简历模板和职位投递流程。"
    file_id: str = "inline-images"
    quality: str = "quality"
    model: str = "doubao-seed-2-0-lite-260428"
    image_count: int = 2
    usage: dict | None = None
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
        vault_relative_root="知识资产/知识入库",
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
    assert "知识资产/知识入库" in str(md_path)
    text = md_path.read_text(encoding="utf-8")
    assert re.search(r'^id: "?\d{8}-knowledge-\d{3}"?$', text, re.MULTILINE)
    assert "type: video_analysis" in text
    assert "asset_family: knowledge_asset" in text
    assert "source_media: douyin_video" in text
    assert "ingest_intent: knowledge_ingest" in text
    assert "source_url:" in text
    assert "tags: [douyin, knowledge-asset, case-study, video-analysis]" in text
    index = vault / "index.md"
    assert index.exists()
    index_text = index.read_text(encoding="utf-8")
    assert "## 知识入库" in index_text
    assert "[[" in index_text
    assert git_status in {"committed", "no changes to commit"}
    assert (vault / ".git").exists()


def test_image_post_metadata_detection_from_image_infos() -> None:
    import sys

    sys.path.insert(0, str(SCRIPTS))
    from downloader import _extract_video_meta

    meta = _extract_video_meta(
        "7654771261239701883",
        {
            "aweme_detail": {
                "aweme_type": 68,
                "media_type": 2,
                "desc": "图文作品标题",
                "author": {"nickname": "Tester", "sec_uid": "sec"},
                "image_infos": [
                    {"display_image": {"url_list": ["https://example.com/01.jpg"]}},
                    {"nested": {"download_url_list": ["https://example.com/02.webp"]}},
                ],
                "video": {
                    "play_addr": {"url_list": ["https://example.com/background.m4a"]},
                },
            }
        },
        "https://www.douyin.com/video/7654771261239701883",
    )

    assert meta.media_type == "image_post"
    assert meta.play_url == ""
    assert meta.image_urls == [
        "https://example.com/01.jpg",
        "https://example.com/02.webp",
    ]


def test_image_post_without_image_urls_fails_clearly() -> None:
    import sys

    sys.path.insert(0, str(SCRIPTS))
    from downloader import DouyinError, _extract_video_meta

    try:
        _extract_video_meta(
            "7654771261239701883",
            {
                "aweme_detail": {
                    "aweme_type": 68,
                    "media_type": 2,
                    "desc": "图文作品标题",
                    "author": {"nickname": "Tester", "sec_uid": "sec"},
                    "image_infos": [{}],
                    "video": {
                        "play_addr": {"url_list": ["https://example.com/background.m4a"]},
                    },
                }
            },
            "https://www.douyin.com/video/7654771261239701883",
        )
    except DouyinError as e:
        assert "识别为图文作品但未提取到图片 URL" in str(e)
    else:
        raise AssertionError("image posts without image URLs must not fall back to video")


def test_analyzer_image_post_payload(tmp: Path) -> None:
    import asyncio
    import sys

    sys.path.insert(0, str(SCRIPTS))
    import analyzer

    image1 = tmp / "01.jpg"
    image2 = tmp / "02.png"
    image1.write_bytes(b"fake-jpg")
    image2.write_bytes(b"fake-png")

    class Usage:
        def model_dump(self) -> dict:
            return {"input_tokens": 10, "output_tokens": 20, "total_tokens": 30}

    class FakeResponses:
        def __init__(self) -> None:
            self.kwargs = None

        def create(self, **kwargs):
            self.kwargs = kwargs
            return SimpleNamespace(
                output=[{
                    "content": [{
                        "type": "output_text",
                        "text": "图文分析结果",
                    }],
                }],
                usage=Usage(),
            )

    fake_responses = FakeResponses()
    old_build_client = analyzer._build_client
    analyzer._build_client = lambda api_key, endpoint: SimpleNamespace(responses=fake_responses)
    try:
        result = asyncio.run(analyzer.analyze_images(
            [image1, image2],
            "请拆解图文",
            api_key="test",
            endpoint="https://ark.cn-beijing.volces.com/api/v3",
            model="doubao-seed-2-0-lite-260428",
        ))
    finally:
        analyzer._build_client = old_build_client

    assert result.text == "图文分析结果"
    assert result.image_count == 2
    assert result.usage["total_tokens"] == 30
    content = fake_responses.kwargs["input"][0]["content"]
    assert content[0]["type"] == "input_image"
    assert content[0]["image_url"].startswith("data:image/jpeg;base64,")
    assert content[1]["type"] == "input_image"
    assert content[1]["image_url"].startswith("data:image/png;base64,")
    assert content[2] == {"type": "input_text", "text": "请拆解图文"}


def test_image_post_vault_write_schema(tmp: Path) -> None:
    import sys

    sys.path.insert(0, str(SCRIPTS))
    from config_loader import Config
    from ingest import write_image_post_to_vault

    vault = tmp / "image-vault"
    vault.mkdir()
    runtime = tmp / "image-runtime"
    runtime.mkdir()
    image1 = tmp / "01.jpg"
    image2 = tmp / "02.png"
    image1.write_bytes(b"fake-jpg")
    image2.write_bytes(b"fake-png")

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
        vault_relative_root="知识资产/知识入库",
        server_enabled=True,
        server_host="127.0.0.1",
        server_port=8765,
        config_file=runtime / "config.toml",
    )

    md_path, git_status = write_image_post_to_vault(
        cfg,
        FakeMeta(
            title=(
                "抖音图文拆解测试 这是一个特别长的标题，用来验证图文入库时不会把"
                "整段分享文案写进 frontmatter 和 index\n第二行不应该进入标题"
            ),
            duration_sec=0,
        ),
        [image1, image2],
        FakeImageResult(),
        {"input_tokens": 1, "output_tokens": 2, "total_tokens": 3, "cost_rmb_estimate": 0.1},
    )

    assert md_path.exists()
    assert "知识资产/知识入库" in str(md_path)
    text = md_path.read_text(encoding="utf-8")
    assert re.search(r'^id: "?\d{8}-knowledge-\d{3}"?$', text, re.MULTILINE)
    assert "type: image_post_analysis" in text
    assert "asset_family: knowledge_asset" in text
    assert "source_media: douyin_image_post" in text
    assert "ingest_intent: knowledge_ingest" in text
    assert "tags: [douyin, knowledge-asset, case-study, image-analysis]" in text
    assert "![[raw/images/" in text
    assert (vault / "raw" / "images").exists()
    index_text = (vault / "index.md").read_text(encoding="utf-8")
    assert "## 知识入库" in index_text
    assert "`#knowledge-asset`" in index_text
    assert "`#image-analysis`" in index_text
    assert "第二行不应该进入标题" not in index_text
    assert all(line.count("[[") == line.count("]]") for line in index_text.splitlines())
    assert git_status in {"committed", "no changes to commit"}


def test_vault_slug_preserves_chinese_title() -> None:
    import sys

    sys.path.insert(0, str(SCRIPTS))
    from ingest import _slug_for_vault

    slug = _slug_for_vault(
        "趁Ai真正的爆发期还没有到来，努力成为那万分之四吧 #Ai新星计划",
        "7657727877504437538",
    )
    assert slug == "趁ai真正的爆发期还没有到来-努力成为那万分之四吧-ai新星计划-437538"
    assert "ai-ai" not in slug

    cleaned = _slug_for_vault("A/B:C*D?E\"F<G> #Tag", "123456789")
    assert cleaned == "a-b-c-d-e-f-g-tag-456789"

    assert _slug_for_vault("//// ####", "123456789") == "untitled-456789"


def test_summary_skips_markdown_section_headings() -> None:
    import sys

    sys.path.insert(0, str(SCRIPTS))
    from ingest import _summary_from_text

    text = """
## 一、全片一句话概括（≤ 40 字）
博主分享Codex操控剪映的高效方法，解答观众疑问。

## 二、结构拆解
"""
    assert _summary_from_text(text, "fallback") == "博主分享Codex操控剪映的高效方法，解答观众疑问。"


def test_vault_write_uses_intent_relative_root(tmp: Path) -> None:
    import sys

    sys.path.insert(0, str(SCRIPTS))
    from config_loader import Config
    from ingest import write_to_vault

    vault = tmp / "custom-root-vault"
    vault.mkdir()
    runtime = tmp / "custom-root-runtime"
    runtime.mkdir()
    video = tmp / "video.mp4"
    video.write_bytes(b"fake-video")
    relative_root = "知识资产/自定义旧目录"

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
        vault_relative_root=relative_root,
        server_enabled=True,
        server_host="127.0.0.1",
        server_port=8765,
        config_file=runtime / "config.toml",
    )
    md_path, _ = write_to_vault(
        cfg,
        FakeMeta(),
        video,
        FakeResult(),
        {"input_tokens": 1, "output_tokens": 2, "total_tokens": 3, "cost_rmb_estimate": 0.1},
        "viral_breakdown",
    )

    assert md_path.parent == vault / "知识资产" / "创作模式"
    text = md_path.read_text(encoding="utf-8")
    assert "asset_family: creative_pattern" in text
    assert "ingest_intent: viral_breakdown" in text


def test_run_task_multi_intent_reuses_one_download_and_writes_two_assets(tmp: Path) -> None:
    import asyncio
    import sys

    sys.path.insert(0, str(SCRIPTS))
    import ingest
    from config_loader import Config

    vault = tmp / "multi-intent-vault"
    vault.mkdir()
    runtime = tmp / "multi-intent-runtime"
    runtime.mkdir()
    cache = tmp / "cache"
    cache.mkdir()
    video = tmp / "video.mp4"
    video.write_bytes(b"fake-video")
    calls = []

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
        vault_relative_root="知识资产/知识入库",
        server_enabled=True,
        server_host="127.0.0.1",
        server_port=8765,
        config_file=runtime / "config.toml",
    )

    class FakeStatusWriter:
        def update(self, **fields):
            calls.append(("status", fields.get("stage"), fields.get("ingest_intents")))

        def progress(self, stage, info):
            calls.append(("progress", stage, info.get("intent")))

    async def fake_fetch_metadata(url, cookie_path):
        calls.append(("fetch_metadata", url))
        return FakeMeta()

    async def fake_download_video(meta, cache_dir, progress_cb=None):
        calls.append(("download_video", meta.aweme_id))
        return video

    async def fake_analyze_video_many(video_path, prompts, **kwargs):
        calls.append(("analyze_video_many", tuple(prompts)))
        return {
            intent: SimpleNamespace(
                text=f"{intent} 输出",
                file_id="file-one-upload",
                fps_used=1.0,
                quality="quality",
                model=cfg.analyzer_model,
                duration_sec=61,
                target_frames=1250,
                actual_frames_estimate=61,
                usage={"input_tokens": 1, "output_tokens": 2, "total_tokens": 3},
                truncated=False,
            )
            for intent in prompts
        }

    def fake_cost(model, usage):
        return {
            "input_tokens": usage["input_tokens"],
            "output_tokens": usage["output_tokens"],
            "total_tokens": usage["total_tokens"],
            "cost_rmb_estimate": 0.01,
            "model": model,
        }

    def fake_write(config, meta, video_path, result, cost, ingest_intent):
        calls.append(("write_to_vault", ingest_intent))
        md_path = config.vault_path / "知识资产" / ingest_intent / "fake.md"
        md_path.parent.mkdir(parents=True, exist_ok=True)
        md_path.write_text("# fake", encoding="utf-8")
        return md_path, "committed"

    originals = {
        name: getattr(ingest, name)
        for name in [
            "fetch_metadata",
            "download_video",
            "analyze_video_many",
            "estimate_cost_rmb",
            "write_to_vault",
        ]
    }
    try:
        ingest.fetch_metadata = fake_fetch_metadata
        ingest.download_video = fake_download_video
        ingest.analyze_video_many = fake_analyze_video_many
        ingest.estimate_cost_rmb = fake_cost
        ingest.write_to_vault = fake_write
        summary = asyncio.run(ingest.run_task(
            task_id="multi-intent",
            url="https://v.douyin.com/test/",
            quality="quality",
            ingest_intents=("knowledge_ingest", "viral_breakdown"),
            config=cfg,
            sw=FakeStatusWriter(),
            cache_dir=cache,
        ))
    finally:
        for name, value in originals.items():
            setattr(ingest, name, value)

    assert sum(1 for call in calls if call[0] == "download_video") == 1
    assert ("analyze_video_many", ("knowledge_ingest", "viral_breakdown")) in calls
    assert ("write_to_vault", "knowledge_ingest") in calls
    assert ("write_to_vault", "viral_breakdown") in calls
    assert len(summary["assets"]) == 2
    assert summary["ingest_intents"] == ["knowledge_ingest", "viral_breakdown"]
    assert summary["analysis"]["file_id"] == "file-one-upload"


def test_analyzer_rejects_empty_response_text(tmp: Path) -> None:
    import asyncio
    import sys

    sys.path.insert(0, str(SCRIPTS))
    import analyzer

    video = tmp / "video.mp4"
    video.write_bytes(b"fake-video")

    async def fake_upload(*args, **kwargs):
        return SimpleNamespace(id="file-test")

    async def fake_wait(*args, **kwargs):
        return SimpleNamespace(status="active")

    async def fake_stream(*args, **kwargs):
        return "", {}

    old_duration = analyzer.get_duration_sec
    old_build_client = analyzer._build_client
    old_upload = analyzer._upload_with_preprocess
    old_wait = analyzer._wait_for_active
    old_stream = analyzer._stream_responses
    try:
        analyzer.get_duration_sec = lambda path: 10.0
        analyzer._build_client = lambda api_key, endpoint: SimpleNamespace()
        analyzer._upload_with_preprocess = fake_upload
        analyzer._wait_for_active = fake_wait
        analyzer._stream_responses = fake_stream
        try:
            asyncio.run(analyzer.analyze_video(
                video,
                "prompt",
                api_key="key",
                endpoint="https://ark.cn-beijing.volces.com/api/v3",
                model="doubao-seed-2-0-lite-260428",
            ))
        except analyzer.APIError as e:
            assert "未返回可写入" in str(e)
        else:
            raise AssertionError("empty analyzer output should fail")
    finally:
        analyzer.get_duration_sec = old_duration
        analyzer._build_client = old_build_client
        analyzer._upload_with_preprocess = old_upload
        analyzer._wait_for_active = old_wait
        analyzer._stream_responses = old_stream


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
    assert cfg.vault_relative_root == "知识资产/知识入库"
    assert cfg.default_quality == "quality"
    assert cfg.quality_target_frames == 1250
    assert cfg.fps_min == 0.2
    assert cfg.fps_max == 5.0
    assert cfg.response_timeout_sec == 900

    assert oct((tmp / "ws-runtime" / "config.toml").stat().st_mode & 0o777) == "0o600"

    weak_status = asyncio.run(server.handle_cookie_update(
        "douyin",
        ".douyin.com\tTRUE\t/\tTRUE\t0\ta\tb",
    ))
    cookie_path = tmp / "ws-runtime" / "cookie" / "douyin.txt"
    assert cookie_path.exists()
    assert oct(cookie_path.stat().st_mode & 0o777) == "0o600"
    assert weak_status["state"] == "incomplete"
    assert weak_status["cookieCount"] == 1

    full_cookie = "\n".join([
        ".douyin.com\tTRUE\t/\tTRUE\t0\tmsToken\tplaceholder",
        ".douyin.com\tTRUE\t/\tTRUE\t0\tttwid\tplaceholder",
        ".douyin.com\tTRUE\t/\tTRUE\t0\ts_v_web_id\tplaceholder",
        ".douyin.com\tTRUE\t/\tTRUE\t0\tsessionid\tplaceholder",
        ".douyin.com\tTRUE\t/\tTRUE\t0\tsid_guard\tplaceholder",
        ".douyin.com\tTRUE\t/\tTRUE\t0\tuid_tt\tplaceholder",
    ])
    ready_status = asyncio.run(server.handle_cookie_update("douyin", full_cookie))
    assert ready_status["state"] == "ready"
    assert ready_status["cookieCount"] == 6


def test_quality_fps_stays_5_until_safe_frame_target() -> None:
    import sys

    sys.path.insert(0, str(SCRIPTS))
    from analyzer import calc_fps

    fps, target, truncated = calc_fps(250, "quality")
    assert fps == 5.0
    assert target == 1250
    assert truncated is False

    fps, target, truncated = calc_fps(251, "quality")
    assert fps == 4.98
    assert target == 1250
    assert truncated is False

    fps, target, truncated = calc_fps(300, "quality")
    assert fps == 4.16
    assert target == 1250
    assert truncated is False


def test_video_chunk_threshold_and_memory_store(tmp: Path) -> None:
    import sys
    import time

    os.environ["OBSIDIAN_LIBRARIAN_HOME"] = str(tmp / "memory-runtime")
    sys.path.insert(0, str(SCRIPTS))
    import analyzer

    assert analyzer.should_chunk_video(600) is False
    assert analyzer.should_chunk_video(601) is True
    plan = analyzer._chunk_plan(601)
    assert len(plan) == 3
    assert plan[0]["start_sec"] == 0
    assert plan[1]["start_sec"] == 230
    assert plan[1]["overlap_sec"] == 10.0

    analyzer.save_response_memory(
        media_type="douyin_video",
        source_id="aweme-1",
        ingest_intent="knowledge_ingest",
        model="model-a",
        response_id="resp-knowledge",
        file_id="file-a",
    )
    analyzer.save_response_memory(
        media_type="douyin_video",
        source_id="aweme-1",
        ingest_intent="viral_breakdown",
        model="model-a",
        response_id="resp-viral",
        file_id="file-a",
    )
    knowledge = analyzer.load_response_memory(
        media_type="douyin_video",
        source_id="aweme-1",
        ingest_intent="knowledge_ingest",
        model="model-a",
    )
    viral = analyzer.load_response_memory(
        media_type="douyin_video",
        source_id="aweme-1",
        ingest_intent="viral_breakdown",
        model="model-a",
    )
    assert knowledge and knowledge["response_id"] == "resp-knowledge"
    assert viral and viral["response_id"] == "resp-viral"
    files = list((tmp / "memory-runtime" / "responses-memory").glob("*.json"))
    assert len(files) == 2
    text = "\n".join(path.read_text(encoding="utf-8") for path in files)
    assert "api_key" not in text.lower()
    assert "cookie" not in text.lower()

    stale = files[0]
    payload = json.loads(stale.read_text(encoding="utf-8"))
    payload["updated_at"] = time.time() - 999
    stale.write_text(json.dumps(payload), encoding="utf-8")
    assert analyzer.load_response_memory(
        media_type=payload["media_type"],
        source_id=payload["source_id"],
        ingest_intent=payload["ingest_intent"],
        model=payload["model"],
        ttl_sec=1,
    ) is None


def test_long_video_strategy_validation_falls_back_to_5fps() -> None:
    import sys

    sys.path.insert(0, str(SCRIPTS))
    import analyzer

    plan = analyzer._chunk_plan(601)
    strategy = analyzer._normalize_long_video_strategy("not json", plan)

    assert strategy["ok"] is False
    assert "JSON" in strategy["fallback_reason"]
    assert len(strategy["chunks"]) == len(plan)
    assert all(item["recommended_fps"] == 5.0 for item in strategy["chunks"])
    assert all(item["fallback_applied"] is True for item in strategy["chunks"])

    valid = analyzer._normalize_long_video_strategy(
        json.dumps({
            "overview": {
                "summary": "一条长视频，前半段讲背景，后半段演示操作。",
                "timeline": [],
                "important_points": ["操作演示"],
                "uncertain_points": [],
            },
            "strategy": {
                "global_notes": "多数片段较稳定，但第二段操作密集。",
                "segments": [
                    {
                        "part_index": 1,
                        "start_sec": 0,
                        "end_sec": 240,
                        "rough_summary": "背景说明",
                        "recommended_fps": 2,
                        "confidence": 0.9,
                        "scores": {
                            "visual_change": 1,
                            "ocr_subtitle_density": 1,
                            "operation_density": 0,
                            "motion_detail": 0,
                            "concept_density": 2,
                            "risk_if_low_fps": 1,
                        },
                        "evidence": ["固定机位，画面变化低"],
                        "focus": ["核心结论"],
                        "risk_flags": [],
                        "why_not_lower_fps": "2fps 已能覆盖慢变化画面",
                    },
                    {
                        "part_index": 2,
                        "start_sec": 230,
                        "end_sec": 470,
                        "rough_summary": "软件操作演示",
                        "recommended_fps": 2,
                        "confidence": 0.6,
                        "scores": {
                            "visual_change": 3,
                            "ocr_subtitle_density": 3,
                            "operation_density": 5,
                            "motion_detail": 3,
                            "concept_density": 3,
                            "risk_if_low_fps": 5,
                        },
                        "evidence": ["多处界面操作"],
                        "focus": ["菜单和按钮"],
                        "risk_flags": ["低 fps 可能漏步骤"],
                        "why_not_lower_fps": "操作密集",
                    },
                    {
                        "part_index": 3,
                        "start_sec": 460,
                        "end_sec": 601,
                        "rough_summary": "总结",
                        "recommended_fps": 3,
                        "confidence": 0.7,
                        "scores": {
                            "visual_change": 1,
                            "ocr_subtitle_density": 2,
                            "operation_density": 0,
                            "motion_detail": 0,
                            "concept_density": 2,
                            "risk_if_low_fps": 1,
                        },
                        "evidence": ["字幕较清楚"],
                        "focus": ["结论"],
                        "risk_flags": [],
                        "why_not_lower_fps": "需要确认字幕",
                    },
                ],
            },
        }, ensure_ascii=False),
        plan,
    )

    assert valid["ok"] is True
    assert valid["chunks"][0]["recommended_fps"] == 2.0
    assert valid["chunks"][1]["recommended_fps"] == 5.0
    assert valid["chunks"][1]["fallback_applied"] is True
    assert valid["chunks"][2]["recommended_fps"] == 4.0


def test_chunk_analysis_uses_strategy_fps_and_context(tmp: Path) -> None:
    import asyncio
    import sys

    os.environ["OBSIDIAN_LIBRARIAN_HOME"] = str(tmp / "chunk-strategy-runtime")
    sys.path.insert(0, str(SCRIPTS))
    import analyzer

    chunk_paths = [tmp / "part-001.mp4", tmp / "part-002.mp4"]
    for path in chunk_paths:
        path.write_bytes(b"fake")
    plan = [
        {"part_index": 1, "start_sec": 0.0, "end_sec": 240.0, "overlap_sec": 0.0},
        {"part_index": 2, "start_sec": 230.0, "end_sec": 470.0, "overlap_sec": 10.0},
    ]
    strategy = {
        "ok": True,
        "overview": {
            "summary": "全片先讲背景，再演示操作。",
            "timeline": [],
            "important_points": ["操作步骤"],
            "uncertain_points": [],
        },
        "global_notes": "第二段需要更高 fps。",
        "chunks": [
            {
                **plan[0],
                "recommended_fps": 2.0,
                "confidence": 0.9,
                "scores": {"risk_if_low_fps": 1},
                "rough_summary": "背景说明",
                "evidence": ["固定画面"],
                "focus": ["结论"],
                "risk_flags": [],
                "why_not_lower_fps": "2fps 足够",
                "fallback_applied": False,
                "fallback_reason": "",
            },
            {
                **plan[1],
                "recommended_fps": 5.0,
                "confidence": 0.8,
                "scores": {"risk_if_low_fps": 5},
                "rough_summary": "密集操作",
                "evidence": ["多处点击"],
                "focus": ["按钮和菜单"],
                "risk_flags": ["可能漏步骤"],
                "why_not_lower_fps": "操作密集",
                "fallback_applied": False,
                "fallback_reason": "",
            },
        ],
    }
    uploads = []
    prompts = []

    async def fake_upload(client, path, *, fps, model):
        uploads.append((Path(path).name, fps))
        return SimpleNamespace(id=f"file-{len(uploads)}")

    async def fake_wait(*args, **kwargs):
        return SimpleNamespace(status="active")

    async def fake_stream(client, *, model, file_id, prompt, on_progress, previous_response_id=None, timeout_sec=None):
        prompts.append(prompt)
        return analyzer.ResponseCallResult(
            text=f"{file_id} 分析结果",
            usage={"total_tokens": 1},
            response_id=f"resp-{file_id}",
        )

    async def fake_text(client, *, model, prompt, on_progress, previous_response_id=None, timeout_sec=None):
        prompts.append(prompt)
        return analyzer.ResponseCallResult(
            text="最终汇总",
            usage={"total_tokens": 1},
            response_id="resp-final",
        )

    old_upload = analyzer._upload_with_preprocess
    old_wait = analyzer._wait_for_active
    old_stream = analyzer._stream_responses
    old_text = analyzer._call_text_responses
    try:
        analyzer._upload_with_preprocess = fake_upload
        analyzer._wait_for_active = fake_wait
        analyzer._stream_responses = fake_stream
        analyzer._call_text_responses = fake_text
        results = asyncio.run(analyzer._analyze_video_chunks(
            chunk_paths,
            plan,
            {"knowledge_ingest": "基础拆解 prompt"},
            files_client=SimpleNamespace(),
            responses_client=SimpleNamespace(),
            model="doubao-seed-2-0-lite-260428",
            quality="quality",
            full_duration=470.0,
            source_id="aweme-strategy",
            strategy=strategy,
            file_active_timeout_sec=120,
            response_timeout_sec=900,
            on_progress=None,
        ))
    finally:
        analyzer._upload_with_preprocess = old_upload
        analyzer._wait_for_active = old_wait
        analyzer._stream_responses = old_stream
        analyzer._call_text_responses = old_text

    assert uploads == [("part-001.mp4", 2.0), ("part-002.mp4", 5.0)]
    assert "全片概览" in prompts[0]
    assert "本段精拆策略" in prompts[0]
    assert "第二段需要更高 fps" in prompts[-1]
    result = results["knowledge_ingest"]
    assert result.chunked is True
    assert result.fps_used == 5.0
    assert result.actual_frames_estimate == 1680
    assert [item["fps"] for item in result.chunks] == [2.0, 5.0]
    assert result.chunks[1]["strategy_focus"] == ["按钮和菜单"]


def test_websocket_config_writer_rejects_agent_plan_payload_key(tmp: Path) -> None:
    import asyncio
    import sys

    runtime = tmp / "ws-runtime-plan"
    os.environ["OBSIDIAN_LIBRARIAN_HOME"] = str(runtime)
    sys.path.insert(0, str(ROOT / "server"))
    from websocket_server import LibrarianServer
    from config_loader import load_config, ConfigError

    vault = tmp / "ws-vault-plan"
    vault.mkdir()
    (vault / ".obsidian").mkdir()
    (vault / "index.md").write_text("# 知识库索引\n", encoding="utf-8")

    server = LibrarianServer()
    asyncio.run(server.handle_config_update({
        "provider": "volcengine_agent_plan",
        "apiKey": "plan-key",
        "agentPlanApiKey": "plan-key",
        "agentPlanEndpoint": "https://ark.cn-beijing.volces.com/api/plan/v3",
        "model": "doubao-seed-2.0-lite",
        "vaultPath": str(vault),
    }))

    config_path = runtime / "config.toml"
    text = config_path.read_text(encoding="utf-8")
    assert "[agent_plan]" not in text
    assert "plan-key" not in text
    assert 'api_key = ""' in text
    try:
        load_config(config_path)
    except ConfigError as e:
        assert "Agent Plan Key 不会自动当作普通 Ark Key 使用" in str(e)
    else:
        raise AssertionError("old Agent Plan payload must not become valid Ark config")


def test_websocket_config_writer_uses_explicit_ark_key_when_old_provider_present(tmp: Path) -> None:
    import asyncio
    import sys

    runtime = tmp / "ws-runtime-plan-fallback"
    os.environ["OBSIDIAN_LIBRARIAN_HOME"] = str(runtime)
    sys.path.insert(0, str(ROOT / "server"))
    from websocket_server import LibrarianServer
    from config_loader import load_config

    vault = tmp / "ws-vault-plan-fallback"
    vault.mkdir()
    (vault / ".obsidian").mkdir()
    (vault / "index.md").write_text("# 知识库索引\n", encoding="utf-8")

    server = LibrarianServer()
    asyncio.run(server.handle_config_update({
        "provider": "volcengine_agent_plan",
        "arkApiKey": "normal-ark-key",
        "apiKey": "",
        "model": "doubao-seed-2.0-lite",
        "vaultPath": str(vault),
    }))

    cfg = load_config(runtime / "config.toml")
    assert cfg.provider == "doubao"
    assert cfg.ark_api_key == "normal-ark-key"
    assert cfg.files_api_key == "normal-ark-key"
    assert cfg.files_endpoint == "https://ark.cn-beijing.volces.com/api/v3"
    assert cfg.analyzer_model == "doubao-seed-2-0-lite-260428"


def test_config_loader_does_not_use_agent_plan_section(tmp: Path) -> None:
    import sys

    runtime = tmp / "dual-key-runtime"
    os.environ["OBSIDIAN_LIBRARIAN_HOME"] = str(runtime)
    sys.path.insert(0, str(SCRIPTS))
    from config_loader import load_config

    vault = tmp / "dual-key-vault"
    vault.mkdir()
    runtime.mkdir(parents=True)
    config = runtime / "config.toml"
    config.write_text(
        f"""
[ark]
api_key = "normal-ark-key"
endpoint = "https://ark.cn-beijing.volces.com/api/v3"

[agent_plan]
api_key = "plan-key"
endpoint = "https://ark.cn-beijing.volces.com/api/plan/v3"

[provider]
active = "volcengine_agent_plan"

[models]
analyzer = "doubao-seed-2.0-lite"
analyzer_fallback = "doubao-seed-2.0-mini"

[analysis]
default_quality = "quality"
balanced_target_frames = 240
quality_target_frames = 1250
fps_min = 0.2
fps_max = 5.0
file_active_timeout_sec = 120

[douyin]
cookie_path = "{runtime / 'cookie' / 'douyin.txt'}"

[vault]
path = "{vault}"
relative_root = "知识资产/知识入库"

[server]
enabled = true
host = "127.0.0.1"
port = 8765
""",
        encoding="utf-8",
    )

    cfg = load_config(config)
    assert cfg.provider == "doubao"
    assert cfg.ark_api_key == "normal-ark-key"
    assert cfg.ark_endpoint == "https://ark.cn-beijing.volces.com/api/v3"
    assert cfg.files_api_key == "normal-ark-key"
    assert cfg.files_endpoint == "https://ark.cn-beijing.volces.com/api/v3"
    assert cfg.agent_plan_api_key == "plan-key"


def test_model_health_check_ignores_old_agent_plan_provider(tmp: Path) -> None:
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
            "arkApiKey": "normal-health-key",
            "apiKey": "plan-health-key",
            "model": "doubao-seed-2.0-lite",
            "endpoint": "https://ark.cn-beijing.volces.com/api/v3",
        })
    finally:
        websocket_server.urllib.request.urlopen = old_urlopen

    assert status["ok"] is True
    assert status["state"] == "ready"
    assert calls
    request, timeout = calls[0]
    assert request.full_url == "https://ark.cn-beijing.volces.com/api/v3/tokenization"
    assert timeout == 10
    assert b'"text": "ping"' in request.data
    assert "normal-health-key" in request.headers.get("Authorization", "")
    assert "plan-health-key" not in request.headers.get("Authorization", "")


def test_vault_discovery_is_strict(tmp: Path) -> None:
    import sys

    sys.path.insert(0, str(ROOT))
    import install.vault_discovery as vault_discovery
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
    old_registry = vault_discovery._obsidian_registry_candidates
    old_cli = vault_discovery._obsidian_cli_candidates
    old_common = vault_discovery._common_roots
    try:
        vault_discovery._obsidian_registry_candidates = lambda: []
        vault_discovery._obsidian_cli_candidates = lambda: []
        vault_discovery._common_roots = lambda: []
        result = discover_vault(cwd=vault, user_hint=str(vault), runtime_root=tmp / "runtime")
    finally:
        vault_discovery._obsidian_registry_candidates = old_registry
        vault_discovery._obsidian_cli_candidates = old_cli
        vault_discovery._common_roots = old_common
    assert result.selected
    assert Path(result.selected.path) == vault.resolve()


def test_analyzer_ark_file_protocol(tmp: Path) -> None:
    import asyncio
    import sys

    sys.path.insert(0, str(SCRIPTS))
    import analyzer

    assert (
        analyzer._default_files_endpoint("https://ark.cn-beijing.volces.com/api/plan/v3")
        == "https://ark.cn-beijing.volces.com/api/v3"
    )
    assert (
        analyzer._default_files_endpoint("https://ark.cn-beijing.volces.com/api/v3")
        == "https://ark.cn-beijing.volces.com/api/v3"
    )

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

    safe_size = tmp / "500mb.mp4"
    with safe_size.open("wb") as f:
        f.truncate(500 * 1024 * 1024)
    assert analyzer._check_size(safe_size) == 500 * 1024 * 1024

    too_large = tmp / "501mb.mp4"
    with too_large.open("wb") as f:
        f.truncate(501 * 1024 * 1024)
    try:
        analyzer._check_size(too_large)
    except analyzer.FileTooLargeError:
        pass
    else:
        raise AssertionError("expected FileTooLargeError for >500MB video")


def test_analyzer_rejects_agent_plan_endpoint(tmp: Path) -> None:
    import asyncio
    import sys

    sys.path.insert(0, str(SCRIPTS))
    import analyzer

    video = tmp / "video.mp4"
    video.write_bytes(b"fake-video")

    try:
        asyncio.run(analyzer.analyze_video(
            video,
            "prompt",
            api_key="plan-key",
            endpoint="https://ark.cn-beijing.volces.com/api/plan/v3",
            model="doubao-seed-2.0-lite",
        ))
    except analyzer.AnalyzerError as e:
        assert "Agent Plan 不再作为运行通道" in str(e)
    else:
        raise AssertionError("Agent Plan endpoint must be rejected")


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
                SimpleNamespace(type="response.completed", response=SimpleNamespace(id="resp-stream", usage=Usage())),
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
    assert responses.kwargs["store"] is True
    content = responses.kwargs["input"][0]["content"]
    assert content[0] == {"type": "input_video", "file_id": "file-ready"}
    assert content[1] == {"type": "input_text", "text": "请拆解视频"}
    call = asyncio.run(
        analyzer._stream_responses(
            stream_client,
            model="doubao-seed-2-1-pro-260628",
            file_id="file-ready",
            prompt="继续拆解",
            on_progress=None,
            previous_response_id="resp-stream",
        )
    )
    assert call.response_id == "resp-stream"
    assert responses.kwargs["previous_response_id"] == "resp-stream"

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


def test_websocket_accepts_task_request(tmp: Path) -> None:
    import asyncio
    import json
    import sys

    os.environ["OBSIDIAN_LIBRARIAN_HOME"] = str(tmp / "ws-runtime-task")
    sys.path.insert(0, str(ROOT / "server"))
    from websocket_server import LibrarianServer

    class FakeSocket:
        def __init__(self) -> None:
            self.sent: list[str] = []

        async def send(self, payload: str) -> None:
            self.sent.append(payload)

    server = LibrarianServer(enable_task_runner=False)
    socket = FakeSocket()
    asyncio.run(server.handle_message(socket, {
        "type": "task_request",
        "requestId": "req-1",
        "source": "extension_inline_button",
        "taskType": "douyin_ingest",
        "ingest_intents": ["knowledge_ingest", "viral_breakdown"],
        "url": "https://www.douyin.com/video/7390000000000000000",
        "pageTitle": "测试视频",
    }))
    assert socket.sent
    reply = json.loads(socket.sent[-1])
    assert reply["type"] == "task_accepted"
    assert reply["requestId"] == "req-1"
    task_id = reply["task"]["id"]

    task_file = Path(os.environ["OBSIDIAN_LIBRARIAN_HOME"]) / "inbox" / f"{task_id}.json"
    status_file = Path(os.environ["OBSIDIAN_LIBRARIAN_HOME"]) / "status" / f"{task_id}.json"
    assert task_file.exists()
    task = json.loads(task_file.read_text(encoding="utf-8"))
    assert task["type"] == "douyin_ingest"
    assert task["ingest_intent"] == "knowledge_ingest"
    assert task["ingest_intents"] == ["knowledge_ingest", "viral_breakdown"]
    assert status_file.exists()
    status = json.loads(status_file.read_text(encoding="utf-8"))
    assert status["stage"] == "queued"
    assert status["source"] == "extension_inline_button"
    assert status["ingest_intent"] == "knowledge_ingest"
    assert status["ingest_intents"] == ["knowledge_ingest", "viral_breakdown"]

    snapshot = server.task_status_snapshot()
    assert snapshot["running"] == 1
    assert snapshot["items"][0]["id"] == task_id
    assert snapshot["items"][0]["stageLabel"] == "排队中"
    assert snapshot["items"][0]["ingestIntent"] == "knowledge_ingest"
    assert snapshot["items"][0]["ingestIntents"] == ["knowledge_ingest", "viral_breakdown"]


def test_websocket_rejects_invalid_ingest_intent(tmp: Path) -> None:
    import asyncio
    import sys

    os.environ["OBSIDIAN_LIBRARIAN_HOME"] = str(tmp / "ws-runtime-task-invalid")
    sys.path.insert(0, str(ROOT / "server"))
    from websocket_server import LibrarianServer

    server = LibrarianServer(enable_task_runner=False)
    reply = asyncio.run(server.handle_task_request({
        "type": "task_request",
        "requestId": "req-bad",
        "ingest_intent": "copywriting_only",
        "url": "https://www.douyin.com/video/7390000000000000000",
    }))
    assert reply["type"] == "task_rejected"
    assert reply["reason"] == "invalid_ingest_intent"


def main() -> int:
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        test_config_loads(tmp)
        test_netscape_cookie_conversion(tmp)
        test_douyin_share_text_url_extraction()
        test_ingest_url_preserves_share_text_argument()
        test_vault_write_schema(tmp)
        test_image_post_metadata_detection_from_image_infos()
        test_image_post_without_image_urls_fails_clearly()
        test_analyzer_image_post_payload(tmp)
        test_image_post_vault_write_schema(tmp)
        test_summary_skips_markdown_section_headings()
        test_vault_write_uses_intent_relative_root(tmp)
        test_run_task_multi_intent_reuses_one_download_and_writes_two_assets(tmp)
        test_analyzer_rejects_empty_response_text(tmp)
        test_websocket_config_writer(tmp)
        test_quality_fps_stays_5_until_safe_frame_target()
        test_video_chunk_threshold_and_memory_store(tmp)
        test_long_video_strategy_validation_falls_back_to_5fps()
        test_chunk_analysis_uses_strategy_fps_and_context(tmp)
        test_websocket_config_writer_rejects_agent_plan_payload_key(tmp)
        test_websocket_config_writer_uses_explicit_ark_key_when_old_provider_present(tmp)
        test_config_loader_does_not_use_agent_plan_section(tmp)
        test_vault_discovery_is_strict(tmp)
        test_analyzer_ark_file_protocol(tmp)
        test_analyzer_rejects_agent_plan_endpoint(tmp)
        test_analyzer_wait_and_stream_protocol(tmp)
        test_model_health_check_ignores_old_agent_plan_provider(tmp)
        test_model_health_check_redacts_secret(tmp)
        test_model_health_status_persists(tmp)
        test_codex_handoff_is_marked_archived()
        test_websocket_accepts_task_request(tmp)
        test_websocket_rejects_invalid_ingest_intent(tmp)
    print("P0 static checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
