"""Small, callable-based evaluation pipeline; imports do not start SDKs or Docker."""

from .contracts import AgentResult, PreparedTask, Score, Task, TrialContext
from .runner import run_evaluation

__all__ = ["AgentResult", "PreparedTask", "Score", "Task", "TrialContext", "run_evaluation"]
