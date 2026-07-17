#!/usr/bin/env python3
"""Unified Agent-wiki installation and runtime command router."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Optional


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _help_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agent-wiki",
        description="安装、运行和诊断 Agent-wiki 本地服务",
    )
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("install", help="幂等准备隔离依赖、运行目录和扩展副本")
    commands.add_parser("start", help="启动本地服务")
    commands.add_parser("stop", help="只停止身份验证通过的本地服务")
    commands.add_parser("restart", help="重启身份验证通过的本地服务")
    commands.add_parser("status", help="查看服务状态")
    commands.add_parser("doctor", help="执行只读环境诊断")
    commands.add_parser("autostart", help="管理显式启用的 macOS 开机启动")
    commands.add_parser("uninstall", help="移除运行接线并保留全部用户数据")
    return parser


def _runtime_root() -> Path:
    return Path(os.environ.get("AGENT_WIKI_HOME", "~/.agent-wiki")).expanduser()


def uninstall(argv: list[str], *, project_root: Path = PROJECT_ROOT) -> int:
    parser = argparse.ArgumentParser(prog="agent-wiki uninstall", description="安全移除 Agent-wiki 运行接线")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    args = parser.parse_args(argv)

    from install.autostart import AutostartManager
    from server.runtime_manager import ServiceController, redact_diagnostic_value

    runtime = _runtime_root()
    controller = ServiceController(project_root, runtime)
    stop_result = controller.stop()
    autostart_result = AutostartManager(project_root, runtime).disable()
    code = 0 if stop_result.code == 0 and autostart_result.code == 0 else 2
    preserved = [
        str(runtime),
        "知识库目录（位置由 config.toml 指向）",
        "配置、凭据引用、日志、缓存、任务和扩展副本",
    ]
    payload: dict[str, Any] = {
        "ok": code == 0,
        "service": {"code": stop_result.code, "messages": list(stop_result.lines)},
        "autostart": dict(autostart_result.payload),
        "preserved": preserved,
        "purge_performed": False,
    }
    if args.json:
        print(json.dumps(redact_diagnostic_value(payload), ensure_ascii=False, indent=2))
    else:
        for line in stop_result.lines:
            print(redact_diagnostic_value(f"服务：{line}"))
        for line in autostart_result.lines:
            print(redact_diagnostic_value(line))
        print("已保留以下内容，不会静默清理：")
        for item in preserved:
            print(redact_diagnostic_value(f"- {item}"))
        print("如需删除这些数据，请先自行备份并人工确认目录内容。")
    return code


def main(argv: Optional[list[str]] = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments or arguments[0] in {"-h", "--help"}:
        _help_parser().print_help()
        return 0
    command, rest = arguments[0], arguments[1:]
    if command == "install":
        from install.bootstrap import main as bootstrap_main

        return bootstrap_main(rest)
    if command in {"start", "stop", "restart", "status", "doctor"}:
        from server.runtime_manager import main as runtime_main

        return runtime_main([command, *rest], project_root=PROJECT_ROOT, runtime_root=_runtime_root())
    if command == "autostart":
        from install.autostart import main as autostart_main

        return autostart_main(rest, project_root=PROJECT_ROOT, runtime_root=_runtime_root())
    if command == "uninstall":
        return uninstall(rest)
    print(f"未知命令：{command}", file=sys.stderr)
    _help_parser().print_help(sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
