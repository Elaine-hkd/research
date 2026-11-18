from contextlib import closing, contextmanager
from datetime import UTC, datetime
from pathlib import Path
import sqlite3


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


_AUDIT_COLUMNS = {
    "user_id": "TEXT", "source": "TEXT", "source_status": "TEXT",
    "pricing_model": "TEXT", "metadata_json": "TEXT", "time_source": "TEXT",
    "pricing_period": "TEXT", "pricing_multiplier": "TEXT", "pricing_rule_json": "TEXT",
    "cache_write_1h_tokens": "INTEGER",
}


@contextmanager
def _writer(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as connection, connection:
        # 先取得写锁，使旧账本的字段迁移与并发导入互不交错。
        connection.execute("BEGIN IMMEDIATE")
        connection.row_factory = sqlite3.Row
        connection.execute("""
            CREATE TABLE IF NOT EXISTS model_calls (
                call_id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                requested_model TEXT NOT NULL,
                response_model TEXT,
                endpoint_host TEXT,
                api_key_env TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                error_type TEXT,
                response_id TEXT,
                provider_request_id TEXT,
                input_tokens INTEGER,
                output_tokens INTEGER,
                total_tokens INTEGER,
                cache_read_tokens INTEGER,
                cache_write_tokens INTEGER,
                reasoning_tokens INTEGER,
                currency TEXT,
                rates_per_million TEXT,
                estimated_cost TEXT,
                cost_status TEXT NOT NULL DEFAULT 'unavailable'
            )
        """)
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(model_calls)")}
        for name, kind in _AUDIT_COLUMNS.items():
            if name not in columns:
                connection.execute(f"ALTER TABLE model_calls ADD COLUMN {name} {kind}")
        yield connection


def initialize_ledger(path: Path) -> None:
    """在模型运行前确认账本可写，并准备或迁移表结构。"""
    with _writer(path):
        pass


def store_call(path: Path, call: dict) -> dict:
    """原子地保存一条记录；重复导入返回原价格快照，冲突用量明确报错。"""
    allowed = {
        "call_id", "project_id", "started_at", "finished_at", "requested_model", "response_model",
        "endpoint_host", "api_key_env", "status", "error_type", "response_id", "provider_request_id",
        "input_tokens", "output_tokens", "total_tokens", "cache_read_tokens", "cache_write_tokens",
        "reasoning_tokens", "currency", "rates_per_million", "estimated_cost", "cost_status",
        *_AUDIT_COLUMNS,
    }
    if extra := call.keys() - allowed:
        raise ValueError(f"未知账本字段：{sorted(extra)}")
    row = dict(call)
    row.setdefault("api_key_env", "")
    columns = ", ".join(row)
    parameters = ", ".join(f":{name}" for name in row)
    with _writer(path) as connection:
        connection.execute(
            f"INSERT INTO model_calls ({columns}) VALUES ({parameters}) ON CONFLICT(call_id) DO NOTHING", row,
        )
        saved = dict(connection.execute("SELECT * FROM model_calls WHERE call_id = ?", (row["call_id"],)).fetchone())
        for name in ("project_id", "user_id", "source", "requested_model", "status", "source_status",
                     "input_tokens", "output_tokens", "total_tokens", "cache_read_tokens",
                     "cache_write_tokens", "cache_write_1h_tokens", "reasoning_tokens"):
            if name in row and saved[name] != row[name]:
                raise ValueError(f"调用 {row['call_id']} 重复导入时 {name} 不一致")
        return saved


def start_call(path: Path, call: dict) -> dict:
    return store_call(path, dict(call, status="pending"))


def finish_call(path: Path, call_id: str, *, status: str, details: dict) -> dict:
    row = dict.fromkeys((
        "error_type", "response_id", "provider_request_id", "response_model", "input_tokens",
        "output_tokens", "total_tokens", "cache_read_tokens", "cache_write_tokens",
        "reasoning_tokens", "estimated_cost", "cache_write_1h_tokens",
        "rates_per_million", "pricing_period", "pricing_multiplier",
    ))
    row.update(cost_status="unavailable")
    row.update(details)
    with _writer(path) as connection:
        updated = connection.execute("""
            UPDATE model_calls SET status = :status, finished_at = :finished_at,
                error_type = :error_type, response_model = :response_model,
                response_id = :response_id, provider_request_id = :provider_request_id,
                input_tokens = :input_tokens, output_tokens = :output_tokens,
                total_tokens = :total_tokens, cache_read_tokens = :cache_read_tokens,
                cache_write_tokens = :cache_write_tokens, reasoning_tokens = :reasoning_tokens,
                cache_write_1h_tokens = :cache_write_1h_tokens,
                rates_per_million = COALESCE(:rates_per_million, rates_per_million),
                pricing_period = COALESCE(:pricing_period, pricing_period),
                pricing_multiplier = COALESCE(:pricing_multiplier, pricing_multiplier),
                estimated_cost = :estimated_cost, cost_status = :cost_status
            WHERE call_id = :call_id AND status = 'pending'
        """, dict(row, call_id=call_id, status=status, finished_at=utc_now()))
        if updated.rowcount != 1:
            raise RuntimeError(f"找不到待结束的调用记录 {call_id}")
        return dict(connection.execute("SELECT * FROM model_calls WHERE call_id = ?", (call_id,)).fetchone())


def read_calls(path: Path, project_id: str, since: str | None, before: str | None):
    uri = path.resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        cursor = connection.execute("""
            SELECT * FROM model_calls WHERE project_id = :project_id
                AND (:since IS NULL OR started_at >= :since)
                AND (:before IS NULL OR started_at < :before)
            ORDER BY started_at, call_id
        """, dict(project_id=project_id, since=since, before=before))
        return [column[0] for column in cursor.description], [dict(row) for row in cursor]
