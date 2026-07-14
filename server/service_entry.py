#!/usr/bin/env python3
"""Minimal background-service entrypoint used by runtime_manager."""

from __future__ import annotations

import argparse
import asyncio
import ipaddress
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


def _loopback_host(value: str) -> str:
    if value.lower() == "localhost":
        return value
    try:
        if ipaddress.ip_address(value).is_loopback:
            return value
    except ValueError:
        pass
    raise argparse.ArgumentTypeError("host must be a loopback address")


def _port(value: str) -> int:
    parsed = int(value)
    if not 1 <= parsed <= 65535:
        raise argparse.ArgumentTypeError("port must be from 1 to 65535")
    return parsed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the Agent-wiki WebSocket control plane")
    parser.add_argument("--host", required=True, type=_loopback_host)
    parser.add_argument("--port", required=True, type=_port)
    args = parser.parse_args(argv)

    from server.websocket_server import LibrarianServer

    try:
        asyncio.run(LibrarianServer(host=args.host, port=args.port).start())
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
