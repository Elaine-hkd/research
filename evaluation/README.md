# 标准评估流程

实验轨迹展示、condition 对比及新 harness 的适配方法见 [可视化接入指南](VISUALIZATION.md)。

评测流程与 harness、数据集通过普通函数组合，不需要给每个 harness 新建 evaluation 文件。

| 模块 | 职责 |
| --- | --- |
| `evaluation/runner.py` | 调度 trial、创建和销毁环境、调用组件、保存结果和汇总 |
| `evaluation/dataset.py` | 共用任务加载入口，按数据集配置随机采样 |
| `evaluation/experiment.py` | 恢复原实验计划、判断跳过项、归档旧尝试并独占实验目录 |
| `datasets/skillsbench.py` | 读取和准备 SkillsBench、配置其 Docker 环境、注入技能、调用原生 verifier |
| `harness/runner.py` | 公共 `run_agent`，检查公共参数，创建和关闭配置的 recorder，调用执行函数 |
| `harness/<名称>/runner.py` | `execute` 绑定工具、运行 SDK、处理中断和收尾，返回 `AgentResult` |
| `tools/loader.py` 与 `tools/<名称>/toolset.py` | 检查工具名称，加载所选 harness 的已有工具实现 |
| `environment/` | 环境生命周期、命令、文件传输等底层操作 |

模型参数和 `[agent]` 的原生运行选项由所选 harness 解释；评测层不填入特定 SDK 的默认值。

在 `E:\lornd\research` 使用现有 Python 3.12+ 环境。默认配置只选 `3d-scan-calc`，
仅执行一次 `with-skill`。本次实验不运行 `no-skill`。

```powershell
Set-Location E:\lornd\research
.\.venv\Scripts\python.exe -X utf8 -m evaluation --config config\skillsbench-openhands.toml --plan
```

预期打印选定任务、模型、`conditions: ["with-skill"]`、各组件引用和 `planned_trials: 1`。
`--plan` 只读取配置和任务索引，
不导入 OpenHands / BenchFlow SDK，不需要 API key，不调用模型或启动 Docker。

实际评估需要现有依赖、Docker CLI、Compose v2、运行中的 Linux Docker 引擎，
以及任务构建、verifier 安装依赖所需的网络。任务镜像须提供 Bash；当前 OpenHands runner
使用支持工具调用的文本模型。宿主读取项目 `.env`（已有环境变量优先），从配置中的
`model.api_key_env` 获取模型密钥，默认是 `DEEPSEEK_API_KEY`。

默认工具名称与 `harness/openhands/main.py` 一致，复用原有四个注册中心工具接口。
评测通过 `[environment].registry_url` 设置注册中心地址，默认 `http://127.0.0.1:5000`；
搜索、加载和执行共用环境自有的注册中心客户端，不依赖宿主的 EXPOSED_SKILLS。
注册中心需预先注册当前任务 skill_catalog 中的技能，并保持技能正文和资源与数据集一致；
setup 将访问范围限制为这份清单，不会上传数据集技能到远端注册中心。
前三个工具仍读取注册中心；execute_resource 下载技能资源后，在当前 trial 容器执行。
镜像需提供 python3 和技能依赖；现有下载协议只支持 UTF-8 文本资源。

```powershell
.\.venv\Scripts\python.exe -X utf8 -m evaluation --config config\skillsbench-openhands.toml
```

构建进度、OpenHands 事件类型和阶段切换会持续打印。每个 trial 使用新的 Compose 项目；
结束或失败后清理容器和该项目的卷，保留镜像缓存与宿主实验文件。Ctrl+C 会记录当前 trial
为 cancelled 并尝试清理；输出目录中的已有结果保留。正常退出码为 0，流程错误为 1，
Ctrl+C 为 130。任务得分为 0 是有效评估结果，不因此返回流程错误。

## 修改实验

配置文件是 `config/skillsbench-openhands.toml`，其中路径相对配置文件所在目录解析。

| 要改变的内容 | 配置项 |
| --- | --- |
| 本次实验名称 | 顶层 `name`，或单次命令的 `--name` |
| 模型及服务地址 | `[model] model`、`base_url`、`api_key_env` |
| 模型采样与输出预算 | `[model] temperature`、`max_output_tokens`、`reasoning_effort` 等 OpenHands LLM 参数 |
| 数据集及任务集合 | `[dataset] index`、`task_ids`、`loader`、`prepare` |
| 随机采样数量及种子 | `[dataset] sample_size`、`seed`，可省略 |
| 环境启动后的数据集设置 | `[dataset] setup`，可省略 |
| harness | `[agent] runner`，直接引用 `harness.<名称>.runner:execute` |
| 记录器 | `[agent] recorder`，例如 `agent_session_analyzer:OpenHandsRecorder`；省略则不录制标准 trace |
| 技能条件 | 本次 `conditions = ["with-skill"]` |
| 每个任务、每组重复次数 | `repeats` |
| 成功判定阈值 | `pass_threshold`，默认 reward >= 1.0 |
| agent 的循环和命令预算 | `[agent] max_iterations`、`timeout_sec`、`tool_timeout_sec` |
| 暴露给模型的工具集合 | `[agent] tools`，每项为跨 harness 使用的工具名称 |
| 评分方式 | `[evaluator] function` |
| 环境构造与参数 | `[environment] factory` 及其余构造参数 |
| 容器工具读取的注册中心 | `[environment] registry_url` |

每次运行可使用同一份配置并指定实验名，支持中文和内部空格：

```powershell
.\.venv\Scripts\python.exe -X utf8 -m evaluation --config config\skillsbench-openhands.toml --name "清华源耗时对比" --plan
```

`--plan` 输出 `name` 与完整的 `experiment_dir`，只预览，不创建目录、启动 Docker 或调用模型。
移除 `--plan` 才开始实际实验，可能构建镜像并产生模型调用费用。
没有传 `--name` 时使用 TOML 顶层的 `name`；它现在表示实验目录最后一级的名称。
相同 benchmark/harness/model 下同名目录已存在时，默认报错；加 `--resume` 可续跑，
不会自动增加时间后缀。
实验名不能包含路径分隔符、Windows 非法文件名字符、首尾空格或末尾句点；拒绝 `.`、`..`
和 Windows 保留设备名。原有配置中的默认 name 保留，需要另一次实验时更换名称。

## 续跑同名实验

使用原配置和实验名称，加 `--resume`。先预览需要重跑的试验：

```powershell
Set-Location E:\lornd\research
.\.venv\Scripts\python.exe -X utf8 -m evaluation --config config\skillsbench-openhands.toml --name "10" --resume --plan
```

`skipped_trials` 是已完成数量，`pending_trials` 和 `pending_trial_ids` 是本次待运行数量与清单。
预览只读文件，不需要模型密钥，不修改实验结果或创建锁文件，不调用模型或启动 Docker。
确认清单后移除 `--plan`：

```powershell
.\.venv\Scripts\python.exe -X utf8 -m evaluation --config config\skillsbench-openhands.toml --name "10" --resume
```

实际续跑需要原实验的依赖、模型凭据、注册中心和 Docker 环境，会对待运行项重新调用模型，
可能产生费用。Python 调用使用 `run_evaluation(config, resume=True)`，与命令行共用恢复逻辑。

- 跳过 `status=completed`、无流程错误且已有合法评分的试验，包括 `reward=0` 和 `passed=false`。
  已完成试验的目录和 trace 不会重写；没有配置 recorder 也按相同规则判断。
- 跳过已收尾的 agent 总时间预算耗尽项，保留其错误、trace 和缺失评分，不归档、不重新调用模型。
  依据 `agent.finish_reason=timeout` 或 agent 阶段明确的预算耗尽异常判断；
  环境构建超时、工具或 API 的普通超时不作为跳过依据。旧日志无需迁移。
  `--resume --plan` 的 `skipped_agent_timeout_trial_ids` 列出这些 trial。
- 重跑 `error`、`cancelled`、中断后残留的 `running`，以及没有结果的试验。
  其中排除上述 agent 超时项。仅存在部分 trace 不代表流程完成。评分或清理报错的试验也会从头重跑。
- 按原 `manifest.json` 的实际任务清单续跑，不重新随机抽样，保留实验 ID、任务顺序、条件和重复次数。
  当前配置必须与原配置快照一致；曾通过 `--task` 指定任务时，续跑也需提供原参数。
  显式传入 `tasks` 或组件函数时也须与原记录一致。要改变实验设置，请使用新实验名称。
- 重跑前把该试验的整份旧目录移至 `attempts/<trial_id>/<唯一标识>/`，保留旧 trace、错误及产物，
  然后在原 `trials/<trial_id>/` 路径创建新环境和新记录。不会恢复原智能体会话。
- 各试验的 `result.json` 是恢复依据；`results.jsonl` 原子更新，每个试验只保留当前一条记录。
  `summary.json` 统计保留项与最新重跑结果，不重复累计旧失败尝试的评分、次数或耗时。
  原始尝试的耗时仍保存在归档中。若所有试验均应跳过，不加载执行后端或要求模型密钥。
  保留的 agent 超时仍是失败项，实验不会因此变成 completed，命令仍返回非零退出码。

同名实验由操作系统文件锁保护，避免两个进程同时重跑；进程结束或被强制终止后锁会释放，
实验目录旁的 `.实验名称.lock` 文件可以保留。强制终止可能遗留 Docker 容器，续跑使用独立新容器，
不接管旧容器；这类遗留资源仍需另行清理。`--resume` 要求原实验存在，不会隐式创建新实验。

## 选择任务与采样

删除 `dataset.task_ids` 一行选择完整索引。也可单次覆盖任务集合：

```powershell
.\.venv\Scripts\python.exe -X utf8 -m evaluation --config config\skillsbench-openhands.toml --task 3d-scan-calc citation-check
```

要从完整索引随机抽取 10 个任务，删除 `[dataset]` 中的 `task_ids` 行，并设置：

```toml
[dataset]
index = "../data/task/skillsbench.json"
sample_size = 10
seed = 42
loader = "datasets.skillsbench:load_tasks"
prepare = "datasets.skillsbench:prepare_task"
setup = "datasets.skillsbench:setup_environment"
```

`sample_size` 必须是正整数且不超过候选任务数；无放回采样不会重复抽取任务。
保留 `task_ids` 或使用 `--task` 时，只从该列表中采样。`seed` 是可选整数，支持 0；
相同候选任务及顺序、相同数量和种子得到相同任务及执行顺序。省略 `seed` 时每次随机抽样；
只设置 `seed` 而没有 `sample_size` 会报错。不配置这两项时，原有选取和顺序保持不变。

`--plan` 输出实际采样的任务列表、数量和种子，不调用模型或启动 Docker。
同次评测的所有 condition 和 repeat 共用一次采样结果；`manifest.json` 保存配置及实际任务清单。
命令行和 `run_evaluation(config)` 共用此逻辑；显式传入 `tasks=...` 时按该列表直接评测，不再采样。
数据集 loader 仍使用原有 `(index, task_ids)` 接口，更换数据集无需额外实现采样参数。

没有指定 `agent.timeout_sec` 时，使用各任务 frontmatter 中的 `agent.timeout_sec`；
verifier 超时、服务、环境变量和 hardening 由任务原配置及 BenchFlow 处理。
模型密钥只填环境变量名，不写进 TOML。其余 `[model]` 参数直接交给 OpenHands `LLM`；
模型名使用该 SDK / LiteLLM 的 provider/model 格式。

## 对比工具组合

默认使用注册中心工具。原 shell 配置继续保留，其任务、模型、with-skill 条件、重复次数
和预算相同，便于需要时另行比较：

- `config/skillsbench-shell-only.toml`：shell + 本地 think/finish。
- `config/skillsbench-openhands.toml`：与 main.py 相同的四个 registry 工具 + 本地 think/finish。

在项目根目录先看计划，再分别执行：

```powershell
.\.venv\Scripts\python.exe -X utf8 -m evaluation --config config\skillsbench-shell-only.toml --plan
.\.venv\Scripts\python.exe -X utf8 -m evaluation --config config\skillsbench-openhands.toml --plan
.\.venv\Scripts\python.exe -X utf8 -m evaluation --config config\skillsbench-shell-only.toml
.\.venv\Scripts\python.exe -X utf8 -m evaluation --config config\skillsbench-openhands.toml
```

plan 输出应分别列出 3 和 6 个工具名称，各计划 1 个 trial。plan 通过公共 validate_tools
检查名称格式和重复项，不导入工具、不验证原生实现、注册中心连接或容器依赖。
增减工具直接编辑各自的 `[agent].tools`，新增实现方法见
[公共工具加载说明](../harness/openhands/README.md)。

工具名称由公共 tools.loader.load_tools 交给所选 harness 的适配代码；配置不再填写
OpenHands 类路径。当前四个 registry 工具在 OpenHands、dsh 都有实现，容器 shell、文件
工具及本地 think/finish 只有 OpenHands 实现，给 dsh 选择这些名称会明确报错。
evaluation/runner.py 从配置中取出工具名称，显式传给 harness 的 tools 参数，
其余迭代和超时预算沿用原接口；harness 不通过 settings 读取工具。

按相同 task_id、condition、repeat 比较两个实验的 reward/passed、duration_sec、
agent.usage 的 token/cost 和 tool_calls_by_name。summary 仍只在各实验内按技能条件
汇总，不把两个实验自动合并。单次结果会受模型采样影响，需要重复时同步修改两份 repeats。
两份配置都在 trial 容器执行，但操作能力不同：shell 可以执行任意命令，execute_resource
只能运行当前任务允许的注册中心技能资源，不能把得分差异只归因于工具接口。
后续修改其他实验参数时需保持两份配置一致。

## 执行与评分

```text
dataset.loader → dataset.prepare → environment.factory → environment.start
               → dataset.setup（可选）→ agent.runner → evaluator.function
               → environment.destroy → 保存结果
```

当前配置通过 `datasets.skillsbench` 提供数据集相关函数。其 `prepare_task` 复用现有
`materialize_skillsbench_task`，把分离的索引还原到 trial/task；
`verifiler_dir` 还原为 `verifier/`，只在副本里把旧 `environment:` 转成 `sandbox:`。
数据源、oracle 和 verifier 原件不修改。实验保存索引与任务文本的 SHA-256、依赖版本和
配置快照；环境输入和技能资源保留在各 trial 副本中。运行期间应保持源数据稳定，当前不对
所有二进制输入做全量内容快照校验。

任务准备通过 BenchFlow 的 `strip_task_bundled_skills` 清除副本的 `environment/skills`
及对应 Dockerfile COPY 行。`with-skill` 从索引的 `skills_dir` 复制完整资源并上传到容器
`/skills`，向模型提供名称、描述及 SKILL.md 路径；注册中心配置通过 load_skill 读取正文，
shell 配置也可读取容器文件。当前只运行 with-skill；技能目录只是可用能力清单，
不代表模型实际使用过技能。

Agent 使用 OpenHands SDK 默认的 system prompt；runner 不覆盖提示词、替换模板或追加
system message 后缀。任务正文及 with-skill 的技能清单作为用户消息输入。
评测流程将 `[agent].tools` 显式传给 OpenHands 的 tools 参数，再由公共 loader
调用 OpenHands 适配代码加载本地实现，关闭 SDK 默认工具；think/finish 也由本项目
实现。默认配置提供 search_skills、load_skill、load_resource、execute_resource、
think 和 finish，加载的仍是 tools/openhands 中原有的注册中心工具。
注册中心读取工具通过 HTTP 访问服务端；执行工具通过当前环境的 executor 下载资源、
上传 worker 并在当前 trial 容器执行。资源执行共用原容器通道的预算、中断和输出限制。
未设置工作目录时遵循镜像 WORKDIR，临时技能目录在执行后清理，产物应写到任务路径。
容器中注入的 /skills 与注册中心仍是两个来源，需要事先准备相同技能。
独立 main.py 没有传 environment 时仍使用原服务端 /execute_resource。
可选的 shell/文件工具通过原事件循环调用当前 env.execute，在任务容器内操作；
命令默认目录遵循镜像 WORKDIR，每次新建 Bash 进程。
完整容器命令和资源执行结果写磁盘，仅传给模型的输出会截断；注册中心读取响应沿用原结构。
配置与实际工具名称不符时会在首次模型请求前报错。

OpenHands 执行逻辑位于 `harness/openhands/runner.py`，容器命令工具位于
`tools/openhands/terminal.py`，文件工具位于 `file_editor.py`，公共工具加载在
`tools/loader.py`，SDK 注册留在 `tools/openhands/toolset.py`。通用评测流程将 `TrialContext`
展开成任务正文、环境、输出目录、技能清单和 metadata，调用公共 `harness.runner.run_agent`，
由它加载配置的执行函数与 recorder。模型请求、工具绑定与安全中断仍在具体 harness 内。
harness 不接收 `TrialContext`、不导入 evaluation，可以单独用于执行任务。
公共入口将配置的 recorder 创建在 `output_dir/trace`，并负责关闭；
调用方显式提供的 recorder 实例仍由调用方关闭。省略 recorder 时直接运行 SDK，不录制标准 trace，
原生 events/result 仍按各 harness 的输出逻辑保存。recorder 必须支持所选 harness 的原生录制接口。
公共入口校验整体超时和名称列表；SDK 原生字段复用 SDK 校验，自定义约束在 execute 内少量检查，
没有增加独立校验函数或参数规则表。
JSON 写入与脱敏统一使用 `utils/json_io.py`。

`datasets.skillsbench:evaluate` 调用 `env.verify()`，在容器销毁前执行原生 BenchFlow hardening 和
`Verifier.verify()`；沿用原任务 test.sh、reward.txt / reward.json、聚合及校验规则。
reward 缺失、格式无效、API 或环境失败时保留 `reward: null` 和错误阶段；不能把这些情况
冒充为模型得分 0。命令超时或整体 agent 超时会作为流程错误停止当前 trial。
迭代上限或 stuck 时，如果命令通道仍可用，会对已经产生的产物执行 verifier，并保留结束原因。

## 结果目录

```text
logs/evaluation/<benchmark>/<harness>/<model>/<实验名称>/
  manifest.json                 配置、任务来源、文本哈希、依赖版本、计划数量
  results.jsonl                 每个 trial 的当前结果，结束时原子更新，不重复追加
  summary.json                  每个 trial 结束更新评分及阶段耗时汇总
  attempts/<trial_id>/<标识>/   重跑前归档的旧试验目录，保留旧 trace、错误和产物
  trials/<任务>__<条件>__<次数>/
    task/                       还原后的环境、verifier、oracle 副本
    skills/                     with-skill 的完整技能副本
    task-info.json              来源与暴露的技能清单
    agent/tools.json             实际工具名称、实现类和基础 function schema
    result.json                 各阶段结束更新耗时，trial 结束补齐评分、用量和错误
    agent/events.jsonl          OpenHands 原生事件
    agent/trace/*.jsonl         harness-trace/1，含模型尝试与工具记录
    agent/tool-calls.jsonl      容器命令与未截断的 stdout/stderr
    agent/result.json           最终回答、结束原因与用量（缺失用量为 null）
    verifier/                   原生 reward、验证输出及测试报告
    artifacts/                  BenchFlow 映射的 /logs/artifacts
    error.txt                   失败时的异常链
```

目录由 `evaluation.config.experiment_directory(config)` 统一计算，命令行预览和实际运行使用
同一份实现。benchmark 取 `dataset.loader` 模块名的最后一段（`datasets.skillsbench` →
`skillsbench`）；harness 取 `agent.runner` 模块去掉末尾 `.runner` 后的最后一段
（`harness.openhands.runner` → `openhands`）。model 使用配置中的完整 `model.model`，
其中 `/`、`:` 等 Windows 非法文件名字符替换成 `-`；原始模型名仍完整保留在配置快照和结果中。
上面的命令对应：

```text
logs/evaluation/skillsbench/openhands/deepseek-deepseek-flash/清华源耗时对比/
```

`manifest.json` 另存独立的 `experiment_id` 和用户指定的 `experiment_name`，recorder metadata
使用相同信息，因此不同模型或 harness 下相同的实验名仍有不同 ID。
`started_at` / `finished_at` 继续使用带时区偏移的本地 ISO 时间；已有实验目录和记录不迁移。
可视化查看器递归扫描 `manifest.json`，不依赖时间格式或固定目录深度，因此无需修改读取逻辑；
选择 `logs/evaluation` 后刷新即可同时读取新旧目录。页面仍按“实验 → 任务 → repeat”浏览，
并提供 harness/model 标签与筛选；同名实验用完整目录区分。

JSON 产物使用 `schema_version = 1`。`events.jsonl` 保留 OpenHands 原生事件；
`agent/trace/` 使用 `harness-trace/1`。trial 结果中的 `trace_path` 相对该 trial 目录，
指向标准 trace；旧实验没有此字段时，查看器读取 `agent/events.jsonl`。
`status=completed` 表示流程完成，是否完成任务看 `passed` 与 `reward`；cleanup 失败会将
status 标成 error，已取得的有效 reward 仍保留。`failure_type` 区分环境构建/准备失败
（`environment_failure`）、agent 运行失败（`agent_failure`）、agent 运行超时（`agent_timeout`）、
评分失败（`evaluation_failure`）和清理失败（`cleanup_failure`），其余失败保留 `failure`。
多个错误以首次流程失败为主，清理错误不覆盖此前的失败；summary 中的 `failure_types` 按类型计数。
旧结果按原有字段即时分类，无需改写。`errors[].stage` 区分 prepare、environment、
setup、agent、evaluate、cleanup。`setup` 是可选的数据集环境设置阶段，包括此处的技能上传。

每个 trial 的 `stage_durations_sec` 分别记录以下阶段的秒数，使用单调时钟测量，包含异步等待：

| 字段 | 计时范围 |
| --- | --- |
| `prepare` | `dataset.prepare`、返回值检查与 task-info.json 写入 |
| `environment` | 环境工厂创建、`environment.start()`，包括镜像构建、容器启动和就绪检查 |
| `setup` | 可选的 `dataset.setup`，例如读取注册中心、上传技能 |
| `agent` | 公共 harness 入口，包括 recorder、智能体执行和该入口内的收尾 |
| `evaluate` | 评分函数及评分返回值检查，包括原生 verifier |
| `cleanup` | `environment.destroy()`，包括容器、卷等资源回收 |

例如 `"stage_durations_sec": {"prepare": 0.8, "environment": 180.2, "setup": 1.1,
"agent": 95.4, "evaluate": 6.3, "cleanup": 2.0}`。这些阶段互不重叠；
原有 `duration_sec` 继续表示整个 trial 的耗时，还包含阶段间结果写入、异常记录等调度开销。
未执行的阶段和未配置的 setup 为 `null`，实际测得零则保留零。
失败或正常取消的阶段仍记录终止前的用时，后续未运行的阶段保持 `null`。

每个阶段退出时原子更新该 trial 的 `result.json`，可以在长实验中读取已完成阶段的耗时；
运行中 `status` 为 `running`，trial 收尾后更新为最终状态，并替换实验 `results.jsonl` 中该 trial 的记录。
强制结束进程或断电时，只能保留最后一次已写入的阶段记录，正在执行阶段的最终用时无法保证。
`summary.json` 在每个 trial 结束时更新；续跑开始时也会根据已保存的结果重建，
此时可能包含上次中断留下的 `running` 记录，其缺失评分仍为 `null`。

`summary.json` 顶层及 `groups.<condition>` 中的 `stage_durations_sec` 均按阶段给出
`count`、`total`、`mean`、`max`；除 count 外单位均为秒。
统计包含成功和失败 trial 中实际测得的阶段，count 是测得该阶段用时的 trial 数；
没有测量值时 count 为 0，其余为 `null`。旧实验没有此字段，不会自动补算。
按 total 查看整轮实验主要耗时，按 mean/max 查看单个 trial 的典型和最长阶段耗时。

新实验完成至少一个 trial 后，可在项目根目录用 PowerShell 查看最近实验的阶段统计：

```powershell
Set-Location E:\lornd\research
$summaryFile = Get-ChildItem .\logs\evaluation -Filter summary.json -File -Recurse |
    Where-Object { Test-Path -LiteralPath (Join-Path $_.DirectoryName 'manifest.json') -PathType Leaf } |
    Sort-Object LastWriteTime -Descending | Select-Object -First 1
$summary = Get-Content -LiteralPath $summaryFile.FullName -Raw |
    ConvertFrom-Json
$summary.stage_durations_sec | Format-List
```

预期列出六个阶段及 count/total/mean/max；此命令只读取实验文件，不启动 Docker 或调用模型。
未执行测试，由用户自行验证。

汇总按 condition 分组，同时给出 planned、recorded、scored、coverage 和错误数量。
`mean_reward_scored` 只计算有效分数，`solve_rate_all` 使用计划数量为分母，
`solve_rate_scored` 使用有效评分数量为分母。`paired_skill_effect` 只比较同一任务、同一次
重复中 `no-skill` / `with-skill` 都取得有效分数的配对，报告有效配对数和平均 reward 差。
其他条件仍按名称独立汇总。这里不计算 pass@k。

## 更换数据集或评分函数

组件直接用 `module:function` 引用，不需要增加注册表。也可在 Python 中将实际函数传给
`run_evaluation(config, tasks=..., prepare_task=..., setup_environment=...,
run_agent=..., evaluate=..., create_environment=...)`。

| 函数 | 输入与返回值 |
| --- | --- |
| dataset.loader | `(index: Path, task_ids: list[str] \| None) -> list[Task]` |
| dataset.prepare | `(task: Task, destination: Path, condition: str) -> PreparedTask` |
| dataset.setup | `async (context: TrialContext) -> None`，可选 |
| environment.factory | `(task_dir: Path, *, output_dir: Path, **options) -> Environment` |
| agent.runner | `async execute(*, instruction, model, tools, environment, output_dir, workspace, timeout_sec, skill_catalog, recorder, **原生选项) -> AgentResult` |
| evaluator.function | `async (context: TrialContext, agent: AgentResult) -> Score` |

评估数据结构定义在 `evaluation/contracts.py`；通用执行结果 `AgentResult` 定义在
`harness/base.py`，并由 `evaluation/contracts.py` 重新导出，保持现有评估接口。
数据集 adapter 负责得到独立任务目录、纯任务正文、
超时和所需技能；模型参数不进入数据集 adapter。评分函数可以读取最终回答、任务元数据，
也可以在销毁前通过 `context.environment` 检查或下载产物。返回 `Score(reward=..., metrics=...)`，
reward 统一归一到 [0, 1]；额外指标放在 metrics，runner 不解释其内部结构。
比如更换成 LLM judge，只需要实现评分函数并修改 evaluator.function；原模型配置仍控制 solver。
`[agent]` 中的 runner、recorder 和 tools 会被取出，其余字段仍作为 settings 字典交给公共入口；
公共入口取出并检查 timeout_sec，再把其余字段展开为 execute 的关键字参数。
OpenHands 用普通函数签名拒绝未知选项，dsh 将其余原生选项交给 SDK 构造函数，
不再重复维护 SDK 参数白名单。没有增加 settings 配置层。
`[environment]` 中 factory 被取出，其余字段传给环境工厂。
SkillsBench 的工厂保留镜像 WORKDIR（workdir=None）并清理每个 trial 的卷。
其他环境需要实现现有 Environment 生命周期接口。

更换 harness 只需选择其 runner 和支持的模型参数、运行选项、工具；更换数据集时配置
loader、prepare、可选 setup 和对应 evaluator。环境工厂可单独选择，SkillsBench 的默认工厂
只是为该数据集保留已有 Docker 行为，没有向评测循环写入 Docker 默认值。

dsh 提供 `harness.dsh.runner:execute` 并返回同一种 AgentResult，独立入口同样调用公共 run_agent。
本轮仅统一调用接口：dsh 的四个 registry 工具仍在服务端执行，不能访问 SkillsBench trial 容器；
向该 runner 传入 environment 会明确报错，因此不能只改 runner 就认为 dsh 已能评测 SkillsBench。
补齐 dsh 容器工具绑定属于后续单独的工作。

## 当前范围与手工检查

当前逐个执行，完整支持现有分离 SkillsBench 索引和 Linux Docker；没有批量并发、自动重试、
断点续跑或 Windows BenchFlow 兼容补丁。依赖接口按项目当前 `benchflow==0.7.8`、
`openhands-sdk==1.53.0` 源码接入。BenchFlow 的原生 Windows 平台限制仍属于现有依赖边界，
本次没有更改 site-packages。需要时在 WSL/Linux 配置相同项目依赖和有效的数据路径。
索引中的绝对路径保持现状；迁移系统时要相应转换索引路径。

为保持 trial 独立，拒绝固定 container_name、显式共享的命名卷/网络，以及 trial 目录外的
可写宿主挂载；BenchFlow 自己的逐 trial 日志映射和只读输入挂载允许使用。
预构建基础镜像应不包含额外技能；本次移除的是数据集内声明的 bundled skills。
容器 `/logs/artifacts` 中的产物保留，其他路径的任意生成文件不会自动导出整个文件系统，
需要保留时由评分函数显式调用 `environment.download`。

手工联调应检查：两组轨迹只在 with-skill 出现技能清单；verifier/reward 与 result.json 一致；
合法 reward=0 仍是 completed；失败无 reward 时保持 null 并显示错误阶段；退出后本次容器被回收。
任务镜像首次构建可能很慢。需要扩大任务量时，先用 --task 选少量任务，再删除 task_ids 跑全量。

本次仅检查源码、diff 和静态语法；未执行测试、模型请求、构建或容器运行。
