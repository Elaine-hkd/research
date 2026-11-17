"""将 RPA 发邮件任务接入现有实验流程，按工具执行证据评分。"""

import argparse
import asyncio
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import tomllib

from datasets.package import read_package_index, resource_path
from evaluation.contracts import AgentResult, PreparedTask, Score, Task, TrialContext
from utils.json_io import write_json


EMAIL_SKILL = "rpa-email"
EMAIL_RESOURCE = "scripts/rpa_email.py"


def load_tasks(index: Path, task_ids: list[str] | None = None) -> list[Task]:
    """从邮件资源包读取一个发信任务，保留原有指令和评分参数。"""
    package = read_package_index(index, task_format="research-email-demo-v1")
    if len(package.index["tasks"]) != 1:
        raise ValueError("邮件示例资源包必须包含一个任务")
    row = package.index["tasks"][0]
    task_file = resource_path(package.root, row["task_file"])
    resource_path(package.root, row["skills_dir"], must_exist=False)
    raw = task_file.read_bytes()
    task = tomllib.loads(raw.decode("utf-8-sig"))["task"]
    task_id = task["id"]
    if not isinstance(task_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", task_id):
        raise ValueError("邮件任务 id 必须为可用于路径的非空标识")
    if task_id != row["id"]:
        raise ValueError("邮件任务文件与索引中的 ID 不一致")
    if task_ids is not None and task_ids != [task_id]:
        raise ValueError(f"邮件示例仅包含任务 {task_id!r}")
    for key in ("receiver", "subject", "instruction"):
        if not isinstance(task.get(key), str) or not task[key].strip():
            raise ValueError(f"邮件任务 {key} 必须为非空字符串")
    timeout = task.get("timeout_sec", 900)
    if (isinstance(timeout, bool) or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout) or timeout <= 0):
        raise ValueError("邮件任务 timeout_sec 必须为有限正数")
    instruction = task["instruction"].strip() + "\n\n邮件参数：\n" + json.dumps(
        {key: task[key] for key in ("receiver", "subject")}, ensure_ascii=False, indent=2,
    )
    return [Task(
        id=task_id, instruction=instruction, agent_timeout_sec=timeout,
        metadata={key: task[key] for key in ("receiver", "subject")},
        source={"index": str(package.index_path), "row": row,
                "dataset_sha256": package.dataset_sha256,
                "index_sha256": package.index_sha256,
                "task_sha256": hashlib.sha256(raw).hexdigest()},
    )]


def prepare_task(task: Task, destination: Path, condition: str) -> PreparedTask:
    """保存任务和技能副本；执行仍通过当前 trial 的注册中心客户端。"""
    if condition != "with-skill":
        raise ValueError("邮件示例只支持 with-skill 条件")
    package = read_package_index(task.source["index"], task_format="research-email-demo-v1")
    if (package.index_sha256 != task.source["index_sha256"]
            or package.dataset_sha256 != task.source["dataset_sha256"]):
        raise ValueError("实验计划生成后，邮件数据集描述或索引发生了变化")
    row = task.source["row"]
    raw = resource_path(package.root, row["task_file"]).read_bytes()
    if hashlib.sha256(raw).hexdigest() != task.source["task_sha256"]:
        raise ValueError("实验计划生成后，邮件任务文件发生了变化")
    source = resource_path(package.root, row["skills_dir"])
    if not source.is_dir():
        raise ValueError(f"邮件技能资源必须为目录：{source}")
    for path in source.rglob("*"):
        if path.is_symlink() or path.is_junction():
            raise ValueError(f"不支持链接形式的技能资源：{path}")
    for relative in ("SKILL.md", EMAIL_RESOURCE):
        if not resource_path(source, relative).is_file():
            raise ValueError(f"邮件技能缺少文件：{relative}")
    destination.mkdir(parents=True)
    (destination / "task.toml").write_bytes(raw)
    skills_dir = destination.parent / "skills"
    shutil.copytree(source, skills_dir / "rpa_email")
    return PreparedTask(task_dir=destination, skills_dir=skills_dir)


def create_environment(task_dir: Path, *, output_dir: Path, **options):
    """在本机执行下载的技能脚本，环境生命周期由实验调度器管理。"""
    from environment.local import LocalEnvironment

    return LocalEnvironment(workdir=task_dir, **options)


async def setup_environment(context: TrialContext) -> None:
    registry = context.environment.executor.registry
    if registry is None or registry.allowed != frozenset({EMAIL_SKILL}):
        raise ValueError("邮件实验需配置 registry_url，且 allowed_skills 仅包含 rpa-email")
    # 提前发现缺少技能的配置错误；这里只读取技能，不下发邮件。
    await asyncio.to_thread(registry.load_skill, EMAIL_SKILL)


async def evaluate(context: TrialContext, agent: AgentResult) -> Score:
    """核对实际调用参数和 RPA 成功结果，不以模型的最终回答作为发送证据。"""
    path = context.output_dir / "agent" / "tool-calls.jsonl"
    if not path.exists() and agent.usage.get("tool_calls_by_name", {}).get("execute_resource", 0):
        raise FileNotFoundError(f"已记录资源调用，但缺少工具执行日志：{path}")
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()] if path.exists() else []
    # 解析约定的发信参数；服务控制参数仍由原脚本解释。
    parser = argparse.ArgumentParser(add_help=False, allow_abbrev=False, exit_on_error=False)
    for name in ("receiver", "subject", "body", "cc", "attachments", "task-id"):
        parser.add_argument("--" + name)
    parser.add_argument("--dry-run", action="store_true")
    calls, options, results = {}, {}, {}
    for row in rows:
        if row.get("tool_name") != "execute_resource":
            continue
        if row["type"] == "call":
            arguments = row["arguments"]
            if (arguments.get("skill_name") != EMAIL_SKILL
                    or arguments.get("relative_path") != EMAIL_RESOURCE):
                continue
            calls[row["index"]] = arguments
            try:
                parsed, _ = parser.parse_known_args(arguments.get("arguments", []))
            except argparse.ArgumentError:
                parsed = None
            options[row["index"]] = parsed
        elif row["type"] == "result":
            results[row["index"]] = row

    dispatches = 0
    matching_task_ids, successful_task_ids = set(), set()
    evidence = []
    for call_index in calls:
        parsed = options[call_index]
        result = results.get(call_index, {})
        if parsed is None or parsed.dry_run:
            continue
        if not parsed.task_id:
            dispatches += 1
        try:
            payload = json.loads(result.get("stdout", ""))
        except json.JSONDecodeError:
            payload = None
        if not isinstance(payload, dict):
            evidence.append({"call_index": call_index, "status": "NO_STRUCTURED_RESULT"})
            continue
        data = payload.get("data")
        data = data if isinstance(data, dict) else {}
        task_id = data.get("task_id")
        if not isinstance(task_id, str) or not task_id:
            task_id = None
        if (not parsed.task_id and task_id
                and parsed.receiver == context.task.metadata["receiver"]
                and parsed.subject == context.task.metadata["subject"]
                and parsed.body and parsed.body.strip()
                and not parsed.cc and not parsed.attachments):
            matching_task_ids.add(task_id)
        # 续查必须关联本次下发的任务，避免把其他邮件的成功结果算进来。
        query_matches = parsed.task_id is None or parsed.task_id == task_id
        if (task_id in matching_task_ids and query_matches
                and result.get("exit_code") == 0 and result.get("timed_out") is False
                and payload.get("success") is True and payload.get("status") == "SUCCESS"
                and data.get("execution_status") == "SUCCESS"):
            successful_task_ids.add(task_id)
        evidence.append({
            "call_index": call_index, "task_id": task_id,
            "status": payload.get("status"), "success": payload.get("success"),
            "exit_code": result.get("exit_code"), "timed_out": result.get("timed_out"),
        })
    passed = dispatches == 1 and len(successful_task_ids) == 1
    metrics = {
        "reward_basis": "rpa_service_success", "dispatch_calls": dispatches,
        "successful_task_ids": sorted(successful_task_ids),
        "recipient_delivery_verified": False,
    }
    write_json(context.output_dir / "verifier" / "result.json", {
        "reward": float(passed), "metrics": metrics, "evidence": evidence,
    })
    return Score(reward=float(passed), metrics=metrics)
