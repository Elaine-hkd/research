import json
import os
from typing import ClassVar
from pydantic import Field

from openhands.sdk import Action, ToolDefinition
from openhands.sdk.tool import ToolExecutor

from tools.openhands.utils import get_json, json_text
from tools.openhands.observation import RegistryObservation


class SearchSkillsAction(Action):
    query: str = Field(
        default="",
        description="Search query; defaults to an empty string",
    )
    exposed_skills: list[str] | None = Field(
        default=None,
        description=(
            "Compatibility parameter. Skill visibility is controlled by the caller, "
            "using the task environment or EXPOSED_SKILLS."
        ),
    )


class SearchSkillsExecutor(
    ToolExecutor[SearchSkillsAction, RegistryObservation]
):
    def __init__(self, registry=None):
        self.registry = registry

    def __call__(
        self, action: SearchSkillsAction, conversation=None
    ) -> RegistryObservation:
        params = {"query": action.query}
        if self.registry is None:
            exposed_skills = json.loads(os.environ.get("EXPOSED_SKILLS", "[]"))
            if not isinstance(exposed_skills, list) or not all(
                isinstance(name, str) and name.strip() for name in exposed_skills
            ):
                raise ValueError("EXPOSED_SKILLS must be a JSON array of skill names")
            params["exposed_skills"] = json_text(exposed_skills)
        result = get_json(
            "/search_skills",
            params=params, registry=self.registry,
        )
        return RegistryObservation.from_text(result)


class SearchSkillsTool(
    ToolDefinition[SearchSkillsAction, RegistryObservation]
):
    name: ClassVar[str] = "search_skills"

    @classmethod
    def create(cls, conv_state=None, *, registry=None, **params):
        return [
            cls(
                description=(
                    "Search the skill registry and return skill summaries "
                    "without loading full instructions. "
                    "Use load_skill to load a selected skill."
                ),
                action_type=SearchSkillsAction,
                observation_type=RegistryObservation,
                executor=SearchSkillsExecutor(registry),
            )
        ]
