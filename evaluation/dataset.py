"""加载配置的候选任务，并可选地在每次运行时抽样一次。"""

from pathlib import Path
import random

from .config import load_function
from .contracts import Task


def load_tasks(dataset: dict) -> list[Task]:
    """保留加载器的 (index, task_ids) 接口约定，并进行不放回抽样。"""
    sample_size = dataset.get("sample_size")
    seed = dataset.get("seed")
    if sample_size is not None and (type(sample_size) is not int or sample_size < 1):
        raise ValueError("dataset.sample_size 必须为正整数")
    if seed is not None:
        if type(seed) is not int:
            raise ValueError("dataset.seed 必须为整数")
        if sample_size is None:
            raise ValueError("使用 dataset.seed 时必须配置 dataset.sample_size")

    loader = load_function(dataset["loader"])
    tasks = list(loader(Path(dataset["index"]), dataset.get("task_ids")))
    if sample_size is None:
        return tasks
    if sample_size > len(tasks):
        raise ValueError(
            f"dataset.sample_size ({sample_size}）超过候选任务数量（{len(tasks)})"
        )
    return random.Random(seed).sample(tasks, sample_size)
