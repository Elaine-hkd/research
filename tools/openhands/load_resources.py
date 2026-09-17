from typing import ClassVar

from pydantic import Field

from openhands.sdk import Action, ToolDefinition
from openhands.sdk.tool import Tool, ToolExecutor

from tools.openhands.utils import get_json
from tools.openhands.observation import RegistryObservation


class LoadResourceAction(Action):
    name: str = Field(description="Name of the skill owning the resource")
    relative_path: str = Field(
        description="Resource path relative to the skill root"
    )


class LoadResourceExecutor(
    ToolExecutor[LoadResourceAction, RegistryObservation]
):
    def __call__(
        self, action: LoadResourceAction, conversation=None
    ) -> RegistryObservation:
        result = get_json(
            "/load_resource",
            {
                "skill_name": action.name,
                "relative_path": action.relative_path,
            },
        )
        return RegistryObservation.from_text(result)


class LoadResourceTool(
    ToolDefinition[LoadResourceAction, RegistryObservation]
):
    name: ClassVar[str] = "load_resource"

    @classmethod
    def create(cls, conv_state, **params):
        return [
            cls(
                description="Read a resource belonging to a skill.",
                action_type=LoadResourceAction,
                observation_type=RegistryObservation,
                executor=LoadResourceExecutor(),
            )
        ]