import asyncio
import json
import os

from litellm.types.utils import Usage
from openhands.sdk.llm.utils.telemetry import Telemetry

from harness.runner import run_agent
from tools.loader import REGISTRY_TOOLS


def _compatible_cache_buckets(usage) -> tuple[int, int]:
    if isinstance(usage, Usage):
        details = getattr(usage, "prompt_tokens_details", None)
    else:
        details = getattr(usage, "input_tokens_details", None)

    if details is None:
        return 0, 0

    cache_read = getattr(details, "cached_tokens", None) or 0
    cache_write = 0

    if isinstance(usage, Usage):
        cache_write = getattr(details, "cache_write_tokens", None)
        if cache_write is None:
            cache_write = getattr(details, "cache_creation_tokens", None)

    return int(cache_read), int(cache_write or 0)


Telemetry._cache_buckets = staticmethod(_compatible_cache_buckets)

registry_address = "http://127.0.0.1:5000"


def run_task(task, exposed_skills: list[str] | None = None, *,
             tools=None):
    os.environ["EXPOSED_SKILLS"] = json.dumps(exposed_skills or [], ensure_ascii=False)
    os.environ.setdefault("SKILL_REGISTRY_URL", registry_address)

    selected = [*REGISTRY_TOOLS, "think", "finish"] if tools is None else tools

    result = asyncio.run(run_agent(
        runner="harness.openhands.runner:execute",
        recorder="agent_session_analyzer:OpenHandsRecorder",
        instruction=task,
        model={
            "model": "deepseek/deepseek-flash",
            "api_key_env": "DEEPSEEK_API_KEY",
            "base_url": "https://api.deepseek.com",
        },
        settings={}, tools=selected, workspace=os.getcwd(),
        output_dir="./logs/harness/openhands", timeout_sec=None,
    ))
    print(result.trace_path)
    return result


def main():
    run_task("请发送一封邮件给 lornd@qq.com, 介绍你自己，你拥有发邮件的权限，无需向我确认", exposed_skills=["rpa-email"])

if __name__ == "__main__":
    main()
