from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
import tomllib


_BEIJING = timezone(timedelta(hours=8))
_RATE_FIELDS = ("input", "output", "cache_read", "cache_write")


def _minute_of_day(value: str) -> int:
    if (not isinstance(value, str) or len(value) != 5 or value[2] != ":"
            or not (value[:2] + value[3:]).isascii()
            or not (value[:2] + value[3:]).isdigit()):
        raise ValueError("时段必须使用 HH:MM 格式")
    hour, minute = int(value[:2]), int(value[3:])
    if hour > 23 or minute > 59:
        raise ValueError("时段必须在 00:00 至 23:59 之间")
    return hour * 60 + minute


@dataclass(frozen=True)
class DailyDiscount:
    """每天按北京时间判断峰段，其他时间对全部 token 单价应用系数。"""

    peak_hours: tuple[tuple[str, str], ...]
    multiplier: Decimal

    def __post_init__(self):
        if (not isinstance(self.multiplier, Decimal) or not self.multiplier.is_finite()
                or not 0 <= self.multiplier <= 1):
            raise ValueError("off_peak.multiplier 必须是 0 至 1 之间的有限 Decimal")
        if not self.peak_hours:
            raise ValueError("off_peak.peak_hours 至少需要一个峰段")
        intervals = []
        for interval in self.peak_hours:
            if len(interval) != 2:
                raise ValueError("每个峰段需要开始和结束两个时间")
            start, end = map(_minute_of_day, interval)
            if start >= end:
                raise ValueError("峰段必须在同一天，且开始时间早于结束时间")
            intervals.append((start, end))
        intervals.sort()
        if any(left[1] > right[0] for left, right in zip(intervals, intervals[1:])):
            raise ValueError("峰段不能重叠")


@dataclass(frozen=True)
class TokenRates:
    """每百万 token 单价；配置分时规则时，这些值作为峰价。"""

    currency: str
    input: Decimal
    output: Decimal
    cache_read: Decimal | None = None
    cache_write: Decimal | None = None
    off_peak: DailyDiscount | None = None

    def __post_init__(self):
        if (len(self.currency) != 3 or not self.currency.isascii()
                or not self.currency.isalpha() or not self.currency.isupper()):
            raise ValueError("currency 必须是三位大写币种代码")
        for name in ("input", "output", "cache_read", "cache_write"):
            rate = getattr(self, name)
            if rate is None and name in ("cache_read", "cache_write"):
                continue
            if not isinstance(rate, Decimal) or not rate.is_finite() or rate < 0:
                raise ValueError(f"{name} 单价必须是有限的非负 Decimal")
        if self.off_peak is not None and not isinstance(self.off_peak, DailyDiscount):
            raise ValueError("off_peak 必须是 DailyDiscount")


def rates_snapshot(rates: TokenRates) -> dict:
    """只保存实际单价，供本次费用计算和旧快照读取。"""
    return {"currency": rates.currency,
            **{name: str(getattr(rates, name)) if getattr(rates, name) is not None else None
               for name in _RATE_FIELDS}}


def price_snapshot(rates: TokenRates) -> dict:
    """保存基础单价和完整规则，便于从日志复核选价过程。"""
    values = rates_snapshot(rates)
    if rates.off_peak is not None:
        values["off_peak"] = {"timezone": "+08:00", "peak_hours": rates.off_peak.peak_hours,
                              "multiplier": str(rates.off_peak.multiplier)}
    return values


def select_rates(rates: TokenRates, started_at: datetime) -> tuple[TokenRates, str, Decimal]:
    """按调用时间选择单价，返回实际单价、时段和系数；不计算 token 费用。"""
    if started_at.utcoffset() is None:
        raise ValueError("计价时间必须包含时区")
    if rates.off_peak is None:
        return rates, "fixed", Decimal(1)
    local = started_at.astimezone(_BEIJING)
    minute = local.hour * 60 + local.minute
    peak = any(_minute_of_day(start) <= minute < _minute_of_day(end)
               for start, end in rates.off_peak.peak_hours)
    multiplier = Decimal(1) if peak else rates.off_peak.multiplier
    effective = TokenRates(currency=rates.currency, **{
        name: getattr(rates, name) * multiplier if getattr(rates, name) is not None else None
        for name in _RATE_FIELDS
    })
    return effective, "peak" if peak else "off_peak", multiplier


@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    reasoning_tokens: int | None = None


def load_prices(path: str | Path | None = None) -> dict[str, tuple[str, TokenRates]]:
    """读取明确配置的模型名称和别名，不改写发往服务商的模型名称。"""
    path = Path(path) if path is not None else Path(__file__).with_name("prices.toml")
    with path.open("rb") as stream:
        config = tomllib.load(stream)
    if set(config) != {"models"} or not isinstance(config["models"], dict):
        raise ValueError("价格文件需要且仅支持 [models.<模型名>] 配置表")
    prices = {}
    for model, values in config["models"].items():
        if not model.strip() or not isinstance(values, dict):
            raise ValueError("价格模型名称不能为空，价格项必须是配置表")
        if extra := values.keys() - {"currency", "input", "output", "cache_read", "cache_write", "aliases", "off_peak"}:
            raise ValueError(f"{model} 包含未知价格字段：{sorted(extra)}")
        aliases = values.get("aliases", [])
        if not isinstance(aliases, list) or any(not isinstance(v, str) or not v.strip() for v in aliases):
            raise ValueError(f"{model} 的 aliases 必须是非空字符串列表")
        discount = None
        if "off_peak" in values:
            rule = values["off_peak"]
            if not isinstance(rule, dict) or set(rule) != {"peak_hours", "multiplier"}:
                raise ValueError(f"{model} 的 off_peak 需要且仅支持 peak_hours、multiplier")
            hours = rule["peak_hours"]
            if not isinstance(hours, list) or any(not isinstance(interval, list) for interval in hours):
                raise ValueError(f"{model} 的 peak_hours 必须是起止时间数组的列表")
            discount = DailyDiscount(tuple(tuple(interval) for interval in hours),
                                     Decimal(str(rule["multiplier"])))
        rates = TokenRates(
            currency=values["currency"],
            input=Decimal(str(values["input"])), output=Decimal(str(values["output"])),
            cache_read=Decimal(str(values.get("cache_read", values["input"]))),
            cache_write=Decimal(str(values.get("cache_write", values["input"]))),
            off_peak=discount,
        )
        for name in (model, *aliases):
            if name in prices:
                raise ValueError(f"价格模型名称或别名重复：{name}")
            prices[name] = (model, rates)
    return prices


def estimate_cost(usage: TokenUsage, rates: TokenRates | None) -> tuple[Decimal | None, str]:
    """按已选定的文本 token 单价计算费用，返回金额和计价状态。"""
    for count in vars(usage).values():
        if count is not None and (type(count) is not int or not 0 <= count < 2**63):
            return None, "inconsistent_usage"
    if usage.input_tokens is None or usage.output_tokens is None:
        return None, "missing_usage"
    read = usage.cache_read_tokens or 0
    write = usage.cache_write_tokens or 0
    uncached = usage.input_tokens - read - write
    if (uncached < 0 or (usage.total_tokens is not None
                        and usage.total_tokens != usage.input_tokens + usage.output_tokens)
            or (usage.reasoning_tokens or 0) > usage.output_tokens):
        return None, "inconsistent_usage"
    if rates is None or (read and rates.cache_read is None) or (write and rates.cache_write is None):
        return None, "missing_rates"

    # 输入包含缓存 token，输出包含推理 token。分别计价的缓存项只计一次。
    for count, rate in ((usage.cache_read_tokens, rates.cache_read),
                        (usage.cache_write_tokens, rates.cache_write)):
        if count is None and rate is not None and rate != rates.input:
            return None, "missing_usage"
    amount = (uncached * rates.input + read * (rates.cache_read or 0)
              + write * (rates.cache_write or 0) + usage.output_tokens * rates.output)
    return amount / Decimal(1_000_000), "estimated"
