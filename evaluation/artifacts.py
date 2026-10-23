"""增量写入 JSON 产物，并明确统计汇总的分母。"""

from collections import Counter
import json
from pathlib import Path
from statistics import mean

from utils.json_io import redact, write_json


def write_results(path: Path, rows: list[dict]) -> None:
    """原子替换当前试验结果索引，重跑不会追加同一试验的重复记录。"""
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
    temporary.replace(path)


def _summarize_stage_durations(rows: list[dict]) -> dict:
    durations_by_stage = {}
    for row in rows:
        for stage, duration in row.get("stage_durations_sec", {}).items():
            durations = durations_by_stage.setdefault(stage, [])
            if duration is not None:
                durations.append(duration)
    return {
        stage: {
            "count": len(durations),
            "total": sum(durations) if durations else None,
            "mean": mean(durations) if durations else None,
            "max": max(durations) if durations else None,
        }
        for stage, durations in durations_by_stage.items()
    }


def summarize(rows: list[dict], conditions: list[str], tasks: int, repeats: int) -> dict:
    groups = {}
    expected = tasks * repeats
    for condition in conditions:
        selected = [row for row in rows if row["condition"] == condition]
        scored = [row for row in selected if row["reward"] is not None]
        passed = sum(row["passed"] is True for row in scored)
        groups[condition] = {
            "planned": expected,
            "recorded": len(selected),
            "scored": len(scored),
            "missing": expected - len(selected),
            "statuses": dict(Counter(row["status"] for row in selected)),
            "coverage": len(scored) / expected,
            "passed": passed,
            "solve_rate_all": passed / expected,
            "solve_rate_scored": passed / len(scored) if scored else None,
            "mean_reward_scored": mean(row["reward"] for row in scored) if scored else None,
            "stage_durations_sec": _summarize_stage_durations(selected),
        }
    paired = {}
    for row in rows:
        if row["reward"] is not None:
            paired.setdefault((row["task_id"], row["repeat"]), {})[row["condition"]] = row["reward"]
    deltas = [pair["with-skill"] - pair["no-skill"] for pair in paired.values()
              if "with-skill" in pair and "no-skill" in pair]
    return {
        "schema_version": 1,
        "stage_durations_sec": _summarize_stage_durations(rows),
        "groups": groups,
        "paired_skill_effect": {
            "scored_pairs": len(deltas),
            "mean_reward_delta": mean(deltas) if deltas else None,
        },
    }
