# 大模型调用和项目用量

`research-llm` 可以单独安装到项目的 Python 环境，复用 `research` 锁定的 LiteLLM 版本。它提供 `completion()`、`acompletion()` 和流式对话接口，也能接收现有 agent 的逐次用量。每条 SQLite 记录包含项目、使用人、模型、时间、token 明细、本次单价和估算金额。用量账本不保存 API Key、提示词和回复正文。

## 安装和 LiteLLM 风格调用

在 `research` 根目录运行下面的命令。请先激活目标项目的 Python 3.12 或更高版本的环境。

```powershell
python -m pip install -e ./llm
```

先在程序启动时配置项目和使用人，然后像调用 LiteLLM 一样传入模型、消息和原生参数。凭据遵循 LiteLLM 的配置方式，也可以显式传入 `api_key`。下面的例子从环境变量读取凭据，会向服务商发送请求并产生费用。

```python
import os
from research_llm import configure, completion

configure(
    project_id="project-a",
    user_id="姓名或学号",
    ledger_path="logs/project-usage.sqlite3",
)
response = completion(
    model="deepseek/deepseek-flash",
    messages=[{"role": "user", "content": "请回复 OK"}],
    api_key=os.environ["DASHSCOPE_API_KEY"],
    api_base="https://uniapi.thingo.com.cn/v1",
    max_tokens=64,
)
print(response.choices[0].message.content)
```

`completion()` 返回原生 `ModelResponse`，`tools`、`tool_choice`、`temperature`、`max_tokens`、`api_base`、重试等参数直接交给 LiteLLM。消息不再限制为纯文本，工具调用结果也可以直接传回。这里实现的是对话调用接口，没有包装 LiteLLM 的 Router、Responses API、embedding 等其他入口。一次函数调用对应一条记录，LiteLLM 内部未暴露的传输重试无法拆分为逐次账单。

`configure()` 在程序启动时调用一次；没有配置就调用模型会直接报错。省略 `user_id` 时使用操作系统用户名，共用机器或账号时应显式填写姓名或学号。相对账本路径按调用时的工作目录解析；价格在配置时读取，此后修改文件不会重算已有记录。并发任务会各自持有开始时的配置，不要用反复调用 `configure()` 来切换并发任务的归属。

异步调用使用同样的参数：

```python
from research_llm import acompletion

# 在已有异步函数内调用；configure() 与凭据准备同上。
response = await acompletion(
    model="deepseek/deepseek-flash",
    messages=[{"role": "user", "content": "请回复 OK"}],
    api_key=os.environ["DASHSCOPE_API_KEY"],
    api_base="https://uniapi.thingo.com.cn/v1",
    max_tokens=64,
)
```

流式调用返回的块保持原样；包装流只收集最终用量，不拼接或保存回复正文：

```python
with completion(
    model="deepseek/deepseek-flash",
    messages=[{"role": "user", "content": "请回复 OK"}],
    api_key=os.environ["DASHSCOPE_API_KEY"],
    api_base="https://uniapi.thingo.com.cn/v1",
    max_tokens=64,
    stream=True,
) as stream:
    for chunk in stream:
        if chunk.choices:
            print(chunk.choices[0].delta.content or "", end="", flush=True)
```

异步流使用 `stream = await acompletion(..., stream=True)`、`async with stream` 和 `async for chunk in stream`。流式请求默认补入 `stream_options.include_usage=True`，其他参数沿用原值。服务商没有返回 usage，或显式关闭该选项时，费用可能未知。提前退出请使用上下文管理器或调用 `close()` / `aclose()`；未消费、未关闭的流会保留 `pending`，提前关闭记为 `interrupted`，读取失败记为 `failed`。已收到的 token 用量仍会保留。

## 团队价格

默认价格集中在 `src/research_llm/prices.toml`，随独立包一起安装。价格文件记录 2026-11-18 的 Thingo 公开价目。所有单价以人民币每百万 token 计，已经乘过分组倍率和团队的 `0.8` 折扣。国内全模型-标准-文本、gpt-特惠和 gemini-特惠相对各自基础价的系数分别为 `0.8`、`0.024`、`0.048`。调用时会读取本地价格表。 [国内站价目](https://uniapi.thingo.com.cn/api/pricing) · [国际站价目](https://intlapi.thingo.com.cn/api/pricing)

- `tiers` 按表中顺序匹配。每档可配置 `max_input_tokens`、`max_output_tokens`，最大值包含在本档内。按含缓存的完整输入量和输出量选档，匹配的单价适用于整次请求。没有匹配档位时使用模型表中的单价。
- `off_peak` 用于分时价格。DeepSeek 的峰段是每天北京时间 09:00–12:00 和 14:00–18:00，包含开始时间。其余时间单价乘以 `0.5`。可在对应模型的 `peak_hours` 和 `multiplier` 中调整。
- `service_tiers` 里列出请求参数 `service_tier` 对应的额外倍率。直调入口从请求参数读取，trace 导入优先读模型尝试中的档位，其次读取 OpenHands 的 `litellm_extra_body.service_tier`。普通请求按标准档计价。

需要使用自己的价格表时，复制这份文件并调用 `configure(..., prices_path="config/prices.toml")`。自定义文件整体替代内置价格，不与默认价格隐式合并。模型名称精确匹配，`aliases` 只用于选价，不改写发往服务商的模型名称；新路由、SDK 返回的模型名或其他模型需要显式添加。未匹配到价格时照常记录 token，`cost_status` 为 `missing_rates`，金额留空。

省略 `cache_read`、`cache_write` 时，对应缓存按普通输入单价计费。`cache_write_1h` 单独指定 1 小时缓存写入价，`cache_write_1h_tokens` 是缓存写入总数中的子集。输出 token 已包含推理 token。缺少必要的缓存计数时标记为 `missing_usage`，明确返回 0 时照常计价。金额使用 `Decimal` 计算并以字符串保存，估算范围是文本 token。

每条记录保存模型、币种、本次采用的 `rates_per_million`、估算额和状态。`pricing_period` 记录时段或阶梯，`pricing_multiplier` 记录本次另行应用的倍率。有选价规则的模型会同时保留规则快照，SQLite 中是 `pricing_rule_json`，`usage.jsonl` 中是 `pricing_rule`。

同步、异步和流式调用在请求开始时保存价格快照，并按开始时间判断分时价格。阶梯价等到收到用量后再选档。手工导入可以传入带时区的 ISO 格式 `started_at`，账本统一存 UTC。历史记录不会随价格文件变化而更新。`LLMClient(...).complete()` 也复用这套流程，传入固定 `TokenRates` 可以覆盖该次调用的价格。

## OpenHands 和 DSH

三个现有 agent TOML 和两个独立 `run_task()` 入口均已启用入账。SDK 继续负责模型调用，公共 `harness.runner.run_agent()` 在原生执行收尾后导入 recorder 的 `model.attempt`，成功、失败和超时均走这条路径；子会话的 trace 也会导入。工具调用不作为模型请求单独计费。

agent TOML 只保留一个费用统计开关，放在已有的 `[agent]` 表中：

```toml
[agent]
# runner、recorder 等原有配置继续保留。
cost_tracking = true
```

项目和使用人从 `RESEARCH_PROJECT_ID`、`RESEARCH_USER_ID` 读取。可以在 PowerShell 中设置：

```powershell
$env:RESEARCH_PROJECT_ID = "research"
$env:RESEARCH_USER_ID = "姓名或学号"
```

也可以把下面两行写入项目根目录的 `.env`。启用入账时，公共入口读取这份文件，已经设置的进程环境变量优先；两个字段都必须为非空值，读取时去掉首尾空白。缺失时在模型请求前报错，不再使用操作系统用户名代替使用人。

```dotenv
RESEARCH_PROJECT_ID=research
RESEARCH_USER_ID=姓名或学号
```

共享账本固定放在 `research` 项目根目录的 `logs/model-usage.sqlite3`，不受启动目录、agent TOML 位置或 trial 输出目录影响。agent 统一使用包内的 `prices.toml`，包括已有的缓存价和 DeepSeek 分时规则；调整团队价格仍修改这份价格表。

`cost_tracking = false` 或省略该项时关闭费用统计，不读取上述用量配置、不打开账本，也不生成 `usage-config.json`、`usage.jsonl` 和 `usage.accounting`。原生 SDK 的 token 指标和配置的 recorder trace 仍按原流程保存。需要关闭 trace 时，单独调整 `recorder`。

旧的 `[agent.usage]` 配置表或 `usage` 布尔开关需要删除，并在已有的 `[agent]` 下改成 `cost_tracking = true`。使用 Python 公共入口时，传入 `run_agent(settings={"cost_tracking": True, ...})`；仍需提供 recorder 和 `output_dir`。两个独立 `run_task()` 入口默认开启，传入 `cost_tracking=False` 可以关闭。独立 `research_llm.configure(...)`、`UsageRecorder(...)`、`LLMClient(...)` 的显式参数配置保持原有方式，仍可自行指定项目、使用人、账本和价格文件。

运行产物包括：

- 共用 `logs/model-usage.sqlite3`：各实验和使用人的逐次明细。
- 每个 agent 输出目录的 `usage-config.json`：运行开始时读取的价格表与使用人快照。
- 同目录 `usage.jsonl`：每个模型尝试的 token、实际采用的单价、金额、状态和实验标识，包含失败尝试，不含正文。
- `AgentResult.usage.accounting` 和 `result.json` 中的同名字段：请求数、未计价数、按币种汇总金额以及账本和明细路径。

`usage-config.json` 是选价配置，其中 `prices` 保存包括分时规则在内的完整价格表，顶层单价只代表运行准备时的快照（`time_source=run_preparation`）。每条 trace 导入时仍按自己的事件时间重新选价，实际结果在 `usage.jsonl` 中查看。原有 trace 格式、SDK token 汇总和 `cost_usd` 保持原样；团队人民币金额在 `accounting.estimated_totals.CNY` 中读取。

trace 导入按 recording、session、event 标识去重，相同归属的重复导入会保留首次价格；同标识的归属或用量冲突会报错。外部 trace 通常只提供观测时间，因此账本以 `time_source` 标记时间来源，不能把它当作精确的请求开始时间。SDK 没有暴露的重试、请求 ID 和用量仍然未知，`uncommitted`、`interrupted` 等状态也会保留。

分时计价使用 trace 的 `time_ms`；缺失时使用 `observed_at_ms`，分别标记为 `trace_event`、`recorder_observed`。选价依据是事件时间，而不是导入时的时间。OpenHands 的模型尝试通常在响应后记录，所以临近时段边界的请求，估算结果可能与服务商按请求开始时间计算的账单不同。

入账失败会使本次公共调用报错；如果 SDK 已经失败，则保留 SDK 原异常并附加入账错误。强制结束进程可能来不及导入。恢复时可以在相同价格和使用人配置下，显式调用 `import_traces()` 导入原 trace，不需要重新运行 agent 或调用模型：

```python
from research_llm import UsageRecorder, import_traces

accounting = UsageRecorder(
    project_id="research", user_id="原使用人", ledger_path="logs/model-usage.sqlite3",
    # prices_path="原运行使用的价格文件.toml",
)
summary = import_traces(
    accounting, ["原 agent/trace/文件.jsonl"],
    model="deepseek/deepseek-flash", api_key_env="DASHSCOPE_API_KEY",
    log_path="logs/recovered-usage.jsonl",
)
```

首次恢复导入必须使用原运行的价格；已经入账的记录仍会复用原快照。账本旧版本的新增审核字段在下次写入时自动补齐，原记录和原金额保持不变。

## 导出和对账

在调用时使用的工作目录运行以下命令。查询按开始时间筛选，时间边界可带本地时区偏移，CSV 中保存的是 UTC 时间。请为 `--csv` 指定新文件。只看汇总时可以省略 `--csv`。

```powershell
python -m research_llm logs/project-usage.sqlite3 project-a `
  --from 2026-11-01T00:00:00+08:00 --before 2026-12-01T00:00:00+08:00 `
  --csv logs/project-a-2026-11.csv
```

默认输出项目汇总和 `by_user` 分人汇总；添加 `--user "姓名或学号"` 可筛选一个人，CSV 同样应用筛选。CSV 包含每条记录的价格快照；旧账本没有使用人的记录归入 `unknown`。

汇总按币种累加已估算金额，并列出未知金额的请求数。审核时请一起检查 `status`、`source_status` 和 `cost_status`，失败或中断也可能有服务商已报告的消耗。团队估算金额与实际扣费可能有差异，实际扣费和报销凭证仍以服务商账单及单位要求为准。

## 本地验证

费用统计开关和环境变量改动未执行测试，由用户自行验证。在 `research` 根目录使用已准备好的 `.venv`，并设置上述两个环境变量后，可以运行下面的命令。它检查实际的入账准备流程：读取环境变量、打开或初始化默认账本，并在临时目录写入价格快照；不会新增调用记录，也不调用模型或启动 Docker。

```powershell
@'
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from harness.usage import prepare_usage

with TemporaryDirectory() as directory:
    output_dir = Path(directory)
    prepare_usage(model={"model": "deepseek/deepseek-flash"}, output_dir=output_dir)
    snapshot = json.loads((output_dir / "usage-config.json").read_text(encoding="utf-8"))
    print(json.dumps({key: snapshot[key] for key in ("project_id", "user_id", "ledger_path")},
                     ensure_ascii=False, indent=2))
'@ | .\.venv\Scripts\python.exe -
```

预期显示配置的项目和使用人，账本指向当前项目根目录的 `logs/model-usage.sqlite3`。检查关闭分支时，将 agent TOML 的 `cost_tracking` 改为 `false`，再按原命令运行一个任务：新运行目录不应包含 `usage-config.json`、`usage.jsonl`，账本不应新增这次调用，原有 trace 仍会生成。这一步会实际运行 agent，并产生模型费用。旧的 `[agent.usage]` 配置表、`usage` 布尔开关，以及字符串、数字形式的开关都会报配置错误。

本次只做静态复核，未执行测试，由用户自行验证。在 `research` 根目录、已准备好的 Python 3.12+ 环境中安装上面的本地包后，可以先运行下面的命令。它只向独立验证账本写入四条用量，不访问模型或其他外部服务：

```powershell
@'
from research_llm import UsageRecorder, TokenUsage

recorder = UsageRecorder(project_id="daily-price-check", user_id="local-check", ledger_path="logs/daily-price-check.sqlite3")
for model in ("deepseek-flash", "qwen3.8-max-0902"):
    for hour in ("09:00", "12:00"):
        timestamp = "2026-11-15T" + hour + ":00+08:00"
        row = recorder.record_usage(
            model=model, call_id="daily-check-" + model + "-" + hour,
            started_at=timestamp, finished_at=timestamp, time_source="manual",
            usage=TokenUsage(input_tokens=1_000_000, output_tokens=1_000_000,
                             total_tokens=2_000_000, cache_read_tokens=1_000_000,
                             cache_write_tokens=0),
            status="completed", source_status="manual", source="manual",
        )
        print(model, hour, row["pricing_period"], row["pricing_multiplier"],
              row["estimated_cost"], row["currency"], row["rates_per_million"])
'@ | python -
python -m research_llm logs/daily-price-check.sqlite3 daily-price-check
```

示例的输入全部命中缓存。使用内置价格时，DeepSeek 两行分别为 `peak / 1 / 6.432 CNY` 和 `off_peak / 0.5 / 3.216 CNY`，Qwen 两行均为 `fixed / 1 / 30 CNY`。首次写入新验证账本合计 `69.648 CNY`，重复导入仍为 4 条记录。完整 agent 验证沿用原 evaluation 命令，需要原有 Docker、SDK、注册中心和 Thingo 凭据，并会产生模型费用。
