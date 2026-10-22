"""harness 实现及其调用方共用的结果类型。"""

from dataclasses import dataclass, field
import json
from typing import Any


def task_prompt(instruction: str, skill_catalog: list[dict[str, str]] | None = None) -> str:
    if not skill_catalog:
        return instruction
    return instruction + (
        "\n\nAvailable task skills (read SKILL.md before using a skill):\n"
        + json.dumps([{key: skill[key] for key in ("name", "description", "path")}
                      for skill in skill_catalog], ensure_ascii=False, indent=2)
    )


@dataclass
class AgentResult:
    final_response: str
    finish_reason: str
    tool_calls: int | None = 0
    usage: dict[str, Any] = field(default_factory=dict)
    # 独立调用方使用绝对路径；评估流程也接受相对于试验目录的路径，
    # 并保存为可迁移的相对引用。
    trace_path: str | None = None
