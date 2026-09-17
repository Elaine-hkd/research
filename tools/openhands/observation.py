import json
import math
import os
from typing import ClassVar
from urllib.parse import urljoin

import requests
from pydantic import Field, field_validator

from openhands.sdk import Action, Observation, TextContent, ToolDefinition
from openhands.sdk.tool import Tool, ToolExecutor, register_tool


BASE_URL = os.environ.get("SKILL_REGISTRY_URL")
if not BASE_URL:
    raise RuntimeError("Missing SKILL_REGISTRY_URL")

EXPOSED_SKILLS = json.loads(os.environ.get("EXPOSED_SKILLS", "[]"))
if not isinstance(EXPOSED_SKILLS, list) or not all(
    isinstance(name, str) and name.strip()
    for name in EXPOSED_SKILLS
):
    raise ValueError(
        "EXPOSED_SKILLS must be a JSON array of skill names"
    )


class RegistryObservation(Observation):
    pass

