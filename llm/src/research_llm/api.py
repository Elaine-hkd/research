"""LiteLLM 风格的同步、异步对话接口；原生参数和回复交给 LiteLLM。"""

import inspect
from pathlib import Path

from .accounting import UsageRecorder, read_response_usage


_recorder: UsageRecorder | None = None


def configure(*, project_id: str, ledger_path: str | Path = "logs/model-usage.sqlite3",
              user_id: str | None = None, prices_path: str | Path | None = None) -> UsageRecorder:
    """在程序启动时设置账本和归属；运行中的调用继续使用开始时的配置。"""
    global _recorder
    _recorder = UsageRecorder(project_id=project_id, ledger_path=ledger_path,
                              user_id=user_id, prices_path=prices_path)
    return _recorder


def _configured() -> UsageRecorder:
    if _recorder is None:
        raise RuntimeError("请先调用 research_llm.configure(project_id=..., user_id=..., ledger_path=...)")
    return _recorder


def _safe(value, key):
    if not isinstance(value, str):
        return None
    return value.replace(key, "[REDACTED]") if isinstance(key, str) and key else value


def _finish_response(recorder, call, response, key, *, status="completed", error_type=None):
    try:
        hidden = getattr(response, "_hidden_params", None) or {}
        headers = getattr(response, "_response_headers", None) or hidden.get("headers") or {}
        return recorder.finish_call(
            call, usage=read_response_usage(response), status=status, error_type=error_type,
            response_model=_safe(getattr(response, "model", None), key),
            response_id=_safe(getattr(response, "id", None), key),
            provider_request_id=_safe(headers.get("x-request-id") or headers.get("request-id"), key),
        )
    except Exception as error:
        raise RuntimeError(f"请求已经发出，但调用 {call['call_id']} 的账本记录未完成，请先对账再重试") from error


def _fail(recorder, call, error):
    error.add_note(f"模型调用编号 {call['call_id']}")
    try:
        recorder.finish_call(call, status="failed", error_type=type(error).__name__)
    except Exception as ledger_error:
        error.add_note(f"账本更新失败：{type(ledger_error).__name__}")


def _options(kwargs):
    options = dict(kwargs)
    if options.get("stream"):
        stream_options = dict(options.get("stream_options") or {})
        stream_options.setdefault("include_usage", True)
        options["stream_options"] = stream_options
    return options


def _complete(recorder, model, messages, kwargs, *, rates=None, api_key_env=""):
    from litellm import completion as native_completion

    options = _options(kwargs)
    key = options.get("api_key")
    call = recorder.begin_call(model=_safe(model, key), api_key_env=_safe(api_key_env, key),
                               base_url=options.get("api_base") or options.get("base_url"), rates=rates)
    try:
        response = native_completion(model=model, messages=messages, **options)
    except BaseException as error:
        _fail(recorder, call, error)
        raise
    if options.get("stream"):
        return _TrackedStream(response, recorder, call, key), call
    return response, _finish_response(recorder, call, response, key)


def completion(model, messages, **kwargs):
    """透传 LiteLLM 对话参数，返回原生 ModelResponse；stream=True 返回记录用量的流。"""
    response, _ = _complete(_configured(), model, messages, kwargs)
    return response


async def acompletion(model, messages, **kwargs):
    """completion() 的异步版本；异步流使用 async for 和 aclose()。"""
    from litellm import acompletion as native_acompletion

    recorder = _configured()
    options = _options(kwargs)
    key = options.get("api_key")
    call = recorder.begin_call(model=_safe(model, key), source="acompletion",
                               base_url=options.get("api_base") or options.get("base_url"))
    try:
        response = await native_acompletion(model=model, messages=messages, **options)
    except BaseException as error:
        _fail(recorder, call, error)
        raise
    if options.get("stream"):
        return _TrackedStream(response, recorder, call, key)
    _finish_response(recorder, call, response, key)
    return response


class _TrackedStream:
    """只保留最终用量块；提前关闭或读取失败不会被记录为完整响应。"""

    def __init__(self, stream, recorder, call, key):
        self._stream = stream
        self._recorder = recorder
        self._call = call
        self._key = key
        self._usage_chunk = None
        self._last_chunk = None
        self._finished = False

    def __getattr__(self, name):
        return getattr(self._stream, name)

    def __iter__(self):
        return self

    def __aiter__(self):
        return self

    def _observe(self, chunk):
        self._last_chunk = chunk
        if getattr(chunk, "usage", None) is not None:
            self._usage_chunk = chunk
        return chunk

    def _finish(self, status, error_type=None):
        if not self._finished:
            # 写账失败后不在 close() 再次尝试完成，保留 pending 供审核。
            self._finished = True
            _finish_response(self._recorder, self._call, self._usage_chunk or self._last_chunk,
                             self._key, status=status, error_type=error_type)

    def _failure(self, error):
        error.add_note(f"模型调用编号 {self._call['call_id']}")
        try:
            self._finish("failed", type(error).__name__)
        except Exception as ledger_error:
            error.add_note(f"账本更新失败：{type(ledger_error).__name__}")

    def __next__(self):
        if self._finished:
            raise StopIteration
        try:
            chunk = next(self._stream)
        except StopIteration:
            self._finish("completed")
            raise
        except BaseException as error:
            self._failure(error)
            raise
        return self._observe(chunk)

    async def __anext__(self):
        if self._finished:
            raise StopAsyncIteration
        try:
            chunk = await self._stream.__anext__()
        except StopAsyncIteration:
            self._finish("completed")
            raise
        except BaseException as error:
            self._failure(error)
            raise
        return self._observe(chunk)

    def close(self):
        try:
            close = getattr(self._stream, "close", None)
            if close is not None:
                close()
        except BaseException as error:
            self._failure(error)
            raise
        self._finish("interrupted")

    async def aclose(self):
        try:
            close = getattr(self._stream, "aclose", None) or getattr(self._stream, "close", None)
            if close is not None:
                result = close()
                if inspect.isawaitable(result):
                    await result
        except BaseException as error:
            self._failure(error)
            raise
        self._finish("interrupted")

    def __enter__(self):
        return self

    def __exit__(self, error_type, error, traceback):
        try:
            self.close()
        except BaseException as close_error:
            if error is None:
                raise
            error.add_note(f"流关闭或入账也失败：{type(close_error).__name__}")

    async def __aenter__(self):
        return self

    async def __aexit__(self, error_type, error, traceback):
        try:
            await self.aclose()
        except BaseException as close_error:
            if error is None:
                raise
            error.add_note(f"流关闭或入账也失败：{type(close_error).__name__}")
