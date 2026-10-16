"""Evaluator functions share (TrialContext, AgentResult) -> Score."""

from .contracts import AgentResult, Score, TrialContext
from environment import DockerEnvironment


async def skillsbench_verifier(context: TrialContext, agent: AgentResult) -> Score:
    if not isinstance(context.environment, DockerEnvironment):
        raise TypeError("The native SkillsBench verifier requires DockerEnvironment")
    rewards = await context.environment.verify()
    # Native verifier handles reward.txt, reward.json and declared aggregation.
    # A missing/invalid score raises; it must never become a model score of zero.
    if "reward" not in rewards:
        raise ValueError("Verifier rewards must contain an aggregate 'reward'")
    return Score(reward=rewards["reward"], metrics=rewards)
