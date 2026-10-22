"""Registry-backed executors bound to the lifetime of their owning environment."""

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

    async def execute_resource(self, skill_name, relative_path, arguments=(), timeout=None,
                               *, total_timeout=None):
        """Optionally share a budget across downloads and script execution.

        Transport and process cleanup retain their own bounded grace periods;
        an outer timer must not cancel them at the script's execution deadline.
        Omitting total_timeout preserves the standalone script timeout contract.
        """
        self.environment._require_running()
        if self.registry is None:
            raise ValueError("Configure registry_url on the environment before executing skills")
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
            # Only read-only registry I/O was started; no worker can launch later.
            return ExecutionResult(
                provider=self.provider, exit_code=None, stdout="",
                stderr="Resource time budget exhausted while downloading files; script was not started",
                timed_out=True, duration_ms=round((time.monotonic() - started) * 1000),
            )
        if budget is not None:
            remaining = budget - (time.monotonic() - started)
            if remaining <= 0:
                return ExecutionResult(
                    provider=self.provider, exit_code=None, stdout="",
                    stderr="Resource time budget exhausted before execution; script was not started",
                    timed_out=True, duration_ms=round((time.monotonic() - started) * 1000),
                )
            payload["timeout"] = min(payload["timeout"], remaining)
        # The owning environment rechecks readiness under its lifecycle lock.
        result = await self.environment._run_worker(payload)
        return ExecutionResult(provider=self.provider, **result)
