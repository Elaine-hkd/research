"""Shared failure recording; harnesses retain ownership of their SDK resources."""

from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
import time
import traceback

from harness.base import AgentResult
from utils.json_io import append_jsonl, write_json


def _attach_secondary(primary, stage, secondary):
    # Exclude implicit context: it can contain the primary error itself.
    details = "".join(traceback.format_exception(secondary, chain=False))
    primary.add_note(f"{stage} also failed:\n{details}")


@contextmanager
def preserve_recording_errors(recording):
    """Keep execute()'s exception if the owned recorder's __exit__ also fails."""
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
    """Own only diagnostic state for one run, never the environment or SDK.

    remember() saves the first failure immediately. stage() lets subsequent
    cleanup/collection steps finish without replacing it. finish() writes the
    shared artifacts after SDK work has joined, then re-raises the first error.
    sanitize(value) must return a JSON-safe value with this run's secrets removed.
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
                _attach_secondary(self.failure, "error event", log_error)

    @contextmanager
    def stage(self, name):
        try:
            yield
        except BaseException as error:
            self.remember(error, name)

    def finish(self, result: AgentResult, *, recorder, session_id: str) -> AgentResult:
        """Recorders implement record_failure(error, session_id=...) and last_path."""
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
