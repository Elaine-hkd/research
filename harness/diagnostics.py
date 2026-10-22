"""共用的失败记录逻辑；SDK 资源仍由各 harness 管理。"""

from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
import time
import traceback

from harness.base import AgentResult
from utils.json_io import append_jsonl, write_json


def _attach_secondary(primary, stage, secondary):
    # 排除隐式上下文，避免其中包含主异常本身。
    details = "".join(traceback.format_exception(secondary, chain=False))
    primary.add_note(f"{stage} 也失败：\n{details}")


@contextmanager
def preserve_recording_errors(recording):
    """若所持记录器的 __exit__ 也失败，保留 execute() 的异常。"""
    primary = None
    try:
        with recording as recorder:
            try:
                yield recorder
            except BaseException as error:
                primary = error
                raise
    except BaseException as error:
        if primary is not None and error is not primary:
            _attach_secondary(primary, "recorder.close", error)
            raise primary
        raise


class RunDiagnostics:
    """仅管理一次运行的诊断状态，不管理环境或 SDK。

    remember() 立即保存首次失败。stage() 允许后续清理和收集步骤完成，
    不覆盖首次失败。finish() 在 SDK 工作结束后写入公共产物，
    再重新抛出首次异常。
    sanitize(value) 必须返回可安全写入 JSON 且已移除本次运行密钥的值。
    """

    def __init__(self, output_dir: Path | None, *, sanitize):
        self.output_dir = output_dir
        self.sanitize = sanitize
        self.failure: BaseException | None = None

    def _details(self, error):
        origin = error.__cause__ or error
        return self.sanitize({
            "error_type": type(origin).__name__, "message": str(origin),
            "traceback": "".join(traceback.format_exception(error)),
        })

    def remember(self, error, stage):
        if self.failure is None:
            self.failure = error
        elif self.failure is not error:
            _attach_secondary(self.failure, stage, error)
        if self.output_dir is not None:
            try:
                append_jsonl(self.output_dir / "events.jsonl", {
                    "type": "runner/error", "time": time.time_ns() // 1_000_000,
                    "data": {"stage": stage, **self._details(error)},
                })
            except Exception as log_error:
                _attach_secondary(self.failure, "错误事件", log_error)

    @contextmanager
    def stage(self, name):
        try:
            yield
        except BaseException as error:
            self.remember(error, name)

    def finish(self, result: AgentResult, *, recorder, session_id: str) -> AgentResult:
        """记录器需实现 record_failure(error, session_id=...) 和 last_path。"""
        with self.stage("record_failure"):
            if self.failure is not None and recorder is not None:
                recorder.record_failure(self._details(self.failure), session_id=session_id)
        with self.stage("trace_path"):
            if recorder is not None and recorder.last_path is not None:
                result.trace_path = str(recorder.last_path)
        with self.stage("result.json"):
            if self.output_dir is not None:
                saved_result = asdict(result)
                if self.failure is not None:
                    saved_result["error"] = self._details(self.failure)
                write_json(self.output_dir / "result.json", self.sanitize(saved_result))
        if self.failure is not None:
            raise self.failure
        return result
