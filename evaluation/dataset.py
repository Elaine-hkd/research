"""Load the configured candidate tasks and optionally sample them once per run."""

from pathlib import Path
import random

from .config import load_function
from .contracts import Task


def load_tasks(dataset: dict) -> list[Task]:
    """Keep the loader's (index, task_ids) contract and sample without replacement."""
    sample_size = dataset.get("sample_size")
    seed = dataset.get("seed")
    if sample_size is not None and (type(sample_size) is not int or sample_size < 1):
        raise ValueError("dataset.sample_size must be a positive integer")
    if seed is not None:
        if type(seed) is not int:
            raise ValueError("dataset.seed must be an integer")
        if sample_size is None:
            raise ValueError("dataset.seed requires dataset.sample_size")

    loader = load_function(dataset["loader"])
    tasks = list(loader(Path(dataset["index"]), dataset.get("task_ids")))
    if sample_size is None:
        return tasks
    if sample_size > len(tasks):
        raise ValueError(
            f"dataset.sample_size ({sample_size}) exceeds the number of candidate tasks ({len(tasks)})"
        )
    return random.Random(seed).sample(tasks, sample_size)
