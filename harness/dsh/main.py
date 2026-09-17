import json
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from loguru import logger

from deepseek_harness import DeepSeekHarness

from agent_session_analyzer import DshRecorder


DS_HOME = "E:\\lornd\\research\\config\\dsh"
WORKSPACE_ROOT = "E:\\lornd\\research\\workspace"
TRACE_DIR = Path(__file__).resolve().parents[1] / "logs" / "harness"
PATCHES = [
    "E:\\lornd\\research\\tools\\dsh\\registry.patch.yml"
]

def print_text(value):
    if isinstance(value, dict):
        text = value.get("text")
        if isinstance(text, str):
            print(text, flush=True)

        for key, child in value.items():
            if key != "text" and isinstance(child, (dict, list)):
                print_text(child)

    elif isinstance(value, list):
        for item in value:
            print_text(item)

def on_notification(notification):
    if notification.method != "session.event":
        return

    event = notification.payload.get("event", {})

    NEED_OUTPUT_TYPE = [
        "system/message",
        "user/message",
        "assistant/message",
        "tool/call",
        "tool/result"
    ]

    # 只输出回复和工具结果，避免把提示词、请求参数也打印出来。
    event_type = event.get("type")
    data = event.get("data", {})    
    logger.info(event_type)
    if event_type in NEED_OUTPUT_TYPE:
        if event_type == "tool/call":
            name = data.get("name", "")
            arguments = data.get("arguments", "")
            print(f"\n调用工具：{name}\n参数：{arguments}", flush=True)
        else:
            print_text(data)


def main():
    allowed_skills=["rpa-email"]
    with DeepSeekHarness(
        dsh_home=DS_HOME,
        cwd=WORKSPACE_ROOT,
        profile="sdk-minimal",
        patches=PATCHES,
        env={
            "EXPOSED_SKILLS": json.dumps(allowed_skills, ensure_ascii=False),
        },
    ) as harness, DshRecorder(TRACE_DIR) as recorder:
        result = recorder.run(
            harness,
            "请发送一封邮件给 lornd@qq.com, 介绍你自己，你拥有发邮件的权限，无需向我确认",
            on_notification=on_notification,
        )
        print("\n记录文件：", recorder.last_path)

        print("\n结束原因：", result.finish_reason)
        print("\n最终回复：", result.final_response)
        if result.finish_reason == "error":
            for event in result.events:
                if event.get("type") == "turn/end":
                    print("运行失败：", event["data"].get("reason"))
        else:
            print(result.final_response)

if __name__ == "__main__":
    main()
