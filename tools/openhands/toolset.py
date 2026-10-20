"""OpenHands-specific class imports and SDK registration for shared tool names."""

from importlib import import_module
from uuid import uuid4

from openhands.sdk import ToolDefinition
from openhands.sdk.tool import Tool, register_tool

from tools.loader import REGISTRY_TOOLS

TOOL_CLASSES = {
    "search_skills": "tools.openhands.search_skills:SearchSkillsTool",
    "load_skill": "tools.openhands.load_skill:LoadSkillTool",
    "load_resource": "tools.openhands.load_resources:LoadResourceTool",
    "execute_resource": "tools.openhands.execute_resource:ExecuteResourceTool",
    "terminal": "tools.openhands.terminal:EnvironmentShellTool",
    "read_file": "tools.openhands.file_editor:ReadFileTool",
    "write_file": "tools.openhands.file_editor:WriteFileTool",
    "replace_text": "tools.openhands.file_editor:ReplaceTextTool",
    "think": "tools.openhands.control:LocalThinkTool",
    "finish": "tools.openhands.control:LocalFinishTool",
}


def create_tools(selected: list[str], *, executor=None) -> tuple[list[Tool], set[str]]:
    """Each class implements create(conv_state=None, *, executor=None, **params).

    executor is the current environment's command channel, or None for tools
    that do not use an environment. Only configured classes are imported.
    """
    if unknown := set(selected) - TOOL_CLASSES.keys():
        raise ValueError(f"No OpenHands tool implementation for: {sorted(unknown)}")
    registry = None
    if executor is not None and set(selected).intersection(REGISTRY_TOOLS):
        registry = executor.environment.executor.registry
        if registry is None:
            raise ValueError("Registry tools with an environment require its registry_url")
    specs, names = [], set()
    for name in selected:
        reference = TOOL_CLASSES[name]
        module, separator, attribute = reference.partition(":")
        if not separator or not module or not attribute:
            raise ValueError(f"Expected module:Class, received {reference!r}")
        tool_class = getattr(import_module(module), attribute)
        if not isinstance(tool_class, type) or not issubclass(tool_class, ToolDefinition):
            raise TypeError(f"Configured tool must be a ToolDefinition subclass: {reference}")
        bindings = {"executor": executor}
        if name in REGISTRY_TOOLS:
            bindings["registry"] = registry
        definitions = tool_class.create(conv_state=None, **bindings)
        if not definitions:
            raise ValueError(f"Configured tool created no definitions: {reference}")
        for definition in definitions:
            if not isinstance(definition, ToolDefinition) or definition.executor is None:
                raise TypeError(f"Tool must have a definition and an executor: {reference}")
            if definition.name in names:
                raise ValueError(f"Duplicate model-visible tool name: {definition.name}")
            names.add(definition.name)
            # Never overwrite another trial's binding in the SDK global registry.
            binding = "custom_" + uuid4().hex
            register_tool(binding, definition)
            specs.append(Tool(name=binding))
    return specs, names
