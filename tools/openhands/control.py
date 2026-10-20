"""Locally defined thought and completion tools, without SDK default tools."""

from typing import ClassVar

from openhands.sdk import Action, Observation, ToolDefinition
from openhands.sdk.tool import ToolExecutor
from pydantic import Field


class LocalThinkAction(Action):
    thought: str = Field(description="A thought to record without changing the task environment")


class LocalFinishAction(Action):
    message: str = Field(description="Final answer with the task outcome and any remaining limitations")


class ControlObservation(Observation):
    """Concrete schema for the SDK's observation discriminator."""


class ThinkExecutor(ToolExecutor[LocalThinkAction, ControlObservation]):
    def __call__(self, action: LocalThinkAction, conversation=None) -> ControlObservation:
        return ControlObservation.from_text("Thought recorded.")


class FinishExecutor(ToolExecutor[LocalFinishAction, ControlObservation]):
    def __call__(self, action: LocalFinishAction, conversation=None) -> ControlObservation:
        return ControlObservation.from_text(action.message)


class LocalThinkTool(ToolDefinition[LocalThinkAction, ControlObservation]):
    name: ClassVar[str] = "think"

    @classmethod
    def create(cls, conv_state=None, **params):
        return [cls(
            description="Record a thought or plan. This tool has no environment side effects.",
            action_type=LocalThinkAction, observation_type=ControlObservation,
            executor=ThinkExecutor(),
        )]


class LocalFinishTool(ToolDefinition[LocalFinishAction, ControlObservation]):
    # SDK 1.53.0 ends an action batch by tool name. Keep this name so the SDK
    # handles completion; the executor does not mutate conversation state.
    name: ClassVar[str] = "finish"

    @classmethod
    def create(cls, conv_state=None, **params):
        return [cls(
            description="Finish the task and send the final message to the user.",
            action_type=LocalFinishAction, observation_type=ControlObservation,
            executor=FinishExecutor(),
        )]
