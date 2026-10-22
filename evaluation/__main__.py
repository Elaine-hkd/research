"""运行方式：python -m evaluation --config config/skillsbench-openhands.toml [--plan]。"""

import argparse
import asyncio
import json
from pathlib import Path

from .config import experiment_directory, load_config
from .dataset import load_tasks


def main() -> int:
    parser = argparse.ArgumentParser(description="在隔离的任务环境中评估智能体")
    parser.add_argument("--config", type=Path, default=Path("config/skillsbench-openhands.toml"))
    parser.add_argument("--name", help="实验名称，覆盖 config.name，支持中文")
    parser.add_argument("--plan", action="store_true", help="仅读取配置和任务，不调用模型或启动 Docker")
    parser.add_argument("--task", nargs="+", help="覆盖 dataset.task_ids")
    args = parser.parse_args()
    config = load_config(args.config)
    if args.name is not None:
        config["name"] = args.name
    if args.task is not None:
        config["dataset"]["task_ids"] = args.task
    output = experiment_directory(config)
    dataset = config["dataset"]
    
    tasks = load_tasks(dataset)
    if args.plan:
        print(json.dumps({
            "name": config["name"], "model": config["model"]["model"],
            "tasks": [task.id for task in tasks], "conditions": config["conditions"],
            "repeats": config["repeats"],
            "sample_size": dataset.get("sample_size"), "seed": dataset.get("seed"),
            "planned_trials": len(tasks) * len(config["conditions"]) * config["repeats"],
            "output_dir": config["output_dir"],
            "experiment_dir": str(output),
            "agent_runner": config["agent"]["runner"], "evaluator": config["evaluator"]["function"],
            "agent_recorder": config["agent"].get("recorder"),
            "tools": config["agent"].get("tools"),
            "dataset_loader": dataset["loader"], "dataset_prepare": dataset["prepare"],
            "dataset_setup": dataset.get("setup"),
            "environment_factory": config["environment"]["factory"],
        }, ensure_ascii=False, indent=2))
        return 0
    # 遵循现有 harness 约定，环境变量中的值优先。
    from dotenv import load_dotenv
    from .runner import run_evaluation

    load_dotenv(Path.cwd() / ".env", override=False)
    output = asyncio.run(run_evaluation(config, tasks=tasks))
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    return 0 if manifest["status"] == "completed" else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
