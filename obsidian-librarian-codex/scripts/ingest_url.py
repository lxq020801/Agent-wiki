#!/usr/bin/env python3
"""Agent-facing P0 entrypoint for Douyin URL ingest."""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Optional


ROOT = Path(__file__).resolve().parents[1]


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Bootstrap and ingest one Douyin URL")
    parser.add_argument("url", help="Douyin share URL or share text")
    args = parser.parse_args(argv)

    sys.path.insert(0, str(ROOT))
    from install.bootstrap import bootstrap, select_runtime_python  # type: ignore

    result = bootstrap(install_deps=True)
    for item in result.actions:
        print(f"[ingest_url] ✓ {item}")
    for item in result.warnings:
        print(f"[ingest_url] ⚠ {item}", file=sys.stderr)
    for item in result.missing_user_actions:
        print(f"[ingest_url] action needed: {item}", file=sys.stderr)

    ingest = ROOT / "deps" / "douyin" / "scripts" / "ingest.py"
    python = select_runtime_python()
    cmd = [str(python), str(ingest), "--url", args.url, "--quality", "quality"]
    return subprocess.run(cmd, cwd=ROOT / "deps" / "douyin").returncode


if __name__ == "__main__":
    raise SystemExit(main())
