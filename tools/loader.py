"""共用的工具选择与适配器加载逻辑，不依赖 harness SDK。"""

from importlib import import_module
import re


REGISTRY_TOOLS = ("search_skills", "load_skill", "load_resource", "execute_resource")
ENVIRONMENT_TOOLS = ("terminal", "think", "finish")


def validate_tools(tools) -> list[str]:
    if (not isinstance(tools, (list, tuple))
            or any(not isinstance(name, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name)
                   for name in tools)
            or len(tools) != len(set(tools))):
        raise ValueError("tools 必须为不含重复项的工具名称列表")
    return list(tools)


def load_tools(harness: str, tools, **bindings):
    """仅加载所选 harness 的适配器，并返回其原生工具。

    适配器实现 create_tools(names, **bindings) -> (native_value, names)。
    OpenHands 返回 SDK Tool 定义；dsh 返回构造参数 patches/env。
    bindings 提供试验执行器等运行中的资源，不承载配置。
    """
    if not isinstance(harness, str) or not re.fullmatch(r"[a-z][a-z0-9_]*", harness):
        raise ValueError("harness 必须为有效的适配器名称")
    selected = validate_tools(tools)
    adapter = import_module(f"tools.{harness}.toolset")
    native, loaded_names = adapter.create_tools(selected, **bindings)
    if set(loaded_names) != set(selected):
        raise ValueError(f"{harness} 加载的工具与选择不一致：{sorted(loaded_names)}")
    return native
