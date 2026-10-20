"""One authenticated loopback tool endpoint owned by a single dsh trial."""

import asyncio
from dataclasses import asdict
import json
import secrets

from pydantic import BaseModel, ConfigDict, Field

from tools.environment import EnvironmentOperations, OperationResult
from tools.loader import REGISTRY_TOOLS, validate_tools


class _Arguments(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _SearchArguments(_Arguments):
    query: str = ""
    # Retained for the existing OpenHands schema; the task owns the real scope.
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
    """Serialize tools on the environment's event loop; never own its lifecycle.

    A disconnected HTTP request cancels its operation. close() stops accepting
    requests and joins their cancellation before the evaluator destroys Docker.
    """

    def __init__(self, operations: EnvironmentOperations, tools):
        self.operations = operations
        self.tools = frozenset(validate_tools(tools))
        if unknown := self.tools - ARGUMENTS.keys():
            raise ValueError(f"No dsh tool implementation for: {sorted(unknown)}")
        self.registry = operations.environment.executor.registry
        if self.tools.intersection(REGISTRY_TOOLS) and self.registry is None:
            raise ValueError("Registry tools with an environment require its registry_url")
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
            raise RuntimeError("Tool bridge cannot be started twice")
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        port = self._server.sockets[0].getsockname()[1]
        self.url = f"http://127.0.0.1:{port}/call"

    def fail(self, error: Exception) -> None:
        if self.failure is None:
            self.failure = error
            self._failed.set()

    async def wait_failed(self) -> None:
        await self._failed.wait()
        raise RuntimeError("dsh task tool channel failed") from self.failure

    async def _call(self, name: str, arguments: dict) -> OperationResult:
        if name not in self.tools:
            raise ValueError(f"Tool is not enabled for this trial: {name!r}")
        arguments = ARGUMENTS[name].model_validate(arguments).model_dump(mode="json")
        async with self._lock:
            if self._closed or self.failure is not None:
                raise RuntimeError("Tool bridge is closed or failed")
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
            # request() keeps the original registry JSON envelope and enforces
            # the exact allowed set already installed by dataset setup.
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
                    raise ValueError("Expected POST /call HTTP/1.1")
                headers = {}
                for line in header_lines:
                    if not line:
                        continue
                    key, separator, value = line.partition(":")
                    key = key.strip().lower()
                    if not separator or key in headers:
                        raise ValueError("Invalid or duplicate HTTP header")
                    headers[key] = value.strip()
                if not secrets.compare_digest(headers.get("authorization", ""), "Bearer " + self.token):
                    await self._respond(writer, 403, {"error": "Invalid bridge credential"})
                    return
                if ("transfer-encoding" in headers
                        or headers.get("content-type", "").split(";")[0] != "application/json"):
                    raise ValueError("Expected a Content-Length JSON request")
                length = int(headers.get("content-length", "0"))
                if not 0 < length <= 8 * 1024 * 1024:
                    raise ValueError("Tool request must contain between 1 byte and 8 MiB")
                payload = json.loads(await reader.readexactly(length))
                if (not isinstance(payload, dict) or set(payload) != {"name", "arguments"}
                        or not isinstance(payload["name"], str)
                        or not isinstance(payload["arguments"], dict)):
                    raise ValueError("Expected a tool name and arguments object")
            operation = asyncio.create_task(self._call(payload["name"], payload["arguments"]))
            disconnected = asyncio.create_task(reader.read(1))
            done, _ = await asyncio.wait([operation, disconnected], return_when=asyncio.FIRST_COMPLETED)
            if operation not in done:
                return  # The finally block cancels and joins the abandoned call.
            result = await operation
            await self._respond(writer, 200, asdict(result))
        except ValueError as exc:
            if self.operations.failure is not None:
                self.fail(exc)
                await self._respond(writer, 500, {"error": str(exc)})
            else:
                await self._respond(writer, 400, {"error": str(exc)})
        except (ConnectionError, asyncio.IncompleteReadError) as exc:
            # Distinguish backend transport errors from a client disconnect.
            if (operation is not None and operation.done() and not operation.cancelled()
                    and operation.exception() is exc):
                self.fail(exc)
        except (TimeoutError, asyncio.LimitOverrunError) as exc:
            if operation is not None:
                self.fail(exc)
            await self._respond(writer, 500 if operation is not None else 400, {"error": str(exc)})
        except Exception as exc:
            self.fail(exc)
            await self._respond(writer, 500, {"error": str(exc)})
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
    async def _respond(writer, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
        reason = {200: "OK", 400: "Bad Request", 403: "Forbidden", 500: "Internal Server Error"}[status]
        header = (f"HTTP/1.1 {status} {reason}\r\nContent-Type: application/json; charset=utf-8\r\n"
                  f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n")
        try:
            writer.write(header.encode("ascii") + body)
            await writer.drain()
        except ConnectionError:
            pass  # A completed call can race with the client's cancellation.

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
