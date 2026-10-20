"""Result shared by harness implementations and their callers."""

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
    # Absolute path for standalone callers; evaluation also accepts a path
    # relative to the trial directory and stores a portable relative reference.
    trace_path: str | None = None
