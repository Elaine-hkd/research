"""为新生成的任务副本添加 Xfce/noVNC 桌面。"""

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
    """仅修改当前准备好的副本，返回其路径，不启动 Docker。

    支持从任务 environment 目录构建、以 root 运行的 Debian/Ubuntu 镜像。
    在 materialize_skillsbench_task() 之后、创建 DockerEnvironment 之前调用一次。
    并发运行的每个桌面必须使用不同的主机端口。
    """
    import yaml

    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValueError("port 必须为 1 到 65535 之间的整数")
    task_dir = Path(task_dir).resolve(strict=True)
    task_file = task_dir / "task.md"
    if not task_file.is_file():
        raise ValueError("需要包含 task.md 的已准备任务目录")
    environment_dir = (task_dir / "environment").resolve(strict=True)
    if not environment_dir.is_relative_to(task_dir):
        raise ValueError("environment 目录超出了任务副本范围")

    # 否则 BenchFlow 会跳过修改后的 Dockerfile，或禁用端口发布。
    task_env = {}
    task_text = task_file.read_text(encoding="utf-8-sig")
    lines = task_text.splitlines()
    if lines and lines[0].strip() == "---":
        end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
        if end is None:
            raise ValueError("task.md 的 YAML 前置元数据未闭合")
        header = yaml.safe_load("\n".join(lines[1:end]))
        if not isinstance(header, dict):
            raise ValueError("task.md 的前置元数据必须为映射")
        sandbox = header.get("sandbox", header.get("environment"))
        if sandbox is None:
            sandbox = {}
        if not isinstance(sandbox, dict):
            raise ValueError("任务 sandbox 配置必须为映射")
        if sandbox.get("docker_image"):
            raise ValueError("准备桌面需要通过 Dockerfile 构建，不能使用 sandbox.docker_image")
        if sandbox.get("allow_internet") is False:
            raise ValueError("allow_internet=false 会禁用网络；noVNC 需要发布端口")
        task_env = sandbox.get("env")
        if task_env is None:
            task_env = {}
        if not isinstance(task_env, dict) or any(not isinstance(key, str) for key in task_env):
            raise ValueError("任务 sandbox.env 必须为键为字符串的映射")

    compose_file = environment_dir / "docker-compose.yaml"
    if compose_file.is_symlink():
        raise ValueError("Compose 文件必须位于任务副本中")
    compose = yaml.safe_load(compose_file.read_text(encoding="utf-8-sig")) if compose_file.exists() else {}
    if compose is None:
        compose = {}
    if not isinstance(compose, dict):
        raise ValueError("docker-compose.yaml 必须为映射")
    services = compose.setdefault("services", {})
    if not isinstance(services, dict):
        raise ValueError("Compose services 必须为映射")
    main = services.setdefault("main", {})
    if not isinstance(main, dict):
        raise ValueError("Compose services.main 必须为映射")
    if main.get("user") not in (None, "", "root", "root:root", "0", "0:0", 0):
        raise ValueError("准备桌面目前要求 services.main 以 root 运行")
    if main.get("network_mode") in ("host", "none"):
        raise ValueError("准备桌面需要支持端口发布的网络")

    build = main.get("build", {})
    if isinstance(build, str):
        build = {"context": build}
    if not isinstance(build, dict) or build.get("target") or "dockerfile_inline" in build:
        raise ValueError("需要通过 Dockerfile 构建最终阶段")
    context = build.get("context", "${CONTEXT_DIR}")
    if context not in (".", "${CONTEXT_DIR}", str(environment_dir), environment_dir.as_posix()):
        raise ValueError("桌面的构建上下文必须为任务的 environment 目录")
    dockerfile = (environment_dir / build.get("dockerfile", "Dockerfile")).resolve(strict=True)
    if not dockerfile.is_relative_to(environment_dir) or not dockerfile.is_file():
        raise ValueError("Dockerfile 必须位于任务环境副本中")
    docker_text = dockerfile.read_text(encoding="utf-8-sig")
    desktop_dir = environment_dir / "research-desktop"
    if _MARKER in docker_text or desktop_dir.exists():
        raise FileExistsError("桌面已经配置；如需修改，请重新准备任务副本")

    command = main.get("command", ["sh", "-c", "sleep infinity"])
    if isinstance(command, str):
        command = shlex.split(command)
    if not isinstance(command, list) or not command or any(not isinstance(arg, str) for arg in command):
        raise ValueError("services.main.command 必须为非空字符串或字符串列表")

    env = main.get("environment")
    if env is None:
        env = {}
    if not compose_file.exists():
        # 首次添加 Compose 文件后，BenchFlow 的环境变量处理会从
        # 执行时注入切换为 Compose 插值；必须保留任务的环境变量键。
        env = {key: "${" + key + "}" for key in task_env}
    if isinstance(env, dict):
        env.update(_DISPLAY_ENV)
    elif isinstance(env, list) and all(isinstance(item, str) for item in env):
        env = [item for item in env if item.split("=", 1)[0] not in _DISPLAY_ENV]
        env.extend(f"{key}={value}" for key, value in _DISPLAY_ENV.items())
    else:
        raise ValueError("services.main.environment 必须为映射或字符串列表")
    ports = main.get("ports")
    if ports is None:
        ports = []
    if not isinstance(ports, list):
        raise ValueError("services.main.ports 必须为列表")
    ports.append(f"127.0.0.1:{port}:6080")

    health = main.get("healthcheck")
    if health is None:
        health = {}
    if not isinstance(health, dict):
        raise ValueError("services.main.healthcheck 必须为映射")
    check = shlex.join(["/bin/bash", _LAUNCHER, "--healthcheck"])
    original_check = health.get("test")
    if not health.get("disable") and original_check not in (None, "", [], ["NONE"]):
        if isinstance(original_check, str):
            original_check = ["CMD-SHELL", original_check]
        if not isinstance(original_check, list) or not all(isinstance(arg, str) for arg in original_check):
            raise ValueError("services.main.healthcheck.test 无效")
        if original_check[0] == "CMD" and len(original_check) > 1:
            check += " && " + shlex.join(original_check[1:])
        elif original_check[0] == "CMD-SHELL" and len(original_check) == 2:
            check += " && (" + original_check[1] + ")"
        else:
            raise ValueError("不支持此 services.main.healthcheck.test")
    health.update(test=["CMD-SHELL", check], disable=False)
    for key, value in {"interval": "2s", "timeout": "5s", "start_period": "30s", "retries": 20}.items():
        health.setdefault(key, value)
    main.update(
        build={**build, "context": "${CONTEXT_DIR}"},
        command=["/bin/bash", _LAUNCHER, *command],
        init=True, environment=env, ports=ports, healthcheck=health,
    )

    install = (
        "test \"$(id -u)\" -eq 0 || { echo '安装桌面需要 root 权限' >&2; exit 1; }; "
        ". /etc/os-release; case \"$ID\" in debian|ubuntu) ;; "
        "*) echo '桌面仅支持 Debian/Ubuntu 镜像' >&2; exit 1;; esac; "
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

    # 在首次修改副本之前，完成全部校验和内容生成。
    desktop_dir.mkdir()
    (desktop_dir / "start-desktop.sh").write_text(launcher, encoding="utf-8", newline="\n")
    dockerfile.write_text(docker_text + suffix, encoding="utf-8", newline="\n")
    compose_file.write_text(compose_text, encoding="utf-8", newline="\n")
    return task_dir
