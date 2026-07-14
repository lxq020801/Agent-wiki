#!/usr/bin/env python3
"""Agent-wiki control-plane launcher and runtime operations CLI."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _python_is_supported(executable: str) -> bool:
    try:
        result = subprocess.run(
            [
                executable,
                "-c",
                "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except OSError:
        return False
    return result.returncode == 0


def _reexec_with_supported_python() -> None:
    if sys.version_info >= (3, 11):
        return

    candidates = [
        PROJECT_ROOT / "deps" / "douyin" / ".venv" / "bin" / "python",
        *(Path(value) for value in [
            shutil.which("python3.13"),
            shutil.which("python3.12"),
            shutil.which("python3.11"),
        ] if value),
    ]
    for candidate in candidates:
        if candidate.exists() and _python_is_supported(str(candidate)):
            os.execv(str(candidate), [str(candidate), str(Path(__file__).resolve()), *sys.argv[1:]])

    print("[Launcher] Python 3.11+ is required. Install it before running Agent-wiki.", file=sys.stderr)
    raise SystemExit(2)


def main(argv: list[str] | None = None) -> int:
    _reexec_with_supported_python()
    sys.path.insert(0, str(PROJECT_ROOT))
    from server.runtime_manager import main as runtime_main

    return runtime_main(argv, project_root=PROJECT_ROOT)


if __name__ == "__main__":
    raise SystemExit(main())
