"""Registry-backed executors bound to the lifetime of their owning environment."""

import asyncio

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

    async def execute_resource(self, skill_name, relative_path, arguments=(), timeout=None):
        self.environment._require_running()
        if self.registry is None:
            raise ValueError("Configure registry_url on the environment before executing skills")
        payload = request_payload(
            skill_name, relative_path, arguments,
            self._timeout if timeout is None else timeout, self._workdir,
            self._python, self._bash, self._env,
        )
        payload["files"] = await asyncio.to_thread(
            self.registry.fetch_files, skill_name, payload["relative_path"],
        )
        # The owning environment rechecks readiness under its lifecycle lock.
        result = await self.environment._run_worker(payload)
        return ExecutionResult(provider=self.provider, **result)
