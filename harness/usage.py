"""公共 harness 的用量接入；原生 SDK 和 recorder 不依赖账本。"""

import json
import os
from pathlib import Path

from dotenv import load_dotenv

from research_llm import UsageRecorder, import_traces
from research_llm.pricing import price_snapshot
from utils.json_io import write_json


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LEDGER_PATH = PROJECT_ROOT / "logs" / "model-usage.sqlite3"


def prepare_usage(*, model: dict, output_dir: Path) -> UsageRecorder:
    load_dotenv(PROJECT_ROOT / ".env", override=False)
    identity = {}
    for field, variable in (("project_id", "RESEARCH_PROJECT_ID"), ("user_id", "RESEARCH_USER_ID")):
        value = os.environ.get(variable, "").strip()
        if not value:
            raise ValueError(f"启用 cost_tracking 需要设置环境变量 {variable}，也可以填写在 {PROJECT_ROOT / '.env'}")
        identity[field] = value
    accounting = UsageRecorder(**identity, ledger_path=DEFAULT_LEDGER_PATH)
    snapshot = accounting.prepare_call(model=model["model"], time_source="run_preparation")
    saved = {
        key: snapshot[key] for key in (
            "project_id", "user_id", "requested_model", "pricing_model", "currency", "rates_per_million",
            "started_at", "time_source", "pricing_period", "pricing_multiplier",
        )
    }
    saved["rates_per_million"] = json.loads(snapshot["rates_per_million"]) if snapshot["rates_per_million"] else None
    saved["pricing_rule"] = json.loads(snapshot["pricing_rule_json"] or "null")
    saved["ledger_path"] = str(accounting.ledger_path)
    saved["prices"] = {
        name: {"pricing_model": canonical, **price_snapshot(rates)}
        for name, (canonical, rates) in accounting.prices.items()
    }
    write_json(output_dir / "usage-config.json", saved)
    return accounting


def save_usage(accounting: UsageRecorder, recorder, *, model: dict, metadata: dict | None,
               output_dir: Path, result, start_offsets: dict) -> None:
    paths = list(getattr(recorder, "paths", {}).values())
    if getattr(recorder, "last_path", None) is not None:
        paths.append(recorder.last_path)
    summary = import_traces(
        accounting, paths, model=model["model"], api_key_env=model.get("api_key_env", ""),
        base_url=model.get("base_url"), metadata=metadata, log_path=output_dir / "usage.jsonl",
        start_offsets=start_offsets,
    )
    if result is not None:
        result.usage["accounting"] = summary
    path = output_dir / "result.json"
    if path.exists():
        # 保留原生收尾已经保存的异常和其他字段，仅补本次审核汇总。
        saved = json.loads(path.read_text(encoding="utf-8"))
        saved.setdefault("usage", {})["accounting"] = summary
        write_json(path, saved)
