# 将数据集转换为框架资源包：以 email-demo 为例

这份指南面向准备接入新数据集的同学。完成接入后，应能通过四份实验配置选择数据集、
环境、agent 和评分方式，先预览任务计划，再运行实验。数据集资源可以整体搬到其他目录，
运行某个数据集时也不需要准备其他数据集的文件。

转换工作分为两部分：整理资源包，以及提供解释这份数据的适配器。框架统一包的入口和
执行接口，任务内容仍可采用原来的 Markdown、JSON、TOML 或其他格式。只增加两个
JSON 文件能完成基本识别；要实际运行，还需要配置与任务格式相匹配的适配器。

下文使用已有的 [email-demo 资源包](../data/email-demo/)，其适配代码位于
[datasets/email_demo.py](email_demo.py)。资源目录叫 `email-demo`，Python 模块叫
`email_demo`；两种名称在配置中各有用途。

## 1. 先明确一条样本如何成为一个任务

整理文件之前，请先回答三个问题：agent 收到什么、在哪里执行、根据什么证据评分。
数据集里的一行记录是否对应一个任务，也由这一步决定。例如多轮对话可以整体作为一个
任务，表格中的每一行则可以分别形成一个分类任务。

email-demo 的对应关系如下：

| 要确定的内容 | email-demo 的选择 |
| --- | --- |
| 任务单位 | 一次按指定收件人和主题发送自我介绍邮件的任务 |
| agent 输入 | 任务指令，以及收件人、主题；通过注册中心发现和读取 rpa-email 技能 |
| 执行环境 | 本机 LocalEnvironment，技能脚本调用外部 RPA 服务 |
| 评分证据 | 工具调用日志中的发送参数、调用次数和服务返回状态 |
| 成功标准 | 只下发一次发送任务，参数匹配，并取得该任务的服务成功结果 |

邮件实验会真实发信。阅读和执行本文的 `--plan` 命令不会发信；只有准备实际运行时，
才核对任务中的收件人和服务配置。其他数据集也应提前写清实际运行会产生哪些操作。

同时区分作答输入和评分专用内容。参考答案、隐藏测试及评分规则可以放在包内，但适配器
只应把实验约定允许的内容交给 agent。打包到同一目录不代表要把整个目录上传到执行环境。

## 2. 按数据集整理文件

目前项目包含两个资源包：

```text
research/
├── data/
│   ├── skillsbench/
│   │   ├── dataset.json
│   │   ├── index.json
│   │   ├── tasks/
│   │   ├── skills/
│   │   ├── environment/
│   │   ├── verifier/
│   │   ├── oracle/
│   │   └── assets/
│   └── email-demo/
│       ├── dataset.json
│       ├── index.json
│       ├── tasks/
│       │   └── send-introduction-email.toml
│       └── skills/
│           ├── rpa_email/
│           │   ├── SKILL.md
│           │   ├── skill.manifest.yaml
│           │   └── scripts/rpa_email.py
│           └── desktop-hello/
│               ├── SKILL.md
│               └── scripts/create_hello.py
├── datasets/
│   ├── package.py
│   ├── skillsbench.py
│   └── email_demo.py
└── config/
    ├── datasets/
    ├── environments/
    ├── agents/
    └── evaluation/
```

只有根目录的 `dataset.json` 和 `index.json` 是包的固定入口。其余目录按实际资源组织，
再由索引和适配器解释。因此邮件包无需新建空的 `environment/`、`verifier/` 或 `oracle/`。
其中 `desktop-hello` 供本地环境示例使用，并不是第二个邮件评测任务。

转换自己的数据集时，先复制需要的题目、输入素材、技能和评分资源，保护原始数据。
已有 Dockerfile、脚本和技能内部可能有相对路径，尽量完整保留其内部目录结构。
SkillsBench 还通过 `extra_paths` 引用额外文件，转换类似数据集时应沿引用收集资源。

实验日志、模型密钥、本机服务地址和运行生成的文件放在包外。原始数据来源及已有许可证
随包保留，便于其他同学确认资源版本和使用条件。

## 3. 写 dataset.json，描述整份包

email-demo 的必需部分是：

```json
{
  "schema_version": 1,
  "id": "email-demo",
  "version": "r1",
  "task_format": "research-email-demo-v1"
}
```

| 字段 | 含义和当前约束 |
| --- | --- |
| `schema_version` | 包描述格式版本，当前必须是整数 `1` |
| `id` | 数据集标识，以字母或数字开头，后续可用字母、数字、`_`、`-`、`.` |
| `version` | 这批资源的版本，必须是非空且没有首尾空白的字符串 |
| `task_format` | 任务格式标识，必须与所选适配器读取时要求的字符串一致 |

实际包还用可选的 `provenance` 记录了抽取来源。固定索引文件名是 `index.json`，不需要
再声明索引路径。两个 JSON 文件都以 UTF-8 保存，`schema_version` 各自都需要填写。

如果新数据集完全采用已有任务格式，可以复用该 `task_format` 和适配器；如果任务字段
或含义不同，就使用新的格式名，并实现相应读取逻辑。框架不会根据 `task_format` 自动
查找、安装或导入适配器，函数入口仍由实验配置显式指定。

题目、素材、技能或评分资源发生变化时更新 `version`；任务格式的解释规则发生不兼容
变化时再调整 `task_format`。不要把模型名称、抽样种子或重复次数写成资源版本。

## 4. 写 index.json，为任务建立索引

email-demo 的完整索引是：

```json
{
  "schema_version": 1,
  "tasks": [
    {
      "id": "send-introduction-email",
      "task_file": "tasks/send-introduction-email.toml",
      "skills_dir": "skills/rpa_email"
    }
  ]
}
```

公共规则是：`tasks` 必须为非空列表，每项有唯一 `id`，任务 ID 的字符规则与数据集 ID
相同。如果提供可选字段 `task_count`，它必须是与任务数相同的整数。应保持原数据集的
任务顺序，避免转换本身改变实验的抽样结果。

`task_file`、`skills_dir` 等字段由具体适配器定义，公共模块不会自动根据名字复制资源。
例如另一个适配器可以接受 `input_file` 和 `answer_file`，但必须自行定义如何读取它们。

包内资源路径统一遵守以下规则：

- 相对于资源包根目录；两个入口文件都在根目录，所以也是相对于 `index.json`。
- 使用 `/`，例如 `tasks/example.json`，Windows 上也使用这种写法。
- 不使用绝对路径、`..`、`.` 路径分量、反斜杠或符号链接、junction。
- 所需文件都放入包内，不能引用另一个数据集或转换者电脑上的目录。

适配器应复用 [package.py](package.py) 的路径解析。例如下面是在邮件 loader 中读取
任务文件的关键片段，`index` 是框架传入的索引路径：

```python
package = read_package_index(index, task_format="research-email-demo-v1")
row = package.index["tasks"][0]
task_file = resource_path(package.root, row["task_file"])
```

这里的函数来自 `datasets.package`。`read_package_index()` 校验包的基本结构和声明格式，
`resource_path()` 检查包内边界与资源是否存在。只检查路径、暂不要求资源已下载时，
显式传入 `must_exist=False`。目录或文件的类型、必需字段等业务约束仍由适配器检查。

## 5. 保存任务内容和技能

邮件任务保存在 [send-introduction-email.toml](../data/email-demo/tasks/send-introduction-email.toml)。
下面仅摘出字段结构，指令以原文件完整正文为准：

```toml
[task]
id = "send-introduction-email"
receiver = "lornd@qq.com"
subject = "Agent 实验：自我介绍"
timeout_sec = 900
instruction = """
请使用 rpa-email 技能，按给定收件人和主题发送一封自我介绍邮件。
"""
```

loader 会检查索引 ID 与任务文件 ID 一致、参数和指令非空、时限为有限正数，再将收件人
与主题追加到实际任务消息中。完整指令还规定了工具、发送次数以及结果未知时的查询方式。

其他数据集可以继续保留自己的原始格式。JSONL 可由 loader 按记录读取，题目与答案可以
分文件保存；不必先转换成邮件 TOML。关键是明确哪些字段进入 `Task.instruction`，哪些
字段留给评分器，以及原始样本 ID 如何稳定映射到任务 ID。

技能应连同脚本、模板等资源完整放入 `skills/`。目录名和技能名不一定相同：本例目录
是 `rpa_email`，`SKILL.md` 中的名称以及工具调用使用的名称是 `rpa-email`。
仅有普通输入文件的任务可以不使用技能，但要选择不依赖注册中心的适配器和工具配置。
现有 email-demo 适配器始终要求 rpa-email，也只支持 `with-skill` 条件。

## 6. 用适配器接上执行流程

框架按下面的顺序调用组件：

```text
dataset.package → 读取 dataset.json 和 index.json
  → dataset.loader(index, task_ids) → 排除和抽样
  → dataset.prepare(task, destination, condition)
  → environment.factory(task_dir, output_dir=..., **options) → start()
  → dataset.setup(context)，若配置了该函数
  → agent.runner → evaluator.function(context, agent_result)
  → environment.destroy() → 保存结果与汇总
```

数据类型在 [evaluation/contracts.py](../evaluation/contracts.py) 中定义。
email-demo 将下面五项能力集中在 `datasets/email_demo.py`，普通数据集也可从一个文件开始：

| 函数接口 | 返回值与职责 | email-demo 如何处理 |
| --- | --- | --- |
| `load_tasks(index: Path, task_ids: list[str] \| None = None)` | 返回 `list[Task]`，加载并校验任务，不启动环境或调用模型 | 读取包、索引和任务 TOML，构造指令与来源记录 |
| `prepare_task(task: Task, destination: Path, condition: str)` | 返回 `PreparedTask`，检查选中任务的资源并准备副本 | 复核哈希，复制任务和 rpa-email 技能 |
| `create_environment(task_dir: Path, *, output_dir: Path, **options)` | 返回环境对象，创建资源的执行环境 | 返回工作目录为任务副本的 `LocalEnvironment` |
| `async setup_environment(context: TrialContext)` | 可选，无返回数据，在环境启动后准备任务 | 检查注册中心仅允许 rpa-email，并读取该技能 |
| `async evaluate(context: TrialContext, agent: AgentResult)` | 返回 `Score`，根据证据评分 | 解析工具日志，核对参数和服务成功结果 |

`Task` 需要 `id`、`instruction` 和 `source`，可带 `metadata` 和 `agent_timeout_sec`。
`source` 保存索引位置、任务对应索引项及哈希等追溯信息；邮件评分所需的收件人、主题
放在 `metadata`。这些字段不会由框架自动全部拼进模型消息，但任务记录会写入实验产物，
适配器和评分器应按实验约定使用其中内容。

`PreparedTask` 至少提供 `task_dir`，可带 `skills_dir` 和 `skill_catalog`。只填写目录
并不会自动上传技能；需要适配器在 setup 中处理，或像邮件示例一样经注册中心加载。
邮件 prepare 将技能保存为 trial 的副本，但实际执行仍读取注册中心提供的资源；它的
`skill_catalog` 保持为空，agent 按任务指令搜索技能。

环境对象必须符合 [Environment 接口](../environment/base.py)，由调度器调用 start 和
destroy，适配器和评分函数使用传入的环境。只运行本机任务时可复用 `LocalEnvironment`；
需要 SkillsBench 的 Docker/BenchFlow 任务格式时可参考 [skillsbench.py](skillsbench.py)。
普通 JSON 任务仍需编写自己的资源准备逻辑，不能直接交给 SkillsBench 的准备函数。

评分结果的形式是：

```python
return Score(
    reward=float(passed),
    metrics={"reward_basis": "rpa_service_success"},
)
```

这是评分函数中的返回片段，`passed` 应由实际证据计算。主评分 `reward` 必须为 0～1 的
有限数值，越高越好；`metrics` 保存能序列化为 JSON 的其他指标。邮件评分依据服务返回
的成功状态，不以模型自述为准，也不核验收件箱。分类任务则可以读取预测标签与参考答案，
问答任务可以比较回答与评分规则，文件任务可以在环境销毁前检查或下载实际产物。

缺少必需评分证据、格式损坏或服务执行失败时，要按含义明确报错或记录评分，不能统一
用零分掩盖故障。`reward=0` 本身是合法评分；抛出异常会由框架保存为对应阶段的错误。

接入多任务数据集时，需要将邮件 loader 中“必须一个任务”的检查替换为遍历和选择逻辑。
传入 `task_ids` 时按其顺序返回任务，未知、重复或非法 ID 明确失败；未传时按索引顺序
返回。排除列表和随机抽样由公共评估流程负责，不必在适配器里再做一次。

## 7. 配置四个组件

资源包里保存任务本身需要的配置，实验选择留在 `config/`。以下是现有邮件示例的接法。

数据集配置 [config/datasets/email-demo.toml](../config/datasets/email-demo.toml)：

```toml
[dataset]
package = "../../data/email-demo"
loader = "datasets.email_demo:load_tasks"
prepare = "datasets.email_demo:prepare_task"
setup = "datasets.email_demo:setup_environment"
```

`package` 相对这份 TOML 文件解析。框架自动派生包内索引路径，并将它传给 loader，
无需在配置中再填 `index`。新包可放到仓库外，只要指定实际包目录。
`task_ids`、`exclude_task_ids`、`sample_size` 和 `seed` 是可选的任务选择配置；邮件包只有
一个任务，当前不设置抽样。如果设置 `seed`，还必须设置 `sample_size`，数量不能超过
排除后的候选任务数。

环境配置 [config/environments/email-demo.toml](../config/environments/email-demo.toml)：

```toml
[environment]
factory = "datasets.email_demo:create_environment"
registry_url = "http://127.0.0.1:5000"
allowed_skills = ["rpa-email"]
registry_timeout = 30
default_timeout = 360
```

环境地址和执行预算属于部署及实验设置。新数据集换了执行方式时，选择相应环境工厂；
没有 registry 工具时，也不应沿用邮件 setup 对 rpa-email 的强制检查。

agent 配置使用 [config/agents/openhands-email.toml](../config/agents/openhands-email.toml)。
其中选择模型、harness、工具、记录器和预算。可复用现有配置，但需要确认工具能够读取
新任务的输入并完成操作。例如当前邮件工具集合没有通用终端工具，文件处理任务需选择
具备对应能力的配置。

评估配置 [config/evaluation/email-demo.toml](../config/evaluation/email-demo.toml)：

```toml
output_dir = "../../logs/exp"
conditions = ["with-skill"]
repeats = 1
pass_threshold = 1.0

[evaluator]
function = "datasets.email_demo:evaluate"
```

`passed` 根据 `reward >= pass_threshold` 计算。修改单次评分方式时更换评分函数，新增
指标放入 `Score.metrics`；目前额外指标会逐 trial 保存，不会自动求平均，汇总逻辑位于
[evaluation/artifacts.py](../evaluation/artifacts.py)。`[evaluator]` 当前只接受 `function`，
尚不支持自定义参数。`conditions` 必须由适配器实际支持，不能仅在配置中添加 `no-skill`
就认为完成了无技能对照。

四份配置的函数引用使用 `module:function`，对应代码需能在项目 Python 环境中导入。
例如新增 `datasets/my_dataset.py` 后，配置中写 `datasets.my_dataset:load_tasks`。
先检查项目现有依赖，新适配器所需依赖也应按项目方式声明。

## 8. 先预览，再验证一次实际执行

前提是项目的 Python 3.12+ 虚拟环境和依赖已经准备好。以下 PowerShell 命令都在
`D:\Projects\research` 根目录执行；其他电脑请先切换到自己的项目目录。

第一步只检查两个入口和公共结构，不导入数据集适配器：

```powershell
.\.venv\Scripts\python.exe -X utf8 -c "from datasets.package import read_package; p = read_package('data/email-demo'); print(p.identity()); print([t['id'] for t in p.index['tasks']])"
```

预期显示 ID 为 `email-demo`、版本为 `r1` 的包身份，以及
`['send-introduction-email']`。这一步不能证明任务文件、技能或运行服务齐全。

第二步通过四份配置预览实验：

```powershell
.\.venv\Scripts\python.exe -X utf8 -m evaluation `
  --dataset config\datasets\email-demo.toml `
  --environment config\environments\email-demo.toml `
  --agent config\agents\openhands-email.toml `
  --evaluation config\evaluation\email-demo.toml `
  --name "email-package-guide" --plan
```

预期输出任务 `send-introduction-email`、`planned_trials: 1`、`dataset_package` 和
`dataset_identity`。预览读取入口和题目，不执行 prepare、setup 或评分，不创建实验目录、
调用模型、启动 Docker、访问注册中心或发送邮件。因此它也不会验证外部服务是否可用。

第三步由实际运行者准备环境。邮件例子需让注册中心扫描这个包的技能：

```powershell
$env:SKILLS_DIR = (Resolve-Path .\data\email-demo\skills).Path
uv run --project ..\skill_registry skill-registry
```

这条命令在另一个终端启动服务，可能同步相邻 `skill_registry` 项目的依赖。
切换包后重新启动服务，或使用独立服务并调整 `registry_url`。注册中心的正文和资源
应与所选包保持一致；仅设置 `skills_dir` 不会自动向服务注册技能，当前 setup 也不做
全部远端资源的逐字节一致性校验。

实际运行还需要模型密钥；邮件示例默认开启费用统计，需要相应使用人和项目配置，详见
[项目入门说明](../README.md) 及 [邮件实验说明](../evaluation/README.md#发邮件示例实验)。
核对收件人、主题、RPA 服务以及发送授权后，移除第二步命令的 `--plan` 才会真正运行，
它会调用付费模型并真实发送邮件。增加重复次数或换名重跑也会再次发送。

检查 trial 的 `task-info.json`、任务和技能副本、`agent/tool-calls.jsonl`、
`verifier/result.json` 和 `result.json`，确认输入、工具证据及评分能对应起来。
若任务明确失败，结果应记录错误阶段；若正常完成但未满足成功标准，可以合法记为零分。

最后，把完整包复制到另一目录，在数据集配置副本中只修改 `package`，使用新实验名
重新预览。这能检查题目和索引的可移动性；还需通过一次实际准备或运行核对大型素材、
脚本和环境资源的路径。运行期间保持包内容稳定，入口和题目哈希会复核，但框架当前
不会对所有二进制素材执行全量内容校验。

## 9. 转换其他数据集时，按差异修改

| 原数据集类型 | 包里怎样组织 | 适配器需要处理什么 |
| --- | --- | --- |
| 分类或问答记录 | 保留 JSON/JSONL，索引列出稳定任务 ID；答案可单独保存 | loader 映射样本与题目，评分器读取预测与答案，避免答案进入作答消息 |
| 文档、表格或视频处理 | 题目放 tasks，素材按任务放 assets 或 environment | prepare 只复制或上传选中任务的输入；评分器检查实际产物 |
| 现成容器任务 | 保留 Dockerfile、依赖和构建上下文 | 准备原生任务目录，选择环境工厂，评分前保留容器与输出 |
| 自定义工具或外部服务任务 | 保留任务参数和技能资源 | 明确工具配置、服务部署、任务副作用，以及能够核验的评分证据 |

复用 email-demo 时，可以沿用包读取、路径解析、来源哈希、任务副本和函数组合方式。
收件人、主题、rpa-email 名称、RPA 日志解析和单任务限制都属于邮件业务，需要按新数据集
替换。包不含技能时，不必强行创建技能目录；只要相应适配器、agent 工具和条件配置一致。

接入完成后，请交付资源目录、适配代码、四份实验配置，以及一条带 `--plan` 的最小命令。
记录资源来源、版本、依赖、预期任务数和评分含义，让下一位同学能沿同一流程复现。
分发时保留两个入口文件的根目录层级，接收者解压后将 `package` 指向同时包含它们的目录。
当前框架读取本地目录，不会自动解压 ZIP、下载素材或安装缺失适配器。
