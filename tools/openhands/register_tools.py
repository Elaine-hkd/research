from openhands.sdk import Tool
from openhands.sdk.tool import register_tool

from tools.openhands.search_skills import SearchSkillsTool
from tools.openhands.load_skill import LoadSkillTool
from tools.openhands.load_resources import LoadResourceTool
from tools.openhands.execute_resource import ExecuteResourceTool

TOOL_CLASSES = (
    SearchSkillsTool,
    LoadSkillTool,
    LoadResourceTool,
    ExecuteResourceTool,
)

for tool_class in TOOL_CLASSES:
    register_tool(tool_class.name, tool_class)


def get_registry_tools() -> list[Tool]:
    return [Tool(name=cls.name) for cls in TOOL_CLASSES]