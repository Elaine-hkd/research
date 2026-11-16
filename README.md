# Research

这个框架用来运行 Agent 任务、组织对照实验，并保存执行过程、评分和模型消耗。当前接入了 OpenHands 和 dsh 两种 harness，以及 SkillsBench 和发邮件示例数据集。

刚接触项目时，可以先运行一个简单任务，确认模型、harness 和 trace 记录正常，再开始实验：

```text
安装依赖 → 配置模型和使用人 → 单次 harness 运行 → 配置数据集和环境 → 预览实验 → 运行和查看结果
```

harness 负责组织模型与工具的交互；experiment 是一次完整实验计划；trial 是一个任务在某个条件下的一次执行。实验入口会为每个 trial 准备环境、运行 Agent、评分、清理环境并保存结果。

## 1. 安装环境

以下命令使用 Windows PowerShell，以 `D:\Projects\research` 为例。先准备 Git、uv 和 Python 3.12；运行 SkillsBench 时还需要 Docker CLI、Compose v2 和正在运行的 Linux Docker 引擎。只做不使用工具的单次试跑，或运行本地邮件实验时，无需 Docker。

### 准备仓库和 Python 依赖

`research` 目前通过本地可编辑依赖使用相邻的 `agent-session-analyzer` 仓库；`research-llm` 则位于本仓库的 `llm/`。请保持以下目录关系：

```text
D:\Projects\
  research\
  agent-session-analyzer\
  skill_registry\             使用 registry 工具时需要
```

如果尚未下载仓库，在已有的项目父目录执行以下命令；私有仓库需要当前 Git 已有访问权限。已有仓库请更新现有 checkout，无需重复克隆。

```powershell
Set-Location D:\Projects
git clone https://github.com/LDlornd/research.git
git clone https://github.com/LDlornd/agent-session-analyzer.git
git clone https://github.com/LDlornd/skill_registry.git

Set-Location .\research
uv sync --python 3.12
```

`uv sync` 会创建本项目的 `.venv` 并按 `uv.lock` 安装依赖，也会安装相邻的 analyzer 和本仓库的 `research-llm`。无需分别给这两个包再建环境，也无需给 analyzer 安装额外的 OpenHands 依赖组。

安装后可以检查 analyzer 是否包含实验入口需要的接口：

```powershell
uv run python -c "from agent_session_analyzer.outcomes import trial_failure_type; print('OK')"
```

预期输出 `OK`。如果提示缺少 `outcomes`，先确认相邻 analyzer 仓库已经更新，再重新执行 `uv sync`。

后文除注明使用另一终端外，均从 `research` 根目录运行。命令统一使用 `uv run`，因此不必先激活虚拟环境；也可以将 `uv run python` 换成 `.\.venv\Scripts\python.exe`。

### dsh 的额外准备

dsh 使用 Python SDK 及其运行时，并通过 `config/dsh/profiles/sdk-minimal` 加载本项目的工具插件。新 checkout 还需要准备这个 profile 的本地插件依赖；在安装了 Node.js 和 pnpm 的终端执行：

```powershell
pnpm --dir .\config\dsh\profiles\sdk-minimal install --no-frozen-lockfile
```

插件依赖使用相对于 profile 的 `file:../../../../tools/dsh`，无需填写本机盘符。Python SDK 默认使用依赖包携带的本机运行时；如果安装的平台包没有对应可执行文件，需要先补齐对应平台的 `deepseek-harness-runtime-bin`，再试跑 dsh。只运行 OpenHands 时可以跳过本节。

## 2. 配置凭据和费用统计

在项目根目录创建或补充 `.env`，将示例值替换成自己的配置：

```dotenv
DEEPSEEK_API_KEY=你的模型密钥
RESEARCH_PROJECT_ID=research
RESEARCH_USER_ID=姓名或学号
SKILL_REGISTRY_URL=http://127.0.0.1:5000
```

`.env` 已被 Git 忽略，请保留密钥在本机。已有进程环境变量优先于 `.env`；如果终端中设置过旧值，需要一并调整。

| 配置 | 用途 |
| --- | --- |
| `DEEPSEEK_API_KEY` | 默认模型凭据；由 agent 配置中的 `api_key_env` 指定读取哪个环境变量 |
| `RESEARCH_PROJECT_ID` | 费用归属的项目编号，同一项目的同学使用相同值 |
| `RESEARCH_USER_ID` | 费用归属的使用人，请填写能识别本人的姓名或学号 |
| `SKILL_REGISTRY_URL` | 独立 harness 入口使用的注册中心地址；实验使用 environment TOML 中的 `registry_url` |

默认 agent 配置和两个独立 `run_task()` 入口均开启费用统计。开启时，项目和使用人都必须填写，否则会在模型调用前报错。实验在 `[agent]` 下用 `cost_tracking = false` 关闭；独立入口传入 `cost_tracking=False`。关闭费用统计后，已有 token 指标和配置的 trace 记录仍会保留。

费用账本统一保存在 `logs/model-usage.sqlite3`，单价在 `llm/src/research_llm/prices.toml` 中维护。模型请求地址由下节的 `base_url` 配置。

## 3. 配置智能体

实验使用 `config/agents/` 下的 TOML，同时选择模型、harness、工具和运行预算：

| 文件 | 使用场景 |
| --- | --- |
| `openhands.toml` | OpenHands + registry、终端和文件工具，用于 SkillsBench |
| `dsh.toml` | dsh + 同类任务环境工具，用于 SkillsBench |
| `openhands-shell-only.toml` | OpenHands，仅 terminal、think、finish |
| `openhands-email.toml` | OpenHands + registry、think、finish，用于 Windows 本地邮件实验 |

可以复制一份现有配置，再修改需要比较的参数。以 `openhands.toml` 中的主要字段为例：

```toml
[model]
model = "deepseek/deepseek-flash"
base_url = "https://api.deepseek.com"
api_key_env = "DEEPSEEK_API_KEY"
timeout = 120
num_retries = 0

[agent]
runner = "harness.openhands.runner:execute"
recorder = "agent_session_analyzer:OpenHandsRecorder"
cost_tracking = true
max_iterations = 100
tool_timeout_sec = 120
max_output_chars = 20000
tools = ["search_skills", "load_skill", "load_resource", "execute_resource", "terminal", "read_file", "write_file"]
```

`[model]` 配置实际请求的模型、地址和凭据来源。走网关时，在这里填写网关的 `base_url`、该网关要求的模型名和密钥环境变量名称；不要把密钥直接写进 TOML。更换模型后，还需核对价格表中是否有该模型或计价别名。

`[agent]` 中，`runner` 选择执行函数，`recorder` 选择对应的 trace 记录器，`tools` 决定模型可以使用哪些工具。`timeout` 是 OpenHands 的单次模型请求超时；`tool_timeout_sec` 是工具执行预算。agent 总时限默认来自任务，若在 `[agent]` 配置 `timeout_sec` 则覆盖任务设置。

两种 harness 使用各自 SDK 的模型参数和运行选项。dsh 配置保留 `provider = "deepseek-official"`、`model = "deepseek-v4-flash"` 和 `profile = "sdk-minimal"` 等原生字段，不接受 OpenHands 的 `max_iterations`。更换 harness 时，请从相应的配置文件开始修改。

**这些 TOML 由实验入口读取。** 独立 `run_task()` 的模型配置目前写在 `harness/openhands/main.py` 和 `harness/dsh/main.py` 中；修改 TOML 不会改变独立入口的模型。

## 4. 先运行一个 harness 任务

完成安装和 `.env` 配置后，可以先做下面的连通性试跑。两条命令都会调用付费模型，并在 `logs/harness/<harness>/<运行目录>/` 保存结果和 trace；示例工具列表不包含邮件或资源执行工具，无需启动注册中心。

OpenHands：

```powershell
uv run python -X utf8 -c "from harness.openhands.main import run_task; result = run_task('请用一句话介绍你自己。', tools=['finish']); print(result.final_response)"
```

dsh（需先完成 profile 准备）：

```powershell
uv run python -X utf8 -c "from harness.dsh.main import run_task; result = run_task('请用一句话介绍你自己。', tools=[]); print(result.final_response)"
```

预期终端打印本次运行目录、回复和 trace 路径。dsh 的配置目录和工作目录会按当前仓库位置确定，工作目录不存在时自动创建。

需要测试 skill 工具时，先按下一节启动注册中心，再把任务、`exposed_skills` 和 `tools` 传给 `run_task()`。例如，`exposed_skills=["rpa-email"]` 控制可见技能，`tools=["search_skills", "load_skill"]` 控制可调用工具，两者含义不同。独立入口没有绑定任务环境时，`execute_resource` 在注册中心服务端执行脚本。

请注意：直接执行 `python -m harness.openhands.main` 或 `python -m harness.dsh.main` 会运行源码中现有的默认邮件任务，向 `lornd@qq.com` 真实发信。初次检查请使用上面的短任务命令。

## 5. 准备数据集和执行环境

实验由四份配置组合，命令行直接指定各自文件：

| 参数 | 配置位置 | 负责内容 |
| --- | --- | --- |
| `--dataset` | `config/datasets/` | 任务索引、任务筛选、抽样、数据准备和环境设置函数 |
| `--environment` | `config/environments/` | 本地或 Docker 环境、注册中心地址、启动和清理预算 |
| `--agent` | `config/agents/` | 模型、harness、工具、记录器和运行预算 |
| `--evaluation` | `config/evaluation/` | 条件、重复次数、通过阈值、评分函数和输出根目录 |

配置中的 `dataset.index` 和 `output_dir` 相对于所在 TOML 文件解析。SkillsBench 索引内的资源路径则相对于索引文件解析，已经改为仓库内的相对路径，移动项目目录后无需替换盘符。

索引中的 `source_task_dir` 保留原始数据来源，只用于溯源，不参与本地资源读取。实验会记录索引哈希；索引内容改变后，旧实验的待重跑任务会触发校验，请使用新实验名重新生成计划，历史结果和 trace 可以继续查看。

### 启动 skill 注册中心

使用 registry 工具的实验需要能访问 HTTP 注册中心。在另一个 PowerShell 终端，从 `research` 根目录启动：

```powershell
Set-Location D:\Projects\research
$env:SKILLS_DIR = (Resolve-Path .\.skills).Path
uv run --project ..\skill_registry skill-registry
```

这会为相邻 `skill_registry` 项目准备其独立环境，并扫描当前仓库的 `.skills`。启动后可打开 `http://127.0.0.1:5000/docs` 查看接口。请保持该终端运行；使用已有服务时，无需再启动，只需在 environment TOML 中填写实际地址。

默认 SkillsBench 环境使用 `registry_url = "http://127.0.0.1:5000"`。框架从宿主访问该地址，技能资源下载后在本次 trial 的容器中执行。每个任务只允许访问其技能清单；注册中心必须已有这些技能，并与数据集中的正文、资源保持一致。环境设置阶段会检查技能，但不会自动向远端服务注册技能。

### SkillsBench：先选一个任务

原始数据位于 `data/`，索引为 `data/task/skillsbench.json`，技能位于 `.skills/skillsbench/`。默认配置在排除忽略项后抽取 10 个任务，只运行 `with-skill`，每个任务重复 3 次，共 30 个 trial。

第一次运行建议复制配置，缩小到一个任务、一次重复：

```powershell
Copy-Item .\config\datasets\skillsbench.toml .\config\datasets\skillsbench-demo.toml
Copy-Item .\config\evaluation\skillsbench.toml .\config\evaluation\skillsbench-demo.toml
```

把数据集副本 `config/datasets/skillsbench-demo.toml` 改为：

```toml
[dataset]
index = "../../data/task/skillsbench.json"
task_ids = ["3d-scan-calc"]
sample_size = 1
seed = 42
loader = "datasets.skillsbench:load_tasks"
prepare = "datasets.skillsbench:prepare_task"
setup = "datasets.skillsbench:setup_environment"
```

在评估副本 `config/evaluation/skillsbench-demo.toml` 中，将 `repeats` 改为 `1`；其余字段保持不变：

```toml
output_dir = "../../logs/exp"
conditions = ["with-skill"]
repeats = 1
pass_threshold = 1.0

[evaluator]
function = "datasets.skillsbench:evaluate"
```

`task_ids` 指定候选任务，`exclude_task_ids` 在抽样前排除任务，`sample_size` 决定抽取数量，`seed` 固定抽样结果。同次实验的条件和重复共用这份任务清单。若只指定一个任务，请同步调整 `sample_size`，否则默认的 10 会超过候选数量。命令行 `--task` 只覆盖候选列表，也需要注意这一点。

SkillsBench 使用独立的 Linux Docker 任务环境，构建镜像和 verifier 安装依赖需要网络。任务镜像需具备任务和技能所需的 Bash、解释器及第三方依赖；框架 worker 的独立 Python 运行时由准备流程注入。具体环境设置见 [环境说明](environment/README.md)。

### 本地邮件示例

如果希望先看一个无需 Docker 的完整实验，可以使用已有的 `email-demo` 配置。任务在 `data/task/email-demo.toml`，智能体在 `config/agents/openhands-email.toml`，环境在 `config/environments/email-demo.toml`，评估配置在 `config/evaluation/email-demo.toml`。

这个实验使用现有 `rpa-email` 技能，运行时会真实发信。请先核对任务里的收件人、主题和指令，并确保注册中心的 `rpa-email` 和对应 RPA 邮件服务可用。评分读取工具返回的发送状态，不调用额外模型，也不核验收件箱是否收到。完整说明见 [发邮件示例实验](evaluation/README.md#发邮件示例实验)。

## 6. 预览并运行实验

### SkillsBench

使用上节准备的一任务配置，先预览计划：

```powershell
uv run python -X utf8 -m evaluation `
  --dataset config\datasets\skillsbench-demo.toml `
  --environment config\environments\skillsbench.toml `
  --agent config\agents\openhands.toml `
  --evaluation config\evaluation\skillsbench-demo.toml `
  --name "skillsbench-openhands-demo" --plan
```

预期显示 `tasks: ["3d-scan-calc"]`、`planned_trials: 1`、所选组件和完整输出目录。`--plan` 只读取配置和任务，不创建实验目录、不启动 Docker、不调用模型，也无需模型密钥或运行中的注册中心。

核对计划后，移除末尾的 `--plan` 执行同一条命令。正式运行会构建或启动任务容器、调用付费模型、执行工具和 verifier；trial 结束或失败后会清理对应环境，日志和结果继续保留在宿主目录。

切换其他组合时，保留 dataset 和 evaluation，按下表替换 agent 与 environment，并使用能区分组合的实验名：

| 组合 | `--agent` | `--environment` |
| --- | --- | --- |
| OpenHands + registry 和环境工具 | `config/agents/openhands.toml` | `config/environments/skillsbench.toml` |
| dsh + registry 和环境工具 | `config/agents/dsh.toml` | `config/environments/skillsbench.toml` |
| OpenHands + shell 工具 | `config/agents/openhands-shell-only.toml` | `config/environments/skillsbench-shell-only.toml` |

shell 组合无需启动注册中心。要扩大实验，可以调整配置副本的任务数量、条件和重复次数；trial 数量为任务数 × 条件数 × 重复次数。SkillsBench 支持 `with-skill`、`no-skill`，对照实验需同时核对工具、模型、预算和任务清单。

### 邮件实验

预览命令如下，预期一个 trial：

```powershell
uv run python -X utf8 -m evaluation `
  --dataset config\datasets\email-demo.toml `
  --environment config\environments\email-demo.toml `
  --agent config\agents\openhands-email.toml `
  --evaluation config\evaluation\email-demo.toml `
  --name "email-trace-demo" --plan
```

移除 `--plan` 后会调用付费模型并真实发送邮件。增加重复次数或更换实验名后再次运行，也会再次发信。

### 续跑与重跑

同一组数据集、harness 和模型下，实验名由 `--name` 指定。目录已经存在时，普通运行会报错；需要续跑时，在原命令后加 `--resume`，也可以先用 `--resume --plan` 检查待运行项。

续跑会跳过已有合法评分的完成项和已收尾的 agent 总预算超时项；其余需要重跑的旧记录会移入 `attempts/`，然后创建新的任务环境从头执行。它不会恢复原智能体会话。除支持的任务忽略列表调整外，续跑需保持原实验配置一致；改变模型、条件、重复次数等设置时请使用新实验名。详细规则见 [续跑同名实验](evaluation/README.md#续跑同名实验)。

对邮件等会改变外部状态的任务，如果前一次结果未知，重跑前请先根据日志中的 RPA 任务 ID 核对发送状态。

## 7. 查看 trace、结果和费用

从 `research` 根目录启动查看器：

```powershell
uv run python -m agent_session_analyzer --root .\logs
```

打开 `http://127.0.0.1:8765`，选择实验、任务和 trace。读取已有日志不需要模型密钥或 Docker，也不会重跑任务。如果直接输入 `agent-session-analyzer` 提示找不到命令，可以继续使用上面的模块启动方式。

单次 harness 结果位于 `logs/harness/<harness>/<本次运行目录>/`。实验结果位于：

```text
logs/exp/<数据集>/<harness>/<模型>/<实验名>/
  manifest.json                     实验计划、配置和来源
  results.jsonl                     每个 trial 的当前结果
  summary.json                      评分、完成情况和阶段耗时汇总
  attempts/<trial_id>/<唯一标识>/    重跑前归档的历史尝试
  trials/<trial_id>/
    task/                           本次任务副本
    result.json                     本 trial 的状态、评分和错误
    agent/
      trace/                        标准会话 trace
      events.jsonl                  原生事件
      tools.json                    工具配置
      result.json                   Agent 结果、token 和费用汇总
      tool-calls.jsonl              环境工具执行明细，按实际调用生成
      usage-config.json             费用统计开启时的配置和价格快照
      usage.jsonl                   费用统计开启时的逐次计价明细
    verifier/                       评分产物，按所选评分器生成
```

模型名中的 `/` 等目录特殊字符会替换为 `-`。例如邮件实验位于 `logs/exp/email_demo/openhands/deepseek-deepseek-flash/email-trace-demo/`。失败较早时，部分文件可能尚未生成；`attempts/` 中的历史尝试不会重复计入当前实验汇总。失败不等同于零分：`reward = 0` 可以是有效评估结果，流程异常则另存状态、错误阶段和缺失评分。

费用统计和 trace 分开保存。查看器目前展示的 `费用 USD` 来自 SDK；按团队价格计算的人民币金额在 `agent/result.json` 的 `usage.accounting.estimated_totals.CNY`，逐次 token、缓存命中、实际单价和金额在 `agent/usage.jsonl`。调整价格不会自动重算历史记录。

查看本项目账本汇总时，将下面的 `research` 换成实际的 `RESEARCH_PROJECT_ID`：

```powershell
uv run python -m research_llm logs/model-usage.sqlite3 research
```

它会输出项目及各使用人的调用数、未计价数和按币种汇总的估算金额。独立调用模型时，也可以像使用 LiteLLM 一样使用 `research-llm` 的调用接口；价格、缓存和分时计费说明见 [大模型调用和项目用量](llm/README.md)。

## 8. 后续从哪里修改

主流程是 `evaluation` 读取四份配置和任务，数据集适配器准备任务，再创建环境；公共 `harness.runner.run_agent()` 管理 trace 记录器和费用统计，所选 harness 的 `execute()` 运行原生 SDK 并返回 `AgentResult`，最后由评分器返回 `Score`，调度器保存结果并清理环境。

| 想调整的内容 | 从哪里开始 |
| --- | --- |
| 模型、工具、预算和费用开关 | `config/agents/`；独立入口则修改 `harness/<名称>/main.py` |
| 任务范围、忽略列表和抽样 | `config/datasets/` |
| 任务指令和数据资源 | `data/task/` 及其索引引用的资源目录 |
| 环境类型、注册中心地址和环境预算 | `config/environments/` |
| 条件、重复次数、评分函数和输出位置 | `config/evaluation/` |
| 新数据集的加载、准备、设置和评分 | `datasets/`，接口类型见 `evaluation/contracts.py` |
| 新 harness 的执行和工具适配 | `harness/<名称>/runner.py`、`tools/<名称>/toolset.py` |
| 团队计价规则 | `llm/src/research_llm/prices.toml` |

进一步阅读：[实验配置与扩展接口](evaluation/README.md) · [工具与 harness 接入](harness/openhands/README.md) · [环境和脚本执行](environment/README.md) · [可视化接入](evaluation/VISUALIZATION.md) · [Agent 实验原则](EXPERIMENT_PRINCIPLES.md)。
