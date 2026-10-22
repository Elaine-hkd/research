"""环境中 shell、文件和技能操作共用的同步桥接接口。"""

import asyncio
from pathlib import Path
import threading
from typing import ClassVar

from openhands.sdk import Action, Observation, ToolDefinition
from openhands.sdk.tool import ToolExecutor
from pydantic import Field

from tools.environment import EnvironmentOperations


class ShellAction(Action):
    command: str = Field(min_length=1, description="Bash command executed inside the task container")
    cwd: str | None = Field(default=None, description="Absolute Linux working directory; omit for image WORKDIR")
    timeout_sec: float = Field(default=120, gt=0, allow_inf_nan=False)


class ShellObservation(Observation):
    """SDK 的 kind 判别器所需的具体输出模式。"""


class ShellExecutor(ToolExecutor[ShellAction, ShellObservation]):
    def __init__(self, environment, loop, *, deadline, timeout, output_chars, log_path: Path):
        self.environment = environment
        self.loop = loop
        self.deadline = deadline
        self.timeout = timeout
        self.output_chars = output_chars
        self.log_path = log_path
        self.operations = EnvironmentOperations(
            environment, deadline=deadline, timeout=timeout,
            output_chars=output_chars, log_path=log_path,
        )
        self.calls_by_tool = self.operations.calls_by_tool
        self._pending = None
        self._closed = False
        self._guard = threading.Lock()
        self._tasks = set()
        self._idle = threading.Event()
        self._idle.set()

    @property
    def failure(self):
        return self.operations.failure

    @property
    def calls(self):
        return self.operations.calls

    async def _execute(self, action, tool_name: str, arguments: dict, *, resource=False):
        task = asyncio.current_task()
        self._tasks.add(task)
        try:
            if resource:
                result = await self.operations.run_resource(arguments)
            else:
                result = await self.operations.run_command(
                    action.command, cwd=action.cwd, timeout_sec=action.timeout_sec,
                    tool_name=tool_name, arguments=arguments,
                )
            return ShellObservation.from_text(result.text, is_error=result.is_error)
        finally:
            self._tasks.discard(task)

    def __call__(self, action: ShellAction, conversation=None) -> ShellObservation:
        return self.run_command(action, conversation)

    def run_command(self, action: ShellAction, conversation=None, *,
                    tool_name="terminal", arguments=None) -> ShellObservation:
        return self._submit(
            action, conversation, tool_name=tool_name,
            arguments=action.model_dump(mode="json") if arguments is None else arguments,
        )

    def run_resource(self, action, conversation=None) -> ShellObservation:
        return self._submit(
            action, conversation, tool_name="execute_resource",
            arguments=action.model_dump(mode="json"), resource=True,
        )

    def _submit(self, action, conversation, *, tool_name, arguments, resource=False):
        # OpenHands 的 arun 在线程中分发工具，需将 Docker I/O
        # 调度回原事件循环；此处使用 asyncio.run 会破坏锁的约束。
        with self._guard:
            if self._closed:
                raise RuntimeError("环境执行通道已关闭")
            self._idle.clear()
            future = asyncio.run_coroutine_threadsafe(
                self._execute(action, tool_name, arguments, resource=resource),
                self.loop,
            )
            self._pending = future
        try:
            return future.result()
        except Exception:
            if conversation is not None:
                conversation.interrupt()
            raise
        finally:
            with self._guard:
                self._pending = None
                self._idle.set()

    def interrupt(self) -> None:
        with self._guard:
            self._closed = True
            if self._pending is not None:
                self._pending.cancel()

    def close(self) -> None:
        self.interrupt()

    async def drain(self) -> None:
        self.interrupt()
        await asyncio.sleep(0)  # 检查进行中的 I/O 前，先传递取消信号。
        if self._tasks:
            await asyncio.gather(*tuple(self._tasks), return_exceptions=True)
        await asyncio.to_thread(self._idle.wait)


class EnvironmentShellTool(ToolDefinition[ShellAction, ShellObservation]):
    name: ClassVar[str] = "terminal"

    @classmethod
    def create(cls, conv_state=None, *, executor):
        return [cls(
            description=("Run Bash commands in this task's isolated Linux container. "
                         "Use this tool to inspect, read, edit files and execute programs. "
                         "Each invocation starts a new shell; use cwd or absolute paths."),
            action_type=ShellAction, observation_type=ShellObservation, executor=executor,
        )]
