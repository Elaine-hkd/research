"""Translate one evaluation trial into arguments for the OpenHands harness."""

from harness.openhands.runner import run_agent as run_openhands_agent

from .contracts import AgentResult, TrialContext


async def run_agent(context: TrialContext, model: dict, settings: dict) -> AgentResult:
    return await run_openhands_agent(
        instruction=context.task.instruction,
        environment=context.environment,
        model=model,
        settings=settings,
        output_dir=context.output_dir / "agent",
        timeout_sec=context.task.agent_timeout_sec,
        skill_catalog=context.prepared.skill_catalog,
    )
