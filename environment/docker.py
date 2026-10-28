"""由环境持有技能执行器、以 BenchFlow 为后端的 Docker 环境。"""

import asyncio
import json
import math
from pathlib import Path
import shlex
import tempfile
from uuid import uuid4

from .backends.benchflow import BenchFlowBackend, _remote_path
from .base import CommandResult, EnvironmentError
from .executor import DockerExecutor, ExecutionError
from .executor.base import checked_timeout
from .runtime import CONTAINER_RUNTIME_PROBE, WORKER_PYTHON_EXECUTABLE


_STOP_COMMAND = '''
import os
from pathlib import Path
import signal
import sys

control = Path(sys.argv[1])
# 失败时保留此标记，防止延迟启动的工作进程执行命令。
control.parent.mkdir(mode=0o700, exist_ok=True)
Path(str(control) + ".cancelled").touch()
if control.exists():
    group = int(control.read_text(encoding="ascii"))
    if group <= 1:
        raise ValueError("命令进程组无效")
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
                 cleanup_volumes=False, worker_python_executable=WORKER_PYTHON_EXECUTABLE):
        super().__init__(task_dir, force_build=force_build, startup_timeout=startup_timeout,
                         ready_timeout=ready_timeout, cleanup_timeout=cleanup_timeout,
                         output_dir=output_dir, cleanup_volumes=cleanup_volumes)
        self.workdir = _remote_path(workdir) if workdir is not None else None
        self._grace = checked_timeout(transport_grace)
        self.worker_python_executable = _remote_path(worker_python_executable)
        self._worker_path = Path(__file__).parent / "executor" / "_worker.py"
        self.executor = DockerExecutor(
            self, registry_url=registry_url, allowed_skills=allowed_skills,
            registry_timeout=registry_timeout, default_timeout=default_timeout,
            python_executable=python_executable, bash_executable=bash_executable,
            workdir=self.workdir, env=env,
        )

    async def _probe(self, timeout):
        # 直接使用原始通道，不依赖尚未确认可启动的 worker。
        result = await asyncio.wait_for(
            self._runtime.exec(
                shlex.join([self.worker_python_executable, "-I", "-B", "-c", CONTAINER_RUNTIME_PROBE]),
                timeout_sec=math.ceil(timeout), service="main",
            ), timeout,
        )
        if result.return_code != 0:
            output = "\n".join(part for part in (result.stdout, result.stderr) if part)
            raise EnvironmentError(
                f"框架运行依赖检查失败（解释器={self.worker_python_executable}）："
                f"需要可运行的独立 Python >= 3.8 和任务 PATH 中的 bash。{output}\n"
                "请重新生成任务副本并构建镜像；预构建或自定义镜像需提供独立运行时，"
                "也可通过 worker_python_executable 指定其绝对路径。"
            )

    async def _execute_command(self, argv, *, cwd, timeout, service):
        # 与执行资源时一样，execute() 已持有环境锁。
        result = await self._run_worker_locked({
            "operation": "command", "argv": list(argv), "timeout": timeout,
            "workdir": cwd,
        }, cwd=cwd, service=service)
        # 工作进程已回收命令进程，传输文件也已清理完成。
        # 返回超时结果不代表 Docker 命令通道失败。
        return CommandResult(result["exit_code"], result["stdout"], result["stderr"],
                             timed_out=result["timed_out"])

    async def _run_worker(self, payload):
        async with self._lock:
            self._require_running()
            return await self._run_worker_locked(payload)

    async def _stop_command(self, control_path, *, service):
        cleanup = await self._runtime.exec(
            shlex.join([self.worker_python_executable, "-I", "-B", "-c", _STOP_COMMAND, control_path]),
            timeout_sec=10, service=service,
        )
        if cleanup.return_code != 0:
            raise ExecutionError(f"无法停止容器命令：{cleanup.stdout} {cleanup.stderr}")

    async def _run_worker_locked(self, payload, *, cwd=None, service="main"):
        # 未指定工作目录时使用镜像的工作目录。工作进程会先解析 '.'，
        # 再在临时目录树中运行下载的资源。
        if payload["workdir"] is None:
            payload = {**payload, "workdir": "."}
        # 调用方在传输和清理期间始终持有生命周期锁。
        token = uuid4().hex
        remote_directory = f"/tmp/research-{token}"
        remote_worker = remote_directory + "/worker.py"
        remote_payload = remote_directory + "/payload.json"
        control_path = remote_directory + "/command.pid"
        payload = {**payload, "control_path": control_path}
        try:
            async with asyncio.timeout(self._grace):
                # 持有父目录的所有权，使镜像中非 root 的 USER 也能删除
                # Docker 复制的文件（Docker 将这些文件的所有者设为 root）。
                prepared = await self._runtime.exec(
                    shlex.join(["mkdir", "-m", "700", "-p", "--", remote_directory]),
                    timeout_sec=math.ceil(self._grace), service=service,
                )
                if prepared.return_code != 0:
                    raise ExecutionError(f"无法创建工作进程目录：{prepared.stdout} {prepared.stderr}")
                with tempfile.TemporaryDirectory(prefix="skill-transfer-") as directory:
                    local_payload = Path(directory) / "payload.json"
                    local_payload.write_text(json.dumps(payload, ensure_ascii=True), encoding="utf-8")
                    # BenchFlow 的 upload_file 将 main 写死；使用相同的 Compose
                    # 通道，确保 execute(service=...) 仍作用于指定服务。
                    for source, target in ((self._worker_path, remote_worker),
                                           (local_payload, remote_payload)):
                        await self._runtime.inner._run_docker_compose_command(
                            ["cp", str(source), f"{service}:{target}"],
                            timeout_sec=math.ceil(self._grace),
                        )
            raw = await self._runtime.exec(
                shlex.join([self.worker_python_executable, "-I", "-B", remote_worker, remote_payload]),
                cwd=cwd, timeout_sec=math.ceil(payload["timeout"] + self._grace), service=service,
            )
        except BaseException as exc:
            self.state = "failed"
            try:
                await self._stop_command(
                    control_path, service=service,
                )
            except BaseException as cleanup_exc:
                exc.add_note(f"容器命令清理也失败：{cleanup_exc}")
            # 保留取消标记，直到 destroy() 移除此沙箱。
            raise
        error = None
        try:
            if raw.return_code != 0:
                raise ExecutionError(f"容器工作进程失败：{raw.stdout} {raw.stderr}")
            try:
                envelope = json.loads(raw.stdout or "")
                if "error" in envelope:
                    worker_error = ExecutionError(envelope["error"])
                    worker_error.add_note("容器工作进程异常堆栈：\n" + envelope["traceback"])
                    raise worker_error
                return envelope["result"]
            except (ValueError, TypeError, KeyError) as exc:
                raise ExecutionError("容器工作进程返回的响应无效") from exc
        except BaseException as exc:
            error = exc
            self.state = "failed"
            try:
                await self._stop_command(
                    control_path, service=service,
                )
            except BaseException as cleanup_exc:
                exc.add_note(f"容器命令清理也失败：{cleanup_exc}")
            raise
        finally:
            if error is None:
                try:
                    cleanup = await self._runtime.exec(
                        shlex.join(["rm", "-rf", "--", remote_directory]),
                        timeout_sec=30, service=service,
                    )
                    if cleanup.return_code != 0:
                        raise ExecutionError("无法移除传输的工作进程文件")
                except BaseException:
                    self.state = "failed"
                    raise
