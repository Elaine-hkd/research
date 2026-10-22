import asyncio
import json
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from harness.runner import run_agent


DS_HOME = "E:\\lornd\\research\\config\\dsh"
WORKSPACE_ROOT = "E:\\lornd\\research\\workspace"
TRACE_DIR = Path(__file__).resolve().parents[1] / "logs" / "harness"

def run_task(task, exposed_skills: list[str] | None = None, *, tools=None):
    result = asyncio.run(run_agent(
        runner="harness.dsh.runner:execute",
        recorder="agent_session_analyzer:DshRecorder",
        instruction=task,
        model={"provider": "deepseek-official", "model": "deepseek-v4-flash",
               "api_key_env": "DEEPSEEK_API_KEY"},
        settings={"dsh_home": DS_HOME, "profile": "sdk-minimal", "env": {
            "EXPOSED_SKILLS": json.dumps(exposed_skills or [], ensure_ascii=False),
        }},
        tools=tools, workspace=WORKSPACE_ROOT, timeout_sec=None,
        output_dir=TRACE_DIR,
    ))
    print("\n记录文件：", result.trace_path)

    print("\n结束原因：", result.finish_reason)
    # 助手回复由运行器的共用控制台输出负责显示。
    return result

def main():
    run_task(
        "请发送一封邮件给 lornd@qq.com, 介绍你自己，你拥有发邮件的权限，无需向我确认",
        exposed_skills=["rpa-email"],
    )

if __name__ == "__main__":
    main()
