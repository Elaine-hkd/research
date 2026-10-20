"""Adapt tool selection and a trial's bridge to dsh plugin launch options."""

import json
from pathlib import Path

from tools.loader import REGISTRY_TOOLS


def create_tools(names, *, bridge=None) -> tuple[dict, set[str]]:
    supported = set(REGISTRY_TOOLS) | {"terminal", "read_file", "write_file"}
    if unknown := set(names) - supported:
        raise ValueError(f"No dsh tool implementation for: {sorted(unknown)}")
    if bridge is None and set(names) - set(REGISTRY_TOOLS):
        raise ValueError("dsh terminal/file tools require a bound task environment")
    env = {"HARNESS_TOOLS": json.dumps(names, ensure_ascii=False),
           "HARNESS_BRIDGE_URL": "", "HARNESS_BRIDGE_TOKEN": ""}
    if bridge is not None:
        if bridge.url is None:
            raise ValueError("Start the trial's bridge before loading dsh tools")
        env.update(HARNESS_BRIDGE_URL=bridge.url, HARNESS_BRIDGE_TOKEN=bridge.token)
    return {
        "patches": (str(Path(__file__).with_name("registry.patch.yml")),),
        "env": env,
    }, set(names)
