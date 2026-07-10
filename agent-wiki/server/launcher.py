#!/usr/bin/env python3
"""
Agent-wiki 启动器

职责：
  1. 检查环境（venv、依赖、目录）
  2. 启动 WebSocket 服务器
  3. 保持常驻运行

使用方法：
  python3 server/launcher.py

或作为后台服务：
  nohup python3 server/launcher.py > logs/server.log 2>&1 &
"""

import os
import shutil
import sys
import subprocess
from pathlib import Path

# 项目根目录
PROJECT_ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT))


def run_bootstrap():
    """Run Scheme C bootstrap before starting the WebSocket control plane."""
    from install.bootstrap import bootstrap

    result = bootstrap(install_deps=False)
    for item in result.actions:
        print(f"[Launcher] ✓ {item}")
    for item in result.warnings:
        print(f"[Launcher] ⚠ {item}")
    for item in result.missing_user_actions:
        print(f"[Launcher] 用户动作: {item}")
    return result.ok


def _can_import(python: Path, module: str) -> bool:
    result = subprocess.run(
        [str(python), "-c", f"import {module}"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return result.returncode == 0


def select_websocket_python() -> Path:
    """Pick an interpreter that can run the control-plane server."""
    candidates: list[Path] = []
    for raw in [sys.executable, shutil.which("python3.13"), shutil.which("python3.12"), shutil.which("python3.11"), shutil.which("python3")]:
        if raw:
            path = Path(raw).resolve()
            if path not in candidates:
                candidates.append(path)

    for python in candidates:
        if _can_import(python, "websockets"):
            return python

    raise RuntimeError("No Python interpreter with the websockets package was found. Run: python3 -m pip install -r requirements.txt")


def start_server():
    """启动 WebSocket 服务器"""
    python = select_websocket_python()
    server_script = PROJECT_ROOT / "server" / "websocket_server.py"
    
    print(f"[Launcher] 启动 WebSocket 服务器: {python}")
    
    # Inherit stdout/stderr instead of piping them. A long-running server with a
    # dead stdout pipe can crash on the next log write.
    process = subprocess.Popen(
        [str(python), str(server_script)],
        cwd=str(PROJECT_ROOT),
    )
    
    print(f"[Launcher] 服务器 PID: {process.pid}")
    print(f"[Launcher] 按 Ctrl+C 停止")
    
    try:
        return process.wait() == 0
    except KeyboardInterrupt:
        print("\n[Launcher] 停止服务器...")
        process.terminate()
        process.wait()
        return True


def main():
    """主入口"""
    print("=" * 50)
    print("Agent-wiki 启动器")
    print("=" * 50)
    
    # 1. 自动初始化
    if not run_bootstrap():
        print("[Launcher] 自初始化有警告，继续启动 WebSocket 以便扩展配置可用")
        
    # 2. 启动服务器
    if not start_server():
        print("[Launcher] 服务器启动失败")
        return 1
        
    return 0


if __name__ == '__main__':
    sys.exit(main())
