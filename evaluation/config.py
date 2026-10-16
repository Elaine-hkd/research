"""A TOML experiment file and ordinary importable function references."""

from importlib import import_module
import math
from pathlib import Path
import re
import tomllib


def positive(value, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be a positive finite number")
    return value


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
    conditions = config.setdefault("conditions", ["no-skill", "with-skill"])
    if (not isinstance(conditions, list) or not conditions
            or any(item not in ("no-skill", "with-skill") for item in conditions)
            or len(conditions) != len(set(conditions))):
        raise ValueError("conditions must contain distinct no-skill / with-skill values")
    threshold = config.setdefault("pass_threshold", 1.0)
    if isinstance(threshold, bool) or not isinstance(threshold, (float, int)) or not 0 <= threshold <= 1:
        raise ValueError("pass_threshold must be in [0, 1]")
    dataset = config["dataset"]
    if extra := dataset.keys() - {"index", "task_ids", "loader", "prepare"}:
        raise ValueError(f"Unknown dataset settings: {sorted(extra)}")
    dataset["index"] = str((path.parent / dataset["index"]).resolve(strict=True))
    if "task_ids" in dataset and (not isinstance(dataset["task_ids"], list)
                                or not all(isinstance(item, str) for item in dataset["task_ids"])):
        raise ValueError("dataset.task_ids must be a list of task ids")
    dataset.setdefault("loader", "evaluation.skillsbench:load_tasks")
    dataset.setdefault("prepare", "evaluation.skillsbench:prepare_task")
    model = config["model"]
    if "api_key" in model:
        raise ValueError("Store the model API key in an environment variable; use api_key_env")
    if not isinstance(model.get("model"), str) or not model["model"].strip():
        raise ValueError("model.model must be non-empty")
    model.setdefault("api_key_env", "DEEPSEEK_API_KEY")
    if not isinstance(model["api_key_env"], str) or not model["api_key_env"].strip():
        raise ValueError("model.api_key_env must name an environment variable")
    model.setdefault("num_retries", 0)
    model.setdefault("timeout", 120)
    positive(model["timeout"], "model.timeout")
    agent = config.setdefault("agent", {})
    if extra := agent.keys() - {"runner", "max_iterations", "timeout_sec", "tool_timeout_sec", "max_output_chars"}:
        raise ValueError(f"Unknown agent settings: {sorted(extra)}")
    agent.setdefault("runner", "evaluation.openhands:run_agent")
    agent.setdefault("max_iterations", 100)
    agent.setdefault("tool_timeout_sec", 120)
    agent.setdefault("max_output_chars", 20000)
    for key in ("max_iterations", "max_output_chars"):
        if type(agent[key]) is not int or agent[key] < 1:
            raise ValueError(f"agent.{key} must be a positive integer")
    positive(agent["tool_timeout_sec"], "agent.tool_timeout_sec")
    if "timeout_sec" in agent:
        positive(agent["timeout_sec"], "agent.timeout_sec")
    evaluator = config.setdefault("evaluator", {})
    if extra := evaluator.keys() - {"function"}:
        raise ValueError(f"Unknown evaluator settings: {sorted(extra)}")
    evaluator.setdefault("function", "evaluation.evaluators:skillsbench_verifier")
    environment = config.setdefault("environment", {})
    if extra := environment.keys() - {"startup_timeout", "ready_timeout", "cleanup_timeout", "force_build"}:
        raise ValueError(f"Unknown environment settings: {sorted(extra)}")
    for key in ("startup_timeout", "ready_timeout", "cleanup_timeout"):
        if key in environment:
            positive(environment[key], f"environment.{key}")
    environment.setdefault("startup_timeout", 1800)
    return config


def load_function(reference: str):
    module_name, separator, attribute = reference.partition(":")
    if not separator or not module_name or not attribute:
        raise ValueError(f"Expected module:function, received {reference!r}")
    function = getattr(import_module(module_name), attribute)
    if not callable(function):
        raise TypeError(f"Configured component is not callable: {reference}")
    return function
