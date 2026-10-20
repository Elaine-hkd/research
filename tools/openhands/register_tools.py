from tools.loader import REGISTRY_TOOLS, load_tools


def get_registry_tools():
    return load_tools("openhands", [*REGISTRY_TOOLS, "think", "finish"])
