import asyncio
import json
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from harness.runner import create_run_directory, run_agent


DS_HOME = "E:\\lornd\\research\\config\\dsh"
WORKSPACE_ROOT = "E:\\lornd\\research\\workspace"
OUTPUT_ROOT = Path(__file__).resolve().parents[2] / "logs" / "harness" / "dsh"

def run_task(task, exposed_skills: list[str] | None = None, *, tools=None, cost_tracking: bool = True):
    output_dir = create_run_directory(OUTPUT_ROOT)
    print("运行目录：", output_dir)
    result = asyncio.run(run_agent(
        runner="harness.dsh.runner:execute",
        recorder="agent_session_analyzer:DshRecorder",
        instruction=task,
        model={"provider": "deepseek-official", "model": "deepseek-v4-flash",
               "api_key_env": "DEEPSEEK_API_KEY"},
        settings={"dsh_home": DS_HOME, "profile": "sdk-minimal", "env": {
            "EXPOSED_SKILLS": json.dumps(exposed_skills or [], ensure_ascii=False),
        }, "cost_tracking": cost_tracking},
        tools=tools, workspace=WORKSPACE_ROOT, timeout_sec=None,
        output_dir=output_dir,
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
