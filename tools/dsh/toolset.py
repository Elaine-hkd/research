"""将工具选择和试验桥接接口适配为 dsh 插件启动选项。"""

import json
from pathlib import Path

from tools.loader import REGISTRY_TOOLS


def create_tools(names, *, bridge=None) -> tuple[dict, set[str]]:
    supported = set(REGISTRY_TOOLS) | {"terminal", "read_file", "write_file"}
    if unknown := set(names) - supported:
        raise ValueError(f"以下工具没有 dsh 实现：{sorted(unknown)}")
    if bridge is None and set(names) - set(REGISTRY_TOOLS):
        raise ValueError("dsh 终端和文件工具需要绑定任务环境")
    env = {"HARNESS_TOOLS": json.dumps(names, ensure_ascii=False),
           "HARNESS_BRIDGE_URL": "", "HARNESS_BRIDGE_TOKEN": ""}
    if bridge is not None:
        if bridge.url is None:
            raise ValueError("加载 dsh 工具前，请先启动本次试验的桥接接口")
        env.update(HARNESS_BRIDGE_URL=bridge.url, HARNESS_BRIDGE_TOKEN=bridge.token)
    return {
        "patches": (str(Path(__file__).with_name("registry.patch.yml")),),
        "env": env,
    }, set(names)
