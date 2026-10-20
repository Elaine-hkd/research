"""A TOML experiment file and ordinary importable function references."""

from pathlib import Path
import re
import tomllib

from tools.loader import validate_tools
from harness.runner import load_function


def function_reference(value, name: str) -> None:
    if (not isinstance(value, str)
            or not re.fullmatch(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*:[A-Za-z_]\w*", value)):
        raise ValueError(f"{name} must be a module:function reference")


def load_config(path: Path) -> dict:
    path = Path(path).resolve(strict=True)
    config = tomllib.loads(path.read_text(encoding="utf-8-sig"))
    allowed = {"name", "output_dir", "conditions", "repeats", "pass_threshold",
               "dataset", "model", "agent", "evaluator", "environment"}
    if extra := config.keys() - allowed:
        raise ValueError(f"Unknown experiment settings: {sorted(extra)}")
    name = config["name"]
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", name):
        raise ValueError("Experiment name must use letters, digits, '.', '_' or '-'")
    config["output_dir"] = str((path.parent / config.get("output_dir", "../logs/evaluation")).resolve())
    config.setdefault("repeats", 1)
    if type(config["repeats"]) is not int or config["repeats"] < 1:
        raise ValueError("repeats must be a positive integer")
    conditions = config.setdefault("conditions", ["default"])
    if (not isinstance(conditions, list) or not conditions
            or any(not isinstance(item, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", item)
                   for item in conditions)
            or len(conditions) != len(set(conditions))):
        raise ValueError("conditions must contain distinct valid path components")
    threshold = config.setdefault("pass_threshold", 1.0)
    if isinstance(threshold, bool) or not isinstance(threshold, (float, int)) or not 0 <= threshold <= 1:
        raise ValueError("pass_threshold must be in [0, 1]")
    dataset = config["dataset"]
    if extra := dataset.keys() - {"index", "task_ids", "sample_size", "seed", "loader", "prepare", "setup"}:
        raise ValueError(f"Unknown dataset settings: {sorted(extra)}")
    dataset["index"] = str((path.parent / dataset["index"]).resolve(strict=True))
    if "task_ids" in dataset and (not isinstance(dataset["task_ids"], list)
                                or not all(isinstance(item, str) for item in dataset["task_ids"])):
        raise ValueError("dataset.task_ids must be a list of task ids")
    for key in ("loader", "prepare"):
        function_reference(dataset.get(key), f"dataset.{key}")
    if dataset.get("setup") is not None:
        function_reference(dataset["setup"], "dataset.setup")
    model = config["model"]
    if "api_key" in model:
        raise ValueError("Store the model API key in an environment variable; use api_key_env")
    if not isinstance(model.get("model"), str) or not model["model"].strip():
        raise ValueError("model.model must be non-empty")
    if not isinstance(model.get("api_key_env"), str) or not model["api_key_env"].strip():
        raise ValueError("model.api_key_env must name an environment variable")
    agent = config.setdefault("agent", {})
    function_reference(agent.get("runner"), "agent.runner")
    if "recorder" in agent:
        function_reference(agent["recorder"], "agent.recorder")
    if "tools" in agent:
        agent["tools"] = validate_tools(agent["tools"])
    evaluator = config.setdefault("evaluator", {})
    if extra := evaluator.keys() - {"function"}:
        raise ValueError(f"Unknown evaluator settings: {sorted(extra)}")
    function_reference(evaluator.get("function"), "evaluator.function")
    environment = config.setdefault("environment", {})
    function_reference(environment.get("factory"), "environment.factory")
    return config
