# 公共工具加载与 OpenHands 入口

工具选择统一使用普通名称列表，通过 tools 参数传入；不使用 settings.tools，
普通任务入口也不再读取上一版的 config/openhands.toml。

```python
from harness.openhands.main import run_task as run_openhands
from harness.dsh.main import run_task as run_dsh

selected = ["search_skills", "load_skill"]
task = "搜索可用技能并读取一个技能的说明，不执行资源"
run_openhands(task, exposed_skills=["rpa-email"], tools=selected)
run_dsh(task, exposed_skills=["rpa-email"], tools=selected)
```

两种入口均提供 run_task(task, exposed_skills=None, *, tools=None)。省略 tools 时，
OpenHands 使用已有四个 registry 工具和本地 think/finish；dsh 使用已有四个 registry
工具并保留其原生运行流程。显式 tools=[] 表示不加载自定义工具，不会回退到默认列表。

## 公共入口

`tools/loader.py` 只依赖 Python 标准库：

```python
from tools.loader import load_tools

selected = ["search_skills", "load_skill"]
openhands_tools = load_tools("openhands", selected)
dsh_options = load_tools("dsh", selected)
```

```text
tools 名称列表 → validate_tools → 导入 tools.<harness>.toolset
                              → create_tools(名称列表, 原生资源绑定)
                              → 返回该 harness 可用的工具参数
```

公共代码检查名称格式、重复项和适配代码返回的实际名称，只导入所选 harness 的适配器。
原生导入与注册留在适配器内：OpenHands 返回 SDK Tool 列表；dsh 返回 DeepSeekHarness
构造函数需要的 patches/env 字典。公共入口不导入任何 SDK 类型，也没有新增配置类或工厂类。

| 工具名称 | OpenHands | dsh |
| --- | --- | --- |
| search_skills / load_skill / load_resource / execute_resource | 已有 Python 实现 | 已有 registry 插件 |
| terminal / read_file / write_file / replace_text | 已有任务容器工具 | 无对应实现，选择时直接报错 |
| think / finish | 已有本地控制工具 | 不映射为 dsh 工具，使用其原生流程 |

增加工具时复用或编写各 harness 的原生实现，并更新相应适配文件中的名称映射/支持列表。
添加新的 harness 时，实现 tools/<harness>/toolset.py 的 create_tools(names, **bindings)，
返回 (原生参数, 实际工具名称集合)；公共 load_tools 不需要添加分支。

## OpenHands 的环境绑定

公共 harness.runner.run_agent(runner="harness.openhands.runner:execute", ...,
tools=["terminal", "read_file", "finish"], environment=env)
会先创建当前 trial 的 ShellExecutor，再调用公共 load_tools("openhands", tools,
executor=executor)。工具实例由适配器绑定和注册，模型看到的是稳定工具名称。
原有 settings 参数仅承载迭代、超时等预算；工具通过单独的 tools 参数传递。
省略 tools 的旧环境调用保留 shell + 本地 think/finish。

独立调用方也可以使用 tools=load_tools("openhands", selected) 或原有 get_registry_tools()
传入 SDK Tool 列表；已创建的 SDK specs 与 environment 互斥，避免误认为它们会自动绑定容器。
SDK 默认工具保持关闭，按名称加载时在首次模型请求前核对实际工具清单。

shell/文件工具仍通过原事件循环调用 environment.execute，共用预算、中断和输出限制。
相对路径遵循镜像 WORKDIR；文件工具要求镜像已有 python3，仅处理 UTF-8 文本。
传入 environment 时，execute_resource 通过同一通道调用 environment.executor.execute_resource，
复用注册中心资源下载和环境已有的 worker。脚本在当前 trial 容器执行，不调用服务端
/execute_resource。工作目录未覆盖时使用镜像 WORKDIR；临时技能目录在执行后删除，
需要保留的输出应写在工作目录或明确的任务路径中。
未传 environment 时，execute_resource 保持原有服务端执行方式。
本地 finish 保留 SDK 1.53.0 识别的 finish 名称；未启用依赖 SDK FinishAction 类型的 critic 扩展。

## dsh 的工具筛选

dsh 适配器复用现有 registry.patch.yml 禁用 persistent-bash/persistent-pwsh，通过子进程
环境变量 HARNESS_TOOLS 传递选中的工具名称。已有 skill_tool 插件只注册其中的工具，
未设置该变量的旧插件调用保留四个工具；显式 [] 不注册，也不要求 registry 地址。
EXPOSED_SKILLS 继续负责技能可见范围，两个列表互不替代。

TS 源码和 package.json 指向的 JS 入口已同步修改，没有运行编译或插件安装命令。
当前项目 tools/dsh/skill_tool.js 与 sdk-minimal profile 中的入口是硬链接，应用脚本保留
文件身份，因此两处内容同步。其他 checkout 或独立复制的 profile 需要按其原有方式更新插件。
dsh 单任务入口调用公共 `harness/runner.py`，选择 `harness.dsh.runner:execute`，返回 `AgentResult`。
公共入口管理配置的 recorder；execute 管理 dsh SDK，并从标准 trace 统计已知用量；没有原生费用来源时保留 null。
现有 registry 工具不能访问 trial 容器，传入 environment 会明确报错；本轮没有补齐容器工具绑定。

## 评测配置和记录

config/skillsbench.toml 的 [agent].tools 使用名称，例如：

```toml
tools = ["search_skills", "load_skill", "load_resource", "execute_resource", "think", "finish"]
```

evaluation/runner.py 取出该列表后显式传给 runner 的 tools 参数。配置和预算仍由现有
评测配置文件管理；不会把整份配置作为工具 settings 传入 harness。
默认列表与 main.py 一致，复用原有四个 registry 实现；think/finish 也沿用原有实现。
评测使用 [environment].registry_url 创建环境自有的 RegistryClient；搜索、加载和执行
共用该地址和当前任务的技能范围，不修改宿主全局环境变量。
SkillsBench setup 将范围限制在该任务的 skill_catalog；注册中心须预先注册对应技能。
本次配置仅运行 with-skill，默认一个任务、一次重复，共 1 个 trial。
独立 run_task 仍从 SKILL_REGISTRY_URL、EXPOSED_SKILLS 读取配置。

容器执行沿用环境层现有的 UTF-8 文本资源下载协议，未增加二进制资源下载；镜像须提供
python3 和脚本依赖。脚本的退出码、stdout/stderr、超时和耗时保存在 tool-calls.jsonl，
返回模型的输出受 max_output_chars 限制。超时或执行通道故障会记为流程错误。
tools.json 继续记录实际工具和基础 function schema；result.json 的 usage.tool_names、
usage.tool_calls_by_name 记录工具清单与模型发出的调用次数。实际请求 schema 以 trace 为准。

`[agent].runner = "harness.openhands.runner:execute"` 选择执行函数，
`[agent].recorder = "agent_session_analyzer:OpenHandsRecorder"` 选择记录器。
没有单独的 evaluation/OpenHands 桥接模块。公共 run_agent 接收 instruction、model、settings、
tools、environment、output_dir、timeout_sec、skill_catalog、metadata，返回值定义在 harness/base.py。
settings 仍只是原有运行参数，工具保持单独的 tools 参数。
公共入口按配置在 output_dir/trace 创建和关闭 recorder，metadata 原样交给记录器。
省略 recorder 不录制标准 trace；显式传入 recorder 实例时，其生命周期仍由调用方管理。
原生 execute 不创建或关闭 recorder，只保留各 SDK 所需的接入方式和安全收尾。
两个 run_task 的参数保持不变，内部均使用公共入口；独立运行的标准 trace 也放在输出目录的 trace/ 下，
同时保存该 harness 的原生事件和结果。SDK 参数复用其校验，仅项目自定义的必要约束留在 execute 内。
数据集准备、技能注入和评分位于 datasets/skillsbench.py，不进入 harness。

最小检查是在项目根目录运行：

```powershell
.\.venv\Scripts\python.exe -X utf8 -m evaluation --config config\skillsbench.toml --plan
.\.venv\Scripts\python.exe -X utf8 -c "from tools.loader import load_tools; print(load_tools('dsh', ['search_skills']))"
```

预期分别显示六个工具名和一个 trial，以及包含 registry.patch.yml 与 HARNESS_TOOLS 的
普通字典。第二条不启动 dsh、不读取模型密钥。完整运行需要现有 SDK、模型和 registry；
任务评测另需可运行的 Docker/BenchFlow 环境。本次只检查源码、diff 和静态语法，未运行测试、
构建、导入探测、模型请求或 Docker。
