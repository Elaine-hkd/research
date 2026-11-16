"""公共智能体入口；SDK 执行仍由所选 harness 负责。"""

from contextlib import nullcontext
from datetime import datetime
from importlib import import_module
import math
import os
from pathlib import Path
from uuid import uuid4

from harness.base import AgentResult
from harness.diagnostics import RunDiagnostics, preserve_recording_errors
from tools.loader import validate_tools
from utils.json_io import redact


def create_run_directory(root: str | Path) -> Path:
    """在指定根目录下创建本次运行目录，返回其绝对路径。"""
    run_name = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S-%f") + "-" + uuid4().hex[:8]
    output_dir = Path(root).resolve() / run_name
    output_dir.mkdir(parents=True)
    return output_dir


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
    settings 中的 cost_tracking 是费用统计开关，省略时关闭，其余选项交给原生 runner。
    """
    options = dict(settings)
    if "usage" in options:
        raise ValueError("费用统计开关已改名，请删除 usage 并设置 cost_tracking = true/false")
    cost_tracking = options.pop("cost_tracking", False)
    if type(cost_tracking) is not bool:
        raise TypeError("cost_tracking 必须为 true/false 布尔开关")
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
    accounting = None
    if cost_tracking:
        if recorder is None or output_dir is None:
            raise ValueError("启用 cost_tracking 需要 recorder 和 output_dir，才能逐次统计模型费用")
        from harness.usage import prepare_usage

        accounting = prepare_usage(model=model, output_dir=Path(output_dir))
    recording = nullcontext(recorder)
    if isinstance(recorder, str):
        if output_dir is None:
            raise ValueError("配置记录器时必须提供 output_dir，也可以直接传入记录器实例")
        recording = load_function(recorder)(Path(output_dir) / "trace", metadata=metadata)
    # 调用方传入的实例，其生命周期仍由调用方管理。
    with preserve_recording_errors(recording) as active_recorder:
        result = None
        key = os.environ.get(model.get("api_key_env", ""), "")
        diagnostics = RunDiagnostics(
            Path(output_dir) if output_dir is not None else None,
            sanitize=lambda value: redact(value, key) if key else value,
        )
        # 外部持有的 recorder 可以串行复用，本次汇总只读取新追加的完整记录。
        start_offsets = ({Path(path).resolve(): Path(path).stat().st_size
                          for path in getattr(active_recorder, "paths", {}).values()}
                         if accounting is not None else {})
        try:
            result = await execute(
                instruction=instruction, model=model, tools=tools, environment=environment,
                output_dir=output_dir, workspace=workspace, timeout_sec=budget,
                skill_catalog=skill_catalog, recorder=active_recorder, **options,
            )
            if not isinstance(result, AgentResult):
                result = None
                raise TypeError("Harness 执行必须返回 harness.base.AgentResult")
        except BaseException as error:
            # 原生 runner 已负责首次失败的归档，这里只保留它来附加后续入账错误。
            diagnostics.failure = error
        if accounting is not None:
            from harness.usage import save_usage

            with diagnostics.stage("usage.import"):
                save_usage(accounting, active_recorder, model=model, metadata=metadata,
                           output_dir=Path(output_dir), result=result, start_offsets=start_offsets)
        if diagnostics.failure is not None:
            raise diagnostics.failure
        return result
