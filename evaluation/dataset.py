"""加载配置的候选任务，排除忽略项，并可选地在每次运行时抽样一次。"""

from pathlib import Path
import inspect
import random

from .config import load_function
from .contracts import Task


def excluded_task_ids(dataset: dict) -> set[str]:
    """统一校验新实验与续跑使用的忽略列表。"""
    excluded_ids = dataset.get("exclude_task_ids", [])
    if (not isinstance(excluded_ids, list)
            or any(not isinstance(task_id, str) or not task_id.strip() for task_id in excluded_ids)):
        raise ValueError("dataset.exclude_task_ids 必须为非空任务 ID 字符串组成的列表")
    return set(excluded_ids)


def load_tasks(dataset: dict, *, retained_tasks: list[Task] | None = None,
               previous_task_ids: set[str] | None = None) -> list[Task]:
    """新实验先排除再抽样；续跑保留已有任务，仅补抽缺少的数量。"""
    excluded = excluded_task_ids(dataset)
    sample_size = dataset.get("sample_size")
    seed = dataset.get("seed")
    if sample_size is not None and (type(sample_size) is not int or sample_size < 1):
        raise ValueError("dataset.sample_size 必须为正整数")
    if seed is not None:
        if type(seed) is not int:
            raise ValueError("dataset.seed 必须为整数")
        if sample_size is None:
            raise ValueError("使用 dataset.seed 时必须配置 dataset.sample_size")

    retained = []
    used_ids = set(previous_task_ids or ())
    needed = sample_size
    if retained_tasks is not None:
        retained = [task for task in retained_tasks if task.id not in excluded]
        if sample_size is None:
            return retained
        needed = sample_size - len(retained)
        if needed <= 0:
            return retained
        used_ids.update(task.id for task in retained_tasks)

    loader = load_function(dataset["loader"])
    # 只有声明了 sandbox 参数的加载器才需要它：指令里的路径描述随运行环境变化。
    options = {}
    if "sandbox" in inspect.signature(loader).parameters:
        options["sandbox"] = dataset.get("sandbox", "local")
    tasks = list(loader(Path(dataset["index"]), dataset.get("task_ids"), **options))
    candidates = [task for task in tasks if task.id not in excluded and task.id not in used_ids]
    if needed is None:
        if excluded and not candidates:
            raise ValueError("应用 dataset.exclude_task_ids 后候选任务为空")
        return candidates
    if needed > len(candidates):
        if retained_tasks is not None:
            raise ValueError(
                f"需要补抽 {needed} 个任务，但排除忽略项及历史已抽任务后仅剩 {len(candidates)} 个候选任务"
            )
        raise ValueError(
            f"dataset.sample_size ({sample_size}）超过候选任务数量（{len(candidates)})"
        )
    return retained + random.Random(seed).sample(candidates, needed)
