from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, Sequence
from .executor.base import ResourceExecutor


class EnvironmentError(RuntimeError):
    pass


@dataclass(frozen=True)
class CommandResult:
    """A completed command attempt; a timeout is a result after safe cleanup."""

    exit_code: int | None
    stdout: str
    stderr: str
    timed_out: bool = False

    @property
    def success(self) -> bool:
        return not self.timed_out and self.exit_code == 0


class Environment(Protocol):
    executor: ResourceExecutor
    async def start(self) -> None: ...
    async def wait_ready(self, timeout: float = 30) -> None: ...
    async def execute(self, argv: Sequence[str], *, cwd: str | None = None,
                      timeout: float = 60, service: str = "main") -> CommandResult: ...
    async def upload(self, source: str | Path, destination: str) -> None: ...
    async def download(self, source: str, destination: str | Path,
                       *, directory: bool = False) -> None: ...
    async def destroy(self) -> None: ...
