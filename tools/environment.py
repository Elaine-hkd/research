"""同一环境中的命令、文件和资源操作，不依赖具体 harness。"""

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
        raise ValueError(f"预期匹配 {data['expected_occurrences']} 处，实际找到 {count} 处；文件未修改")
    with path.open("w", encoding="utf-8", newline="") as stream:
        stream.write(content.replace(data["old_text"], data["new_text"]))
    print(json.dumps({"path": str(path), "replacements": count}))
else:
    raise ValueError(f"未知文件操作：{operation}")
'''


def file_command(operation: str, arguments: dict, *, python_executable: str) -> str:
    """构造现有容器端 UTF-8 文件操作命令，argv 仅承载 JSON 数据。"""
    if operation not in {"read_file", "write_file", "replace_text"}:
        raise ValueError(f"未知文件操作：{operation}")
    return shlex.join([
        python_executable, "-I", "-B", "-X", "utf8", "-c", _FILE_PROGRAM, operation,
        json.dumps(arguments, ensure_ascii=False),
    ])


class EnvironmentOperations:
    """调用方管理环境、事件循环及尚未结束的操作任务。"""

    def __init__(self, environment, *, deadline: float, timeout: float,
                 output_chars: int, log_path: Path):
        if (isinstance(timeout, bool) or not isinstance(timeout, (int, float))
                or not math.isfinite(timeout) or timeout <= 0):
            raise ValueError("tool_timeout_sec 必须为有限正数")
        if type(output_chars) is not int or output_chars < 1:
            raise ValueError("max_output_chars 必须为正整数")
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
        command = file_command(
            operation, arguments,
            python_executable=self.environment.worker_python_executable,
        )
        return await self.run_command(
            command, timeout_sec=self.timeout,
            tool_name=operation, arguments=arguments,
        )

    async def _execute(self, tool_name, arguments, *, command=None, cwd=None,
                       requested_timeout=None, resource=False) -> OperationResult:
        call_index = None
        try:
            remaining = self.deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("智能体时间预算已耗尽")
            timeout = min(requested_timeout or self.timeout, self.timeout, remaining)
            self.calls += 1
            call_index = self.calls
            self.calls_by_tool[tool_name] += 1
            append_jsonl(self.log_path, {"type": "call", "index": call_index,
                                       "tool_name": tool_name, "arguments": arguments})
            if resource:
                # 执行器为下载和脚本执行分配时间预算，再由工作进程
                # 在规定时限内完成清理，不直接取消工作进程。
                result = await self.environment.executor.execute_resource(
                    arguments["skill_name"], arguments["relative_path"],
                    arguments=arguments.get("arguments", []), timeout=timeout,
                    total_timeout=timeout,
                )
                raw = result.to_dict()
            else:
                result = await self.environment.execute(
                    ["bash", "-c", command], cwd=cwd, timeout=timeout,
                )
                raw = asdict(result)
            if result.timed_out:
                raw["error"] = {
                    "code": "TOOL_TIMEOUT",
                    "message": f"工具超过了 {timeout:g} 秒的时间预算；执行已停止或尚未启动",
                    "recoverable": True,
                }
            append_jsonl(self.log_path, {"type": "result", "index": call_index,
                                       "tool_name": tool_name, **raw})
            for field in ("stdout", "stderr"):
                if len(raw[field]) > self.output_chars:
                    raw[field] = (raw[field][:self.output_chars]
                                  + "\n[输出已截断，完整输出已保存至 tool-calls.jsonl]")
            return OperationResult(json.dumps(raw, ensure_ascii=False), not result.success)
        except ValueError as exc:
            if not resource:
                self.failure = exc
                raise
            # 无效资源名称和路径仍作为模型可见的工具错误返回。
            raw = {"error": str(exc)}
            append_jsonl(self.log_path, {"type": "result", "index": call_index,
                                       "tool_name": tool_name, **raw})
            return OperationResult(json.dumps(raw, ensure_ascii=False), True)
        except Exception as exc:
            self.failure = exc
            raise
