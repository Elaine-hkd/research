"""仅依赖标准库的工作进程代码，也会原样发送到容器内的 Python。"""

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import tempfile
import traceback


def run_resource(payload):
    # 每次调用使用新的快照，避免资源过期和竞态。
    with tempfile.TemporaryDirectory(prefix="skill-executor-") as directory:
        root = Path(directory)
        for relative, content in payload["files"].items():
            destination = (root / relative).resolve()
            if not destination.is_relative_to(root.resolve()):
                raise ValueError("下载的资源超出了准备目录范围")
            destination.parent.mkdir(parents=True, exist_ok=True)
            with destination.open("w", encoding="utf-8", newline="") as stream:
                stream.write(content)
        return _run_resource({**payload, "root": str(root)})


def _run_resource(payload):
    root = Path(payload["root"]).resolve(strict=True)
    resource = (root / payload["relative_path"]).resolve(strict=True)
    if not root.is_dir() or not resource.is_relative_to(root):
        raise ValueError("资源超出了技能目录范围（包括符号链接）")
    if not resource.is_file():
        raise ValueError("资源不是文件：" + str(resource))
    cwd = (Path(payload["workdir"]).resolve(strict=True)
           if payload["workdir"] is not None else resource.parent)
    if not cwd.is_dir():
        raise ValueError("工作目录不是目录：" + str(cwd))

    suffix = resource.suffix.lower()
    if suffix == ".py":
        command = [payload["python_executable"], str(resource)]
    elif suffix == ".sh":
        command = [payload["bash_executable"], str(resource)]
    elif suffix == ".mjs":
        command = ["node", str(resource)]
    elif os.name == "nt" and suffix in {".bat", ".cmd"}:
        raise ValueError("不支持 Windows 批处理文件，请使用 Python 脚本")
    else:
        command = [str(resource)]
    command.extend(payload["arguments"])
    env = os.environ.copy()
    env.update(payload["env"])
    env["PYTHONIOENCODING"] = "utf-8"
    return run_command(command, cwd, env, payload["timeout"],
                       control_path=payload.get("control_path"))


def _kill_process_tree(process):
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=10, check=False, shell=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            if process.poll() is None:
                process.kill()
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    if process.poll() is None:
        process.kill()


def run_command(command, cwd, env, timeout, *, control_path=None, merge_stderr=False):
    started = time.monotonic()
    cancelled = Path(control_path + ".cancelled") if control_path is not None else None
    if cancelled is not None and cancelled.exists():
        raise InterruptedError("命令在启动前已取消")
    process = subprocess.Popen(
        command, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT if merge_stderr else subprocess.PIPE, shell=False,
        start_new_session=(os.name != "nt"),
    )
    timed_out = False
    try:
        if control_path is not None:
            pending_control = Path(control_path + ".tmp")
            pending_control.write_text(str(process.pid), encoding="ascii")
            pending_control.replace(control_path)
            # 断开连接可能与 Popen 和进程组发布同时发生。
            if cancelled.exists():
                raise InterruptedError("命令在启动过程中被取消")
        stdout, stderr = process.communicate(timeout=timeout)
    except BaseException as exc:
        timed_out = isinstance(exc, subprocess.TimeoutExpired)
        _kill_process_tree(process)
        try:
            stdout, stderr = process.communicate(timeout=5)
        except subprocess.TimeoutExpired as cleanup_exc:
            # 脱离的后代进程可能仍持有管道。有时限的等待不能证明
            # 清理完成，因此不能将这种情况报告为可恢复的超时。
            process.stdout.close()
            if process.stderr is not None:
                process.stderr.close()
            process.wait(timeout=5)
            raise RuntimeError("进程清理后，命令输出管道仍未关闭") from cleanup_exc
        if not timed_out:
            raise
    return {
        "exit_code": None if timed_out else process.returncode,
        "stdout": stdout.decode("utf-8", errors="replace"),
        "stderr": (stderr or b"").decode("utf-8", errors="replace"),
        "timed_out": timed_out,
        "duration_ms": max(0, round((time.monotonic() - started) * 1000)),
    }


def main():
    try:
        if len(sys.argv) > 1:
            with open(sys.argv[1], encoding="utf-8") as stream:
                payload = json.load(stream)
        else:
            payload = json.load(sys.stdin)
        if payload.get("operation") == "command":
            command_result = run_command(
                payload["argv"], payload["workdir"], os.environ.copy(), payload["timeout"],
                control_path=payload["control_path"], merge_stderr=True,
            )
        else:
            command_result = run_resource(payload)
        result = {"result": command_result}
    except Exception as exc:
        result = {"error": type(exc).__name__ + ": " + str(exc),
                  "error_type": type(exc).__name__, "traceback": traceback.format_exc()}
    print(json.dumps(result, ensure_ascii=True))


if __name__ == "__main__":
    main()
