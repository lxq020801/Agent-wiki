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
    os.environ["AGENT_WIKI_HOME"] = str(tmp / "runtime")
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
    assert cfg.strategy_model == "doubao-seed-2-0-mini-260428"


def test_config_loader_rejects_invalid_ark_endpoints(tmp: Path) -> None:
    import sys

    os.environ["AGENT_WIKI_HOME"] = str(tmp / "endpoint-runtime")
    sys.path.insert(0, str(SCRIPTS))
    from config_loader import ConfigError, load_config

    vault = tmp / "endpoint-vault"
    vault.mkdir()
    invalid_cases = [
        ("http://evil.example.invalid/api/v3", "HTTPS"),
        ("https://evil.example.invalid/api/v3", "可信 Ark 官方域名"),
        ("https://user:pass@ark.cn-beijing.volces.com/api/v3", "账号密码"),
        ("https://ark.cn-beijing.volces.com/api/plan/v3", "Agent Plan endpoint"),
    ]
    for index, (endpoint, expected) in enumerate(invalid_cases):
        config = tmp / f"endpoint-runtime-{index}" / "config.toml"
        config.parent.mkdir(parents=True)
        config.write_text(
            f"""
[ark]
api_key = "test-key"
endpoint = "{endpoint}"

[models]
analyzer = "doubao-seed-2-0-lite-260428"
analyzer_fallback = "doubao-seed-2-0-mini-260428"

[analysis]
default_quality = "quality"
balanced_target_frames = 240
quality_target_frames = 1250
fps_min = 0.2
fps_max = 5.0
file_active_timeout_sec = 120

[douyin]
cookie_path = "{config.parent / 'cookie' / 'douyin.txt'}"

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
        try:
            load_config(config)
        except ConfigError as e:
            assert expected in str(e)
        else:
            raise AssertionError(f"invalid endpoint must be rejected: {endpoint}")


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


def test_download_video_resumes_partial_file(tmp_path: Path) -> None:
    import asyncio
    import sys

    sys.path.insert(0, str(SCRIPTS))
    import downloader

    requests: list[dict[str, str]] = []

    class FakeHTTPError(Exception):
        pass

    class FakeStream:
        def __init__(
            self,
            status_code: int,
            headers: dict[str, str],
            chunks: list[bytes],
            *,
            fail_after_chunks: bool = False,
        ) -> None:
            self.status_code = status_code
            self.headers = headers
            self.chunks = chunks
            self.fail_after_chunks = fail_after_chunks

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def aiter_bytes(self, chunk_size: int):
            for chunk in self.chunks:
                yield chunk
            if self.fail_after_chunks:
                raise FakeHTTPError("peer closed connection without sending complete message body")

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def stream(self, method: str, url: str, headers: dict[str, str]):
            requests.append(dict(headers))
            if len(requests) == 1:
                return FakeStream(
                    200,
                    {"content-length": "6"},
                    [b"abc"],
                    fail_after_chunks=True,
                )
            return FakeStream(
                206,
                {"content-length": "3", "content-range": "bytes 3-5/6"},
                [b"def"],
            )

    fake_httpx = SimpleNamespace(
        AsyncClient=FakeAsyncClient,
        Timeout=lambda *args, **kwargs: object(),
        HTTPError=FakeHTTPError,
    )
    missing = object()
    old_httpx = sys.modules.get("httpx", missing)
    sys.modules["httpx"] = fake_httpx
    try:
        meta = downloader.VideoMeta(
            aweme_id="1234567890123456789",
            title="续传测试",
            author="author",
            author_sec_uid="sec",
            duration_sec=1,
            cover_url="",
            play_url="https://example.test/video.mp4",
            source_url="https://example.test/share",
            raw={},
        )

        out = asyncio.run(downloader.download_video(meta, tmp_path, timeout=1))
    finally:
        if old_httpx is missing:
            sys.modules.pop("httpx", None)
        else:
            sys.modules["httpx"] = old_httpx

    assert out.read_bytes() == b"abcdef"
    assert not out.with_suffix(".mp4.part").exists()
    assert requests[1]["Range"] == "bytes=3-"


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


def test_extension_sync_preserves_unchanged_files(tmp: Path) -> None:
    import sys

    sys.path.insert(0, str(ROOT))
    import install.bootstrap as bootstrap_module

    src = tmp / "chrome-extension-src"
    dest = tmp / "chrome-extension-dest"
    (src / "popup").mkdir(parents=True)
    (src / "manifest.json").write_text('{"manifest_version":3}\n', encoding="utf-8")
    (src / "background.js").write_text("console.log('ok');\n", encoding="utf-8")
    (src / "popup" / "popup.js").write_text("console.log('popup');\n", encoding="utf-8")
    (src / ".DS_Store").write_text("ignored", encoding="utf-8")

    copied, removed, unchanged = bootstrap_module._sync_extension_tree(src, dest)
    assert copied == 3
    assert removed == 0
    assert unchanged == 0
    assert not (dest / ".DS_Store").exists()

    target = dest / "background.js"
    before_content = target.read_text(encoding="utf-8")
    before_mtime = target.stat().st_mtime_ns
    (dest / "stale.js").write_text("old", encoding="utf-8")

    copied, removed, unchanged = bootstrap_module._sync_extension_tree(src, dest)
    assert copied == 0
    assert removed == 1
    assert unchanged == 3
    assert target.read_text(encoding="utf-8") == before_content
    assert target.stat().st_mtime_ns == before_mtime


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


def _derived_candidate_json(*, name: str = "LangGraph", url: str = "https://github.com/langchain-ai/langgraph") -> str:
    return json.dumps({
        "candidates": [
            {
                "name": name,
                "target_type": "github_project",
                "target_url": url,
                "subtype": "",
                "mentioned_context": "视频提到用它构建 Agent Harness 的状态图和人工确认节点。",
                "reason": "它是父笔记里方法能否复用的关键工具，需要沉淀成可执行项目资产。",
                "evidence": ["时间码[估算 320s]：字幕出现 LangGraph 和 GitHub 仓库名"],
                "confidence": 0.86,
                "requires_confirmation": True,
                "scores": {
                    "knowledge_value": 5,
                    "parent_dependency": 5,
                    "evidence_strength": 5,
                    "actionability": 5,
                    "freshness_risk": 4,
                    "novelty": 4,
                    "asset_fit": 5,
                    "cost_risk_inverse": 4,
                    "ambiguity_inverse": 5,
                },
            }
        ],
    }, ensure_ascii=False)


def test_derive_strategy_scores_limits_dedupes_and_redacts(tmp: Path) -> None:
    import sys

    os.environ["AGENT_WIKI_HOME"] = str(tmp / "derive-runtime")
    sys.path.insert(0, str(SCRIPTS))
    from derive_strategy import derive_tasks_from_analysis, public_derived_tasks

    vault = tmp / "derive-vault"
    existing = vault / "知识资产" / "GitHub项目"
    existing.mkdir(parents=True)
    (existing / "20260705-langgraph.md").write_text(
        "---\nrepo: langchain-ai/langgraph\n---\n",
        encoding="utf-8",
    )

    high_scores = {
        "knowledge_value": 5,
        "parent_dependency": 4,
        "evidence_strength": 5,
        "actionability": 5,
        "freshness_risk": 4,
        "novelty": 4,
        "asset_fit": 5,
        "cost_risk_inverse": 4,
        "ambiguity_inverse": 5,
    }
    candidates = [
        {
            "name": "LangGraph",
            "target_type": "github_project",
            "target_url": "https://github.com/langchain-ai/langgraph/tree/main?utm_source=douyin",
            "reason": "父视频用它解释 Agent Harness 状态图。",
            "evidence": ["时间码 03:20 出现 GitHub 仓库"],
            "scores": high_scores,
            "requires_confirmation": True,
        },
        {
            "name": "LangGraph duplicate",
            "target_type": "github_project",
            "target_url": "https://github.com/langchain-ai/langgraph/issues/1",
            "reason": "重复线索，分数更低，应被同源去重。",
            "evidence": ["重复 URL"],
            "scores": {**high_scores, "knowledge_value": 3},
            "requires_confirmation": True,
        },
    ]
    for index in range(9):
        candidates.append({
            "name": f"Official API Doc {index}",
            "target_type": "official_doc",
            "target_url": f"https://example.com/docs/api/{index}?utm_campaign=x",
            "subtype": "api_doc",
            "reason": f"第 {index} 个官方文档线索，用于核验 API 参数。",
            "evidence": [f"时间码 0{index}:10 出现文档名"],
            "scores": high_scores,
            "requires_confirmation": False,
        })
    candidates.append({
        "name": "AI",
        "target_type": "web_research",
        "reason": "泛概念不应成为派生任务。",
        "evidence": ["口播泛称"],
        "scores": {
            "knowledge_value": 1,
            "parent_dependency": 1,
            "evidence_strength": 1,
            "actionability": 1,
            "freshness_risk": 1,
            "novelty": 1,
            "asset_fit": 1,
            "cost_risk_inverse": 5,
            "ambiguity_inverse": 1,
        },
        "requires_confirmation": True,
    })
    placeholder = {
        "candidates": [{
            "name": "候选名称",
            "target_type": "official_doc",
            "reason": "为什么这个派生能提升父笔记可信度、可复用性或可执行性",
        }]
    }
    analysis = "```json\n" + json.dumps(placeholder, ensure_ascii=False) + "\n```\n## 九、派生决策 JSON\n```json\n" + json.dumps(
        {"candidates": candidates},
        ensure_ascii=False,
    ) + "\n```"

    decision = derive_tasks_from_analysis(
        analysis,
        source_id="aweme-derive",
        source_url="https://v.douyin.com/derive/",
        source_media="douyin_video",
        ingest_intent="knowledge_ingest",
        vault_path=vault,
        task_id="derive-task",
    )

    assert decision["enabled"] is True
    assert len(decision["items"]) <= 3
    assert decision["counts"]["suppressed"] >= 1
    assert len({item["dedupe_key"] for item in decision["items"]}) == len(decision["items"])
    existing_items = [
        item for item in decision["items"]
        if item["canonical_target"] == "https://github.com/langchain-ai/langgraph"
    ]
    assert len(existing_items) == 1
    assert existing_items[0]["score"] >= 80
    assert existing_items[0]["dedupe"]["status"] == "existing_related"
    assert existing_items[0]["execution_status"] == "existing_related"
    assert not any(item["name"] == "LangGraph duplicate" for item in decision["items"])
    assert all(item.get("execution_status") != "queued" for item in decision["items"])

    public = public_derived_tasks(decision)
    assert len(public) <= 3
    assert all(item["status"] in {"candidate", "existing_related"} for item in public)
    assert all(item["decision"] == "candidate" for item in public)
    log_text = (tmp / "derive-runtime" / "logs" / "derive-strategy-events.jsonl").read_text(encoding="utf-8")
    assert "derive_decision" in log_text
    assert "api_key" not in log_text.lower()
    assert "cookie" not in log_text.lower()

    audit = decision["audit_artifacts"]
    assert audit["dir"] == "run-artifacts/derive-task"
    files = audit["files"]
    for key in (
        "derive_input",
        "derive_raw_json_candidates",
        "derive_raw_markdown_candidates",
        "derive_normalized_candidates",
        "derive_scored_retained_candidates",
        "derive_public_candidates",
    ):
        assert key in files
        artifact_path = tmp / "derive-runtime" / files[key]
        assert artifact_path.exists(), key
        artifact_text = artifact_path.read_text(encoding="utf-8")
        assert "api_key" not in artifact_text.lower()
        assert "cookie" not in artifact_text.lower()
        assert "resp-" not in artifact_text


def test_derive_strategy_marks_high_confidence_github_without_url_auto_ready(tmp: Path) -> None:
    import sys

    os.environ["AGENT_WIKI_HOME"] = str(tmp / "derive-auto-runtime")
    sys.path.insert(0, str(SCRIPTS))
    from derive_strategy import derive_tasks_from_analysis, public_derived_tasks

    vault = tmp / "derive-auto-vault"
    vault.mkdir()
    scores = {
        "knowledge_value": 5,
        "parent_dependency": 5,
        "evidence_strength": 5,
        "actionability": 5,
        "freshness_risk": 4,
        "novelty": 5,
        "asset_fit": 5,
        "cost_risk_inverse": 5,
        "ambiguity_inverse": 3,
    }
    analysis = "## 九、派生决策 JSON\n```json\n" + json.dumps({
        "candidates": [
            {
                "name": "LangGraph",
                "target_type": "github_project",
                "mentioned_context": "视频说 LangGraph 用于 Agent 状态图和工作流编排。",
                "reason": "这是父视频方法可执行化的关键项目。",
                "evidence": ["03:20 口播 LangGraph 做状态图"],
                "confidence": 0.9,
                "scores": scores,
                "requires_confirmation": True,
            },
            {
                "name": "Some API Docs",
                "target_type": "official_doc",
                "mentioned_context": "视频提到某个 API，但没有给链接。",
                "reason": "需要官方文档核验。",
                "evidence": ["只出现 API 名称"],
                "confidence": 0.9,
                "scores": scores,
            },
        ]
    }, ensure_ascii=False) + "\n```"
    decision = derive_tasks_from_analysis(
        analysis,
        source_id="auto-aweme",
        source_url="https://v.douyin.com/auto/",
        source_media="douyin_video",
        ingest_intent="knowledge_ingest",
        vault_path=vault,
        task_id="derive-auto-task",
    )
    public = public_derived_tasks(decision)
    langgraph = next(item for item in public if item["name"] == "LangGraph")
    assert langgraph["autoEligible"] is True
    assert langgraph["status"] == "auto_ready"
    assert "requires_confirmation" not in langgraph["autoBlockReasons"]
    assert "target_resolution_required" not in langgraph["autoBlockReasons"]
    assert not any(item["name"] == "Some API Docs" for item in public)
    suppressed = decision["audit_artifacts"]["files"]["derive_scored_retained_candidates"]
    suppressed_text = (tmp / "derive-auto-runtime" / suppressed).read_text(encoding="utf-8")
    assert "Some API Docs" in suppressed_text
    assert "target_url_required_for_visible_candidate" in suppressed_text


def test_derive_strategy_auto_blocks_non_github_and_unsafe_urls(tmp: Path) -> None:
    import sys

    os.environ["AGENT_WIKI_HOME"] = str(tmp / "derive-url-runtime")
    sys.path.insert(0, str(SCRIPTS))
    from derive_strategy import derive_tasks_from_analysis, public_derived_tasks

    vault = tmp / "derive-url-vault"
    vault.mkdir()
    scores = {
        "knowledge_value": 5,
        "parent_dependency": 5,
        "evidence_strength": 5,
        "actionability": 5,
        "freshness_risk": 4,
        "novelty": 5,
        "asset_fit": 5,
        "cost_risk_inverse": 5,
        "ambiguity_inverse": 5,
    }
    analysis = "## 九、派生决策 JSON\n```json\n" + json.dumps({
        "candidates": [
            {
                "name": "Official Docs",
                "target_type": "official_doc",
                "target_url": "https://docs.example.com/api?token=secret&utm_source=x",
                "reason": "需要核验 API 参数。",
                "evidence": ["视频出现官方文档名称"],
                "confidence": 0.95,
                "scores": scores,
            },
            {
                "name": "Credential Repo",
                "target_type": "github_project",
                "target_url": "https://user:pass@github.com/langchain-ai/langgraph",
                "reason": "视频说它用于状态图。",
                "evidence": ["口播 LangGraph"],
                "confidence": 0.95,
                "scores": scores,
            },
        ],
    }, ensure_ascii=False) + "\n```"

    decision = derive_tasks_from_analysis(
        analysis,
        source_id="unsafe-url-aweme",
        source_url="https://v.douyin.com/unsafe/",
        source_media="douyin_video",
        ingest_intent="knowledge_ingest",
        vault_path=vault,
        task_id="derive-url-task",
    )
    public = public_derived_tasks(decision)
    docs = next(item for item in public if item["name"] == "Official Docs")
    assert docs["targetUrl"] == "https://docs.example.com/api"
    assert docs["autoEligible"] is False
    assert "manual_review_required_for_target_type" in docs["autoBlockReasons"]
    assert not any(item["name"] == "Credential Repo" for item in public)
    suppressed = decision["audit_artifacts"]["files"]["derive_scored_retained_candidates"]
    suppressed_text = (tmp / "derive-url-runtime" / suppressed).read_text(encoding="utf-8")
    assert "Credential Repo" in suppressed_text
    assert "url_contains_credentials" in suppressed_text
    log_text = (tmp / "derive-url-runtime" / "logs" / "derive-strategy-events.jsonl").read_text(encoding="utf-8")
    assert "pass@github.com" not in log_text
    assert "secret" not in log_text


def test_knowledge_prompts_do_not_force_github_manual_confirmation() -> None:
    video_prompt = (SCRIPTS / "prompts" / "video_knowledge_ingest.md").read_text(encoding="utf-8")
    image_prompt = (SCRIPTS / "prompts" / "image_post_knowledge_ingest.md").read_text(encoding="utf-8")
    for prompt in (video_prompt, image_prompt):
        assert '"requires_confirmation": false' in prompt
        assert "最多给 `3` 个强候选" in prompt
        assert "默认不要生成派生候选" in prompt
        assert "高置信 GitHub 项目候选可以设为 `false`" in prompt
        assert "由执行层通过 GitHub API + README 解析" in prompt
        assert '"evidence_strength": 5' in prompt
        assert '"ambiguity_inverse": 4' in prompt


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
        strategy_model="doubao-seed-2-0-mini-260428",
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


def test_derive_strategy_ignores_candidates_json_outside_derived_section(tmp: Path) -> None:
    import sys

    sys.path.insert(0, str(SCRIPTS))
    from derive_strategy import derive_tasks_from_analysis

    vault = tmp / "derive-section-vault"
    vault.mkdir()
    analysis = """
## 三、正文示例
教程里展示了一个业务 JSON，不应被当成派生候选。

```json
{"candidates":[{"name":"Wrong API","target_type":"official_doc","target_url":"https://example.com/wrong","scores":{"knowledge_value":5,"parent_dependency":5,"evidence_strength":5,"actionability":5,"freshness_risk":5,"novelty":5,"asset_fit":5,"cost_risk_inverse":5,"ambiguity_inverse":5}}]}
```

## 四、工具、项目、API、关键词
| 名称 | 类型 | 上下文 | 后续动作 |
|---|---|---|---|
| LangGraph | GitHub | Agent 状态图工具 | 派生 GitHub 任务 |
"""
    decision = derive_tasks_from_analysis(
        analysis,
        source_id="aweme-json-noise",
        source_url="https://v.douyin.com/noise/",
        source_media="douyin_video",
        ingest_intent="knowledge_ingest",
        vault_path=vault,
        task_id="noise-task",
    )

    assert decision["source"] == "markdown_fallback"
    assert all(item["name"] != "Wrong API" for item in decision["items"])
    assert any(item["name"] == "LangGraph" for item in decision["items"])


def test_derived_status_prefers_knowledge_decision_even_when_second() -> None:
    import sys

    sys.path.insert(0, str(SCRIPTS))
    from ingest import _status_derived_decision

    viral = {
        "enabled": False,
        "items": [],
        "counts": {"candidate": 0, "rejected": 0, "suppressed": 0},
    }
    knowledge = {
        "enabled": True,
        "items": [{"id": "dt-knowledge", "decision": "candidate"}],
        "counts": {"candidate": 1, "rejected": 0, "suppressed": 0},
    }

    selected = _status_derived_decision(
        {"viral_breakdown": viral, "knowledge_ingest": knowledge},
        "viral_breakdown",
    )

    assert selected is knowledge


def test_vault_write_includes_derived_tasks_and_record(tmp: Path) -> None:
    import sys

    sys.path.insert(0, str(SCRIPTS))
    from config_loader import Config
    from ingest import write_to_vault

    vault = tmp / "derived-vault"
    vault.mkdir()
    runtime = tmp / "derived-runtime"
    runtime.mkdir()
    video = tmp / "video.mp4"
    video.write_bytes(b"fake-video")

    cfg = Config(
        ark_api_key="test",
        ark_endpoint="https://ark.cn-beijing.volces.com/api/v3",
        analyzer_model="doubao-seed-2-0-lite-260428",
        analyzer_fallback="doubao-seed-2-0-mini-260428",
        strategy_model="doubao-seed-2-0-mini-260428",
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
    derived_decision = {
        "enabled": True,
        "source": "json",
        "counts": {"candidate": 1, "rejected": 0, "suppressed": 0},
        "items": [{
            "id": "dt-test",
            "name": "LangGraph",
            "target_type": "github_project",
            "derived_kind": "github_project",
            "target_url": "https://github.com/langchain-ai/langgraph",
            "canonical_target": "https://github.com/langchain-ai/langgraph",
            "dedupe_key": "dedupe-test",
            "decision": "candidate",
            "execution_status": "candidate",
            "score": 88,
            "confidence": 0.86,
            "scores": {
                "knowledge_value": 5,
                "parent_dependency": 5,
                "evidence_strength": 5,
                "actionability": 5,
                "freshness_risk": 4,
                "novelty": 4,
                "asset_fit": 5,
                "cost_risk_inverse": 4,
                "ambiguity_inverse": 5,
            },
            "reason": "父视频用它解释 Agent Harness 状态图。",
            "evidence": ["时间码 03:20 出现仓库名"],
            "relation_type": "implements",
            "intended_asset_family": "github_project",
            "lineage_depth": 1,
            "dedupe": {"status": "new", "matched_asset": ""},
            "downgrade_flags": ["requires_confirmation"],
            "reject_reasons": [],
            "next_action": "manual_review",
        }],
    }

    md_path, _ = write_to_vault(
        cfg,
        FakeMeta(),
        video,
        FakeResult(text=(
            "## 一、摘要\n视频介绍 Agent Harness。\n\n"
            "## 九、派生决策 JSON\n```json\n"
            + _derived_candidate_json()
            + "\n```\n"
        )),
        {"input_tokens": 1, "output_tokens": 2, "total_tokens": 3, "cost_rmb_estimate": 0.1},
        "knowledge_ingest",
        derived_decision,
        "task-derived",
    )

    text = md_path.read_text(encoding="utf-8")
    assert "derived_candidate_record:" in text
    assert 'derived_candidate_ids: ["dt-test"]' in text
    assert "target_type:" not in text.split("---", 2)[1]
    assert "## 派生任务候选" in text
    assert "[LangGraph](https://github.com/langchain-ai/langgraph)" in text
    assert "## 九、派生决策 JSON" not in text
    assert "候选名称" not in text

    records = list((vault / "系统记录" / "派生任务候选").glob("*.json"))
    assert len(records) == 1
    record = json.loads(records[0].read_text(encoding="utf-8"))
    item = record["items"][0]
    assert item["parent_task_id"] == "task-derived"
    assert item["parent_asset_id"]
    assert item["parent_asset_path"] == str(md_path.relative_to(vault))
    assert item["parent_source_url"] == FakeMeta.source_url
    assert item["parent_aweme_id"] == FakeMeta.aweme_id


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
        strategy_model="doubao-seed-2-0-mini-260428",
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
        strategy_model="doubao-seed-2-0-mini-260428",
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
        strategy_model="doubao-seed-2-0-mini-260428",
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
            if fields.get("stage") == "derived_candidates_ready":
                calls.append((
                    "derived_status",
                    fields.get("derived_tasks"),
                    fields.get("derived_summary"),
                    fields.get("derived_audit_artifacts"),
                ))

        def progress(self, stage, info):
            calls.append(("progress", stage, info.get("intent")))

    async def fake_fetch_metadata(url, cookie_path):
        calls.append(("fetch_metadata", url))
        return FakeMeta()

    async def fake_download_video(meta, cache_dir, progress_cb=None):
        calls.append(("download_video", meta.aweme_id))
        return video

    async def fake_analyze_video_many(video_path, prompts, **kwargs):
        calls.append(("analyze_video_many", tuple(prompts), kwargs.get("strategy_model")))
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

    primary_derived_decision = {
        "enabled": True,
        "source": "json",
        "counts": {"candidate": 1, "rejected": 0, "suppressed": 0},
        "audit_artifacts": {
            "dir": "run-artifacts/multi-intent",
            "files": {"derive_public_candidates": "run-artifacts/multi-intent/05-derive/05-public-candidates.json"},
        },
        "items": [{
            "id": "dt-primary",
            "name": "Primary API",
            "target_type": "official_doc",
            "target_url": "https://example.com/docs/primary-api",
            "decision": "candidate",
            "execution_status": "candidate",
            "score": 84,
            "reason": "需要核验父视频里的 API 参数。",
        }],
    }

    def fake_derive_tasks(text, *, source_id, source_url, source_media, ingest_intent, vault_path,
                          task_id=""):
        calls.append(("derive_tasks", ingest_intent, source_media, task_id))
        if ingest_intent != "knowledge_ingest":
            return {
                "enabled": False,
                "reason": "derivation_only_runs_for_knowledge_ingest",
                "items": [],
                "counts": {"candidate": 0, "rejected": 0, "suppressed": 0},
            }
        assert source_id == FakeMeta.aweme_id
        assert source_url == FakeMeta.source_url
        assert source_media == "douyin_video"
        assert vault_path == cfg.vault_path
        return primary_derived_decision

    def fake_write(config, meta, video_path, result, cost, ingest_intent,
                   derived_decision=None, task_id=""):
        calls.append(("write_to_vault", ingest_intent))
        calls.append(("write_derived", ingest_intent, derived_decision, task_id))
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
            "derive_tasks_from_analysis",
            "write_to_vault",
        ]
    }
    try:
        ingest.fetch_metadata = fake_fetch_metadata
        ingest.download_video = fake_download_video
        ingest.analyze_video_many = fake_analyze_video_many
        ingest.estimate_cost_rmb = fake_cost
        ingest.derive_tasks_from_analysis = fake_derive_tasks
        ingest.write_to_vault = fake_write
        sw = FakeStatusWriter()
        summary = asyncio.run(ingest.run_task(
            task_id="multi-intent",
            url="https://v.douyin.com/test/",
            quality="quality",
            ingest_intents=("knowledge_ingest", "viral_breakdown"),
            config=cfg,
            sw=sw,
            cache_dir=cache,
        ))
    finally:
        for name, value in originals.items():
            setattr(ingest, name, value)

    assert sum(1 for call in calls if call[0] == "download_video") == 1
    assert (
        "analyze_video_many",
        ("knowledge_ingest", "viral_breakdown"),
        "doubao-seed-2-0-mini-260428",
    ) in calls
    assert ("write_to_vault", "knowledge_ingest") in calls
    assert ("write_to_vault", "viral_breakdown") in calls
    assert len(summary["assets"]) == 2
    assert summary["ingest_intents"] == ["knowledge_ingest", "viral_breakdown"]
    assert summary["analysis"]["file_id"] == "file-one-upload"
    assert summary["derived_summary"] == {"candidate": 1, "rejected": 0, "suppressed": 0}
    assert len(summary["derived_tasks"]) == 1
    expected_public = {
        "id": "dt-primary",
        "name": "Primary API",
        "targetType": "official_doc",
        "targetUrl": "https://example.com/docs/primary-api",
        "decision": "candidate",
        "status": "candidate",
        "score": 84,
        "reason": "需要核验父视频里的 API 参数。",
    }
    for key, value in expected_public.items():
        assert summary["derived_tasks"][0][key] == value
    assert summary["assets"][0]["derived_tasks"] == summary["derived_tasks"]
    assert summary["assets"][0]["derived_summary"] == summary["derived_summary"]
    assert summary["assets"][0]["derived_audit_artifacts"] == primary_derived_decision["audit_artifacts"]
    assert summary["assets"][1]["derived_tasks"] == []
    assert summary["assets"][1]["derived_audit_artifacts"] == {}
    assert summary["derived_audit_artifacts"] == primary_derived_decision["audit_artifacts"]
    assert (
        "derived_status",
        summary["derived_tasks"],
        summary["derived_summary"],
        summary["derived_audit_artifacts"],
    ) in calls
    assert ("write_derived", "knowledge_ingest", primary_derived_decision, "multi-intent") in calls


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

    os.environ["AGENT_WIKI_HOME"] = str(tmp / "ws-runtime")
    sys.path.insert(0, str(ROOT / "server"))
    from websocket_server import LibrarianServer
    from config_loader import load_config

    vault = tmp / "ws-vault"
    vault.mkdir()
    (vault / ".obsidian").mkdir()
    (vault / "index.md").write_text("# 知识库索引\n", encoding="utf-8")
    server = LibrarianServer()
    asyncio.run(server.handle_config_update({
        "llm": {
            "provider": "doubao",
            "apiKey": "test-key",
            "endpoint": "https://ark.cn-beijing.volces.com/api/v3",
        },
        "vaultPath": str(vault),
        "videoAnalysis": {
            "modelPreset": "lite",
            "analyzerModel": "doubao-seed-2-0-lite-260428",
            "strategyModel": "doubao-seed-2-0-mini-260428",
            "chunkConcurrency": 4,
        },
        "server": {"taskConcurrency": 3},
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
    assert cfg.strategy_model == "doubao-seed-2-0-mini-260428"
    assert cfg.chunk_concurrency == 4
    assert server.task_concurrency == 3

    config_path = tmp / "ws-runtime" / "config.toml"
    config_text = config_path.read_text(encoding="utf-8")
    assert "task_concurrency = 3" in config_text
    assert "chunk_concurrency = 4" in config_text
    assert oct(config_path.stat().st_mode & 0o777) == "0o600"

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

    os.environ["AGENT_WIKI_HOME"] = str(tmp / "memory-runtime")
    sys.path.insert(0, str(SCRIPTS))
    import analyzer

    assert analyzer.should_chunk_video(600) is False
    assert analyzer.should_chunk_video(601) is True
    assert analyzer._long_overview_fps(1200) == 1.0
    assert analyzer._long_overview_fps(1800) == 1.0
    assert analyzer._ultra_long_threshold_sec() == 1230.0
    assert analyzer._is_ultra_long_video(1230) is False
    assert analyzer._is_ultra_long_video(1231) is True
    assert analyzer._is_ultra_long_video(1800) is True
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


def test_status_writer_redacts_sensitive_fields(tmp: Path) -> None:
    import sys

    sys.path.insert(0, str(SCRIPTS))
    from status_writer import StatusWriter

    writer = StatusWriter("task-redact", tmp / "status")
    writer.progress("chunk_uploaded", {
        "part_index": 2,
        "chunk_count": 3,
        "file_id": "file-safe",
    })
    writer.progress("analyzing_done", {
        "response_id": "resp-secret",
        "previous_response_id": "resp-old",
        "arkApiKey": "sk-camel",
        "agentPlanApiKey": "sk-plan-camel",
        "doubaoApiKey": "sk-doubao-camel",
        "filesApiKey": "sk-files-camel",
        "fileApiKey": "sk-file-camel",
        "doubao_api_key": "sk-doubao-snake",
        "files_api_key": "sk-files-snake",
        "message": (
            "Authorization: Bearer sk-secret\n"
            "cookie: sid=abc\n"
            "{\"api_key\":\"sk-json\",\"response_id\":\"abc-json\"}"
        ),
        "nested": {
            "api_key": "sk-nested",
            "previousResponseId": "abc-camel-response",
            "ok": True,
        },
    })
    writer.update(ok=False, stage="failed", error="failed with api_key=sk-error and resp-error")

    text = writer.path.read_text(encoding="utf-8")
    data = json.loads(text)
    assert data["ok"] is False
    assert data["finished_at"] >= data["started_at"]
    assert data["elapsed_sec"] >= 0
    assert data["task_duration_sec"] == data["elapsed_sec"]
    assert data["chunk_progress"]["2"]["chunk_uploaded"]["file_id"] == "file-safe"
    assert "resp-secret" not in text
    assert "resp-old" not in text
    assert "sk-secret" not in text
    assert "sk-nested" not in text
    assert "sk-error" not in text
    assert "sk-camel" not in text
    assert "sk-plan-camel" not in text
    assert "sk-doubao-camel" not in text
    assert "sk-files-camel" not in text
    assert "sk-file-camel" not in text
    assert "sk-doubao-snake" not in text
    assert "sk-files-snake" not in text
    assert "sk-json" not in text
    assert "abc-json" not in text
    assert "abc-camel-response" not in text
    assert "sid=abc" not in text
    assert "response_id" not in text
    assert "previous_response_id" not in text


def test_long_video_strategy_accepts_top_level_segments_and_partial_fallback() -> None:
    import sys

    sys.path.insert(0, str(SCRIPTS))
    import analyzer

    plan = analyzer._chunk_plan(601)

    def segment(item, *, part_index=None, evidence=True):
        payload = {
            "part_index": part_index or item["part_index"],
            "start_sec": item["start_sec"],
            "end_sec": item["end_sec"],
            "rough_summary": "稳定讲解",
            "recommended_fps": 3,
            "confidence": 0.9,
            "scores": {
                "visual_change": 1,
                "ocr_subtitle_density": 2,
                "operation_density": 1,
                "motion_detail": 0,
                "concept_density": 3,
                "risk_if_low_fps": 2,
            },
            "focus": ["结论"],
            "lite_brief": "重点提取口播结论，画面稳定，不要把重复画面当作新信息。",
            "risk_flags": [],
            "why_not_lower_fps": "需要保留字幕细节",
        }
        if evidence:
            payload["evidence"] = ["字幕稳定可读"]
        return payload

    top_level = analyzer._normalize_long_video_strategy(
        json.dumps({
            "overview": {"summary": "这条视频讲 Open Design。", "timeline": []},
            "strategy": {"global_notes": "模型把 segments 放在顶层。"},
            "segments": [segment(item) for item in plan],
        }, ensure_ascii=False),
        plan,
    )
    assert top_level["ok"] is True
    assert top_level["detected_structure"]["segments_path"] == "segments"
    assert analyzer._strategy_needs_json_repair(top_level) is False
    assert [item["recommended_fps"] for item in top_level["chunks"]] == [3.0, 3.0, 3.0]

    partial = analyzer._normalize_long_video_strategy(
        json.dumps({
            "overview": {"summary": "部分策略字段坏。", "timeline": []},
            "segments": [
                segment(plan[0]),
                {k: v for k, v in segment(plan[1]).items() if k != "lite_brief"},
                segment(plan[2]),
            ],
        }, ensure_ascii=False),
        plan,
    )
    assert partial["ok"] is True
    assert partial["chunks"][0]["recommended_fps"] == 3.0
    assert partial["chunks"][0]["fallback_applied"] is False
    assert partial["chunks"][1]["recommended_fps"] == 5.0
    assert partial["chunks"][1]["fallback_applied"] is True
    assert partial["chunks"][1]["validation_fallback"] is True
    assert "必填字段" in partial["chunks"][1]["fallback_reason"]
    assert partial["chunks"][2]["recommended_fps"] == 3.0


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
                        "lite_brief": "重点理解口播观点和结论，画面只是低变化背景。",
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
                        "lite_brief": "重点捕捉界面菜单、按钮和操作顺序，避免漏掉短暂视觉步骤。",
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
                        "lite_brief": "重点提取收尾结论和字幕里的关键词。",
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


def test_long_video_strategy_does_not_raise_fps_for_concepts_only() -> None:
    import sys

    sys.path.insert(0, str(SCRIPTS))
    import analyzer

    plan = analyzer._chunk_plan(601)
    strategy = analyzer._normalize_long_video_strategy(
        json.dumps({
            "overview": {"summary": "静态长访谈，观点密度高。", "timeline": []},
            "strategy": {
                "global_notes": "全程固定机位，主要靠口播承载信息。",
                "segments": [
                    {
                        "part_index": item["part_index"],
                        "start_sec": item["start_sec"],
                        "end_sec": item["end_sec"],
                        "rough_summary": "嘉宾密集输出产业观点。",
                        "recommended_fps": 5,
                        "confidence": 0.92,
                        "information_carriers": {
                            "audio_argument": 5,
                            "subtitle_ocr": 1,
                            "visual_scene": 1,
                            "operation_steps": 0,
                            "motion_detail": 0,
                            "structure_context": 5,
                        },
                        "scores": {
                            "visual_change": 1,
                            "ocr_subtitle_density": 1,
                            "operation_density": 0,
                            "motion_detail": 0,
                            "concept_density": 5,
                            "risk_if_low_fps": 5,
                        },
                        "lite_brief": "画面重复，核心信息来自口播论证。Lite 应重点提取观点链、数字和待验证事实。",
                        "evidence": ["固定机位坐着说话"],
                        "risk_flags": [],
                        "why_not_lower_fps": "观点很密，但视觉风险低。",
                    }
                    for item in plan
                ],
            },
        }, ensure_ascii=False),
        plan,
    )

    assert strategy["ok"] is True
    assert all(item["recommended_fps"] == 3.0 for item in strategy["chunks"])
    assert all(item["fps_adjusted"] is True for item in strategy["chunks"])
    assert all("概念密度" in item["fps_adjust_reason"] for item in strategy["chunks"])


def test_long_video_strategy_missing_required_fields_requests_repair() -> None:
    import sys

    sys.path.insert(0, str(SCRIPTS))
    import analyzer

    plan = analyzer._chunk_plan(601)
    missing_fields = analyzer._normalize_long_video_strategy(
        json.dumps({
            "overview": {"summary": "概览", "timeline": []},
            "strategy": {
                "segments": [
                    {
                        "part_index": item["part_index"],
                        "start_sec": item["start_sec"],
                        "end_sec": item["end_sec"],
                        "recommended_fps": 2,
                        "confidence": 0.95,
                        "evidence": ["稳定画面"],
                    }
                    for item in plan
                ],
            },
        }, ensure_ascii=False),
        plan,
    )

    assert missing_fields["ok"] is True
    assert analyzer._strategy_needs_json_repair(missing_fields) is True
    assert all(item["recommended_fps"] == 5.0 for item in missing_fields["chunks"])
    assert all("必填字段" in item["fallback_reason"] for item in missing_fields["chunks"])


def test_prepare_long_video_strategy_repairs_json_with_strategy_model(tmp: Path) -> None:
    import asyncio
    import sys

    os.environ["AGENT_WIKI_HOME"] = str(tmp / "strategy-repair-runtime")
    sys.path.insert(0, str(SCRIPTS))
    import analyzer

    video = tmp / "long.mp4"
    video.write_bytes(b"fake-video")
    plan = analyzer._chunk_plan(601)
    calls = []
    progress = []

    async def fake_upload(client, path, *, fps, model):
        calls.append(("upload", fps, model))
        return SimpleNamespace(id="file-overview")

    async def fake_wait(*args, **kwargs):
        calls.append(("wait", args[1] if len(args) > 1 else ""))
        return SimpleNamespace(status="active")

    async def fake_stream(client, *, model, file_id, prompt, on_progress, previous_response_id=None, timeout_sec=None):
        calls.append(("stream", model, file_id, previous_response_id))
        return analyzer.ResponseCallResult(
            text="坏 JSON",
            usage={"input_tokens": 10, "output_tokens": 2, "total_tokens": 12},
            response_id="resp-overview",
        )

    async def fake_text(client, *, model, prompt, on_progress, previous_response_id=None, timeout_sec=None):
        calls.append(("repair", model, previous_response_id, "坏 JSON" in prompt))
        repaired = {
            "overview": {
                "summary": "全片先讲背景，再演示流程。",
                "timeline": [],
                "important_points": ["流程"],
                "uncertain_points": [],
            },
            "strategy": {
                "global_notes": "修复后的策略。",
                "segments": [
                    {
                        "part_index": item["part_index"],
                        "start_sec": item["start_sec"],
                        "end_sec": item["end_sec"],
                        "rough_summary": "稳定讲解",
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
                        "evidence": ["画面稳定"],
                        "focus": ["结论"],
                        "lite_brief": "画面稳定，重点提取口播结论和关键事实。",
                        "risk_flags": [],
                        "why_not_lower_fps": "低风险",
                    }
                    for item in plan
                ],
            },
        }
        return analyzer.ResponseCallResult(
            text=json.dumps(repaired, ensure_ascii=False),
            usage={"input_tokens": 5, "output_tokens": 3, "total_tokens": 8},
            response_id="resp-repair",
        )

    async def on_progress(stage, info):
        progress.append((stage, info))

    old_upload = analyzer._upload_with_preprocess
    old_wait = analyzer._wait_for_active
    old_stream = analyzer._stream_responses
    old_text = analyzer._call_text_responses
    try:
        analyzer._upload_with_preprocess = fake_upload
        analyzer._wait_for_active = fake_wait
        analyzer._stream_responses = fake_stream
        analyzer._call_text_responses = fake_text
        strategy = asyncio.run(analyzer._prepare_long_video_strategy(
            video,
            plan,
            ["knowledge_ingest"],
            files_client=SimpleNamespace(),
            responses_client=SimpleNamespace(),
            model="doubao-seed-2-0-lite-260428",
            strategy_model="doubao-seed-2-0-mini-260428",
            source_id="aweme-repair",
            file_active_timeout_sec=120,
            response_timeout_sec=900,
            on_progress=on_progress,
        ))
    finally:
        analyzer._upload_with_preprocess = old_upload
        analyzer._wait_for_active = old_wait
        analyzer._stream_responses = old_stream
        analyzer._call_text_responses = old_text

    assert strategy["ok"] is True
    assert all(item["recommended_fps"] == 2.0 for item in strategy["chunks"])
    assert ("upload", 1.0, "doubao-seed-2-0-mini-260428") in calls
    assert ("stream", "doubao-seed-2-0-mini-260428", "file-overview", None) in calls
    assert ("repair", "doubao-seed-2-0-mini-260428", "resp-overview", True) in calls
    assert any(stage == "repairing_overview_strategy" for stage, _ in progress)
    assert any(stage == "overview_strategy_repaired" for stage, _ in progress)
    log_path = tmp / "strategy-repair-runtime" / "logs" / "video-strategy-events.jsonl"
    assert log_path.exists()
    log_text = log_path.read_text(encoding="utf-8")
    assert "overview_strategy_repair_needed" in log_text
    assert "overview_strategy_repaired" in log_text
    assert "resp-overview" not in log_text


def test_prepare_long_video_strategy_chunks_unsafe_full_overview(tmp: Path) -> None:
    import asyncio
    import sys

    os.environ["AGENT_WIKI_HOME"] = str(tmp / "strategy-too-long-runtime")
    sys.path.insert(0, str(SCRIPTS))
    import analyzer

    video = tmp / "thirty-minutes.mp4"
    video.write_bytes(b"fake-video")
    plan = analyzer._chunk_plan(1800)
    chunk_paths = []
    for item in plan:
        path = tmp / f"part-{int(item['part_index']):03d}.mp4"
        path.write_bytes(b"fake-chunk")
        chunk_paths.append(path)
    upload_calls = []
    stream_calls = []
    synth_calls = []
    progress = []

    async def fake_upload(client, path, *, fps, model):
        assert path != video
        upload_calls.append((Path(path).name, fps, model))
        return SimpleNamespace(id=f"file-{Path(path).stem}")

    async def fake_wait(*args, **kwargs):
        return SimpleNamespace(status="active")

    async def fake_stream(client, *, model, file_id, prompt, on_progress, previous_response_id=None, timeout_sec=None):
        stream_calls.append((model, file_id, previous_response_id))
        return analyzer.ResponseCallResult(
            text=f"{file_id} 粗概览：画面稳定，主要是口播和字幕。",
            usage={"total_tokens": 1},
            response_id=f"resp-{file_id}",
        )

    async def fake_text(client, *, model, prompt, on_progress, previous_response_id=None, timeout_sec=None):
        synth_calls.append((model, previous_response_id, "粗概览结果" in prompt))
        strategy = {
            "overview": {
                "summary": "超长视频按切片粗概览后，整体以稳定口播和字幕讲解为主。",
                "timeline": [],
                "important_points": ["稳定口播"],
                "uncertain_points": [],
            },
            "strategy": {
                "global_notes": "画面稳定，低 fps 漏细节风险低。",
                "segments": [
                    {
                        "part_index": item["part_index"],
                        "start_sec": item["start_sec"],
                        "end_sec": item["end_sec"],
                        "rough_summary": "稳定讲解",
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
                        "evidence": ["粗概览显示画面稳定"],
                        "focus": ["口播结论"],
                        "lite_brief": "画面稳定，核心信息来自口播和字幕，重点提取观点链和关键事实。",
                        "risk_flags": [],
                        "why_not_lower_fps": "低风险",
                    }
                    for item in plan
                ],
            },
        }
        return analyzer.ResponseCallResult(
            text=json.dumps(strategy, ensure_ascii=False),
            usage={"total_tokens": 2},
            response_id="resp-chunked-overview-strategy",
        )

    async def on_progress(stage, info):
        progress.append((stage, info))

    old_upload = analyzer._upload_with_preprocess
    old_wait = analyzer._wait_for_active
    old_stream = analyzer._stream_responses
    old_text = analyzer._call_text_responses
    try:
        analyzer._upload_with_preprocess = fake_upload
        analyzer._wait_for_active = fake_wait
        analyzer._stream_responses = fake_stream
        analyzer._call_text_responses = fake_text
        strategy = asyncio.run(analyzer._prepare_long_video_strategy(
            video,
            plan,
            ["knowledge_ingest"],
            files_client=SimpleNamespace(),
            responses_client=SimpleNamespace(),
            model="doubao-seed-2-0-lite-260428",
            strategy_model="doubao-seed-2-0-mini-260428",
            source_id="aweme-too-long",
            file_active_timeout_sec=120,
            response_timeout_sec=900,
            chunk_paths=chunk_paths,
            chunk_concurrency=4,
            on_progress=on_progress,
        ))
    finally:
        analyzer._upload_with_preprocess = old_upload
        analyzer._wait_for_active = old_wait
        analyzer._stream_responses = old_stream
        analyzer._call_text_responses = old_text

    assert len(upload_calls) == len(plan)
    assert all(call[1] == 1.0 for call in upload_calls)
    assert len(stream_calls) == len(plan)
    assert synth_calls == [("doubao-seed-2-0-mini-260428", None, True)]
    assert strategy["ok"] is True
    assert all(item["recommended_fps"] == 2.0 for item in strategy["chunks"])
    assert any(stage == "overview_chunking" for stage, _ in progress)
    assert any(stage == "synthesizing_overview_strategy" for stage, _ in progress)
    assert any(stage == "overview_strategy_decided" for stage, _ in progress)
    log_path = tmp / "strategy-too-long-runtime" / "logs" / "video-strategy-events.jsonl"
    assert log_path.exists()
    log_text = log_path.read_text(encoding="utf-8")
    assert "overview_strategy_chunked_started" in log_text
    assert "overview_strategy_chunked_synthesized" in log_text
    assert "ultra_long_video" in log_text
    assert "1800" in log_text
    assert "response_id" not in log_text


def test_strategy_log_redacts_sensitive_values(tmp: Path) -> None:
    import sys

    os.environ["AGENT_WIKI_HOME"] = str(tmp / "strategy-log-runtime")
    sys.path.insert(0, str(SCRIPTS))
    import analyzer

    analyzer._write_strategy_log("redaction_test", {
        "raw_text": (
            "Authorization: Bearer sk-secret\n"
            "cookie: sid=abc\n"
            "api_key=sk-another\n"
            "arkApiKey=sk-camel\n"
            "{\"api_key\":\"sk-json\",\"cookie\":\"sid=json\",\"response_id\":\"abc-json\"}\n"
            "response_id=resp-secret"
        ),
        "nested": {
            "response_id": "resp-nested",
            "previousResponseId": "abc-camel-response",
            "agentPlanApiKey": "sk-plan-camel",
            "doubaoApiKey": "sk-doubao-camel",
            "filesApiKey": "sk-files-camel",
            "fileApiKey": "sk-file-camel",
            "doubao_api_key": "sk-doubao-snake",
            "files_api_key": "sk-files-snake",
            "note": "Bearer sk-note",
        },
    })

    log_path = tmp / "strategy-log-runtime" / "logs" / "video-strategy-events.jsonl"
    text = log_path.read_text(encoding="utf-8")
    assert "sk-secret" not in text
    assert "sid=abc" not in text
    assert "sk-another" not in text
    assert "sk-camel" not in text
    assert "sk-json" not in text
    assert "sid=json" not in text
    assert "abc-json" not in text
    assert "resp-secret" not in text
    assert "resp-nested" not in text
    assert "abc-camel-response" not in text
    assert "sk-plan-camel" not in text
    assert "sk-doubao-camel" not in text
    assert "sk-files-camel" not in text
    assert "sk-file-camel" not in text
    assert "sk-doubao-snake" not in text
    assert "sk-files-snake" not in text
    assert "sk-note" not in text
    assert "response_id" not in text


def test_chunk_analysis_uses_strategy_fps_and_context(tmp: Path) -> None:
    import asyncio
    import sys

    os.environ["AGENT_WIKI_HOME"] = str(tmp / "chunk-strategy-runtime")
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
                "lite_brief": "重点理解背景结论，画面稳定。",
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
                "lite_brief": "重点捕捉界面菜单、按钮和操作顺序，避免漏掉短暂视觉步骤。",
                "risk_flags": ["可能漏步骤"],
                "why_not_lower_fps": "操作密集",
                "fallback_applied": False,
                "fallback_reason": "",
            },
        ],
    }
    uploads = []
    prompts = []
    audit_dir = tmp / "audit"
    audit_files = {}

    async def fake_upload(client, path, *, fps, model):
        if Path(path).name == "part-001.mp4":
            await asyncio.sleep(0.02)
        uploads.append((Path(path).name, fps))
        return SimpleNamespace(id=f"file-{Path(path).name}")

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
            audit_dir=audit_dir,
            audit_files=audit_files,
            file_active_timeout_sec=120,
            response_timeout_sec=900,
            on_progress=None,
        ))
    finally:
        analyzer._upload_with_preprocess = old_upload
        analyzer._wait_for_active = old_wait
        analyzer._stream_responses = old_stream
        analyzer._call_text_responses = old_text

    assert sorted(uploads) == [("part-001.mp4", 2.0), ("part-002.mp4", 5.0)]
    assert "全片概览" in prompts[0]
    assert "本段精拆策略" in prompts[0]
    assert "第二段需要更高 fps" in prompts[-1]
    result = results["knowledge_ingest"]
    assert result.chunked is True
    assert result.file_id == "file-part-001.mp4"
    assert result.fps_used == 5.0
    assert result.actual_frames_estimate == 1680
    assert [item["fps"] for item in result.chunks] == [2.0, 5.0]
    assert result.chunks[1]["strategy_focus"] == ["按钮和菜单"]
    assert result.chunks[1]["strategy_lite_brief"] == "重点捕捉界面菜单、按钮和操作顺序，避免漏掉短暂视觉步骤。"
    assert result.audit_artifacts["dir"].endswith("audit")
    assert (audit_dir / "03-lite/knowledge_ingest/part-001-prompt.md").exists()
    assert (audit_dir / "03-lite/knowledge_ingest/part-001-output.md").exists()
    assert (audit_dir / "04-synthesis/knowledge_ingest-synthesis-prompt.md").exists()
    assert (audit_dir / "04-synthesis/knowledge_ingest-synthesis-output.md").exists()
    assert all("response_id" not in item for item in result.chunks)


def test_chunk_analysis_retries_transient_stream_failure(tmp: Path) -> None:
    import asyncio
    import sys

    os.environ["AGENT_WIKI_HOME"] = str(tmp / "chunk-retry-runtime")
    sys.path.insert(0, str(SCRIPTS))
    import analyzer

    chunk_paths = [tmp / "part-001.mp4", tmp / "part-002.mp4"]
    for path in chunk_paths:
        path.write_bytes(b"fake")
    plan = [
        {"part_index": 1, "start_sec": 0.0, "end_sec": 240.0, "overlap_sec": 0.0},
        {"part_index": 2, "start_sec": 230.0, "end_sec": 470.0, "overlap_sec": 10.0},
    ]
    audit_dir = tmp / "retry-audit"
    progress_events = []
    attempts: dict[int, int] = {}

    async def fake_upload(client, path, *, fps, model):
        return SimpleNamespace(id=f"file-{Path(path).stem}")

    async def fake_wait(*args, **kwargs):
        return SimpleNamespace(status="active")

    async def fake_stream(client, *, model, file_id, prompt, on_progress, previous_response_id=None, timeout_sec=None):
        part = 1 if "第 1/2" in prompt else 2
        attempts[part] = attempts.get(part, 0) + 1
        if part == 1 and attempts[part] == 1:
            raise analyzer.APIError(
                "Responses API 调用失败: peer closed connection without sending complete message body "
                "(incomplete chunked read)"
            )
        return analyzer.ResponseCallResult(
            text=f"分片 {part} 分析结果",
            usage={"total_tokens": part},
            response_id=f"resp-{part}",
        )

    async def fake_text(client, *, model, prompt, on_progress, previous_response_id=None, timeout_sec=None):
        return analyzer.ResponseCallResult(
            text="最终汇总",
            usage={"total_tokens": 10},
            response_id="resp-final",
        )

    async def fake_progress(stage: str, info: dict) -> None:
        progress_events.append((stage, dict(info)))

    old_upload = analyzer._upload_with_preprocess
    old_wait = analyzer._wait_for_active
    old_stream = analyzer._stream_responses
    old_text = analyzer._call_text_responses
    old_delay = analyzer._RESPONSE_RETRY_BASE_DELAY_SEC
    try:
        analyzer._upload_with_preprocess = fake_upload
        analyzer._wait_for_active = fake_wait
        analyzer._stream_responses = fake_stream
        analyzer._call_text_responses = fake_text
        analyzer._RESPONSE_RETRY_BASE_DELAY_SEC = 0
        results = asyncio.run(analyzer._analyze_video_chunks(
            chunk_paths,
            plan,
            {"knowledge_ingest": "基础拆解 prompt"},
            files_client=SimpleNamespace(),
            responses_client=SimpleNamespace(),
            model="doubao-seed-2-0-lite-260428",
            quality="quality",
            full_duration=470.0,
            source_id="aweme-retry",
            strategy={"ok": True, "chunks": []},
            audit_dir=audit_dir,
            audit_files={},
            file_active_timeout_sec=120,
            response_timeout_sec=900,
            chunk_concurrency=1,
            on_progress=fake_progress,
        ))
    finally:
        analyzer._upload_with_preprocess = old_upload
        analyzer._wait_for_active = old_wait
        analyzer._stream_responses = old_stream
        analyzer._call_text_responses = old_text
        analyzer._RESPONSE_RETRY_BASE_DELAY_SEC = old_delay

    assert attempts == {1: 2, 2: 1}
    assert any(stage == "chunk_retrying" and info["part_index"] == 1 for stage, info in progress_events)
    assert (audit_dir / "03-lite/knowledge_ingest/part-001-output.md").exists()
    assert (audit_dir / "03-lite/knowledge_ingest/part-001-meta.json").exists()
    result = results["knowledge_ingest"]
    assert result.text == "最终汇总"
    assert [item["reused_from_artifact"] for item in result.chunks] == [False, False]


def test_chunk_analysis_reuses_existing_chunk_artifact_on_rerun(tmp: Path) -> None:
    import asyncio
    import sys

    os.environ["AGENT_WIKI_HOME"] = str(tmp / "chunk-resume-runtime")
    sys.path.insert(0, str(SCRIPTS))
    import analyzer

    chunk_paths = [tmp / "part-001.mp4", tmp / "part-002.mp4"]
    for path in chunk_paths:
        path.write_bytes(b"fake")
    plan = [
        {"part_index": 1, "start_sec": 0.0, "end_sec": 240.0, "overlap_sec": 0.0},
        {"part_index": 2, "start_sec": 230.0, "end_sec": 470.0, "overlap_sec": 10.0},
    ]
    audit_dir = tmp / "resume-audit"
    cached_output = audit_dir / "03-lite" / "knowledge_ingest" / "part-001-output.md"
    cached_output.parent.mkdir(parents=True)
    cached_output.write_text(
        "## 分片 1/2 (0.0s - 240.0s)\n\n"
        "这是第一次运行已经完成的分片分析结果，长度足够用于断点续跑复用。"
        "这里补充更多正文内容，模拟真实 Lite 分片输出里的摘要、核心知识、证据和待验证点，"
        "避免把测试缓存误判为一次中断后的残缺文件。",
        encoding="utf-8",
    )
    uploads = []
    stream_prompts = []
    synthesis_prompts = []
    progress_events = []

    async def fake_upload(client, path, *, fps, model):
        uploads.append(Path(path).name)
        return SimpleNamespace(id=f"file-{Path(path).stem}")

    async def fake_wait(*args, **kwargs):
        return SimpleNamespace(status="active")

    async def fake_stream(client, *, model, file_id, prompt, on_progress, previous_response_id=None, timeout_sec=None):
        stream_prompts.append(prompt)
        if "第 1/2" in prompt:
            raise AssertionError("cached part 1 must not be analyzed again")
        return analyzer.ResponseCallResult(
            text="分片 2 新分析结果",
            usage={"total_tokens": 2},
            response_id="resp-2",
        )

    async def fake_text(client, *, model, prompt, on_progress, previous_response_id=None, timeout_sec=None):
        synthesis_prompts.append(prompt)
        return analyzer.ResponseCallResult(
            text="最终汇总",
            usage={"total_tokens": 10},
            response_id="resp-final",
        )

    async def fake_progress(stage: str, info: dict) -> None:
        progress_events.append((stage, dict(info)))

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
            source_id="aweme-resume",
            strategy={"ok": True, "chunks": []},
            audit_dir=audit_dir,
            audit_files={},
            file_active_timeout_sec=120,
            response_timeout_sec=900,
            chunk_concurrency=1,
            on_progress=fake_progress,
        ))
    finally:
        analyzer._upload_with_preprocess = old_upload
        analyzer._wait_for_active = old_wait
        analyzer._stream_responses = old_stream
        analyzer._call_text_responses = old_text

    assert uploads == ["part-002.mp4"]
    assert len(stream_prompts) == 1
    assert "第 2/2" in stream_prompts[0]
    assert "第一次运行已经完成的分片分析结果" in synthesis_prompts[0]
    assert any(stage == "chunk_reused" and info["part_index"] == 1 for stage, info in progress_events)
    result = results["knowledge_ingest"]
    assert [item["reused_from_artifact"] for item in result.chunks] == [True, False]
    assert result.chunks[0]["file_id"] == "reused-from-artifact"


def test_chunk_synthesis_without_response_id_does_not_refresh_memory(tmp: Path) -> None:
    import asyncio
    import sys

    os.environ["AGENT_WIKI_HOME"] = str(tmp / "chunk-memory-runtime")
    sys.path.insert(0, str(SCRIPTS))
    import analyzer

    chunk_path = tmp / "part-001.mp4"
    chunk_path.write_bytes(b"fake")
    plan = [{"part_index": 1, "start_sec": 0.0, "end_sec": 120.0, "overlap_sec": 0.0}]
    saves = []
    stream_previous = []
    synth_previous = []

    async def fake_upload(client, path, *, fps, model):
        return SimpleNamespace(id="file-part-001")

    async def fake_wait(*args, **kwargs):
        return SimpleNamespace(status="active")

    async def fake_stream(client, *, model, file_id, prompt, on_progress, previous_response_id=None, timeout_sec=None):
        stream_previous.append(previous_response_id)
        return analyzer.ResponseCallResult(
            text="分片分析",
            usage={"total_tokens": 1},
            response_id="resp-chunk",
        )

    async def fake_text(client, *, model, prompt, on_progress, previous_response_id=None, timeout_sec=None):
        synth_previous.append(previous_response_id)
        return analyzer.ResponseCallResult(
            text="最终汇总",
            usage={"total_tokens": 1},
            response_id=None,
        )

    def fake_load_response_memory(**kwargs):
        return {"response_id": "resp-old"}

    def fake_save_response_memory(**kwargs):
        saves.append(kwargs)

    old_upload = analyzer._upload_with_preprocess
    old_wait = analyzer._wait_for_active
    old_stream = analyzer._stream_responses
    old_text = analyzer._call_text_responses
    old_load = analyzer.load_response_memory
    old_save = analyzer.save_response_memory
    try:
        analyzer._upload_with_preprocess = fake_upload
        analyzer._wait_for_active = fake_wait
        analyzer._stream_responses = fake_stream
        analyzer._call_text_responses = fake_text
        analyzer.load_response_memory = fake_load_response_memory
        analyzer.save_response_memory = fake_save_response_memory
        results = asyncio.run(analyzer._analyze_video_chunks(
            [chunk_path],
            plan,
            {"knowledge_ingest": "基础拆解 prompt"},
            files_client=SimpleNamespace(),
            responses_client=SimpleNamespace(),
            model="doubao-seed-2-0-lite-260428",
            quality="quality",
            full_duration=120.0,
            source_id="aweme-memory",
            strategy={"ok": True, "chunks": []},
            file_active_timeout_sec=120,
            response_timeout_sec=900,
            on_progress=None,
        ))
    finally:
        analyzer._upload_with_preprocess = old_upload
        analyzer._wait_for_active = old_wait
        analyzer._stream_responses = old_stream
        analyzer._call_text_responses = old_text
        analyzer.load_response_memory = old_load
        analyzer.save_response_memory = old_save

    assert results["knowledge_ingest"].response_id == "resp-old"
    assert stream_previous == [None]
    assert synth_previous == ["resp-old"]
    assert saves and saves[0]["response_id"] is None
    assert saves[0]["file_id"] == "file-part-001"
    assert saves[0]["flow_version"] == "chunked-v1"
    assert saves[0]["chunked"] is True


def test_websocket_config_writer_rejects_agent_plan_payload_key(tmp: Path) -> None:
    import asyncio
    import sys

    runtime = tmp / "ws-runtime-plan"
    os.environ["AGENT_WIKI_HOME"] = str(runtime)
    sys.path.insert(0, str(ROOT / "server"))
    from websocket_server import LibrarianServer

    vault = tmp / "ws-vault-plan"
    vault.mkdir()
    (vault / ".obsidian").mkdir()
    (vault / "index.md").write_text("# 知识库索引\n", encoding="utf-8")

    server = LibrarianServer()
    try:
        asyncio.run(server.handle_config_update({
            "provider": "volcengine_agent_plan",
            "apiKey": "plan-key",
            "agentPlanApiKey": "plan-key",
            "agentPlanEndpoint": "https://ark.cn-beijing.volces.com/api/plan/v3",
            "model": "doubao-seed-2.0-lite",
            "vaultPath": str(vault),
        }))
    except ValueError as e:
        assert "Agent Plan endpoint" in str(e)
    else:
        raise AssertionError("old Agent Plan endpoint must be rejected")

    assert not (runtime / "config.toml").exists()


def test_websocket_config_writer_rejects_invalid_explicit_endpoints(tmp: Path) -> None:
    import asyncio
    import sys

    os.environ["AGENT_WIKI_HOME"] = str(tmp / "ws-runtime-invalid-endpoints")
    sys.path.insert(0, str(ROOT / "server"))
    from websocket_server import LibrarianServer

    vault = tmp / "ws-vault-invalid-endpoints"
    vault.mkdir()
    (vault / ".obsidian").mkdir()
    (vault / "index.md").write_text("# 知识库索引\n", encoding="utf-8")

    invalid_payloads = [
        (
            {
                "llm": {
                    "provider": "doubao",
                    "apiKey": "test-key",
                    "endpoint": "http://evil.example.invalid/api/v3",
                },
                "vaultPath": str(vault),
            },
            "HTTPS",
        ),
        (
            {
                "llm": {
                    "provider": "doubao",
                    "apiKey": "test-key",
                    "endpoint": "https://evil.example.invalid/api/v3",
                },
                "vaultPath": str(vault),
            },
            "可信 Ark 官方域名",
        ),
        (
            {
                "provider": "doubao",
                "apiKey": "test-key",
                "endpoint": "https://user:pass@ark.cn-beijing.volces.com/api/v3",
                "vaultPath": str(vault),
            },
            "账号密码",
        ),
        (
            {
                "provider": "doubao",
                "apiKey": "test-key",
                "endpoint": "https://ark.cn-beijing.volces.com/api/plan/v3",
                "vaultPath": str(vault),
            },
            "Agent Plan endpoint",
        ),
    ]
    for index, (payload, expected) in enumerate(invalid_payloads):
        runtime = tmp / f"ws-runtime-invalid-endpoints-{index}"
        os.environ["AGENT_WIKI_HOME"] = str(runtime)
        server = LibrarianServer()
        try:
            asyncio.run(server.handle_config_update(payload))
        except ValueError as e:
            assert expected in str(e)
        else:
            raise AssertionError(f"invalid config endpoint must be rejected: {payload}")
        assert not (runtime / "config.toml").exists()


def test_websocket_config_writer_uses_explicit_ark_key_when_old_provider_present(tmp: Path) -> None:
    import asyncio
    import sys

    runtime = tmp / "ws-runtime-plan-fallback"
    os.environ["AGENT_WIKI_HOME"] = str(runtime)
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
    os.environ["AGENT_WIKI_HOME"] = str(runtime)
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

    os.environ["AGENT_WIKI_HOME"] = str(tmp / "health-plan-runtime")
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
    (skill_pkg / "SKILL.md").write_text("---\nname: agent-wiki\n---\n", encoding="utf-8")
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

    try:
        asyncio.run(analyzer.analyze_video(
            video,
            "prompt",
            api_key="key",
            endpoint="https://user:pass@ark.cn-beijing.volces.com/api/v3",
            model="doubao-seed-2-0-lite-260428",
        ))
    except analyzer.AnalyzerError as e:
        assert "账号密码" in str(e)
    else:
        raise AssertionError("endpoint with userinfo must be rejected")

    try:
        asyncio.run(analyzer.analyze_video(
            video,
            "prompt",
            api_key="key",
            endpoint="http://evil.example.invalid/api/v3",
            model="doubao-seed-2-0-lite-260428",
        ))
    except analyzer.AnalyzerError as e:
        assert "HTTPS" in str(e)
    else:
        raise AssertionError("non-HTTPS endpoint must be rejected")

    try:
        asyncio.run(analyzer.analyze_video(
            video,
            "prompt",
            api_key="key",
            endpoint="https://evil.example.invalid/api/v3",
            model="doubao-seed-2-0-lite-260428",
        ))
    except analyzer.AnalyzerError as e:
        assert "可信 Ark 官方域名" in str(e)
    else:
        raise AssertionError("untrusted endpoint host must be rejected")


def test_analyzer_rejects_invalid_image_endpoint(tmp: Path) -> None:
    import asyncio
    import sys

    sys.path.insert(0, str(SCRIPTS))
    import analyzer

    image = tmp / "image.jpg"
    image.write_bytes(b"fake-image")
    invalid_cases = [
        ("https://ark.cn-beijing.volces.com/api/plan/v3", "Agent Plan 不再作为运行通道"),
        ("http://evil.example.invalid/api/v3", "HTTPS"),
        ("https://evil.example.invalid/api/v3", "可信 Ark 官方域名"),
        ("https://user:pass@ark.cn-beijing.volces.com/api/v3", "账号密码"),
    ]
    for endpoint, expected in invalid_cases:
        try:
            asyncio.run(analyzer.analyze_images_many(
                [image],
                {"knowledge_ingest": "prompt"},
                api_key="key",
                endpoint=endpoint,
                model="doubao-seed-2-0-lite-260428",
            ))
        except analyzer.AnalyzerError as e:
            assert expected in str(e)
        else:
            raise AssertionError(f"image endpoint must be rejected: {endpoint}")


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

    os.environ["AGENT_WIKI_HOME"] = str(tmp / "health-runtime")
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
        try:
            server._check_model_health_sync({
                "provider": "doubao",
                "apiKey": "secret-health-key",
                "model": "doubao-seed-2-0-lite-260428",
                "endpoint": "http://evil.example.invalid/api/v3",
            })
        except ValueError as exc:
            assert "HTTPS" in str(exc)
        else:
            raise AssertionError("invalid endpoint must be rejected")
        try:
            server._check_model_health_sync({
                "provider": "doubao",
                "apiKey": "secret-health-key",
                "model": "doubao-seed-2-0-lite-260428",
                "endpoint": "https://evil.example.invalid/api/v3",
            })
        except ValueError as exc:
            assert "可信 Ark 官方域名" in str(exc)
        else:
            raise AssertionError("untrusted endpoint host must be rejected")
    finally:
        websocket_server.urllib.request.urlopen = old_urlopen

    assert calls == []


def test_model_health_status_persists(tmp: Path) -> None:
    import sys

    runtime = tmp / "health-persist-runtime"
    os.environ["AGENT_WIKI_HOME"] = str(runtime)
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
            "endpoint": "https://ark.cn-beijing.volces.com/api/v3",
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

    os.environ["AGENT_WIKI_HOME"] = str(tmp / "ws-runtime-task")
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
        "source": "extension_popup",
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

    task_file = Path(os.environ["AGENT_WIKI_HOME"]) / "inbox" / f"{task_id}.json"
    status_file = Path(os.environ["AGENT_WIKI_HOME"]) / "status" / f"{task_id}.json"
    assert task_file.exists()
    task = json.loads(task_file.read_text(encoding="utf-8"))
    assert task["type"] == "douyin_ingest"
    assert task["ingest_intent"] == "knowledge_ingest"
    assert task["ingest_intents"] == ["knowledge_ingest", "viral_breakdown"]
    assert status_file.exists()
    status = json.loads(status_file.read_text(encoding="utf-8"))
    assert status["stage"] == "queued"
    assert status["source"] == "extension_popup"
    assert status["ingest_intent"] == "knowledge_ingest"
    assert status["ingest_intents"] == ["knowledge_ingest", "viral_breakdown"]

    snapshot = server.task_status_snapshot()
    assert snapshot["running"] == 1
    assert snapshot["items"][0]["id"] == task_id
    assert snapshot["items"][0]["stageLabel"] == "排队中"
    assert snapshot["items"][0]["ingestIntent"] == "knowledge_ingest"
    assert snapshot["items"][0]["ingestIntents"] == ["knowledge_ingest", "viral_breakdown"]


def test_websocket_public_task_status_exposes_derived_candidates(tmp: Path) -> None:
    import sys

    runtime = tmp / "ws-runtime-derived"
    os.environ["AGENT_WIKI_HOME"] = str(runtime)
    sys.path.insert(0, str(ROOT / "server"))
    from websocket_server import LibrarianServer

    status_dir = runtime / "status"
    status_dir.mkdir(parents=True)
    derived_tasks = [{
        "id": "dt-status",
        "name": "Status API",
        "targetType": "official_doc",
        "targetUrl": "https://example.com/docs/status-api",
        "decision": "candidate",
        "status": "candidate",
        "score": 82,
        "reason": "需要核验状态 API。",
    }]
    derived_summary = {"candidate": 1, "rejected": 1, "suppressed": 0}
    audit_artifacts = {
        "dir": "run-artifacts/task-derived-status",
        "files": {"derive_input": "run-artifacts/task-derived-status/05-derive/00-input.json"},
    }
    status_file = status_dir / "task-derived-status.json"
    status_file.write_text(json.dumps({
        "id": "task-derived-status",
        "ok": None,
        "stage": "derived_candidates_ready",
        "started_at": 100.0,
        "updated_at": 105.0,
        "source_url": "https://v.douyin.com/status/",
        "ingest_intent": "knowledge_ingest",
        "ingest_intents": ["knowledge_ingest"],
        "derived_tasks": derived_tasks,
        "derived_summary": derived_summary,
        "derived_audit_artifacts": audit_artifacts,
    }, ensure_ascii=False), encoding="utf-8")

    server = LibrarianServer(enable_task_runner=False)
    item = server._public_task_status(status_file)
    assert item["derivedTasks"] == derived_tasks
    for key, value in derived_summary.items():
        assert item["derivedSummary"][key] == value
    assert item["derivedAuditArtifacts"] == audit_artifacts
    assert item["stageLabel"] == "派生候选已生成"

    snapshot = server.task_status_snapshot()
    assert snapshot["items"][0]["id"] == "task-derived-status"
    assert snapshot["items"][0]["derivedTasks"] == derived_tasks
    for key, value in derived_summary.items():
        assert snapshot["items"][0]["derivedSummary"][key] == value
    assert snapshot["items"][0]["derivedAuditArtifacts"] == audit_artifacts


def test_websocket_auto_enqueues_derived_ingest_task(tmp: Path) -> None:
    import asyncio
    import sys

    os.environ["AGENT_WIKI_HOME"] = str(tmp / "ws-runtime-derived-auto")
    sys.path.insert(0, str(ROOT / "server"))
    from websocket_server import LibrarianServer

    server = LibrarianServer(enable_task_runner=False)
    parent_status = {
        "id": "parent-task",
        "ok": True,
        "stage": "done",
        "source_url": "https://v.douyin.com/auto/",
        "title": "Agent Harness 视频",
        "assets": [{
            "vault_path": str(tmp / "vault" / "知识资产" / "知识入库" / "20260705-parent.md"),
        }],
        "derived_tasks": [{
            "id": "dt-auto",
            "name": "LangGraph",
            "targetType": "github_project",
            "taskKind": "github_project_ingest",
            "status": "auto_ready",
            "autoEligible": True,
            "score": 91,
            "confidence": 0.9,
            "reason": "视频用它做 Agent 状态图。",
        }],
    }
    queued = asyncio.run(server.enqueue_auto_derived_tasks("parent-task", parent_status))
    assert len(queued) == 1
    child = queued[0]
    assert child["type"] == "derived_ingest"
    assert child["parent_task_id"] == "parent-task"
    assert child["candidate"]["id"] == "dt-auto"
    task_file = tmp / "ws-runtime-derived-auto" / "inbox" / f"{child['id']}.json"
    status_file = tmp / "ws-runtime-derived-auto" / "status" / f"{child['id']}.json"
    assert task_file.exists()
    assert status_file.exists()
    written = json.loads(task_file.read_text(encoding="utf-8"))
    child_status = json.loads(status_file.read_text(encoding="utf-8"))
    assert written["type"] == "derived_ingest"
    assert written["parent_asset_path"].endswith("20260705-parent.md")
    assert child_status["audit_artifacts"]["dir"] == f"run-artifacts/{child['id']}"
    sidecar = json.loads((tmp / "ws-runtime-derived-auto" / "derived-actions" / "parent-task.json").read_text(encoding="utf-8"))
    assert sidecar["items"]["dt-auto"]["childTaskId"] == child["id"]


def test_websocket_derived_actions_require_ready_parent_and_valid_state(tmp: Path) -> None:
    import asyncio
    import sys

    runtime = tmp / "ws-runtime-derived-action"
    os.environ["AGENT_WIKI_HOME"] = str(runtime)
    sys.path.insert(0, str(ROOT / "server"))
    from websocket_server import LibrarianServer

    status_dir = runtime / "status"
    status_dir.mkdir(parents=True)
    parent_file = status_dir / "parent-action.json"
    parent_file.write_text(json.dumps({
        "id": "parent-action",
        "ok": None,
        "stage": "derived_candidates_ready",
        "source_url": "https://v.douyin.com/action/",
        "derived_tasks": [{
            "id": "dt-action",
            "name": "LangGraph",
            "targetType": "github_project",
            "decision": "candidate",
            "status": "candidate",
        }],
    }, ensure_ascii=False), encoding="utf-8")
    server = LibrarianServer(enable_task_runner=False)
    reply = asyncio.run(server.handle_derived_task_action({
        "action": "confirm",
        "taskId": "parent-action",
        "derivedTaskId": "dt-action",
    }))
    assert reply["type"] == "derived_task_action_rejected"
    assert reply["reason"] == "parent_asset_not_ready"

    parent_asset = tmp / "vault" / "知识资产" / "知识入库" / "20260705-parent.md"
    parent_asset.parent.mkdir(parents=True)
    parent_asset.write_text("---\nrelated: []\n---\n# Parent\n", encoding="utf-8")
    parent_file.write_text(json.dumps({
        "id": "parent-action",
        "ok": True,
        "stage": "done",
        "source_url": "https://v.douyin.com/action/",
        "assets": [{"vault_path": str(parent_asset), "derived_tasks": []}],
        "derived_tasks": [{
            "id": "dt-existing",
            "name": "LangGraph",
            "targetType": "github_project",
            "decision": "candidate",
            "status": "existing_related",
        }],
    }, ensure_ascii=False), encoding="utf-8")
    reply = asyncio.run(server.handle_derived_task_action({
        "action": "confirm",
        "taskId": "parent-action",
        "derivedTaskId": "dt-existing",
    }))
    assert reply["type"] == "derived_task_action_rejected"
    assert reply["reason"] == "candidate_status_not_executable"


def test_websocket_derived_enqueue_is_idempotent_and_redacts_urls(tmp: Path) -> None:
    import asyncio
    import sys

    runtime = tmp / "ws-runtime-derived-idempotent"
    os.environ["AGENT_WIKI_HOME"] = str(runtime)
    sys.path.insert(0, str(ROOT / "server"))
    from websocket_server import LibrarianServer

    vault = tmp / "vault"
    parent = vault / "知识资产" / "知识入库" / "20260705-parent.md"
    parent.parent.mkdir(parents=True, exist_ok=True)
    parent.write_text("---\nrelated: []\n---\n# Parent\n", encoding="utf-8")
    server = LibrarianServer(enable_task_runner=False)
    parent_status = {
        "id": "parent-idem",
        "ok": True,
        "stage": "done",
        "source_url": "https://v.douyin.com/idem/",
        "assets": [{
            "vault_path": str(parent),
            "derived_tasks": [{"id": "dt-idem"}],
        }],
        "derived_tasks": [{
            "id": "dt-idem",
            "name": "LangGraph",
            "targetType": "github_project",
            "taskKind": "github_project_ingest",
            "status": "auto_ready",
            "autoEligible": True,
            "targetUrl": "https://github.com/langchain-ai/langgraph?access_token=secret&utm_source=x",
        }],
    }
    queued = asyncio.run(server.enqueue_auto_derived_tasks("parent-idem", parent_status))
    assert len(queued) == 1
    child_id = queued[0]["id"]
    status_file = runtime / "status" / f"{child_id}.json"
    first_status = json.loads(status_file.read_text(encoding="utf-8"))
    assert first_status["source_url"] == "https://github.com/langchain-ai/langgraph?utm_source=x"
    assert first_status["ingest_intent"] == "derived_ingest"
    assert first_status["ingest_intents"] == ["derived_ingest"]
    assert "secret" not in status_file.read_text(encoding="utf-8")

    status_file.write_text(json.dumps({"id": child_id, "ok": True, "stage": "done"}), encoding="utf-8")
    queued_again = asyncio.run(server.enqueue_auto_derived_tasks("parent-idem", parent_status))
    assert queued_again == []
    assert json.loads(status_file.read_text(encoding="utf-8"))["stage"] == "done"


def test_derive_executor_resolves_github_name_and_links_parent(tmp: Path) -> None:
    import sys

    sys.path.insert(0, str(SCRIPTS))
    import derive_executor

    def fake_json_request(url: str, *, timeout: int = 20):
        if "search/repositories" in url:
            return {
                "items": [
                    {
                        "name": "langgraph",
                        "full_name": "langchain-ai/langgraph",
                        "description": "Build resilient language agents as graphs",
                        "stargazers_count": 10000,
                        "owner": {"login": "langchain-ai"},
                    },
                    {
                        "name": "langgraph-demo",
                        "full_name": "someone/langgraph-demo",
                        "description": "Demo project",
                        "stargazers_count": 1,
                        "owner": {"login": "someone"},
                    },
                ]
            }
        if url.endswith("/readme"):
            return {
                "content": "TGFuZ0dyYXBoIGJ1aWxkcyBzdGF0ZWZ1bCBtdWx0aS1hY3RvciBhZ2VudHMgYXMgZ3JhcGhzLg=="
            }
        if "/repos/someone/langgraph-demo" in url:
            return {
                "name": "langgraph-demo",
                "full_name": "someone/langgraph-demo",
                "description": "Demo project",
                "language": "Python",
                "stargazers_count": 1,
                "forks_count": 0,
                "open_issues_count": 0,
                "license": {"spdx_id": "MIT"},
                "pushed_at": "2026-07-01T00:00:00Z",
                "html_url": "https://github.com/someone/langgraph-demo",
                "owner": {"login": "someone"},
            }
        return {
            "name": "langgraph",
            "full_name": "langchain-ai/langgraph",
            "description": "Build resilient language agents as graphs",
            "language": "Python",
            "stargazers_count": 10000,
            "forks_count": 1200,
            "open_issues_count": 300,
            "license": {"spdx_id": "MIT"},
            "pushed_at": "2026-07-01T00:00:00Z",
            "html_url": "https://github.com/langchain-ai/langgraph",
            "owner": {"login": "langchain-ai"},
        }

    original = derive_executor._json_request
    derive_executor._json_request = fake_json_request
    try:
        target = derive_executor.resolve_github_target({
            "name": "LangGraph",
            "reason": "视频说它用于 Agent 状态图和工作流编排。",
            "evidence": ["口播 Agent 状态图"],
        })
    finally:
        derive_executor._json_request = original
    assert target.url == "https://github.com/langchain-ai/langgraph"
    assert target.confidence >= 0.75

    vault = tmp / "vault"
    existing = vault / "知识资产" / "GitHub项目" / "20260705-existing-langgraph.md"
    existing.parent.mkdir(parents=True)
    existing.write_text(
        "---\n"
        'title: "LangGraph 已有资产"\n'
        'repo: "https://github.com/langchain-ai/langgraph?utm_source=old"\n'
        "related: []\n"
        "---\n"
        "# LangGraph 已有资产\n",
        encoding="utf-8",
    )
    existing_path, existing_title = derive_executor._existing_asset_for_target(vault, target)
    assert existing_path == existing
    assert existing_title == "LangGraph 已有资产"

    parent = tmp / "20260705-parent.md"
    child = tmp / "20260705-langgraph.md"
    parent.write_text(
        "---\nrelated: []\n---\n\n# Parent\n\n正文\n",
        encoding="utf-8",
    )
    child.write_text("# Child\n", encoding="utf-8")
    touched = derive_executor._link_parent_child(parent, child, "LangGraph 项目", "implements")
    text = parent.read_text(encoding="utf-8")
    assert touched == [parent]
    assert "[[20260705-langgraph|LangGraph 项目]]" in text
    assert "related:" in text


def test_derive_executor_resolves_github_from_cleaned_chinese_candidate(tmp: Path) -> None:
    import sys
    from urllib.parse import parse_qs, unquote, urlparse

    sys.path.insert(0, str(SCRIPTS))
    import derive_executor

    search_queries: list[str] = []

    def repo_meta(owner: str, name: str, description: str, stars: int) -> dict:
        return {
            "name": name,
            "full_name": f"{owner}/{name}",
            "description": description,
            "language": "TypeScript",
            "stargazers_count": stars,
            "forks_count": 1200 if owner == "nexu-io" else 0,
            "open_issues_count": 80 if owner == "nexu-io" else 0,
            "license": {"spdx_id": "AGPL-3.0"},
            "pushed_at": "2026-07-01T00:00:00Z",
            "html_url": f"https://github.com/{owner}/{name}",
            "owner": {"login": owner},
        }

    correct = repo_meta(
        "nexu-io",
        "open-design",
        "The Vibe Design Workspace and open-source Claude Design alternative. Coding agents generate prototypes, slides, images and video.",
        75105,
    )
    stale = repo_meta("manalkaff", "opendesign", "claude.ai/design open-sourced", 207)
    generic = repo_meta("shadcn-ui", "ui", "Beautiful components for open source design systems.", 118137)

    def fake_json_request(url: str, *, timeout: int = 20):
        if "search/repositories" in url:
            q = unquote(parse_qs(urlparse(url).query).get("q", [""])[0])
            search_queries.append(q)
            q_lower = q.lower()
            if '"open design"' in q_lower or "open-design" in q_lower:
                return {"items": [generic, correct, stale]}
            if "opendesign" in q_lower:
                return {"items": [stale]}
            return {"items": []}
        if url.endswith("/readme"):
            if "/repos/nexu-io/open-design" in url:
                return {
                    "content": "T3BlbiBEZXNpZ24gdXNlcyBDbGF1ZGUgQ29kZSwgQ29kZXgsIEN1cnNvciBhbmQgbG9jYWwgQ0xJcyB0byBidWlsZCBoaWdoLWZpZGVsaXR5IHByb3RvdHlwZXMu"
                }
            return {"content": ""}
        if "/repos/nexu-io/open-design" in url:
            return correct
        if "/repos/shadcn-ui/ui" in url:
            return generic
        if "/repos/manalkaff/opendesign" in url:
            return stale
        raise AssertionError(f"unexpected URL {url}")

    original = derive_executor._json_request
    derive_executor._json_request = fake_json_request
    try:
        target = derive_executor.resolve_github_target({
            "name": "OpenDesign 开源AI原型设计项目",
            "searchQuery": "OpenDesign AI 原型设计 GitHub repository",
            "parentContext": "支持 Claude Code、Codex 等 CLI 生成 prototype 和设计系统",
            "evidence": ["视频展示 OpenDesign 的 Quickstart、CLI 配置和高保真原型能力"],
        })
    finally:
        derive_executor._json_request = original

    assert target.url == "https://github.com/nexu-io/open-design"
    assert target.title == "nexu-io/open-design"
    assert any('"Open Design"' in item or "Open-Design" in item or "open-design" in item for item in search_queries)
    assert all("OpenDesign 开源AI原型设计项目" not in item for item in search_queries)


def test_derive_executor_parent_link_prefers_clean_frontmatter_title(tmp: Path) -> None:
    import sys

    sys.path.insert(0, str(SCRIPTS))
    import derive_executor

    vault = tmp / "vault"
    parent = vault / "知识资产" / "知识入库" / "20260705-parent-long-title.md"
    parent.parent.mkdir(parents=True, exist_ok=True)
    parent.write_text(
        "---\n"
        'title: "Open Design：超火开源免费AI原型设计 open…"\n'
        "related: []\n"
        "---\n\n"
        "# Parent\n",
        encoding="utf-8",
    )
    noisy_title = "Open Design 完整标题\nOpen Design 使用\ngit clone https://github.com/nexu-io/open-design.git\npnpm install"
    link, path = derive_executor._parent_link({
        "parent_asset_path": str(parent),
        "parent_title": noisy_title,
    }, vault)

    assert path == parent
    assert link == "[[20260705-parent-long-title|Open Design：超火开源免费AI原型设计 open…]]"
    assert "git clone" not in link
    assert "\n" not in link


def test_derive_executor_existing_child_backlink_is_cleaned(tmp: Path) -> None:
    import sys

    sys.path.insert(0, str(SCRIPTS))
    import derive_executor

    parent = tmp / "20260705-parent.md"
    child = tmp / "20260705-child.md"
    parent.write_text(
        "---\n"
        'title: "短父标题"\n'
        "related: []\n"
        "---\n\n"
        "# Parent\n",
        encoding="utf-8",
    )
    dirty_parent_link = "[[20260705-parent|长标题\n命令行\npnpm install]]"
    clean_parent_link = "[[20260705-parent|短父标题]]"
    child.write_text(
        "---\n"
        f"related: [{json.dumps(dirty_parent_link, ensure_ascii=False)}]\n"
        "---\n\n"
        "# Child\n\n"
        "# 模型重复标题\n\n"
        "## 被引用\n"
        f"- {dirty_parent_link}：implements\n",
        encoding="utf-8",
    )

    touched = derive_executor._link_child_back_to_parent(parent, child, clean_parent_link, "implements")
    text = child.read_text(encoding="utf-8")
    assert touched == [child]
    assert clean_parent_link in text
    assert "pnpm install" not in text
    assert dirty_parent_link not in text
    assert "# Child" in text
    assert "# 模型重复标题" not in text


def test_derive_executor_sanitizes_leading_h1() -> None:
    import sys

    sys.path.insert(0, str(SCRIPTS))
    import derive_executor

    body = derive_executor._sanitize_generated_body("# 重复标题\n\n## 项目结论\n正文")
    assert body.startswith("## 项目结论")
    assert "# 重复标题" not in body


def test_derive_executor_execute_task_writes_child_and_backlinks(tmp: Path) -> None:
    import sys

    os.environ["AGENT_WIKI_HOME"] = str(tmp / "derive-exec-runtime")
    sys.path.insert(0, str(SCRIPTS))
    import derive_executor
    from config_loader import Config

    vault = tmp / "derive-e2e-vault"
    parent = vault / "知识资产" / "知识入库" / "20260705-parent.md"
    parent.parent.mkdir(parents=True)
    parent.write_text(
        "---\n"
        'title: "父视频：Agent Harness"\n'
        "related: []\n"
        "---\n"
        "# 父视频：Agent Harness\n\n正文\n",
        encoding="utf-8",
    )
    cfg = Config(
        ark_api_key="test",
        ark_endpoint="https://ark.cn-beijing.volces.com/api/v3",
        analyzer_model="doubao-seed-2-0-lite-260428",
        analyzer_fallback="doubao-seed-2-0-mini-260428",
        strategy_model="doubao-seed-2-0-mini-260428",
        default_quality="quality",
        balanced_target_frames=240,
        quality_target_frames=1250,
        fps_min=0.2,
        fps_max=5.0,
        file_active_timeout_sec=120,
        cookie_path=tmp / "cookie.txt",
        vault_path=vault,
        vault_relative_root="知识资产/知识入库",
        server_enabled=True,
        server_host="127.0.0.1",
        server_port=8765,
        config_file=tmp / "config.toml",
    )

    target = derive_executor.ResolvedTarget(
        url="https://github.com/langchain-ai/langgraph",
        title="LangGraph 项目",
        kind="github_project",
        confidence=0.95,
        evidence=["GitHub API 搜索命中 langchain-ai/langgraph"],
        raw={
            "repo": {
                "full_name": "langchain-ai/langgraph",
                "description": "Build resilient language agents as graphs",
                "language": "Python",
                "stargazers_count": 10000,
                "forks_count": 1200,
                "open_issues_count": 300,
                "license": {"spdx_id": "MIT"},
                "pushed_at": "2026-07-01T00:00:00Z",
                "html_url": "https://github.com/langchain-ai/langgraph",
            },
            "readme": "LangGraph builds stateful multi-actor agents as graphs.",
        },
    )
    task = {
        "id": "child-task",
        "parent_task_id": "parent-task",
        "parent_asset_path": str(parent),
        "parent_title": "父视频：Agent Harness",
        "parent_source_url": "https://v.douyin.com/parent/",
        "candidate": {
            "id": "dt-e2e",
            "name": "LangGraph",
            "targetType": "github_project",
            "relationType": "implements",
            "reason": "父视频用它解释 Agent 状态图。",
            "evidence": ["口播 LangGraph"],
        },
    }

    class FakeStatusWriter:
        def __init__(self) -> None:
            self.updates = []

        def update(self, **fields):
            self.updates.append(fields)

    def fake_resolve(candidate, **_kwargs):
        return target

    def fake_model(config, prompt):
        assert "父资产与派生上下文" in prompt
        return (
            "父资产与派生上下文：\n"
            '{"candidate_name":"LangGraph","parent_source_url":"https://v.douyin.com/parent/"}\n\n'
            "目标来源材料：\n"
            '{"repo":{"full_name":"langchain-ai/langgraph"},"readme":"internal-labeled"}\n\n'
            "```json\n"
            '{"repo":{"full_name":"langchain-ai/langgraph"},"readme":"internal-fenced"}\n'
            "```\n\n"
            "## 项目结论\nLangGraph 适合沉淀为 Agent Harness 状态图工具。\n\n"
            "## 最小可运行路径\n安装依赖后从官方示例开始验证。"
        ), {"input_tokens": 10, "output_tokens": 20, "total_tokens": 30}

    original_resolve = derive_executor.resolve_target
    original_model = derive_executor._call_lite_model
    original_git = derive_executor._git_commit
    try:
        derive_executor.resolve_target = fake_resolve
        derive_executor._call_lite_model = fake_model
        derive_executor._git_commit = lambda vault_path, title, touched, asset_type: "committed"
        sw = FakeStatusWriter()
        summary = derive_executor.execute_derived_task(task, cfg, sw)
    finally:
        derive_executor.resolve_target = original_resolve
        derive_executor._call_lite_model = original_model
        derive_executor._git_commit = original_git

    child = Path(summary["vault_path"])
    assert child.exists()
    child_text = child.read_text(encoding="utf-8")
    parent_text = parent.read_text(encoding="utf-8")
    child_link = f"[[{child.stem}|LangGraph 项目]]"
    parent_link = f"[[{parent.stem}|父视频：Agent Harness]]"
    assert child_link in parent_text
    assert parent_link in child_text
    assert "## 被引用" in child_text
    assert "父资产与派生上下文" not in child_text
    assert "目标来源材料" not in child_text
    assert "candidate_name" not in child_text
    assert "internal-labeled" not in child_text
    assert "internal-fenced" not in child_text
    assert '"repo"' not in child_text
    assert '"readme"' not in child_text
    assert "derived_from:" in child_text
    assert "parent_candidate_id: \"dt-e2e\"" in child_text
    assert "## 相关资产" in parent_text
    assert (vault / "index.md").exists()
    assert summary["audit_artifacts"]["dir"] == "run-artifacts/child-task"
    artifact_files = summary["audit_artifacts"]["files"]
    for key in (
        "derive_executor_task",
        "derive_source_material",
        "derive_model_prompt",
        "derive_model_output_raw",
        "derive_model_output_sanitized",
        "derive_write_result",
        "derive_linkback",
    ):
        assert key in artifact_files
        artifact_path = tmp / "derive-exec-runtime" / artifact_files[key]
        assert artifact_path.exists(), key
    raw_output = (tmp / "derive-exec-runtime" / artifact_files["derive_model_output_raw"]).read_text(encoding="utf-8")
    sanitized_output = (tmp / "derive-exec-runtime" / artifact_files["derive_model_output_sanitized"]).read_text(encoding="utf-8")
    assert "父资产与派生上下文" in raw_output
    assert "父资产与派生上下文" not in sanitized_output
    write_result = json.loads((tmp / "derive-exec-runtime" / artifact_files["derive_write_result"]).read_text(encoding="utf-8"))
    assert write_result["mode"] == "new_asset"
    assert write_result["vault_path"] == str(child)

    all_stems = {path.stem for path in vault.glob("**/*.md")}
    for text in (parent_text, child_text):
        for match in re.findall(r"!?\[\[([^|\]#]+)", text):
            target_stem = match.split("|", 1)[0].split("#", 1)[0]
            assert target_stem in all_stems, target_stem
    assert any(update.get("stage") == "resolving_target" for update in sw.updates)
    assert summary["git_status"] == "committed"


def test_websocket_auto_enqueue_respects_ignored_candidate(tmp: Path) -> None:
    import asyncio
    import sys

    runtime = tmp / "ws-runtime-derived-ignore"
    os.environ["AGENT_WIKI_HOME"] = str(runtime)
    sys.path.insert(0, str(ROOT / "server"))
    from websocket_server import LibrarianServer

    server = LibrarianServer(enable_task_runner=False)
    parent_status = {
        "id": "parent-ignore",
        "ok": True,
        "stage": "done",
        "source_url": "https://v.douyin.com/ignore/",
        "derived_tasks": [{
            "id": "dt-ignore",
            "name": "LangGraph",
            "targetType": "github_project",
            "taskKind": "github_project_ingest",
            "status": "auto_ready",
            "autoEligible": True,
            "targetUrl": "https://github.com/langchain-ai/langgraph",
        }],
    }
    server._update_derived_action_item(
        "parent-ignore",
        "dt-ignore",
        status="ignored",
        ignoredAt=123,
    )

    queued = asyncio.run(server.enqueue_auto_derived_tasks("parent-ignore", parent_status))
    assert queued == []
    action = server._read_derived_actions("parent-ignore")["items"]["dt-ignore"]
    assert action["status"] == "ignored"
    child_id = server._derived_child_task_id("parent-ignore", "dt-ignore")
    assert not (runtime / "inbox" / f"{child_id}.json").exists()
    assert not (runtime / "status" / f"{child_id}.json").exists()


def test_websocket_rejects_invalid_ingest_intent(tmp: Path) -> None:
    import asyncio
    import sys

    os.environ["AGENT_WIKI_HOME"] = str(tmp / "ws-runtime-task-invalid")
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
        test_config_loader_rejects_invalid_ark_endpoints(tmp)
        test_netscape_cookie_conversion(tmp)
        test_douyin_share_text_url_extraction()
        test_download_video_resumes_partial_file(tmp)
        test_ingest_url_preserves_share_text_argument()
        test_derive_strategy_scores_limits_dedupes_and_redacts(tmp)
        test_derive_strategy_marks_high_confidence_github_without_url_auto_ready(tmp)
        test_derive_strategy_auto_blocks_non_github_and_unsafe_urls(tmp)
        test_knowledge_prompts_do_not_force_github_manual_confirmation()
        test_derive_strategy_ignores_candidates_json_outside_derived_section(tmp)
        test_derived_status_prefers_knowledge_decision_even_when_second()
        test_vault_write_schema(tmp)
        test_vault_write_includes_derived_tasks_and_record(tmp)
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
        test_status_writer_redacts_sensitive_fields(tmp)
        test_long_video_strategy_accepts_top_level_segments_and_partial_fallback()
        test_long_video_strategy_validation_falls_back_to_5fps()
        test_long_video_strategy_does_not_raise_fps_for_concepts_only()
        test_long_video_strategy_missing_required_fields_requests_repair()
        test_prepare_long_video_strategy_repairs_json_with_strategy_model(tmp)
        test_prepare_long_video_strategy_chunks_unsafe_full_overview(tmp)
        test_strategy_log_redacts_sensitive_values(tmp)
        test_chunk_analysis_uses_strategy_fps_and_context(tmp)
        test_chunk_analysis_retries_transient_stream_failure(tmp)
        test_chunk_analysis_reuses_existing_chunk_artifact_on_rerun(tmp)
        test_chunk_synthesis_without_response_id_does_not_refresh_memory(tmp)
        test_websocket_config_writer_rejects_agent_plan_payload_key(tmp)
        test_websocket_config_writer_rejects_invalid_explicit_endpoints(tmp)
        test_websocket_config_writer_uses_explicit_ark_key_when_old_provider_present(tmp)
        test_config_loader_does_not_use_agent_plan_section(tmp)
        test_vault_discovery_is_strict(tmp)
        test_analyzer_ark_file_protocol(tmp)
        test_analyzer_rejects_agent_plan_endpoint(tmp)
        test_analyzer_rejects_invalid_image_endpoint(tmp)
        test_analyzer_wait_and_stream_protocol(tmp)
        test_model_health_check_ignores_old_agent_plan_provider(tmp)
        test_model_health_check_redacts_secret(tmp)
        test_model_health_status_persists(tmp)
        test_codex_handoff_is_marked_archived()
        test_websocket_accepts_task_request(tmp)
        test_websocket_public_task_status_exposes_derived_candidates(tmp)
        test_websocket_auto_enqueues_derived_ingest_task(tmp)
        test_websocket_derived_actions_require_ready_parent_and_valid_state(tmp)
        test_websocket_derived_enqueue_is_idempotent_and_redacts_urls(tmp)
        test_derive_executor_resolves_github_name_and_links_parent(tmp)
        test_derive_executor_resolves_github_from_cleaned_chinese_candidate(tmp)
        test_derive_executor_parent_link_prefers_clean_frontmatter_title(tmp)
        test_derive_executor_existing_child_backlink_is_cleaned(tmp)
        test_derive_executor_sanitizes_leading_h1()
        test_derive_executor_execute_task_writes_child_and_backlinks(tmp)
        test_websocket_auto_enqueue_respects_ignored_candidate(tmp)
        test_websocket_rejects_invalid_ingest_intent(tmp)
    print("P0 static checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
