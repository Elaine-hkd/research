# Research

SkillsBench 评估入口：`python -m evaluation --config config/skillsbench-openhands.toml`。

通用 `evaluation` 调度实验；`datasets/skillsbench.py` 负责数据准备、技能注入和原生评分；
`harness/runner.py` 提供公共 run_agent，管理配置的 recorder 和公共参数检查；
`harness/<名称>/runner.py` 提供 execute，管理各自的 SDK 和工具，统一返回 `AgentResult`。
`harness/diagnostics.py` 提供两者共用的异常归档：保留首次失败、附加收尾错误，统一写入事件、结果和 recorder trace。
配置通过 `module:function` 直接组合组件，无需为每个 harness 编写 evaluation 文件。

当前 SkillsBench 配置使用 OpenHands 和 `DockerEnvironment`，工具选择与
`harness/openhands/main.py` 一致：四个已有 registry 工具及 think/finish。
本次只运行 `with-skill`：注册中心负责技能读取，`execute_resource` 在当前 trial 容器
执行下载后的脚本。独立 `main.py` 未传环境时仍使用服务端执行。
`config/skillsbench-shell-only.toml` 保留任务容器 shell 配置。dsh 已提供相同调用接口，
其 registry 工具尚未绑定 trial 容器。

配置、使用方法、结果格式和扩展接口见 [evaluation/README.md](evaluation/README.md)。
环境与技能执行接口见 [environment/README.md](environment/README.md)。
统一实验格式、组件边界、结果语义和恢复规则见 [Agent 实验原则](EXPERIMENT_PRINCIPLES.md)。

## 代码思路

尝试构建 dataset(environment) -> harness(model) --> evaluation --> visualization 全流程可配置，可替换。

为了解决不同环境下的脚本执行问题（包括构建脚本、技能脚本、评估脚本等），定义了一个 worker 对容器内进程进行管理，每次要执行一个脚本时，先启动 worker，通过 worker 运行该脚本。因此需要在环境内预装 worker 依赖的 python 环境。
