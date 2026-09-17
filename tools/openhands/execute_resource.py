import math
from typing import ClassVar

from pydantic import Field, field_validator

from openhands.sdk import Action, ToolDefinition
from openhands.sdk.tool import Tool, ToolExecutor

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
            "Execution timeout in seconds; omit for server default"
        ),
    )

    @field_validator("skill_name", "relative_path")
    @classmethod
    def check_nonempty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError(
                "skill_name and relative_path must not be empty"
            )
        return value

    @field_validator("timeout")
    @classmethod
    def check_timeout(cls, value: float | None) -> float | None:
        if value is not None and (
            not math.isfinite(value) or value <= 0
        ):
            raise ValueError(
                "timeout must be a positive finite number"
            )
        return value


class ExecuteResourceExecutor(
    ToolExecutor[ExecuteResourceAction, RegistryObservation]
):
    def __call__(
        self, action: ExecuteResourceAction, conversation=None
    ) -> RegistryObservation:
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


class ExecuteResourceTool(
    ToolDefinition[ExecuteResourceAction, RegistryObservation]
):
    name: ClassVar[str] = "execute_resource"

    @classmethod
    def create(cls, conv_state, **params):
        return [
            cls(
                description=(
                    "Execute a script or executable resource belonging "
                    "to a skill. Pass command-line arguments as separate "
                    "array elements. Execution takes place in the "
                    "environment managed by the registry server."
                ),
                action_type=ExecuteResourceAction,
                observation_type=RegistryObservation,
                executor=ExecuteResourceExecutor(),
            )
        ]