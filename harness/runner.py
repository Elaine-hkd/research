"""公共智能体入口；SDK 执行仍由所选 harness 负责。"""

from contextlib import nullcontext
from importlib import import_module
import math
from pathlib import Path

from harness.base import AgentResult
from harness.diagnostics import preserve_recording_errors
from tools.loader import validate_tools


def load_function(reference: str):
    module_name, separator, attribute = reference.partition(":")
    if not separator or not module_name or not attribute:
        raise ValueError(f"需要 module:function 格式，实际收到 {reference!r}")
    function = getattr(import_module(module_name), attribute)
    if not callable(function):
        raise TypeError(f"配置的组件不可调用：{reference}")
    return function


async def run_agent(*, runner, instruction: str, model: dict, settings: dict,
                    tools=None, environment=None, output_dir: str | Path | None = None,
                    workspace: str | Path | None = None, timeout_sec: float | None = 900,
                    skill_catalog: list[dict[str, str]] | None = None,
                    metadata: dict | None = None, recorder=None) -> AgentResult:
    """解析执行和记录组件，并校验共用运行参数。

    recorder 可为 module:class 引用、由外部持有的实例，或用 None 禁用记录。
    配置创建的记录器使用 output_dir/trace，必须支持上下文管理器及
    所选 harness 的原生记录器 API。
    settings 保留为原生选项的扁平集合，不添加参数模式。
    """
    options = dict(settings)
    budget = options.pop("timeout_sec", timeout_sec)
    if budget is not None and (isinstance(budget, bool) or not isinstance(budget, (int, float))
                               or not math.isfinite(budget) or budget <= 0):
        raise ValueError("timeout_sec 必须为有限正数或 None")
    if tools is not None:
        if not isinstance(tools, (list, tuple)):
            raise TypeError("tools 必须为工具名称列表或原生工具定义列表")
        if all(isinstance(tool, str) for tool in tools):
            tools = validate_tools(tools)
    execute = load_function(runner) if isinstance(runner, str) else runner
    recording = nullcontext(recorder)
    if isinstance(recorder, str):
        if output_dir is None:
            raise ValueError("配置记录器时必须提供 output_dir，也可以直接传入记录器实例")
        recording = load_function(recorder)(Path(output_dir) / "trace", metadata=metadata)
    # 调用方传入的实例，其生命周期仍由调用方管理。
    with preserve_recording_errors(recording) as active_recorder:
        result = await execute(
            instruction=instruction, model=model, tools=tools, environment=environment,
            output_dir=output_dir, workspace=workspace, timeout_sec=budget,
            skill_catalog=skill_catalog, recorder=active_recorder, **options,
        )
        if not isinstance(result, AgentResult):
            raise TypeError("Harness 执行必须返回 harness.base.AgentResult")
        return result
