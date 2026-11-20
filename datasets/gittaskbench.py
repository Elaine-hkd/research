"""将 GitTaskBench 的仓库级任务接入实验流程，按上游 test_script 评分。"""

import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import re
import shlex
import shutil
import sys

import yaml

from datasets.package import read_package_index, resource_path
from evaluation.contracts import AgentResult, PreparedTask, Score, Task, TrialContext
from utils.json_io import write_json


TASK_FORMAT = "research-gittaskbench-v1"
DEFAULT_TIMEOUT = 1800
# 容器内任务根目录。上游评测在 Linux 下运行，这里保持同一布局；
# 评分材料单独放在一个目录，容器起来后才上传，agent 运行期间看不到。
CONTAINER_ROOT = "/task"
EVALUATION_DIR = "/task/.evaluation"
SANDBOXES = ("local", "docker")

# 校验「import 到的模块来自仓库」，避免 PyPI 上的同名包把仓库架空。
_REPO_IMPORT_PROBE = """\
import importlib, pathlib, sys
repo = pathlib.Path(sys.argv[1]).resolve()
bad = []
for name in sys.argv[2:]:
    try:
        origin = pathlib.Path(importlib.import_module(name).__file__).resolve()
    except Exception as error:
        bad.append((name, repr(error)))
        continue
    if repo not in origin.parents:
        bad.append((name, str(origin)))
print(bad)
sys.exit(1 if bad else 0)
"""


def _identifier(value, name: str) -> str:
    if (not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", value)):
        raise ValueError(f"{name} 必须为可用于路径的非空标识")
    return value


def _positive(value, name: str) -> float:
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or value <= 0):
        raise ValueError(f"{name} 必须为有限正数")
    return float(value)


def _resource(value, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} 必须为非空相对路径")
    relative = PurePosixPath(value)
    if relative.is_absolute() or ".." in relative.parts or "\\" in value:
        raise ValueError(f"{name} 必须是包内相对路径：{value!r}")
    return value


def _front_matter(path: Path) -> tuple[dict, str]:
    """读取 SKILL.md 的 YAML 前置元数据与正文。"""
    text = path.read_text(encoding="utf-8-sig")
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        raise ValueError(f"技能文件缺少 YAML 前置元数据：{path}")
    end = next((index for index, line in enumerate(lines[1:], 1) if line.strip() == "---"), None)
    if end is None:
        raise ValueError(f"技能文件的 YAML 前置元数据未闭合：{path}")
    metadata = yaml.safe_load("".join(lines[1:end]))
    if not isinstance(metadata, dict):
        raise ValueError(f"技能文件的前置元数据必须为映射：{path}")
    return metadata, "".join(lines[end + 1:]).strip()


def _skill_catalog(skills_dir: Path, task_dir: Path) -> list[dict[str, str]]:
    """为任务目录内的技能建立目录清单；路径相对任务根目录，供 read_file 直接使用。"""
    catalog: list[dict[str, str]] = []
    for skill_file in sorted(skills_dir.rglob("SKILL.md")):
        metadata, body = _front_matter(skill_file)
        if any(not isinstance(metadata.get(key), str) or not metadata[key].strip()
               for key in ("name", "description")):
            raise ValueError(f"技能 name 与 description 必须为非空字符串：{skill_file}")
        if not body:
            raise ValueError(f"技能正文为空：{skill_file}")
        relative = PurePosixPath(skill_file.relative_to(task_dir).as_posix())
        if "\\" in str(relative):
            raise ValueError(f"技能路径不能包含反斜杠：{relative}")
        catalog.append({
            "name": metadata["name"].strip(),
            "description": metadata["description"].strip(),
            "path": str(relative),
            "sha256": hashlib.sha256(skill_file.read_bytes()).hexdigest(),
        })
    names = [item["name"] for item in catalog]
    if len(names) != len(set(names)):
        raise ValueError(f"技能名称重复：{sorted({name for name in names if names.count(name) > 1})}")
    return catalog


def _instruction(prompt: str, task_id: str, description: str,
                 inputs: list[dict[str, str]], expected: str,
                 repo: str | None = None, sandbox: str = "local") -> str:
    """拼出 agent 收到的完整指令：通用规范 + 本次任务 + 仓库与输入位置 + 产物约定。"""
    # 上游模板用这个占位符指向代码仓库；有仓库快照时指向任务目录内的仓库副本，
    # 用 $(pwd) 让 shell 在运行时展开成绝对路径，满足上游「必须用绝对路径」的要求。
    prompt = prompt.replace("{remote_repo_path}", f"$(pwd)/{repo}" if repo else "$(pwd)")
    # 上游那一行的引号带反斜杠转义（\"），渲染时归一成普通引号，避免模型照抄出多余反斜杠。
    # 只影响发给模型的内容，上游文件保持原样。
    prompt = prompt.replace('\\"', '"')
    lines = [prompt.strip(), "", "---", "", "## 本次任务", "",
             f"任务 ID：{task_id}", "", description.strip(), "", "## 工作目录与输入素材", "",
             "当前工作目录即任务根目录，输入素材位于其下的 `input/`：", ""]
    lines += [f"- `{item['path']}`：{item['description']}" for item in inputs]
    if repo:
        lines += ["", "## 代码仓库", "",
                  f"本任务配套的仓库快照位于任务根目录下的 `{repo}`"
                  f"（在 terminal 中即 `$(pwd)/{repo}`），README.md、源码与 tests/ 都是完整副本。",
                  "请先读该仓库的 `README.md`，再读与本任务相关的源码，用仓库已有的能力完成任务。",
                  "该仓库已经用 `pip install -e` 装进当前 Python 环境，"
                  "`import` 到的是仓库内的源码；**不要**再从 PyPI 安装同名包覆盖它。"]
    if sandbox == "docker":
        repo_hint = (f"需要把仓库加进 PYTHONPATH 时，用 `export PYTHONPATH=\"$PYTHONPATH:/task/{repo}\"`。"
                     if repo else
                     '需要把任务根目录加进 PYTHONPATH 时，用 `export PYTHONPATH="$PYTHONPATH:/task"`。')
        runtime = ["本任务在 Linux 容器内执行（CPU、可联网），任务根目录固定为 `/task`。",
                   "terminal 的默认工作目录就是 `/task`；需要绝对路径时直接用 `/task/...`。"
                   "不要给 terminal 传 `/`、`/workspace`、`/root` 作为 cwd。",
                   repo_hint,
                   "read_file 和 write_file 的 path 参数不做 shell 展开，请写相对 `/task` 的路径"
                   "（如 `input/...`、`output/...`），不要写 `$(pwd)`。",
                   "Python 解释器用 `python3`。容器可联网，但**不要**从 PyPI 安装与仓库同名的包；"
                   "仓库本身已经装好。"]
    else:
        repo_hint = (f'需要把仓库加进 PYTHONPATH 时，用 `export PYTHONPATH="$PYTHONPATH:$(pwd)/{repo}"`。'
                     if repo else
                     '需要把任务根目录加进 PYTHONPATH 时，用 `export PYTHONPATH="$PYTHONPATH:$(pwd)"`。')
        runtime = ["本任务在 Windows 主机上执行，shell 为 Git Bash（MINGW64），不是 Linux 容器，"
                   "`/root`、`/workspace` 这类目录在本机不存在。",
                   'terminal 的默认 cwd 就是任务根目录：需要绝对路径时用 `$(pwd)` 取，'
                   '例如 `python "$(pwd)/extract.py"`；不要给 terminal 传 `/`、`/workspace`、'
                   '`/root` 或 `C:\\workspace` 作为 cwd。',
                   repo_hint,
                   "read_file 和 write_file 的 path 参数不做 shell 展开，请写相对任务根目录的路径"
                   "（如 `input/...`、`output/...`），不要写 `$(pwd)`。",
                   "Python 解释器请用 `python`，不要用 `python3`；apt、sudo 等命令不可用。"]
    lines += ["", "## 产物要求", "",
              f"评分脚本只读取一个文件：`{expected}`。",
              f"请把最终结果写入任务根目录下的 `{expected}`"
              f"（在 terminal 中即 `$(pwd)/{expected}`），文件名和扩展名必须完全一致。",
              "不要改动 `input/` 下的原始素材；中间文件可以放在同一目录下，但评分不读取它们。",
              "", "## 运行环境", "", *runtime, ""]
    return "\n".join(lines)


def _load_task(package, index: Path, row: dict, sandbox: str = "local") -> Task:
    task_id = _identifier(row.get("id"), "任务 ID")
    query_path = resource_path(package.root, _resource(row.get("query"), "query 路径"))
    query = json.loads(query_path.read_text(encoding="utf-8-sig"))
    if not isinstance(query, dict):
        raise ValueError(f"query 文件必须是 JSON 对象：{query_path}")
    if query.get("task_id") != task_id:
        raise ValueError(f"query 中的 task_id 与索引不一致：{query_path}")
    description = query.get("task_description")
    if not isinstance(description, str) or not description.strip():
        raise ValueError(f"任务描述不能为空：{query_path}")

    prompt_path = resource_path(package.root, _resource(row.get("prompt"), "prompt 路径"))
    prompt = prompt_path.read_text(encoding="utf-8-sig")
    if not prompt.strip():
        raise ValueError(f"prompt 模板不能为空：{prompt_path}")

    expected = _resource(row.get("expected_output") or f"output/{task_id}/output.txt",
                         "expected_output")
    # query 中的素材路径指向上游容器的绝对位置，这里改成本次任务目录内的相对位置。
    file_paths = query.get("file_paths") or {}
    inputs = []
    for item in file_paths.get("input_files") or []:
        source = item.get("path")
        if not isinstance(source, str) or not source.strip():
            continue
        name = PurePosixPath(source).name
        inputs.append({"path": f"input/{name}", "description": str(item.get("description") or "")})

    timeout = _positive(row.get("agent_timeout_sec", DEFAULT_TIMEOUT), "agent_timeout_sec")
    packages = row.get("python_packages") or []
    if not isinstance(packages, list) or any(
            not isinstance(name, str) or not name.strip() for name in packages):
        raise ValueError(f"python_packages 必须为字符串列表：{task_id}")

    # 仓库是 GitTaskBench 的任务本体：缺了它，任务就退化成「装个同名库调 API」。
    code_base = row.get("code_base")
    if code_base is not None:
        code_base = _resource(code_base, "code_base")
    install = row.get("install") or []
    if not isinstance(install, list) or any(
            not isinstance(command, str) or not command.strip() for command in install):
        raise ValueError(f"install 必须为命令字符串列表：{task_id}")
    if install and not code_base:
        raise ValueError(f"声明了 install 但没有 code_base，无法确定仓库位置：{task_id}")

    # 技能由生成器读同一份仓库产出；没有仓库就没有技能来源，直接拒绝配置。
    skills_dir = row.get("skills_dir")
    if skills_dir is not None:
        skills_dir = _resource(skills_dir, "skills_dir")
        if not code_base:
            raise ValueError(f"声明了 skills_dir 但没有 code_base，技能缺少来源仓库：{task_id}")

    return Task(
        id=task_id,
        instruction=_instruction(prompt, task_id, description, inputs, expected,
                                 code_base, sandbox),
        agent_timeout_sec=timeout,
        metadata={
            "task_description": description.strip(),
            "expected_output": expected,
            "python_packages": list(packages),
            "input_files": inputs,
            "code_base": code_base,
            "install": list(install),
            "skills_dir": skills_dir,
            # 记录指令按哪种运行环境生成；setup 阶段会与环境实际形态比对，不一致直接失败。
            "sandbox": sandbox,
        },
        source={
            "index": str(index), "row": row,
            "index_sha256": package.index_sha256,
            "dataset_sha256": package.dataset_sha256,
            "query_sha256": hashlib.sha256(query_path.read_bytes()).hexdigest(),
        },
    )


def load_tasks(index: Path, task_ids: list[str] | None = None, *,
               sandbox: str = "local") -> list[Task]:
    """读取索引中的任务；默认按索引顺序返回全部，指定 task_ids 时按其顺序返回。

    `sandbox` 决定指令按哪种运行环境描述（本机 Git Bash / Linux 容器），
    不改变任务内容本身；它与环境工厂的实际选择由 `setup_environment` 做硬校验。
    """
    if sandbox not in SANDBOXES:
        raise ValueError(f"sandbox 只能是 {SANDBOXES}，实际收到 {sandbox!r}")
    package = read_package_index(index, task_format=TASK_FORMAT)
    rows = package.index["tasks"]
    by_id = {row["id"]: row for row in rows}
    if task_ids is None:
        selected = [row["id"] for row in rows]
    else:
        if isinstance(task_ids, (str, bytes)) or not isinstance(task_ids, (list, tuple)):
            raise ValueError("task_ids 必须为任务 ID 列表")
        selected = list(task_ids)
        for task_id in selected:
            _identifier(task_id, "任务 ID")
        repeated = sorted({task_id for task_id in selected if selected.count(task_id) > 1})
        if repeated:
            raise ValueError(f"task_ids 中存在重复的任务 ID：{repeated}")
        missing = [task_id for task_id in selected if task_id not in by_id]
        if missing:
            raise ValueError(f"索引中不存在这些任务：{missing}")
    return [_load_task(package, index, by_id[task_id], sandbox) for task_id in selected]


def _stage_dockerfile(package_root: Path, destination: Path) -> bool:
    """把包内共享 Dockerfile 复制进任务目录，并注入镜像源与框架运行时。

    local 模式不需要镜像，缺少模板时返回 False；容器模式由 create_environment 拒绝。
    """
    template = package_root / "environment" / "Dockerfile"
    if not template.is_file():
        return False
    target = destination / "environment" / "Dockerfile"
    target.parent.mkdir(parents=True)
    shutil.copy2(template, target)
    from environment.tasks import prepare_container_task

    prepare_container_task(destination)
    return True


def prepare_task(task: Task, destination: Path, condition: str) -> PreparedTask:
    """把仓库快照与作答输入放进任务目录；真值和评分脚本留到评分阶段再读取。

    `no-skill` 与 `with-skill` 用同一份任务目录，唯一差别是后者多出包内技能副本；
    技能只从仓库产出，不含真值与评分脚本。
    """
    if condition not in {"no-skill", "with-skill"}:
        raise ValueError(f"不支持的实验条件：{condition}")
    row = task.source["row"]
    package = read_package_index(Path(task.source["index"]), task_format=TASK_FORMAT)
    if (package.index_sha256 != task.source["index_sha256"]
            or package.dataset_sha256 != task.source["dataset_sha256"]):
        raise ValueError("实验计划生成后，数据集描述或索引发生了变化")
    query_path = resource_path(package.root, _resource(row.get("query"), "query 路径"))
    if hashlib.sha256(query_path.read_bytes()).hexdigest() != task.source["query_sha256"]:
        raise ValueError(f"实验计划生成后，任务查询文件发生了变化：{task.id}")

    assets = resource_path(package.root, _resource(row.get("assets_dir"), "素材目录"))
    if not assets.is_dir():
        raise ValueError(f"素材目录不存在：{assets}")
    for path in assets.rglob("*"):
        if path.is_symlink() or path.is_junction():
            raise ValueError(f"不支持链接形式的素材：{path}")

    destination.mkdir(parents=True)
    shutil.copytree(assets, destination / "input")
    # 镜像与任务无关：所有题共用包内一份 Dockerfile，逐 trial 复制后注入镜像源
    # 与框架运行时（框架的 worker Python 需要独立于任务解释器）。
    _stage_dockerfile(package.root, destination)
    # 仓库按上游布局复制到任务目录，agent 才能在任务里读到 README 与源码。
    code_base = task.metadata.get("code_base")
    if code_base:
        repo = resource_path(package.root, _resource(code_base, "code_base"))
        if not repo.is_dir():
            raise ValueError(f"code_base 目录不存在：{repo}")
        for path in repo.rglob("*"):
            if path.is_symlink() or path.is_junction():
                raise ValueError(f"不支持链接形式的仓库文件：{path}")
        shutil.copytree(repo, destination / PurePosixPath(code_base))

    skills_dir: Path | None = None
    catalog: list[dict[str, str]] = []
    if condition == "with-skill":
        declared = task.metadata.get("skills_dir")
        if not declared:
            raise ValueError(f"with-skill 需要索引声明 skills_dir：{task.id}")
        source = resource_path(package.root, _resource(declared, "skills_dir"))
        if not source.is_dir():
            raise ValueError(f"技能目录不存在：{source}（可先用 datasets/skillgen.py 生成）")
        for path in source.rglob("*"):
            if path.is_symlink() or path.is_junction():
                raise ValueError(f"不支持链接形式的技能资源：{path}")
        skills_dir = destination / "skills"
        # 保留技能目录名，任务目录里就是 skills/<Repo>/SKILL.md，扩到多仓库时不会互相覆盖。
        shutil.copytree(source, skills_dir / source.name)
        catalog = _skill_catalog(skills_dir, destination)
        if not catalog:
            raise ValueError(f"with-skill 至少需要一个 SKILL.md：{source}")

    # benchflow 的任务包约定由 task.md 承载：frontmatter 描述沙箱，正文是给 agent 的指令。
    # 这里只借用它的镜像构建与容器生命周期，agent 循环和评分仍由本框架负责。
    front_matter = yaml.safe_dump({
        "schema_version": "1.3",
        "metadata": {"author_name": "GitTaskBench", "difficulty": "hard"},
        "agent": {"timeout_sec": float(task.agent_timeout_sec)},
        "sandbox": {
            "network_mode": "public",
            "os": "linux",
            "cpus": 1,
            "memory_mb": 4096,
            "storage_mb": 10240,
            "gpus": 0,
        },
    }, allow_unicode=True, sort_keys=False)
    (destination / "task.md").write_text(
        f"---\n{front_matter}---\n\n{task.instruction}\n", encoding="utf-8")

    write_json(destination / "task.json", {
        "task_id": task.id,
        "task_description": task.metadata["task_description"],
        "expected_output": task.metadata["expected_output"],
        "code_base": code_base,
        "condition": condition,
        "skills": [item["name"] for item in catalog],
    })
    return PreparedTask(task_dir=destination, skills_dir=skills_dir, skill_catalog=catalog)


def create_environment(task_dir: Path, *, output_dir: Path,
                       sandbox: str = "local", **options):
    """按 sandbox 选择执行环境：本机 Git Bash 或共享镜像的 Linux 容器。

    两种实现共用同一契约，评分链路只按任务根目录的绝对路径定位文件。
    """
    if sandbox not in SANDBOXES:
        raise ValueError(f"sandbox 只能是 {SANDBOXES}，实际收到 {sandbox!r}")
    if sandbox == "local":
        from environment.local import LocalEnvironment

        return LocalEnvironment(workdir=task_dir, **options)
    from environment import DockerEnvironment

    dockerfile = task_dir / "environment" / "Dockerfile"
    if not dockerfile.is_file():
        raise ValueError(f"容器模式需要任务目录内的镜像定义：{dockerfile}")
    return DockerEnvironment(task_dir, output_dir=output_dir, workdir=CONTAINER_ROOT,
                             **options)


def _python(context: TrialContext) -> str:
    executable = getattr(context.environment.executor, "python_executable", None)
    return str(executable) if executable else sys.executable


def _container(environment) -> bool:
    """判断本 trial 是否在容器内执行；只有这一处需要知道环境的具体实现。"""
    from environment.docker import DockerEnvironment

    return isinstance(environment, DockerEnvironment)


def _root(context: TrialContext) -> str:
    """任务根目录：容器内固定为 /task，本机则是准备出来的任务目录。"""
    if _container(context.environment):
        return CONTAINER_ROOT
    return context.prepared.task_dir.resolve().as_posix()


def _path(context: TrialContext, relative: str) -> str:
    return f"{_root(context)}/{PurePosixPath(relative).as_posix()}"


async def _container_test(context: TrialContext, *arguments: str) -> bool:
    """在容器内做一次存在性检查；超时按环境通道失败处理。"""
    result = await context.environment.execute(["test", *arguments], timeout=60)
    if result.timed_out:
        raise TimeoutError(f"检查任务目录内容超时：{' '.join(arguments)}")
    return result.success


async def _upload_task(context: TrialContext) -> None:
    """把任务目录整体放进容器；benchflow 的 upload_dir 会顺带修正 CRLF 行尾。"""
    await context.environment.upload(context.prepared.task_dir, CONTAINER_ROOT)
    if not await _container_test(context, "-f", f"{CONTAINER_ROOT}/task.json"):
        raise RuntimeError(f"任务目录没有完整进入容器：{CONTAINER_ROOT}")


async def setup_environment(context: TrialContext) -> None:
    """确认素材与仓库就位：先按索引命令安装仓库，再校验 import 指向仓库本身。"""
    task_dir = context.prepared.task_dir
    container = _container(context.environment)
    # 指令按哪种运行环境描述，必须与实际环境一致；否则指令里的路径全是错的。
    declared = context.task.metadata.get("sandbox", "local")
    actual = "docker" if container else "local"
    if declared != actual:
        raise ValueError(
            f"任务指令按 {declared} 生成，但环境实际是 {actual}；"
            "dataset.sandbox 与 environment 的工厂必须一致")
    if container:
        await _upload_task(context)

    expected = context.task.metadata["expected_output"]
    if container:
        parent = PurePosixPath(expected).parent.as_posix()
        made = await context.environment.execute(
            ["mkdir", "-p", f"{_root(context)}/{parent}"], timeout=60)
        if not made.success:
            raise RuntimeError(f"无法创建产物目录：{made.stderr.strip()[-500:]}")
        if not await _container_test(context, "-d", f"{CONTAINER_ROOT}/input"):
            raise ValueError("容器内任务目录缺少 input/")
    else:
        (task_dir / expected).parent.mkdir(parents=True, exist_ok=True)
        if not (task_dir / "input").is_dir():
            raise ValueError(f"任务目录缺少 input/：{task_dir}")

    # 技能属于实验条件：少带或多带都会改变被测能力，一律按配置错误处理，不静默继续。
    skills_dir = context.prepared.skills_dir
    catalog = context.prepared.skill_catalog
    if context.condition == "with-skill":
        if skills_dir is None or not catalog:
            raise ValueError("with-skill 任务准备阶段未提供技能")
        if container:
            missing = [item["path"] for item in catalog
                       if not await _container_test(context, "-f", _path(context, item["path"]))]
        else:
            missing = [item["path"] for item in catalog
                       if not (task_dir / PurePosixPath(item["path"])).is_file()]
        if missing:
            raise ValueError(f"任务目录缺少技能文件：{missing}")
    elif skills_dir is not None or catalog:
        raise ValueError("no-skill 任务准备阶段暴露了技能")
    elif container:
        if await _container_test(context, "-e", f"{CONTAINER_ROOT}/skills"):
            raise ValueError("no-skill 任务目录不应包含 skills/")
    elif (task_dir / "skills").exists():
        raise ValueError(f"no-skill 任务目录不应包含 skills/：{task_dir}")

    python = _python(context)
    code_base = context.task.metadata.get("code_base")
    repo: str | None = None
    if code_base:
        repo = _path(context, code_base)
        if container:
            if not await _container_test(context, "-d", repo):
                raise ValueError(f"容器内缺少仓库：{repo}")
        elif not (task_dir / PurePosixPath(code_base)).is_dir():
            raise ValueError(f"任务目录缺少仓库：{task_dir / code_base}")
        bash = getattr(context.environment.executor, "bash_executable", None) or "bash"
        for template in context.task.metadata.get("install") or []:
            command = (template.replace("{python}", shlex.quote(Path(python).as_posix()))
                               .replace("{repo}", shlex.quote(repo)))
            install = await context.environment.execute([str(bash), "-lc", command], timeout=900)
            if install.timed_out:
                raise TimeoutError(f"安装仓库超时：{command}")
            if not install.success:
                raise RuntimeError(f"安装仓库失败：{command}\n{install.stderr.strip()[-2000:]}")

    packages = context.task.metadata.get("python_packages") or []
    if not packages:
        return
    modules = [name.replace("-", "_").split("[")[0] for name in packages]
    if repo is not None:
        # 仓库任务：同名依赖必须解析到仓库源码，否则任务又退化成「装个库调 API」。
        probe = await context.environment.execute(
            [python, "-c", _REPO_IMPORT_PROBE, repo, *modules], timeout=120,
        )
        if probe.timed_out:
            raise TimeoutError(f"检查仓库依赖是否可导入时超时：{packages}")
        if not probe.success:
            raise RuntimeError(
                f"这些模块没有解析到仓库 {repo}，请检查 install 命令：{packages}\n"
                f"{probe.stdout.strip()[-1000:]}"
            )
        return
    probe = await context.environment.execute(
        [python, "-c", "import importlib.util as u, sys; "
                       f"sys.exit(0 if all(u.find_spec(m) for m in {modules!r}) else 1)"],
        timeout=120,
    )
    if probe.timed_out:
        raise TimeoutError(f"检查依赖是否已安装时超时：{packages}")
    if probe.success:
        return
    install = await context.environment.execute(
        [python, "-m", "pip", "install", "--disable-pip-version-check", *packages],
        timeout=900,
    )
    if install.timed_out:
        raise TimeoutError(f"安装依赖超时：{packages}")
    if not install.success:
        raise RuntimeError(f"安装依赖失败：{packages}\n{install.stderr.strip()[-2000:]}")


def _skill_usage(output_dir: Path) -> tuple[int, list[str]]:
    """从工具调用日志里数出 agent 实际读过哪些技能文件；只作观测，不参与判分。"""
    path = output_dir / "agent" / "tool-calls.jsonl"
    if not path.exists():
        return 0, []
    calls = 0
    files: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("type") != "call" or not isinstance(row.get("arguments"), dict):
            continue
        arguments = row["arguments"]
        # read_file 看 path，terminal 看 command：两种方式读到技能都算。
        candidates = [arguments.get("path"), arguments.get("command")]
        matched = [str(item).replace("\\", "/") for item in candidates
                   if isinstance(item, str) and "skills/" in item.replace("\\", "/")]
        if not matched:
            continue
        calls += 1
        for target in matched:
            if target not in files:
                files.append(target)
    return calls, files


async def evaluate(context: TrialContext, agent: AgentResult) -> Score:
    """在任务环境内运行上游 test_script，以其 JSONL 结论判定是否通过。"""
    row = context.task.source["row"]
    package = read_package_index(Path(context.task.source["index"]), task_format=TASK_FORMAT)
    groundtruth = resource_path(package.root, _resource(row.get("groundtruth"), "真值路径")).resolve()
    script = resource_path(package.root, _resource(row.get("test_script"), "评分脚本路径")).resolve()
    for path, name in ((groundtruth, "真值"), (script, "评分脚本")):
        if not path.is_file():
            raise FileNotFoundError(f"{name}文件缺失：{path}")

    container = _container(context.environment)
    expected = context.task.metadata["expected_output"]
    task_dir = context.prepared.task_dir.resolve()
    # 评分脚本以任务根目录为 cwd 运行，所有路径先解析成绝对路径，避免相对路径被二次拼接。
    prediction = _path(context, expected)
    result_file = context.output_dir.resolve() / "verifier" / "results.jsonl"
    result_file.parent.mkdir(parents=True, exist_ok=True)
    result_file.unlink(missing_ok=True)

    if container:
        # 评分材料在 agent 停止后才上传，运行期间不在容器里，也读不到。
        # 目录要先自己建：benchflow 的 upload_file 用宿主 Path 解析容器路径，
        # 在 Windows 上会把 /task/.evaluation 拼成反斜杠目录。
        made = await context.environment.execute(
            ["mkdir", "-p", EVALUATION_DIR], timeout=60)
        if not made.success:
            raise RuntimeError(f"无法创建评分目录：{made.stderr.strip()[-500:]}")
        gt_name = f"groundtruth{groundtruth.suffix}"
        await context.environment.upload(script, f"{EVALUATION_DIR}/test_script.py")
        await context.environment.upload(groundtruth, f"{EVALUATION_DIR}/{gt_name}")
        script_arg, gt_arg = f"{EVALUATION_DIR}/test_script.py", f"{EVALUATION_DIR}/{gt_name}"
        result_arg = f"{EVALUATION_DIR}/results.jsonl"
    else:
        script_arg, gt_arg, result_arg = str(script), str(groundtruth), str(result_file)

    python = _python(context)
    run = await context.environment.execute(
        [python, script_arg, "--output", prediction,
         "--groundtruth", gt_arg, "--result", result_arg],
        cwd=_root(context), timeout=600,
    )
    stdout = run.stdout or ""

    if container:
        if await _container_test(context, "-f", result_arg):
            await context.environment.download(result_arg, result_file)
        # 产物也取回宿主，既便于复核，也让两种模式的目录布局保持一致。
        if await _container_test(context, "-f", prediction):
            local_prediction = task_dir / PurePosixPath(expected)
            local_prediction.parent.mkdir(parents=True, exist_ok=True)
            await context.environment.download(prediction, local_prediction)

    rows = []
    if result_file.exists():
        rows = [json.loads(line) for line in
                result_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    # 上游脚本按追加写入：取本次运行的最后一条结论。
    verdict = rows[-1] if rows else None
    process = bool(isinstance(verdict, dict) and verdict.get("Process"))
    result = bool(isinstance(verdict, dict) and verdict.get("Result"))
    similarity = None
    match = re.search(r"similarity score:\s*([0-9.]+)%", stdout)
    if match:
        similarity = float(match.group(1))

    local_prediction = task_dir / PurePosixPath(expected)
    passed = process and result and not run.timed_out and run.exit_code == 0
    skill_calls, skill_files = _skill_usage(context.output_dir)
    metrics = {
        "reward_basis": "official_test_script",
        "sandbox": "docker" if container else "local",
        "process": process,
        "result": result,
        "prediction_exists": local_prediction.is_file(),
        "prediction_bytes": local_prediction.stat().st_size if local_prediction.is_file() else 0,
        "similarity": similarity,
        "exit_code": run.exit_code,
        "timed_out": bool(run.timed_out),
        "comments": verdict.get("comments", "") if isinstance(verdict, dict) else "",
        # 技能只在 with-skill 条件出现；这两个字段用于核对「技能是否真被使用」。
        "skill_catalog": [item["name"] for item in context.prepared.skill_catalog],
        "skill_calls": skill_calls,
        "skill_files_read": skill_files,
    }
    write_json(context.output_dir / "verifier" / "result.json", {
        "reward": float(passed), "metrics": metrics, "verdict": verdict,
        "stdout_tail": stdout.strip()[-4000:], "stderr_tail": (run.stderr or "").strip()[-4000:],
    })
    return Score(reward=float(passed), metrics=metrics)
