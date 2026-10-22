from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from pathlib import PurePosixPath, PureWindowsPath
from typing import Mapping, Protocol, Sequence


class ExecutionError(RuntimeError):
    """资源无法启动，或无法取得执行结果。"""


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    provider: str
    exit_code: int | None
    stdout: str
    stderr: str
    timed_out: bool
    duration_ms: int

    @property
    def success(self) -> bool:
        return not self.timed_out and self.exit_code == 0

    def to_dict(self) -> dict:
        return {**asdict(self), "success": self.success}


class ResourceExecutor(Protocol):
    async def execute_resource(
        self, skill_name: str, relative_path: str,
        arguments: Sequence[str] = (), timeout: float | None = None,
        *, total_timeout: float | None = None,
    ) -> ExecutionResult: ...


def checked_timeout(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("timeout 必须为有限正数")
    if not math.isfinite(value) or value <= 0:
        raise ValueError("timeout 必须为有限正数")
    return float(value)


def checked_env(env: Mapping[str, str] | None) -> dict[str, str]:
    result = dict(env or {})
    for key, value in result.items():
        if (not isinstance(key, str) or not key or "=" in key or "\0" in key
                or not isinstance(value, str) or "\0" in value):
            raise ValueError("env 必须包含有效的字符串名称和值")
    return result


def request_payload(
    skill_name: str, relative_path: str,
    arguments: Sequence[str], timeout: float, workdir: str | None,
    python_executable: str, bash_executable: str, env: Mapping[str, str],
) -> dict:
    if not isinstance(skill_name, str) or not skill_name.strip():
        raise ValueError("skill_name 必须为非空字符串")
    if not isinstance(relative_path, str) or not relative_path.strip():
        raise ValueError("relative_path 必须为非空字符串")
    path = relative_path.replace("\\", "/")
    if ("\0" in path or PurePosixPath(path).is_absolute()
            or PureWindowsPath(path).drive or ".." in PurePosixPath(path).parts):
        raise ValueError("relative_path 必须位于技能目录内")
    if isinstance(arguments, (str, bytes)) or not isinstance(arguments, Sequence):
        raise ValueError("arguments 必须为字符串序列")
    if any(not isinstance(arg, str) or "\0" in arg for arg in arguments):
        raise ValueError("arguments 中的字符串不能包含 NUL 字符")
    return {
        "relative_path": path,
        "arguments": list(arguments), "timeout": checked_timeout(timeout),
        "workdir": workdir, "python_executable": python_executable,
        "bash_executable": bash_executable, "env": dict(env),
    }
