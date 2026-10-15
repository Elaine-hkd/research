"""Reusable execution-environment backends."""

from .base import CommandResult, Environment, EnvironmentError
from .desktop import configure_task_desktop
from .docker import DockerEnvironment
from .local import LocalEnvironment
from .tasks import materialize_skillsbench_task

# Compatibility name for the previous environment API (now owns an executor).
BenchFlowEnvironment = DockerEnvironment

__all__ = ["LocalEnvironment", "DockerEnvironment", "BenchFlowEnvironment", "CommandResult", "Environment", "EnvironmentError", "materialize_skillsbench_task", "configure_task_desktop"]
