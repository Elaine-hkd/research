"""OpenHands' synchronous tool executor bridges to the owning async environment."""

import asyncio
from dataclasses import asdict
import json
from pathlib import Path
import threading
import time
from typing import ClassVar

from openhands.sdk import Action, Observation, ToolDefinition
from openhands.sdk.tool import ToolExecutor
from pydantic import Field

from utils.json_io import append_jsonl


class ShellAction(Action):
    command: str = Field(min_length=1, description="Bash command executed inside the task container")
    cwd: str | None = Field(default=None, description="Absolute Linux working directory; omit for image WORKDIR")
    timeout_sec: float = Field(default=120, gt=0, allow_inf_nan=False)


class ShellObservation(Observation):
    """Concrete output schema required by the SDK's kind discriminator."""


class ShellExecutor(ToolExecutor[ShellAction, ShellObservation]):
    def __init__(self, environment, loop, *, deadline, timeout, output_chars, log_path: Path):
        self.environment = environment
        self.loop = loop
        self.deadline = deadline
        self.timeout = timeout
        self.output_chars = output_chars
        self.log_path = log_path
        self.failure = None
        self.calls = 0
        self._pending = None
        self._closed = False
        self._guard = threading.Lock()
        self._tasks = set()
        self._idle = threading.Event()
        self._idle.set()

    async def _execute(self, action: ShellAction):
        task = asyncio.current_task()
        self._tasks.add(task)
        try:
            remaining = self.deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Agent time budget exhausted")
            timeout = min(action.timeout_sec, self.timeout, remaining)
            self.calls += 1
            append_jsonl(self.log_path, {"type": "call", "index": self.calls,
                                       "arguments": action.model_dump(mode="json")})
            result = await self.environment.execute(
                ["bash", "-c", action.command], cwd=action.cwd, timeout=timeout,
            )
            raw = asdict(result)
            append_jsonl(self.log_path, {"type": "result", "index": self.calls, **raw})
            # Keep complete output on disk while bounding model context growth.
            for key in ("stdout", "stderr"):
                if len(raw[key]) > self.output_chars:
                    raw[key] = raw[key][:self.output_chars] + "\n[truncated; full output saved in tool-calls.jsonl]"
            return ShellObservation.from_text(json.dumps(raw, ensure_ascii=False))
        except Exception as exc:
            self.failure = exc
            raise
        finally:
            self._tasks.discard(task)

    def __call__(self, action: ShellAction, conversation=None) -> ShellObservation:
        # OpenHands arun dispatches tools on worker threads. Schedule Docker I/O
        # back on the original loop; running asyncio.run here would break locks.
        with self._guard:
            if self._closed:
                raise RuntimeError("Evaluation shell is closed")
            self._idle.clear()
            future = asyncio.run_coroutine_threadsafe(self._execute(action), self.loop)
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
        await asyncio.sleep(0)  # Deliver cancellation before checking active I/O.
        if self._tasks:
            await asyncio.gather(*tuple(self._tasks), return_exceptions=True)
        await asyncio.to_thread(self._idle.wait)


class EnvironmentShellTool(ToolDefinition[ShellAction, ShellObservation]):
    name: ClassVar[str] = "evaluation_shell"

    @classmethod
    def create(cls, conv_state=None, *, executor):
        return [cls(
            description=("Run Bash commands in this task's isolated Linux container. "
                         "Use this tool to inspect, read, edit files and execute programs. "
                         "Each invocation starts a new shell; use cwd or absolute paths."),
            action_type=ShellAction, observation_type=ShellObservation, executor=executor,
        )]
