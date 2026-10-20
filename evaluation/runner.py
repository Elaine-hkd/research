"""Sequential trials with independent environments and durable per-trial results."""

import asyncio
from dataclasses import asdict
from datetime import datetime
from importlib.metadata import PackageNotFoundError, version
import json
import math
import os
from pathlib import Path
import re
import time
import traceback
from uuid import uuid4

from harness.runner import run_agent as run_harness

from .artifacts import append_jsonl, redact, summarize, write_json
from .config import load_function
from .contracts import PreparedTask, Score, TrialContext


def _local_time() -> str:
    return datetime.now().astimezone().isoformat()


def _versions() -> dict:
    found = {}
    for package in ("benchflow", "openhands-sdk", "deepseek-harness-sdk", "litellm"):
        try:
            found[package] = version(package)
        except PackageNotFoundError:
            found[package] = None
    return found


def _score(score: Score) -> None:
    if not isinstance(score, Score):
        raise TypeError("Evaluator must return evaluation.Score")
    value = score.reward
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1:
        raise ValueError("Evaluator reward must be a finite number in [0, 1]")
    json.dumps(score.metrics, allow_nan=False)


async def run_evaluation(config: dict, *, tasks=None, prepare_task=None,
                         setup_environment=None, run_agent=None, evaluate=None,
                         create_environment=None) -> Path:
    """Replace any component by passing a function, or configure module:function.

    Dataset: load(index, ids) -> list[Task]; prepare(task, destination, condition)
    -> PreparedTask. Optional setup: async (context) after environment.start().
    Agent: common run_agent calls the configured execute(**native_options).
    No evaluation objects are passed to harnesses or their recorders.
    Evaluator: async (context, agent_result) -> Score. Environments are external factories.
    """
    # 加载数据集和任务
    dataset = config["dataset"]
    if tasks is None:
        tasks = load_function(dataset["loader"])(Path(dataset["index"]), dataset.get("task_ids"))
    tasks = list(tasks)
    if not tasks:
        raise ValueError("Dataset selected no tasks")
    ids = [task.id for task in tasks]
    if len(set(ids)) != len(ids) or any(not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", value) for value in ids):
        raise ValueError("Task ids must be unique, non-empty and valid path components")
    
    
    prepare_task = prepare_task or load_function(dataset["prepare"])
    if setup_environment is None and dataset.get("setup") is not None:
        setup_environment = load_function(dataset["setup"])
    run_agent = run_agent or load_function(config["agent"]["runner"])
    evaluate = evaluate or load_function(config["evaluator"]["function"])
    environment_options = dict(config["environment"])
    factory = environment_options.pop("factory", None)
    if create_environment is None:
        if factory is None:
            raise ValueError("Configure environment.factory or pass create_environment")
        create_environment = load_function(factory)
    agent_options = dict(config["agent"])
    agent_options.pop("runner", None)
    recorder = agent_options.pop("recorder", None)
    tools = agent_options.pop("tools", None)
    key_name = config["model"]["api_key_env"]
    secret = os.environ.get(key_name)
    if not secret:
        raise ValueError(f"Set model credential environment variable {key_name} before running")

    # 设置输出目录
    stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    output = Path(config["output_dir"]) / f"{config['name']}-{stamp}-{uuid4().hex[:8]}"
    output.mkdir(parents=True)
    
    conditions, repeats = config["conditions"], config["repeats"]
    total = len(tasks) * len(conditions) * repeats
    
    manifest = {
        "schema_version": 1, "started_at": _local_time(), "config": config,
        "versions": _versions(), "planned_trials": total,
        "tasks": [asdict(task) for task in tasks],
        "components": {
            "prepare": f"{prepare_task.__module__}:{prepare_task.__qualname__}",
            "setup": (f"{setup_environment.__module__}:{setup_environment.__qualname__}"
                      if setup_environment is not None else None),
            "agent": f"{run_agent.__module__}:{run_agent.__qualname__}",
            "recorder": recorder,
            "evaluator": f"{evaluate.__module__}:{evaluate.__qualname__}",
            "environment": f"{create_environment.__module__}:{create_environment.__qualname__}",
        },
    }
    write_json(output / "manifest.json", manifest)
    
    rows = []
    print(f"Experiment: {output}\nPlanned trials: {total}", flush=True)
    try:
        for task in tasks:
            for repeat in range(1, repeats + 1):
                for condition in conditions:
                    trial_id = f"{task.id}__{condition}__{repeat:03d}"
                    trial_dir = output / "trials" / trial_id
                    trial_dir.mkdir(parents=True)
                    started = time.monotonic()
                    row = {
                        "schema_version": 1, "trial_id": trial_id,
                        "task_id": task.id, "condition": condition, "repeat": repeat,
                        "model": config["model"]["model"], "started_at": _local_time(),
                        "dataset_index": dataset["index"],
                        "agent_runner": manifest["components"]["agent"],
                        "evaluator": manifest["components"]["evaluator"],
                        "status": "error", "reward": None, "passed": None,
                        "metrics": {}, "agent": None, "errors": [],
                    }
                    stage, environment = "prepare", None
                    print(f"[{len(rows) + 1}/{total}] {trial_id}: prepare", flush=True)
                    try:
                        # 准备任务
                        prepared = prepare_task(task, trial_dir / "task", condition)
                        if not isinstance(prepared, PreparedTask):
                            raise TypeError("Dataset preparer must return evaluation.PreparedTask")
                        write_json(trial_dir / "task-info.json", {
                            "task_id": task.id, "source": task.source,
                            "skill_catalog": prepared.skill_catalog,
                        })

                        # 初始化环境
                        stage = "environment"
                        environment = create_environment(
                            prepared.task_dir, output_dir=trial_dir,
                            **environment_options,
                        )
                        row["environment_id"] = getattr(environment, "run_id", None)
                        await environment.start()
                        context = TrialContext(task, prepared, environment, trial_dir, condition, repeat)
                        
                        # 设置环境
                        stage = "setup"
                        if setup_environment is not None:
                            await setup_environment(context)

                        # 智能体运行
                        stage = "agent"
                        print(f"  {trial_id}: agent", flush=True)
                        agent_result = await run_harness(
                            runner=run_agent,
                            recorder=recorder,
                            instruction=task.instruction,
                            model=config["model"],
                            tools=tools,
                            settings=dict(agent_options),
                            environment=environment,
                            output_dir=trial_dir / "agent",
                            timeout_sec=task.agent_timeout_sec,
                            skill_catalog=prepared.skill_catalog,
                            metadata={
                                "experiment_id": output.name, "experiment_name": output.name,
                                "trial_id": trial_id, "task_id": task.id,
                                "condition": condition, "repeat": repeat,
                            },
                        )
                        row["agent"] = asdict(agent_result)

                        #评估任务结果
                        stage = "evaluate"
                        print(f"  {trial_id}: evaluate", flush=True)
                        score = await evaluate(context, agent_result)
                        _score(score)
                        row.update(reward=float(score.reward), metrics=score.metrics,
                                   passed=score.reward >= config["pass_threshold"], status="completed")
                    except BaseException as exc:
                        row["status"] = "cancelled" if isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt)) else "error"
                        row["errors"].append({"stage": stage, "type": type(exc).__name__, "message": redact(str(exc), secret)})
                        (trial_dir / "error.txt").write_text(redact(traceback.format_exc(), secret), encoding="utf-8")
                        if not isinstance(exc, Exception):
                            raise
                    finally:
                        # 清理环境
                        if environment is not None:
                            try:
                                await environment.destroy()
                            except Exception as exc:
                                row["status"] = "error"
                                row["errors"].append({"stage": "cleanup", "type": type(exc).__name__, "message": redact(str(exc), secret)})
                        partial_agent = trial_dir / "agent" / "result.json"
                        if row["agent"] is None and partial_agent.is_file():
                            row["agent"] = json.loads(partial_agent.read_text(encoding="utf-8"))
                        trace_path = (row["agent"] or {}).get("trace_path")
                        if trace_path:
                            path = Path(trace_path)
                            if not path.is_absolute():
                                path = trial_dir / path
                            path = path.resolve()
                            base = trial_dir.resolve()
                            row["trace_path"] = path.relative_to(base).as_posix() if path.is_relative_to(base) else str(path)
                        row.update(finished_at=_local_time(), duration_sec=time.monotonic() - started)
                        # Reward remains useful if only cleanup failed; status exposes it.
                        safe_row = redact(row, secret)
                        write_json(trial_dir / "result.json", safe_row)
                        append_jsonl(output / "results.jsonl", safe_row)
                        rows.append(safe_row)
                        write_json(output / "summary.json", summarize(rows, conditions, len(tasks), repeats))
                        print(f"  {trial_id}: {row['status']}, reward={row['reward']}", flush=True)
    finally:
        # 保存结果
        manifest["finished_at"] = _local_time()
        manifest["recorded_trials"] = len(rows)
        interrupted = len(rows) < total or any(
            error["type"] in {"CancelledError", "KeyboardInterrupt"}
            for row in rows for error in row["errors"]
        )
        manifest["status"] = "interrupted" if interrupted else ("error" if any(row["errors"] for row in rows) else "completed")
        write_json(output / "manifest.json", manifest)
        write_json(output / "summary.json", summarize(rows, conditions, len(tasks), repeats))
        print(f"Results saved: {output}", flush=True)
    return output
