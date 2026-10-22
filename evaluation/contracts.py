"""在数据集、智能体和评分函数之间传递的数据。"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from environment.base import Environment
from harness.base import AgentResult


@dataclass(frozen=True)
class Task:
    id: str
    instruction: str
    source: dict[str, Any]
    metadata: dict[str, Any] = field(default_factory=dict)
    agent_timeout_sec: float = 900


@dataclass(frozen=True)
class PreparedTask:
    task_dir: Path
    skills_dir: Path | None = None
    # 仅包含描述和容器路径，完整指令保留在文件中。
    skill_catalog: list[dict[str, str]] = field(default_factory=list)


@dataclass(frozen=True)
class TrialContext:
    task: Task
    prepared: PreparedTask
    environment: Environment
    output_dir: Path
    condition: str
    repeat: int


@dataclass(frozen=True)
class Score:
    reward: float
    metrics: dict[str, Any] = field(default_factory=dict)
