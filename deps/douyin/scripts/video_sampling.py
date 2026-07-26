"""System-wide video sampling policy.

Agent-wiki uses one fixed upload rate. This module keeps the value and its
audit description in one place so legacy config cannot silently change it.
"""
from __future__ import annotations

from typing import Any


SYSTEM_VIDEO_FPS = 5.0
SYSTEM_FPS_MODE = "fixed_5"
POLICY_VERSION = "system-fixed-video-fps-v1"


def system_sampling_decision(duration_sec: float) -> dict[str, Any]:
    """Return the fixed system decision recorded in task diagnostics."""
    return {
        "policy_version": POLICY_VERSION,
        "mode": SYSTEM_FPS_MODE,
        "selected_fps": SYSTEM_VIDEO_FPS,
        "fallback_applied": False,
        "fallback_reason": "",
        "decision_reasons": [f"system-wide fixed upload rate of {SYSTEM_VIDEO_FPS:g} FPS"],
        "duration_sec": round(max(0.0, float(duration_sec)), 3),
    }
