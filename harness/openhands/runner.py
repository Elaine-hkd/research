"""使用显式选择的本地工具实现运行一个 OpenHands 任务。"""

import asyncio
from collections import Counter
import math
import os
from pathlib import Path
import time

from environment.base import Environment
from harness.base import AgentResult, task_prompt
from harness.diagnostics import RunDiagnostics
from utils.json_io import append_jsonl, redact, write_json


async def execute(*, instruction: str, model: dict,
                    environment: Environment | None = None, tools=None,
                    output_dir: str | Path | None = None,
                    workspace: str | Path | None = None,
                    timeout_sec: float | None = 900,
                    skill_catalog: list[dict[str, str]] | None = None,
                    recorder=None, max_iterations: int = 500,
                    tool_timeout_sec: float = 120,
                    max_output_chars: int = 20000) -> AgentResult:
    """执行任务，不依赖数据集或评估上下文。

    tools 为显式指定的共用工具名称列表；传入 environment 可将环境工具
    绑定到任务容器。独立调用方也可传入已有的 SDK Tool 定义。
    原生运行选项通过普通关键字参数传入。
    环境和记录器的生命周期由调用方管理。记录器在线程中使用同步 run API，
    默认流程使用 SDK 的 arun()。output_dir 启用原生事件和结果产物；
    timeout_sec=None 禁用智能体总时限，其覆盖值由公共入口解析。
    系统提示词使用 SDK 默认值。
    """
    from openhands.sdk import Agent, Conversation, LLM
    from openhands.sdk.tool import Tool
    from tools.loader import ENVIRONMENT_TOOLS, load_tools

    # SDK 会话参数和本地 shell 限制缺少这些值校验。
    for name, value in (("max_iterations", max_iterations), ("max_output_chars", max_output_chars)):
        if type(value) is not int or value < 1:
            raise ValueError(f"{name} 必须为正整数")
    if (isinstance(tool_timeout_sec, bool) or not isinstance(tool_timeout_sec, (int, float))
            or not math.isfinite(tool_timeout_sec) or tool_timeout_sec <= 0):
        raise ValueError("tool_timeout_sec 必须为有限正数")

    if environment is None and tools is None:
        raise ValueError("请传入 environment 或显式指定 tools")
    if environment is not None and output_dir is None:
        raise ValueError("容器运行需要 output_dir 来存放独立工作区")
    selected = list(ENVIRONMENT_TOOLS) if tools is None else tools
    if not isinstance(selected, (list, tuple)):
        raise TypeError("tools 必须为工具名称列表或 SDK Tool 定义列表")
    by_name = all(isinstance(tool, str) for tool in selected)
    if not by_name and (environment is not None or not all(isinstance(tool, Tool) for tool in selected)):
        raise TypeError("容器工具需使用工具名称；独立工具可使用 SDK Tool 定义")

    model_kwargs = dict(model)
    key_name = model_kwargs.pop("api_key_env")
    key = os.environ[key_name]
    if not key:
        raise ValueError(f"模型凭据环境变量为空：{key_name}")
    llm = LLM(**model_kwargs, api_key=key)
    agent_dir = Path(output_dir) if output_dir is not None else None
    if agent_dir is not None:
        agent_dir.mkdir(parents=True, exist_ok=True)
    budget = timeout_sec
    executor = None
    if environment is not None:
        from tools.openhands.terminal import ShellExecutor

        executor = ShellExecutor(
            environment, asyncio.get_running_loop(),
            deadline=time.monotonic() + budget if budget is not None else float("inf"),
            timeout=tool_timeout_sec, output_chars=max_output_chars,
            log_path=agent_dir / "tool-calls.jsonl",
        )
    expected_names = set(selected) if by_name else None
    tool_specs = load_tools("openhands", selected, executor=executor) if by_name else list(selected)
    agent = Agent(llm=llm, tools=tool_specs, tool_concurrency_limit=1, include_default_tools=[])
    result = AgentResult(final_response="", finish_reason="error")
    diagnostics = RunDiagnostics(agent_dir, sanitize=lambda value: redact(value, key))
    error_codes = []
    calls_by_tool = Counter()
    tool_snapshot = None

    def check_tools():
        nonlocal tool_snapshot
        actual = set(agent.tools_map)
        if expected_names is not None and actual != expected_names:
            raise ValueError(
                f"OpenHands 工具列表与配置不一致："
                f"多余项={sorted(actual - expected_names)}，缺失项={sorted(expected_names - actual)}"
            )
        if tool_snapshot is None:
            tool_snapshot = {
                "configured": selected if by_name else None,
                "tools": [{
                    "name": tool.name,
                    "implementation": f"{type(tool).__module__}:{type(tool).__name__}",
                    "schema": tool.to_openai_tool(),
                } for tool in agent.tools_map.values()],
            }
            if agent_dir is not None:
                write_json(agent_dir / "tools.json", redact(tool_snapshot, key))

    def on_event(event):
        if type(event).__name__ == "SystemPromptEvent":
            check_tools()
        raw = event.model_dump(mode="json")
        # SDK 事件有助于分析；即使服务提供方将注入的凭据
        # 嵌入异常信息，也必须将其脱敏。
        raw = redact(raw, key)
        if agent_dir is not None:
            append_jsonl(agent_dir / "events.jsonl", raw)
            event_type = raw.get("kind", type(event).__name__)
            print(f"  [OpenHands 事件] {event_type} {raw.get('tool_name', '')}".rstrip(), flush=True)
        if raw.get("code"):
            error_codes.append(raw["code"])
        if type(event).__name__ == "ActionEvent":
            result.tool_calls += 1
            calls_by_tool[raw.get("tool_name", "unknown")] += 1
        action = raw.get("action") or {}
        if raw.get("tool_name") == "finish" and isinstance(action.get("message"), str):
            result.final_response = action["message"]
        elif raw.get("source") == "agent" and raw.get("llm_message"):
            result.final_response = "".join(
                block.get("text", "") for block in raw["llm_message"].get("content", [])
                if block.get("type") == "text"
            )

    if workspace is None:
        if agent_dir is not None:
            # 评估工作区必须与数据集及参考答案文件分离。
            workspace = agent_dir / "workspace"
            workspace.mkdir()
        else:
            workspace = Path.cwd()
    conversation_kwargs = {
        "agent": agent, "workspace": workspace, "callbacks": [on_event],
        "max_iteration_per_run": max_iterations,
    }
    prompt = task_prompt(instruction, skill_catalog)
    if recorder is None:
        conversation_kwargs["visualizer"] = None
        conversation = Conversation(**conversation_kwargs)
    else:
        conversation = recorder.create_conversation(**conversation_kwargs)
    session_id = str(conversation.id)
    running = None
    try:
        if recorder is None:
            conversation.send_message(prompt)
            check_tools()
            running = asyncio.create_task(conversation.arun())
        else:
            # 记录器负责 send_message 及遥测、重试钩子；直接调用
            # arun() 会绕过其运行、轮次和模型尝试记录。
            running = asyncio.create_task(asyncio.to_thread(recorder.run, conversation, prompt))
        done, _ = await asyncio.wait([running], timeout=budget)
        if not done:
            # 在中断或清理可能失败前，先保存时间预算耗尽的异常。
            result.finish_reason = "timeout"
            raise TimeoutError(f"OpenHands 超过了 {budget} 秒的智能体时间预算")
        await running
        if executor is not None and executor.failure is not None:
            raise RuntimeError("任务容器命令通道失败") from executor.failure
        status = conversation.state.execution_status.value
        result.finish_reason = "max_iterations" if "MaxIterationsReached" in error_codes else status
        if result.finish_reason not in {"finished", "stuck", "max_iterations"}:
            raise RuntimeError(f"OpenHands 已停止，状态：{status}: {error_codes}")
    except BaseException as exc:
        # 工具通道失败后，SDK 可能抛出异常或返回暂停状态。
        # 若其他 SDK 异常掩盖了工具错误，优先使用已保存的工具异常。
        tool_failure = executor.failure if executor is not None else None
        if tool_failure is not None and exc is not tool_failure and exc.__cause__ is not tool_failure:
            diagnostics.remember(tool_failure, "tool")
        diagnostics.remember(exc, "run")
    finally:
        if running is not None and not running.done():
            with diagnostics.stage("conversation.interrupt"):
                # SDK 的同步状态锁不能阻塞环境事件循环。
                await asyncio.to_thread(conversation.interrupt)
            with diagnostics.stage("run.join"):
                await running
        with diagnostics.stage("executor.drain"):
            if executor is not None:
                await executor.drain()
        with diagnostics.stage("metrics"):
            metrics = llm.metrics
            tokens = metrics.accumulated_token_usage.model_dump(mode="json") if metrics.token_usages else None
            result.usage = {
                "tokens": tokens,  # 为已有调用方保留 SDK 的原始表示。
                "input_tokens": tokens.get("prompt_tokens") if tokens else None,
                "output_tokens": tokens.get("completion_tokens") if tokens else None,
                "total_tokens": tokens.get("total_tokens") if tokens else None,
                "cache_read_tokens": tokens.get("cache_read_tokens") if tokens else None,
                "cache_write_tokens": tokens.get("cache_write_tokens") if tokens else None,
                "reasoning_tokens": tokens.get("reasoning_tokens") if tokens else None,
                "cost_usd": metrics.accumulated_cost if metrics.costs else None,
                "tool_names": list(agent.tools_map),
                "tool_calls_by_name": dict(calls_by_tool),
            }
            if executor is not None:
                result.usage["shell_calls"] = executor.calls_by_tool["terminal"]
                result.usage["environment_calls"] = executor.calls
        with diagnostics.stage("conversation.close"):
            conversation.close()
    return diagnostics.finish(result, recorder=recorder, session_id=session_id)
