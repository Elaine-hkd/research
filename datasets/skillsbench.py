"""Adapt the split SkillsBench index without changing source data."""

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
    """Preserve SkillsBench image WORKDIR and per-trial Docker isolation."""
    from environment import DockerEnvironment

    return DockerEnvironment(task_dir, output_dir=output_dir, workdir=None,
                             cleanup_volumes=True, **options)


async def setup_environment(context: TrialContext) -> None:
    """Inject only the skills selected by this dataset's preparation policy."""
    registry = context.environment.executor.registry
    if registry is not None:
        skill_names = frozenset(skill["name"] for skill in context.prepared.skill_catalog)
        if registry.allowed is not None and not skill_names.issubset(registry.allowed):
            raise ValueError("environment.allowed_skills excludes skills required by this task")
        registry.allowed = skill_names
    if context.condition == "with-skill":
        if context.prepared.skills_dir is None:
            raise ValueError("with-skill task preparation did not supply skills_dir")
        if registry is not None:
            # Missing required registry data is a setup failure, not a solver
            # score of zero. Do not expose another task's skills as a fallback.
            for skill_name in sorted(registry.allowed):
                await asyncio.to_thread(registry.load_skill, skill_name)
        exists = await context.environment.execute(["test", "!", "-e", "/skills"])
        if not exists.success:
            raise ValueError("Task image already contains /skills; refuse to merge skill sources")
        await context.environment.upload(context.prepared.skills_dir, "/skills")
    elif context.condition == "no-skill":
        if context.prepared.skills_dir is not None or context.prepared.skill_catalog:
            raise ValueError("no-skill task preparation exposed skills")
    else:
        raise ValueError(f"Unsupported SkillsBench condition: {context.condition}")


async def evaluate(context: TrialContext, agent: AgentResult) -> Score:
    from environment import DockerEnvironment

    if not isinstance(context.environment, DockerEnvironment):
        raise TypeError("The native SkillsBench verifier requires DockerEnvironment")
    rewards = await context.environment.verify()
    if "reward" not in rewards:
        raise ValueError("Verifier rewards must contain an aggregate 'reward'")
    return Score(reward=rewards["reward"], metrics=rewards)


def _document(path: Path) -> tuple[dict, str]:
    text = path.read_text(encoding="utf-8-sig")
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        raise ValueError(f"Missing YAML frontmatter: {path}")
    end = next((i for i, line in enumerate(lines[1:], 1) if line.strip() == "---"), None)
    if end is None:
        raise ValueError(f"Unclosed YAML frontmatter: {path}")
    metadata = yaml.safe_load("".join(lines[1:end]))
    if not isinstance(metadata, dict):
        raise ValueError(f"Frontmatter must be a mapping: {path}")
    return metadata, "".join(lines[end + 1:]).strip()


def _source(index: Path, value: str) -> Path:
    path = Path(value)
    path = path if path.is_absolute() else index.parent / path
    if path.is_symlink() or path.is_junction():
        raise ValueError(f"Linked dataset resources are unsupported: {path}")
    return path.resolve(strict=True)


def load_tasks(index: Path, task_ids: list[str] | None = None) -> list[Task]:
    """Return tasks in index order, or in the explicitly requested order."""
    index = Path(index).resolve(strict=True)
    raw = index.read_bytes()
    document = json.loads(raw.decode("utf-8-sig"))
    if document.get("schema_version") != 1 or not isinstance(document.get("tasks"), list):
        raise ValueError("Expected a schema_version=1 SkillsBench index with tasks")
    rows = document["tasks"]
    by_id = {}
    for row in rows:
        task_id = row["id"]
        if not isinstance(task_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", task_id):
            raise ValueError(f"Unsupported task id: {task_id!r}")
        if task_id in by_id:
            raise ValueError(f"Duplicate task id: {task_id}")
        by_id[task_id] = row
    selected = list(by_id) if task_ids is None else task_ids
    if not selected or len(set(selected)) != len(selected):
        raise ValueError("Task selection must be non-empty and contain no duplicates")
    unknown = set(selected) - by_id.keys()
    if unknown:
        raise ValueError(f"Unknown task ids: {sorted(unknown)}")
    tasks = []
    for task_id in selected:
        row = by_id[task_id]
        task_file = _source(index, row["task_file"])
        metadata, instruction = _document(task_file)
        if not instruction:
            raise ValueError(f"Empty task instruction: {task_file}")
        for key in ("environment_dir", "verifiler_dir", "oracle_dir", "skills_dir"):
            directory = _source(index, row[key])
            if not directory.is_dir():
                raise ValueError(f"{key} must be a directory: {directory}")
        timeout = metadata.get("agent", {}).get("timeout_sec", 900)
        if isinstance(timeout, bool) or not isinstance(timeout, (float, int)) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError(f"Invalid agent timeout in {task_file}")
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
                raise ValueError(f"Fresh trials cannot share fixed/external {section}: {name}")
    for name, service in (compose.get("services") or {}).items():
        if service.get("container_name"):
            raise ValueError(f"Fresh trials require project-scoped container names: {name}")
        for mount in service.get("volumes") or []:
            if isinstance(mount, str):
                # BenchFlow expands these to this trial's unique output directory.
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
                    raise ValueError(f"Writable host mount escapes the trial task: {source}")


def prepare_task(task: Task, destination: Path, condition: str) -> PreparedTask:
    """Use identical skill-free images; inject indexed skills at runtime only."""
    from benchflow.skill_policy import strip_task_bundled_skills
    from environment import materialize_skillsbench_task

    index = Path(task.source["index"])
    if hashlib.sha256(index.read_bytes()).hexdigest() != task.source["index_sha256"]:
        raise ValueError("Dataset index changed after the experiment was planned")
    task_file = _source(index, task.source["row"]["task_file"])
    if hashlib.sha256(task_file.read_bytes()).hexdigest() != task.source["task_sha256"]:
        raise ValueError(f"Task instruction changed after planning: {task.id}")
    task_dir = materialize_skillsbench_task(index, task.id, destination)
    # This also removes Dockerfile COPY skills instructions (e.g. radar-vital-signs).
    # Both conditions use runtime injection, so no-skill cannot inherit bundled files.
    strip_task_bundled_skills(task_dir)
    _check_compose_isolation(task_dir)
    if condition == "no-skill":
        return PreparedTask(task_dir)
    if condition != "with-skill":
        raise ValueError(f"Unsupported condition: {condition}")
    source = _source(index, task.source["row"]["skills_dir"])
    for path in source.rglob("*"):
        if path.is_symlink() or path.is_junction():
            raise ValueError(f"Linked skill resource is unsupported: {path}")
    skills_dir = destination.parent / "skills"
    shutil.copytree(source, skills_dir)
    catalog = []
    for skill in task.source["row"]["skills"]:
        relative_dir = PurePosixPath(skill["relative_path"])
        if relative_dir.is_absolute() or ".." in relative_dir.parts or "\\" in skill["relative_path"]:
            raise ValueError(f"Invalid indexed skill path: {relative_dir}")
        skill_file = skills_dir.joinpath(*relative_dir.parts) / "SKILL.md"
        metadata, _ = _document(skill_file)
        if any(not isinstance(metadata.get(key), str) or not metadata[key].strip()
               for key in ("name", "description")):
            raise ValueError(f"Skill name and description must be non-empty strings: {skill_file}")
        relative = PurePosixPath(skill_file.relative_to(skills_dir).as_posix())
        catalog.append({
            "name": str(metadata["name"]),
            "description": str(metadata["description"]),
            "path": str(PurePosixPath("/skills") / relative),
            "sha256": hashlib.sha256(skill_file.read_bytes()).hexdigest(),
        })
    if not catalog:
        raise ValueError(f"with-skill requires at least one SKILL.md: {source}")
    return PreparedTask(task_dir, skills_dir, catalog)
