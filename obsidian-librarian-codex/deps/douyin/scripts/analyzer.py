"""
analyzer.py — 火山方舟视频拆解

职责：
  1. ffprobe 测视频时长
  2. 根据 quality 档位算 fps（动态）
  3. Files API 上传（带 preprocess_configs.video.model + fps）
  4. 轮询 file.status 直到 active
  5. Responses API + stream，得到结构化拆解结果

核心坑（8 个，必须全部规避）：
  ① 不走 base64/url 直传，必须走 Files API
  ② Files API 上传必须传 preprocess_configs.video.model（否则回落 640 帧上限）
  ③ fps 必须在上传时设，分析时再设无效
  ④ fps=5 是抽帧不是逐帧；fps × duration 不能超 1280
  ⑤ Files API 默认托管空间支持 ≤512MB；超过 512MB P0 直接失败
  ⑥ 必须等 file.status == "active" 才能分析
  ⑦ 同一视频换 quality 必须重新上传
  ⑧ P0 不做长视频切片；先依赖动态 fps 和 Ark 均匀抽帧上限

公共契约：
    analyze_video(video_path, prompt, *, config, quality="quality",
                  on_progress=None) -> AnalyzeResult
"""
from __future__ import annotations

import asyncio
import json
import math
import re
import subprocess
import sys
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
    """P0：文件超 512MB 拒绝（未来接 TOS 才支持到 2GB）。"""


class FileNotActiveError(AnalyzerError):
    """轮询超时 file 仍未 active。"""


class APIError(AnalyzerError):
    """火山 API 调用失败。"""


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
    truncated: bool = False         # 抽帧数超 1280 上限时为 True


# ─────────────────────────────────────────────────────────────────
# ffprobe 时长检测
# ─────────────────────────────────────────────────────────────────


def get_duration_sec(video_path: Path) -> float:
    """用 ffprobe 取视频时长（秒）。"""
    video_path = Path(video_path)
    if not video_path.exists():
        raise FFprobeError(f"视频文件不存在: {video_path}")
    try:
        out = subprocess.run(
            [
                "ffprobe", "-v", "error",
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


# 火山 Seed 2.0 系列单视频抽帧上限
_FRAMES_HARD_CAP = 1280
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
        - target_frames: 目标抽帧数（按 quality 档）
        - will_truncate: True 表示 fps×duration 会超 1280 上限，火山会做均匀抽样
    """
    if quality == "quality":
        target = quality_target_frames
    elif quality == "balanced":
        target = balanced_target_frames
    else:
        raise AnalyzerError(f"未知 quality: {quality!r}")

    if duration_sec <= 0:
        raise AnalyzerError(f"非法 duration_sec: {duration_sec}")

    # fps = target / duration，clamp 到 [fps_min, fps_max]
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


# P0 使用 Files API 二进制上传到方舟默认托管空间，官方上限 512MB。
# TOS Bucket 可到 2GB，但需要额外授权，不属于 P0。
_FILE_SIZE_HARD_LIMIT = 512 * 1024 * 1024  # 512MB


def _check_size(path: Path) -> int:
    size = path.stat().st_size
    if size > _FILE_SIZE_HARD_LIMIT:
        raise FileTooLargeError(
            f"视频文件 {size / 1024 / 1024:.1f}MB 超出 512MB 上限，"
            f"v0.1 不支持 TOS Bucket"
        )
    return size


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
    file_id: str,
    prompt: str,
    on_progress: ProgressCb,
) -> tuple[str, dict]:
    """Responses API + stream，返回 (完整文本, usage)。"""
    def _do_stream() -> tuple[str, dict]:
        chunks: list[str] = []
        usage: dict = {}
        final_response: Any = None
        # SDK 在某些版本里把 responses.create 当生成器（同步），
        # 在 streamed 模式下逐 event 返回
        stream = client.responses.create(
            model=model,
            input=[{
                "role": "user",
                "content": [
                    {"type": "input_video", "file_id": file_id},
                    {"type": "input_text", "text": prompt},
                ],
            }],
            stream=True,
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
            if resp is not None:
                final_response = resp
                u = resp.get("usage") if isinstance(resp, dict) else getattr(resp, "usage", None)
                usage = _usage_to_dict(u)
        if not chunks and final_response is not None:
            final_text = _extract_response_text(final_response)
            if final_text:
                chunks.append(final_text)
        return "".join(chunks), usage

    # 用 thread + 周期性 progress 推
    # （SDK 的 stream 是同步迭代器，不能直接 await）
    text, usage = await asyncio.to_thread(_do_stream)
    await _call_progress(on_progress, "analyzing_done", {
        "text_length": len(text),
        "usage": usage,
    })
    return text, usage


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

    walk(response)
    return "".join(pieces)


async def analyze_video(
    video_path: Path,
    prompt: str,
    *,
    api_key: str,
    endpoint: str,
    model: str,
    quality: str = "quality",
    quality_params: Optional[dict] = None,
    file_active_timeout_sec: int = 120,
    on_progress: ProgressCb = None,
) -> AnalyzeResult:
    """端到端拆解。

    Args:
      video_path: 本地 mp4 路径
      prompt: 拆解指令文本（中文）
      api_key, endpoint: 火山方舟配置
      model: Files API 预处理用的模型 ID（必须传，否则回落 640 帧）
              同时也是 Responses API 推理用的模型
      quality: 'balanced' | 'quality'
      quality_params: {target_frames, fps_min, fps_max}，可选；
                      不传则使用默认（balanced=240, quality=1250）
      file_active_timeout_sec: 等 file active 的最长秒数
      on_progress: async (stage:str, info:dict) -> None，可选进度回调
    """
    video_path = Path(video_path).expanduser().resolve()
    if not video_path.exists():
        raise AnalyzerError(f"视频文件不存在: {video_path}")

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

    # 4. Files API 上传
    client = _build_client(api_key, endpoint)
    await _call_progress(on_progress, "uploading", {"path": str(video_path)})
    try:
        file_obj = await _upload_with_preprocess(
            client, video_path, fps=fps, model=model
        )
    except Exception as e:
        raise APIError(f"Files API 上传失败: {e}") from e

    file_id = getattr(file_obj, "id", None) or getattr(file_obj, "file_id", None)
    if not file_id:
        raise APIError(f"Files API 返回缺 id: {file_obj!r}")

    await _call_progress(on_progress, "uploaded", {"file_id": file_id})

    # 5. 等 active
    await _wait_for_active(
        client, file_id,
        timeout_sec=file_active_timeout_sec,
        on_progress=on_progress,
    )

    # 6. 流式拆解
    await _call_progress(on_progress, "analyzing", {"file_id": file_id, "model": model})
    text, usage = await _stream_responses(
        client, model=model, file_id=file_id, prompt=prompt,
        on_progress=on_progress,
    )

    return AnalyzeResult(
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
    )


# ─────────────────────────────────────────────────────────────────
# CLI for debug
# ─────────────────────────────────────────────────────────────────


def _cli_main() -> int:
    import argparse
    import os

    parser = argparse.ArgumentParser(description="火山视频拆解（debug 用）")
    parser.add_argument("video", help="本地 mp4 文件路径")
    parser.add_argument(
        "--prompt-file",
        default=str(Path(__file__).parent / "prompts" / "video_analysis.md"),
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

    prompt_path = Path(args.prompt_file)
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
        print("⚠️  超 1280 帧上限，火山做了均匀抽样")
    print(f"usage:   {result.usage}")
    print("=" * 60)
    print(result.text)
    return 0


if __name__ == "__main__":
    sys.exit(_cli_main())
