import math
from typing import ClassVar

from pydantic import Field, field_validator

from openhands.sdk import Action, ToolDefinition
from openhands.sdk.tool import ToolExecutor

from tools.openhands.utils import post_json
from tools.openhands.observation import RegistryObservation


class ExecuteResourceAction(Action):
    skill_name: str = Field(
        description="Name of the skill owning the resource"
    )
    relative_path: str = Field(
        description="Resource path relative to the skill root"
    )
    arguments: list[str] = Field(
        default_factory=list,
        description="Command-line arguments as separate array elements",
    )
    timeout: float | None = Field(
        default=None,
        description=(
            "Execution timeout in seconds; omit for the execution backend default"
        ),
    )

    @field_validator("skill_name", "relative_path")
    @classmethod
    def check_nonempty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError(
                "skill_name 和 relative_path 不能为空"
            )
        return value

    @field_validator("timeout")
    @classmethod
    def check_timeout(cls, value: float | None) -> float | None:
        if value is not None and (
            not math.isfinite(value) or value <= 0
        ):
            raise ValueError(
                "timeout 必须为有限正数"
            )
        return value


class ExecuteResourceExecutor(
    ToolExecutor[ExecuteResourceAction, RegistryObservation]
):
    def __init__(self, channel=None):
        self.channel = channel

    def __call__(
        self, action: ExecuteResourceAction, conversation=None
    ) -> RegistryObservation:
        if self.channel is not None:
            observation = self.channel.run_resource(action, conversation)
            return RegistryObservation.from_text(observation.text, is_error=observation.is_error)

        body = {
            "skill_name": action.skill_name,
            "relative_path": action.relative_path,
            "arguments": action.arguments,
        }

        result = post_json(
            "/execute_resource",
            body
        )
        return RegistryObservation.from_text(result)

    def interrupt(self) -> None:
        if self.channel is not None:
            self.channel.interrupt()

    def close(self) -> None:
        if self.channel is not None:
            self.channel.close()


class ExecuteResourceTool(
    ToolDefinition[ExecuteResourceAction, RegistryObservation]
):
    name: ClassVar[str] = "execute_resource"

    @classmethod
    def create(cls, conv_state=None, *, executor=None, **params):
        execution_description = "Execution takes place in the environment managed by the registry server."
        if executor is not None:
            execution_description = (
                "Resources are downloaded from the registry and executed in this task's "
                "environment. Relative input/output paths use its working directory; "
                "write persistent outputs outside the temporary skill directory."
            )
        return [
            cls(
                description=(
                    "Execute a script or executable resource belonging "
                    "to a skill. Pass command-line arguments as separate "
                    "array elements. "
                    + execution_description
                ),
                action_type=ExecuteResourceAction,
                observation_type=RegistryObservation,
                executor=ExecuteResourceExecutor(executor),
            )
        ]
