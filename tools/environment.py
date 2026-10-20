"""Harness-independent command, file and resource operations in one environment."""

import asyncio
from collections import Counter
from dataclasses import asdict, dataclass
import json
import math
from pathlib import Path
import shlex
import time

from utils.json_io import append_jsonl


@dataclass(frozen=True)
class OperationResult:
    text: str
    is_error: bool = False


_FILE_PROGRAM = '''
import json
from itertools import islice
from pathlib import Path
import sys

operation, data = sys.argv[1], json.loads(sys.argv[2])
path = Path(data["path"])
if operation == "read_file":
    start = data["start_line"]
    with path.open("r", encoding="utf-8", newline="") as stream:
        for number, line in enumerate(islice(stream, start - 1, start - 1 + data["max_lines"]), start):
            print(f"{number}: {line}", end="" if line.endswith("\\n") else "\\n")
elif operation == "write_file":
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as stream:
        stream.write(data["content"])
    print(json.dumps({"path": str(path), "written_chars": len(data["content"])}))
elif operation == "replace_text":
    with path.open("r", encoding="utf-8", newline="") as stream:
        content = stream.read()
    count = content.count(data["old_text"])
    if count != data["expected_occurrences"]:
        raise ValueError(f"Expected {data['expected_occurrences']} matches, found {count}; file unchanged")
    with path.open("w", encoding="utf-8", newline="") as stream:
        stream.write(content.replace(data["old_text"], data["new_text"]))
    print(json.dumps({"path": str(path), "replacements": count}))
else:
    raise ValueError(f"Unknown file operation: {operation}")
'''


def file_command(operation: str, arguments: dict) -> str:
    """Build the existing container-side UTF-8 file operation; argv holds JSON data."""
    if operation not in {"read_file", "write_file", "replace_text"}:
        raise ValueError(f"Unknown file operation: {operation}")
    return shlex.join([
        "python3", "-X", "utf8", "-c", _FILE_PROGRAM, operation,
        json.dumps(arguments, ensure_ascii=False),
    ])


class EnvironmentOperations:
    """The caller owns the environment, event loop and in-flight operation tasks."""

    def __init__(self, environment, *, deadline: float, timeout: float,
                 output_chars: int, log_path: Path):
        if (isinstance(timeout, bool) or not isinstance(timeout, (int, float))
                or not math.isfinite(timeout) or timeout <= 0):
            raise ValueError("tool_timeout_sec must be a positive finite number")
        if type(output_chars) is not int or output_chars < 1:
            raise ValueError("max_output_chars must be a positive integer")
        self.environment = environment
        self.deadline = deadline
        self.timeout = timeout
        self.output_chars = output_chars
        self.log_path = Path(log_path)
        self.failure = None
        self.calls = 0
        self.calls_by_tool = Counter()

    async def run_command(self, command: str, *, cwd=None, timeout_sec=120,
                          tool_name="terminal", arguments=None) -> OperationResult:
        if arguments is None:
            arguments = {"command": command, "cwd": cwd, "timeout_sec": timeout_sec}
        return await self._execute(tool_name, arguments, command=command, cwd=cwd,
                                   requested_timeout=timeout_sec)

    async def run_resource(self, arguments: dict) -> OperationResult:
        return await self._execute("execute_resource", arguments,
                                   requested_timeout=arguments.get("timeout"), resource=True)

    async def run_file(self, operation: str, arguments: dict) -> OperationResult:
        return await self.run_command(
            file_command(operation, arguments), timeout_sec=self.timeout,
            tool_name=operation, arguments=arguments,
        )

    async def _execute(self, tool_name, arguments, *, command=None, cwd=None,
                       requested_timeout=None, resource=False) -> OperationResult:
        call_index = None
        try:
            remaining = self.deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Agent time budget exhausted")
            timeout = min(requested_timeout or self.timeout, self.timeout, remaining)
            self.calls += 1
            call_index = self.calls
            self.calls_by_tool[tool_name] += 1
            append_jsonl(self.log_path, {"type": "call", "index": call_index,
                                       "tool_name": tool_name, "arguments": arguments})
            if resource:
                # Bound download, transfer and execution together, as OpenHands did.
                async with asyncio.timeout(timeout):
                    result = await self.environment.executor.execute_resource(
                        arguments["skill_name"], arguments["relative_path"],
                        arguments=arguments.get("arguments", []), timeout=timeout,
                    )
                raw = result.to_dict()
            else:
                result = await self.environment.execute(
                    ["bash", "-c", command], cwd=cwd, timeout=timeout,
                )
                raw = asdict(result)
            append_jsonl(self.log_path, {"type": "result", "index": call_index,
                                       "tool_name": tool_name, **raw})
            if resource and result.timed_out:
                raise TimeoutError(f"Skill resource exceeded its {timeout}s execution budget")
            for field in ("stdout", "stderr"):
                if len(raw[field]) > self.output_chars:
                    raw[field] = (raw[field][:self.output_chars]
                                  + "\n[truncated; full output saved in tool-calls.jsonl]")
            return OperationResult(json.dumps(raw, ensure_ascii=False), not result.success)
        except ValueError as exc:
            if not resource:
                self.failure = exc
                raise
            # Invalid resource names/paths remain model tool errors.
            raw = {"error": str(exc)}
            append_jsonl(self.log_path, {"type": "result", "index": call_index,
                                       "tool_name": tool_name, **raw})
            return OperationResult(json.dumps(raw, ensure_ascii=False), True)
        except Exception as exc:
            self.failure = exc
            raise
