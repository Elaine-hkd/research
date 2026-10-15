"""Explicit user-run environment smoke check; does not run an agent or verifier."""

import argparse
import asyncio
from pathlib import Path
from uuid import uuid4

from . import BenchFlowEnvironment, materialize_skillsbench_task


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--task", type=Path, help="Native task package directory")
    source.add_argument("--index", type=Path, help="Research skillsbench.json index")
    parser.add_argument("--task-id")
    args = parser.parse_args()
    output = Path("workspace/environment") / uuid4().hex
    if args.index:
        if not args.task_id:
            parser.error("--index requires --task-id")
        task = materialize_skillsbench_task(args.index, args.task_id, output / "task")
    else:
        task = args.task
    async with BenchFlowEnvironment(task) as environment:
        result = await environment.execute([
            "python3", "-c",
            "from pathlib import Path; Path('/tmp/environment-hello.txt').write_text('hello'); print('ready')",
        ])
        if not result.success:
            raise RuntimeError(f"Smoke command failed: {result}")
        target = output / "hello.txt"
        await environment.download("/tmp/environment-hello.txt", target)
        if target.read_text(encoding="utf-8") != "hello":
            raise RuntimeError("Downloaded output does not match expected content")
        print(f"Command and file transfer succeeded: {target.resolve()}")
    print("Environment teardown requested successfully; images and host logs retained.")


if __name__ == "__main__":
    asyncio.run(main())
