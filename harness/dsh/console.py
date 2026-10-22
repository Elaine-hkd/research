"""将 dsh 事件转换为可读控制台输出，保留持久化事件原样。"""

from datetime import datetime
import json
import math
import os
from pathlib import Path
import re
import shutil
import sys
import time
import unicodedata


_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _decode_json(value):
    if isinstance(value, str) and value.lstrip().startswith(("{", "[")):
        try:
            return json.loads(value)
        except ValueError:
            pass  # 保留纯文本原样，包括格式错误的工具 JSON。
    return value


def _pretty_json(value) -> str:
    value = _decode_json(value)
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2)


def _argument_text(value) -> str:
    value = _decode_json(value)
    if not isinstance(value, dict):
        return _pretty_json(value)
    multiline = {key: text for key, text in value.items()
                 if isinstance(text, str) and ("\n" in text or "\r" in text)}
    metadata = {key: item for key, item in value.items() if key not in multiline}
    sections = [_pretty_json(metadata)] if metadata else []
    sections.extend(f"{key}\n{text}" for key, text in multiline.items())
    return "\n\n".join(sections) if sections else _pretty_json(value)


def _result_text(value) -> str:
    value = _decode_json(value)
    if not isinstance(value, dict):
        return _pretty_json(value)
    metadata = dict(value)
    sections = []
    # 展开文本字段，使 JSON 转义的换行在输出中正常显示。
    if isinstance(metadata.get("skill"), dict):
        metadata["skill"] = dict(metadata["skill"])
        content = metadata["skill"].pop("content", None)
        if isinstance(content, str):
            sections.append("SKILL.md\n" + (content or "(空)"))
    for field, label in (("stdout", "标准输出"), ("stderr", "标准错误"), ("content", "资源内容")):
        if isinstance(metadata.get(field), str):
            sections.append(label + "\n" + (metadata.pop(field) or "(空)"))
    if metadata:
        sections.insert(0, _pretty_json(metadata))
    return "\n\n".join(sections) if sections else _pretty_json(value)


def _content_text(blocks, kind="text") -> str:
    return "".join(block["text"] for block in blocks if isinstance(block, dict)
                   and block.get("type") == kind and isinstance(block.get("text"), str))


def _stream_text(items, kind: str) -> str:
    parts = []
    for item in items:
        if not isinstance(item, dict):
            continue
        if item.get("type") == kind + "-chunks":
            parts.extend(text for text in item.get("texts", []) if isinstance(text, str))
        chunk = item.get("chunk")
        if isinstance(chunk, dict) and chunk.get("type") == kind + "-delta":
            text = chunk.get("text", chunk.get("delta"))
            if isinstance(text, str):
                parts.append(text)
    return "".join(parts)


def _cell_width(character: str) -> int:
    if unicodedata.combining(character):
        return 0
    return 2 if unicodedata.east_asian_width(character) in {"W", "F"} else 1


def _width(text: str) -> int:
    return sum(_cell_width(character) for character in text)


def _wrap(text: str, width: int):
    for line in text.expandtabs(4).split("\n"):
        segment, used = [], 0
        for character in line:
            cells = _cell_width(character)
            if used + cells > width:
                yield "".join(segment)
                segment, used = [], 0
            segment.append(character)
            used += cells
        yield "".join(segment)


class DshConsole:
    """显示返回的推理和回复，并按调用 ID 关联工具结果。

    此 SDK 的 assistant/message 包含完整消息块，不是实时 token 通知；
    流数据仅用于补充未完成的尝试。
    """

    def __init__(self, *, max_output_chars=20000, event_log_path: Path | None = None):
        if type(max_output_chars) is not int or max_output_chars < 1:
            raise ValueError("max_output_chars 必须为正整数")
        self.max_output_chars = max_output_chars
        self.event_log_path = event_log_path
        self.stream = sys.stdout
        self.color = (self.stream.isatty() and "NO_COLOR" not in os.environ
                      and os.environ.get("TERM") != "dumb")
        self.calls = {}
        self.call_count = 0

    def _paint(self, text: str, color: str) -> str:
        return f"\x1b[{color}m{text}\x1b[0m" if self.color else text

    def _block(self, title: str, text: str, *, color="36", timestamp="") -> None:
        # 移除内容中的终端控制字符，仅自己的标题使用 ANSI。
        text = _CONTROL.sub("", _ANSI.sub("", text).replace("\r\n", "\n").replace("\r", "\n"))
        if len(text) > self.max_output_chars:
            omitted = len(text) - self.max_output_chars
            destination = str(self.event_log_path) if self.event_log_path is not None else "dsh 会话日志"
            text = text[:self.max_output_chars] + f"\n… 控制台已省略 {omitted} 字符，完整事件见 {destination}"
        width = max(48, min(shutil.get_terminal_size((110, 24)).columns - 4, 110))
        heading = f"┌─ [{timestamp}] {title} "
        lines = [self._paint(heading + "─" * max(0, width - _width(heading) - 1) + "┐", color)]
        for line in _wrap(text or "(空)", width - 4):
            lines.append("│ " + line + " " * max(0, width - 4 - _width(line)) + " │")
        lines.append(self._paint("└" + "─" * (width - 2) + "┘", color))
        print("\n" + "\n".join("  " + line for line in lines), file=self.stream, flush=True)

    def feed(self, event: dict) -> None:
        kind, data = event.get("type"), event.get("data", {})
        event_time = event.get("time")
        event_time = event_time if type(event_time) in (int, float) and math.isfinite(event_time) else None
        timestamp = (datetime.fromtimestamp(event_time / 1000).astimezone()
                     if event_time is not None else datetime.now().astimezone()).strftime("%H:%M:%S")
        if kind == "step/start":
            context = f"第 {data.get('turn', '?')} 轮 · 步骤 {data.get('step', '?')}"
            print(self._paint(f"\n  ── [{timestamp}] {context} ──", "1;36"), file=self.stream, flush=True)
        elif kind in {"assistant/message", "assistant/attempt"}:
            message = data.get("message") or {}
            blocks = message.get("content") or []
            stream = data.get("stream") or []
            suffix = "（未提交的尝试）" if kind == "assistant/attempt" else ""
            for block_kind, title, color in (("reasoning", "AI 思考", "35"), ("text", "AI 回复", "36")):
                text = _content_text(blocks, block_kind) or _stream_text(stream, block_kind)
                if text.strip():
                    self._block(title + suffix, text, color=color, timestamp=timestamp)
            if data.get("error") is not None:
                self._block("模型请求异常", _pretty_json(data["error"]), color="31", timestamp=timestamp)
        elif kind == "tool/call":
            self.call_count += 1
            name = data.get("name", "未知工具")
            self.calls[data.get("callId")] = (self.call_count, name, event_time, time.monotonic())
            self._block(f"工具调用 #{self.call_count:02d} · {name} · 参数",
                        _argument_text(data.get("arguments", {})), color="33", timestamp=timestamp)
        elif kind == "tool/result":
            for block in (data.get("message") or {}).get("content", []):
                if not isinstance(block, dict) or block.get("type") != "tool-result":
                    continue
                call = self.calls.pop(block.get("toolCallId"), None)
                label = f"#{call[0]:02d} · {call[1]}" if call else str(block.get("toolCallId", "未知调用"))
                if call:
                    elapsed = ((event_time - call[2]) / 1000 if event_time is not None and call[2] is not None
                               else time.monotonic() - call[3])
                    label += f" · {max(0, elapsed):.2f}s"
                failed = bool(block.get("isError")) or data.get("error") is not None
                parts = []
                for content in block.get("content", []):
                    if isinstance(content, dict) and content.get("type") == "text":
                        parts.append(_result_text(content.get("text", "")))
                    elif isinstance(content, dict):
                        parts.append(f"[非文本内容：{content.get('type', '未知')}]")
                if data.get("error") is not None:
                    parts.append(_pretty_json(data["error"]))
                self._block(f"工具结果 {label} · {'错误' if failed else '返回'}", "\n\n".join(parts),
                            color="31" if failed else "32", timestamp=timestamp)
        elif kind == "turn/end":
            reason = data.get("reason", {})
            status = reason.get("kind", "unknown") if isinstance(reason, dict) else str(reason)
            self._block("本轮结束", _pretty_json(reason), color="32" if status == "completed" else "31",
                        timestamp=timestamp)
