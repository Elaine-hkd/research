"""基于注册中心的执行器，生命周期绑定到所属环境。"""

import asyncio
import time

from .base import ExecutionResult, checked_env, checked_timeout, request_payload
from .registry import RegistryClient


class BoundExecutor:
    provider: str

    def __init__(self, environment, *, registry_url=None, allowed_skills=None,
                 registry_timeout=30, default_timeout=300, python_executable,
                 bash_executable="bash", workdir=None, env=None):
        self.environment = environment
        self.registry = (RegistryClient(registry_url, timeout=registry_timeout,
                                        allowed_skills=allowed_skills)
                         if registry_url is not None else None)
        self._timeout = checked_timeout(default_timeout)
        self._python = python_executable
        self._bash = bash_executable
        self._workdir = workdir
        self._env = checked_env(env)

    @property
    def python_executable(self) -> str:
        """任务配置的技能脚本解释器，不用于框架 worker 或文件工具。"""
        return self._python

    async def execute_resource(self, skill_name, relative_path, arguments=(), timeout=None,
                               *, total_timeout=None):
        """可选地为下载和脚本执行设置共享时间预算。

        传输和进程清理保留各自有时限的宽限期；
        外层计时器不得在脚本截止时间到达时取消清理。
        省略 total_timeout 时，保留独立脚本的超时约定。
        """
        self.environment._require_running()
        if self.registry is None:
            raise ValueError("执行技能前，请在环境上配置 registry_url")
        payload = request_payload(
            skill_name, relative_path, arguments,
            self._timeout if timeout is None else timeout, self._workdir,
            self._python, self._bash, self._env,
        )
        started = time.monotonic()
        budget = None if total_timeout is None else checked_timeout(total_timeout)
        download_timer = asyncio.timeout(budget)
        try:
            async with download_timer:
                payload["files"] = await asyncio.to_thread(
                    self.registry.fetch_files, skill_name, payload["relative_path"],
                )
        except TimeoutError:
            if not download_timer.expired():
                raise
            # 仅启动了注册中心只读请求，之后不会有工作进程启动。
            return ExecutionResult(
                provider=self.provider, exit_code=None, stdout="",
                stderr="下载文件时资源时间预算已耗尽，脚本尚未启动",
                timed_out=True, duration_ms=round((time.monotonic() - started) * 1000),
            )
        if budget is not None:
            remaining = budget - (time.monotonic() - started)
            if remaining <= 0:
                return ExecutionResult(
                    provider=self.provider, exit_code=None, stdout="",
                    stderr="执行前资源时间预算已耗尽，脚本尚未启动",
                    timed_out=True, duration_ms=round((time.monotonic() - started) * 1000),
                )
            payload["timeout"] = min(payload["timeout"], remaining)
        # 所属环境会在生命周期锁内再次检查就绪状态。
        result = await self.environment._run_worker(payload)
        return ExecutionResult(provider=self.provider, **result)
