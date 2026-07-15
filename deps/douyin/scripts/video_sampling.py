"""Local video-change prescan and adaptive 2-5 FPS decisions.

The prescan decodes small grayscale frames only. It does not run OCR, infer
knowledge, or claim which frames a remote model consumed.
"""
from __future__ import annotations

import math
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Optional


FPS_MODE_AUTO = "auto"
FPS_MODE_FIXED_2 = "fixed_2"
FPS_MODE_FIXED_3 = "fixed_3"
FPS_MODE_FIXED_5 = "fixed_5"
FPS_MODES = {
    FPS_MODE_AUTO,
    FPS_MODE_FIXED_2,
    FPS_MODE_FIXED_3,
    FPS_MODE_FIXED_5,
}
POLICY_VERSION = "adaptive-video-fps-v1"

_FRAME_WIDTH = 96
_FRAME_HEIGHT = 54
_FRAME_BYTES = _FRAME_WIDTH * _FRAME_HEIGHT
_PRESCAN_FPS = 1.0
_PRESCAN_MAX_COVERAGE_SEC = 600.0
_PRESCAN_TIMEOUT_SEC = 30.0
_CHANGE_POINT_SCORE = 0.045
_CHANGE_POINT_PIXEL_RATIO = 0.12


def normalize_fps_mode(value: Any) -> str:
    mode = str(value or FPS_MODE_AUTO).strip().lower().replace("-", "_")
    aliases = {
        "2": FPS_MODE_FIXED_2,
        "3": FPS_MODE_FIXED_3,
        "5": FPS_MODE_FIXED_5,
        "fixed2": FPS_MODE_FIXED_2,
        "fixed3": FPS_MODE_FIXED_3,
        "fixed5": FPS_MODE_FIXED_5,
    }
    mode = aliases.get(mode, mode)
    if mode not in FPS_MODES:
        raise ValueError(
            f"unsupported video_fps_mode {value!r}; expected auto, fixed_2, fixed_3, or fixed_5"
        )
    return mode


def fixed_fps_for_mode(mode: str) -> Optional[float]:
    normalized = normalize_fps_mode(mode)
    return {
        FPS_MODE_FIXED_2: 2.0,
        FPS_MODE_FIXED_3: 3.0,
        FPS_MODE_FIXED_5: 5.0,
    }.get(normalized)


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(percentile * len(ordered)) - 1))
    return float(ordered[index])


def _frame_change(previous: bytes, current: bytes) -> tuple[float, float]:
    total = 0
    changed = 0
    for before, after in zip(previous, current):
        delta = abs(after - before)
        total += delta
        if delta >= 20:
            changed += 1
    return total / (len(current) * 255.0), changed / len(current)


def _write_pgm(path: Path, frame: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(f"P5\n{_FRAME_WIDTH} {_FRAME_HEIGHT}\n255\n".encode("ascii") + frame)


def _failed_prescan(
    *,
    duration_sec: float,
    elapsed_sec: float,
    reason: str,
    coverage_sec: float = 0.0,
) -> dict[str, Any]:
    return {
        "ok": False,
        "purpose": "local_visual_change_measurement_only",
        "sample_fps": _PRESCAN_FPS,
        "sample_count": 0,
        "elapsed_sec": round(max(0.0, elapsed_sec), 4),
        "requested_duration_sec": round(duration_sec, 3),
        "coverage_sec": round(max(0.0, coverage_sec), 3),
        "coverage_ratio": 0.0,
        "timestamps_sec": [],
        "thumbnail_manifest": [],
        "change_points": [],
        "mean_change_score": 0.0,
        "p90_change_score": 0.0,
        "peak_change_score": 0.0,
        "change_point_ratio": 0.0,
        "visual_risk_proxies": {
            "basis": "visual_change_only_not_ocr_or_content_recognition",
            "presentation_risk": 0.0,
            "ocr_risk": 0.0,
            "action_risk": 0.0,
            "motion_risk": 0.0,
        },
        "failure_reason": str(reason)[:1000],
    }


def prescan_video(
    video_path: Path,
    duration_sec: float,
    *,
    ffmpeg_path: str = "ffmpeg",
    thumbnail_dir: Optional[Path] = None,
    runner: Callable[..., Any] = subprocess.run,
    timeout_sec: float = _PRESCAN_TIMEOUT_SEC,
) -> dict[str, Any]:
    """Decode up to ten minutes at 1 FPS and measure grayscale frame changes."""
    started = time.perf_counter()
    duration = float(duration_sec)
    if duration <= 0:
        return _failed_prescan(
            duration_sec=duration,
            elapsed_sec=time.perf_counter() - started,
            reason="duration must be positive",
        )
    coverage_sec = min(duration, _PRESCAN_MAX_COVERAGE_SEC)
    command = [
        ffmpeg_path,
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(Path(video_path)),
        "-t",
        f"{coverage_sec:.3f}",
        "-map",
        "0:v:0",
        "-vf",
        (
            f"fps={_PRESCAN_FPS:g},"
            f"scale={_FRAME_WIDTH}:{_FRAME_HEIGHT}:force_original_aspect_ratio=decrease,"
            f"pad={_FRAME_WIDTH}:{_FRAME_HEIGHT}:(ow-iw)/2:(oh-ih)/2:color=black,format=gray"
        ),
        "-pix_fmt",
        "gray",
        "-f",
        "rawvideo",
        "pipe:1",
    ]
    try:
        completed = runner(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=max(1.0, float(timeout_sec)),
        )
    except Exception as exc:
        return _failed_prescan(
            duration_sec=duration,
            elapsed_sec=time.perf_counter() - started,
            coverage_sec=coverage_sec,
            reason=f"ffmpeg prescan failed: {type(exc).__name__}",
        )
    if int(getattr(completed, "returncode", 1) or 0) != 0:
        stderr = getattr(completed, "stderr", b"") or b""
        detail = stderr.decode("utf-8", errors="replace").strip()[-600:]
        detail = detail.replace(str(Path(video_path)), "<video>")
        detail = detail.replace(Path(video_path).name, "<video>")
        return _failed_prescan(
            duration_sec=duration,
            elapsed_sec=time.perf_counter() - started,
            coverage_sec=coverage_sec,
            reason=f"ffmpeg prescan exited {completed.returncode}: {detail}",
        )

    raw = bytes(getattr(completed, "stdout", b"") or b"")
    sample_count = len(raw) // _FRAME_BYTES
    if sample_count <= 0:
        return _failed_prescan(
            duration_sec=duration,
            elapsed_sec=time.perf_counter() - started,
            coverage_sec=coverage_sec,
            reason="ffmpeg prescan returned no video frames",
        )

    frames = [
        raw[index * _FRAME_BYTES:(index + 1) * _FRAME_BYTES]
        for index in range(sample_count)
    ]
    timestamps = [round(index / _PRESCAN_FPS, 3) for index in range(sample_count)]
    thumbnail_manifest: list[dict[str, Any]] = []
    for index, (frame, timestamp) in enumerate(zip(frames, timestamps), start=1):
        name = f"frame-{index:04d}-{int(timestamp * 1000):09d}ms.pgm"
        if thumbnail_dir is not None:
            _write_pgm(Path(thumbnail_dir) / name, frame)
        thumbnail_manifest.append({
            "local_frame_index": index,
            "timestamp_sec": timestamp,
            "thumbnail": name if thumbnail_dir is not None else None,
            "fact_source": "local_prescan",
        })

    scores: list[float] = []
    ratios: list[float] = []
    change_points: list[dict[str, Any]] = []
    for index in range(1, len(frames)):
        score, ratio = _frame_change(frames[index - 1], frames[index])
        scores.append(score)
        ratios.append(ratio)
        if score >= _CHANGE_POINT_SCORE or ratio >= _CHANGE_POINT_PIXEL_RATIO:
            change_points.append({
                "local_frame_index": index + 1,
                "timestamp_sec": timestamps[index],
                "change_score": round(score, 6),
                "changed_pixel_ratio": round(ratio, 6),
                "fact_source": "local_prescan",
            })

    elapsed = time.perf_counter() - started
    comparisons = max(1, len(scores))
    mean_change = sum(scores) / comparisons
    p90_change = _percentile(scores, 0.90)
    peak_change = max(scores, default=0.0)
    mean_changed_ratio = sum(ratios) / comparisons
    change_point_ratio = len(change_points) / comparisons
    sparse_change_ratio = sum(
        1
        for item in change_points
        if 0.01 <= float(item["changed_pixel_ratio"]) <= 0.35
    ) / comparisons
    visual_risk_proxies = {
        "basis": "visual_change_only_not_ocr_or_content_recognition",
        "presentation_risk": round(min(5.0, change_point_ratio * 12 + p90_change * 8), 3),
        "ocr_risk": round(min(5.0, sparse_change_ratio * 14), 3),
        "action_risk": round(min(5.0, p90_change * 22 + change_point_ratio * 8), 3),
        "motion_risk": round(min(5.0, peak_change * 9 + mean_changed_ratio * 5), 3),
    }
    return {
        "ok": True,
        "purpose": "local_visual_change_measurement_only",
        "sample_fps": _PRESCAN_FPS,
        "sample_count": sample_count,
        "elapsed_sec": round(elapsed, 4),
        "requested_duration_sec": round(duration, 3),
        "coverage_sec": round(min(coverage_sec, sample_count / _PRESCAN_FPS), 3),
        "coverage_ratio": round(min(1.0, coverage_sec / duration), 6),
        "timestamps_sec": timestamps,
        "thumbnail_manifest": thumbnail_manifest,
        "change_points": change_points,
        "mean_change_score": round(mean_change, 6),
        "p90_change_score": round(p90_change, 6),
        "peak_change_score": round(peak_change, 6),
        "mean_changed_pixel_ratio": round(mean_changed_ratio, 6),
        "change_point_ratio": round(change_point_ratio, 6),
        "visual_risk_proxies": visual_risk_proxies,
        "failure_reason": "",
    }


def _risk_value(risk_hints: Optional[dict[str, Any]]) -> float:
    hints = risk_hints or {}
    values = []
    for key in ("presentation_risk", "ocr_risk", "action_risk", "motion_risk"):
        try:
            values.append(max(0.0, min(5.0, float(hints.get(key, 0) or 0))))
        except (TypeError, ValueError):
            continue
    return max(values, default=0.0)


def decide_sampling_fps(
    *,
    mode: str,
    duration_sec: float,
    prescan: Optional[dict[str, Any]],
    risk_hints: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Choose an upload FPS from 2 through 5 without claiming remote frame facts."""
    normalized = normalize_fps_mode(mode)
    fixed = fixed_fps_for_mode(normalized)
    duration = max(0.0, float(duration_sec))
    if fixed is not None:
        return {
            "policy_version": POLICY_VERSION,
            "mode": normalized,
            "selected_fps": fixed,
            "fallback_applied": False,
            "fallback_reason": "",
            "decision_reasons": [f"configured fixed upload rate {fixed:g} FPS"],
            "risk_hints": risk_hints or {},
            "duration_sec": round(duration, 3),
        }
    if not prescan or not bool(prescan.get("ok")):
        reason = str((prescan or {}).get("failure_reason") or "local prescan unavailable")
        return {
            "policy_version": POLICY_VERSION,
            "mode": normalized,
            "selected_fps": 5.0,
            "fallback_applied": True,
            "fallback_reason": reason[:1000],
            "decision_reasons": ["prescan failed; conservatively use 5 FPS"],
            "risk_hints": risk_hints or {},
            "duration_sec": round(duration, 3),
        }

    mean_change = float(prescan.get("mean_change_score") or 0.0)
    p90_change = float(prescan.get("p90_change_score") or 0.0)
    point_ratio = float(prescan.get("change_point_ratio") or 0.0)
    peak_change = float(prescan.get("peak_change_score") or 0.0)
    effective_risk_hints = risk_hints or (
        prescan.get("visual_risk_proxies")
        if isinstance(prescan.get("visual_risk_proxies"), dict)
        else {}
    )
    risk = _risk_value(effective_risk_hints)
    reasons: list[str] = []

    if mean_change < 0.012 and p90_change < 0.035 and point_ratio < 0.08:
        fps = 2.0
        reasons.append("low visual-change rate is consistent with static visual material")
    elif mean_change >= 0.075 or p90_change >= 0.16 or point_ratio >= 0.30:
        fps = 5.0
        reasons.append("frequent or large visual changes require dense sampling")
    elif mean_change >= 0.035 or p90_change >= 0.09 or point_ratio >= 0.16:
        fps = 4.0
        reasons.append("moderate-to-high visual changes require denser local coverage")
    else:
        fps = 3.0
        reasons.append("moderate visual changes use the balanced adaptive level")

    if risk >= 4.5 and fps < 5:
        fps = 5.0
        reasons.append("high presentation/OCR/action/motion risk raises sampling to 5 FPS")
    elif risk >= 3.5 and fps < 4:
        fps = 4.0
        reasons.append("presentation/OCR/action/motion risk raises sampling to 4 FPS")
    elif risk >= 2.5 and fps < 3:
        fps = 3.0
        reasons.append("non-trivial visual-detail risk raises sampling to 3 FPS")

    coverage_ratio = float(prescan.get("coverage_ratio") or 0.0)
    if duration > _PRESCAN_MAX_COVERAGE_SEC and coverage_ratio < 0.99:
        reasons.append("long-video prescan is coverage-limited; cloud chunk strategy remains authoritative")
    if duration >= 1800 and fps < 3:
        fps = 3.0
        reasons.append("ultra-long duration raises the local baseline to 3 FPS")

    return {
        "policy_version": POLICY_VERSION,
        "mode": normalized,
        "selected_fps": max(2.0, min(5.0, fps)),
        "fallback_applied": False,
        "fallback_reason": "",
        "decision_reasons": reasons,
        "risk_hints": effective_risk_hints,
        "duration_sec": round(duration, 3),
        "metrics": {
            "mean_change_score": mean_change,
            "p90_change_score": p90_change,
            "peak_change_score": peak_change,
            "change_point_ratio": point_ratio,
            "coverage_ratio": coverage_ratio,
        },
    }


def prescan_window(
    prescan: Optional[dict[str, Any]],
    *,
    start_sec: float,
    end_sec: float,
) -> Optional[dict[str, Any]]:
    """Return change metrics for a covered time window without inventing gaps."""
    if not prescan or not prescan.get("ok"):
        return prescan
    timestamps = list(prescan.get("timestamps_sec") or [])
    covered = [float(value) for value in timestamps if start_sec <= float(value) <= end_sec]
    if not covered:
        return None
    points = [
        item for item in (prescan.get("change_points") or [])
        if start_sec <= float(item.get("timestamp_sec") or -1) <= end_sec
    ]
    window_duration = max(1.0, end_sec - start_sec)
    scores = [float(item.get("change_score") or 0.0) for item in points]
    comparisons = max(1, len(covered) - 1)
    return {
        "ok": True,
        "mean_change_score": sum(scores) / comparisons,
        "p90_change_score": _percentile(scores, 0.90),
        "peak_change_score": max(scores, default=0.0),
        "change_point_ratio": len(points) / comparisons,
        "coverage_ratio": min(1.0, len(covered) / window_duration),
    }


def merge_chunk_sampling_strategy(
    strategy: dict[str, Any],
    chunk_plan: list[dict[str, float | int]],
    *,
    mode: str,
    prescan: Optional[dict[str, Any]],
) -> dict[str, Any]:
    """Merge local change evidence with the existing semantic chunk strategy."""
    normalized = normalize_fps_mode(mode)
    chunks = strategy.get("chunks") if isinstance(strategy.get("chunks"), list) else []
    by_part = {
        int(item.get("part_index")): item
        for item in chunks
        if isinstance(item, dict) and item.get("part_index") is not None
    }
    merged: list[dict[str, Any]] = []
    for plan_item in chunk_plan:
        part_index = int(plan_item["part_index"])
        original = dict(by_part.get(part_index) or plan_item)
        semantic_fps = max(2.0, min(5.0, float(original.get("recommended_fps") or 5.0)))
        start = float(plan_item["start_sec"])
        end = float(plan_item["end_sec"])
        scores = original.get("scores") if isinstance(original.get("scores"), dict) else {}
        risk_hints = {
            "presentation_risk": scores.get("visual_change", 0),
            "ocr_risk": scores.get("ocr_subtitle_density", 0),
            "action_risk": scores.get("operation_density", 0),
            "motion_risk": scores.get("motion_detail", 0),
        }
        window = prescan_window(prescan, start_sec=start, end_sec=end)
        if normalized == FPS_MODE_AUTO and window is None:
            local = None
            selected = semantic_fps
            reason = "local prescan did not cover this chunk; retained semantic overview strategy"
        else:
            local = decide_sampling_fps(
                mode=normalized,
                duration_sec=end - start,
                prescan=window,
                risk_hints=risk_hints,
            )
            local_fps = float(local["selected_fps"])
            selected = local_fps if fixed_fps_for_mode(normalized) is not None else max(semantic_fps, local_fps)
            reason = "; ".join(local.get("decision_reasons") or [])
        original.update({
            **plan_item,
            "recommended_fps": max(2.0, min(5.0, selected)),
            "semantic_strategy_fps": semantic_fps,
            "local_sampling_decision": local,
            "sampling_merge_reason": reason,
        })
        merged.append(original)
    return {**strategy, "chunks": merged, "sampling_mode": normalized}
