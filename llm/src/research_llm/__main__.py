import argparse
from collections import Counter, defaultdict
import csv
from datetime import UTC, datetime
import json
from pathlib import Path
import sqlite3

from .ledger import read_calls
from .traces import summarize_calls


def _utc_boundary(value: str) -> str:
    try:
        timestamp = datetime.fromisoformat(value)
        if timestamp.utcoffset() is not None:
            return timestamp.astimezone(UTC).isoformat(timespec="microseconds")
    except (ValueError, OverflowError):
        pass
    raise argparse.ArgumentTypeError("请使用带时区的 ISO 8601 时间，例如 2026-11-01T00:00:00+08:00")


def _csv_cell(value):
    if isinstance(value, str) and (value.startswith(("\t", "\r", "\n"))
                                  or value.lstrip().startswith(("=", "+", "-", "@"))):
        return "'" + value
    return value


def main():
    parser = argparse.ArgumentParser(description="汇总一个项目的模型调用和费用估算")
    parser.add_argument("ledger", type=Path, help="SQLite 账本路径")
    parser.add_argument("project_id", help="项目编号")
    parser.add_argument("--from", dest="since", type=_utc_boundary, help="开始时间，含边界")
    parser.add_argument("--before", type=_utc_boundary, help="截止时间，不含边界")
    parser.add_argument("--csv", type=Path, help="明细 CSV 的新文件路径")
    parser.add_argument("--user", help="仅查看指定 user_id 的记录")
    args = parser.parse_args()
    if args.since and args.before and args.since >= args.before:
        parser.error("--from 必须早于 --before")

    try:
        columns, rows = read_calls(args.ledger, args.project_id, args.since, args.before)
        if args.user is not None:
            rows = [row for row in rows if row.get("user_id") == args.user]
        if args.csv is not None:
            with args.csv.open("x", encoding="utf-8-sig", newline="") as stream:
                writer = csv.writer(stream)
                writer.writerow(columns)
                writer.writerows([_csv_cell(row[column]) for column in columns] for row in rows)
    except (sqlite3.Error, OSError) as error:
        parser.exit(1, f"生成报告失败：{error}\n")

    by_user = defaultdict(list)
    for row in rows:
        by_user[row.get("user_id") or "unknown"].append(row)
    print(json.dumps({
        "project_id": args.project_id,
        **summarize_calls(rows),
        "by_status": Counter(row["status"] for row in rows),
        "by_cost_status": Counter(row["cost_status"] for row in rows),
        "by_user": {user: summarize_calls(calls) for user, calls in by_user.items()},
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
