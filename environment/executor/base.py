from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from pathlib import PurePosixPath, PureWindowsPath
from typing import Mapping, Protocol, Sequence


class ExecutionError(RuntimeError):
    """A resource could not start, or its execution result is unavailable."""


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
    ) -> ExecutionResult: ...


def checked_timeout(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("timeout must be a positive finite number")
    if not math.isfinite(value) or value <= 0:
        raise ValueError("timeout must be a positive finite number")
    return float(value)


def checked_env(env: Mapping[str, str] | None) -> dict[str, str]:
    result = dict(env or {})
    for key, value in result.items():
        if (not isinstance(key, str) or not key or "=" in key or "\0" in key
                or not isinstance(value, str) or "\0" in value):
            raise ValueError("env must contain valid string names and values")
    return result


def request_payload(
    skill_name: str, relative_path: str,
    arguments: Sequence[str], timeout: float, workdir: str | None,
    python_executable: str, bash_executable: str, env: Mapping[str, str],
) -> dict:
    if not isinstance(skill_name, str) or not skill_name.strip():
        raise ValueError("skill_name must be a non-empty string")
    if not isinstance(relative_path, str) or not relative_path.strip():
        raise ValueError("relative_path must be a non-empty string")
    path = relative_path.replace("\\", "/")
    if ("\0" in path or PurePosixPath(path).is_absolute()
            or PureWindowsPath(path).drive or ".." in PurePosixPath(path).parts):
        raise ValueError("relative_path must stay within the skill directory")
    if isinstance(arguments, (str, bytes)) or not isinstance(arguments, Sequence):
        raise ValueError("arguments must be a sequence of strings")
    if any(not isinstance(arg, str) or "\0" in arg for arg in arguments):
        raise ValueError("arguments must contain strings without NUL characters")
    return {
        "relative_path": path,
        "arguments": list(arguments), "timeout": checked_timeout(timeout),
        "workdir": workdir, "python_executable": python_executable,
        "bash_executable": bash_executable, "env": dict(env),
    }
