# 标准评估流程

在 `E:\lornd\research` 使用现有 Python 3.12+ 环境。默认配置只选 `3d-scan-calc`，
各执行一次 `no-skill` 和 `with-skill`，便于先联调完整流程。

```powershell
Set-Location E:\lornd\research
.\.venv\Scripts\python.exe -X utf8 -m evaluation --config config\skillsbench.toml --plan
```

预期打印选定任务、模型、条件和 `planned_trials: 2`。`--plan` 只读取配置和任务索引，
不导入 OpenHands / BenchFlow SDK，不需要 API key，不调用模型或启动 Docker。

实际评估需要现有依赖、Docker CLI、Compose v2、运行中的 Linux Docker 引擎，
以及任务构建、verifier 安装依赖所需的网络。任务镜像须提供 Bash；当前 OpenHands adapter
使用支持工具调用的文本模型。宿主读取项目 `.env`（已有环境变量优先），从配置中的
`model.api_key_env` 获取模型密钥，默认是 `DEEPSEEK_API_KEY`。

```powershell
.\.venv\Scripts\python.exe -X utf8 -m evaluation --config config\skillsbench.toml
```

构建进度、OpenHands 事件类型和阶段切换会持续打印。每个 trial 使用新的 Compose 项目；
结束或失败后清理容器和该项目的卷，保留镜像缓存与宿主实验文件。Ctrl+C 会记录当前 trial
为 cancelled 并尝试清理；输出目录中的已有结果保留。正常退出码为 0，流程错误为 1，
Ctrl+C 为 130。任务得分为 0 是有效评估结果，不因此返回流程错误。

## 修改实验

配置文件是 `config/skillsbench.toml`，其中路径相对配置文件所在目录解析。

| 要改变的内容 | 配置项 |
| --- | --- |
| 模型及服务地址 | `[model] model`、`base_url`、`api_key_env` |
| 模型采样与输出预算 | `[model] temperature`、`max_output_tokens`、`reasoning_effort` 等 OpenHands LLM 参数 |
| 数据集及任务集合 | `[dataset] index`、`task_ids`、`loader`、`prepare` |
| 技能对照条件 | `conditions = ["no-skill", "with-skill"]`，也可只保留一组 |
| 每个任务、每组重复次数 | `repeats` |
| 成功判定阈值 | `pass_threshold`，默认 reward >= 1.0 |
| agent 的循环和命令预算 | `[agent] max_iterations`、`timeout_sec`、`tool_timeout_sec` |
| 评分方式 | `[evaluator] function` |

删除 `dataset.task_ids` 一行选择完整索引。也可单次覆盖任务集合：

```powershell
.\.venv\Scripts\python.exe -X utf8 -m evaluation --config config\skillsbench.toml --task 3d-scan-calc citation-check
```

没有指定 `agent.timeout_sec` 时，使用各任务 frontmatter 中的 `agent.timeout_sec`；
verifier 超时、服务、环境变量和 hardening 由任务原配置及 BenchFlow 处理。
模型密钥只填环境变量名，不写进 TOML。其余 `[model]` 参数直接交给 OpenHands `LLM`；
模型名使用该 SDK / LiteLLM 的 provider/model 格式。

## 执行与评分

```text
load_tasks → prepare_task → DockerEnvironment.start → 注入所选技能
           → OpenHands.arun → evaluator → 保存结果 → destroy
```

`prepare_task` 复用现有 `materialize_skillsbench_task`，把分离的索引还原到 trial/task；
`verifiler_dir` 还原为 `verifier/`，只在副本里把旧 `environment:` 转成 `sandbox:`。
数据源、oracle 和 verifier 原件不修改。实验保存索引与任务文本的 SHA-256、依赖版本和
配置快照；环境输入和技能资源保留在各 trial 副本中。运行期间应保持源数据稳定，当前不对
所有二进制输入做全量内容快照校验。

两种条件都会通过 BenchFlow 的 `strip_task_bundled_skills` 清除副本的 `environment/skills`
及对应 Dockerfile COPY 行。`with-skill` 从索引的 `skills_dir` 复制完整资源并上传到容器
`/skills`，向模型提供名称、描述及 SKILL.md 路径；正文由模型通过容器命令自行读取。
`no-skill` 不上传也不公布这些内容。原始任务内容保持一致，技能目录只是可用能力清单，
不代表模型实际使用过技能。

Agent 使用 OpenHands SDK 默认的 system prompt；runner 不覆盖提示词、替换模板或追加
system message 后缀。任务正文及 with-skill 的技能清单作为用户消息输入。
OpenHands 使用固定的 `evaluation_shell`、`think`、`finish` 工具。
`evaluation_shell` 通过原事件循环调用当前 `env.execute`，全部文件读写、命令运行都发生在
同一个任务容器；不使用注册中心远程执行接口。命令默认目录遵循镜像 WORKDIR，每次新建
Bash 进程，不保留前一条命令的 shell 状态。完整工具输出写磁盘，仅传给模型的输出会截断。
不加载宿主项目技能、用户技能或记忆。如果 SDK 自动增加了其他工具，会在首次模型请求前报错。

OpenHands 执行逻辑位于 `harness/openhands/runner.py`，容器命令工具位于
`tools/openhands/environment_shell.py`。`evaluation/openhands.py` 只将 `TrialContext`
转换成任务正文、环境、输出目录等参数，再调用 harness 中的 `run_agent`。依赖方向为
`evaluation → harness → tools/environment`；harness 不导入 evaluation，可以单独用于执行任务。
JSON 写入与脱敏统一使用 `utils/json_io.py`。

默认 evaluator 调用新增的 `env.verify()`，在容器销毁前执行原生 BenchFlow hardening 和
`Verifier.verify()`；沿用原任务 test.sh、reward.txt / reward.json、聚合及校验规则。
reward 缺失、格式无效、API 或环境失败时保留 `reward: null` 和错误阶段；不能把这些情况
冒充为模型得分 0。命令超时或整体 agent 超时会作为流程错误停止当前 trial。
迭代上限或 stuck 时，如果命令通道仍可用，会对已经产生的产物执行 verifier，并保留结束原因。

## 结果目录

```text
logs/evaluation/<实验名>-<UTC时间>-<随机ID>/
  manifest.json                 配置、任务来源、文本哈希、依赖版本、计划数量
  results.jsonl                 每个 trial 结束就追加一条
  summary.json                  每个 trial 结束更新汇总
  trials/<任务>__<条件>__<次数>/
    task/                       还原后的环境、verifier、oracle 副本
    skills/                     with-skill 的完整技能副本
    task-info.json              来源与暴露的技能清单
    result.json                 reward、passed、结束原因、用量、耗时、错误阶段
    agent/events.jsonl          OpenHands 原生事件
    agent/tool-calls.jsonl      容器命令与未截断的 stdout/stderr
    agent/result.json           最终回答、结束原因与用量（缺失用量为 null）
    verifier/                   原生 reward、验证输出及测试报告
    artifacts/                  BenchFlow 映射的 /logs/artifacts
    error.txt                   失败时的异常链
```

JSON 产物使用 `schema_version = 1`。事件文件使用 OpenHands 原生 schema。
`status=completed` 表示流程完成，是否完成任务看 `passed` 与 `reward`；cleanup 失败会将
status 标成 error，已取得的有效 reward 仍保留。`errors[].stage` 区分 prepare、environment、
skills、agent、evaluate、cleanup。

汇总按 condition 分组，同时给出 planned、recorded、scored、coverage 和错误数量。
`mean_reward_scored` 只计算有效分数，`solve_rate_all` 使用计划数量为分母，
`solve_rate_scored` 使用有效评分数量为分母。`paired_skill_effect` 只比较同一任务、同一次
重复中两组都取得有效分数的配对，报告有效配对数和平均 reward 差。这里不计算 pass@k。

## 更换数据集或评分函数

组件直接用 `module:function` 引用，不需要增加注册表。也可在 Python 中将实际函数传给
`run_evaluation(config, tasks=..., prepare_task=..., run_agent=..., evaluate=...)`。

| 函数 | 输入与返回值 |
| --- | --- |
| dataset.loader | `(index: Path, task_ids: list[str] \| None) -> list[Task]` |
| dataset.prepare | `(task: Task, destination: Path, condition: str) -> PreparedTask` |
| agent.runner | `async (context: TrialContext, model: dict, settings: dict) -> AgentResult` |
| evaluator.function | `async (context: TrialContext, agent: AgentResult) -> Score` |

评估数据结构定义在 `evaluation/contracts.py`；通用执行结果 `AgentResult` 定义在
`harness/base.py`，并由 `evaluation/contracts.py` 重新导出，保持现有评估接口。
数据集 adapter 负责得到独立任务目录、纯任务正文、
超时和所需技能；模型参数不进入数据集 adapter。评分函数可以读取最终回答、任务元数据，
也可以在销毁前通过 `context.environment` 检查或下载产物。返回 `Score(reward=..., metrics=...)`，
reward 统一归一到 [0, 1]；额外指标放在 metrics，runner 不解释其内部结构。
比如更换成 LLM judge，只需要实现评分函数并修改 evaluator.function；原模型配置仍控制 solver。
默认已暴露 `create_environment` Python 参数，其他环境需要实现其实际生命周期接口。

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
