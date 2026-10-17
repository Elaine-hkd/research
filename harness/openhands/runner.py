"""Run one OpenHands task with container tools or caller-supplied tools."""

import asyncio
from dataclasses import asdict
import json
import os
from pathlib import Path
import time
from uuid import uuid4

from environment.base import Environment
from harness.base import AgentResult
from utils.json_io import append_jsonl, redact, write_json


async def run_agent(*, instruction: str, model: dict, settings: dict,
                    environment: Environment | None = None, tools=None,
                    output_dir: str | Path | None = None,
                    workspace: str | Path | None = None,
                    timeout_sec: float | None = 900,
                    skill_catalog: list[dict[str, str]] | None = None,
                    recorder=None) -> AgentResult:
    """Execute a task without depending on a dataset or evaluation context.

    Pass either environment (bind a shell) or tools (e.g. the registry tool set).
    The caller owns environment and recorder lifetimes. A recorder uses its
    synchronous run API in a worker thread; the default path uses SDK arun().
    output_dir enables native event/result artifacts; timeout_sec=None disables
    the overall agent deadline. settings may override timeout_sec.
    System prompts are supplied by the SDK defaults.
    """
    from openhands.sdk import Agent, Conversation, LLM
    from openhands.sdk.tool import Tool, register_tool

    if (environment is None) == (tools is None):
        raise ValueError("Pass either environment or tools, but not both")
    if environment is not None and output_dir is None:
        raise ValueError("Container runs require output_dir for their isolated workspace")

    model_kwargs = dict(model)
    key_name = model_kwargs.pop("api_key_env")
    key = os.environ[key_name]
    if not key:
        raise ValueError(f"Empty model credential environment variable: {key_name}")
    llm = LLM(**model_kwargs, api_key=key)
    agent_dir = Path(output_dir) if output_dir is not None else None
    if agent_dir is not None:
        agent_dir.mkdir(parents=True, exist_ok=True)
    budget = settings.get("timeout_sec", timeout_sec)
    executor = None
    agent_kwargs = {"llm": llm, "tools": tools, "tool_concurrency_limit": 1}
    if environment is not None:
        from tools.openhands.environment_shell import EnvironmentShellTool, ShellExecutor

        executor = ShellExecutor(
            environment, asyncio.get_running_loop(),
            deadline=time.monotonic() + budget if budget is not None else float("inf"),
            timeout=settings["tool_timeout_sec"], output_chars=settings["max_output_chars"],
            log_path=agent_dir / "tool-calls.jsonl",
        )
        # Specs remain serializable; only executor holds the live environment.
        binding_name = "evaluation_shell_" + uuid4().hex
        register_tool(binding_name, EnvironmentShellTool.create(executor=executor)[0])
        agent_kwargs["tools"] = [Tool(name=binding_name)]
        agent_kwargs["include_default_tools"] = ["FinishTool", "ThinkTool"]
    agent = Agent(**agent_kwargs)
    result = AgentResult(final_response="", finish_reason="error")
    error_codes = []

    def check_container_tools():
        if environment is not None:
            unexpected = set(agent.tools_map) - {"evaluation_shell", "finish", "think"}
            if unexpected:
                raise ValueError(f"OpenHands added tools outside the task container: {sorted(unexpected)}")

    def on_event(event):
        if type(event).__name__ == "SystemPromptEvent":
            check_container_tools()
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
        "max_iteration_per_run": settings.get("max_iterations", 500),
    }
    if recorder is None:
        conversation_kwargs["visualizer"] = None
        conversation = Conversation(**conversation_kwargs)
    else:
        conversation = recorder.create_conversation(**conversation_kwargs)
    prompt = instruction
    if skill_catalog:
        prompt += (
            "\n\nAvailable task skills (read SKILL.md before using a skill):\n"
            + json.dumps([{key: skill[key] for key in ("name", "description", "path")}
                          for skill in skill_catalog], ensure_ascii=False, indent=2)
        )
    running = None
    try:
        if recorder is None:
            conversation.send_message(prompt)
            check_container_tools()
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
        }
        if recorder is not None and recorder.last_path is not None:
            result.trace_path = str(recorder.last_path)
        if executor is not None:
            result.usage["shell_calls"] = executor.calls
        try:
            if agent_dir is not None:
                write_json(agent_dir / "result.json", redact(asdict(result), key))
        finally:
            conversation.close()
