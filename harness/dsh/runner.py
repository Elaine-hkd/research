"""Run native dsh with registry tools or a bridge to the current task environment."""

import asyncio
from collections import Counter
from dataclasses import asdict
import json
import math
import os
from pathlib import Path
import time
from uuid import uuid4

from harness.base import AgentResult, task_prompt
from harness.dsh.console import DshConsole
from tools.loader import REGISTRY_TOOLS, load_tools, validate_tools
from utils.json_io import append_jsonl, redact, write_json


def _trace_metrics(path: Path) -> tuple[int, dict]:
    attempts, calls = [], Counter()
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            row = json.loads(line)
            if row.get("event") == "model.attempt":
                attempts.append(row["data"].get("usage") or {})
            elif row.get("event") == "tool.call":
                calls[row["data"].get("name", "unknown")] += 1
    usage = {}
    for field in ("input_tokens", "output_tokens", "total_tokens", "cache_read_tokens",
                  "cache_write_tokens", "reasoning_tokens"):
        values = [attempt.get(field) for attempt in attempts]
        usage[field] = (sum(values) if values and all(
            isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
            for value in values) else None)
    # The current recorder exposes token usage, not an authoritative dollar cost.
    usage.update(cost_usd=None, tool_calls_by_name=dict(calls))
    return sum(calls.values()), usage


async def execute(*, instruction: str, model: dict,
                    environment=None, tools=None, output_dir: str | Path | None = None,
                    workspace: str | Path | None = None, timeout_sec: float | None = 900,
                    skill_catalog: list[dict[str, str]] | None = None,
                    recorder=None, on_notification=None, tool_timeout_sec: float = 120,
                    max_output_chars: int = 20000, **options) -> AgentResult:
    """The caller owns environment/recorder; this call owns bridge and SDK child.

    Tool limits are consumed here, never forwarded to DeepSeekHarnessConfig.
    With no environment, preserve the existing four registry-server tools.
    Native prompts and the dataset's existing task_prompt() remain unchanged.
    """
    if environment is not None and output_dir is None:
        raise ValueError("Container runs require output_dir for their isolated workspace")
    budget = timeout_sec
    deadline = time.monotonic() + budget if budget is not None else float("inf")
    selected = validate_tools(list(REGISTRY_TOOLS) if tools is None else tools)
    model_options = dict(model)
    key_name = model_options.pop("api_key_env")
    key = os.environ[key_name]
    if not key:
        raise ValueError(f"Empty model credential environment variable: {key_name}")
    prompt = task_prompt(instruction, skill_catalog)
    agent_dir = Path(output_dir) if output_dir is not None else None
    if agent_dir is not None:
        agent_dir.mkdir(parents=True, exist_ok=True)
        write_json(agent_dir / "tools.json", {"configured": selected})
    if workspace is None:
        workspace = agent_dir / "workspace" if agent_dir is not None else Path.cwd()
        if agent_dir is not None:
            workspace.mkdir()

    from deepseek_harness import DeepSeekHarness

    options.setdefault("dsh_home", str(Path(__file__).resolve().parents[2] / "config" / "dsh"))
    options.setdefault("profile", "sdk-minimal")
    result = AgentResult(final_response="", finish_reason="error", tool_calls=None)
    session_id = "session-" + uuid4().hex
    starting = running = failing = None
    harness = bridge = operations = None
    tool_snapshot = validation_error = None
    loop = asyncio.get_running_loop()
    console = DshConsole(
        max_output_chars=max_output_chars,
        event_log_path=agent_dir / "events.jsonl" if agent_dir is not None else None,
    )

    def sanitize(value):
        value = redact(value, key)
        return redact(value, bridge.token) if bridge is not None else value

    def on_event(notification):
        nonlocal tool_snapshot, validation_error
        if (notification.method == "session.event"
                and notification.payload.get("sessionId") == session_id):
            event = notification.payload.get("event", {})
            safe_event = sanitize(event)
            if agent_dir is not None:
                append_jsonl(agent_dir / "events.jsonl", safe_event)
            console.feed(safe_event)
            if event.get("type") == "request/header" and tool_snapshot is None:
                schemas = event.get("data", {}).get("header", {}).get("tools")
                actual = [tool.get("name") for tool in schemas if isinstance(tool, dict)] if isinstance(schemas, list) else []
                if (not isinstance(schemas, list) or len(actual) != len(schemas)
                        or not all(isinstance(name, str) for name in actual)
                        or len(actual) != len(set(actual)) or set(actual) != set(selected)):
                    validation_error = ValueError(f"dsh model-visible tools differ from configuration: {actual!r}")
                    if bridge is not None:
                        loop.call_soon_threadsafe(bridge.fail, validation_error)
                elif tool_snapshot is None:
                    tool_snapshot = {"configured": selected, "tools": schemas}
                    if agent_dir is not None:
                        write_json(agent_dir / "tools.json", sanitize(tool_snapshot))
        if on_notification is not None:
            on_notification(notification)

    try:
        if environment is not None:
            from tools.dsh.bridge import ToolBridge
            from tools.environment import EnvironmentOperations

            operations = EnvironmentOperations(
                environment, deadline=deadline, timeout=tool_timeout_sec,
                output_chars=max_output_chars, log_path=agent_dir / "tool-calls.jsonl",
            )
            bridge = ToolBridge(operations, selected)
            await bridge.start()
        tool_options = load_tools("dsh", selected, bridge=bridge)
        child_env = dict(options.pop("env", {}))
        child_env.update(tool_options["env"])
        if bridge is not None:
            allowed = bridge.registry.allowed if bridge.registry is not None else None
            child_env["EXPOSED_SKILLS"] = json.dumps(sorted(allowed) if allowed is not None else [], ensure_ascii=False)
        else:
            child_env.setdefault("EXPOSED_SKILLS", os.environ.get("EXPOSED_SKILLS", "[]"))
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            result.finish_reason = "timeout"
            raise TimeoutError("dsh agent budget exhausted before initialization")
        if budget is not None:
            options["initialize_timeout_seconds"] = min(options.get("initialize_timeout_seconds", 30), remaining)
        harness = DeepSeekHarness(
            **options, cwd=str(workspace), patches=tool_options["patches"], env=child_env,
            **model_options, api_key=key,
        )
        # Join initialization before shutdown, including when this coroutine is
        # cancelled, so a still-starting worker cannot launch after cleanup.
        starting = asyncio.create_task(asyncio.to_thread(harness.start))
        await asyncio.shield(starting)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            result.finish_reason = "timeout"
            raise TimeoutError(f"dsh exceeded the {budget}s agent budget during initialization")
        if recorder is None:
            running = asyncio.create_task(asyncio.to_thread(
                harness.run, prompt, session_id=session_id, on_notification=on_event,
            ))
        else:
            running = asyncio.create_task(asyncio.to_thread(
                recorder.run, harness, prompt, session_id=session_id, on_notification=on_event,
            ))
        waiting = [running]
        if bridge is not None:
            failing = asyncio.create_task(bridge.wait_failed())
            waiting.append(failing)
        done, _ = await asyncio.wait(waiting, timeout=remaining if budget is not None else None,
                                     return_when=asyncio.FIRST_COMPLETED)
        if not done:
            result.finish_reason = "timeout"
            raise TimeoutError(f"dsh exceeded the {budget}s agent budget")
        if bridge is not None and bridge.failure is not None:
            raise RuntimeError("dsh task tool channel failed") from bridge.failure
        native = await running
        if validation_error is not None:
            raise validation_error
        if operations is not None and operations.failure is not None:
            raise RuntimeError("Task container command channel failed") from operations.failure
        result.final_response = native.final_response
        calls = Counter(event.get("data", {}).get("name", "unknown")
                        for event in native.events if event.get("type") == "tool/call")
        result.tool_calls = sum(calls.values())
        result.usage["tool_calls_by_name"] = dict(calls)
        result.finish_reason = "finished" if native.finish_reason == "completed" else native.finish_reason or "error"
        if result.finish_reason not in {"finished", "stuck", "max_iterations"}:
            reason = next((event.get("data", {}).get("reason") for event in reversed(native.events)
                           if event.get("type") == "turn/end"), None)
            raise RuntimeError(f"dsh stopped with {result.finish_reason}: {sanitize(reason)}")
        if selected and tool_snapshot is None:
            raise RuntimeError("dsh did not report the configured model-visible tools")
        return result
    finally:
        if starting is not None:
            await asyncio.gather(starting, return_exceptions=True)
        try:
            # Join container work before releasing the evaluator-owned environment.
            try:
                if bridge is not None:
                    await bridge.close()
            finally:
                # SDK close wakes run() and reaps its runtime subprocess.
                if harness is not None:
                    await asyncio.to_thread(harness.close)
        finally:
            if failing is not None:
                failing.cancel()
                await asyncio.gather(failing, return_exceptions=True)
            if running is not None:
                await asyncio.gather(running, return_exceptions=True)
            if recorder is not None and recorder.last_path is not None:
                result.trace_path = str(recorder.last_path)
                result.tool_calls, result.usage = _trace_metrics(Path(recorder.last_path))
            result.usage["tool_names"] = selected
            if operations is not None:
                result.usage["shell_calls"] = operations.calls_by_tool["terminal"]
                result.usage["environment_calls"] = operations.calls
            if agent_dir is not None:
                write_json(agent_dir / "result.json", sanitize(asdict(result)))
