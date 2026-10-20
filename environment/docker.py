"""Docker environment with an owned skill executor and a BenchFlow backend."""

import json
import math
from pathlib import Path
import shlex
import tempfile
from uuid import uuid4

from .backends.benchflow import BenchFlowBackend, _remote_path
from .executor import DockerExecutor, ExecutionError
from .executor.base import checked_timeout


class DockerEnvironment(BenchFlowBackend):
    def __init__(self, task_dir, *, registry_url=None, workdir="/root",
                 python_executable="python3", bash_executable="bash", env=None,
                 allowed_skills=None, registry_timeout=30, default_timeout=300,
                 transport_grace=30, force_build=False, startup_timeout=900,
                 ready_timeout=30, cleanup_timeout=180, output_dir=None,
                 cleanup_volumes=False):
        super().__init__(task_dir, force_build=force_build, startup_timeout=startup_timeout,
                         ready_timeout=ready_timeout, cleanup_timeout=cleanup_timeout,
                         output_dir=output_dir, cleanup_volumes=cleanup_volumes)
        self.workdir = _remote_path(workdir) if workdir is not None else None
        self._grace = checked_timeout(transport_grace)
        self._worker_path = Path(__file__).parent / "executor" / "_worker.py"
        self.executor = DockerExecutor(
            self, registry_url=registry_url, allowed_skills=allowed_skills,
            registry_timeout=registry_timeout, default_timeout=default_timeout,
            python_executable=python_executable, bash_executable=bash_executable,
            workdir=self.workdir, env=env,
        )

    async def _run_worker(self, payload):
        # A missing override means the image's working directory. The worker
        # resolves '.' before running the downloaded resource in its temp tree.
        if payload["workdir"] is None:
            payload = {**payload, "workdir": "."}
        # Serialize the complete transfer/execute/cleanup transaction with destroy.
        async with self._lock:
            self._require_running()
            token = uuid4().hex
            remote_worker = f"/tmp/research-{token}.py"
            remote_payload = f"/tmp/research-{token}.json"
            try:
                with tempfile.TemporaryDirectory(prefix="skill-transfer-") as directory:
                    local_payload = Path(directory) / "payload.json"
                    local_payload.write_text(json.dumps(payload, ensure_ascii=True), encoding="utf-8")
                    await self._runtime.upload_file(self._worker_path, remote_worker)
                    await self._runtime.upload_file(local_payload, remote_payload)
                raw = await self._runtime.exec(
                    shlex.join([payload["python_executable"], remote_worker, remote_payload]),
                    timeout_sec=math.ceil(payload["timeout"] + self._grace), service="main",
                )
            except BaseException:
                self.state = "failed"
                # The container is the cleanup boundary after uncertain transport.
                raise
            try:
                if raw.return_code != 0:
                    raise ExecutionError(f"Container worker failed: {raw.stdout} {raw.stderr}")
                try:
                    envelope = json.loads(raw.stdout or "")
                    if "error" in envelope:
                        raise ExecutionError(envelope["error"])
                    return envelope["result"]
                except (ValueError, TypeError, KeyError) as exc:
                    raise ExecutionError("Invalid response from container worker") from exc
            finally:
                try:
                    cleanup = await self._runtime.exec(
                        shlex.join(["rm", "-f", "--", remote_worker, remote_payload]),
                        timeout_sec=30, service="main",
                    )
                    if cleanup.return_code != 0:
                        raise ExecutionError("Could not remove transferred worker files")
                except BaseException:
                    self.state = "failed"
                    raise
