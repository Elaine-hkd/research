"""适配拆分后的 SkillsBench 索引，保留原始数据。"""

import asyncio
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import re
import shutil

import yaml

from evaluation.contracts import AgentResult, PreparedTask, Score, Task, TrialContext


def create_environment(task_dir: Path, *, output_dir: Path, **options):
    """保留 SkillsBench 镜像的 WORKDIR，并隔离每次试验的 Docker 环境。"""
    from environment import DockerEnvironment

    return DockerEnvironment(task_dir, output_dir=output_dir, workdir=None,
                             cleanup_volumes=True, **options)


async def setup_environment(context: TrialContext) -> None:
    """仅注入本数据集准备策略选中的技能。"""
    registry = context.environment.executor.registry
    if registry is not None:
        skill_names = frozenset(skill["name"] for skill in context.prepared.skill_catalog)
        if registry.allowed is not None and not skill_names.issubset(registry.allowed):
            raise ValueError("environment.allowed_skills 排除了本任务必需的技能")
        registry.allowed = skill_names
    if context.condition == "with-skill":
        if context.prepared.skills_dir is None:
            raise ValueError("with-skill 任务准备阶段未提供 skills_dir")
        if registry is not None:
            # 缺少必需的注册中心数据属于环境设置失败，不能记作求解得分为零。
            # 不得使用其他任务的技能作为回退。
            for skill_name in sorted(registry.allowed):
                await asyncio.to_thread(registry.load_skill, skill_name)
        exists = await context.environment.execute(["test", "!", "-e", "/skills"])
        if exists.timed_out:
            raise TimeoutError("检查任务镜像是否包含 /skills 时超时")
        if not exists.success:
            raise ValueError("任务镜像已包含 /skills，拒绝合并不同技能来源")
        await context.environment.upload(context.prepared.skills_dir, "/skills")
    elif context.condition == "no-skill":
        if context.prepared.skills_dir is not None or context.prepared.skill_catalog:
            raise ValueError("no-skill 任务准备阶段暴露了技能")
    else:
        raise ValueError(f"不支持的 SkillsBench 实验条件：{context.condition}")


async def evaluate(context: TrialContext, agent: AgentResult) -> Score:
    from environment import DockerEnvironment

    if not isinstance(context.environment, DockerEnvironment):
        raise TypeError("原生 SkillsBench 评分器需要 DockerEnvironment")
    rewards = await context.environment.verify()
    if "reward" not in rewards:
        raise ValueError("评分器返回的奖励必须包含汇总字段 'reward'")
    return Score(reward=rewards["reward"], metrics=rewards)


def _document(path: Path) -> tuple[dict, str]:
    text = path.read_text(encoding="utf-8-sig")
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        raise ValueError(f"缺少 YAML 前置元数据：{path}")
    end = next((i for i, line in enumerate(lines[1:], 1) if line.strip() == "---"), None)
    if end is None:
        raise ValueError(f"YAML 前置元数据未闭合：{path}")
    metadata = yaml.safe_load("".join(lines[1:end]))
    if not isinstance(metadata, dict):
        raise ValueError(f"前置元数据必须为映射：{path}")
    return metadata, "".join(lines[end + 1:]).strip()


def _source(index: Path, value: str) -> Path:
    path = Path(value)
    path = path if path.is_absolute() else index.parent / path
    if path.is_symlink() or path.is_junction():
        raise ValueError(f"不支持链接形式的数据集资源：{path}")
    return path.resolve(strict=True)


def load_tasks(index: Path, task_ids: list[str] | None = None) -> list[Task]:
    """按索引顺序或显式指定的顺序返回任务。"""
    index = Path(index).resolve(strict=True)
    raw = index.read_bytes()
    document = json.loads(raw.decode("utf-8-sig"))
    if document.get("schema_version") != 1 or not isinstance(document.get("tasks"), list):
        raise ValueError("需要包含 tasks 且 schema_version=1 的 SkillsBench 索引")
    rows = document["tasks"]
    by_id = {}
    for row in rows:
        task_id = row["id"]
        if not isinstance(task_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", task_id):
            raise ValueError(f"不支持的任务 ID：{task_id!r}")
        if task_id in by_id:
            raise ValueError(f"重复的任务 ID：{task_id}")
        by_id[task_id] = row
    selected = list(by_id) if task_ids is None else task_ids
    if not selected or len(set(selected)) != len(selected):
        raise ValueError("任务选择不能为空，也不能包含重复项")
    unknown = set(selected) - by_id.keys()
    if unknown:
        raise ValueError(f"未知任务 ID：{sorted(unknown)}")
    tasks = []
    for task_id in selected:
        row = by_id[task_id]
        task_file = _source(index, row["task_file"])
        metadata, instruction = _document(task_file)
        if not instruction:
            raise ValueError(f"任务指令为空：{task_file}")
        for key in ("environment_dir", "verifiler_dir", "oracle_dir", "skills_dir"):
            directory = _source(index, row[key])
            if not directory.is_dir():
                raise ValueError(f"{key} 必须为目录：{directory}")
        timeout = metadata.get("agent", {}).get("timeout_sec", 900)
        if isinstance(timeout, bool) or not isinstance(timeout, (float, int)) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError(f"智能体超时设置无效，文件：{task_file}")
        tasks.append(Task(
            id=task_id, instruction=instruction, metadata=metadata,
            agent_timeout_sec=timeout,
            source={"index": str(index), "row": row,
                    "index_sha256": hashlib.sha256(raw).hexdigest(),
                    "task_sha256": hashlib.sha256(task_file.read_bytes()).hexdigest()},
        ))
    return tasks


def _check_compose_isolation(task_dir: Path) -> None:
    compose_path = task_dir / "environment" / "docker-compose.yaml"
    if not compose_path.exists():
        return
    compose = yaml.safe_load(compose_path.read_text(encoding="utf-8")) or {}
    for section in ("volumes", "networks"):
        for name, config in (compose.get(section) or {}).items():
            if config and (config.get("external") or config.get("name")):
                raise ValueError(f"新试验不能共享固定或外部的资源 {section}: {name}")
    for name, service in (compose.get("services") or {}).items():
        if service.get("container_name"):
            raise ValueError(f"新试验需要使用项目范围内的容器名称：{name}")
        for mount in service.get("volumes") or []:
            if isinstance(mount, str):
                # BenchFlow 将这些变量展开为本次试验独有的输出目录。
                if re.match(r"^\$\{HOST_(?:VERIFIER_LOGS|AGENT_LOGS|ARTIFACTS)_PATH(?::-[^}]+)?\}:", mount):
                    continue
                parts = mount.split(":")
                source = parts[0]
                readonly = len(parts) > 2 and "ro" in parts[-1].split(",")
                bind = source.startswith((".", "/", "~")) or "${" in source or "\\" in source
            else:
                source = mount.get("source", "")
                readonly = mount.get("read_only", False)
                bind = mount.get("type") == "bind"
            if bind and not readonly:
                path = (compose_path.parent / source).resolve()
                if not source.startswith(".") or not path.is_relative_to(task_dir):
                    raise ValueError(f"可写主机挂载超出了本次试验任务目录：{source}")


def prepare_task(task: Task, destination: Path, condition: str) -> PreparedTask:
    """使用相同的不含技能的镜像，仅在运行时注入索引中的技能。"""
    from benchflow.skill_policy import strip_task_bundled_skills
    from environment import materialize_skillsbench_task

    index = Path(task.source["index"])
    if hashlib.sha256(index.read_bytes()).hexdigest() != task.source["index_sha256"]:
        raise ValueError("实验计划生成后，数据集索引发生了变化")
    task_file = _source(index, task.source["row"]["task_file"])
    if hashlib.sha256(task_file.read_bytes()).hexdigest() != task.source["task_sha256"]:
        raise ValueError(f"计划生成后，任务指令发生了变化：{task.id}")
    task_dir = materialize_skillsbench_task(index, task.id, destination)
    # 同时移除 Dockerfile 中复制技能的 COPY 指令（如 radar-vital-signs）。
    # 两种实验条件均在运行时注入技能，避免 no-skill 继承镜像内置文件。
    strip_task_bundled_skills(task_dir)
    _check_compose_isolation(task_dir)
    if condition == "no-skill":
        return PreparedTask(task_dir)
    if condition != "with-skill":
        raise ValueError(f"不支持的实验条件：{condition}")
    source = _source(index, task.source["row"]["skills_dir"])
    for path in source.rglob("*"):
        if path.is_symlink() or path.is_junction():
            raise ValueError(f"不支持链接形式的技能资源：{path}")
    skills_dir = destination.parent / "skills"
    shutil.copytree(source, skills_dir)
    catalog = []
    for skill in task.source["row"]["skills"]:
        relative_dir = PurePosixPath(skill["relative_path"])
        if relative_dir.is_absolute() or ".." in relative_dir.parts or "\\" in skill["relative_path"]:
            raise ValueError(f"索引中的技能路径无效：{relative_dir}")
        skill_file = skills_dir.joinpath(*relative_dir.parts) / "SKILL.md"
        metadata, _ = _document(skill_file)
        if any(not isinstance(metadata.get(key), str) or not metadata[key].strip()
               for key in ("name", "description")):
            raise ValueError(f"技能名称和描述必须为非空字符串：{skill_file}")
        relative = PurePosixPath(skill_file.relative_to(skills_dir).as_posix())
        catalog.append({
            "name": str(metadata["name"]),
            "description": str(metadata["description"]),
            "path": str(PurePosixPath("/skills") / relative),
            "sha256": hashlib.sha256(skill_file.read_bytes()).hexdigest(),
        })
    if not catalog:
        raise ValueError(f"with-skill 至少需要一个 SKILL.md：{source}")
    return PreparedTask(task_dir, skills_dir, catalog)
