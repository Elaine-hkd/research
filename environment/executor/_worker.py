"""Standard-library worker, also sent verbatim to Python inside a container."""

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import tempfile


def run_resource(payload):
    # Each invocation gets a fresh snapshot, avoiding stale resources and races.
    with tempfile.TemporaryDirectory(prefix="skill-executor-") as directory:
        root = Path(directory)
        for relative, content in payload["files"].items():
            destination = (root / relative).resolve()
            if not destination.is_relative_to(root.resolve()):
                raise ValueError("Downloaded resource escapes staging directory")
            destination.parent.mkdir(parents=True, exist_ok=True)
            with destination.open("w", encoding="utf-8", newline="") as stream:
                stream.write(content)
        return _run_resource({**payload, "root": str(root)})


def _run_resource(payload):
    root = Path(payload["root"]).resolve(strict=True)
    resource = (root / payload["relative_path"]).resolve(strict=True)
    if not root.is_dir() or not resource.is_relative_to(root):
        raise ValueError("Resource escapes the skill directory (including symlinks)")
    if not resource.is_file():
        raise ValueError("Resource is not a file: " + str(resource))
    cwd = (Path(payload["workdir"]).resolve(strict=True)
           if payload["workdir"] is not None else resource.parent)
    if not cwd.is_dir():
        raise ValueError("Working directory is not a directory: " + str(cwd))

    suffix = resource.suffix.lower()
    if suffix == ".py":
        command = [payload["python_executable"], str(resource)]
    elif suffix == ".sh":
        command = [payload["bash_executable"], str(resource)]
    elif suffix == ".mjs":
        command = ["node", str(resource)]
    elif os.name == "nt" and suffix in {".bat", ".cmd"}:
        raise ValueError("Windows batch files are unsupported; use a Python script")
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
        raise InterruptedError("Command cancelled before startup")
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
            # A disconnect may race with Popen and publishing its process group.
            if cancelled.exists():
                raise InterruptedError("Command cancelled during startup")
        stdout, stderr = process.communicate(timeout=timeout)
    except BaseException as exc:
        timed_out = isinstance(exc, subprocess.TimeoutExpired)
        _kill_process_tree(process)
        try:
            stdout, stderr = process.communicate(timeout=5)
        except subprocess.TimeoutExpired as cleanup_exc:
            # A detached descendant may still hold a pipe. Bounded waiting is
            # not proof of cleanup, so never report this as a recoverable timeout.
            process.stdout.close()
            if process.stderr is not None:
                process.stderr.close()
            process.wait(timeout=5)
            raise RuntimeError("Command output pipes remained open after process cleanup") from cleanup_exc
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
        result = {"error": type(exc).__name__ + ": " + str(exc)}
    print(json.dumps(result, ensure_ascii=True))


if __name__ == "__main__":
    main()
