# Research

SkillsBench 评估入口直接选择组件：

```powershell
python -m evaluation `
  --dataset config/datasets/skillsbench.toml `
  --environment config/environments/skillsbench.toml `
  --agent config/agents/openhands.toml `
  --evaluation config/evaluation/skillsbench.toml `
  --name "skillsbench-openhands" --plan
```

`--plan` 仅读取配置和任务；移除后实际运行，会启动 Docker 并可能产生模型调用费用。

通用 `evaluation` 调度实验；`datasets/skillsbench.py` 负责数据准备、技能注入和原生评分；
`harness/runner.py` 提供公共 run_agent，管理配置的 recorder 和公共参数检查；
`harness/<名称>/runner.py` 提供 execute，管理各自的 SDK 和工具，统一返回 `AgentResult`。
`harness/diagnostics.py` 提供两者共用的异常归档：保留首次失败、附加收尾错误，统一写入事件、结果和 recorder trace。
配置通过 `module:function` 直接组合组件，无需为每个 harness 编写 evaluation 文件。

`--dataset`、`--environment`、`--agent`、`--evaluation` 分别选择 `config/datasets/`、
`config/environments/`、`config/agents/` 和 `config/evaluation/` 中的 TOML 文件。
任务忽略列表统一在 `config/datasets/skillsbench.toml` 的 `exclude_task_ids` 中维护；
模型随 agent 配置，条件、重复次数和评分方法在测试配置中维护。
实验名称由 `--name` 指定，具体规则见 [修改实验](evaluation/README.md#修改实验)。

当前 SkillsBench 配置支持 OpenHands、dsh 和 `DockerEnvironment`，两个默认 agent
均选择四个 registry 工具以及 terminal/read_file/write_file。
本次只运行 `with-skill`：注册中心负责技能读取，`execute_resource` 在当前 trial 容器
执行下载后的脚本。独立 `main.py` 未传环境时仍使用服务端执行。
`config/agents/openhands-shell-only.toml` 保留任务容器 shell + think/finish 配置，
搭配 `config/environments/skillsbench-shell-only.toml` 使用。
dsh 通过每个 trial 的桥接接口将注册中心资源执行、终端和文件操作绑定到任务容器。

配置、使用方法、结果格式和扩展接口见 [evaluation/README.md](evaluation/README.md)。
环境与技能执行接口见 [environment/README.md](environment/README.md)。
统一实验格式、组件边界、结果语义和恢复规则见 [Agent 实验原则](EXPERIMENT_PRINCIPLES.md)。

想先查看一个完整实验的 trace，可以运行 [发邮件示例实验](evaluation/README.md#发邮件示例实验)。
它通过现有 `rpa-email` 技能真实发信，默认一个任务、运行一次，使用本地环境，无需 Docker。

如果项目需要直接调用模型并记录费用，可以单独安装 `research-llm`。调用、单价配置和明细导出见 [大模型调用和项目用量](llm/README.md)。
OpenHands、dsh 在 `[agent]` 下用 `cost_tracking = true/false` 控制费用统计，默认配置已开启。
运行前请设置 `RESEARCH_PROJECT_ID`、`RESEARCH_USER_ID`，也可以写入项目根目录的 `.env`。
账本固定在项目根目录的 `logs/model-usage.sqlite3`；开启时还会在 agent 输出目录保存价格快照与 `usage.jsonl` 审核明细。
模型价格统一在 `llm/src/research_llm/prices.toml` 中配置。

## 代码思路

尝试构建 dataset(environment) -> harness(model) --> evaluation --> visualization 全流程可配置，可替换。

为了解决不同环境下的脚本执行问题（包括构建脚本、技能脚本、评估脚本等），定义了一个 worker 对容器内进程进行管理，每次要执行一个脚本时，先启动 worker，通过 worker 运行该脚本。因此需要在环境内预装 worker 依赖的 python 环境。
