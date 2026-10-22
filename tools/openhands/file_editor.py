"""在当前任务容器内实现文本文件工具，不在宿主机上操作。"""

from typing import ClassVar

from openhands.sdk import Action, Observation, ToolDefinition
from openhands.sdk.tool import ToolExecutor
from pydantic import Field

from tools.environment import file_command
from .terminal import ShellAction


class ReadFileAction(Action):
    path: str = Field(min_length=1, description="UTF-8 file path inside the task container")
    start_line: int = Field(default=1, ge=1, description="First line to read, numbered from 1")
    max_lines: int = Field(default=200, ge=1, le=2000)


class WriteFileAction(Action):
    path: str = Field(min_length=1, description="UTF-8 file path inside the task container")
    content: str = Field(description="Complete new file contents; overwrites an existing file")


class ReplaceTextAction(Action):
    path: str = Field(min_length=1, description="UTF-8 file path inside the task container")
    old_text: str = Field(min_length=1, description="Exact text to replace, including whitespace")
    new_text: str = Field(description="Replacement text; empty string deletes the matched text")
    expected_occurrences: int = Field(default=1, ge=1, description="Require exactly this many matches before writing")


class FileObservation(Observation):
    """使用与 shell 工具相同的限制显示命令结果。"""


class FileExecutor(ToolExecutor):
    def __init__(self, channel, operation: str):
        if channel is None:
            raise ValueError(f"{operation} 需要绑定任务环境")
        self.channel = channel
        self.operation = operation

    def __call__(self, action, conversation=None) -> FileObservation:
        arguments = action.model_dump(mode="json")
        # JSON 仅作为 argv 中的数据，不作为可执行源码或 shell 插值。
        command = file_command(self.operation, arguments)
        observation = self.channel.run_command(
            ShellAction(command=command, timeout_sec=self.channel.timeout),
            conversation, tool_name=self.operation, arguments=arguments,
        )
        return FileObservation.from_text(observation.text, is_error=observation.is_error)

    def interrupt(self) -> None:
        # 仅含文件操作的工具集也需要取消共用的环境 I/O。
        self.channel.interrupt()

    def close(self) -> None:
        self.channel.close()


class ReadFileTool(ToolDefinition[ReadFileAction, FileObservation]):
    name: ClassVar[str] = "read_file"

    @classmethod
    def create(cls, conv_state=None, *, executor=None):
        return [cls(
            description=("Read numbered lines from a UTF-8 file in the task container. "
                         "Relative paths use image WORKDIR; page with start_line and max_lines."),
            action_type=ReadFileAction, observation_type=FileObservation,
            executor=FileExecutor(executor, cls.name),
        )]


class WriteFileTool(ToolDefinition[WriteFileAction, FileObservation]):
    name: ClassVar[str] = "write_file"

    @classmethod
    def create(cls, conv_state=None, *, executor=None):
        return [cls(
            description=("Write a complete UTF-8 file in the task container, creating parent directories. "
                         "Overwrites existing contents. Relative paths use image WORKDIR."),
            action_type=WriteFileAction, observation_type=FileObservation,
            executor=FileExecutor(executor, cls.name),
        )]


class ReplaceTextTool(ToolDefinition[ReplaceTextAction, FileObservation]):
    name: ClassVar[str] = "replace_text"

    @classmethod
    def create(cls, conv_state=None, *, executor=None):
        return [cls(
            description=("Replace exact text in a UTF-8 file in the task container. "
                         "Rejects a mismatched occurrence count without modifying the file. "
                         "Relative paths use image WORKDIR."),
            action_type=ReplaceTextAction, observation_type=FileObservation,
            executor=FileExecutor(executor, cls.name),
        )]
