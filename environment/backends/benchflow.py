"""Async adapter for the inspected BenchFlow 0.7.8 public runtime API."""

import asyncio
import codecs
from importlib.metadata import PackageNotFoundError, version
import math
import os
from pathlib import Path, PurePosixPath
import shlex
import signal
import subprocess
import sys
from typing import Sequence
from uuid import uuid4

from ..base import CommandResult, EnvironmentError


def _seconds(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ValueError("timeout must be a positive finite number")
    return float(value)


def _remote_path(value: str) -> str:
    if not isinstance(value, str) or not PurePosixPath(value).is_absolute() or "\0" in value or "\\" in value:
        raise ValueError("Remote paths must be absolute Linux paths")
    return value


async def _kill_cli_tree(process):
    """Stop this CLI and its plugin children without an unbounded pipe wait."""
    tree_error = None
    if os.name == "nt" and process.returncode is None:
        # Kill the tree before its root: docker.exe launches the Compose plugin.
        killer = await asyncio.create_subprocess_exec(
            "taskkill", "/PID", str(process.pid), "/T", "/F",
            stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW,
        )
        try:
            await asyncio.wait_for(killer.wait(), 5)
        finally:
            if killer.returncode is None:
                killer.kill()
                await asyncio.wait_for(killer.wait(), 5)
        if killer.returncode != 0 and process.returncode is None:
            tree_error = RuntimeError(f"taskkill failed with exit code {killer.returncode}")
    elif os.name != "nt":
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    if process.returncode is None:
        try:
            process.kill()
        except ProcessLookupError:
            pass
    await asyncio.wait_for(process.wait(), 5)
    if tree_error is not None:
        raise tree_error


async def _run_compose_command(sandbox, command, *, check=True, timeout_sec=None):
    from benchflow.sandbox._base import ExecResult
    from benchflow.sandbox.docker import _sanitize_docker_compose_project_name

    full_command = [
        "docker", "compose",
        "--project-name", _sanitize_docker_compose_project_name(sandbox.session_id),
        "--project-directory", str(sandbox.environment_dir.resolve()),
    ]
    for path in sandbox._docker_compose_paths:
        full_command.extend(["-f", str(path.resolve())])
    streaming = bool(command and command[0] == "build")
    if streaming:
        full_command[2:2] = ["--progress", "plain"]
    full_command.extend(command)

    if streaming:
        print(f"[Docker build] Building {sandbox.environment_name}", file=sys.stderr, flush=True)
    process = await asyncio.create_subprocess_exec(
        *full_command, env=sandbox._docker_compose_env(),
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt"
           else {"start_new_session": True}),
    )
    output = bytearray()
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")

    async def collect_output():
        # Chunks avoid readline's length limit; decoding preserves split UTF-8 characters.
        while chunk := await process.stdout.read(4096):
            output.extend(chunk)
            if streaming:
                print(decoder.decode(chunk), end="", file=sys.stderr, flush=True)
        if streaming:
            print(decoder.decode(b"", final=True), end="", file=sys.stderr, flush=True)
        await process.wait()

    reader = asyncio.create_task(collect_output())
    try:
        # Shield preserves partial output while timeout cleanup stops the tree.
        await asyncio.wait_for(asyncio.shield(reader), timeout_sec)
    except BaseException as exc:
        try:
            await _kill_cli_tree(process)
            await asyncio.wait_for(asyncio.shield(reader), 5)
        except BaseException as cleanup_exc:
            exc.add_note(f"Docker CLI cleanup also failed: {cleanup_exc}")
        finally:
            reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)
            # asyncio.Process has no public close method. Close inherited pipes
            # even if an escaped plugin still owns a handle on Windows.
            process._transport.close()
        if isinstance(exc, TimeoutError):
            error = RuntimeError(
                f"Docker compose command timed out after {timeout_sec} seconds. "
                f"Output:\n{output.decode('utf-8', errors='replace')}"
            )
            for note in getattr(exc, "__notes__", ()):
                error.add_note(note)
            raise error from exc
        raise

    result = ExecResult(
        return_code=process.returncode,
        stdout=output.decode("utf-8", errors="replace"), stderr=None,
    )
    if check and result.return_code != 0:
        raise RuntimeError(
            f"Docker compose command failed for environment {sandbox.environment_name}. "
            f"Command: {' '.join(full_command)}. Return code: {result.return_code}. "
            f"Stdout: {result.stdout}. Stderr: {result.stderr}."
        )
    return result


def _make_runtime(task_dir: Path, run_id: str, output_dir: Path | None = None):
    # Lazy import: importing this package or running mock tests starts nothing.
    try:
        version("benchflow")
    except PackageNotFoundError as exc:
        raise EnvironmentError("Install benchflow==0.7.8 in this Python environment first") from exc
    from benchflow import Environment as RuntimeEnvironment
    if output_dir is None:
        runtime = RuntimeEnvironment.from_task(task_dir, sandbox="docker", rollout_name=run_id)
    else:
        from benchflow.contracts import default_rollout_planes
        from benchflow.task import RolloutPaths, Task

        paths = RolloutPaths(rollout_dir=output_dir)
        paths.mkdir()
        inner = default_rollout_planes().create_environment(
            "docker", task=Task(task_dir), task_path=task_dir,
            rollout_name=run_id, rollout_paths=paths,
            preserve_agent_network=False, environment_manifest=None,
        )
        runtime = RuntimeEnvironment(inner=inner, task_path=task_dir, sandbox="docker")

    async def run_compose(command, check=True, timeout_sec=None):
        return await _run_compose_command(
            runtime.inner, command, check=check, timeout_sec=timeout_sec,
        )

    # Hook only this sandbox: keep build streaming and bound CLI-tree cleanup
    # for exec, transfers and teardown without changing the installed package.
    runtime.inner._run_docker_compose_command = run_compose
    return runtime


class BenchFlowBackend:
    """Own a fresh BenchFlow Docker task sandbox; no agent or verifier is run.

    One instance represents one lifecycle. All methods must run on the same
    asyncio loop. stop() tears down containers just like destroy(); it does
    NOT promise persistent suspension. Images and host logs are retained.
    """

    def __init__(self, task_dir: str | Path, *, force_build: bool = False,
                 startup_timeout: float = 900, ready_timeout: float = 30,
                 cleanup_timeout: float = 180, output_dir: str | Path | None = None,
                 cleanup_volumes: bool = False):
        self.task_dir = Path(task_dir).resolve()
        if not self.task_dir.is_dir():
            raise ValueError(f"Task directory does not exist: {self.task_dir}")
        if not ((self.task_dir / "task.md").is_file() or (self.task_dir / "task.toml").is_file()):
            raise ValueError("A native task.md or legacy task.toml task package is required")
        self.run_id = "research-" + uuid4().hex
        self.force_build = force_build
        self.startup_timeout = _seconds(startup_timeout)
        self.ready_timeout = _seconds(ready_timeout)
        self.cleanup_timeout = _seconds(cleanup_timeout)
        self.output_dir = Path(output_dir).resolve() if output_dir is not None else None
        self.cleanup_volumes = cleanup_volumes
        self.state = "new"
        self._runtime = None
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        async with self._lock:
            if self.state == "running":
                return
            if self.state != "new":
                raise EnvironmentError(f"Cannot start environment in state {self.state}; create a new instance")
            self.state = "starting"
            try:
                self._runtime = _make_runtime(self.task_dir, self.run_id, self.output_dir)
                await asyncio.wait_for(self._runtime.start(force_build=self.force_build), self.startup_timeout)
                await self._probe(self.ready_timeout)
                self.state = "running"
            except BaseException as exc:
                self.state = "failed"
                try:
                    await self._cleanup()
                except BaseException as cleanup_exc:
                    exc.add_note(f"Cleanup also failed; retry destroy(): {cleanup_exc}")
                raise

    async def _probe(self, timeout: float) -> None:
        # Test the actual command channel, not just whether a container exists.
        result = await asyncio.wait_for(
            self._runtime.exec("true", timeout_sec=math.ceil(timeout), service="main"), timeout,
        )
        if result.return_code != 0:
            raise EnvironmentError(f"Sandbox command channel is not ready: {result.stdout} {result.stderr}")

    def _require_running(self):
        if self.state != "running":
            raise EnvironmentError(f"Environment is not running (state={self.state})")

    async def wait_ready(self, timeout: float = 30) -> None:
        timeout = _seconds(timeout)
        async with self._lock:
            self._require_running()
            try:
                await self._probe(timeout)
            except BaseException:
                self.state = "failed"
                raise

    async def execute(self, argv: Sequence[str], *, cwd: str | None = None,
                      timeout: float = 60, service: str = "main") -> CommandResult:
        if isinstance(argv, (str, bytes)) or not isinstance(argv, Sequence) or not argv:
            raise ValueError("argv must be a non-empty sequence, not a shell string")
        if any(not isinstance(arg, str) or "\0" in arg for arg in argv) or not argv[0]:
            raise ValueError("argv must contain NUL-free strings and a non-empty executable")
        if not isinstance(service, str) or not service or service.startswith("-") or "\0" in service:
            raise ValueError("Invalid Compose service name")
        timeout = _seconds(timeout)
        if cwd is not None:
            cwd = _remote_path(cwd)
        async with self._lock:
            self._require_running()
            try:
                return await self._execute_command(argv, cwd=cwd, timeout=timeout, service=service)
            except BaseException:
                # Supervised command timeouts return CommandResult. Exceptions
                # mean execution/cleanup is unconfirmed; disallow environment reuse.
                self.state = "failed"
                raise

    async def _execute_command(self, argv, *, cwd, timeout, service):
        """Execute under the lifecycle lock; DockerEnvironment adds supervision."""
        result = await self._runtime.exec(
            shlex.join(argv), cwd=cwd, timeout_sec=math.ceil(timeout), service=service,
        )
        return CommandResult(result.return_code, result.stdout or "", result.stderr or "")

    async def upload(self, source: str | Path, destination: str) -> None:
        source = Path(source).resolve(strict=True)
        destination = _remote_path(destination)
        async with self._lock:
            self._require_running()
            if source.is_dir():
                # BenchFlow's Windows upload_dir path interpolates destination in
                # a shell expression. Reject shell syntax on that code path.
                if any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789/_-." for c in destination):
                    raise ValueError("Directory upload destination only supports letters, digits, /, _, -, .")
                await self._runtime.upload_dir(source, destination)
            elif source.is_file():
                await self._runtime.upload_file(source, destination)
            else:
                raise ValueError("source must be a regular file or directory")

    async def download(self, source: str, destination: str | Path, *, directory: bool = False) -> None:
        source = _remote_path(source)
        destination = Path(destination).resolve()
        async with self._lock:
            self._require_running()
            if directory:
                destination.mkdir(parents=True, exist_ok=True)
                await self._runtime.download_dir(source, destination)
            else:
                destination.parent.mkdir(parents=True, exist_ok=True)
                await self._runtime.download_file(source, destination)

    async def verify(self) -> dict:
        """Run the native task verifier against this environment, after the agent.

        Keep BenchFlow-specific hardening, reward parsing and mount paths here.
        The evaluation runner does not need access to the private runtime.
        """
        from benchflow.sandbox.lockdown import harden_before_verify
        from benchflow.task.verifier import Verifier

        async with self._lock:
            self._require_running()
            task = self._runtime.task
            sandbox = self._runtime.inner
            await harden_before_verify(sandbox, task, sandbox_user=None)
            result = await Verifier(task, sandbox.rollout_paths, sandbox).verify()
            if result.rewards is None:
                raise EnvironmentError("Verifier returned no rewards")
            return result.rewards

    async def _cleanup(self):
        if self._runtime is not None:
            # The public inner handle bypasses Environment.stop's _started gate:
            # start() may fail AFTER partial provisioning but BEFORE _started=True.
            # delete=False avoids BenchFlow's `down --rmi all --volumes` path.
            async def stop_runtime():
                await self._runtime.inner.stop(delete=False)
                if self.cleanup_volumes:
                    # Remove only this experiment's Compose volumes; retain images.
                    await self._runtime.inner._run_docker_compose_command(
                        ["down", "--volumes", "--remove-orphans", "-t", "5"],
                        timeout_sec=90,
                    )

            await asyncio.wait_for(stop_runtime(), self.cleanup_timeout)
            self._runtime = None

    async def destroy(self) -> None:
        async with self._lock:
            if self.state == "destroyed":
                return
            try:
                await self._cleanup()
            except BaseException:
                self.state = "failed"
                raise
            self.state = "destroyed"

    async def stop(self) -> None:
        """Tear down this sandbox; this backend has no suspend/resume contract."""
        await self.destroy()

    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        try:
            await self.destroy()
        except BaseException as cleanup_exc:
            if exc is None:
                raise
            exc.add_note(f"Environment cleanup failed; retry destroy(): {cleanup_exc}")
        return False
