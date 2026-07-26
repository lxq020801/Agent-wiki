"""Task-level fixed video sampling policy."""
from __future__ import annotations

from typing import Any


ALLOWED_VIDEO_FPS = (1.0, 2.0, 5.0)
DEFAULT_VIDEO_FPS = 1.0
POLICY_VERSION = "task-fixed-video-fps-v1"


def normalize_video_fps(value: Any) -> float:
    """Return an allowed task FPS, defaulting only when the value is absent."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return DEFAULT_VIDEO_FPS
    if isinstance(value, bool):
        raise ValueError("video FPS must be one of 1, 2, or 5")
    try:
        fps = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("video FPS must be one of 1, 2, or 5") from exc
    if fps not in ALLOWED_VIDEO_FPS:
        raise ValueError("video FPS must be one of 1, 2, or 5")
    return fps


def sampling_decision(duration_sec: float, video_fps: Any = DEFAULT_VIDEO_FPS) -> dict[str, Any]:
    """Return the explicit fixed-rate decision recorded in task diagnostics."""
    fps = normalize_video_fps(video_fps)
    return {
        "policy_version": POLICY_VERSION,
        "mode": f"fixed_{fps:g}",
        "selected_fps": fps,
        "fallback_applied": False,
        "fallback_reason": "",
        "decision_reasons": [f"task-selected fixed upload rate of {fps:g} FPS"],
        "duration_sec": round(max(0.0, float(duration_sec)), 3),
    }
