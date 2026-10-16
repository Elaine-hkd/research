"""Incremental JSON artifacts and explicit aggregation denominators."""

from collections import Counter
from statistics import mean

from utils.json_io import append_jsonl, redact, write_json


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
        }
    paired = {}
    for row in rows:
        if row["reward"] is not None:
            paired.setdefault((row["task_id"], row["repeat"]), {})[row["condition"]] = row["reward"]
    deltas = [pair["with-skill"] - pair["no-skill"] for pair in paired.values()
              if "with-skill" in pair and "no-skill" in pair]
    return {
        "schema_version": 1,
        "groups": groups,
        "paired_skill_effect": {
            "scored_pairs": len(deltas),
            "mean_reward_delta": mean(deltas) if deltas else None,
        },
    }
