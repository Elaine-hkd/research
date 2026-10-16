"""Result shared by harness implementations and their callers."""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class AgentResult:
    final_response: str
    finish_reason: str
    tool_calls: int = 0
    usage: dict[str, Any] = field(default_factory=dict)
