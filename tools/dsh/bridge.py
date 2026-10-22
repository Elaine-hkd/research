"""由单次 dsh 试验持有、经过身份验证的本机回环工具端点。"""

import asyncio
from dataclasses import asdict
import json
import secrets
import traceback

from pydantic import BaseModel, ConfigDict, Field

from tools.environment import EnvironmentOperations, OperationResult
from tools.loader import REGISTRY_TOOLS, validate_tools


class _Arguments(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _SearchArguments(_Arguments):
    query: str = ""
    # 保留字段以兼容现有 OpenHands 模式，实际技能范围由任务决定。
    exposed_skills: list[str] | None = None


class _SkillArguments(_Arguments):
    name: str


class _ResourceArguments(_SkillArguments):
    relative_path: str


class _ExecuteArguments(_Arguments):
    skill_name: str = Field(min_length=1)
    relative_path: str = Field(min_length=1)
    arguments: list[str] = Field(default_factory=list)
    timeout: float | None = Field(default=None, gt=0, allow_inf_nan=False)


class _TerminalArguments(_Arguments):
    command: str = Field(min_length=1)
    cwd: str | None = None
    timeout_sec: float = Field(default=120, gt=0, allow_inf_nan=False)


class _ReadArguments(_Arguments):
    path: str = Field(min_length=1)
    start_line: int = Field(default=1, ge=1)
    max_lines: int = Field(default=200, ge=1, le=2000)


class _WriteArguments(_Arguments):
    path: str = Field(min_length=1)
    content: str


ARGUMENTS = {
    "search_skills": _SearchArguments,
    "load_skill": _SkillArguments,
    "load_resource": _ResourceArguments,
    "execute_resource": _ExecuteArguments,
    "terminal": _TerminalArguments,
    "read_file": _ReadArguments,
    "write_file": _WriteArguments,
}


class ToolBridge:
    """在环境事件循环中串行执行工具，不管理环境生命周期。

    HTTP 请求断开时会取消对应操作。close() 停止接受请求，
    并等待取消完成后，再由评分流程销毁 Docker。
    """

    def __init__(self, operations: EnvironmentOperations, tools):
        self.operations = operations
        self.tools = frozenset(validate_tools(tools))
        if unknown := self.tools - ARGUMENTS.keys():
            raise ValueError(f"以下工具没有 dsh 实现：{sorted(unknown)}")
        self.registry = operations.environment.executor.registry
        if self.tools.intersection(REGISTRY_TOOLS) and self.registry is None:
            raise ValueError("环境中的注册中心工具需要配置环境的 registry_url")
        self.token = secrets.token_urlsafe(32)
        self.url = None
        self.failure = None
        self._failed = asyncio.Event()
        self._lock = asyncio.Lock()
        self._server = None
        self._connections = set()
        self._closed = False

    async def start(self) -> None:
        if self._closed or self._server is not None:
            raise RuntimeError("工具桥接接口不能重复启动")
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        port = self._server.sockets[0].getsockname()[1]
        self.url = f"http://127.0.0.1:{port}/call"

    def fail(self, error: Exception) -> None:
        if self.failure is None:
            self.failure = error
            self._failed.set()

    async def wait_failed(self) -> None:
        await self._failed.wait()
        raise RuntimeError("dsh 任务工具通道失败") from self.failure

    async def _call(self, name: str, arguments: dict) -> OperationResult:
        if name not in self.tools:
            raise ValueError(f"本次试验未启用该工具：{name!r}")
        arguments = ARGUMENTS[name].model_validate(arguments).model_dump(mode="json")
        async with self._lock:
            if self._closed or self.failure is not None:
                raise RuntimeError("工具桥接接口已关闭或失败")
            if name == "terminal":
                return await self.operations.run_command(**arguments)
            if name in {"read_file", "write_file"}:
                return await self.operations.run_file(name, arguments)
            if name == "execute_resource":
                return await self.operations.run_resource(arguments)
            if name == "search_skills":
                params = {"query": arguments["query"]}
            else:
                params = {"skill_name": arguments["name"]}
                if name == "load_resource":
                    params["relative_path"] = arguments["relative_path"]
            # request() 保留注册中心原始 JSON 响应结构，并严格执行
            # 数据集设置阶段已确定的允许访问集合。
            envelope = await asyncio.to_thread(self.registry.request, "/" + name, params)
            return OperationResult(json.dumps(envelope, ensure_ascii=False, separators=(",", ":")))

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        connection = asyncio.current_task()
        self._connections.add(connection)
        operation = disconnected = None
        try:
            if self._closed:
                return
            async with asyncio.timeout(10):
                header = (await reader.readuntil(b"\r\n\r\n")).decode("ascii")
                request_line, *header_lines = header.split("\r\n")
                if request_line != "POST /call HTTP/1.1":
                    raise ValueError("请求必须为 POST /call HTTP/1.1")
                headers = {}
                for line in header_lines:
                    if not line:
                        continue
                    key, separator, value = line.partition(":")
                    key = key.strip().lower()
                    if not separator or key in headers:
                        raise ValueError("HTTP 请求头无效或重复")
                    headers[key] = value.strip()
                if not secrets.compare_digest(headers.get("authorization", ""), "Bearer " + self.token):
                    await self._respond(writer, 403, {"error": "桥接接口凭据无效"})
                    return
                if ("transfer-encoding" in headers
                        or headers.get("content-type", "").split(";")[0] != "application/json"):
                    raise ValueError("需要带 Content-Length 的 JSON 请求")
                length = int(headers.get("content-length", "0"))
                if not 0 < length <= 8 * 1024 * 1024:
                    raise ValueError("工具请求大小必须在 1 字节到 8 MiB 之间")
                payload = json.loads(await reader.readexactly(length))
                if (not isinstance(payload, dict) or set(payload) != {"name", "arguments"}
                        or not isinstance(payload["name"], str)
                        or not isinstance(payload["arguments"], dict)):
                    raise ValueError("需要工具名称及参数对象")
            operation = asyncio.create_task(self._call(payload["name"], payload["arguments"]))
            disconnected = asyncio.create_task(reader.read(1))
            done, _ = await asyncio.wait([operation, disconnected], return_when=asyncio.FIRST_COMPLETED)
            if operation not in done:
                self.fail(ConnectionError(
                    f"dsh 客户端在执行工具时断开连接：{payload['name']}；正在取消对应的环境操作"
                ))
                return  # finally 块会取消并等待这个已放弃的调用结束。
            result = await operation
            await self._respond(writer, 200, asdict(result))
        except ValueError as exc:
            if self.operations.failure is not None:
                self.fail(exc)
                await self._respond(writer, 500, self._error_details(exc))
            else:
                await self._respond(writer, 400, self._error_details(exc))
        except (ConnectionError, asyncio.IncompleteReadError) as exc:
            # 区分后端传输错误与客户端断开连接。
            if (operation is not None and operation.done() and not operation.cancelled()
                    and operation.exception() is exc):
                self.fail(exc)
        except (TimeoutError, asyncio.LimitOverrunError) as exc:
            if operation is not None:
                self.fail(exc)
            await self._respond(writer, 500 if operation is not None else 400, self._error_details(exc))
        except Exception as exc:
            self.fail(exc)
            await self._respond(writer, 500, self._error_details(exc))
        finally:
            pending = [task for task in (operation, disconnected) if task is not None]
            for task in pending:
                if not task.done():
                    task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass
            self._connections.discard(connection)

    @staticmethod
    def _error_details(error):
        return {"error": str(error), "error_type": type(error).__name__,
                "traceback": "".join(traceback.format_exception(error))}

    @staticmethod
    async def _respond(writer, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
        reason = {200: "OK", 400: "Bad Request", 403: "Forbidden", 500: "Internal Server Error"}[status]
        header = (f"HTTP/1.1 {status} {reason}\r\nContent-Type: application/json; charset=utf-8\r\n"
                  f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n")
        try:
            writer.write(header.encode("ascii") + body)
            await writer.drain()
        except ConnectionError:
            pass  # 调用完成可能与客户端取消同时发生。

    async def close(self) -> None:
        self._closed = True
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
        connections = tuple(self._connections)
        for connection in connections:
            connection.cancel()
        if connections:
            await asyncio.gather(*connections, return_exceptions=True)
