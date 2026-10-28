"""供用户显式运行的环境冒烟检查，不运行智能体或评分器。"""

import argparse
import asyncio
from pathlib import Path
from uuid import uuid4

from . import BenchFlowEnvironment, materialize_skillsbench_task


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--task", type=Path, help="原生任务包目录")
    source.add_argument("--index", type=Path, help="research 的 skillsbench.json 索引")
    parser.add_argument("--task-id")
    args = parser.parse_args()
    output = Path("workspace/environment") / uuid4().hex
    if args.index:
        if not args.task_id:
            parser.error("使用 --index 时必须提供 --task-id")
        task = materialize_skillsbench_task(args.index, args.task_id, output / "task")
    else:
        task = args.task
    async with BenchFlowEnvironment(task) as environment:
        print(f"框架解释器：{environment.worker_python_executable}")
        print(f"任务技能解释器：{environment.executor.python_executable}")
        result = await environment.execute([
            environment.worker_python_executable, "-I", "-B", "-c",
            "from pathlib import Path; Path('/tmp/environment-hello.txt').write_text('hello'); print('已就绪')",
        ])
        if not result.success:
            raise RuntimeError(f"冒烟检查命令失败：{result}")
        target = output / "hello.txt"
        await environment.download("/tmp/environment-hello.txt", target)
        if target.read_text(encoding="utf-8") != "hello":
            raise RuntimeError("下载内容与预期不符")
        print(f"命令执行和文件传输成功：{target.resolve()}")
    print("已成功请求销毁环境；镜像和主机日志已保留。")


if __name__ == "__main__":
    asyncio.run(main())
