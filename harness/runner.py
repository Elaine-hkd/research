"""Common agent entry point; SDK execution stays in the selected harness."""

from contextlib import nullcontext
from importlib import import_module
import math
from pathlib import Path

from harness.base import AgentResult
from tools.loader import validate_tools


def load_function(reference: str):
    module_name, separator, attribute = reference.partition(":")
    if not separator or not module_name or not attribute:
        raise ValueError(f"Expected module:function, received {reference!r}")
    function = getattr(import_module(module_name), attribute)
    if not callable(function):
        raise TypeError(f"Configured component is not callable: {reference}")
    return function


async def run_agent(*, runner, instruction: str, model: dict, settings: dict,
                    tools=None, environment=None, output_dir: str | Path | None = None,
                    workspace: str | Path | None = None, timeout_sec: float | None = 900,
                    skill_catalog: list[dict[str, str]] | None = None,
                    metadata: dict | None = None, recorder=None) -> AgentResult:
    """Resolve execution/recording components and check shared run arguments.

    recorder may be a module:class reference, an externally owned instance, or
    None to disable recording. Configured recorders use output_dir/trace and must
    support a context manager plus the selected harness's native recorder API.
    settings remains a flat bag of native options; no parameter schema is added.
    """
    options = dict(settings)
    budget = options.pop("timeout_sec", timeout_sec)
    if budget is not None and (isinstance(budget, bool) or not isinstance(budget, (int, float))
                               or not math.isfinite(budget) or budget <= 0):
        raise ValueError("timeout_sec must be a positive finite number or None")
    if tools is not None:
        if not isinstance(tools, (list, tuple)):
            raise TypeError("tools must be a list of tool names or native tool specs")
        if all(isinstance(tool, str) for tool in tools):
            tools = validate_tools(tools)
    execute = load_function(runner) if isinstance(runner, str) else runner
    recording = nullcontext(recorder)
    if isinstance(recorder, str):
        if output_dir is None:
            raise ValueError("A configured recorder requires output_dir; alternatively pass a recorder instance")
        recording = load_function(recorder)(Path(output_dir) / "trace", metadata=metadata)
    # Caller-provided instances retain their caller-owned lifetime.
    with recording as active_recorder:
        result = await execute(
            instruction=instruction, model=model, tools=tools, environment=environment,
            output_dir=output_dir, workspace=workspace, timeout_sec=budget,
            skill_catalog=skill_catalog, recorder=active_recorder, **options,
        )
        if not isinstance(result, AgentResult):
            raise TypeError("Harness execution must return harness.base.AgentResult")
        return result
