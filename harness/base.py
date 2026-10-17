"""Result shared by harness implementations and their callers."""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class AgentResult:
    final_response: str
    finish_reason: str
    tool_calls: int | None = 0
    usage: dict[str, Any] = field(default_factory=dict)
    # Absolute path for standalone callers; evaluation also accepts a path
    # relative to the trial directory and stores a portable relative reference.
    trace_path: str | None = None
