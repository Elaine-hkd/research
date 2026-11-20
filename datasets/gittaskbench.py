"""将 GitTaskBench 的仓库级任务接入实验流程，按上游 test_script 评分。"""

import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import re
import shlex
import shutil
import sys

from datasets.package import read_package_index, resource_path
from evaluation.contracts import AgentResult, PreparedTask, Score, Task, TrialContext
from utils.json_io import write_json


TASK_FORMAT = "research-gittaskbench-v1"
DEFAULT_TIMEOUT = 1800

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


def _instruction(prompt: str, task_id: str, description: str,
                 inputs: list[dict[str, str]], expected: str,
                 repo: str | None = None) -> str:
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
    repo_hint = (f'需要把仓库加进 PYTHONPATH 时，用 `export PYTHONPATH="$PYTHONPATH:$(pwd)/{repo}"`。'
                 if repo else
                 '需要把任务根目录加进 PYTHONPATH 时，用 `export PYTHONPATH="$PYTHONPATH:$(pwd)"`。')
    lines += ["", "## 产物要求", "",
              f"评分脚本只读取一个文件：`{expected}`。",
              f"请把最终结果写入任务根目录下的 `{expected}`"
              f"（在 terminal 中即 `$(pwd)/{expected}`），文件名和扩展名必须完全一致。",
              "不要改动 `input/` 下的原始素材；中间文件可以放在同一目录下，但评分不读取它们。",
              "", "## 运行环境", "",
              "本任务在 Windows 主机上执行，shell 为 Git Bash（MINGW64），不是 Linux 容器，"
              "`/root`、`/workspace` 这类目录在本机不存在。",
              'terminal 的默认 cwd 就是任务根目录：需要绝对路径时用 `$(pwd)` 取，'
              '例如 `python "$(pwd)/extract.py"`；不要给 terminal 传 `/`、`/workspace`、'
              '`/root` 或 `C:\\workspace` 作为 cwd。',
              repo_hint,
              "read_file 和 write_file 的 path 参数不做 shell 展开，请写相对任务根目录的路径"
              "（如 `input/...`、`output/...`），不要写 `$(pwd)`。",
              "Python 解释器请用 `python`，不要用 `python3`；apt、sudo 等命令不可用。", ""]
    return "\n".join(lines)


def _load_task(package, index: Path, row: dict) -> Task:
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

    return Task(
        id=task_id,
        instruction=_instruction(prompt, task_id, description, inputs, expected, code_base),
        agent_timeout_sec=timeout,
        metadata={
            "task_description": description.strip(),
            "expected_output": expected,
            "python_packages": list(packages),
            "input_files": inputs,
            "code_base": code_base,
            "install": list(install),
        },
        source={
            "index": str(index), "row": row,
            "index_sha256": package.index_sha256,
            "dataset_sha256": package.dataset_sha256,
            "query_sha256": hashlib.sha256(query_path.read_bytes()).hexdigest(),
        },
    )


def load_tasks(index: Path, task_ids: list[str] | None = None) -> list[Task]:
    """读取索引中的任务；默认按索引顺序返回全部，指定 task_ids 时按其顺序返回。"""
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
    return [_load_task(package, index, by_id[task_id]) for task_id in selected]


def prepare_task(task: Task, destination: Path, condition: str) -> PreparedTask:
    """把仓库快照与作答输入放进任务目录；真值和评分脚本留到评分阶段再读取。"""
    if condition != "no-skill":
        raise ValueError("GitTaskBench 没有技能维度，只支持 no-skill 条件")
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
    write_json(destination / "task.json", {
        "task_id": task.id,
        "task_description": task.metadata["task_description"],
        "expected_output": task.metadata["expected_output"],
        "code_base": code_base,
    })
    return PreparedTask(task_dir=destination)


def create_environment(task_dir: Path, *, output_dir: Path, **options):
    """在本机执行任务；需要容器隔离时改由 DockerEnvironment 承担同一契约。"""
    from environment.local import LocalEnvironment

    return LocalEnvironment(workdir=task_dir, **options)


def _python(context: TrialContext) -> str:
    executable = getattr(context.environment.executor, "python_executable", None)
    return str(executable) if executable else sys.executable


async def setup_environment(context: TrialContext) -> None:
    """确认素材与仓库就位：先按索引命令安装仓库，再校验 import 指向仓库本身。"""
    task_dir = context.prepared.task_dir
    expected = task_dir / context.task.metadata["expected_output"]
    expected.parent.mkdir(parents=True, exist_ok=True)
    if not (task_dir / "input").is_dir():
        raise ValueError(f"任务目录缺少 input/：{task_dir}")

    python = _python(context)
    code_base = context.task.metadata.get("code_base")
    repo: Path | None = None
    if code_base:
        repo = (task_dir / PurePosixPath(code_base)).resolve()
        if not repo.is_dir():
            raise ValueError(f"任务目录缺少仓库：{repo}")
        bash = getattr(context.environment.executor, "bash_executable", None) or "bash"
        for template in context.task.metadata.get("install") or []:
            command = (template.replace("{python}", shlex.quote(Path(python).as_posix()))
                               .replace("{repo}", shlex.quote(repo.as_posix())))
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
            [python, "-c", _REPO_IMPORT_PROBE, str(repo), *modules], timeout=120,
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


async def evaluate(context: TrialContext, agent: AgentResult) -> Score:
    """在任务环境内运行上游 test_script，以其 JSONL 结论判定是否通过。"""
    row = context.task.source["row"]
    package = read_package_index(Path(context.task.source["index"]), task_format=TASK_FORMAT)
    groundtruth = resource_path(package.root, _resource(row.get("groundtruth"), "真值路径")).resolve()
    script = resource_path(package.root, _resource(row.get("test_script"), "评分脚本路径")).resolve()
    for path, name in ((groundtruth, "真值"), (script, "评分脚本")):
        if not path.is_file():
            raise FileNotFoundError(f"{name}文件缺失：{path}")

    # 评分脚本以任务目录为 cwd 运行，所有路径先解析成绝对路径，避免相对路径被二次拼接。
    task_dir = context.prepared.task_dir.resolve()
    prediction = (task_dir / context.task.metadata["expected_output"]).resolve()
    result_file = context.output_dir.resolve() / "verifier" / "results.jsonl"
    result_file.parent.mkdir(parents=True, exist_ok=True)
    result_file.unlink(missing_ok=True)

    python = _python(context)
    run = await context.environment.execute(
        [python, str(script), "--output", str(prediction),
         "--groundtruth", str(groundtruth), "--result", str(result_file)],
        cwd=str(task_dir), timeout=600,
    )
    stdout = run.stdout or ""

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

    passed = process and result and not run.timed_out and run.exit_code == 0
    metrics = {
        "reward_basis": "official_test_script",
        "process": process,
        "result": result,
        "prediction_exists": prediction.is_file(),
        "prediction_bytes": prediction.stat().st_size if prediction.is_file() else 0,
        "similarity": similarity,
        "exit_code": run.exit_code,
        "timed_out": bool(run.timed_out),
        "comments": verdict.get("comments", "") if isinstance(verdict, dict) else "",
    }
    write_json(context.output_dir / "verifier" / "result.json", {
        "reward": float(passed), "metrics": metrics, "verdict": verdict,
        "stdout_tail": stdout.strip()[-4000:], "stderr_tail": (run.stderr or "").strip()[-4000:],
    })
    return Score(reward=float(passed), metrics=metrics)
