"""Run one OpenHands task with explicitly selected local tool implementations."""

import asyncio
from collections import Counter
from dataclasses import asdict
import math
import os
from pathlib import Path
import time

from environment.base import Environment
from harness.base import AgentResult, task_prompt
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
    """Execute a task without depending on a dataset or evaluation context.

    tools is an explicit list of shared tool names. Pass environment to bind
    environment tools to the task container. Standalone callers may also pass
    existing SDK Tool specs. Native run options are ordinary keyword arguments.
    The caller owns environment and recorder lifetimes. A recorder uses its
    synchronous run API in a worker thread; the default path uses SDK arun().
    output_dir enables native event/result artifacts; timeout_sec=None disables
    the overall agent deadline. The common entry point resolves its override.
    System prompts are supplied by the SDK defaults.
    """
    from openhands.sdk import Agent, Conversation, LLM
    from openhands.sdk.tool import Tool
    from tools.loader import ENVIRONMENT_TOOLS, load_tools

    # SDK conversation arguments and our shell limits lack these value checks.
    for name, value in (("max_iterations", max_iterations), ("max_output_chars", max_output_chars)):
        if type(value) is not int or value < 1:
            raise ValueError(f"{name} must be a positive integer")
    if (isinstance(tool_timeout_sec, bool) or not isinstance(tool_timeout_sec, (int, float))
            or not math.isfinite(tool_timeout_sec) or tool_timeout_sec <= 0):
        raise ValueError("tool_timeout_sec must be a positive finite number")

    if environment is None and tools is None:
        raise ValueError("Pass environment or explicit tools")
    if environment is not None and output_dir is None:
        raise ValueError("Container runs require output_dir for their isolated workspace")
    selected = list(ENVIRONMENT_TOOLS) if tools is None else tools
    if not isinstance(selected, (list, tuple)):
        raise TypeError("tools must be a list of tool names or SDK Tool specs")
    by_name = all(isinstance(tool, str) for tool in selected)
    if not by_name and (environment is not None or not all(isinstance(tool, Tool) for tool in selected)):
        raise TypeError("Container tools require tool names; standalone tools may use SDK Tool specs")

    model_kwargs = dict(model)
    key_name = model_kwargs.pop("api_key_env")
    key = os.environ[key_name]
    if not key:
        raise ValueError(f"Empty model credential environment variable: {key_name}")
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
    error_codes = []
    calls_by_tool = Counter()
    tool_snapshot = None

    def check_tools():
        nonlocal tool_snapshot
        actual = set(agent.tools_map)
        if expected_names is not None and actual != expected_names:
            raise ValueError(
                f"OpenHands tool list differs from configuration: "
                f"extra={sorted(actual - expected_names)}, missing={sorted(expected_names - actual)}"
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
        # SDK events are useful for analysis; redact the injected credential even
        # when a provider embeds it in an exception message.
        raw = redact(raw, key)
        if agent_dir is not None:
            append_jsonl(agent_dir / "events.jsonl", raw)
            event_type = raw.get("kind", type(event).__name__)
            print(f"  [OpenHands] {event_type} {raw.get('tool_name', '')}".rstrip(), flush=True)
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
            # Keep evaluation workspaces separate from dataset/oracle files.
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
    running = None
    try:
        if recorder is None:
            conversation.send_message(prompt)
            check_tools()
            running = asyncio.create_task(conversation.arun())
        else:
            # The recorder owns send_message and telemetry/retry hooks. Calling
            # arun() directly would bypass its run/turn/model-attempt records.
            running = asyncio.create_task(asyncio.to_thread(recorder.run, conversation, prompt))
        done, _ = await asyncio.wait([running], timeout=budget)
        if not done:
            # A synchronous run may hold SDK state locks. Keep the event loop
            # free while requesting interruption, then join before closing it.
            await asyncio.to_thread(conversation.interrupt)
            await running
            result.finish_reason = "timeout"
            raise TimeoutError(f"OpenHands exceeded the {budget}s agent budget")
        await running
        if executor is not None and executor.failure is not None:
            raise RuntimeError("Task container command channel failed") from executor.failure
        status = conversation.state.execution_status.value
        result.finish_reason = "max_iterations" if "MaxIterationsReached" in error_codes else status
        if result.finish_reason not in {"finished", "stuck", "max_iterations"}:
            raise RuntimeError(f"OpenHands stopped with {status}: {error_codes}")
        return result
    finally:
        if running is not None and not running.done():
            await asyncio.to_thread(conversation.interrupt)
            await asyncio.gather(running, return_exceptions=True)
        if executor is not None:
            await executor.drain()
        metrics = llm.metrics
        tokens = metrics.accumulated_token_usage.model_dump(mode="json") if metrics.token_usages else None
        result.usage = {
            "tokens": tokens,  # Preserve the SDK representation for existing callers.
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
        if recorder is not None and recorder.last_path is not None:
            result.trace_path = str(recorder.last_path)
        if executor is not None:
            result.usage["shell_calls"] = executor.calls_by_tool["terminal"]
            result.usage["environment_calls"] = executor.calls
        try:
            if agent_dir is not None:
                write_json(agent_dir / "result.json", redact(asdict(result), key))
        finally:
            conversation.close()
