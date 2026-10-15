"""Add an Xfce/noVNC desktop to a fresh, materialized task copy."""

import json
from pathlib import Path
import shlex


_MARKER = "# research: Xfce desktop"
_LAUNCHER = "/usr/local/bin/research-start-desktop.sh"
_DISPLAY_ENV = {
    "DISPLAY": ":0",
    "XAUTHORITY": "/root/.Xauthority",
    "XDG_RUNTIME_DIR": "/tmp/research-desktop-runtime",
}


def configure_task_desktop(task_dir: str | Path, *, port: int = 6080) -> Path:
    """Modify only this staged copy; return its path without starting Docker.

    Supports root-run Debian/Ubuntu images built from the task's environment
    directory. Call once after materialize_skillsbench_task(), before creating
    DockerEnvironment. Each concurrent desktop needs a different host port.
    """
    import yaml

    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValueError("port must be an integer between 1 and 65535")
    task_dir = Path(task_dir).resolve(strict=True)
    task_file = task_dir / "task.md"
    if not task_file.is_file():
        raise ValueError("Expected a materialized task directory containing task.md")
    environment_dir = (task_dir / "environment").resolve(strict=True)
    if not environment_dir.is_relative_to(task_dir):
        raise ValueError("environment directory escapes the task copy")

    # BenchFlow otherwise skips the modified Dockerfile or disables publishing.
    task_env = {}
    task_text = task_file.read_text(encoding="utf-8-sig")
    lines = task_text.splitlines()
    if lines and lines[0].strip() == "---":
        end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
        if end is None:
            raise ValueError("Unclosed task.md YAML frontmatter")
        header = yaml.safe_load("\n".join(lines[1:end]))
        if not isinstance(header, dict):
            raise ValueError("task.md frontmatter must be a mapping")
        sandbox = header.get("sandbox", header.get("environment"))
        if sandbox is None:
            sandbox = {}
        if not isinstance(sandbox, dict):
            raise ValueError("Task sandbox configuration must be a mapping")
        if sandbox.get("docker_image"):
            raise ValueError("Desktop preparation requires a Dockerfile build, not sandbox.docker_image")
        if sandbox.get("allow_internet") is False:
            raise ValueError("allow_internet=false disables networking; noVNC requires port publishing")
        task_env = sandbox.get("env")
        if task_env is None:
            task_env = {}
        if not isinstance(task_env, dict) or any(not isinstance(key, str) for key in task_env):
            raise ValueError("Task sandbox.env must be a mapping with string keys")

    compose_file = environment_dir / "docker-compose.yaml"
    if compose_file.is_symlink():
        raise ValueError("Compose file must belong to the task copy")
    compose = yaml.safe_load(compose_file.read_text(encoding="utf-8-sig")) if compose_file.exists() else {}
    if compose is None:
        compose = {}
    if not isinstance(compose, dict):
        raise ValueError("docker-compose.yaml must be a mapping")
    services = compose.setdefault("services", {})
    if not isinstance(services, dict):
        raise ValueError("Compose services must be a mapping")
    main = services.setdefault("main", {})
    if not isinstance(main, dict):
        raise ValueError("Compose services.main must be a mapping")
    if main.get("user") not in (None, "", "root", "root:root", "0", "0:0", 0):
        raise ValueError("Desktop preparation currently requires services.main to run as root")
    if main.get("network_mode") in ("host", "none"):
        raise ValueError("Desktop preparation requires a network that supports port publishing")

    build = main.get("build", {})
    if isinstance(build, str):
        build = {"context": build}
    if not isinstance(build, dict) or build.get("target") or "dockerfile_inline" in build:
        raise ValueError("Expected a Dockerfile build of the final stage")
    context = build.get("context", "${CONTEXT_DIR}")
    if context not in (".", "${CONTEXT_DIR}", str(environment_dir), environment_dir.as_posix()):
        raise ValueError("Desktop build context must be the task's environment directory")
    dockerfile = (environment_dir / build.get("dockerfile", "Dockerfile")).resolve(strict=True)
    if not dockerfile.is_relative_to(environment_dir) or not dockerfile.is_file():
        raise ValueError("Dockerfile must belong to the task environment copy")
    docker_text = dockerfile.read_text(encoding="utf-8-sig")
    desktop_dir = environment_dir / "research-desktop"
    if _MARKER in docker_text or desktop_dir.exists():
        raise FileExistsError("Desktop already configured; prepare a fresh task copy to change it")

    command = main.get("command", ["sh", "-c", "sleep infinity"])
    if isinstance(command, str):
        command = shlex.split(command)
    if not isinstance(command, list) or not command or any(not isinstance(arg, str) for arg in command):
        raise ValueError("services.main.command must be a non-empty string or string list")

    env = main.get("environment")
    if env is None:
        env = {}
    if not compose_file.exists():
        # Adding the first Compose file switches BenchFlow's env handling from
        # exec injection to Compose interpolation. Preserve the task env keys.
        env = {key: "${" + key + "}" for key in task_env}
    if isinstance(env, dict):
        env.update(_DISPLAY_ENV)
    elif isinstance(env, list) and all(isinstance(item, str) for item in env):
        env = [item for item in env if item.split("=", 1)[0] not in _DISPLAY_ENV]
        env.extend(f"{key}={value}" for key, value in _DISPLAY_ENV.items())
    else:
        raise ValueError("services.main.environment must be a mapping or string list")
    ports = main.get("ports")
    if ports is None:
        ports = []
    if not isinstance(ports, list):
        raise ValueError("services.main.ports must be a list")
    ports.append(f"127.0.0.1:{port}:6080")

    health = main.get("healthcheck")
    if health is None:
        health = {}
    if not isinstance(health, dict):
        raise ValueError("services.main.healthcheck must be a mapping")
    check = shlex.join(["/bin/bash", _LAUNCHER, "--healthcheck"])
    original_check = health.get("test")
    if not health.get("disable") and original_check not in (None, "", [], ["NONE"]):
        if isinstance(original_check, str):
            original_check = ["CMD-SHELL", original_check]
        if not isinstance(original_check, list) or not all(isinstance(arg, str) for arg in original_check):
            raise ValueError("Invalid services.main.healthcheck.test")
        if original_check[0] == "CMD" and len(original_check) > 1:
            check += " && " + shlex.join(original_check[1:])
        elif original_check[0] == "CMD-SHELL" and len(original_check) == 2:
            check += " && (" + original_check[1] + ")"
        else:
            raise ValueError("Unsupported services.main.healthcheck.test")
    health.update(test=["CMD-SHELL", check], disable=False)
    for key, value in {"interval": "2s", "timeout": "5s", "start_period": "30s", "retries": 20}.items():
        health.setdefault(key, value)
    main.update(
        build={**build, "context": "${CONTEXT_DIR}"},
        command=["/bin/bash", _LAUNCHER, *command],
        init=True, environment=env, ports=ports, healthcheck=health,
    )

    install = (
        "test \"$(id -u)\" -eq 0 || { echo 'Desktop installation requires root' >&2; exit 1; }; "
        ". /etc/os-release; case \"$ID\" in debian|ubuntu) ;; "
        "*) echo 'Desktop supports Debian/Ubuntu images only' >&2; exit 1;; esac; "
        "apt-get update; "
        "DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "
        "python3 xvfb xfce4 xfce4-terminal dbus-x11 x11vnc novnc websockify "
        "xterm x11-utils fonts-dejavu-core fonts-noto-cjk; "
        "rm -rf /var/lib/apt/lists/*"
    )
    env_line = " ".join(f"{key}={value}" for key, value in _DISPLAY_ENV.items())
    suffix = (
        f"\n\n{_MARKER}\n"
        f"RUN {json.dumps(['/bin/sh', '-ec', install])}\n"
        f"ENV {env_line}\n"
        f"COPY research-desktop/start-desktop.sh {_LAUNCHER}\n"
        f"RUN [\"chmod\", \"755\", \"{_LAUNCHER}\"]\n"
    )
    launcher = Path(__file__).with_name("start-desktop.sh").read_text(encoding="utf-8")
    compose_text = yaml.safe_dump(compose, allow_unicode=True, sort_keys=False)

    # Validate and render everything before making the first change to the copy.
    desktop_dir.mkdir()
    (desktop_dir / "start-desktop.sh").write_text(launcher, encoding="utf-8", newline="\n")
    dockerfile.write_text(docker_text + suffix, encoding="utf-8", newline="\n")
    compose_file.write_text(compose_text, encoding="utf-8", newline="\n")
    return task_dir
