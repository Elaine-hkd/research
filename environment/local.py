"""本地执行会话，不关闭或删除宿主机。"""

import asyncio
import os
from pathlib import Path
import shutil
import sys
from collections.abc import Sequence

from .base import CommandResult, EnvironmentError
from .executor import ExecutionError, LocalExecutor
from .executor.base import checked_env, checked_timeout
from .executor._worker import run_command, run_resource


async def _finish_thread_on_cancel(function, *args):
    # 取消 to_thread 不会停止其子进程，需持续持有会话锁，
    # 直到工作进程结束（受进程超时限制）。
    job = asyncio.create_task(asyncio.to_thread(function, *args))
    try:
        return await asyncio.shield(job)
    except asyncio.CancelledError:
        while not job.done():
            try:
                await asyncio.shield(job)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if job.done() and not job.cancelled():
            job.exception()
        raise


class LocalEnvironment:
    def __init__(self, *, registry_url=None, workdir=None, python_executable=None,
                 bash_executable="bash", env=None, allowed_skills=None,
                 registry_timeout=30, default_timeout=300):
        self.workdir = Path(workdir or Path.cwd()).resolve()
        self.state = "new"
        self._lock = asyncio.Lock()
        self._env = checked_env(env)
        # 本地监督代码直接运行在宿主 Python 中，不改变技能配置的解释器。
        self.worker_python_executable = sys.executable
        self.executor = LocalExecutor(
            self, registry_url=registry_url, allowed_skills=allowed_skills,
            registry_timeout=registry_timeout, default_timeout=default_timeout,
            python_executable=python_executable or sys.executable,
            bash_executable=bash_executable, workdir=str(self.workdir), env=self._env,
        )

    def _require_running(self):
        if self.state != "running":
            raise EnvironmentError(f"本地环境未运行：{self.state}")

    async def start(self):
        async with self._lock:
            if self.state == "running":
                return
            if self.state != "new":
                raise EnvironmentError("销毁后请创建新的 LocalEnvironment")
            if not self.workdir.is_dir():
                raise FileNotFoundError(f"工作目录不存在：{self.workdir}")
            self.state = "running"

    async def wait_ready(self, timeout=30):
        checked_timeout(timeout)
        async with self._lock:
            self._require_running()
            if not self.workdir.is_dir():
                raise FileNotFoundError(self.workdir)

    async def _run_worker(self, payload):
        async with self._lock:
            self._require_running()
            try:
                return await _finish_thread_on_cancel(run_resource, payload)
            except (OSError, ValueError, RuntimeError) as exc:
                raise ExecutionError(f"本地技能执行失败：{exc}") from exc

    async def execute(self, argv, *, cwd=None, timeout=60, service="main"):
        if service != "main":
            raise ValueError("LocalEnvironment 仅提供 main 服务")
        if (isinstance(argv, (str, bytes)) or not isinstance(argv, Sequence) or not argv
                or any(not isinstance(a, str) or "\0" in a for a in argv) or not argv[0]):
            raise ValueError("argv 必须为非空字符串序列")
        if os.name == "nt" and Path(argv[0]).suffix.lower() in {".bat", ".cmd"}:
            raise ValueError("不支持 Windows 批处理文件，请使用 Python")
        timeout = checked_timeout(timeout)
        directory = Path(cwd).resolve() if cwd is not None else self.workdir
        values = {**os.environ, **self._env, "PYTHONIOENCODING": "utf-8"}
        async with self._lock:
            self._require_running()
            result = await _finish_thread_on_cancel(run_command, list(argv), directory, values, timeout)
            return CommandResult(result["exit_code"], result["stdout"], result["stderr"],
                                 timed_out=result["timed_out"])

    async def upload(self, source, destination):
        async with self._lock:
            self._require_running()
            source = Path(source).resolve(strict=True)
            destination = Path(destination).resolve()
            if source == destination:
                return
            if source.is_dir():
                if source in destination.parents or destination in source.parents:
                    raise ValueError("目录复制的源和目标重叠")
                shutil.copytree(source, destination, dirs_exist_ok=True)
            else:
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)

    async def download(self, source, destination, *, directory=False):
        if Path(source).is_dir() != directory:
            raise ValueError("directory 标志必须与源类型一致")
        await self.upload(source, destination)

    async def destroy(self):
        async with self._lock:
            self.state = "destroyed"

    async def stop(self):
        await self.destroy()

    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, *exc):
        await self.destroy()
