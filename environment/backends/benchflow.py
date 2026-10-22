"""适配已核对的 BenchFlow 0.7.8 公共运行时 API 的异步接口。"""

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
        raise ValueError("timeout 必须为有限正数")
    return float(value)


def _remote_path(value: str) -> str:
    if not isinstance(value, str) or not PurePosixPath(value).is_absolute() or "\0" in value or "\\" in value:
        raise ValueError("远程路径必须为 Linux 绝对路径")
    return value


async def _kill_cli_tree(process):
    """终止当前命令行进程及其插件子进程，避免无限等待管道。"""
    tree_error = None
    if os.name == "nt" and process.returncode is None:
        # 先终止进程树，再终止根进程：docker.exe 会启动 Compose 插件。
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
            tree_error = RuntimeError(f"taskkill 执行失败，退出码：{killer.returncode}")
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
        print(f"[Docker 构建] 正在构建 {sandbox.environment_name}", file=sys.stderr, flush=True)
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
        # 分块读取避开 readline 的长度限制；增量解码保留跨块 UTF-8 字符。
        while chunk := await process.stdout.read(4096):
            output.extend(chunk)
            if streaming:
                print(decoder.decode(chunk), end="", file=sys.stderr, flush=True)
        if streaming:
            print(decoder.decode(b"", final=True), end="", file=sys.stderr, flush=True)
        await process.wait()

    reader = asyncio.create_task(collect_output())
    try:
        # 使用 shield 保留部分输出，超时清理负责终止进程树。
        await asyncio.wait_for(asyncio.shield(reader), timeout_sec)
    except BaseException as exc:
        try:
            await _kill_cli_tree(process)
            await asyncio.wait_for(asyncio.shield(reader), 5)
        except BaseException as cleanup_exc:
            exc.add_note(f"Docker 命令行进程清理也失败：{cleanup_exc}")
        finally:
            reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)
            # asyncio.Process 没有公开的 close 方法，仍需关闭继承的管道，
            # 即使 Windows 上脱离进程树的插件仍持有句柄。
            process._transport.close()
        if isinstance(exc, TimeoutError):
            error = RuntimeError(
                f"Docker Compose 命令超时，已等待 {timeout_sec} 秒。"
                f"输出：\n{output.decode('utf-8', errors='replace')}"
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
            f"Docker Compose 命令执行失败，环境：{sandbox.environment_name}。"
            f"命令：{' '.join(full_command)}。返回码：{result.return_code}。"
            f"标准输出：{result.stdout}。标准错误：{result.stderr}。"
        )
    return result


def _make_runtime(task_dir: Path, run_id: str, output_dir: Path | None = None):
    # 延迟导入：导入本包或运行模拟测试不会启动任何组件。
    try:
        version("benchflow")
    except PackageNotFoundError as exc:
        raise EnvironmentError("请先在当前 Python 环境安装 benchflow==0.7.8") from exc
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

    # 仅挂接当前沙箱，为执行、传输和销毁保留构建流式输出及
    # 有时限的命令行进程树清理，不修改已安装的依赖包。
    runtime.inner._run_docker_compose_command = run_compose
    return runtime


class BenchFlowBackend:
    """管理新建的 BenchFlow Docker 任务沙箱，不运行智能体或评分器。

    一个实例对应一个生命周期，所有方法必须在同一个 asyncio 事件循环中运行。
    stop() 与 destroy() 一样销毁容器，不承诺持久暂停。
    镜像和主机日志会保留。
    """

    def __init__(self, task_dir: str | Path, *, force_build: bool = False,
                 startup_timeout: float = 900, ready_timeout: float = 30,
                 cleanup_timeout: float = 180, output_dir: str | Path | None = None,
                 cleanup_volumes: bool = False):
        self.task_dir = Path(task_dir).resolve()
        if not self.task_dir.is_dir():
            raise ValueError(f"任务目录不存在：{self.task_dir}")
        if not ((self.task_dir / "task.md").is_file() or (self.task_dir / "task.toml").is_file()):
            raise ValueError("需要包含原生 task.md 或旧版 task.toml 的任务包")
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
                raise EnvironmentError(f"无法启动环境，当前状态为 {self.state}；请创建新实例")
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
                    exc.add_note(f"清理也失败，请重试 destroy()：{cleanup_exc}")
                raise

    async def _probe(self, timeout: float) -> None:
        # 检查实际命令通道，不能仅判断容器是否存在。
        result = await asyncio.wait_for(
            self._runtime.exec("true", timeout_sec=math.ceil(timeout), service="main"), timeout,
        )
        if result.return_code != 0:
            raise EnvironmentError(f"沙箱命令通道尚未就绪：{result.stdout} {result.stderr}")

    def _require_running(self):
        if self.state != "running":
            raise EnvironmentError(f"环境未运行（状态={self.state})")

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
            raise ValueError("argv 必须为非空序列，不能是 shell 字符串")
        if any(not isinstance(arg, str) or "\0" in arg for arg in argv) or not argv[0]:
            raise ValueError("argv 必须包含不含 NUL 字符的字符串，且可执行程序不能为空")
        if not isinstance(service, str) or not service or service.startswith("-") or "\0" in service:
            raise ValueError("Compose 服务名称无效")
        timeout = _seconds(timeout)
        if cwd is not None:
            cwd = _remote_path(cwd)
        async with self._lock:
            self._require_running()
            try:
                return await self._execute_command(argv, cwd=cwd, timeout=timeout, service=service)
            except BaseException:
                # 受监督命令的超时通过 CommandResult 返回；异常表示
                # 无法确认执行或清理状态，此时禁止复用环境。
                self.state = "failed"
                raise

    async def _execute_command(self, argv, *, cwd, timeout, service):
        """在生命周期锁内执行；DockerEnvironment 负责添加监督机制。"""
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
                # BenchFlow 在 Windows 的 upload_dir 路径中将目标目录插入
                # shell 表达式，因此该路径必须拒绝 shell 语法。
                if any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789/_-." for c in destination):
                    raise ValueError("目录上传目标仅支持字母、数字及 /、_、-、.")
                await self._runtime.upload_dir(source, destination)
            elif source.is_file():
                await self._runtime.upload_file(source, destination)
            else:
                raise ValueError("source 必须为普通文件或目录")

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
        """智能体执行后，在当前环境运行任务原生评分器。

        BenchFlow 专用的加固、奖励解析和挂载路径集中在此处理。
        评估调度器无需访问内部运行时。
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
                raise EnvironmentError("评分器未返回奖励")
            return result.rewards

    async def _cleanup(self):
        if self._runtime is not None:
            # 公开的内部句柄绕过 Environment.stop 对 _started 的检查：
            # start() 可能在部分资源创建后、_started=True 之前失败。
            # delete=False 避免进入 BenchFlow 的 `down --rmi all --volumes` 分支。
            async def stop_runtime():
                await self._runtime.inner.stop(delete=False)
                if self.cleanup_volumes:
                    # 仅移除本实验的 Compose 卷，保留镜像。
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
        """销毁当前沙箱；此后端不提供暂停和恢复约定。"""
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
            exc.add_note(f"环境清理失败，请重试 destroy()：{cleanup_exc}")
        return False
