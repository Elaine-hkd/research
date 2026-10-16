"""Sequential trials with independent environments and durable per-trial results."""

import asyncio
from dataclasses import asdict
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
import json
import math
import os
from pathlib import Path
import re
import time
import traceback
from uuid import uuid4

from environment import DockerEnvironment

from .artifacts import append_jsonl, redact, summarize, write_json
from .config import load_function
from .contracts import AgentResult, PreparedTask, Score, TrialContext


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _versions() -> dict:
    found = {}
    for package in ("benchflow", "openhands-sdk", "litellm"):
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


async def _inject_skills(context: TrialContext) -> None:
    if context.condition == "with-skill":
        if context.prepared.skills_dir is None:
            raise ValueError("with-skill task preparation did not supply skills_dir")
        exists = await context.environment.execute(["test", "!", "-e", "/skills"])
        if not exists.success:
            raise ValueError("Task image already contains /skills; refuse to merge skill sources")
        await context.environment.upload(context.prepared.skills_dir, "/skills")
    elif context.prepared.skills_dir is not None or context.prepared.skill_catalog:
        raise ValueError("no-skill task preparation exposed skills")


async def run_evaluation(config: dict, *, tasks=None, prepare_task=None,
                         run_agent=None, evaluate=None, create_environment=DockerEnvironment) -> Path:
    """Replace any component by passing a function, or configure module:function.

    Dataset: load(index, ids) -> list[Task]; prepare(task, destination, condition)
    -> PreparedTask. Agent: async (context, model, settings) -> AgentResult.
    Evaluator: async (context, agent_result) -> Score.
    """
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
    run_agent = run_agent or load_function(config["agent"]["runner"])
    evaluate = evaluate or load_function(config["evaluator"]["function"])
    
    key_name = config["model"]["api_key_env"]
    secret = os.environ.get(key_name)
    if not secret:
        raise ValueError(f"Set model credential environment variable {key_name} before running")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = Path(config["output_dir"]) / f"{config['name']}-{stamp}-{uuid4().hex[:8]}"
    output.mkdir(parents=True)
    
    conditions, repeats = config["conditions"], config["repeats"]
    total = len(tasks) * len(conditions) * repeats
    
    manifest = {
        "schema_version": 1, "started_at": _utc(), "config": config,
        "versions": _versions(), "planned_trials": total,
        "tasks": [asdict(task) for task in tasks],
        "components": {
            "prepare": f"{prepare_task.__module__}:{prepare_task.__qualname__}",
            "agent": f"{run_agent.__module__}:{run_agent.__qualname__}",
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
                        "model": config["model"]["model"], "started_at": _utc(),
                        "dataset_index": dataset["index"],
                        "agent_runner": manifest["components"]["agent"],
                        "evaluator": manifest["components"]["evaluator"],
                        "status": "error", "reward": None, "passed": None,
                        "metrics": {}, "agent": None, "errors": [],
                    }
                    stage, environment = "prepare", None
                    print(f"[{len(rows) + 1}/{total}] {trial_id}: prepare", flush=True)
                    try:
                        prepared = prepare_task(task, trial_dir / "task", condition)
                        if not isinstance(prepared, PreparedTask):
                            raise TypeError("Dataset preparer must return evaluation.PreparedTask")
                        write_json(trial_dir / "task-info.json", {
                            "task_id": task.id, "source": task.source,
                            "skill_catalog": prepared.skill_catalog,
                        })
                        stage = "environment"
                        environment = create_environment(
                            prepared.task_dir, output_dir=trial_dir,
                            workdir=None, cleanup_volumes=True, **config["environment"],
                        )
                        row["environment_id"] = environment.run_id
                        await environment.start()
                        context = TrialContext(task, prepared, environment, trial_dir, condition, repeat)
                        stage = "skills"
                        await _inject_skills(context)
                        stage = "agent"
                        print(f"  {trial_id}: agent", flush=True)
                        agent_result = await run_agent(context, config["model"], config["agent"])
                        if not isinstance(agent_result, AgentResult):
                            raise TypeError("Agent runner must return evaluation.AgentResult")
                        row["agent"] = asdict(agent_result)
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
                        if environment is not None:
                            try:
                                await environment.destroy()
                            except Exception as exc:
                                row["status"] = "error"
                                row["errors"].append({"stage": "cleanup", "type": type(exc).__name__, "message": redact(str(exc), secret)})
                        partial_agent = trial_dir / "agent" / "result.json"
                        if row["agent"] is None and partial_agent.is_file():
                            row["agent"] = json.loads(partial_agent.read_text(encoding="utf-8"))
                        row.update(finished_at=_utc(), duration_sec=time.monotonic() - started)
                        # Reward remains useful if only cleanup failed; status exposes it.
                        safe_row = redact(row, secret)
                        write_json(trial_dir / "result.json", safe_row)
                        append_jsonl(output / "results.jsonl", safe_row)
                        rows.append(safe_row)
                        write_json(output / "summary.json", summarize(rows, conditions, len(tasks), repeats))
                        print(f"  {trial_id}: {row['status']}, reward={row['reward']}", flush=True)
    finally:
        manifest["finished_at"] = _utc()
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
