# 实验与独立 trace 的可视化接入指南

## 查看已经生成的实验

项目使用本地 editable 安装的 `E:\lornd\agent-session-analyzer`。在现有项目环境运行：

```powershell
Set-Location E:\lornd\research
.\.venv\Scripts\python.exe -X utf8 -m agent_session_analyzer --root E:\lornd\research\logs\evaluation --root E:\lornd\research\logs\harness
```

浏览器打开 http://127.0.0.1:8765 。若旧查看器仍在运行，先 Ctrl+C 退出后重启，
浏览器 Ctrl+F5 更新静态资源。查看已有结果不需要 Docker 或模型密钥，也不会重跑实验。
需要完整 condition 对比时，应选择整个实验目录或 `logs/evaluation`，不要只选择其中的 `agent` 目录。

页面上方选择实验和任务。汇总按 condition 展示 reward、输入/输出 token、费用、
工具调用和全流程耗时；括号是该指标已知样本数 / 已记录样本数。
下方按同一实验的 `task_id + repeat` 横向配对，点击“查看 trace”打开对应时间线。
不同实验运行单独配对；缺少一个 condition 时显示“尚无结果”，失败保留状态、错误和 null reward。
这张表展示数据，不把一次重复的差异当成统计结论。

当前已经生成的 `agent/events.jsonl` 可以直接导入，不必修改、移动或复制日志。
原生日志没有 completion 明细时，无法恢复逐次 token、隐藏重试和精确 step 耗时。
页面独立展示 trial 累计用量，逐步缺失数据仍为未知。导入生成的 session ID 标识源文件，
不是原始 SDK conversation ID；turn 是依据用户消息推导的分组。

## 两个独立约定

```text
harness 原生事件 → adapter / recorder → harness-trace/1 → 同一个时间线页面
适配函数返回 AgentResult → Evaluation 保存 trial 结果 → 同一个 condition 对比页面
```

trace 可以单独存在，不必属于实验。实验通过 `trace_path` 关联 trace，不要求前端理解
某个 SDK。展示层的目录兼容逻辑集中在 analyzer 的 `experiments.py`；不要向页面增加
`if harness == ...` 的事件解析。后续 harness 优先直接录制标准 trace，原生日志导入用于兼容旧记录。

## 实现 agent.runner 适配函数

参考本项目完整的 [OpenHands 适配函数](openhands.py)，函数接口为：

```python
async def run_agent(context: TrialContext, model: dict, settings: dict) -> AgentResult:
```

函数由 `run_evaluation` 在每个 trial 中调用。它需要完成以下步骤：

1. 从 `context.task.instruction` 取得任务正文，从 `context.prepared.skill_catalog` 取得技能清单。
   condition 已经决定是否注入技能；不要重新从宿主目录加载其他技能。
2. 配置 harness 的模型。`model.api_key_env` 是环境变量名，真实密钥从本地环境取得。
   模型配置来自当前 `[model]`，`settings` 来自 `[agent]`。
3. 将 harness 的文件和命令工具绑定到 `context.environment`。
   命令使用 `await context.environment.execute(...)`，上传、下载使用该环境已有接口。
   不能让模型通过默认宿主 shell 操作评估数据。普通独立运行可由调用方提供自己的工具。
4. 在 `context.output_dir` 内创建本次独立的 recorder，并运行 harness。
   不同 trial 各建一个 recorder，不把两个 condition 写进同一个 session 文件。
5. 返回 `AgentResult`，关闭自己创建的 recorder 和 conversation。
   环境由 Evaluation 创建和销毁，适配函数不用重复管理。命令通道、录制或 SDK 失败应抛出异常，
   不伪造成功或 0 reward。失败前可把部分 `AgentResult` 写入 `context.output_dir / "agent/result.json"`，
   Evaluation 会在失败收尾时读取它；当前 OpenHands harness 已这样处理。

通用结果定义在 [harness/base.py](../harness/base.py)。推荐字段如下，未知数值使用 `None`：

```python
result = AgentResult(
    final_response="模型返回的最终回答",
    finish_reason="finished",
    tool_calls=7,
    usage={
        "input_tokens": 50093,
        "output_tokens": 2205,
        "total_tokens": None,
        "cache_read_tokens": 44800,
        "cache_write_tokens": None,
        "reasoning_tokens": 809,
        "cost_usd": 0.0045027,
    },
    trace_path=str(recorder.last_path),
)
```

这是返回值示例；数值必须来自实际 SDK 统计，不能照抄。`trace_path` 可以是绝对路径，
也可以相对当前 trial 目录。推荐 `str(recorder.last_path)`，由 Evaluation 在最终结果中
转换成相对路径。如果不知道工具调用次数，显式使用 `tool_calls=None`。
缓存/推理 token 通常已经包含在输入/输出口径中，不再次相加；没有来源总数时不擅自计算 total。
SDK 原始用量可另外保存在 `usage["tokens"]`，标准字段用于跨 harness 展示。

现有 [OpenHands harness](../harness/openhands/runner.py) 已支持 `recorder=`，会在 finally 中填写累计用量与 trace 路径，
因此 `evaluation/openhands.py` 只负责创建带 metadata 的 recorder 和转发参数。
其同步运行在工作线程上，容器操作仍调度到原来的 asyncio loop，不能用新的事件循环替代。
recorder 通过本机 SDK 的 completion/retry 回调捕获模型尝试；SDK 升级后需要核对回调契约。
本次只核对了 1.53.0 的源码，运行验证由使用者执行。

把函数放在可导入模块中，在实验 TOML 中选择它，例如现有配置：

```toml
[agent]
runner = "evaluation.openhands:run_agent"
```

其他 harness 改为其实际 `module:function`，不需要注册工厂或更改查看器。
数据集准备与 evaluator 可继续复用，也可以使用 Evaluation 已有的函数接口替换。
当前 SkillsBench 业务配置只接受 `no-skill` / `with-skill`；页面本身可展示任意 condition 名称。
增加业务条件时还要修改配置校验、任务准备及注入逻辑，不能只改页面标签。

## 新 harness 如何录制标准 trace

已有 dsh / OpenHands adapter、recorder 时直接复用包顶层的 `DshRecorder` / `OpenHandsRecorder`。
例如 dsh 原始调用 `harness.run(...)` 应由 `recorder.run(harness, ...)` 包装，
OpenHands 使用 `recorder.create_conversation(...)` 和 `recorder.run(...)`。
每个 recorder 的参数、返回值和同步限制以 analyzer README 及源码为准。

没有适配器时，新增 `adapters/<harness>.py` 做纯事件转换，`recorders/<harness>.py`
连接 SDK 回调与 JSONL 文件。保留来源 ID 和 raw，统一行至少包含这些字段：

| 字段 | 约定 |
| --- | --- |
| `schema` | 固定 `harness-trace/1` |
| `harness` / `session_id` | harness 名称 / 会话 ID；一个文件只包含一个 session |
| `event` | 下表的事件类型 |
| `turn_id` / `step_id` | 相应层级的标识；没有对应层级时为 null |
| `attempt_id` / `tool_call_id` | 关联模型尝试及工具调用、结果的 ID |
| `time_ms` | 来源时间戳；缺少时为 null，不用读取时间冒充执行时间 |
| `model` | 模型信息对象，例如 `{"model": "provider/model"}` |
| `data` | 归一化内容对象 |
| `raw` | 原始事件，可为 null |

| `event` | `data` 中的主要内容 |
| --- | --- |
| `session.recording` | 可选 `metadata`：task_id、experiment_id、experiment_name、trial_id、condition、repeat |
| `turn/start` | `input`：本轮任务输入 |
| `user/message` | `content`：任务文本或文本块，供时间线直接显示 |
| `step/start` / `step/end` | 保留原生边界；推导边界需要注明来源 |
| `model.attempt` | `attempt_index`、`status`、`text`、`reasoning`、`finish_reason`、`usage` |
| `tool.call` | `name`、`arguments`；行上的 tool_call_id 与结果一致 |
| `tool.result` | `content`、`is_error`；错误也保留结果关联 |
| `turn/end` | `reason: {"kind": "completed"}` 或实际中断/失败状态 |

每个 `model.attempt.data.usage` 使用上面的标准 token 字段，即使全部未知也保留对象和 null。
一个模型响应请求多个工具时，只记一次模型尝试；重试单独记尝试。
step 的定义可能因 harness 而异，必须在录制信息中说明，不强行认为不同 SDK 的 step 可直接等同。
每行写完换行并 flush，关闭时落盘；失败不能静默丢记录。不要将 API key 或凭证配置写入 metadata。

## 实验结果如何与 trace 关联

Evaluation 自动生成以下结构，新适配函数不需要自己写 manifest 或 reward：

```text
<experiment>/
  manifest.json                 schema_version=1，config.conditions
  results.jsonl                 每个结束的 trial 一行
  trials/<trial_id>/
    result.json                 同一行结果的 JSON 副本
    agent/trace/<session>.jsonl  标准 trace，实际名称由 recorder 决定
```

`results.jsonl` 行的 `trace_path` 相对 `trials/<trial_id>/`，例如
`agent/trace/abc.jsonl`。目录搬迁时，只要整体移动实验目录，相对引用仍然有效。
没有该字段的旧实验才按固定的 `agent/events.jsonl` 寻找 trace。
标准 trace 与原生 events 同时存在时，关联的标准 trace 作为展示入口，原生文件不重复计数。
引用缺失、重复归属或位于所选扫描目录之外时，页面显示原因，不擅自改用另一个文件。

实验外运行只需 recorder 输出的 JSONL。缺少 experiment / condition 标签会显示为独立运行，
不要求补建 manifest。选择 `logs/harness` 即可与实验 trace 一起浏览。

## 最小人工验证

启动上面的查看器后，检查已有实验的两组 condition 均能打开，reward 与原结果一致；
原生时间线能看到工具参数、结果和最终回答；累计 token 出现在 trial 汇总，逐次缺失仍为未知。
再打开已有的独立 dsh / OpenHands 标准 trace，确认仍显示原来的层级与已知用量。
已有结果查看通过后，再用原评估命令运行选定任务，检查新结果的 trace_path 和标准 trace。
重新评估需要现有 Docker、模型密钥及任务网络条件，且会产生模型调用费用。

本次未执行测试、构建、lint、类型检查、模型调用或查看器启动，只进行了源码和 diff 检查。
