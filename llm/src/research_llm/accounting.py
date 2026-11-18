"""直接调用与外部 SDK 共用的价格快照和逐次入账能力。"""

from dataclasses import asdict
from datetime import UTC, datetime
import getpass
import json
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from .ledger import finish_call, initialize_ledger, start_call, store_call, utc_now
from .pricing import (TokenRates, TokenUsage, estimate_cost, load_prices, parse_rates, price_snapshot,
                      rates_snapshot, select_rates)


def _token_count(*values) -> int | None:
    value = next((value for value in values if value is not None), None)
    return value if type(value) is int and 0 <= value < 2**63 else None


def read_response_usage(response) -> TokenUsage:
    """读取 LiteLLM 原生响应的 token 字段，不读取消息正文。"""
    usage = getattr(response, "usage", None)
    prompt_details = getattr(usage, "prompt_tokens_details", None)
    output_details = getattr(usage, "completion_tokens_details", None)
    cache_creation = getattr(prompt_details, "cache_creation_token_details", None)
    return TokenUsage(
        input_tokens=_token_count(getattr(usage, "prompt_tokens", None)),
        output_tokens=_token_count(getattr(usage, "completion_tokens", None)),
        total_tokens=_token_count(getattr(usage, "total_tokens", None)),
        cache_read_tokens=_token_count(
            getattr(prompt_details, "cached_tokens", None),
            getattr(usage, "prompt_cache_hit_tokens", None),
            getattr(usage, "cache_read_input_tokens", None),
        ),
        cache_write_tokens=_token_count(
            getattr(prompt_details, "cache_write_tokens", None),
            getattr(prompt_details, "cache_creation_tokens", None),
            getattr(usage, "cache_creation_input_tokens", None),
        ),
        reasoning_tokens=_token_count(getattr(output_details, "reasoning_tokens", None)),
        cache_write_1h_tokens=_token_count(getattr(cache_creation, "ephemeral_1h_input_tokens", None)),
    )


def usage_fields(value) -> TokenUsage:
    """接收标准字段，保留未知与零的区别；不推算服务商未报告的总数。"""
    value = value or {}
    return TokenUsage(**{name: value.get(name) for name in TokenUsage.__dataclass_fields__})


def usage_details(usage: TokenUsage, rates: TokenRates | None) -> dict:
    amount, status = estimate_cost(usage, rates)
    # 无效计数不能写入 SQLite INTEGER；状态仍明确保留不一致的事实。
    counts = {name: count if type(count) is int and 0 <= count < 2**63 else None
              for name, count in asdict(usage).items()}
    return {**counts, "estimated_cost": format(amount, "f") if amount is not None else None,
            "cost_status": status}


class UsageRecorder:
    """持有项目和价格配置；每次写账自行打开并关闭 SQLite 连接。"""

    def __init__(self, *, project_id: str, ledger_path: str | Path,
                 user_id: str | None = None, prices_path: str | Path | None = None):
        if not isinstance(project_id, str) or not project_id.strip():
            raise ValueError("project_id 必须是非空字符串")
        user_id = getpass.getuser() if user_id is None else user_id
        if not isinstance(user_id, str) or not user_id.strip():
            raise ValueError("user_id 必须是非空字符串")
        self.project_id = project_id
        self.user_id = user_id
        self.ledger_path = Path(ledger_path).resolve()
        self.prices = load_prices(prices_path)
        initialize_ledger(self.ledger_path)

    def prepare_call(self, *, model: str, api_key_env: str = "", base_url: str | None = None,
                     rates: TokenRates | None = None, source: str = "completion",
                     call_id: str | None = None, started_at: str | None = None,
                     time_source: str = "request_start", metadata: dict | None = None,
                     service_tier: str | None = None) -> dict:
        if not isinstance(model, str) or not model.strip():
            raise ValueError("model 必须是非空字符串")
        price_model, configured_rates = self.prices.get(model, (None, None))
        if rates is None:
            rates = configured_rates
        else:
            price_model = model
        started_at = started_at or utc_now()
        timestamp = datetime.fromisoformat(started_at)
        if timestamp.utcoffset() is None:
            raise ValueError("started_at 必须是包含时区的 ISO 时间")
        rule = None
        currency = rates.currency if rates is not None else None
        selected, period, multiplier = None, None, None
        if rates is not None:
            selected, period, multiplier = select_rates(rates, timestamp, service_tier=service_tier)
            if rates.has_pricing_rule:
                rule = price_snapshot(rates)
                if isinstance(service_tier, str) and service_tier in dict(rates.service_tiers):
                    rule["service_tier"] = service_tier
        return {
            "call_id": call_id or uuid4().hex, "project_id": self.project_id, "user_id": self.user_id,
            "started_at": timestamp.astimezone(UTC).isoformat(timespec="microseconds"), "requested_model": model,
            "api_key_env": api_key_env, "endpoint_host": urlsplit(base_url).hostname if base_url else None,
            "source": source, "pricing_model": price_model, "time_source": time_source,
            "metadata_json": json.dumps(metadata or {}, ensure_ascii=False, allow_nan=False),
            "currency": currency,
            "rates_per_million": json.dumps(rates_snapshot(selected)) if selected is not None else None,
            "pricing_period": period,
            "pricing_multiplier": str(multiplier) if multiplier is not None else None,
            "pricing_rule_json": json.dumps(rule) if rule is not None else None,
        }

    def begin_call(self, **kwargs) -> dict:
        return start_call(self.ledger_path, self.prepare_call(**kwargs))

    def finish_call(self, call: dict, *, usage: TokenUsage | None = None,
                    status: str = "completed", error_type: str | None = None,
                    response_model: str | None = None, response_id: str | None = None,
                    provider_request_id: str | None = None) -> dict:
        try:
            return finish_call(self.ledger_path, call["call_id"], status=status, details={
                **self._finish_details(call, usage or TokenUsage()),
                "error_type": error_type, "response_model": response_model,
                "response_id": response_id, "provider_request_id": provider_request_id,
            })
        except Exception as error:
            raise RuntimeError(
                f"调用 {call['call_id']} 的账本记录未完成，请先对账再重试"
            ) from error

    def record_usage(self, *, usage: TokenUsage, status: str, source_status: str,
                     finished_at: str | None = None, response_model: str | None = None,
                     response_id: str | None = None, error_type: str | None = None, **kwargs) -> dict:
        """保存外部已发生的模型尝试，不发送请求；call_id 用于幂等导入。"""
        kwargs.setdefault("time_source", "recorded")
        call = self.prepare_call(**kwargs)
        return store_call(self.ledger_path, {
            **call, **self._finish_details(call, usage), "status": status,
            "source_status": source_status, "finished_at": finished_at or utc_now(),
            "response_model": response_model, "response_id": response_id, "error_type": error_type,
        })

    @staticmethod
    def _finish_details(call: dict, usage: TokenUsage) -> dict:
        rule = json.loads(call.get("pricing_rule_json") or "null")
        snapshot = json.loads(call["rates_per_million"] or "null")
        pricing = {}
        if rule is not None and "tiers" in rule:
            rates, period, multiplier = select_rates(
                parse_rates(rule), datetime.fromisoformat(call["started_at"]),
                usage=usage, service_tier=rule.get("service_tier"),
            )
            pricing = {
                "rates_per_million": json.dumps(rates_snapshot(rates)) if rates is not None else None,
                "pricing_period": period,
                "pricing_multiplier": str(multiplier) if multiplier is not None else None,
            }
        else:
            rates = parse_rates(snapshot) if snapshot is not None else None
        return {**pricing, **usage_details(usage, rates)}
