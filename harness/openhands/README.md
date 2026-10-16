# OpenHands 入口与 runner

`main.py` 保留普通任务入口 `run_task(task, exposed_skills=None)`：使用 DeepSeek 模型、
现有四个 registry 工具、当前工作目录和 `OpenHandsRecorder`，结束时打印原来的 trace 路径。
模型、Agent、Conversation 的创建、执行和关闭由 `runner.run_agent` 负责。

```text
main.run_task
  → 设置 EXPOSED_SKILLS / SKILL_REGISTRY_URL
  → 创建 OpenHandsRecorder
  → asyncio.run(runner.run_agent(tools=..., recorder=..., workspace=...))
      → recorder.create_conversation
      → 工作线程执行 recorder.run(conversation, task)
      → 收集 AgentResult、关闭 conversation
  → 打印 recorder.last_path、关闭 Recorder
```

Recorder 目前只支持本地同步 Conversation。runner 使用 `asyncio.to_thread` 调用它的
原生 `run()`，保留 run/turn、模型尝试、usage 和 retry hooks；不修改 Recorder 包。
中断或超时时，会先请求中断并等待同步运行返回，再关闭 Conversation 和 Recorder。
同步模型或工具的阻塞调用需先返回，才能完成清理；registry 工具沿用其现有 HTTP 超时行为。

runner 有两种工具来源，调用时二选一：

- `tools=...`：使用调用方提供的工具，workspace 可传当前项目。
- `environment=...`：为指定任务环境绑定容器 shell，使用隔离 workspace。

两种入口都使用 OpenHands SDK 自带的 system prompt，runner 不覆盖提示词、不替换模板，
也不追加 system message 后缀。任务正文和 with-skill 的技能清单通过用户消息提供；
工具仍按调用方提供的 registry 工具或任务容器 shell 接入。

SkillsBench 的 `evaluation/openhands.py` 继续传入 environment、输出目录、技能清单和任务
预算，不传 Recorder 时使用原有 `conversation.arun()`。公共 runner 不导入 evaluation。

`output_dir` 可用于保存原生事件和结果；main.py 沿用 Recorder 的 JSONL，不额外启用这份输出。
`timeout_sec=None` 表示不加整体 agent 时间上限，保持 main.py 原有行为；SDK 单次请求和工具
本身的超时独立生效。main.py 的同步 run_task 用于同步调用方；异步调用方直接 await run_agent。

EXPOSED_SKILLS 使用 `json.dumps` 编码，供 registry 工具的 `json.loads` 读取。
工具在设置环境变量后导入。已有 SKILL_REGISTRY_URL 优先，否则使用 main.py 声明的本地地址。

验证前提：当前 Python 环境可导入项目使用的 OpenHands SDK、OpenHandsRecorder 和工具依赖，
已配置 DEEPSEEK_API_KEY，registry 可用。请在项目根目录运行模块，避免 openhands 目录遮蔽 SDK：

```powershell
Set-Location E:\lornd\research
.\.venv\Scripts\python.exe -X utf8 -m harness.openhands.main
```

该命令会执行 main() 中现有的发邮件任务；预期仍使用 registry 工具并打印 Recorder 日志路径。
需要程序化取得 AgentResult 的调用方可直接调用 runner.run_agent。

本次仅检查源码、diff 和静态语法；未执行模型请求、任务、测试或运行验证。
