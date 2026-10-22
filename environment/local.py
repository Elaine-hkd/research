"""A local execution session; never shuts down or deletes the host machine."""

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
    # A cancelled to_thread does not stop its subprocess. Keep the session lock
    # until the worker finishes (bounded by its process timeout).
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
        self.executor = LocalExecutor(
            self, registry_url=registry_url, allowed_skills=allowed_skills,
            registry_timeout=registry_timeout, default_timeout=default_timeout,
            python_executable=python_executable or sys.executable,
            bash_executable=bash_executable, workdir=str(self.workdir), env=self._env,
        )

    def _require_running(self):
        if self.state != "running":
            raise EnvironmentError(f"Local environment is not running: {self.state}")

    async def start(self):
        async with self._lock:
            if self.state == "running":
                return
            if self.state != "new":
                raise EnvironmentError("Create a new LocalEnvironment after destruction")
            if not self.workdir.is_dir():
                raise FileNotFoundError(f"Working directory does not exist: {self.workdir}")
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
                raise ExecutionError(f"Local skill execution failed: {exc}") from exc

    async def execute(self, argv, *, cwd=None, timeout=60, service="main"):
        if service != "main":
            raise ValueError("LocalEnvironment has only the main service")
        if (isinstance(argv, (str, bytes)) or not isinstance(argv, Sequence) or not argv
                or any(not isinstance(a, str) or "\0" in a for a in argv) or not argv[0]):
            raise ValueError("argv must be a non-empty sequence of strings")
        if os.name == "nt" and Path(argv[0]).suffix.lower() in {".bat", ".cmd"}:
            raise ValueError("Windows batch files are unsupported; use Python instead")
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
                    raise ValueError("Directory copy source and destination overlap")
                shutil.copytree(source, destination, dirs_exist_ok=True)
            else:
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)

    async def download(self, source, destination, *, directory=False):
        if Path(source).is_dir() != directory:
            raise ValueError("directory flag must match the source type")
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
