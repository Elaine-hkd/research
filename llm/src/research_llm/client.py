from dataclasses import dataclass
from decimal import Decimal
import math
import os
from pathlib import Path
from typing import TYPE_CHECKING

from .accounting import UsageRecorder
from .pricing import TokenRates

if TYPE_CHECKING:
    from litellm import ModelResponse


@dataclass(frozen=True)
class Completion:
    """SDK 原始回复与账本中的调用编号和费用估算。"""

    response: "ModelResponse"
    call_id: str
    estimated_cost: Decimal | None
    currency: str | None
    cost_status: str


@dataclass(frozen=True)
class LLMClient:
    """项目与模型配置。每次 complete() 发起一次同步的非流式文本调用。"""

    project_id: str
    model: str
    api_key_env: str
    ledger_path: str | Path
    rates: TokenRates | None = None
    base_url: str | None = None
    timeout: float = 120
    user_id: str | None = None
    prices_path: str | Path | None = None

    def __post_init__(self):
        if not self.project_id.strip() or not self.api_key_env.strip():
            raise ValueError("project_id 和 api_key_env 不能为空")
        provider, _, model_name = self.model.partition("/")
        if not provider.strip() or not model_name.strip():
            raise ValueError("model 必须使用 LiteLLM 的 provider/model 格式")
        if (isinstance(self.timeout, bool) or not isinstance(self.timeout, (int, float))
                or not math.isfinite(self.timeout) or self.timeout <= 0):
            raise ValueError("timeout 必须为有限正数")

    def complete(self, messages: list[dict[str, str]], *, max_tokens: int | None = None) -> Completion:
        """发送文本消息，保存本次用量后返回回复。API 出错时保留失败记录并抛出原异常。"""
        if not isinstance(messages, list) or not messages or any(
            not isinstance(message, dict) or not isinstance(message.get("content"), str)
            or message.get("role") not in ("system", "developer", "user", "assistant")
            for message in messages
        ):
            raise ValueError("messages 必须是非空的角色和文本消息列表")
        if max_tokens is not None and (type(max_tokens) is not int or max_tokens <= 0):
            raise ValueError("max_tokens 必须为正整数")
        key = os.environ.get(self.api_key_env, "")
        if not key.strip():
            raise ValueError(f"请设置模型凭据环境变量 {self.api_key_env}")

        from .api import _complete

        recorder = UsageRecorder(project_id=self.project_id, ledger_path=self.ledger_path,
                                  user_id=self.user_id, prices_path=self.prices_path)
        response, row = _complete(
            recorder, self.model, [{"role": msg["role"], "content": msg["content"]} for msg in messages],
            {"api_key": key, "api_base": self.base_url, "timeout": self.timeout, "max_tokens": max_tokens,
             "stream": False, "caching": False, "num_retries": 0, "max_retries": 0},
            rates=self.rates, api_key_env=self.api_key_env,
        )
        amount = Decimal(row["estimated_cost"]) if row["estimated_cost"] is not None else None
        return Completion(response, row["call_id"], amount, row["currency"], row["cost_status"])
