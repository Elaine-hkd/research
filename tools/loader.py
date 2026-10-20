"""Shared tool selection and adapter loading, without a harness SDK dependency."""

from importlib import import_module
import re


REGISTRY_TOOLS = ("search_skills", "load_skill", "load_resource", "execute_resource")
ENVIRONMENT_TOOLS = ("terminal", "think", "finish")


def validate_tools(tools) -> list[str]:
    if (not isinstance(tools, (list, tuple))
            or any(not isinstance(name, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name)
                   for name in tools)
            or len(tools) != len(set(tools))):
        raise ValueError("tools must be a list of distinct tool names")
    return list(tools)


def load_tools(harness: str, tools, **bindings):
    """Load only the selected harness's adapter and return its native tools.

    Adapters implement create_tools(names, **bindings) -> (native_value, names).
    OpenHands returns SDK Tool specs; dsh returns patches/env constructor kwargs.
    bindings supplies live resources such as a trial's executor, not settings.
    """
    if not isinstance(harness, str) or not re.fullmatch(r"[a-z][a-z0-9_]*", harness):
        raise ValueError("harness must be a valid adapter name")
    selected = validate_tools(tools)
    adapter = import_module(f"tools.{harness}.toolset")
    native, loaded_names = adapter.create_tools(selected, **bindings)
    if set(loaded_names) != set(selected):
        raise ValueError(f"{harness} loaded tools differ from selection: {sorted(loaded_names)}")
    return native
