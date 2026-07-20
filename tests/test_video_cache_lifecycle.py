#!/usr/bin/env python3
"""视频不入库 + 任务私有缓存目录生命周期的回归测试。

全部使用 mock 与临时目录：不触网、不调用真实模型、不读写真实
Agent-wiki 仓库或 ~/.agent-wiki。
"""
from __future__ import annotations

import asyncio
import os
import signal
import sys
import tempfile
import threading
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "deps" / "douyin" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import ingest  # noqa: E402
from config_loader import Config  # noqa: E402


@dataclass
class FakeVideoMeta:
    aweme_id: str = "1234567890123456789"
    title: str = "A Douyin Test Video"
    author: str = "Tester"
    author_sec_uid: str = "sec"
    duration_sec: float = 61
    cover_url: str = ""
    source_url: str = "https://v.douyin.com/test/"
    media_type: str = "video"
    image_urls: list[str] = field(default_factory=list)


@dataclass
class FakeImageMeta:
    aweme_id: str = "7654771261239701883"
    title: str = "图文测试"
    author: str = "Tester"
    author_sec_uid: str = "sec"
    duration_sec: float = 0.0
    cover_url: str = ""
    source_url: str = "https://v.douyin.com/image/"
    media_type: str = "image_post"
    image_urls: list[str] = field(default_factory=lambda: ["https://img/1", "https://img/2"])


@dataclass
class FakeResult:
    text: str = (
        "## 简洁概括\n测试来源概括。\n\n"
        "## 完整内容整理\n测试来源完整内容。\n\n"
        "## AI 分析\n测试来源分析。"
    )
    file_id: str = "file-test"
    fps_used: float = 1.0
    quality: str = "quality"
    model: str = "doubao-seed-2-0-lite-260428"
    target_frames: int = 1250
    actual_frames_estimate: int = 61
    usage: dict[str, Any] = field(
        default_factory=lambda: {"input_tokens": 1, "output_tokens": 2, "total_tokens": 3}
    )
    truncated: bool = False
    image_count: int = 2


def _config(tmp: Path, vault: Path, runtime_name: str = "runtime") -> Config:
    runtime = tmp / runtime_name
    runtime.mkdir(parents=True, exist_ok=True)
    return Config(
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
    def __init__(self) -> None:
        self.updates: list[dict[str, Any]] = []

    def update(self, **fields):
        self.updates.append(fields)

    def progress(self, stage, info):
        pass


class TaskCacheNameTests(unittest.TestCase):
    def test_sanitizes_to_single_level_name(self) -> None:
        self.assertEqual(ingest._task_cache_dir_name("20260720-101530-ab12"), "20260720-101530-ab12")
        self.assertNotIn("/", ingest._task_cache_dir_name("../escape"))
        self.assertNotIn("..", ingest._task_cache_dir_name(".."))
        self.assertTrue(ingest._task_cache_dir_name("").startswith("task-"))
        self.assertTrue(ingest._task_cache_dir_name("..").startswith("task-"))

    def test_task_cache_dir_is_direct_child(self) -> None:
        root = Path("/tmp/agent-wiki-test-cache")
        candidate = ingest.task_cache_dir(root, "task-1")
        self.assertEqual(candidate.parent, root)
        self.assertEqual(candidate.name, "task-1")


class CleanupSafetyTests(unittest.TestCase):
    """cleanup_task_cache 只删本任务目录，保护其余一切。"""

    def test_protects_other_tasks_symlinks_and_plain_files(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "cache" / "videos"
            root.mkdir(parents=True)
            task_a = root / "task-a"
            task_b = root / "task-b"
            task_a.mkdir()
            (task_a / "a.mp4").write_bytes(b"a")
            task_b.mkdir()
            (task_b / "b.mp4").write_bytes(b"b")
            plain_file = root / "task-file"
            plain_file.write_bytes(b"not a dir")

            outside = Path(d) / "precious"
            outside.mkdir()
            (outside / "keep.txt").write_text("keep", encoding="utf-8")
            link = root / "task-link"
            link.symlink_to(outside, target_is_directory=True)

            # symlink 目标不跟随、不删除
            ingest.cleanup_task_cache(root, "task-link")
            self.assertTrue(link.is_symlink())
            self.assertEqual((outside / "keep.txt").read_text(encoding="utf-8"), "keep")

            # 普通文件不是本任务创建的目录，不动
            ingest.cleanup_task_cache(root, "task-file")
            self.assertTrue(plain_file.is_file())

            # 非常规 task_id 不删除任何内容
            ingest.cleanup_task_cache(root, "../task-b")
            self.assertTrue((task_b / "b.mp4").is_file())

            # 正常删除本任务目录，其他任务不受影响
            ingest.cleanup_task_cache(root, "task-a")
            self.assertFalse(task_a.exists())
            self.assertTrue((task_b / "b.mp4").is_file())
            self.assertTrue(root.is_dir())

            # 重复清理是 no-op
            ingest.cleanup_task_cache(root, "task-a")
            self.assertTrue(root.is_dir())

    def test_video_cleanup_never_touches_image_cache(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            cache = Path(d) / "cache"
            videos = cache / "videos"
            images = cache / "images" / "20260720-post-123456"
            videos.mkdir(parents=True)
            images.mkdir(parents=True)
            (images / "01.jpg").write_bytes(b"img")
            task_dir = videos / "task-x"
            task_dir.mkdir()
            (task_dir / "v.mp4").write_bytes(b"v")

            ingest.cleanup_task_cache(videos, "task-x")
            self.assertFalse(task_dir.exists())
            self.assertTrue((images / "01.jpg").is_file())


class MainLifecycleTests(unittest.TestCase):
    """main() 在成功/失败/异常/取消/并发下都清理本任务缓存目录。"""

    def _run_main(self, tmp: Path, fake_run_task, expect_raises=None):
        vault = tmp / "vault"
        vault.mkdir(exist_ok=True)
        cfg = _config(tmp, vault)
        captured: dict[str, Any] = {}

        async def runner(**kwargs):
            captured.update(kwargs)
            return await fake_run_task(**kwargs)

        with (
            mock.patch.object(ingest, "load_config", return_value=cfg),
            mock.patch.object(ingest, "run_task", runner),
        ):
            if expect_raises is not None:
                with self.assertRaises(expect_raises):
                    ingest.main(["--url", "https://v.douyin.com/test/"])
                return captured, None
            code = ingest.main(["--url", "https://v.douyin.com/test/"])
        return captured, code

    def test_success_cleans_only_own_task_cache(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            cache_root = tmp / "runtime" / "cache" / "videos"
            other = cache_root / "other-task"
            other.mkdir(parents=True)
            (other / "keep.mp4").write_bytes(b"keep")

            async def fake_run_task(**kwargs):
                cache_dir = kwargs["cache_dir"]
                self.assertEqual(cache_dir.parent, cache_root)
                self.assertEqual(kwargs["image_cache_dir"], tmp / "runtime" / "cache" / "images")
                (cache_dir / "video.mp4").write_bytes(b"fake-video")
                return {"vault_path": str(tmp / "vault" / "asset.md")}

            captured, code = self._run_main(tmp, fake_run_task)
            self.assertEqual(code, 0)
            task_cache = captured["cache_dir"]
            self.assertFalse(task_cache.exists())
            self.assertTrue((other / "keep.mp4").is_file())
            self.assertTrue(cache_root.is_dir())

    def test_ingest_error_still_cleans_task_cache(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)

            async def fake_run_task(**kwargs):
                (kwargs["cache_dir"] / "video.mp4.part").write_bytes(b"partial")
                raise ingest.IngestError("network_error", "mock download failure")

            captured, code = self._run_main(tmp, fake_run_task)
            self.assertEqual(code, 1)
            self.assertFalse(captured["cache_dir"].exists())

    def test_unexpected_exception_still_cleans_task_cache(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)

            async def fake_run_task(**kwargs):
                (kwargs["cache_dir"] / "video.mp4").write_bytes(b"fake-video")
                raise RuntimeError("mock analyzer crash")

            captured, code = self._run_main(tmp, fake_run_task)
            self.assertEqual(code, 1)
            self.assertFalse(captured["cache_dir"].exists())

    @unittest.skipUnless(hasattr(signal, "SIGTERM"), "需要 SIGTERM")
    def test_sigterm_cancel_still_cleans_task_cache(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)

            async def fake_run_task(**kwargs):
                (kwargs["cache_dir"] / "video.mp4").write_bytes(b"fake-video")
                os.kill(os.getpid(), signal.SIGTERM)
                await asyncio.sleep(5)
                raise AssertionError("SIGTERM 必须中断任务")

            captured, _ = self._run_main(tmp, fake_run_task, expect_raises=KeyboardInterrupt)
            self.assertFalse(captured["cache_dir"].exists())
            # main 退出后恢复原 SIGTERM handler，不影响调用方
            self.assertNotEqual(signal.getsignal(signal.SIGTERM), signal.SIG_IGN)

    def test_concurrent_tasks_clean_only_their_own_cache(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            cache_root = tmp / "runtime" / "cache" / "videos"
            barrier = threading.Barrier(2)
            seen: dict[str, Path] = {}
            lock = threading.Lock()

            async def fake_run_task(**kwargs):
                cache_dir = kwargs["cache_dir"]
                with lock:
                    seen[kwargs["task_id"]] = cache_dir
                (cache_dir / "video.mp4").write_bytes(b"fake-video")
                barrier.wait(timeout=30)  # 两个任务真正并发重叠
                await asyncio.sleep(0)
                return {"vault_path": str(tmp / "vault" / f"{kwargs['task_id']}.md")}

            results: dict[str, Any] = {}

            def worker(name: str) -> None:
                try:
                    captured, code = self._run_main(tmp, fake_run_task)
                    results[name] = (captured, code)
                except BaseException as exc:  # noqa: BLE001
                    results[name] = exc

            threads = [threading.Thread(target=worker, args=(f"w{i}",)) for i in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=60)

            for name, result in results.items():
                self.assertNotIsInstance(result, BaseException, f"{name}: {result!r}")
                self.assertEqual(result[1], 0, name)
            self.assertEqual(len(seen), 2)
            dirs = set(seen.values())
            self.assertEqual(len(dirs), 2, "并发任务必须使用各自独立的缓存目录")
            for task_id, cache_dir in seen.items():
                self.assertFalse(cache_dir.exists(), task_id)
            self.assertTrue(cache_root.is_dir())


class ImagePipelineCacheTests(unittest.TestCase):
    """图文链路：显式 image_cache_dir 生效，且不被视频缓存清理影响。"""

    def test_run_task_uses_explicit_image_cache_dir(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            vault = tmp / "vault"
            vault.mkdir()
            cfg = _config(tmp, vault)
            video_cache = tmp / "runtime" / "cache" / "videos" / "image-task"
            video_cache.mkdir(parents=True)
            image_cache = tmp / "runtime" / "cache" / "images"
            meta = FakeImageMeta()
            image_paths = [tmp / "01.jpg", tmp / "02.jpg"]
            for path in image_paths:
                path.write_bytes(b"image")
            calls: list[str] = []

            async def fake_fetch_metadata(url, cookie_path):
                calls.append("fetch_metadata")
                return meta

            async def fake_download_images(meta_arg, out_dir, progress_cb=None):
                calls.append("download_images")
                self.assertEqual(out_dir, image_cache)
                return image_paths

            async def fake_analyze_images(paths_arg, prompt, **kwargs):
                calls.append("analyze_images")
                return FakeResult()

            def fake_derive(text, **kwargs):
                calls.append("derive")
                return {"enabled": True, "source": "json", "counts": {}, "items": []}

            def fake_write(*args, **kwargs):
                calls.append("write")
                md_path = cfg.vault_path / "知识资产" / "知识入库" / "fake.md"
                md_path.parent.mkdir(parents=True, exist_ok=True)
                md_path.write_text("# fake", encoding="utf-8")
                return md_path, "not_managed"

            async def fail_video_download(*args, **kwargs):
                raise AssertionError("图文任务不得进入视频下载链路")

            with (
                mock.patch.object(ingest, "fetch_metadata", fake_fetch_metadata),
                mock.patch.object(ingest, "download_images", fake_download_images),
                mock.patch.object(ingest, "download_video", fail_video_download),
                mock.patch.object(ingest, "analyze_images", fake_analyze_images),
                mock.patch.object(ingest, "derive_tasks_from_analysis", fake_derive),
                mock.patch.object(ingest, "write_image_post_to_vault", fake_write),
            ):
                summary = asyncio.run(ingest.run_task(
                    task_id="image-task",
                    url=meta.source_url,
                    quality="quality",
                    ingest_intent="knowledge_ingest",
                    config=cfg,
                    sw=FakeStatusWriter(),
                    cache_dir=video_cache,
                    image_cache_dir=image_cache,
                ))

            self.assertEqual(
                calls,
                ["fetch_metadata", "download_images", "analyze_images", "derive", "write"],
            )
            self.assertEqual(summary["source_media"], "douyin_image_post")
            # 视频任务缓存清理不影响图文图片缓存
            ingest.cleanup_task_cache(video_cache.parent, "image-task")
            self.assertFalse(video_cache.exists())
            self.assertTrue(image_cache.is_dir() or not image_cache.exists())


class VideoVaultWriteTests(unittest.TestCase):
    """视频入库：不复制 mp4 进 vault、Markdown 无原始媒体 embed、无 Git。"""

    def test_video_write_keeps_metadata_without_raw_video_or_git(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d)
            vault = tmp / "vault"
            vault.mkdir()
            cfg = _config(tmp, vault)
            video = tmp / "cache" / "videos" / "task-1" / "video.mp4"
            video.parent.mkdir(parents=True)
            video.write_bytes(b"fake-video")

            md_path, git_status = ingest.write_to_vault(
                cfg,
                FakeVideoMeta(),
                video,
                FakeResult(),
                {"input_tokens": 1, "output_tokens": 2, "total_tokens": 3},
            )

            self.assertTrue(md_path.is_file())
            text = md_path.read_text(encoding="utf-8")
            # 元数据保留
            self.assertIn('source_url: "https://v.douyin.com/test/"', text)
            self.assertIn('source_id: "1234567890123456789"', text)
            self.assertIn('aweme_id: "1234567890123456789"', text)
            self.assertIn('author: "Tester"', text)
            self.assertIn("type: video_analysis", text)
            # 不再保留原始媒体区块和 mp4 embed
            self.assertNotIn("原始媒体", text)
            self.assertNotIn("![[", text)
            self.assertNotIn(".mp4", text)
            # 视频不复制进 vault
            self.assertFalse((vault / "raw").exists())
            self.assertEqual(list(vault.rglob("*.mp4")), [])
            # 源文件不被移动或删除（由任务缓存清理负责）
            self.assertTrue(video.is_file())
            # 无 Git：不 init、不 commit
            self.assertEqual(git_status, "not_managed")
            self.assertFalse((vault / ".git").exists())
            # index 仍然更新
            self.assertIn(md_path.stem, (vault / "index.md").read_text(encoding="utf-8"))

    def test_template_has_no_raw_video_embed(self) -> None:
        template = (ROOT / "templates" / "video_analysis.md").read_text(encoding="utf-8")
        self.assertNotIn("原始媒体", template)
        self.assertNotIn("raw/videos", template)
        self.assertNotIn(".mp4", template)
        self.assertIn("source_url", template)
        # 图文模板保持原始图片区块不变
        image_template = (ROOT / "templates" / "image_post_analysis.md").read_text(encoding="utf-8")
        self.assertIn("原始媒体", image_template)


class ServerFallbackCleanupTests(unittest.TestCase):
    """websocket_server 的 SIGKILL 兜底清理与 ingest 规则一致。"""

    def test_safe_remove_task_video_cache(self) -> None:
        sys.path.insert(0, str(ROOT))
        from server.websocket_server import _safe_remove_task_video_cache

        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "cache" / "videos"
            root.mkdir(parents=True)
            task = root / "task-1"
            task.mkdir()
            (task / "v.mp4").write_bytes(b"v")
            other = root / "task-2"
            other.mkdir()
            (other / "v.mp4").write_bytes(b"v")

            outside = Path(d) / "precious"
            outside.mkdir()
            (outside / "keep.txt").write_text("keep", encoding="utf-8")
            link = root / "task-link"
            link.symlink_to(outside, target_is_directory=True)

            _safe_remove_task_video_cache(root, "task-link")
            self.assertTrue(link.is_symlink())
            self.assertTrue((outside / "keep.txt").is_file())

            _safe_remove_task_video_cache(root, "../task-2")
            self.assertTrue((other / "v.mp4").is_file())

            _safe_remove_task_video_cache(root, "task-1")
            self.assertFalse(task.exists())
            self.assertTrue((other / "v.mp4").is_file())
            self.assertTrue(root.is_dir())

            # 不存在的目录是 no-op
            _safe_remove_task_video_cache(root, "task-1")
            self.assertTrue(root.is_dir())


def main() -> int:
    unittest.main(argv=[sys.argv[0]], verbosity=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
