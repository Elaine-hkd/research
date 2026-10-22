"""基于可调用对象的轻量评估流程；导入不会启动 SDK 或 Docker。"""

from .contracts import AgentResult, PreparedTask, Score, Task, TrialContext
from .runner import run_evaluation

__all__ = ["AgentResult", "PreparedTask", "Score", "Task", "TrialContext", "run_evaluation"]
