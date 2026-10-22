"""Docker environment with an owned skill executor and a BenchFlow backend."""

import asyncio
import json
import math
from pathlib import Path
import shlex
import tempfile
from uuid import uuid4

from .backends.benchflow import BenchFlowBackend, _remote_path
from .base import CommandResult
from .executor import DockerExecutor, ExecutionError
from .executor.base import checked_timeout


_STOP_COMMAND = '''
import os
from pathlib import Path
import signal
import sys

control = Path(sys.argv[1])
# Keep this marker on failure: a late-starting worker must not launch a command.
control.parent.mkdir(mode=0o700, exist_ok=True)
Path(str(control) + ".cancelled").touch()
if control.exists():
    group = int(control.read_text(encoding="ascii"))
    if group <= 1:
        raise ValueError("Invalid command process group")
    try:
        os.killpg(group, signal.SIGKILL)
    except ProcessLookupError:
        pass
'''


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
        self._python_executable = python_executable
        self._worker_path = Path(__file__).parent / "executor" / "_worker.py"
        self.executor = DockerExecutor(
            self, registry_url=registry_url, allowed_skills=allowed_skills,
            registry_timeout=registry_timeout, default_timeout=default_timeout,
            python_executable=python_executable, bash_executable=bash_executable,
            workdir=self.workdir, env=env,
        )

    async def _execute_command(self, argv, *, cwd, timeout, service):
        # execute() already owns the environment lock, like resource execution.
        result = await self._run_worker_locked({
            "operation": "command", "argv": list(argv), "timeout": timeout,
            "workdir": cwd, "python_executable": self._python_executable,
        }, cwd=cwd, service=service)
        if result["timed_out"]:
            raise RuntimeError(f"Command timed out after {timeout:g} seconds")
        return CommandResult(result["exit_code"], result["stdout"], result["stderr"])

    async def _run_worker(self, payload):
        async with self._lock:
            self._require_running()
            return await self._run_worker_locked(payload)

    async def _stop_command(self, control_path, *, python_executable, service):
        cleanup = await self._runtime.exec(
            shlex.join([python_executable, "-c", _STOP_COMMAND, control_path]),
            timeout_sec=10, service=service,
        )
        if cleanup.return_code != 0:
            raise ExecutionError(f"Could not stop container command: {cleanup.stdout} {cleanup.stderr}")

    async def _run_worker_locked(self, payload, *, cwd=None, service="main"):
        # A missing override means the image's working directory. The worker
        # resolves '.' before running the downloaded resource in its temp tree.
        if payload["workdir"] is None:
            payload = {**payload, "workdir": "."}
        # The caller holds the lifecycle lock through transfer and cleanup.
        token = uuid4().hex
        remote_directory = f"/tmp/research-{token}"
        remote_worker = remote_directory + "/worker.py"
        remote_payload = remote_directory + "/payload.json"
        control_path = remote_directory + "/command.pid"
        payload = {**payload, "control_path": control_path}
        try:
            async with asyncio.timeout(self._grace):
                # Own the parent directory so a non-root image USER can remove
                # files copied by Docker (which assigns the files to root).
                prepared = await self._runtime.exec(
                    shlex.join(["mkdir", "-m", "700", "-p", "--", remote_directory]),
                    timeout_sec=math.ceil(self._grace), service=service,
                )
                if prepared.return_code != 0:
                    raise ExecutionError(f"Could not create worker directory: {prepared.stdout} {prepared.stderr}")
                with tempfile.TemporaryDirectory(prefix="skill-transfer-") as directory:
                    local_payload = Path(directory) / "payload.json"
                    local_payload.write_text(json.dumps(payload, ensure_ascii=True), encoding="utf-8")
                    # BenchFlow upload_file hard-codes main; use its same Compose
                    # channel so execute(service=...) keeps targeting that service.
                    for source, target in ((self._worker_path, remote_worker),
                                           (local_payload, remote_payload)):
                        await self._runtime.inner._run_docker_compose_command(
                            ["cp", str(source), f"{service}:{target}"],
                            timeout_sec=math.ceil(self._grace),
                        )
            raw = await self._runtime.exec(
                shlex.join([payload["python_executable"], remote_worker, remote_payload]),
                cwd=cwd, timeout_sec=math.ceil(payload["timeout"] + self._grace), service=service,
            )
        except BaseException as exc:
            self.state = "failed"
            try:
                await self._stop_command(
                    control_path, python_executable=payload["python_executable"], service=service,
                )
            except BaseException as cleanup_exc:
                exc.add_note(f"Container command cleanup also failed: {cleanup_exc}")
            # Keep the cancellation marker until destroy() removes this sandbox.
            raise
        error = None
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
        except BaseException as exc:
            error = exc
            self.state = "failed"
            try:
                await self._stop_command(
                    control_path, python_executable=payload["python_executable"], service=service,
                )
            except BaseException as cleanup_exc:
                exc.add_note(f"Container command cleanup also failed: {cleanup_exc}")
            raise
        finally:
            if error is None:
                try:
                    cleanup = await self._runtime.exec(
                        shlex.join(["rm", "-rf", "--", remote_directory]),
                        timeout_sec=30, service=service,
                    )
                    if cleanup.return_code != 0:
                        raise ExecutionError("Could not remove transferred worker files")
                except BaseException:
                    self.state = "failed"
                    raise
