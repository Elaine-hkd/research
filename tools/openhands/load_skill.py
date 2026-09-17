from typing import ClassVar

from pydantic import Field

from openhands.sdk import Action, ToolDefinition
from openhands.sdk.tool import Tool, ToolExecutor

from tools.openhands.utils import get_json
from tools.openhands.observation import RegistryObservation

class LoadSkillAction(Action):
    name: str = Field(
        description="Skill name from the available skills catalog"
    )


class LoadSkillExecutor(
    ToolExecutor[LoadSkillAction, RegistryObservation]
):
    def __call__(
        self, action: LoadSkillAction, conversation=None
    ) -> RegistryObservation:
        result = get_json(
            "/load_skill",
            {"skill_name": action.name},
        )
        return RegistryObservation.from_text(result)


class LoadSkillTool(
    ToolDefinition[LoadSkillAction, RegistryObservation]
):
    name: ClassVar[str] = "load_skill"

    @classmethod
    def create(cls, conv_state, **params):
        return [
            cls(
                description=(
                    "Load a skill's instructions and resource list by name."
                ),
                action_type=LoadSkillAction,
                observation_type=RegistryObservation,
                executor=LoadSkillExecutor(),
            )
        ]