"""框架专用 Python 的构建来源、路径与启动检查。"""

import shlex


MIN_CONTAINER_PYTHON = (3, 8)
WORKER_PYTHON_VERSION = "3.12.12"
WORKER_RUNTIME_IMAGE = "ghcr.io/astral-sh/uv:0.9.22-bookworm-slim"
WORKER_RUNTIME_DIRECTORY = "/opt/research-runtime"
WORKER_PYTHON_EXECUTABLE = WORKER_RUNTIME_DIRECTORY + "/python3"
CONTAINER_RUNTIME_PROBE = f"""
import sys
if sys.version_info < {MIN_CONTAINER_PYTHON!r}:
    raise SystemExit("research worker requires Python >= 3.8; found " + sys.version)
import json, os, signal, subprocess, tempfile, time, traceback
from pathlib import Path
from shutil import which
if which("bash") is None:
    raise SystemExit("research tools require bash in PATH")
""".strip()


def worker_runtime_build_command() -> str:
    """仅在独立构建阶段下载 Python，不安装系统包或全局命令。"""
    install = shlex.join([
        "/usr/local/bin/uv", "python", "install", "--no-config", "--no-cache",
        "--no-bin", WORKER_PYTHON_VERSION,
    ])
    find = shlex.join([
        "/usr/local/bin/uv", "python", "find", "--no-config", "--no-cache",
        "--no-project", "--managed-python", "--no-python-downloads", WORKER_PYTHON_VERSION,
    ])
    return f"""
export UV_PYTHON_INSTALL_DIR={shlex.quote(WORKER_RUNTIME_DIRECTORY + '/python')}
{install}
interpreter=$({find})
ln -s "$interpreter" {shlex.quote(WORKER_PYTHON_EXECUTABLE)}
chmod -R a+rX {shlex.quote(WORKER_RUNTIME_DIRECTORY)}
""".strip()
