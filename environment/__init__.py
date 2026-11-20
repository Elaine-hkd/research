"""可复用的执行环境后端。"""

from .base import CommandResult, Environment, EnvironmentError
from .desktop import configure_task_desktop
from .docker import DockerEnvironment
from .local import LocalEnvironment
from .tasks import materialize_skillsbench_task, prepare_container_task

# 保留旧环境 API 的兼容名称（现在由环境持有执行器）。
BenchFlowEnvironment = DockerEnvironment

__all__ = ["LocalEnvironment", "DockerEnvironment", "BenchFlowEnvironment", "CommandResult", "Environment", "EnvironmentError", "materialize_skillsbench_task", "prepare_container_task", "configure_task_desktop"]
