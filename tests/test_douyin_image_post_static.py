#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "deps" / "douyin" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


@dataclass
class FakeImageResult:
    text: str = "这是一篇图文作品分析，提炼了画面顺序、信息重点和可复用方法。"
    file_id: str = "inline-images"
    quality: str = "quality"
    model: str = "doubao-seed-2-0-lite-260428"
    image_count: int = 2
    usage: dict[str, Any] = field(default_factory=lambda: {"total_tokens": 9})
    truncated: bool = False
    # Compatibility guards: image-post code should not need these video fields.
    fps_used: float = 0.0
    target_frames: int = 0
    actual_frames_estimate: int = 0


class FakeStatusWriter:
    def __init__(self) -> None:
        self.updates: list[dict[str, Any]] = []
        self.progress_events: list[tuple[str, dict[str, Any]]] = []

    def update(self, **fields: Any) -> None:
        self.updates.append(dict(fields))

    def progress(self, stage: str, info: dict[str, Any]) -> None:
        self.progress_events.append((stage, dict(info)))


class DouyinImagePostStaticTests(unittest.TestCase):
    def _image_meta(self):
        import downloader

        return downloader.VideoMeta(
            aweme_id="7390000000000000000",
            title="图文作品测试",
            author="Tester",
            author_sec_uid="sec",
            duration_sec=0.0,
            cover_url="https://example.invalid/01.jpg",
            play_url="",
            source_url="https://www.douyin.com/note/7390000000000000000",
            raw={},
            media_type="image_post",
            image_urls=[
                "https://example.invalid/01.jpg",
                "https://example.invalid/02.webp",
            ],
        )

    def _config(self, tmp: Path):
        from config_loader import Config

        vault = tmp / "vault"
        vault.mkdir()
        runtime = tmp / "runtime"
        runtime.mkdir()
        return Config(
            ark_api_key="dummy-key",
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

    def test_image_post_metadata_detects_media_type_aweme_type_and_images(self) -> None:
        import downloader

        base_detail = {
            "desc": "图文 metadata 识别",
            "author": {"nickname": "Author", "sec_uid": "sec"},
            "status": {"is_delete": False, "allow_share": True},
            "images": [
                {"url_list": ["https://example.invalid/01.jpg"]},
                {"display_image": {"url_list": ["https://example.invalid/02.webp"]}},
            ],
            "video": {
                "play_addr": {"url_list": ["https://example.invalid/video.mp4"]},
                "duration": 6000,
            },
        }

        marker_cases = [
            {"media_type": 2},
            {"aweme_type": 68},
            {"image_infos": [{"width": 1080, "height": 1440}]},
        ]
        for marker in marker_cases:
            with self.subTest(marker=marker):
                detail = {**base_detail, **marker}
                meta = downloader._extract_video_meta(
                    "7390000000000000000",
                    {"aweme_detail": detail},
                    "https://www.douyin.com/note/7390000000000000000",
                )
                self.assertEqual(meta.media_type, "image_post")
                self.assertEqual(meta.aweme_id, "7390000000000000000")
                self.assertEqual(meta.play_url, "")
                self.assertEqual(meta.duration_sec, 0.0)
                self.assertEqual(meta.cover_url, "https://example.invalid/01.jpg")
                self.assertEqual(meta.image_urls, [
                    "https://example.invalid/01.jpg",
                    "https://example.invalid/02.webp",
                ])

    def test_analyze_images_sends_input_image_payload(self) -> None:
        import analyzer

        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            first = tmp / "01.jpg"
            second = tmp / "02.webp"
            first.write_bytes(b"first-image")
            second.write_bytes(b"second-image")

            class FakeResponses:
                def __init__(self) -> None:
                    self.kwargs: dict[str, Any] | None = None

                def create(self, **kwargs):
                    self.kwargs = kwargs
                    return SimpleNamespace(
                        output=[{
                            "content": [{
                                "type": "output_text",
                                "text": "图文分析完成",
                            }],
                        }],
                        usage={"total_tokens": 7},
                    )

            responses = FakeResponses()
            old_build_response_client = analyzer._build_response_client
            try:
                analyzer._build_response_client = (
                    lambda api_key, endpoint, timeout_sec: SimpleNamespace(responses=responses)
                )
                result = asyncio.run(analyzer.analyze_images(
                    [first, second],
                    "请分析这组图文",
                    api_key="dummy-key",
                    endpoint="https://ark.cn-beijing.volces.com/api/v3",
                    model="doubao-seed-2-0-lite-260428",
                ))
            finally:
                analyzer._build_response_client = old_build_response_client

        self.assertEqual(result.file_id, "inline-images")
        self.assertEqual(result.image_count, 2)
        self.assertEqual(result.usage["total_tokens"], 7)
        assert responses.kwargs is not None
        self.assertFalse(responses.kwargs["stream"])
        content = responses.kwargs["input"][0]["content"]
        self.assertEqual([item["type"] for item in content], [
            "input_image",
            "input_image",
            "input_text",
        ])
        self.assertTrue(content[0]["image_url"].startswith("data:image/jpeg;base64,"))
        self.assertTrue(content[1]["image_url"].startswith("data:image/webp;base64,"))
        self.assertEqual(content[-1], {"type": "input_text", "text": "请分析这组图文"})
        self.assertNotIn("input_video", str(content))

    def test_run_task_image_post_uses_image_pipeline_not_video_download(self) -> None:
        import ingest

        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            cfg = self._config(tmp)
            cache_dir = tmp / "cache"
            cache_dir.mkdir()
            image_paths = [tmp / "01.jpg", tmp / "02.jpg"]
            for path in image_paths:
                path.write_bytes(b"image")
            meta = self._image_meta()
            calls: list[str] = []

            async def fail_video_download(*args, **kwargs):
                raise AssertionError("image posts must not enter the video download path")

            async def fail_video_analysis(*args, **kwargs):
                raise AssertionError("image posts must not enter the video analysis path")

            async def fake_fetch_metadata(url, cookie_path):
                calls.append("fetch_metadata")
                return meta

            async def fake_download_images(meta_arg, out_dir, progress_cb=None):
                calls.append("download_images")
                self.assertEqual(meta_arg.media_type, "image_post")
                self.assertEqual(out_dir, cache_dir.parent / "images")
                return image_paths

            async def fake_analyze_images(paths_arg, prompt, **kwargs):
                calls.append("analyze_images")
                self.assertEqual(paths_arg, image_paths)
                self.assertTrue(prompt.strip())
                self.assertIn("api_key", kwargs)
                self.assertEqual(kwargs["model"], cfg.analyzer_model)
                self.assertEqual(kwargs["analysis_key"], "knowledge_ingest")
                return FakeImageResult()

            def fake_cost(model, usage):
                calls.append("estimate_cost")
                return {"total_tokens": usage["total_tokens"], "cost_rmb_estimate": 0}

            image_derived_decision = {
                "enabled": True,
                "source": "json",
                "counts": {"candidate": 1, "rejected": 0, "suppressed": 0},
                "items": [{
                    "id": "dt-image",
                    "name": "Image API",
                    "target_type": "official_doc",
                    "target_url": "https://example.com/docs/image-api",
                    "decision": "candidate",
                    "execution_status": "candidate",
                    "score": 81,
                    "reason": "图文里出现可复用接口，需要核验官方文档。",
                }],
            }

            def fake_derive_tasks(text, *, source_id, source_url, source_media, ingest_intent, vault_path,
                                  task_id=""):
                calls.append("derive")
                self.assertEqual(source_id, meta.aweme_id)
                self.assertEqual(source_url, meta.source_url)
                self.assertEqual(source_media, "douyin_image_post")
                self.assertEqual(ingest_intent, "knowledge_ingest")
                self.assertEqual(vault_path, cfg.vault_path)
                self.assertEqual(task_id, "image-task")
                return image_derived_decision

            def fake_write(
                config,
                meta_arg,
                media_paths,
                result,
                cost,
                ingest_intent="knowledge_ingest",
                derived_decision=None,
                task_id="",
            ):
                calls.append("write")
                self.assertEqual(meta_arg.media_type, "image_post")
                self.assertEqual(media_paths, image_paths)
                self.assertEqual(result.file_id, "inline-images")
                self.assertEqual(ingest_intent, "knowledge_ingest")
                self.assertEqual(derived_decision, image_derived_decision)
                self.assertEqual(task_id, "image-task")
                md_path = config.vault_path / "知识资产" / "知识入库" / "fake.md"
                md_path.parent.mkdir(parents=True, exist_ok=True)
                md_path.write_text("# fake", encoding="utf-8")
                return md_path, "committed"

            originals = {
                name: getattr(ingest, name, None)
                for name in [
                    "download", "analyze_video", "fetch_metadata", "download_images",
                    "analyze_images", "estimate_cost_rmb", "write_to_vault",
                    "write_image_post_to_vault", "derive_tasks_from_analysis",
                ]
            }
            try:
                ingest.download = fail_video_download
                ingest.analyze_video = fail_video_analysis
                ingest.fetch_metadata = fake_fetch_metadata
                ingest.download_images = fake_download_images
                ingest.analyze_images = fake_analyze_images
                ingest.estimate_cost_rmb = fake_cost
                ingest.write_to_vault = fake_write
                ingest.write_image_post_to_vault = fake_write
                ingest.derive_tasks_from_analysis = fake_derive_tasks

                status_writer = FakeStatusWriter()
                summary = asyncio.run(ingest.run_task(
                    task_id="image-task",
                    url=meta.source_url,
                    quality="quality",
                    ingest_intent="knowledge_ingest",
                    config=cfg,
                    sw=status_writer,
                    cache_dir=cache_dir,
                ))
            finally:
                for name, value in originals.items():
                    if value is None:
                        try:
                            delattr(ingest, name)
                        except AttributeError:
                            pass
                    else:
                        setattr(ingest, name, value)

        self.assertEqual(calls, [
            "fetch_metadata",
            "download_images",
            "analyze_images",
            "estimate_cost",
            "derive",
            "write",
        ])
        self.assertTrue(summary["vault_path"].endswith("知识资产/知识入库/fake.md"))
        self.assertEqual(summary["analysis"]["file_id"], "inline-images")
        expected_derived_tasks = [{
            "id": "dt-image",
            "name": "Image API",
            "targetType": "official_doc",
            "targetUrl": "https://example.com/docs/image-api",
            "decision": "candidate",
            "status": "candidate",
            "score": 81,
            "reason": "图文里出现可复用接口，需要核验官方文档。",
        }]
        self.assertEqual(len(summary["derived_tasks"]), 1)
        for key, value in expected_derived_tasks[0].items():
            self.assertEqual(summary["derived_tasks"][0][key], value)
        self.assertEqual(summary["derived_summary"], {"candidate": 1, "rejected": 0, "suppressed": 0})
        for key, value in expected_derived_tasks[0].items():
            self.assertEqual(summary["assets"][0]["derived_tasks"][0][key], value)
        derived_updates = [
            update for update in status_writer.updates
            if update.get("stage") == "derived_candidates_ready"
        ]
        self.assertEqual(len(derived_updates), 1)
        for key, value in expected_derived_tasks[0].items():
            self.assertEqual(derived_updates[0]["derived_tasks"][0][key], value)
        self.assertEqual(derived_updates[0]["derived_summary"], summary["derived_summary"])

    def test_image_post_vault_write_uses_image_path_and_index_tags(self) -> None:
        import json
        import ingest

        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            cfg = self._config(tmp)
            image_paths = [tmp / "01.jpg", tmp / "02.jpg"]
            for index, path in enumerate(image_paths, start=1):
                path.write_bytes(f"image-{index}".encode("ascii"))

            writer = getattr(ingest, "write_image_post_to_vault", None)
            if writer is None:
                writer = ingest.write_to_vault

            derived_decision = {
                "enabled": True,
                "source": "json",
                "counts": {"candidate": 1, "rejected": 0, "suppressed": 0},
                "items": [{
                    "id": "dt-image-write",
                    "name": "Image Write API",
                    "target_type": "official_doc",
                    "target_url": "https://example.com/docs/image-write-api",
                    "decision": "candidate",
                    "execution_status": "candidate",
                    "score": 83,
                    "reason": "图文写库路径应保留派生候选。",
                }],
            }
            md_path, git_status = writer(
                cfg,
                self._image_meta(),
                image_paths,
                FakeImageResult(),
                {"input_tokens": 3, "output_tokens": 4, "total_tokens": 7, "cost_rmb_estimate": 0.02},
                derived_decision=derived_decision,
                task_id="image-parent",
            )

            self.assertTrue(md_path.exists())
            self.assertIn("知识资产/知识入库", str(md_path))
            text = md_path.read_text(encoding="utf-8")
            self.assertIn("type: image_post_analysis", text)
            self.assertIn("asset_family: knowledge_asset", text)
            self.assertIn("source_media: douyin_image_post", text)
            self.assertIn("ingest_intent: knowledge_ingest", text)
            self.assertIn("tags: [douyin, knowledge-asset, case-study, image-analysis]", text)
            self.assertIn("image_count: 2", text)
            self.assertNotIn("video_path:", text)
            self.assertNotIn("fps_used:", text)
            self.assertIn("derived_candidate_record:", text)
            self.assertIn('derived_candidate_ids: ["dt-image-write"]', text)
            self.assertNotIn("target_type:", text.split("---", 2)[1])
            self.assertIn("[Image Write API](https://example.com/docs/image-write-api)", text)

            index_text = (cfg.vault_path / "index.md").read_text(encoding="utf-8")
            self.assertIn("[[", index_text)
            self.assertIn("`#douyin`", index_text)
            self.assertIn("`#knowledge-asset`", index_text)
            self.assertIn("`#image-analysis`", index_text)
            self.assertNotIn("`#video-analysis`", index_text)
            self.assertIn(git_status, {"committed", "no changes to commit"})
            records = list((cfg.vault_path / "系统记录" / "派生任务候选").glob("*.json"))
            self.assertEqual(len(records), 1)
            record = json.loads(records[0].read_text(encoding="utf-8"))
            item = record["items"][0]
            self.assertEqual(item["parent_task_id"], "image-parent")
            self.assertEqual(item["parent_asset_path"], str(md_path.relative_to(cfg.vault_path)))
            self.assertEqual(item["parent_source_url"], self._image_meta().source_url)


if __name__ == "__main__":
    unittest.main()
