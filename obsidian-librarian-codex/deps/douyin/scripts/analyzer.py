"""
analyzer.py — 火山方舟视频拆解

职责：
  1. ffprobe 测视频时长
  2. 根据 quality 档位算 fps（动态）
  3. 普通 Ark Files API 上传（带 preprocess_configs.video.model + fps）
  4. 普通 Ark 轮询 file.status 直到 active
  5. Responses API 得到结构化拆解结果

核心坑（8 个，必须全部规避）：
  ① 本地视频走普通 Ark Files API；Agent Plan 只保留历史验证记录，
     不作为运行通道
  ② Files API 上传必须传 preprocess_configs.video.model（否则回落 640 帧上限）
  ③ file_id 模式 fps 必须在上传时设，分析时再设无效
  ④ fps=5 是抽帧不是逐帧；fps × duration 先按 1250 安全目标控制，
     1280 是方舟硬上限
  ⑤ Files API 默认托管空间支持 ≤512MB；超过 512MB P0 直接失败
  ⑥ file_id 模式必须等 file.status == "active" 才能分析
  ⑦ file_id 模式同一视频换 quality 必须重新上传
  ⑧ 超 10 分钟固定切片，每片 240s，重叠 10s，再汇总

公共契约：
    analyze_video(video_path, prompt, *, config, quality="quality",
                  on_progress=None) -> AnalyzeResult
"""
from __future__ import annotations

import asyncio
import json
import math
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional


# ─────────────────────────────────────────────────────────────────
# Exceptions
# ─────────────────────────────────────────────────────────────────


class AnalyzerError(Exception):
    """analyzer 阶段错误的基类。"""


class FFprobeError(AnalyzerError):
    """ffprobe 不可用或读不出时长。"""


class FileTooLargeError(AnalyzerError):
    """P0：文件超项目侧安全上限拒绝（未来接 TOS 才支持到 2GB）。"""


class FileNotActiveError(AnalyzerError):
    """轮询超时 file 仍未 active。"""


class APIError(AnalyzerError):
    """火山 API 调用失败。"""


class ResponseTimeoutError(AnalyzerError):
    """Responses API 分析超时。"""


# ─────────────────────────────────────────────────────────────────
# 数据结构
# ─────────────────────────────────────────────────────────────────


@dataclass
class AnalyzeResult:
    """analyzer.analyze_video 的返回。"""
    text: str                       # 流式拼接后的完整文本
    file_id: str
    fps_used: float
    quality: str                    # 'balanced' | 'quality'
    model: str
    duration_sec: float
    target_frames: int
    actual_frames_estimate: int
    usage: dict = field(default_factory=dict)   # token usage（如果 API 返回）
    truncated: bool = False         # 抽帧数超 1280 硬上限时为 True
    response_id: Optional[str] = None
    chunked: bool = False
    chunk_count: int = 1
    chunks: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class ImageAnalyzeResult:
    """Ark image-post analysis result."""
    text: str
    file_id: str
    quality: str
    model: str
    image_count: int
    usage: dict = field(default_factory=dict)
    truncated: bool = False
    response_id: Optional[str] = None


@dataclass
class ResponseCallResult:
    """Responses API call result.

    __iter__ keeps old tests/callers compatible with:
        text, usage = await _stream_responses(...)
    """
    text: str
    usage: dict = field(default_factory=dict)
    response_id: Optional[str] = None

    def __iter__(self):
        yield self.text
        yield self.usage


# ─────────────────────────────────────────────────────────────────
# ffprobe 时长检测
# ─────────────────────────────────────────────────────────────────

def _ffprobe_command() -> str:
    """Return an ffprobe executable path independent of service PATH."""
    found = shutil.which("ffprobe")
    if found:
        return found
    for raw in (
        "/opt/homebrew/bin/ffprobe",
        "/usr/local/bin/ffprobe",
        "/usr/bin/ffprobe",
    ):
        path = Path(raw)
        if path.exists() and path.is_file():
            return str(path)
    return "ffprobe"


def _ffmpeg_command() -> str:
    """Return an ffmpeg executable path independent of service PATH."""
    found = shutil.which("ffmpeg")
    if found:
        return found
    for raw in (
        "/opt/homebrew/bin/ffmpeg",
        "/usr/local/bin/ffmpeg",
        "/usr/bin/ffmpeg",
    ):
        path = Path(raw)
        if path.exists() and path.is_file():
            return str(path)
    return "ffmpeg"


def get_duration_sec(video_path: Path) -> float:
    """用 ffprobe 取视频时长（秒）。"""
    video_path = Path(video_path)
    if not video_path.exists():
        raise FFprobeError(f"视频文件不存在: {video_path}")
    try:
        out = subprocess.run(
            [
                _ffprobe_command(), "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(video_path),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except FileNotFoundError as e:
        raise FFprobeError(
            "ffprobe 未找到。请安装 ffmpeg：\n"
            "  macOS:  brew install ffmpeg\n"
            "  Linux:  sudo apt install ffmpeg"
        ) from e
    except subprocess.CalledProcessError as e:
        raise FFprobeError(f"ffprobe 失败: {e.stderr.strip()}") from e
    except subprocess.TimeoutExpired as e:
        raise FFprobeError("ffprobe 30s 超时（视频文件可能损坏）") from e

    dur_str = out.stdout.strip()
    try:
        dur = float(dur_str)
    except ValueError:
        raise FFprobeError(f"ffprobe 返回非数字时长: {dur_str!r}")
    if dur <= 0:
        raise FFprobeError(f"ffprobe 返回非正时长: {dur}")
    return dur


# ─────────────────────────────────────────────────────────────────
# 动态 fps 计算（8 个坑里的 ③④）
# ─────────────────────────────────────────────────────────────────


# 火山 Seed 2.0 系列单视频抽帧硬上限
_FRAMES_HARD_CAP = 1280
# 项目侧安全目标：比硬上限留 30 帧冗余，避免擦边导致服务端再抽样。
_FRAMES_SAFE_TARGET = 1250
# fps 取值范围（火山官方文档）
_FPS_MIN = 0.2
_FPS_MAX = 5.0


def calc_fps(
    duration_sec: float,
    quality: str,
    *,
    fps_min: float = _FPS_MIN,
    fps_max: float = _FPS_MAX,
    balanced_target_frames: int = 240,
    quality_target_frames: int = 1250,
) -> tuple[float, int, bool]:
    """根据质量档位和视频时长算 fps。

    Returns:
        (fps, target_frames, will_truncate)
        - fps: 实际用于上传的 fps（向下保留两位）
        - target_frames: 目标抽帧数（按 quality 档，quality 默认 1250）
        - will_truncate: True 表示 fps×duration 会超 1280 上限，火山会做均匀抽样
    """
    if quality == "quality":
        target = min(max(1, int(quality_target_frames)), _FRAMES_SAFE_TARGET)
    elif quality == "balanced":
        target = balanced_target_frames
    else:
        raise AnalyzerError(f"未知 quality: {quality!r}")

    if duration_sec <= 0:
        raise AnalyzerError(f"非法 duration_sec: {duration_sec}")

    # quality 档优先使用 5fps；超过 1250 安全目标才下调，1280 只作为硬上限兜底。
    if quality == "quality":
        raw = fps_max if duration_sec * fps_max <= target else target / duration_sec
    else:
        raw = target / duration_sec
    fps = max(fps_min, min(fps_max, raw))
    # 向下保留两位小数，避免擦边
    fps = math.floor(fps * 100) / 100.0
    if fps < fps_min:
        fps = fps_min

    actual_frames = int(fps * duration_sec)
    will_truncate = actual_frames > _FRAMES_HARD_CAP
    return fps, target, will_truncate


# ─────────────────────────────────────────────────────────────────
# 文件大小校验
# ─────────────────────────────────────────────────────────────────


# P0 普通 Ark 使用 Files API 二进制上传到方舟默认托管空间，官方硬上限 512MB。
# 项目侧留 12MB 冗余，避免编码/请求/元数据擦边；TOS Bucket 可到 2GB，
# 但需要额外授权，不属于 P0。
_FILE_SIZE_HARD_LIMIT = 500 * 1024 * 1024  # 500MB safe limit
_INLINE_IMAGE_TOTAL_SIZE_LIMIT = 45 * 1024 * 1024
_IMAGE_COUNT_LIMIT = 18
_CHUNK_THRESHOLD_SEC = 600.0
_CHUNK_LEN_SEC = 240.0
_CHUNK_OVERLAP_SEC = 10.0
_RESPONSE_MEMORY_TTL_SEC = 7 * 24 * 60 * 60


def _check_size(path: Path) -> int:
    size = path.stat().st_size
    if size > _FILE_SIZE_HARD_LIMIT:
        raise FileTooLargeError(
            f"视频文件 {size / 1024 / 1024:.1f}MB 超出 500MB 安全上限，"
            f"v0.1 不支持 TOS Bucket"
        )
    return size


def _runtime_root() -> Path:
    raw = os.environ.get("OBSIDIAN_LIBRARIAN_HOME")
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".obsidian-librarian"


def _response_memory_dir() -> Path:
    return _runtime_root() / "responses-memory"


def _memory_key(*, media_type: str, source_id: str, ingest_intent: str, model: str) -> str:
    raw = json.dumps({
        "media_type": media_type,
        "source_id": source_id,
        "ingest_intent": ingest_intent,
        "model": model,
    }, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _memory_path(key: str) -> Path:
    return _response_memory_dir() / f"{key}.json"


def load_response_memory(
    *,
    media_type: str,
    source_id: str,
    ingest_intent: str,
    model: str,
    ttl_sec: int = _RESPONSE_MEMORY_TTL_SEC,
) -> dict[str, Any] | None:
    key = _memory_key(
        media_type=media_type,
        source_id=source_id,
        ingest_intent=ingest_intent,
        model=model,
    )
    path = _memory_path(key)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except Exception:
        return None
    response_id = payload.get("response_id")
    updated_at = float(payload.get("updated_at") or 0)
    if not response_id or time.time() - updated_at > ttl_sec:
        return None
    return payload


def save_response_memory(
    *,
    media_type: str,
    source_id: str,
    ingest_intent: str,
    model: str,
    response_id: Optional[str],
    file_id: str = "",
    chunked: bool = False,
) -> None:
    if not response_id:
        return
    key = _memory_key(
        media_type=media_type,
        source_id=source_id,
        ingest_intent=ingest_intent,
        model=model,
    )
    directory = _response_memory_dir()
    directory.mkdir(parents=True, exist_ok=True)
    payload = {
        "key": key,
        "media_type": media_type,
        "source_id": source_id,
        "ingest_intent": ingest_intent,
        "model": model,
        "response_id": response_id,
        "file_id": file_id,
        "chunked": bool(chunked),
        "updated_at": time.time(),
    }
    target = _memory_path(key)
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, target)


def _combine_usage(usages: list[dict[str, Any]]) -> dict[str, Any]:
    totals: dict[str, Any] = {}
    for usage in usages:
        if not isinstance(usage, dict):
            continue
        for key, value in usage.items():
            if isinstance(value, (int, float)):
                totals[key] = totals.get(key, 0) + value
    return totals


def _as_response_call_result(value: Any) -> ResponseCallResult:
    if isinstance(value, ResponseCallResult):
        return value
    if isinstance(value, tuple):
        text = value[0] if len(value) > 0 else ""
        usage = value[1] if len(value) > 1 and isinstance(value[1], dict) else {}
        response_id = value[2] if len(value) > 2 and isinstance(value[2], str) else None
        return ResponseCallResult(text=str(text or ""), usage=usage, response_id=response_id)
    return ResponseCallResult(text=str(value or ""))


def _chunk_plan(duration_sec: float) -> list[dict[str, float | int]]:
    if duration_sec <= _CHUNK_THRESHOLD_SEC:
        return []
    plan: list[dict[str, float | int]] = []
    start = 0.0
    stride = _CHUNK_LEN_SEC - _CHUNK_OVERLAP_SEC
    index = 1
    while start < duration_sec:
        end = min(duration_sec, start + _CHUNK_LEN_SEC)
        overlap = 0.0 if index == 1 else _CHUNK_OVERLAP_SEC
        plan.append({
            "part_index": index,
            "start_sec": round(start, 3),
            "end_sec": round(end, 3),
            "overlap_sec": overlap,
        })
        if end >= duration_sec:
            break
        start += stride
        index += 1
    return plan


def should_chunk_video(duration_sec: float) -> bool:
    return duration_sec > _CHUNK_THRESHOLD_SEC


def _split_video_for_chunks(video_path: Path, plan: list[dict[str, float | int]], out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for item in plan:
        index = int(item["part_index"])
        start = float(item["start_sec"])
        duration = float(item["end_sec"]) - start
        target = out_dir / f"part-{index:03d}.mp4"
        cmd = [
            _ffmpeg_command(),
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            f"{start:.3f}",
            "-i",
            str(video_path),
            "-t",
            f"{duration:.3f}",
            "-map",
            "0",
            "-c",
            "copy",
            "-avoid_negative_ts",
            "make_zero",
            "-y",
            str(target),
        ]
        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=180)
        except FileNotFoundError as e:
            raise FFprobeError(
                "ffmpeg 未找到。请安装 ffmpeg：\n"
                "  macOS:  brew install ffmpeg\n"
                "  Linux:  sudo apt install ffmpeg"
            ) from e
        except subprocess.CalledProcessError as e:
            raise AnalyzerError(f"ffmpeg 切片失败: {e.stderr.strip()}") from e
        except subprocess.TimeoutExpired as e:
            raise AnalyzerError("ffmpeg 切片 180s 超时") from e
        paths.append(target)
    return paths


# ─────────────────────────────────────────────────────────────────
# 主流程
# ─────────────────────────────────────────────────────────────────


ProgressCb = Optional[Callable[[str, dict], Awaitable[None]]]


async def _call_progress(cb: ProgressCb, stage: str, info: dict) -> None:
    if cb is None:
        return
    try:
        ret = cb(stage, info)
        if asyncio.iscoroutine(ret):
            await ret
    except Exception:
        # 进度回调不能让主流程崩溃
        pass


def _build_client(api_key: str, endpoint: str) -> Any:
    from openai import OpenAI  # type: ignore

    return OpenAI(api_key=api_key, base_url=endpoint)


def _build_response_client(api_key: str, endpoint: str, timeout_sec: int | None) -> Any:
    if timeout_sec and timeout_sec > 0:
        try:
            from openai import OpenAI  # type: ignore
            return OpenAI(api_key=api_key, base_url=endpoint, timeout=timeout_sec)
        except (ImportError, TypeError):
            pass
    return _build_client(api_key, endpoint)


def _default_files_endpoint(endpoint: str) -> str:
    """Files API is hosted under the normal Ark v3 endpoint."""
    normalized = str(endpoint or "").rstrip("/")
    if normalized.endswith("/api/plan/v3"):
        return normalized[: -len("/api/plan/v3")] + "/api/v3"
    return normalized


def _is_agent_plan_endpoint(endpoint: str) -> bool:
    return str(endpoint or "").rstrip("/").endswith("/api/plan/v3")


def _image_data_url(image_path: Path) -> str:
    import base64

    mime = mimetypes.guess_type(str(image_path))[0] or "image/jpeg"
    encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


async def _upload_with_preprocess(
    client: Any, video_path: Path, *, fps: float, model: str,
) -> Any:
    """走 Files API 上传 + 设置 preprocess_configs.video.{fps, model}。

    阻塞 IO 跑在 thread。
    """
    def _do_upload() -> Any:
        with video_path.open("rb") as f:
            preprocess_configs = {
                "video": {
                    "fps": fps,
                    "model": model,  # keep model here for SDKs that support it
                }
            }
            try:
                # Official Ark SDK shape.
                return client.files.create(
                    file=f,
                    purpose="user_data",
                    preprocess_configs=preprocess_configs,
                )
            except TypeError:
                # OpenAI-compatible SDK fallback: pass Ark-only fields through
                # extra_body. This keeps older Hermes environments working.
                f.seek(0)
                return client.files.create(
                    file=f,
                    purpose="user_data",
                    extra_body={
                        "preprocess_configs": {
                            "video": {
                                "fps": fps,
                                "model": model,  # 8 个坑里的 ②，必传
                            }
                        }
                    },
                )
    return await asyncio.to_thread(_do_upload)


async def _wait_for_active(
    client: Any,
    file_id: str,
    *,
    timeout_sec: int,
    on_progress: ProgressCb,
) -> Any:
    """轮询 file.status 直到 active 或超时。"""
    elapsed = 0.0
    interval = 2.0
    while True:
        file_obj = await asyncio.to_thread(client.files.retrieve, file_id)
        status = str(getattr(file_obj, "status", "") or "").lower()
        await _call_progress(on_progress, "waiting_active", {
            "file_id": file_id,
            "status": status,
            "elapsed_sec": round(elapsed, 1),
        })
        if status == "active":
            return file_obj
        if status == "failed":
            error = getattr(file_obj, "error", None)
            raise APIError(f"Files API 处理失败: {error or file_obj!r}")
        if status and status != "processing":
            raise APIError(f"Files API 返回未知状态: {status!r}")
        if elapsed >= timeout_sec:
            raise FileNotActiveError(
                f"等待 file {file_id} active 超时 {timeout_sec}s（当前 status={status}）"
            )
        await asyncio.sleep(interval)
        elapsed += interval
        # 渐进式延长间隔，最多 5s
        interval = min(interval * 1.2, 5.0)


async def _stream_responses(
    client: Any,
    *,
    model: str,
    prompt: str,
    on_progress: ProgressCb,
    file_id: Optional[str] = None,
    previous_response_id: Optional[str] = None,
    timeout_sec: Optional[int] = None,
) -> ResponseCallResult:
    """Responses API + stream，返回正文、usage 和 response_id。"""
    if file_id:
        video_item = {"type": "input_video", "file_id": file_id}
    else:
        raise AnalyzerError("Responses API 缺少视频输入")

    input_payload = [{
        "role": "user",
        "content": [
            video_item,
            {"type": "input_text", "text": prompt},
        ],
    }]

    def _do_stream() -> tuple[str, dict]:
        chunks: list[str] = []
        usage: dict = {}
        response_id: Optional[str] = None
        final_response: Any = None

        # SDK 在某些版本里把 responses.create 当生成器（同步），
        # 在 streamed 模式下逐 event 返回
        create_kwargs = {
            "model": model,
            "input": input_payload,
            "stream": True,
            "store": True,
        }
        if previous_response_id:
            create_kwargs["previous_response_id"] = previous_response_id
        stream = client.responses.create(
            **create_kwargs,
        )
        for event in stream:
            # 兼容 Ark / OpenAI SDK 常见事件：
            # response.output_text.delta、response.output_text.done、
            # 以及部分 SDK 暴露的 delta/text 属性。
            delta = _extract_stream_text(event)
            if delta:
                chunks.append(delta)
            # 试图获取最终的 response（含 usage）
            resp = getattr(event, "response", None)
            if resp is None and isinstance(event, dict):
                resp = event.get("response")
            if resp is not None:
                final_response = resp
                response_id = _extract_response_id(resp) or response_id
                u = resp.get("usage") if isinstance(resp, dict) else getattr(resp, "usage", None)
                usage = _usage_to_dict(u)
            response_id = _extract_response_id(event) or response_id
        if not chunks and final_response is not None:
            final_text = _extract_response_text(final_response)
            if final_text:
                chunks.append(final_text)
        return "".join(chunks), usage, response_id

    # 用 thread + 周期性 progress 推
    # （SDK 的 stream 是同步迭代器，不能直接 await）
    try:
        task = asyncio.to_thread(_do_stream)
        if timeout_sec and timeout_sec > 0:
            text, usage, response_id = await asyncio.wait_for(task, timeout=timeout_sec)
        else:
            text, usage, response_id = await task
    except asyncio.TimeoutError as e:
        raise ResponseTimeoutError(
            f"Responses API 视频理解超过 {timeout_sec}s 未返回"
        ) from e
    except Exception as e:
        raise APIError(f"Responses API 调用失败: {e}") from e
    await _call_progress(on_progress, "analyzing_done", {
        "text_length": len(text),
        "usage": usage,
        "response_id": response_id,
    })
    return ResponseCallResult(text=text, usage=usage, response_id=response_id)


async def _call_image_responses(
    client: Any,
    *,
    model: str,
    prompt: str,
    image_urls: list[str],
    on_progress: ProgressCb,
    timeout_sec: Optional[int] = None,
) -> tuple[str, dict]:
    """Responses API for one Douyin image post: images first, prompt last."""
    if not image_urls:
        raise AnalyzerError("Responses API 缺少图片输入")

    content = [{"type": "input_image", "image_url": url} for url in image_urls]
    content.append({"type": "input_text", "text": prompt})
    input_payload = [{
        "role": "user",
        "content": content,
    }]

    def _do_call() -> tuple[str, dict]:
        response = client.responses.create(
            model=model,
            input=input_payload,
            stream=False,
            store=True,
        )
        text = _extract_response_text(response)
        return text, _usage_to_dict(getattr(response, "usage", None))

    try:
        task = asyncio.to_thread(_do_call)
        if timeout_sec and timeout_sec > 0:
            text, usage = await asyncio.wait_for(task, timeout=timeout_sec)
        else:
            text, usage = await task
    except asyncio.TimeoutError as e:
        raise ResponseTimeoutError(
            f"Responses API 图文理解超过 {timeout_sec}s 未返回"
        ) from e
    except Exception as e:
        raise APIError(f"Responses API 图片理解调用失败: {e}") from e
    await _call_progress(on_progress, "analyzing_done", {
        "text_length": len(text),
        "usage": usage,
    })
    return text, usage


async def _call_text_responses(
    client: Any,
    *,
    model: str,
    prompt: str,
    on_progress: ProgressCb,
    previous_response_id: Optional[str] = None,
    timeout_sec: Optional[int] = None,
) -> ResponseCallResult:
    """Responses API text-only call, used to synthesize chunk outputs."""
    input_payload = [{
        "role": "user",
        "content": [{"type": "input_text", "text": prompt}],
    }]

    def _do_call() -> tuple[str, dict, Optional[str]]:
        create_kwargs = {
            "model": model,
            "input": input_payload,
            "stream": False,
            "store": True,
        }
        if previous_response_id:
            create_kwargs["previous_response_id"] = previous_response_id
        response = client.responses.create(**create_kwargs)
        text = _extract_response_text(response)
        return text, _usage_to_dict(getattr(response, "usage", None)), _extract_response_id(response)

    try:
        task = asyncio.to_thread(_do_call)
        if timeout_sec and timeout_sec > 0:
            text, usage, response_id = await asyncio.wait_for(task, timeout=timeout_sec)
        else:
            text, usage, response_id = await task
    except asyncio.TimeoutError as e:
        raise ResponseTimeoutError(
            f"Responses API 分片汇总超过 {timeout_sec}s 未返回"
        ) from e
    except Exception as e:
        raise APIError(f"Responses API 分片汇总调用失败: {e}") from e
    await _call_progress(on_progress, "synthesizing_done", {
        "text_length": len(text),
        "usage": usage,
        "response_id": response_id,
    })
    return ResponseCallResult(text=text, usage=usage, response_id=response_id)


def _usage_to_dict(usage_obj: Any) -> dict:
    if usage_obj is None:
        return {}
    try:
        if hasattr(usage_obj, "model_dump"):
            return usage_obj.model_dump()
        if isinstance(usage_obj, dict):
            return dict(usage_obj)
        return dict(usage_obj)
    except Exception:
        return {"raw": str(usage_obj)}


def _extract_response_id(value: Any) -> Optional[str]:
    if value is None:
        return None
    if hasattr(value, "model_dump"):
        try:
            value = value.model_dump()
        except Exception:
            pass
    if isinstance(value, dict):
        rid = value.get("id") or value.get("response_id")
        return rid if isinstance(rid, str) and rid else None
    rid = getattr(value, "id", None) or getattr(value, "response_id", None)
    return rid if isinstance(rid, str) and rid else None


def _extract_stream_text(event: Any) -> str:
    event_type = str(getattr(event, "type", "") or "")
    delta = getattr(event, "delta", None)
    if (
        isinstance(delta, str)
        and delta
        and (not event_type or event_type.endswith("output_text.delta"))
    ):
        return delta
    text = getattr(event, "text", None)
    if (
        isinstance(text, str)
        and text
        and (not event_type or event_type.endswith("output_text.delta"))
    ):
        return text
    return ""


def _extract_response_text(response: Any) -> str:
    direct = getattr(response, "output_text", None)
    if isinstance(direct, str) and direct:
        return direct

    if hasattr(response, "model_dump"):
        try:
            response = response.model_dump()
        except Exception:
            pass

    pieces: list[str] = []

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            value_type = value.get("type")
            text = value.get("text")
            if value_type == "output_text" and isinstance(text, str):
                pieces.append(text)
                return
            for child in value.values():
                walk(child)
            return
        if isinstance(value, list):
            for child in value:
                walk(child)
            return
        if not isinstance(value, (str, int, float, bool, type(None))):
            if hasattr(value, "model_dump"):
                try:
                    walk(value.model_dump())
                    return
                except Exception:
                    pass
            if hasattr(value, "__dict__"):
                walk(vars(value))

    walk(response)
    return "".join(pieces)


async def analyze_video(
    video_path: Path,
    prompt: str,
    *,
    api_key: str,
    endpoint: str,
    model: str,
    file_api_key: Optional[str] = None,
    file_endpoint: Optional[str] = None,
    quality: str = "quality",
    quality_params: Optional[dict] = None,
    source_id: Optional[str] = None,
    file_active_timeout_sec: int = 120,
    response_timeout_sec: int = 900,
    on_progress: ProgressCb = None,
) -> AnalyzeResult:
    """端到端拆解。

    Args:
      video_path: 本地 mp4 路径
      prompt: 拆解指令文本（中文）
      api_key, endpoint: Responses API 使用的火山方舟配置
      file_api_key, file_endpoint: Files API 使用的配置；通常与 Responses
          使用同一个普通 Ark key/endpoint
      model: Files API 预处理用的模型 ID（必须传，否则回落 640 帧）
              同时也是 Responses API 推理用的模型
      quality: 'balanced' | 'quality'
      quality_params: {target_frames, fps_min, fps_max}，可选；
                      不传则使用默认（balanced=240, quality=1250）
      file_active_timeout_sec: 等 file active 的最长秒数
      response_timeout_sec: 等 Responses API 返回的最长秒数
      on_progress: async (stage:str, info:dict) -> None，可选进度回调
    """
    results = await analyze_video_many(
        video_path,
        {"default": prompt},
        api_key=api_key,
        endpoint=endpoint,
        model=model,
        file_api_key=file_api_key,
        file_endpoint=file_endpoint,
        quality=quality,
        quality_params=quality_params,
        source_id=source_id,
        file_active_timeout_sec=file_active_timeout_sec,
        response_timeout_sec=response_timeout_sec,
        on_progress=on_progress,
    )
    return results["default"]


async def analyze_video_many(
    video_path: Path,
    prompts: dict[str, str],
    *,
    api_key: str,
    endpoint: str,
    model: str,
    file_api_key: Optional[str] = None,
    file_endpoint: Optional[str] = None,
    quality: str = "quality",
    quality_params: Optional[dict] = None,
    source_id: Optional[str] = None,
    file_active_timeout_sec: int = 120,
    response_timeout_sec: int = 900,
    on_progress: ProgressCb = None,
) -> dict[str, AnalyzeResult]:
    """Analyze one video with multiple prompts while reusing one video input.

    Ordinary Ark uploads once and reuses the active file_id. Videos longer than
    10 minutes are split into 240s chunks with 10s overlap.
    """
    if not prompts:
        raise AnalyzerError("缺少视频分析 prompt")
    if _is_agent_plan_endpoint(endpoint):
        raise AnalyzerError("Agent Plan 不再作为运行通道；请使用普通豆包 Ark API endpoint")

    video_path = Path(video_path).expanduser().resolve()
    if not video_path.exists():
        raise AnalyzerError(f"视频文件不存在: {video_path}")
    memory_source_id = str(source_id or video_path.stem)

    # 1. 测时长
    duration = get_duration_sec(video_path)
    await _call_progress(on_progress, "probed_duration", {
        "duration_sec": duration,
        "file_size_mb": video_path.stat().st_size / 1024 / 1024,
    })

    # 2. 文件大小校验
    _check_size(video_path)

    # 3. 算 fps
    q_params = quality_params or {}
    fps, target_frames, will_truncate = calc_fps(
        duration,
        quality,
        fps_min=q_params.get("fps_min", _FPS_MIN),
        fps_max=q_params.get("fps_max", _FPS_MAX),
        balanced_target_frames=q_params.get("target_frames", 240) if quality == "balanced" else 240,
        quality_target_frames=q_params.get("target_frames", 1250) if quality == "quality" else 1250,
    )
    actual_frames_est = int(fps * duration)

    await _call_progress(on_progress, "fps_decided", {
        "fps": fps,
        "target_frames": target_frames,
        "actual_frames_estimate": actual_frames_est,
        "will_truncate": will_truncate,
        "quality": quality,
    })

    responses_client = _build_response_client(api_key, endpoint, response_timeout_sec)

    files_client = _build_client(
        file_api_key or api_key,
        file_endpoint or _default_files_endpoint(endpoint),
    )

    chunk_plan = _chunk_plan(duration)
    if chunk_plan:
        await _call_progress(on_progress, "chunking_plan", {
            "chunk_count": len(chunk_plan),
            "chunk_len_sec": _CHUNK_LEN_SEC,
            "overlap_sec": _CHUNK_OVERLAP_SEC,
            "duration_sec": duration,
        })
        with tempfile.TemporaryDirectory(prefix="obsidian-librarian-chunks-") as tmpdir:
            chunk_paths = await asyncio.to_thread(
                _split_video_for_chunks,
                video_path,
                chunk_plan,
                Path(tmpdir),
            )
            return await _analyze_video_chunks(
                chunk_paths,
                chunk_plan,
                prompts,
                files_client=files_client,
                responses_client=responses_client,
                model=model,
                quality=quality,
                full_duration=duration,
                source_id=memory_source_id,
                file_active_timeout_sec=file_active_timeout_sec,
                response_timeout_sec=response_timeout_sec,
                on_progress=on_progress,
            )

    await _call_progress(on_progress, "uploading", {"path": str(video_path)})
    try:
        file_obj = await _upload_with_preprocess(
            files_client, video_path, fps=fps, model=model
        )
    except Exception as e:
        raise APIError(f"Files API 上传失败: {e}") from e

    file_id = getattr(file_obj, "id", None) or getattr(file_obj, "file_id", None)
    if not file_id:
        raise APIError(f"Files API 返回缺 id: {file_obj!r}")

    await _call_progress(on_progress, "uploaded", {"file_id": file_id})

    # 5. 等 active
    await _wait_for_active(
        files_client, file_id,
        timeout_sec=file_active_timeout_sec,
        on_progress=on_progress,
    )

    # 6. 用同一个 file_id 按多个 prompt 顺序拆解
    results: dict[str, AnalyzeResult] = {}
    for intent, prompt in prompts.items():
        memory = load_response_memory(
            media_type="douyin_video",
            source_id=memory_source_id,
            ingest_intent=intent,
            model=model,
        )
        previous_response_id = memory.get("response_id") if memory else None
        await _call_progress(on_progress, "analyzing", {
            "file_id": file_id,
            "model": model,
            "intent": intent,
            "has_previous_response": bool(previous_response_id),
        })
        call = _as_response_call_result(await _stream_responses(
            responses_client, model=model, file_id=file_id, prompt=prompt,
            on_progress=on_progress,
            timeout_sec=response_timeout_sec,
            previous_response_id=previous_response_id,
        ))
        text, usage = call.text, call.usage
        if not text.strip():
            raise APIError(f"Responses API 未返回可写入的分析文本: {intent}")
        save_response_memory(
            media_type="douyin_video",
            source_id=memory_source_id,
            ingest_intent=intent,
            model=model,
            response_id=call.response_id,
            file_id=file_id,
            chunked=False,
        )

        results[intent] = AnalyzeResult(
            text=text,
            file_id=file_id,
            fps_used=fps,
            quality=quality,
            model=model,
            duration_sec=duration,
            target_frames=target_frames,
            actual_frames_estimate=actual_frames_est,
            usage=usage,
            truncated=will_truncate,
            response_id=call.response_id,
        )
    return results


async def _analyze_video_chunks(
    chunk_paths: list[Path],
    chunk_plan: list[dict[str, float | int]],
    prompts: dict[str, str],
    *,
    files_client: Any,
    responses_client: Any,
    model: str,
    quality: str,
    full_duration: float,
    source_id: str,
    file_active_timeout_sec: int,
    response_timeout_sec: int,
    on_progress: ProgressCb,
) -> dict[str, AnalyzeResult]:
    """Analyze fixed video chunks and return one aggregated result per intent."""
    per_intent_texts: dict[str, list[str]] = {intent: [] for intent in prompts}
    per_intent_usages: dict[str, list[dict[str, Any]]] = {intent: [] for intent in prompts}
    per_intent_response: dict[str, Optional[str]] = {
        intent: (
            (load_response_memory(
                media_type="douyin_video",
                source_id=source_id,
                ingest_intent=intent,
                model=model,
            ) or {}).get("response_id")
        )
        for intent in prompts
    }
    per_intent_chunks: dict[str, list[dict[str, Any]]] = {intent: [] for intent in prompts}
    first_file_id = ""
    total = len(chunk_paths)

    for chunk_path, plan_item in zip(chunk_paths, chunk_plan):
        part_index = int(plan_item["part_index"])
        chunk_duration = float(plan_item["end_sec"]) - float(plan_item["start_sec"])
        fps, target_frames, will_truncate = calc_fps(chunk_duration, quality)
        actual_frames_est = int(fps * chunk_duration)
        await _call_progress(on_progress, "chunk_uploading", {
            "part_index": part_index,
            "chunk_count": total,
            "start_sec": plan_item["start_sec"],
            "end_sec": plan_item["end_sec"],
            "fps": fps,
        })
        try:
            file_obj = await _upload_with_preprocess(files_client, chunk_path, fps=fps, model=model)
        except Exception as e:
            raise APIError(f"Files API 切片上传失败 part={part_index}: {e}") from e
        file_id = getattr(file_obj, "id", None) or getattr(file_obj, "file_id", None)
        if not file_id:
            raise APIError(f"Files API 切片返回缺 id part={part_index}: {file_obj!r}")
        if not first_file_id:
            first_file_id = file_id
        await _call_progress(on_progress, "chunk_uploaded", {
            "part_index": part_index,
            "chunk_count": total,
            "file_id": file_id,
        })
        await _wait_for_active(
            files_client,
            file_id,
            timeout_sec=file_active_timeout_sec,
            on_progress=on_progress,
        )

        for intent, prompt in prompts.items():
            previous_response_id = per_intent_response.get(intent)
            chunk_prompt = (
                f"{prompt}\n\n"
                f"当前是长视频第 {part_index}/{total} 段，"
                f"时间范围 {float(plan_item['start_sec']):.1f}s - "
                f"{float(plan_item['end_sec']):.1f}s，"
                f"与上一段约重叠 {float(plan_item['overlap_sec']):.1f}s。"
                "请只分析当前片段，并保留可用于最终合并的结构化要点。"
            )
            await _call_progress(on_progress, "analyzing_chunk", {
                "part_index": part_index,
                "chunk_count": total,
                "file_id": file_id,
                "model": model,
                "intent": intent,
                "has_previous_response": bool(previous_response_id),
            })
            call = _as_response_call_result(await _stream_responses(
                responses_client,
                model=model,
                file_id=file_id,
                prompt=chunk_prompt,
                on_progress=on_progress,
                timeout_sec=response_timeout_sec,
                previous_response_id=previous_response_id,
            ))
            if not call.text.strip():
                raise APIError(f"Responses API 未返回可写入的分片分析文本: {intent} part={part_index}")
            per_intent_response[intent] = call.response_id or previous_response_id
            per_intent_usages[intent].append(call.usage)
            per_intent_texts[intent].append(
                f"## 分片 {part_index}/{total} "
                f"({float(plan_item['start_sec']):.1f}s - {float(plan_item['end_sec']):.1f}s)\n\n"
                f"{call.text.strip()}"
            )
            per_intent_chunks[intent].append({
                "part_index": part_index,
                "start_sec": plan_item["start_sec"],
                "end_sec": plan_item["end_sec"],
                "overlap_sec": plan_item["overlap_sec"],
                "file_id": file_id,
                "fps": fps,
                "target_frames": target_frames,
                "actual_frames_estimate": actual_frames_est,
                "truncated": will_truncate,
                "usage": call.usage,
                "response_id": call.response_id,
            })
            await _call_progress(on_progress, "chunk_done", {
                "part_index": part_index,
                "chunk_count": total,
                "intent": intent,
                "text_length": len(call.text),
            })

    results: dict[str, AnalyzeResult] = {}
    for intent in prompts:
        body = "\n\n".join(per_intent_texts[intent]).strip()
        synth_prompt = (
            "下面是同一个长视频按时间切片得到的多段拆解结果。"
            "请合并成一份可直接写入 Obsidian 的最终资产正文：去重重叠片段，"
            "保留时间顺序、关键细节、结论和不确定点，不要提到内部 file_id 或 response_id。\n\n"
            f"原始分析指令：\n{prompts[intent]}\n\n"
            f"分片结果：\n{body}"
        )
        await _call_progress(on_progress, "synthesizing_chunks", {
            "chunk_count": total,
            "intent": intent,
        })
        synth = await _call_text_responses(
            responses_client,
            model=model,
            prompt=synth_prompt,
            on_progress=on_progress,
            previous_response_id=per_intent_response.get(intent),
            timeout_sec=response_timeout_sec,
        )
        final_response_id = synth.response_id or per_intent_response.get(intent)
        final_usage = _combine_usage(per_intent_usages[intent] + [synth.usage])
        save_response_memory(
            media_type="douyin_video",
            source_id=source_id,
            ingest_intent=intent,
            model=model,
            response_id=final_response_id,
            file_id=first_file_id,
            chunked=True,
        )
        text = synth.text.strip() or body
        results[intent] = AnalyzeResult(
            text=text,
            file_id=first_file_id or "chunked",
            fps_used=_FPS_MAX,
            quality=quality,
            model=model,
            duration_sec=full_duration,
            target_frames=_FRAMES_SAFE_TARGET,
            actual_frames_estimate=sum(
                int(item.get("actual_frames_estimate", 0))
                for item in per_intent_chunks[intent]
            ),
            usage=final_usage,
            truncated=False,
            response_id=final_response_id,
            chunked=True,
            chunk_count=total,
            chunks=per_intent_chunks[intent],
        )
    return results


async def analyze_images(
    image_paths: list[Path],
    prompt: str,
    *,
    api_key: str,
    endpoint: str,
    model: str,
    quality: str = "quality",
    response_timeout_sec: int = 900,
    on_progress: ProgressCb = None,
) -> ImageAnalyzeResult:
    """Analyze a Douyin image post with Ark Responses input_image."""
    results = await analyze_images_many(
        image_paths,
        {"default": prompt},
        api_key=api_key,
        endpoint=endpoint,
        model=model,
        quality=quality,
        response_timeout_sec=response_timeout_sec,
        on_progress=on_progress,
    )
    return results["default"]


async def analyze_images_many(
    image_paths: list[Path],
    prompts: dict[str, str],
    *,
    api_key: str,
    endpoint: str,
    model: str,
    quality: str = "quality",
    response_timeout_sec: int = 900,
    on_progress: ProgressCb = None,
) -> dict[str, ImageAnalyzeResult]:
    """Analyze one image post with multiple prompts while reusing encoded images."""
    if not prompts:
        raise AnalyzerError("缺少图文分析 prompt")
    paths = [Path(path).expanduser().resolve() for path in image_paths]
    missing = [str(path) for path in paths if not path.exists()]
    if missing:
        raise AnalyzerError(f"图片文件不存在: {missing[0]}")
    if not paths:
        raise AnalyzerError("图文作品没有图片可分析")

    truncated = len(paths) > _IMAGE_COUNT_LIMIT
    used_paths = paths[:_IMAGE_COUNT_LIMIT]
    total_size = sum(path.stat().st_size for path in used_paths)
    if total_size > _INLINE_IMAGE_TOTAL_SIZE_LIMIT:
        raise FileTooLargeError(
            f"图文图片合计 {total_size / 1024 / 1024:.1f}MB，超过 "
            f"{_INLINE_IMAGE_TOTAL_SIZE_LIMIT / 1024 / 1024:.0f}MB inline 上限"
        )

    await _call_progress(on_progress, "encoding_images", {
        "image_count": len(used_paths),
        "original_image_count": len(paths),
        "total_size_mb": round(total_size / 1024 / 1024, 2),
        "truncated": truncated,
    })
    image_urls = [await asyncio.to_thread(_image_data_url, path) for path in used_paths]

    client = _build_response_client(api_key, endpoint, response_timeout_sec)
    results: dict[str, ImageAnalyzeResult] = {}
    for intent, prompt in prompts.items():
        await _call_progress(on_progress, "analyzing", {
            "file_id": "inline-images",
            "model": model,
            "image_count": len(used_paths),
            "mode": "responses_input_image",
            "intent": intent,
        })
        text, usage = await _call_image_responses(
            client,
            model=model,
            prompt=prompt,
            image_urls=image_urls,
            on_progress=on_progress,
            timeout_sec=response_timeout_sec,
        )
        if not text.strip():
            raise APIError(f"Responses API 未返回可写入的图文分析文本: {intent}")

        results[intent] = ImageAnalyzeResult(
            text=text,
            file_id="inline-images",
            quality=quality,
            model=model,
            image_count=len(used_paths),
            usage=usage,
            truncated=truncated,
        )
    return results


# ─────────────────────────────────────────────────────────────────
# CLI for debug
# ─────────────────────────────────────────────────────────────────


def _cli_main() -> int:
    import argparse
    import os

    parser = argparse.ArgumentParser(description="火山视频拆解（debug 用）")
    parser.add_argument("video", help="本地 mp4 文件路径")
    parser.add_argument(
        "--intent",
        default="knowledge_ingest",
        choices=["knowledge_ingest", "viral_breakdown"],
        help="选择调试用提示词意图",
    )
    parser.add_argument(
        "--prompt-file",
        default=None,
    )
    parser.add_argument(
        "--quality", default="quality", choices=["balanced", "quality"],
    )
    parser.add_argument(
        "--model", default="doubao-seed-2-0-lite-260428",
    )
    parser.add_argument(
        "--endpoint", default="https://ark.cn-beijing.volces.com/api/v3",
    )
    args = parser.parse_args()

    api_key = os.getenv("ARK_API_KEY") or os.getenv("DOUBAO_API_KEY")
    if not api_key:
        # 尝试从配置读
        try:
            sys.path.insert(0, str(Path(__file__).parent))
            from config_loader import load_config  # type: ignore
            cfg = load_config()
            api_key = cfg.ark_api_key
        except Exception:
            print("✗ 没有 API key（env ARK_API_KEY 或 config.toml）", file=sys.stderr)
            return 1

    prompt_path = Path(args.prompt_file) if args.prompt_file else (
        Path(__file__).parent / "prompts" / f"video_{args.intent}.md"
    )
    if not prompt_path.exists():
        print(f"✗ prompt 文件不存在: {prompt_path}", file=sys.stderr)
        return 1
    prompt = prompt_path.read_text(encoding="utf-8")

    async def _progress(stage: str, info: dict) -> None:
        print(f"  [{stage}] {info}")

    async def _run():
        return await analyze_video(
            Path(args.video),
            prompt,
            api_key=api_key,
            endpoint=args.endpoint,
            model=args.model,
            quality=args.quality,
            on_progress=_progress,
        )

    try:
        result = asyncio.run(_run())
    except AnalyzerError as e:
        print(f"✗ {type(e).__name__}: {e}", file=sys.stderr)
        return 1

    print()
    print("=" * 60)
    print(f"file_id: {result.file_id}")
    print(f"model:   {result.model}")
    print(f"fps:     {result.fps_used} ({result.quality})")
    print(f"duration: {result.duration_sec:.1f}s, "
          f"~{result.actual_frames_estimate} frames "
          f"(target {result.target_frames})")
    if result.truncated:
        print("⚠️  超 1280 帧硬上限，火山做了均匀抽样")
    print(f"usage:   {result.usage}")
    print("=" * 60)
    print(result.text)
    return 0


if __name__ == "__main__":
    sys.exit(_cli_main())
