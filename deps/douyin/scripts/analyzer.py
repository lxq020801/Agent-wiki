"""
analyzer.py — 火山方舟视频拆解

职责：
  1. ffprobe 测视频时长
  2. 本地 1fps 变化预扫描后，在 2-5fps 内自动决策或使用固定档
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
  ⑧ 超 10 分钟先做 2fps 全片/分片概览规划（策略粒度 240s），
     精拆前把相邻同 fps 分段按帧预算合并装满（1250÷fps 秒，最长 600s）再上传

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
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from video_sampling import (
    FPS_MODE_AUTO,
    decide_sampling_fps,
    merge_chunk_sampling_strategy,
    normalize_fps_mode,
    prescan_video,
)


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
    audit_artifacts: dict[str, Any] = field(default_factory=dict)


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
# 知识分析上传只允许使用已确认的 2-5fps；本地变化预扫描另用 1fps。
_FPS_MIN = 2.0
_FPS_MAX = 5.0
_TRUSTED_ARK_HOSTS = {"ark.cn-beijing.volces.com"}
_RESPONSE_RETRY_MAX_ATTEMPTS = 3
_RESPONSE_RETRY_BASE_DELAY_SEC = 1.2


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
_LONG_OVERVIEW_FPS = 2.0
# 2fps 概览在 1250 帧安全目标前留 20 帧余量。
_ULTRA_LONG_THRESHOLD_SEC = 10 * 60 + 15
_LONG_CHUNK_FPS_MIN = 2.0
_LONG_CHUNK_FPS_MAX = 5.0
_CHUNK_ANALYSIS_CONCURRENCY = 2
_LONG_STRATEGY_CONFIDENCE_MIN = 0.65
_LONG_STRATEGY_LOW_FPS_CONFIDENCE_MIN = 0.78
_RESPONSE_MEMORY_TTL_SEC = 3 * 24 * 60 * 60
_STRATEGY_LOG_NAME = "video-strategy-events.jsonl"
_AUDIT_ROOT_NAME = "run-artifacts"
_STRATEGY_SEGMENT_REQUIRED_FIELDS = (
    "part_index",
    "start_sec",
    "end_sec",
    "rough_summary",
    "recommended_fps",
    "confidence",
    "lite_brief",
)


def _check_size(path: Path) -> int:
    size = path.stat().st_size
    if size > _FILE_SIZE_HARD_LIMIT:
        raise FileTooLargeError(
            f"视频文件 {size / 1024 / 1024:.1f}MB 超出 500MB 安全上限，"
            f"v0.1 不支持 TOS Bucket"
        )
    return size


def _runtime_root() -> Path:
    raw = os.environ.get("AGENT_WIKI_HOME")
    if raw:
        return Path(raw).expanduser()
    return Path.home() / ".agent-wiki"


def _safe_artifact_name(value: Any, *, default: str = "run") -> str:
    text = str(value or "").strip()
    if not text:
        text = default
    text = re.sub(r"[^A-Za-z0-9._-]+", "-", text).strip(".-")
    return text[:120] or default


def _audit_dir(audit_id: Optional[str], source_id: str) -> Optional[Path]:
    if not audit_id:
        return None
    name = _safe_artifact_name(audit_id, default=source_id or "run")
    path = _runtime_root() / _AUDIT_ROOT_NAME / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def _audit_rel(path: Path) -> str:
    try:
        return str(path.relative_to(_runtime_root()))
    except ValueError:
        return str(path)


def _write_audit_text(audit_dir: Optional[Path], rel_path: str, text: str) -> str:
    if audit_dir is None:
        return ""
    target = audit_dir / rel_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_redact_sensitive_text(str(text or "")), encoding="utf-8")
    return _audit_rel(target)


def _write_audit_json(audit_dir: Optional[Path], rel_path: str, payload: Any) -> str:
    if audit_dir is None:
        return ""
    target = audit_dir / rel_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(_redact_sensitive_payload(payload), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return _audit_rel(target)


def _add_artifact(artifacts: dict[str, Any], key: str, path: str) -> None:
    if path:
        artifacts[key] = path


def _safe_api_metadata(value: Any) -> dict[str, Any]:
    """Keep a small allowlist of non-secret provider response facts."""
    if value is None:
        return {}
    if isinstance(value, dict):
        payload = value
    elif hasattr(value, "model_dump"):
        try:
            payload = value.model_dump()
        except Exception:
            payload = {}
    elif hasattr(value, "__dict__"):
        payload = vars(value)
    else:
        payload = {}
    allowed = {
        "id",
        "file_id",
        "object",
        "status",
        "model",
        "created_at",
        "bytes",
        "purpose",
    }
    return _redact_sensitive_payload({
        str(key): child
        for key, child in payload.items()
        if str(key) in allowed and child is not None
    })


def _new_sampling_evidence(
    *,
    mode: str,
    duration_sec: float,
    prescan: dict[str, Any],
    decision: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "created_at": time.time(),
        "truth_boundaries": {
            "local_reproduction_evidence": {
                "description": "Locally decoded grayscale samples used only to measure visual change.",
                "facts": prescan,
            },
            "upload_request_facts": {
                "description": "FPS values requested in Ark Files API preprocessing; not proof of consumed frames.",
                "mode": mode,
                "decision": decision,
                "requests": [],
            },
            "vendor_returned_facts": {
                "description": "Only metadata and usage actually returned by the provider.",
                "actual_model_frames": None,
                "actual_model_frames_availability": "not_returned_by_provider",
                "files": [],
                "responses": [],
            },
        },
        "duration_sec": round(float(duration_sec), 3),
    }


def _persist_sampling_evidence(
    audit_dir: Optional[Path],
    audit_files: dict[str, Any],
    evidence: Optional[dict[str, Any]],
) -> None:
    if evidence is None:
        return
    path = _write_audit_json(audit_dir, "01-sampling/evidence.json", evidence)
    _add_artifact(audit_files, "sampling.evidence", path)


def _record_upload_evidence(
    evidence: Optional[dict[str, Any]],
    *,
    phase: str,
    fps: float,
    duration_sec: float,
    model: str,
    file_obj: Any = None,
    active_obj: Any = None,
    part_index: Optional[int] = None,
    record_request: bool = True,
) -> None:
    if evidence is None:
        return
    boundaries = evidence["truth_boundaries"]
    if record_request:
        request = {
            "phase": phase,
            "part_index": part_index,
            "requested_fps": float(fps),
            "segment_duration_sec": round(float(duration_sec), 3),
            "planned_frame_count": int(float(fps) * float(duration_sec)),
            "actual_model_frames": None,
            "actual_model_frames_availability": "not_returned_by_provider",
            "model": model,
        }
        boundaries["upload_request_facts"]["requests"].append(request)
    if file_obj is not None or active_obj is not None:
        files = boundaries["vendor_returned_facts"]["files"]
        fact = next((
            item for item in reversed(files)
            if item.get("phase") == phase and item.get("part_index") == part_index
        ), None)
        if fact is None:
            fact = {"phase": phase, "part_index": part_index}
            files.append(fact)
        if file_obj is not None:
            fact["upload_response"] = _safe_api_metadata(file_obj)
        if active_obj is not None:
            fact["active_response"] = _safe_api_metadata(active_obj)


def _record_response_evidence(
    evidence: Optional[dict[str, Any]],
    *,
    phase: str,
    model: str,
    usage: dict[str, Any],
    text_length: int,
    part_index: Optional[int] = None,
    intent: Optional[str] = None,
) -> None:
    if evidence is None:
        return
    evidence["truth_boundaries"]["vendor_returned_facts"]["responses"].append({
        "phase": phase,
        "part_index": part_index,
        "intent": intent,
        "model": model,
        "completed": True,
        "output_text_length": int(text_length),
        "usage": _redact_sensitive_payload(usage),
        "provider_identifier_persisted": False,
    })


def _chunk_output_rel_path(intent: str, part_index: int) -> str:
    return f"03-lite/{intent}/part-{part_index:03d}-output.md"


def _chunk_meta_rel_path(intent: str, part_index: int) -> str:
    return f"03-lite/{intent}/part-{part_index:03d}-meta.json"


def _cached_chunk_output(
    audit_dir: Optional[Path],
    *,
    intent: str,
    part_index: int,
    prompt_hash: str,
) -> tuple[str, str] | None:
    if audit_dir is None:
        return None
    output_path = audit_dir / _chunk_output_rel_path(intent, part_index)
    if not output_path.exists():
        return None
    meta_path = audit_dir / _chunk_meta_rel_path(intent, part_index)
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            meta = {}
        if meta.get("prompt_hash") and meta.get("prompt_hash") != prompt_hash:
            return None
    try:
        text = output_path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None
    if len(text) < 80 or not text.startswith("## 分片"):
        return None
    return text, _audit_rel(output_path)


def _response_memory_dir() -> Path:
    return _runtime_root() / "responses-memory"


def _memory_key(
    *,
    media_type: str,
    source_id: str,
    ingest_intent: str,
    model: str,
    prompt_hash: str = "",
    flow_version: str = "responses-v1",
    chunked: bool = False,
) -> str:
    raw = json.dumps({
        "media_type": media_type,
        "source_id": source_id,
        "ingest_intent": ingest_intent,
        "model": model,
        "prompt_hash": prompt_hash,
        "flow_version": flow_version,
        "chunked": bool(chunked),
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
    prompt_hash: str = "",
    flow_version: str = "responses-v1",
    chunked: bool = False,
    ttl_sec: int = _RESPONSE_MEMORY_TTL_SEC,
) -> dict[str, Any] | None:
    key = _memory_key(
        media_type=media_type,
        source_id=source_id,
        ingest_intent=ingest_intent,
        model=model,
        prompt_hash=prompt_hash,
        flow_version=flow_version,
        chunked=chunked,
    )
    path = _memory_path(key)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except Exception as e:
        _write_strategy_log("response_memory_load_failed", {
            "path": str(path),
            "error": str(e),
        })
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
    prompt_hash: str = "",
    flow_version: str = "responses-v1",
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
        prompt_hash=prompt_hash,
        flow_version=flow_version,
        chunked=chunked,
    )
    directory = _response_memory_dir()
    directory.mkdir(parents=True, exist_ok=True)
    payload = {
        "key": key,
        "media_type": media_type,
        "source_id": source_id,
        "ingest_intent": ingest_intent,
        "model": model,
        "prompt_hash": prompt_hash,
        "flow_version": flow_version,
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
    def merge(target: dict[str, Any], source: dict[str, Any]) -> None:
        for key, value in source.items():
            if isinstance(value, bool):
                continue
            if isinstance(value, (int, float)):
                target[key] = target.get(key, 0) + value
            elif isinstance(value, dict):
                child = target.setdefault(key, {})
                if isinstance(child, dict):
                    merge(child, value)

    totals: dict[str, Any] = {}
    for usage in usages:
        if not isinstance(usage, dict):
            continue
        merge(totals, usage)
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


def _is_retryable_response_error(exc: BaseException) -> bool:
    if isinstance(exc, ResponseTimeoutError):
        return True
    if not isinstance(exc, APIError):
        return False
    msg = str(exc).lower()
    non_retry_markers = (
        "invalidparameter",
        "badrequest",
        "error code: 400",
        "type is not video",
        "unsupported",
        "permission",
        "unauthorized",
        "401",
        "403",
    )
    if any(marker in msg for marker in non_retry_markers):
        return False
    retry_markers = (
        "incomplete chunked read",
        "peer closed connection",
        "remoteprotocolerror",
        "connection reset",
        "connection aborted",
        "server disconnected",
        "readerror",
        "timeout",
        "timed out",
        "temporarily",
        "rate limit",
        "429",
        "500",
        "502",
        "503",
        "504",
    )
    return any(marker in msg for marker in retry_markers)


async def _retry_response_call(
    call_factory: Callable[[], Awaitable[Any]],
    *,
    label: str,
    progress_stage: str,
    on_progress: ProgressCb,
    context: dict[str, Any],
    max_attempts: int = _RESPONSE_RETRY_MAX_ATTEMPTS,
) -> ResponseCallResult:
    attempts = max(1, int(max_attempts or 1))
    for attempt in range(1, attempts + 1):
        try:
            return _as_response_call_result(await call_factory())
        except (APIError, ResponseTimeoutError) as exc:
            if attempt >= attempts or not _is_retryable_response_error(exc):
                raise
            delay = min(
                _RESPONSE_RETRY_BASE_DELAY_SEC * (2 ** (attempt - 1)),
                8.0,
            )
            payload = {
                **context,
                "attempt": attempt,
                "next_attempt": attempt + 1,
                "max_attempts": attempts,
                "delay_sec": round(delay, 2),
                "error": str(exc)[:600],
            }
            _write_strategy_log(f"{label}_retrying", payload)
            await _call_progress(on_progress, progress_stage, payload)
            await asyncio.sleep(delay)
    raise APIError(f"{label} failed without captured exception")


def _chunk_plan(
    duration_sec: float,
    *,
    force_for_frame_budget: bool = False,
) -> list[dict[str, float | int]]:
    if duration_sec <= _CHUNK_THRESHOLD_SEC and not force_for_frame_budget:
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


_ANALYSIS_CHUNK_MAX_LEN_SEC = 600.0


def _repack_analysis_plan(
    chunk_plan: list[dict[str, Any]],
    strategy: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], bool]:
    """把相邻同 fps 的 240s 策略分段合并成"装满帧预算"的分析分段。

    每段目标长度 = min(安全帧数 / fps, _ANALYSIS_CHUNK_MAX_LEN_SEC)；
    分段边界保留 10 秒重叠。返回 (新分段计划, 新策略分段, 是否有变化)。
    """
    strategy_chunks = {
        int(item["part_index"]): item
        for item in (strategy.get("chunks") or [])
        if isinstance(item, dict) and item.get("part_index") is not None
    }
    if not strategy_chunks:
        return chunk_plan, list(strategy.get("chunks") or []), False

    groups: list[list[Any]] = []  # [fps, [plan_items], [strategy_items]]
    for plan_item in chunk_plan:
        part_index = int(plan_item["part_index"])
        strategy_item = strategy_chunks.get(part_index)
        if strategy_item is None:
            return chunk_plan, list(strategy.get("chunks") or []), False
        fps = max(_LONG_CHUNK_FPS_MIN, min(
            _LONG_CHUNK_FPS_MAX,
            float(strategy_item.get("recommended_fps") or _LONG_CHUNK_FPS_MAX),
        ))
        if groups and abs(groups[-1][0] - fps) < 1e-6:
            groups[-1][1].append(plan_item)
            groups[-1][2].append(strategy_item)
        else:
            groups.append([fps, [plan_item], [strategy_item]])

    new_plan: list[dict[str, Any]] = []
    new_chunks: list[dict[str, Any]] = []
    part = 1
    for fps, plan_items, strategy_items in groups:
        seg_start = float(plan_items[0]["start_sec"])
        seg_end = float(plan_items[-1]["end_sec"])
        max_len = min(_FRAMES_SAFE_TARGET / fps, _ANALYSIS_CHUNK_MAX_LEN_SEC)
        stride = max_len - _CHUNK_OVERLAP_SEC
        brief = " ".join(
            text for text in (
                str(item.get("lite_brief") or "").strip() for item in strategy_items
            ) if text
        )
        confidences = [
            float(item["confidence"]) for item in strategy_items
            if item.get("confidence") is not None
        ]
        group_strategy = {
            **dict(strategy_items[0]),
            "recommended_fps": fps,
            "lite_brief": brief,
            "confidence": min(confidences) if confidences else None,
        }
        start = seg_start
        while start < seg_end - 1e-6:
            end = min(seg_end, start + max_len)
            new_plan.append({
                "part_index": part,
                "start_sec": round(start, 3),
                "end_sec": round(end, 3),
                "overlap_sec": 0.0 if part == 1 else _CHUNK_OVERLAP_SEC,
            })
            new_chunks.append({**group_strategy, "part_index": part})
            if end >= seg_end - 1e-6:
                break
            start += stride
            part += 1

    changed = not (
        len(new_plan) == len(chunk_plan)
        and all(
            float(a["start_sec"]) == float(b["start_sec"])
            and float(a["end_sec"]) == float(b["end_sec"])
            for a, b in zip(new_plan, chunk_plan)
        )
    )
    if not changed:
        return chunk_plan, list(strategy.get("chunks") or []), False
    return new_plan, new_chunks, True


def _prompt_hash(prompt: str) -> str:
    return hashlib.sha256(str(prompt or "").encode("utf-8")).hexdigest()[:16]


def _long_overview_fps(duration_sec: float) -> float:
    return _LONG_OVERVIEW_FPS


def _ultra_long_threshold_sec() -> float:
    return float(_ULTRA_LONG_THRESHOLD_SEC)


def _is_ultra_long_video(duration_sec: float) -> bool:
    return duration_sec > _ultra_long_threshold_sec()


def _long_overview_exceeds_safe_target(duration_sec: float) -> bool:
    return _is_ultra_long_video(duration_sec)


def should_chunk_video(duration_sec: float) -> bool:
    return duration_sec > _CHUNK_THRESHOLD_SEC


def _prompt_dir() -> Path:
    return Path(__file__).resolve().parent / "prompts"


def _load_prompt_file(name: str) -> str:
    path = _prompt_dir() / name
    return path.read_text(encoding="utf-8")


def _build_long_overview_prompt(
    *,
    duration_sec: float,
    chunk_plan: list[dict[str, float | int]],
    intents: list[str],
) -> str:
    template = _load_prompt_file("video_long_overview_strategy.md")
    return (
        template
        .replace("{duration_sec}", f"{duration_sec:.1f}")
        .replace(
            "{chunk_plan_json}",
            json.dumps(chunk_plan, ensure_ascii=False, indent=2),
        )
        .replace("{ingest_intent}", ", ".join(intents))
    )


def _build_overview_chunk_prompt(
    *,
    duration_sec: float,
    chunk_count: int,
    plan_item: dict[str, float | int],
    intents: list[str],
) -> str:
    return (
        "你是长视频拆解链路的粗概览员。当前视频太长，不能一次性做全片概览，"
        "所以你只需要粗略看懂当前切片，为后续生成全片分段策略提供依据。\n\n"
        f"视频总时长：{duration_sec:.1f} 秒\n"
        f"当前切片：第 {int(plan_item['part_index'])}/{chunk_count} 段\n"
        f"时间范围：{float(plan_item['start_sec']):.1f}s - {float(plan_item['end_sec']):.1f}s\n"
        f"用户入库意图：{', '.join(intents)}\n\n"
        "请输出简洁文本，不要输出最终 JSON。必须包含：\n"
        "- 本段大概讲了什么。\n"
        "- 本段粗时间线。\n"
        "- 本段信息主要由什么承载：口播/字幕/OCR/画面变化/界面操作/图表/动作/结构关系。\n"
        "- 画面是否重复；低 fps 会不会漏视觉、OCR、操作或动作证据。\n"
        "- 后续精拆本段建议重点关注什么，写成给下一个模型的人话说明。\n"
        "- 不确定点：看不清、听不清、实体名不确定或需要后续验证的内容。\n"
    )


def _build_chunked_overview_strategy_prompt(
    *,
    duration_sec: float,
    chunk_plan: list[dict[str, float | int]],
    intents: list[str],
    overview_pieces: list[tuple[int, str]],
) -> str:
    ordered = "\n\n".join(
        f"## 粗概览分片 {part_index}\n{text.strip()[:3000]}"
        for part_index, text in sorted(overview_pieces, key=lambda item: item[0])
    )
    base = _build_long_overview_prompt(
        duration_sec=duration_sec,
        chunk_plan=chunk_plan,
        intents=intents,
    )
    return (
        "下面是同一个超长视频按固定切片用 2fps 得到的粗概览。"
        "请把这些粗概览合成为全片概览与分段精拆策略。\n"
        "你必须根据粗概览里的证据，为每一个固定切片给出 2、3、4 或 5fps 建议。\n"
        "只输出最终 JSON，不要输出 Markdown，不要输出 JSON 外的内容。\n\n"
        f"粗概览结果：\n{ordered}\n\n"
        f"最终 JSON 结构和判断标准如下：\n{base}"
    )


def _build_strategy_repair_prompt(
    *,
    validation_reason: str,
    chunk_plan: list[dict[str, float | int]],
    raw_text: str,
) -> str:
    return (
        "你刚才输出的长视频概览与分段策略 JSON 没有通过程序校验。\n"
        "请只修复格式和缺失字段，不要重新发挥，不要输出 Markdown，"
        "不要输出 JSON 外的任何文字。\n\n"
        f"校验失败原因：{validation_reason}\n\n"
        "必须覆盖这些固定切片：\n"
        f"{json.dumps(chunk_plan, ensure_ascii=False, indent=2)}\n\n"
        "每个 segment 必须包含：part_index, start_sec, end_sec, rough_summary, "
        "recommended_fps, confidence, lite_brief。\n"
        "recommended_fps 只能是 2、3、4、5；confidence 是 0-1。\n"
        "可以保留 information_carriers, evidence, risk_flags, why_not_lower_fps；"
        "lite_brief 必须是给下一个模型的人话精拆说明。\n\n"
        "原始输出如下：\n"
        f"{raw_text[:8000]}"
    )


def _extract_json_object(text: str) -> dict[str, Any]:
    """Extract the first JSON object from a model response."""
    raw = (text or "").strip()
    if not raw:
        raise AnalyzerError("长视频概览策略为空")
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", raw, re.IGNORECASE)
    if fence:
        raw = fence.group(1).strip()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start < 0 or end <= start:
            raise
        payload = json.loads(raw[start:end + 1])
    if not isinstance(payload, dict):
        raise AnalyzerError("长视频概览策略不是 JSON object")
    return payload


def _to_float(value: Any, default: float) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _score(value: Any) -> int:
    return int(max(0, min(5, round(_to_float(value, 0)))))


def _string_list(value: Any, *, limit: int = 8) -> list[str]:
    if isinstance(value, list):
        items = value
    elif isinstance(value, str) and value.strip():
        items = [value]
    else:
        return []
    out: list[str] = []
    for item in items:
        text = str(item).strip()
        if text:
            out.append(text[:300])
        if len(out) >= limit:
            break
    return out


def _score_dict(value: Any) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    return {str(key): _score(child) for key, child in value.items()}


def _has_visual_or_ocr_evidence(*values: Any) -> bool:
    text = " ".join(
        item
        for value in values
        for item in _string_list(value, limit=12)
    )
    if not text:
        return False
    return bool(re.search(
        r"(画面变化|场景变化|镜头切换|快速切换|字幕|OCR|文字|界面|操作|点击|"
        r"拖拽|菜单|按钮|代码|表格|图表|PPT|截图|屏幕|产品展示|运动|动作|看不清|快速)",
        text,
        re.IGNORECASE,
    ))


def _redact_sensitive_text(text: str) -> str:
    """Scrub secrets from local diagnostic logs."""
    cleaned = str(text)
    replacements = [
        (r"(?i)Bearer\s+[A-Za-z0-9._~+/=-]+", "Bearer [REDACTED]"),
        (
            r"(?i)[\"']?(api[_-]?key|ark[_-]?api[_-]?key|agent[_-]?plan[_-]?api[_-]?key|"
            r"arkApiKey|agentPlanApiKey|response[_-]?id|previous[_-]?response[_-]?id|"
            r"responseId|previousResponseId)[\"']?\s*[:=]\s*"
            r"(\"[^\"]*\"|'[^']*'|[^,\s}\]\n\r]+)",
            "sensitive=[REDACTED]",
        ),
        (
            r"(?i)[\"']?(authorization|cookie|set-cookie)[\"']?\s*[:=]\s*[^\n\r]+",
            "sensitive=[REDACTED]",
        ),
        (r"(?i)(https?://)[^/\s:@]+:[^/\s@]+@", r"\1[REDACTED]@"),
        (r"\bresp-[A-Za-z0-9._-]+\b", "resp-[REDACTED]"),
        (r"\bghp_[A-Za-z0-9_]{20,}\b", "ghp_[REDACTED]"),
        (r"\bgithub_pat_[A-Za-z0-9_]{20,}\b", "github_pat_[REDACTED]"),
        (r"(?i)(access_token|private_token|github_token)=([^&\s]+)", r"\1=[REDACTED]"),
        (r"(?i)([?&][^=&#]*(token|key|secret|signature|sig)[^=&#]*=)[^&#\s]+", r"\1[REDACTED]"),
    ]
    for pattern, repl in replacements:
        cleaned = re.sub(pattern, repl, cleaned)
    return cleaned


def _canonical_secret_key(key: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(key).lower())


def _is_sensitive_key(key: Any) -> bool:
    canonical = _canonical_secret_key(key)
    return canonical in {
        "authorization",
        "bearer",
        "cookie",
        "setcookie",
        "responseid",
        "previousresponseid",
        "githubtoken",
        "accesstoken",
        "privatetoken",
    } or canonical.endswith("apikey")


def _redact_sensitive_payload(value: Any) -> Any:
    if isinstance(value, str):
        return _redact_sensitive_text(value)
    if isinstance(value, dict):
        clean: dict[str, Any] = {}
        for key, child in value.items():
            if _is_sensitive_key(key):
                continue
            else:
                clean[key] = _redact_sensitive_payload(child)
        return clean
    if isinstance(value, list):
        return [_redact_sensitive_payload(item) for item in value]
    return value


def _missing_strategy_segment_fields(segment: dict[str, Any]) -> list[str]:
    return [
        field for field in _STRATEGY_SEGMENT_REQUIRED_FIELDS
        if field not in segment
    ]


def _strategy_fallback(
    chunk_plan: list[dict[str, float | int]],
    *,
    reason: str,
    overview: Optional[dict[str, Any]] = None,
    raw_text: str = "",
) -> dict[str, Any]:
    chunks = []
    for item in chunk_plan:
        chunks.append({
            **item,
            "recommended_fps": _LONG_CHUNK_FPS_MAX,
            "confidence": 0.0,
            "information_carriers": {},
            "scores": {
                "visual_change": 0,
                "ocr_subtitle_density": 0,
                "operation_density": 0,
                "motion_detail": 0,
                "concept_density": 0,
                "risk_if_low_fps": 5,
            },
            "rough_summary": "",
            "evidence": [],
            "focus": ["概览策略不可用，按最高精拆 fps 保守处理"],
            "lite_brief": "概览策略不可用。请按最高视觉采样保守精拆，并明确标记不确定点。",
            "risk_flags": [reason],
            "why_not_lower_fps": reason,
            "fallback_applied": True,
            "fallback_reason": reason,
            "fallback_type": "validation_fallback",
            "validation_fallback": True,
            "fps_adjusted": True,
            "fps_adjust_reason": reason,
        })
    return {
        "ok": False,
        "fallback_reason": reason,
        "overview": overview or {},
        "global_notes": "",
        "detected_structure": {},
        "raw_text": raw_text[:4000],
        "chunks": chunks,
    }


def _strategy_needs_json_repair(strategy: dict[str, Any]) -> bool:
    if not strategy.get("ok"):
        return True
    chunks = strategy.get("chunks")
    if not isinstance(chunks, list) or not chunks:
        return True
    for item in chunks:
        if not isinstance(item, dict):
            return True
        reason = str(item.get("fallback_reason") or "")
        if "概览策略缺少" in reason:
            return True
    return False


def _raw_text_diagnostic(text: Any, *, limit: int = 1200) -> dict[str, Any]:
    raw = str(text or "")
    return {
        "raw_text_len": len(raw),
        "raw_text_head": raw[:limit],
        "raw_text_tail": raw[-limit:] if len(raw) > limit else "",
    }


def _write_strategy_log(event: str, payload: dict[str, Any]) -> None:
    """Append non-sensitive strategy diagnostics for later tuning."""
    try:
        log_dir = _runtime_root() / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        record = {
            "event": event,
            "at": time.time(),
            **payload,
        }
        record = _redact_sensitive_payload(record)
        # Avoid large raw model output in local logs.
        if "raw_text" in record:
            raw_text = str(record.pop("raw_text"))
            record.update(_raw_text_diagnostic(raw_text))
        path = log_dir / _STRATEGY_LOG_NAME
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    except Exception:
        # Logging must never break ingest.
        pass


def _strategy_segments_from_payload(payload: dict[str, Any]) -> tuple[list[Any], dict[str, Any], str, dict[str, Any]]:
    strategy = payload.get("strategy") if isinstance(payload.get("strategy"), dict) else {}
    detected = {
        "has_strategy": isinstance(payload.get("strategy"), dict),
        "strategy_has_segments": isinstance(strategy.get("segments"), list),
        "top_level_has_segments": isinstance(payload.get("segments"), list),
        "top_level_has_chunks": isinstance(payload.get("chunks"), list),
        "top_level_has_fps_plan": isinstance(payload.get("fps_plan"), list),
    }
    if isinstance(strategy.get("segments"), list):
        return strategy["segments"], strategy, "strategy.segments", detected
    if isinstance(payload.get("segments"), list):
        return payload["segments"], strategy, "segments", detected
    if isinstance(payload.get("chunks"), list):
        return payload["chunks"], strategy, "chunks", detected
    if isinstance(payload.get("fps_plan"), list):
        return payload["fps_plan"], strategy, "fps_plan", detected
    return [], strategy, "", detected


def _segment_value(segment: dict[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if key in segment and segment.get(key) not in (None, ""):
            return segment.get(key)
    return default


def _strategy_rank(strategy: dict[str, Any]) -> tuple[int, int, int]:
    chunks = strategy.get("chunks") if isinstance(strategy.get("chunks"), list) else []
    valid = sum(1 for item in chunks if isinstance(item, dict) and not item.get("fallback_applied"))
    fallback = sum(1 for item in chunks if isinstance(item, dict) and item.get("fallback_applied"))
    ok = 1 if strategy.get("ok") else 0
    return (valid, ok, -fallback)


def _better_strategy(first: dict[str, Any], second: dict[str, Any]) -> dict[str, Any]:
    return first if _strategy_rank(first) >= _strategy_rank(second) else second


def _normalize_long_video_strategy(
    strategy_text: str,
    chunk_plan: list[dict[str, float | int]],
) -> dict[str, Any]:
    """Validate model strategy and return per-chunk fps with conservative fallbacks."""
    try:
        payload = _extract_json_object(strategy_text)
    except Exception as e:
        return _strategy_fallback(
            chunk_plan,
            reason=f"概览策略 JSON 无效: {e}",
            raw_text=strategy_text,
        )

    overview = payload.get("overview") if isinstance(payload.get("overview"), dict) else {}
    raw_segments, strategy, segments_path, detected_structure = _strategy_segments_from_payload(payload)
    by_part: dict[int, dict[str, Any]] = {}
    for segment in raw_segments:
        if not isinstance(segment, dict):
            continue
        part_index = int(_to_float(segment.get("part_index"), -1))
        if part_index > 0:
            by_part[part_index] = segment

    if not by_part:
        return _strategy_fallback(
            chunk_plan,
            reason="概览策略缺少 segments",
            overview=overview,
            raw_text=strategy_text,
        ) | {"detected_structure": detected_structure}

    normalized: list[dict[str, Any]] = []
    for item in chunk_plan:
        part_index = int(item["part_index"])
        segment = by_part.get(part_index)
        if not segment:
            normalized.append(_strategy_fallback(
                [item],
                reason=f"概览策略缺少第 {part_index} 段",
                overview=overview,
                raw_text=strategy_text,
            )["chunks"][0])
            continue

        missing_fields = _missing_strategy_segment_fields(segment)
        confidence = max(0.0, min(1.0, _to_float(segment.get("confidence"), 0.0)))
        raw_fps = _to_float(_segment_value(
            segment,
            "recommended_fps",
            "fps",
            "target_fps",
            "suggested_fps",
            default=_LONG_CHUNK_FPS_MAX,
        ), _LONG_CHUNK_FPS_MAX)
        fps = max(_LONG_CHUNK_FPS_MIN, min(_LONG_CHUNK_FPS_MAX, raw_fps))
        # Keep upload values simple and stable; Ark accepts floats, but these are
        # strategy levels rather than exact mathematical results.
        fps = float(math.ceil(fps))
        information_carriers = _score_dict(segment.get("information_carriers"))
        scores_raw = segment.get("scores") if isinstance(segment.get("scores"), dict) else {}
        scores = {
            "visual_change": _score(scores_raw.get("visual_change")),
            "ocr_subtitle_density": _score(scores_raw.get("ocr_subtitle_density")),
            "operation_density": _score(scores_raw.get("operation_density")),
            "motion_detail": _score(scores_raw.get("motion_detail")),
            "concept_density": _score(scores_raw.get("concept_density")),
            "risk_if_low_fps": _score(scores_raw.get("risk_if_low_fps")),
        }
        visual_need_score = max(
            scores["visual_change"],
            scores["ocr_subtitle_density"],
            scores["operation_density"],
            scores["motion_detail"],
            information_carriers.get("visual_scene", 0),
            information_carriers.get("subtitle_ocr", 0),
            information_carriers.get("operation_steps", 0),
            information_carriers.get("motion_detail", 0),
        )
        evidence = _string_list(_segment_value(segment, "evidence", "fps_evidence", "reason"))
        focus = _string_list(_segment_value(segment, "focus", "focus_points", "analysis_focus", "lite_focus"))
        risk_flags = _string_list(_segment_value(segment, "risk_flags", "risks", "risk"))
        lite_brief = str(_segment_value(
            segment,
            "lite_brief",
            "analysis_brief",
            "next_model_brief",
            "precision_brief",
            default="",
        ) or "").strip()[:1600]
        fallback_reasons: list[str] = []
        validation_reasons: list[str] = []
        fps_adjust_reasons: list[str] = []

        if missing_fields:
            validation_reasons.append(
                "概览策略缺少必填字段: "
                + ", ".join(missing_fields[:12])
            )
            fps = _LONG_CHUNK_FPS_MAX

        if confidence < _LONG_STRATEGY_CONFIDENCE_MIN:
            fps_adjust_reasons.append(
                f"策略置信度 {confidence:.2f} 低于 {_LONG_STRATEGY_CONFIDENCE_MIN:.2f}"
            )
            fps = _LONG_CHUNK_FPS_MAX
        elif confidence < _LONG_STRATEGY_LOW_FPS_CONFIDENCE_MIN and fps <= 3:
            fps_adjust_reasons.append(
                f"低 fps 建议置信度 {confidence:.2f} 不足，向上保守"
            )
            fps = 4.0

        has_visual_evidence = visual_need_score >= 4 or _has_visual_or_ocr_evidence(
            evidence, focus, risk_flags, lite_brief
        )
        if scores["risk_if_low_fps"] >= 5 and fps < _LONG_CHUNK_FPS_MAX and has_visual_evidence:
            fps_adjust_reasons.append("低 fps 漏视觉/OCR/操作细节风险评分为 5")
            fps = _LONG_CHUNK_FPS_MAX
        elif scores["risk_if_low_fps"] >= 4 and fps < 4 and has_visual_evidence:
            fps_adjust_reasons.append("低 fps 漏视觉/OCR/操作细节风险较高")
            fps = 4.0
        if (
            fps >= 4
            and not has_visual_evidence
            and not validation_reasons
            and confidence >= _LONG_STRATEGY_CONFIDENCE_MIN
        ):
            fps_adjust_reasons.append(
                "缺少明确视觉/OCR/操作证据，不因概念密度单独使用 4/5fps"
            )
            fps = 3.0

        chunk_duration = float(item["end_sec"]) - float(item["start_sec"])
        if int(chunk_duration * fps) > _FRAMES_SAFE_TARGET:
            safe_fps = math.floor((_FRAMES_SAFE_TARGET / chunk_duration) * 100) / 100.0
            safe_fps = max(_LONG_CHUNK_FPS_MIN, min(_LONG_CHUNK_FPS_MAX, safe_fps))
            if safe_fps < fps:
                fps_adjust_reasons.append(
                    f"按 {fps:g}fps 会超过安全帧数，降到 {safe_fps:g}fps"
                )
                fps = safe_fps
        fallback_reasons = validation_reasons + fps_adjust_reasons

        normalized.append({
            **item,
            "recommended_fps": fps,
            "confidence": confidence,
            "information_carriers": information_carriers,
            "scores": scores,
            "rough_summary": str(_segment_value(segment, "rough_summary", "summary", "rough_content", default="") or "").strip()[:800],
            "evidence": evidence,
            "focus": focus,
            "lite_brief": lite_brief,
            "risk_flags": risk_flags,
            "why_not_lower_fps": str(_segment_value(segment, "why_not_lower_fps", "why_not_lower", "lower_fps_risk", default="") or "").strip()[:800],
            "fallback_applied": bool(fallback_reasons),
            "fallback_reason": "; ".join(fallback_reasons),
            "fallback_type": (
                "validation_fallback" if validation_reasons
                else "fps_adjustment" if fps_adjust_reasons
                else ""
            ),
            "validation_fallback": bool(validation_reasons),
            "fps_adjusted": bool(fps_adjust_reasons),
            "fps_adjust_reason": "; ".join(fps_adjust_reasons),
        })

    return {
        "ok": True,
        "fallback_reason": "",
        "overview": overview,
        "global_notes": str(strategy.get("global_notes") or "").strip()[:1200],
        "detected_structure": {**detected_structure, "segments_path": segments_path},
        "raw_text": strategy_text[:4000],
        "chunks": normalized,
    }


def _overview_text_for_prompt(strategy: Optional[dict[str, Any]]) -> str:
    if not strategy:
        return ""
    overview = strategy.get("overview") if isinstance(strategy.get("overview"), dict) else {}
    pieces: list[str] = []
    summary = str(overview.get("summary") or "").strip()
    if summary:
        pieces.append(f"全片概览：{summary}")
    timeline = overview.get("timeline")
    if isinstance(timeline, list) and timeline:
        lines = []
        for item in timeline[:12]:
            if not isinstance(item, dict):
                continue
            start = _to_float(item.get("start_sec"), 0.0)
            end = _to_float(item.get("end_sec"), 0.0)
            chapter = str(item.get("chapter") or "").strip()
            content = str(item.get("rough_content") or "").strip()
            if chapter or content:
                lines.append(f"- {start:.0f}s-{end:.0f}s：{chapter} {content}".strip())
        if lines:
            pieces.append("粗时间线：\n" + "\n".join(lines))
    important = _string_list(overview.get("important_points"), limit=10)
    if important:
        pieces.append("重要线索：\n" + "\n".join(f"- {item}" for item in important))
    uncertain = _string_list(overview.get("uncertain_points"), limit=8)
    if uncertain:
        pieces.append("不确定点：\n" + "\n".join(f"- {item}" for item in uncertain))
    global_notes = str(strategy.get("global_notes") or "").strip()
    if global_notes:
        pieces.append(f"策略总评：{global_notes}")
    return "\n\n".join(pieces).strip()


def _chunk_strategy_context(strategy: Optional[dict[str, Any]], part_index: int) -> str:
    if not strategy:
        return ""
    chunks = strategy.get("chunks") if isinstance(strategy.get("chunks"), list) else []
    item = next((chunk for chunk in chunks if int(chunk.get("part_index", -1)) == part_index), None)
    if not isinstance(item, dict):
        return ""
    lines = [
        "本段精拆策略：",
        f"- 推荐 fps：{item.get('recommended_fps')}",
        f"- 策略置信度：{item.get('confidence')}",
    ]
    rough = str(item.get("rough_summary") or "").strip()
    if rough:
        lines.append(f"- 粗摘要：{rough}")
    carriers = item.get("information_carriers")
    if isinstance(carriers, dict) and carriers:
        lines.append(
            "- 信息承载判断："
            + json.dumps(carriers, ensure_ascii=False, sort_keys=True)
        )
    lite_brief = str(item.get("lite_brief") or "").strip()
    if lite_brief:
        lines.append(f"- 给本段精拆模型的说明：{lite_brief}")
    for key, label in (
        ("evidence", "证据"),
        ("focus", "精拆重点"),
        ("risk_flags", "风险"),
    ):
        values = _string_list(item.get(key), limit=6)
        if values:
            lines.append(f"- {label}：" + "；".join(values))
    why = str(item.get("why_not_lower_fps") or "").strip()
    if why:
        lines.append(f"- 不用更低 fps 的理由：{why}")
    if item.get("validation_fallback"):
        lines.append(f"- 策略结构兜底：{item.get('fallback_reason')}")
    elif item.get("fps_adjusted"):
        lines.append(f"- 程序 fps 调整：{item.get('fps_adjust_reason')}")
    return "\n".join(lines)


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


def _validate_ark_endpoint(endpoint: str) -> str:
    normalized = str(endpoint or "").strip().rstrip("/")
    parsed = urllib.parse.urlparse(normalized)
    if parsed.scheme != "https" or not parsed.hostname:
        raise AnalyzerError("Ark endpoint 必须是有效的 HTTPS 地址")
    if parsed.username or parsed.password:
        raise AnalyzerError("Ark endpoint 不能包含账号密码")
    if parsed.hostname.lower() not in _TRUSTED_ARK_HOSTS:
        raise AnalyzerError("Ark endpoint 必须使用可信 Ark 官方域名")
    if _is_agent_plan_endpoint(normalized):
        raise AnalyzerError("Agent Plan 不再作为运行通道；请使用字节跳动火山方舟 Ark API endpoint")
    return normalized


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
    if not (_FPS_MIN <= float(fps) <= _FPS_MAX):
        raise AnalyzerError(
            f"模型视频上传 fps 必须在 {_FPS_MIN:g}-{_FPS_MAX:g}，实际 {fps!r}"
        )

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
    if previous_response_id:
        await asyncio.sleep(0.12)

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
        "response_stored": bool(response_id),
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
    if previous_response_id:
        await asyncio.sleep(0.12)

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
        "response_stored": bool(response_id),
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
    strategy_model: Optional[str] = None,
    file_api_key: Optional[str] = None,
    file_endpoint: Optional[str] = None,
    quality: str = "quality",
    quality_params: Optional[dict] = None,
    source_id: Optional[str] = None,
    audit_id: Optional[str] = None,
    analysis_key: str = "default",
    file_active_timeout_sec: int = 120,
    response_timeout_sec: int = 900,
    chunk_concurrency: int = _CHUNK_ANALYSIS_CONCURRENCY,
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
      quality_params: {target_frames, fps_min, fps_max, fps_mode}，可选；
                      不传则使用默认（balanced=240, quality=1250）
      file_active_timeout_sec: 等 file active 的最长秒数
      response_timeout_sec: 等 Responses API 返回的最长秒数
      on_progress: async (stage:str, info:dict) -> None，可选进度回调
    """
    key = str(analysis_key or "default").strip() or "default"
    results = await analyze_video_many(
        video_path,
        {key: prompt},
        api_key=api_key,
        endpoint=endpoint,
        model=model,
        strategy_model=strategy_model,
        file_api_key=file_api_key,
        file_endpoint=file_endpoint,
        quality=quality,
        quality_params=quality_params,
        source_id=source_id,
        audit_id=audit_id,
        file_active_timeout_sec=file_active_timeout_sec,
        response_timeout_sec=response_timeout_sec,
        chunk_concurrency=chunk_concurrency,
        on_progress=on_progress,
    )
    return results[key]


async def analyze_video_many(
    video_path: Path,
    prompts: dict[str, str],
    *,
    api_key: str,
    endpoint: str,
    model: str,
    strategy_model: Optional[str] = None,
    file_api_key: Optional[str] = None,
    file_endpoint: Optional[str] = None,
    quality: str = "quality",
    quality_params: Optional[dict] = None,
    source_id: Optional[str] = None,
    audit_id: Optional[str] = None,
    file_active_timeout_sec: int = 120,
    response_timeout_sec: int = 900,
    chunk_concurrency: int = _CHUNK_ANALYSIS_CONCURRENCY,
    on_progress: ProgressCb = None,
) -> dict[str, AnalyzeResult]:
    """Analyze one video with multiple prompts while reusing one video input.

    Ordinary Ark uploads once and reuses the active file_id. Videos longer than
    10 minutes are split into 240s chunks with 10s overlap.
    """
    if not prompts:
        raise AnalyzerError("缺少视频分析 prompt")
    endpoint = _validate_ark_endpoint(endpoint)
    file_endpoint = _validate_ark_endpoint(file_endpoint or _default_files_endpoint(endpoint))

    video_path = Path(video_path).expanduser().resolve()
    if not video_path.exists():
        raise AnalyzerError(f"视频文件不存在: {video_path}")
    memory_source_id = str(source_id or video_path.stem)
    audit_root = _audit_dir(audit_id, memory_source_id)
    audit_files: dict[str, Any] = {}
    q_params = quality_params or {}
    try:
        sampling_mode = normalize_fps_mode(q_params.get("fps_mode", FPS_MODE_AUTO))
    except ValueError as exc:
        raise AnalyzerError(str(exc)) from exc
    if audit_root is not None:
        _add_artifact(audit_files, "run_manifest", _write_audit_json(audit_root, "00-run-manifest.json", {
            "audit_id": audit_id,
            "source_id": memory_source_id,
            "video_file_name": video_path.name,
            "intents": list(prompts),
            "analysis_model": model,
            "strategy_model": strategy_model or model,
            "quality": quality,
            "video_fps_mode": sampling_mode,
            "created_at": time.time(),
        }))
        for intent, prompt in prompts.items():
            _add_artifact(
                audit_files,
                f"prompt.{intent}",
                _write_audit_text(audit_root, f"01-prompts/{intent}.md", prompt),
            )
    strategy_model = strategy_model or model
    chunk_concurrency = max(1, min(4, int(chunk_concurrency or _CHUNK_ANALYSIS_CONCURRENCY)))

    # 1. 测时长
    duration = get_duration_sec(video_path)
    await _call_progress(on_progress, "probed_duration", {
        "duration_sec": duration,
        "file_size_mb": video_path.stat().st_size / 1024 / 1024,
    })

    # 2. 文件大小校验
    _check_size(video_path)

    # 3. 本地变化预扫描 + 2-5fps 决策。固定档无需预扫描。
    if sampling_mode == FPS_MODE_AUTO:
        await _call_progress(on_progress, "prescanning_started", {
            "purpose": "local_visual_change_measurement_only",
            "sample_fps": 1.0,
            "duration_sec": duration,
        })
        thumbnail_dir = audit_root / "01-sampling" / "thumbnails" if audit_root else None
        prescan = await asyncio.to_thread(
            prescan_video,
            video_path,
            duration,
            ffmpeg_path=_ffmpeg_command(),
            thumbnail_dir=thumbnail_dir,
        )
        await _call_progress(
            on_progress,
            "prescanning_done" if prescan.get("ok") else "prescanning_failed",
            {
                "ok": bool(prescan.get("ok")),
                "elapsed_sec": prescan.get("elapsed_sec"),
                "sample_count": prescan.get("sample_count"),
                "change_point_count": len(prescan.get("change_points") or []),
                "coverage_ratio": prescan.get("coverage_ratio"),
                "failure_reason": prescan.get("failure_reason"),
            },
        )
    else:
        prescan = {
            "ok": False,
            "skipped": True,
            "purpose": "local_visual_change_measurement_only",
            "sample_fps": 1.0,
            "sample_count": 0,
            "elapsed_sec": 0.0,
            "timestamps_sec": [],
            "thumbnail_manifest": [],
            "change_points": [],
            "failure_reason": "fixed FPS mode does not require prescan",
        }
        await _call_progress(on_progress, "prescanning_skipped", {
            "mode": sampling_mode,
            "reason": prescan["failure_reason"],
        })

    decision = decide_sampling_fps(
        mode=sampling_mode,
        duration_sec=duration,
        prescan=prescan,
    )
    fps = float(decision["selected_fps"])
    target_frames = min(
        _FRAMES_SAFE_TARGET,
        max(1, int(q_params.get("target_frames", _FRAMES_SAFE_TARGET))),
    )
    actual_frames_est = int(fps * duration)
    will_truncate = actual_frames_est > _FRAMES_HARD_CAP
    sampling_evidence = _new_sampling_evidence(
        mode=sampling_mode,
        duration_sec=duration,
        prescan=prescan,
        decision=decision,
    )
    _persist_sampling_evidence(audit_root, audit_files, sampling_evidence)

    await _call_progress(on_progress, "fps_decided", {
        "fps": fps,
        "target_frames": target_frames,
        "actual_frames_estimate": actual_frames_est,
        "will_truncate": will_truncate,
        "quality": quality,
        "mode": sampling_mode,
        "decision_reasons": decision.get("decision_reasons"),
        "fallback_applied": decision.get("fallback_applied"),
        "fallback_reason": decision.get("fallback_reason"),
    })

    responses_client = _build_response_client(api_key, endpoint, response_timeout_sec)

    files_client = _build_client(
        file_api_key or api_key,
        file_endpoint,
    )

    frame_budget_requires_chunks = actual_frames_est > _FRAMES_SAFE_TARGET
    chunk_plan = _chunk_plan(
        duration,
        force_for_frame_budget=frame_budget_requires_chunks,
    )
    if chunk_plan:
        await _call_progress(on_progress, "chunking_plan", {
            "chunk_count": len(chunk_plan),
            "chunk_len_sec": _CHUNK_LEN_SEC,
            "overlap_sec": _CHUNK_OVERLAP_SEC,
            "duration_sec": duration,
            "reason": (
                "requested_fps_exceeds_safe_frame_budget"
                if frame_budget_requires_chunks and duration <= _CHUNK_THRESHOLD_SEC
                else "long_video_duration"
            ),
        })
        with tempfile.TemporaryDirectory(prefix="agent-wiki-chunks-") as tmpdir:
            chunk_paths = await asyncio.to_thread(
                _split_video_for_chunks,
                video_path,
                chunk_plan,
                Path(tmpdir),
            )
            strategy = await _prepare_long_video_strategy(
                video_path,
                chunk_plan,
                list(prompts),
                files_client=files_client,
                responses_client=responses_client,
                model=model,
                strategy_model=strategy_model,
                file_active_timeout_sec=file_active_timeout_sec,
                response_timeout_sec=response_timeout_sec,
                source_id=memory_source_id,
                audit_dir=audit_root,
                audit_files=audit_files,
                sampling_evidence=sampling_evidence,
                chunk_paths=chunk_paths,
                chunk_concurrency=chunk_concurrency,
                on_progress=on_progress,
            )
            strategy = merge_chunk_sampling_strategy(
                strategy,
                chunk_plan,
                mode=sampling_mode,
                prescan=prescan,
            )
            repacked_plan, repacked_chunks, repack_changed = _repack_analysis_plan(
                chunk_plan, strategy
            )
            if repack_changed:
                strategy["analysis_plan_repacked"] = {
                    "original_chunk_count": len(chunk_plan),
                    "repacked_chunk_count": len(repacked_plan),
                    "rule": "merge adjacent same-fps segments up to safe frame budget",
                }
                chunk_plan = repacked_plan
                strategy["chunks"] = repacked_chunks
                chunk_paths = await asyncio.to_thread(
                    _split_video_for_chunks,
                    video_path,
                    chunk_plan,
                    Path(tmpdir) / "analysis-chunks",
                )
                await _call_progress(on_progress, "chunking_repacked", {
                    "chunk_count": len(chunk_plan),
                    "rule": "frame_budget_packing",
                })
            _add_artifact(
                audit_files,
                "sampling.chunk_strategy",
                _write_audit_json(audit_root, "01-sampling/chunk-strategy.json", strategy),
            )
            _persist_sampling_evidence(audit_root, audit_files, sampling_evidence)
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
                strategy=strategy,
                audit_dir=audit_root,
                audit_files=audit_files,
                sampling_evidence=sampling_evidence,
                file_active_timeout_sec=file_active_timeout_sec,
                response_timeout_sec=response_timeout_sec,
                chunk_concurrency=chunk_concurrency,
                on_progress=on_progress,
            )

    await _call_progress(on_progress, "uploading", {
        "file_name": video_path.name,
        "requested_fps": fps,
        "planned_frame_count": actual_frames_est,
    })
    _record_upload_evidence(
        sampling_evidence,
        phase="precision_analysis",
        fps=fps,
        duration_sec=duration,
        model=model,
    )
    _persist_sampling_evidence(audit_root, audit_files, sampling_evidence)
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
    _record_upload_evidence(
        sampling_evidence,
        phase="precision_analysis",
        fps=fps,
        duration_sec=duration,
        model=model,
        file_obj=file_obj,
        record_request=False,
    )
    _persist_sampling_evidence(audit_root, audit_files, sampling_evidence)

    # 5. 等 active
    active_obj = await _wait_for_active(
        files_client, file_id,
        timeout_sec=file_active_timeout_sec,
        on_progress=on_progress,
    )
    _record_upload_evidence(
        sampling_evidence,
        phase="precision_analysis",
        fps=fps,
        duration_sec=duration,
        model=model,
        file_obj=file_obj,
        active_obj=active_obj,
        record_request=False,
    )
    _persist_sampling_evidence(audit_root, audit_files, sampling_evidence)

    # 6. 用同一个 file_id 按多个 prompt 顺序拆解
    results: dict[str, AnalyzeResult] = {}
    for intent, prompt in prompts.items():
        prompt_hash = _prompt_hash(prompt)
        memory = load_response_memory(
            media_type="douyin_video",
            source_id=memory_source_id,
            ingest_intent=intent,
            model=model,
            prompt_hash=prompt_hash,
            flow_version="single-v1",
            chunked=False,
        )
        previous_response_id = memory.get("response_id") if memory else None
        await _call_progress(on_progress, "analyzing", {
            "file_id": file_id,
            "model": model,
            "intent": intent,
            "has_previous_response": bool(previous_response_id),
        })
        call = await _retry_response_call(
            lambda: _stream_responses(
                responses_client,
                model=model,
                file_id=file_id,
                prompt=prompt,
                on_progress=on_progress,
                timeout_sec=response_timeout_sec,
                previous_response_id=previous_response_id,
            ),
            label="single_video_analysis",
            progress_stage="analysis_retrying",
            on_progress=on_progress,
            context={
                "source_id": memory_source_id,
                "file_id": file_id,
                "model": model,
                "intent": intent,
            },
        )
        text, usage = call.text, call.usage
        if not text.strip():
            raise APIError(f"Responses API 未返回可写入的分析文本: {intent}")
        _record_response_evidence(
            sampling_evidence,
            phase="precision_analysis",
            model=model,
            usage=usage,
            text_length=len(text),
            intent=intent,
        )
        _persist_sampling_evidence(audit_root, audit_files, sampling_evidence)
        save_response_memory(
            media_type="douyin_video",
            source_id=memory_source_id,
            ingest_intent=intent,
            model=model,
            prompt_hash=prompt_hash,
            flow_version="single-v1",
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
            audit_artifacts={"dir": _audit_rel(audit_root) if audit_root else "", "files": audit_files},
        )
    return results


async def _prepare_long_video_strategy(
    video_path: Path,
    chunk_plan: list[dict[str, float | int]],
    intents: list[str],
    *,
    files_client: Any,
    responses_client: Any,
    model: str,
    strategy_model: str,
    source_id: str,
    file_active_timeout_sec: int,
    response_timeout_sec: int,
    chunk_paths: Optional[list[Path]] = None,
    chunk_concurrency: int = _CHUNK_ANALYSIS_CONCURRENCY,
    audit_dir: Optional[Path] = None,
    audit_files: Optional[dict[str, Any]] = None,
    sampling_evidence: Optional[dict[str, Any]] = None,
    on_progress: ProgressCb,
) -> dict[str, Any]:
    """Run a 2fps full-video overview or chunked overview and return a per-chunk strategy."""
    overview_duration = float(chunk_plan[-1]["end_sec"]) if chunk_plan else 0.0
    audit_files = audit_files if audit_files is not None else {}
    strategy_usages: list[dict[str, Any]] = []
    try:
        if _is_ultra_long_video(overview_duration):
            call = await _prepare_chunked_overview_strategy(
                chunk_paths or [],
                chunk_plan,
                intents,
                files_client=files_client,
                responses_client=responses_client,
                strategy_model=strategy_model,
                duration_sec=overview_duration,
                audit_dir=audit_dir,
                audit_files=audit_files,
                sampling_evidence=sampling_evidence,
                file_active_timeout_sec=file_active_timeout_sec,
                response_timeout_sec=response_timeout_sec,
                chunk_concurrency=chunk_concurrency,
                on_progress=on_progress,
            )
        else:
            overview_fps = _long_overview_fps(overview_duration)
            await _call_progress(on_progress, "overview_uploading", {
                "fps": overview_fps,
                "chunk_count": len(chunk_plan),
                "model": strategy_model,
            })
            _record_upload_evidence(
                sampling_evidence,
                phase="overview_strategy",
                fps=overview_fps,
                duration_sec=overview_duration,
                model=strategy_model,
            )
            _persist_sampling_evidence(audit_dir, audit_files, sampling_evidence)
            file_obj = await _upload_with_preprocess(
                files_client, video_path, fps=overview_fps, model=strategy_model
            )
            file_id = getattr(file_obj, "id", None) or getattr(file_obj, "file_id", None)
            if not file_id:
                raise APIError(f"Files API 概览上传返回缺 id: {file_obj!r}")
            await _call_progress(on_progress, "overview_uploaded", {
                "file_id": file_id,
                "fps": overview_fps,
            })
            _record_upload_evidence(
                sampling_evidence,
                phase="overview_strategy",
                fps=overview_fps,
                duration_sec=overview_duration,
                model=strategy_model,
                file_obj=file_obj,
                record_request=False,
            )
            _persist_sampling_evidence(audit_dir, audit_files, sampling_evidence)
            active_obj = await _wait_for_active(
                files_client,
                file_id,
                timeout_sec=file_active_timeout_sec,
                on_progress=on_progress,
            )
            _record_upload_evidence(
                sampling_evidence,
                phase="overview_strategy",
                fps=overview_fps,
                duration_sec=overview_duration,
                model=strategy_model,
                file_obj=file_obj,
                active_obj=active_obj,
                record_request=False,
            )
            _persist_sampling_evidence(audit_dir, audit_files, sampling_evidence)
            overview_prompt = _build_long_overview_prompt(
                duration_sec=overview_duration,
                chunk_plan=chunk_plan,
                intents=intents,
            )
            _add_artifact(
                audit_files,
                "mini.overview_strategy_prompt",
                _write_audit_text(audit_dir, "02-mini/overview-strategy-prompt.md", overview_prompt),
            )
            await _call_progress(on_progress, "analyzing_overview", {
                "file_id": file_id,
                "model": strategy_model,
                "fps": overview_fps,
            })
            call = await _retry_response_call(
                lambda: _stream_responses(
                    responses_client,
                    model=strategy_model,
                    file_id=file_id,
                    prompt=overview_prompt,
                    on_progress=on_progress,
                    timeout_sec=response_timeout_sec,
                ),
                label="overview_strategy",
                progress_stage="overview_strategy_retrying",
                on_progress=on_progress,
                context={
                    "source_id": source_id,
                    "model": strategy_model,
                    "fps": overview_fps,
                },
            )
            _record_response_evidence(
                sampling_evidence,
                phase="overview_strategy",
                model=strategy_model,
                usage=call.usage,
                text_length=len(call.text),
            )
            _persist_sampling_evidence(audit_dir, audit_files, sampling_evidence)
        strategy_usages.append(call.usage)
        if not call.text.strip():
            raise APIError("Responses API 未返回长视频概览策略文本")
        _add_artifact(
            audit_files,
            "mini.overview_strategy_raw",
            _write_audit_text(audit_dir, "02-mini/overview-strategy-raw.md", call.text),
        )
        strategy = _normalize_long_video_strategy(call.text, chunk_plan)
        _add_artifact(
            audit_files,
            "mini.overview_strategy_normalized_initial",
            _write_audit_json(audit_dir, "02-mini/overview-strategy-normalized-initial.json", strategy),
        )
        if _strategy_needs_json_repair(strategy):
            original_strategy = strategy
            repair_reason = str(strategy.get("fallback_reason") or "策略字段缺失")
            _write_strategy_log("overview_strategy_repair_needed", {
                "source_id": source_id,
                "strategy_model": strategy_model,
                "analysis_model": model,
                "reason": repair_reason,
                "detected_structure": strategy.get("detected_structure", {}),
                "raw_text": strategy.get("raw_text", call.text),
            })
            await _call_progress(on_progress, "repairing_overview_strategy", {
                "reason": repair_reason,
                "model": strategy_model,
            })
            repair_prompt = _build_strategy_repair_prompt(
                validation_reason=repair_reason,
                chunk_plan=chunk_plan,
                raw_text=call.text,
            )
            _add_artifact(
                audit_files,
                "mini.strategy_repair_prompt",
                _write_audit_text(audit_dir, "02-mini/strategy-repair-prompt.md", repair_prompt),
            )
            repair = await _retry_response_call(
                lambda: _call_text_responses(
                    responses_client,
                    model=strategy_model,
                    prompt=repair_prompt,
                    on_progress=on_progress,
                    previous_response_id=call.response_id,
                    timeout_sec=response_timeout_sec,
                ),
                label="overview_strategy_repair",
                progress_stage="overview_strategy_repair_retrying",
                on_progress=on_progress,
                context={
                    "source_id": source_id,
                    "model": strategy_model,
                    "reason": repair_reason,
                },
            )
            _record_response_evidence(
                sampling_evidence,
                phase="overview_strategy_repair",
                model=strategy_model,
                usage=repair.usage,
                text_length=len(repair.text),
            )
            _persist_sampling_evidence(audit_dir, audit_files, sampling_evidence)
            strategy_usages.append(repair.usage)
            _add_artifact(
                audit_files,
                "mini.strategy_repair_raw",
                _write_audit_text(audit_dir, "02-mini/strategy-repair-raw.md", repair.text),
            )
            repaired_strategy = _normalize_long_video_strategy(repair.text, chunk_plan)
            _add_artifact(
                audit_files,
                "mini.strategy_repair_normalized",
                _write_audit_json(audit_dir, "02-mini/strategy-repair-normalized.json", repaired_strategy),
            )
            if not _strategy_needs_json_repair(repaired_strategy):
                strategy = repaired_strategy
                _write_strategy_log("overview_strategy_repaired", {
                    "source_id": source_id,
                    "strategy_model": strategy_model,
                    "analysis_model": model,
                    "reason": repair_reason,
                    "detected_structure": repaired_strategy.get("detected_structure", {}),
                    "usage": repair.usage,
                })
                await _call_progress(on_progress, "overview_strategy_repaired", {
                    "ok": True,
                    "model": strategy_model,
                })
            else:
                strategy = _better_strategy(original_strategy, repaired_strategy)
                _write_strategy_log("overview_strategy_repair_failed", {
                    "source_id": source_id,
                    "strategy_model": strategy_model,
                    "analysis_model": model,
                    "reason": repaired_strategy.get("fallback_reason", repair_reason),
                    "detected_structure": repaired_strategy.get("detected_structure", {}),
                    "kept_original_strategy": strategy is original_strategy,
                    "raw_text": repaired_strategy.get("raw_text", repair.text),
                    "usage": repair.usage,
                    "fallback": "fallback_to_5fps",
                })
        await _call_progress(on_progress, "overview_strategy_decided", {
            "ok": bool(strategy.get("ok")),
            "fallback_reason": strategy.get("fallback_reason", ""),
            "chunk_count": len(strategy.get("chunks", [])),
            "model": strategy_model,
            "fps_plan": [
                {
                    "part_index": item.get("part_index"),
                    "fps": item.get("recommended_fps"),
                    "confidence": item.get("confidence"),
                    "fallback_applied": item.get("fallback_applied"),
                    "validation_fallback": item.get("validation_fallback"),
                    "fps_adjusted": item.get("fps_adjusted"),
                    "fps_adjust_reason": item.get("fps_adjust_reason"),
                    "lite_brief": str(item.get("lite_brief") or "")[:300],
                }
                for item in strategy.get("chunks", [])
            ],
            "audit_artifacts": {
                "dir": _audit_rel(audit_dir) if audit_dir else "",
                "files": audit_files,
            },
        })
        fallback_chunks = [
            {
                "part_index": item.get("part_index"),
                "fps": item.get("recommended_fps"),
                "confidence": item.get("confidence"),
                "reason": item.get("fallback_reason"),
                "fallback_type": item.get("fallback_type"),
            }
            for item in strategy.get("chunks", [])
            if isinstance(item, dict) and item.get("fallback_applied")
        ]
        if fallback_chunks or not strategy.get("ok"):
            _write_strategy_log("overview_strategy_fallback_applied", {
                "source_id": source_id,
                "strategy_model": strategy_model,
                "analysis_model": model,
                "ok": bool(strategy.get("ok")),
                "fallback_reason": strategy.get("fallback_reason", ""),
                "fallback_chunks": fallback_chunks,
                "fps_plan": [
                    {
                        "part_index": item.get("part_index"),
                        "fps": item.get("recommended_fps"),
                        "confidence": item.get("confidence"),
                    }
                    for item in strategy.get("chunks", [])
                    if isinstance(item, dict)
                ],
            })
        strategy["usage_by_model"] = {
            strategy_model: _combine_usage(strategy_usages),
        }
        _add_artifact(
            audit_files,
            "mini.overview_strategy_final",
            _write_audit_json(audit_dir, "02-mini/overview-strategy-final.json", strategy),
        )
        return strategy
    except Exception as e:
        reason = f"长视频概览策略失败，按 5fps 分片兜底: {e}"
        strategy = _strategy_fallback(chunk_plan, reason=reason)
        strategy["usage_by_model"] = {
            strategy_model: _combine_usage(strategy_usages),
        }
        _write_strategy_log("overview_strategy_failed", {
            "source_id": source_id,
            "strategy_model": strategy_model,
            "analysis_model": model,
            "reason": reason,
            "fallback": "fallback_to_5fps",
        })
        await _call_progress(on_progress, "overview_strategy_decided", {
            "ok": False,
            "fallback_reason": reason,
            "model": strategy_model,
            "chunk_count": len(chunk_plan),
            "fps_plan": [
                {"part_index": item["part_index"], "fps": _LONG_CHUNK_FPS_MAX}
                for item in chunk_plan
            ],
        })
        return strategy


async def _prepare_chunked_overview_strategy(
    chunk_paths: list[Path],
    chunk_plan: list[dict[str, float | int]],
    intents: list[str],
    *,
    files_client: Any,
    responses_client: Any,
    strategy_model: str,
    duration_sec: float,
    audit_dir: Optional[Path] = None,
    audit_files: Optional[dict[str, Any]] = None,
    sampling_evidence: Optional[dict[str, Any]] = None,
    file_active_timeout_sec: int,
    response_timeout_sec: int,
    chunk_concurrency: int,
    on_progress: ProgressCb,
) -> ResponseCallResult:
    """Build ultra-long-video strategy from 2fps overview chunks."""
    if len(chunk_paths) != len(chunk_plan):
        raise AnalyzerError("超长视频概览切片数量与计划不一致")
    audit_files = audit_files if audit_files is not None else {}

    full_overview_frames = int(math.ceil(duration_sec * _LONG_OVERVIEW_FPS))
    chunk_concurrency = max(1, min(4, int(chunk_concurrency or _CHUNK_ANALYSIS_CONCURRENCY)))
    total = len(chunk_plan)
    await _call_progress(on_progress, "overview_chunking", {
        "mode": "ultra_long_video",
        "chunk_count": total,
        "fps": _LONG_OVERVIEW_FPS,
        "concurrency": chunk_concurrency,
        "estimated_full_overview_frames": full_overview_frames,
        "safe_target": _FRAMES_SAFE_TARGET,
        "ultra_long_threshold_sec": _ultra_long_threshold_sec(),
    })

    _write_strategy_log("overview_strategy_chunked_started", {
        "mode": "ultra_long_video",
        "duration_sec": duration_sec,
        "overview_fps": _LONG_OVERVIEW_FPS,
        "estimated_full_overview_frames": full_overview_frames,
        "safe_target": _FRAMES_SAFE_TARGET,
        "ultra_long_threshold_sec": _ultra_long_threshold_sec(),
        "chunk_count": total,
        "chunk_overview_fps": _LONG_OVERVIEW_FPS,
        "concurrency": chunk_concurrency,
    })

    pieces: list[tuple[int, str]] = []
    usages: list[dict[str, Any]] = []
    lock = asyncio.Lock()
    semaphore = asyncio.Semaphore(chunk_concurrency)

    async def process_overview_chunk(
        chunk_path: Path,
        plan_item: dict[str, float | int],
    ) -> None:
        part_index = int(plan_item["part_index"])
        await _call_progress(on_progress, "overview_chunk_uploading", {
            "part_index": part_index,
            "chunk_count": total,
            "fps": _LONG_OVERVIEW_FPS,
            "model": strategy_model,
            "concurrency": chunk_concurrency,
        })
        _record_upload_evidence(
            sampling_evidence,
            phase="overview_strategy_chunk",
            fps=_LONG_OVERVIEW_FPS,
            duration_sec=float(plan_item["end_sec"]) - float(plan_item["start_sec"]),
            model=strategy_model,
            part_index=part_index,
        )
        _persist_sampling_evidence(audit_dir, audit_files, sampling_evidence)
        file_obj = await _upload_with_preprocess(
            files_client,
            chunk_path,
            fps=_LONG_OVERVIEW_FPS,
            model=strategy_model,
        )
        file_id = getattr(file_obj, "id", None) or getattr(file_obj, "file_id", None)
        if not file_id:
            raise APIError(f"Files API 超长概览切片返回缺 id part={part_index}: {file_obj!r}")
        await _call_progress(on_progress, "overview_chunk_uploaded", {
            "part_index": part_index,
            "chunk_count": total,
            "file_id": file_id,
        })
        _record_upload_evidence(
            sampling_evidence,
            phase="overview_strategy_chunk",
            fps=_LONG_OVERVIEW_FPS,
            duration_sec=float(plan_item["end_sec"]) - float(plan_item["start_sec"]),
            model=strategy_model,
            file_obj=file_obj,
            part_index=part_index,
            record_request=False,
        )
        _persist_sampling_evidence(audit_dir, audit_files, sampling_evidence)
        active_obj = await _wait_for_active(
            files_client,
            file_id,
            timeout_sec=file_active_timeout_sec,
            on_progress=on_progress,
        )
        _record_upload_evidence(
            sampling_evidence,
            phase="overview_strategy_chunk",
            fps=_LONG_OVERVIEW_FPS,
            duration_sec=float(plan_item["end_sec"]) - float(plan_item["start_sec"]),
            model=strategy_model,
            file_obj=file_obj,
            active_obj=active_obj,
            part_index=part_index,
            record_request=False,
        )
        _persist_sampling_evidence(audit_dir, audit_files, sampling_evidence)
        prompt = _build_overview_chunk_prompt(
            duration_sec=duration_sec,
            chunk_count=total,
            plan_item=plan_item,
            intents=intents,
        )
        _add_artifact(
            audit_files,
            f"mini.overview_chunk.{part_index}.prompt",
            _write_audit_text(
                audit_dir,
                f"02-mini/overview-chunks/part-{part_index:03d}-prompt.md",
                prompt,
            ),
        )
        await _call_progress(on_progress, "analyzing_overview_chunk", {
            "part_index": part_index,
            "chunk_count": total,
            "file_id": file_id,
            "model": strategy_model,
            "fps": _LONG_OVERVIEW_FPS,
        })
        call = await _retry_response_call(
            lambda: _stream_responses(
                responses_client,
                model=strategy_model,
                file_id=file_id,
                prompt=prompt,
                on_progress=on_progress,
                timeout_sec=response_timeout_sec,
            ),
            label="overview_chunk",
            progress_stage="overview_chunk_retrying",
            on_progress=on_progress,
            context={
                "part_index": part_index,
                "chunk_count": total,
                "model": strategy_model,
                "fps": _LONG_OVERVIEW_FPS,
            },
        )
        if not call.text.strip():
            raise APIError(f"Responses API 未返回超长概览切片文本 part={part_index}")
        _record_response_evidence(
            sampling_evidence,
            phase="overview_strategy_chunk",
            model=strategy_model,
            usage=call.usage,
            text_length=len(call.text),
            part_index=part_index,
        )
        _persist_sampling_evidence(audit_dir, audit_files, sampling_evidence)
        async with lock:
            pieces.append((part_index, call.text.strip()))
            usages.append(call.usage)
            _add_artifact(
                audit_files,
                f"mini.overview_chunk.{part_index}.output",
                _write_audit_text(
                    audit_dir,
                    f"02-mini/overview-chunks/part-{part_index:03d}-output.md",
                    call.text.strip(),
                ),
            )
        await _call_progress(on_progress, "overview_chunk_done", {
            "part_index": part_index,
            "chunk_count": total,
            "text_length": len(call.text),
            "artifact": audit_files.get(f"mini.overview_chunk.{part_index}.output", ""),
        })

    async def process_overview_chunk_guarded(
        chunk_path: Path,
        plan_item: dict[str, float | int],
    ) -> None:
        async with semaphore:
            await process_overview_chunk(chunk_path, plan_item)

    await asyncio.gather(*(
        process_overview_chunk_guarded(chunk_path, plan_item)
        for chunk_path, plan_item in zip(chunk_paths, chunk_plan)
    ))

    synth_prompt = _build_chunked_overview_strategy_prompt(
        duration_sec=duration_sec,
        chunk_plan=chunk_plan,
        intents=intents,
        overview_pieces=pieces,
    )
    _add_artifact(
        audit_files,
        "mini.chunked_strategy_prompt",
        _write_audit_text(audit_dir, "02-mini/chunked-strategy-prompt.md", synth_prompt),
    )
    _add_artifact(
        audit_files,
        "mini.overview_pieces",
        _write_audit_text(
            audit_dir,
            "02-mini/overview-pieces.md",
            "\n\n".join(
                f"## part {part_index}\n\n{text}"
                for part_index, text in sorted(pieces, key=lambda item: item[0])
            ),
        ),
    )
    await _call_progress(on_progress, "synthesizing_overview_strategy", {
        "chunk_count": total,
        "model": strategy_model,
    })
    synth = await _retry_response_call(
        lambda: _call_text_responses(
            responses_client,
            model=strategy_model,
            prompt=synth_prompt,
            on_progress=on_progress,
            timeout_sec=response_timeout_sec,
        ),
        label="chunked_overview_strategy",
        progress_stage="overview_strategy_synthesis_retrying",
        on_progress=on_progress,
        context={
            "chunk_count": total,
            "model": strategy_model,
        },
    )
    _record_response_evidence(
        sampling_evidence,
        phase="overview_strategy_synthesis",
        model=strategy_model,
        usage=synth.usage,
        text_length=len(synth.text),
    )
    _persist_sampling_evidence(audit_dir, audit_files, sampling_evidence)
    _add_artifact(
        audit_files,
        "mini.chunked_strategy_raw",
        _write_audit_text(audit_dir, "02-mini/chunked-strategy-raw.md", synth.text),
    )
    _write_strategy_log("overview_strategy_chunked_synthesized", {
        "chunk_count": total,
        "usage": _combine_usage(usages + [synth.usage]),
        "response_stored": bool(synth.response_id),
    })
    return ResponseCallResult(
        text=synth.text,
        usage=_combine_usage(usages + [synth.usage]),
        response_id=synth.response_id,
    )


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
    strategy: Optional[dict[str, Any]],
    audit_dir: Optional[Path] = None,
    audit_files: Optional[dict[str, Any]] = None,
    sampling_evidence: Optional[dict[str, Any]] = None,
    file_active_timeout_sec: int,
    response_timeout_sec: int,
    chunk_concurrency: int = _CHUNK_ANALYSIS_CONCURRENCY,
    on_progress: ProgressCb,
) -> dict[str, AnalyzeResult]:
    """Analyze fixed video chunks and return one aggregated result per intent."""
    audit_files = audit_files if audit_files is not None else {}
    per_intent_texts: dict[str, list[tuple[int, str]]] = {intent: [] for intent in prompts}
    per_intent_usages: dict[str, list[dict[str, Any]]] = {intent: [] for intent in prompts}
    previous_memory_response: dict[str, Optional[str]] = {}
    per_intent_chunks: dict[str, list[dict[str, Any]]] = {intent: [] for intent in prompts}
    total = len(chunk_paths)
    raw_strategy_chunks = (strategy or {}).get("chunks", [])
    if not isinstance(raw_strategy_chunks, list):
        raw_strategy_chunks = []
    strategy_chunks = {
        int(item.get("part_index")): item
        for item in raw_strategy_chunks
        if isinstance(item, dict) and item.get("part_index") is not None
    }
    overview_context = _overview_text_for_prompt(strategy)
    lock = asyncio.Lock()
    chunk_concurrency = max(1, min(4, int(chunk_concurrency or _CHUNK_ANALYSIS_CONCURRENCY)))
    semaphore = asyncio.Semaphore(chunk_concurrency)

    async def process_chunk(chunk_path: Path, plan_item: dict[str, float | int]) -> None:
        part_index = int(plan_item["part_index"])
        chunk_duration = float(plan_item["end_sec"]) - float(plan_item["start_sec"])
        strategy_item = strategy_chunks.get(part_index, {})
        if strategy_item:
            fps = float(strategy_item.get("recommended_fps") or _LONG_CHUNK_FPS_MAX)
            target_frames = _FRAMES_SAFE_TARGET
            will_truncate = int(fps * chunk_duration) > _FRAMES_HARD_CAP
        else:
            fps, target_frames, will_truncate = calc_fps(chunk_duration, quality)
        actual_frames_est = int(fps * chunk_duration)
        base_chunk_meta = {
            "part_index": part_index,
            "start_sec": plan_item["start_sec"],
            "end_sec": plan_item["end_sec"],
            "overlap_sec": plan_item["overlap_sec"],
            "fps": fps,
            "target_frames": target_frames,
            "actual_frames_estimate": actual_frames_est,
            "truncated": will_truncate,
            "strategy_confidence": strategy_item.get("confidence"),
            "strategy_scores": strategy_item.get("scores"),
            "strategy_fallback_applied": strategy_item.get("fallback_applied"),
            "strategy_fallback_reason": strategy_item.get("fallback_reason"),
            "strategy_validation_fallback": strategy_item.get("validation_fallback"),
            "strategy_fps_adjusted": strategy_item.get("fps_adjusted"),
            "strategy_fps_adjust_reason": strategy_item.get("fps_adjust_reason"),
            "strategy_lite_brief": strategy_item.get("lite_brief"),
            "strategy_focus": strategy_item.get("focus"),
        }
        intent_prompts: dict[str, str] = {}
        missing_intents: list[str] = []
        for intent, prompt in prompts.items():
            strategy_context = _chunk_strategy_context(strategy, part_index)
            chunk_prompt = f"{prompt}\n\n"
            if overview_context:
                chunk_prompt += f"{overview_context}\n\n"
            chunk_prompt += (
                f"{strategy_context}\n\n" if strategy_context else ""
            )
            chunk_prompt += (
                f"当前是长视频第 {part_index}/{total} 段，"
                f"时间范围 {float(plan_item['start_sec']):.1f}s - "
                f"{float(plan_item['end_sec']):.1f}s，"
                f"与上一段约重叠 {float(plan_item['overlap_sec']):.1f}s。"
                "请只分析当前片段，并保留可用于最终合并的结构化要点。"
            )
            intent_prompts[intent] = chunk_prompt
            chunk_prompt_hash = _prompt_hash(chunk_prompt)
            _add_artifact(
                audit_files,
                f"lite.{intent}.chunk.{part_index}.prompt",
                _write_audit_text(
                    audit_dir,
                    f"03-lite/{intent}/part-{part_index:03d}-prompt.md",
                    chunk_prompt,
                ),
            )
            cached = _cached_chunk_output(
                audit_dir,
                intent=intent,
                part_index=part_index,
                prompt_hash=chunk_prompt_hash,
            )
            if cached:
                text_piece, output_artifact = cached
                _add_artifact(
                    audit_files,
                    f"lite.{intent}.chunk.{part_index}.output",
                    output_artifact,
                )
                chunk_meta = {
                    **base_chunk_meta,
                    "file_id": "reused-from-artifact",
                    "audit_artifact": output_artifact,
                    "usage": {},
                    "reused_from_artifact": True,
                }
                async with lock:
                    per_intent_texts[intent].append((part_index, text_piece))
                    per_intent_chunks[intent].append(chunk_meta)
                await _call_progress(on_progress, "chunk_reused", {
                    "part_index": part_index,
                    "chunk_count": total,
                    "intent": intent,
                    "artifact": output_artifact,
                    "text_length": len(text_piece),
                })
                continue
            missing_intents.append(intent)

        if not missing_intents:
            return

        await _call_progress(on_progress, "chunk_uploading", {
            "part_index": part_index,
            "chunk_count": total,
            "concurrency": chunk_concurrency,
            "start_sec": plan_item["start_sec"],
            "end_sec": plan_item["end_sec"],
            "fps": fps,
            "strategy_confidence": strategy_item.get("confidence"),
            "strategy_fallback": strategy_item.get("fallback_applied"),
            "strategy_validation_fallback": strategy_item.get("validation_fallback"),
            "strategy_fps_adjusted": strategy_item.get("fps_adjusted"),
            "strategy_fps_adjust_reason": strategy_item.get("fps_adjust_reason"),
            "strategy_lite_brief": str(strategy_item.get("lite_brief") or "")[:300],
            "missing_intents": missing_intents,
        })
        _record_upload_evidence(
            sampling_evidence,
            phase="precision_chunk",
            fps=fps,
            duration_sec=chunk_duration,
            model=model,
            part_index=part_index,
        )
        _persist_sampling_evidence(audit_dir, audit_files, sampling_evidence)
        try:
            file_obj = await _upload_with_preprocess(files_client, chunk_path, fps=fps, model=model)
        except Exception as e:
            raise APIError(f"Files API 切片上传失败 part={part_index}: {e}") from e
        file_id = getattr(file_obj, "id", None) or getattr(file_obj, "file_id", None)
        if not file_id:
            raise APIError(f"Files API 切片返回缺 id part={part_index}: {file_obj!r}")
        await _call_progress(on_progress, "chunk_uploaded", {
            "part_index": part_index,
            "chunk_count": total,
            "file_id": file_id,
        })
        _record_upload_evidence(
            sampling_evidence,
            phase="precision_chunk",
            fps=fps,
            duration_sec=chunk_duration,
            model=model,
            file_obj=file_obj,
            part_index=part_index,
            record_request=False,
        )
        _persist_sampling_evidence(audit_dir, audit_files, sampling_evidence)
        active_obj = await _wait_for_active(
            files_client,
            file_id,
            timeout_sec=file_active_timeout_sec,
            on_progress=on_progress,
        )
        _record_upload_evidence(
            sampling_evidence,
            phase="precision_chunk",
            fps=fps,
            duration_sec=chunk_duration,
            model=model,
            file_obj=file_obj,
            active_obj=active_obj,
            part_index=part_index,
            record_request=False,
        )
        _persist_sampling_evidence(audit_dir, audit_files, sampling_evidence)

        for intent in missing_intents:
            chunk_prompt = intent_prompts[intent]
            await _call_progress(on_progress, "analyzing_chunk", {
                "part_index": part_index,
                "chunk_count": total,
                "file_id": file_id,
                "model": model,
                "intent": intent,
                "concurrency": chunk_concurrency,
                "has_previous_response": False,
            })
            call = await _retry_response_call(
                lambda: _stream_responses(
                    responses_client,
                    model=model,
                    file_id=file_id,
                    prompt=chunk_prompt,
                    on_progress=on_progress,
                    timeout_sec=response_timeout_sec,
                ),
                label="chunk_analysis",
                progress_stage="chunk_retrying",
                on_progress=on_progress,
                context={
                    "part_index": part_index,
                    "chunk_count": total,
                    "file_id": file_id,
                    "model": model,
                    "intent": intent,
                },
            )
            if not call.text.strip():
                raise APIError(f"Responses API 未返回可写入的分片分析文本: {intent} part={part_index}")
            _record_response_evidence(
                sampling_evidence,
                phase="precision_chunk",
                model=model,
                usage=call.usage,
                text_length=len(call.text),
                part_index=part_index,
                intent=intent,
            )
            _persist_sampling_evidence(audit_dir, audit_files, sampling_evidence)
            text_piece = (
                f"## 分片 {part_index}/{total} "
                f"({float(plan_item['start_sec']):.1f}s - {float(plan_item['end_sec']):.1f}s)\n\n"
                f"{call.text.strip()}"
            )
            output_artifact = _write_audit_text(
                audit_dir,
                _chunk_output_rel_path(intent, part_index),
                text_piece,
            )
            _add_artifact(
                audit_files,
                f"lite.{intent}.chunk.{part_index}.output",
                output_artifact,
            )
            _add_artifact(
                audit_files,
                f"lite.{intent}.chunk.{part_index}.meta",
                _write_audit_json(
                    audit_dir,
                    _chunk_meta_rel_path(intent, part_index),
                    {
                        "part_index": part_index,
                        "intent": intent,
                        "model": model,
                        "file_id": file_id,
                        "fps": fps,
                        "prompt_hash": _prompt_hash(chunk_prompt),
                        "text_length": len(call.text),
                        "usage": call.usage,
                    },
                ),
            )
            chunk_meta = {
                **base_chunk_meta,
                "file_id": file_id,
                "audit_artifact": output_artifact,
                "usage": call.usage,
                "reused_from_artifact": False,
            }
            async with lock:
                per_intent_usages[intent].append(call.usage)
                per_intent_texts[intent].append((part_index, text_piece))
                per_intent_chunks[intent].append(chunk_meta)
            await _call_progress(on_progress, "chunk_done", {
                "part_index": part_index,
                "chunk_count": total,
                "intent": intent,
                "text_length": len(call.text),
                "artifact": output_artifact,
            })

    async def process_chunk_guarded(chunk_path: Path, plan_item: dict[str, float | int]) -> None:
        async with semaphore:
            await process_chunk(chunk_path, plan_item)

    await asyncio.gather(*(
        process_chunk_guarded(chunk_path, plan_item)
        for chunk_path, plan_item in zip(chunk_paths, chunk_plan)
    ))

    results: dict[str, AnalyzeResult] = {}
    for intent in prompts:
        prompt_hash = _prompt_hash(prompts[intent])
        memory = load_response_memory(
            media_type="douyin_video",
            source_id=source_id,
            ingest_intent=intent,
            model=model,
            prompt_hash=prompt_hash,
            flow_version="chunked-v1",
            chunked=True,
        )
        previous_memory_response[intent] = memory.get("response_id") if memory else None
        ordered_texts = [
            text
            for _, text in sorted(per_intent_texts[intent], key=lambda item: item[0])
        ]
        ordered_chunks = sorted(
            per_intent_chunks[intent],
            key=lambda item: int(item.get("part_index", 0)),
        )
        representative_file_id = str(
            (ordered_chunks[0].get("file_id") if ordered_chunks else "") or "chunked"
        )
        body = "\n\n".join(ordered_texts).strip()
        synth_prompt = (
            "下面是同一个长视频按时间切片得到的多段拆解结果。"
            "请合并成一份可直接写入 Obsidian 的最终资产正文：去重重叠片段，"
            "保留时间顺序、关键细节、结论和不确定点，不要提到内部 file_id 或 response_id。\n\n"
            f"全片低 fps 概览与策略：\n{overview_context or '无'}\n\n"
            f"原始分析指令：\n{prompts[intent]}\n\n"
            f"分片结果：\n{body}"
        )
        _add_artifact(
            audit_files,
            f"lite.{intent}.synthesis_prompt",
            _write_audit_text(
                audit_dir,
                f"04-synthesis/{intent}-synthesis-prompt.md",
                synth_prompt,
            ),
        )
        await _call_progress(on_progress, "synthesizing_chunks", {
            "chunk_count": total,
            "intent": intent,
        })
        synth = await _retry_response_call(
            lambda: _call_text_responses(
                responses_client,
                model=model,
                prompt=synth_prompt,
                on_progress=on_progress,
                previous_response_id=previous_memory_response.get(intent),
                timeout_sec=response_timeout_sec,
            ),
            label="chunk_synthesis",
            progress_stage="chunk_synthesis_retrying",
            on_progress=on_progress,
            context={
                "source_id": source_id,
                "chunk_count": total,
                "model": model,
                "intent": intent,
            },
        )
        final_response_id = synth.response_id or previous_memory_response.get(intent)
        main_usage = _combine_usage(per_intent_usages[intent] + [synth.usage])
        usage_by_model: dict[str, dict[str, Any]] = {}
        strategy_usage_by_model = (strategy or {}).get("usage_by_model", {})
        if isinstance(strategy_usage_by_model, dict):
            for usage_model, model_usage in strategy_usage_by_model.items():
                if isinstance(model_usage, dict):
                    usage_by_model[str(usage_model)] = _combine_usage([model_usage])
        usage_by_model[model] = _combine_usage([
            usage_by_model.get(model, {}),
            main_usage,
        ])
        final_usage = _combine_usage(list(usage_by_model.values()))
        final_usage["usage_by_model"] = usage_by_model
        _record_response_evidence(
            sampling_evidence,
            phase="chunk_synthesis",
            model=model,
            usage=synth.usage,
            text_length=len(synth.text),
            intent=intent,
        )
        _persist_sampling_evidence(audit_dir, audit_files, sampling_evidence)
        _add_artifact(
            audit_files,
            f"lite.{intent}.synthesis_output",
            _write_audit_text(
                audit_dir,
                f"04-synthesis/{intent}-synthesis-output.md",
                synth.text.strip() or body,
            ),
        )
        save_response_memory(
            media_type="douyin_video",
            source_id=source_id,
            ingest_intent=intent,
            model=model,
            prompt_hash=prompt_hash,
            flow_version="chunked-v1",
            response_id=synth.response_id,
            file_id=representative_file_id,
            chunked=True,
        )
        text = synth.text.strip() or body
        results[intent] = AnalyzeResult(
            text=text,
            file_id=representative_file_id,
            fps_used=max(
                float(item.get("fps", _LONG_CHUNK_FPS_MAX))
                for item in ordered_chunks
            ),
            quality=quality,
            model=model,
            duration_sec=full_duration,
            target_frames=_FRAMES_SAFE_TARGET,
            actual_frames_estimate=sum(
                int(item.get("actual_frames_estimate", 0))
                for item in ordered_chunks
            ),
            usage=final_usage,
            truncated=False,
            response_id=final_response_id,
            chunked=True,
            chunk_count=total,
            chunks=ordered_chunks,
            audit_artifacts={"dir": _audit_rel(audit_dir) if audit_dir else "", "files": audit_files},
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
    analysis_key: str = "default",
    response_timeout_sec: int = 900,
    on_progress: ProgressCb = None,
) -> ImageAnalyzeResult:
    """Analyze a Douyin image post with Ark Responses input_image."""
    key = str(analysis_key or "default").strip() or "default"
    results = await analyze_images_many(
        image_paths,
        {key: prompt},
        api_key=api_key,
        endpoint=endpoint,
        model=model,
        quality=quality,
        response_timeout_sec=response_timeout_sec,
        on_progress=on_progress,
    )
    return results[key]


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
    endpoint = _validate_ark_endpoint(endpoint)
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
        "--strategy-model", default="doubao-seed-2-0-mini-260428",
    )
    parser.add_argument(
        "--chunk-concurrency", type=int, default=_CHUNK_ANALYSIS_CONCURRENCY,
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
        Path(__file__).parent / "prompts" / "video_knowledge_ingest.md"
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
            strategy_model=args.strategy_model,
            chunk_concurrency=args.chunk_concurrency,
            quality=args.quality,
            analysis_key="knowledge_ingest",
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
