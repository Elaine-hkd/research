import json
import os
from typing import ClassVar
from urllib.parse import urljoin

import requests
from pydantic import Field

from openhands.sdk import Action, ToolDefinition
from openhands.sdk.tool import Tool, ToolExecutor

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
            "Compatibility parameter. This adapter uses the server-side "
            "EXPOSED_SKILLS environment configuration instead."
        ),
    )


class SearchSkillsExecutor(
    ToolExecutor[SearchSkillsAction, RegistryObservation]
):
    def __call__(
        self, action: SearchSkillsAction, conversation=None
    ) -> RegistryObservation:
        base_url = os.environ.get("SKILL_REGISTRY_URL")
        if not base_url:
            raise RuntimeError("Missing SKILL_REGISTRY_URL")
        exposed_skills = json.loads(os.environ.get("EXPOSED_SKILLS", "[]"))

        result = get_json(
            "/search_skills",
            params={
                "query": action.query,
                "exposed_skills": json_text(exposed_skills),
            },
        )
        return RegistryObservation.from_text(result)


class SearchSkillsTool(
    ToolDefinition[SearchSkillsAction, RegistryObservation]
):
    name: ClassVar[str] = "search_skills"

    @classmethod
    def create(cls, conv_state, **params):
        return [
            cls(
                description=(
                    "Search the skill registry and return skill summaries "
                    "without loading full instructions. "
                    "Use load_skill to load a selected skill."
                ),
                action_type=SearchSkillsAction,
                observation_type=RegistryObservation,
                executor=SearchSkillsExecutor(),
            )
        ]