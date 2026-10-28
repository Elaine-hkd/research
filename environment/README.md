# Environment owns Executor

本地和 Docker 使用同一个入口：先创建环境，再调用 `await env.executor.execute_resource(...)`。
环境创建时绑定执行器；只在 running 状态允许执行，destroy 后原 executor 引用也失效。
所有方法均为 async，同一环境在同一个事件循环中使用。

```text
environment/
  local.py                  LocalEnvironment → LocalExecutor
  docker.py                 DockerEnvironment → DockerExecutor
  backends/benchflow.py      Docker 创建、执行通道和清理
  executor/
    bound.py                注册中心下载、参数和结果协议
    local.py / docker.py    绑定环境的执行器类型
    registry.py             原 HTTP 注册中心客户端
    _worker.py              目标环境内的脚本监督进程
  tasks.py                  SkillsBench 分离索引转任务包
  runtime.py                框架独立 Python 的版本、构建来源与启动探测
  desktop.py                给任务副本统一添加 Xfce/noVNC 配置
  start-desktop.sh           所有任务复用的桌面启动脚本
```

## 本地

```python
import asyncio
from environment import LocalEnvironment

async def main():
    async with LocalEnvironment(
        registry_url="http://127.0.0.1:5000",
        workdir=r"E:\lornd\research\workspace",
        allowed_skills=["desktop-hello"],
    ) as env:
        result = await env.executor.execute_resource(
            "desktop-hello", "scripts/create_hello.py", arguments=[], timeout=60,
        )
        print(result.to_dict())

asyncio.run(main())
```

本地环境是当前电脑上的执行会话，不是 VM。启动检查工作目录；销毁只关闭会话，
不会关闭电脑、删除工作目录或产物。默认 workdir 为构造时的 cwd。
执行中取消任务会等待脚本结束或到达其超时再释放会话锁，避免线程仍在执行时误报已销毁。

## Docker（由 BenchFlow 创建）

```python
import asyncio
from pathlib import Path
from uuid import uuid4
from environment import DockerEnvironment, materialize_skillsbench_task

async def main():
    task_dir = materialize_skillsbench_task(
        "data/task/skillsbench.json", "3d-scan-calc",
        Path("workspace/environment") / uuid4().hex / "task",
    )
    async with DockerEnvironment(
        task_dir=task_dir,
        registry_url="http://127.0.0.1:5000",
        workdir="/root",
    ) as env:
        # 替换成 registry 中存在的 Linux 技能和实际资源路径。
        result = await env.executor.execute_resource("my-skill", "scripts/process.py")
        print(result.to_dict())
        # 在销毁前取回任务产物。
        # await env.download("/root/result.json", "outputs/result.json")

asyncio.run(main())
```

无需手动提供 container_id。注册中心资源在宿主下载，通过同一环境的 BenchFlow 通道上传，
监督进程在该任务容器中执行。没有第二套独立 docker exec 管理逻辑。
本地 Windows 专用 desktop-hello 技能不适用于 Linux 容器。

`BenchFlowEnvironment` 名称作为 DockerEnvironment 的兼容别名保留。
旧顶层 executor 包已移除；原 `from executor import ...` 应改为创建环境。
项目 test.py 已改为 LocalEnvironment 示例，执行它会运行 desktop-hello。

## 环境接口与生命周期

- `start()` / `wait_ready()`：启动及就绪检查。
- `execute(argv, cwd=None, timeout=60, service="main")`：原始命令执行；返回 CommandResult。
- `upload(source, destination)` / `download(source, destination, directory=False)`：文件传输。
- `executor.execute_resource(...)`：下载技能并执行；返回 ExecutionResult（退出码、stdout、stderr、超时、耗时）。
- `stop()` / `destroy()`：关闭本次环境；Docker 中两者都回收容器，不支持暂停恢复。
- 支持 async with，退出自动回收。执行器执行期间销毁操作等待生命周期锁。

Docker 适配以已检查源码的 benchflow==0.7.8 为准，仅支持其 Linux Docker 后端。需要 Docker CLI、Compose v2、运行中的 Docker 引擎。
框架使用独立 Python（最低 3.8，默认构建 3.12.12），任务镜像需自行提供 Bash；宿主 Python 3.12+。
`start()` / `wait_ready()` 通过原始容器通道检查框架解释器版本、worker 标准库导入和
任务 PATH 中的 Bash；失败时不进入 running 状态。注册中心请求只发生在宿主。

| 设置 | 用途 | Docker 默认值 |
| --- | --- | --- |
| `worker_python_executable` | worker、命令清理、DSH/OpenHands 文件工具 | `/opt/research-runtime/python3` |
| `python_executable` | `execute_resource` 执行 Python 技能脚本 | `python3`，按任务原 PATH 查找 |

框架 Python 通过绝对路径及 `-I -B` 启动，忽略 `PYTHONHOME`、`PYTHONPATH` 和用户 site-packages，
不在任务目录导入同名模块，也不写入 Python 字节码。任务子进程继续使用原有环境变量，
原生终端命令与评分器不切换到框架 Python；技能缺少解释器或第三方包时不会自动借用框架环境。
本地环境不下载运行时，文件工具使用宿主 `sys.executable`，技能脚本保留其原配置。
原始命令通道将 stderr 合并至 stdout，但技能脚本由监督进程分别捕获 stdout/stderr。
Docker 原始命令与技能脚本复用容器内监督进程，每次命令在独立进程组中执行；
达到执行时限后杀死该组，并限时回收输出和退出状态。清理成功的命令超时返回
`CommandResult(exit_code=None, timed_out=True, ...)`，保留已收集的输出，`success=False`，环境仍为 running。
普通非零退出码同样是工具结果；Docker 传输失败、worker 响应异常或清理未完成才抛异常并标记 failed。
如果清理后输出管道仍未关闭，不能确认子进程已退出，仍按故障处理。
DSH 和 OpenHands 的共享工具层将已确认的超时返回为 `TOOL_TIMEOUT`、`recoverable=true` 的工具错误，
不会设置通道失败或中断会话；模型可继续调用工具。两分钟工具上限不变，总 agent 预算到期仍结束 trial。
宿主 Docker CLI 另留 transport_grace（默认30秒）供回传和清理；超时或取消时，
Windows 使用 taskkill /T /F 回收本次 CLI 及 Compose 插件，其他宿主回收该 CLI 进程组。
所有退出与输出等待都有清理边界；连接中断时还会通过独立调用停止容器内对应命令组。
停止失败会附加在原始异常中，环境仍标记 failed 并由 destroy 回收容器。
Docker 传输异常后标记 failed，拒绝继续执行；通过 destroy 回收容器，不假定结束宿主等待就终止了远端脚本。
startup_timeout 默认900秒、ready_timeout 30秒、cleanup_timeout 180秒。
`docker compose build` 默认使用 `--progress plain`，输出实时写入宿主 stderr，
同时保留完整输出用于构建失败的异常；无需修改 DockerEnvironment 的调用方式。
启动超时或取消时会终止本次构建的 Docker CLI，并继续执行环境清理。
该进程适配只修改本次 sandbox 实例的 BenchFlow 0.7.8 内部方法，不修改安装包或其他环境实例。
镜像、宿主日志、任务 staging 和持久卷保留；持久卷的实验重置策略由 eval 管理。
日志按 BenchFlow 原生规则写入 cwd/jobs/environment/，不修改进程 cwd。

## 注册中心与文件

GET /load_skill?skill_name=... 返回正文及 resources；GET /load_resource 返回 UTF-8 文本。
每次执行下载清单中的全部文本资源，在目标环境临时目录保留结构，结束后清理。
SKILL.md 保存正文，不包含注册中心剥离的 frontmatter；未列出的文件不下载。
图片、Excel 等二进制资源不支持，失败时不会执行脚本；需后续新增 registry 下载/打包接口。
需要保留的输出写入持久 workdir 或指定绝对路径，不写入临时技能目录。
allowed_skills=None 允许全部技能，[] 禁用全部；registry_url 可省略，此时只能使用原始环境命令和文件操作。
registry_timeout 是单次 HTTP 超时；default_timeout 是脚本超时（默认300秒）；Docker transport_grace 默认30秒。
`.py`、`.sh` 分别使用已配置的 Python、Bash；`.mjs` 使用目标环境 PATH 中的 `node` 启动，
无需脚本执行权限或 shebang。目标环境需自行提供 Node.js；不会自动安装或切换到宿主执行。
worker 启动失败时回传原始错误类型和 traceback，宿主异常保留容器 traceback 与异常链；
脚本正常启动后非零退出的 stdout/stderr 继续保存在执行结果中。
DSH 工具错误保留 `cause`、错误码、stack 和实际请求信号的取消原因；TS、JS 和当前 profile 副本保持同步。
DSH 和 OpenHands 共用 `harness/diagnostics.py` 的 `RunDiagnostics`：关闭 SDK 前将上游异常写入
`agent/events.jsonl` 的 `runner/error` 事件，运行任务结束后通过 recorder 的统一
`record_failure(error, session_id=...)` 接口写入带 `source=runner` 的 `run.failed`，
并在 `agent/result.json.error` 保存完整异常链。SDK 关闭、指标提取和归档的后续异常作为附注，
不覆盖首次失败。公共 run_agent 同样保护原始异常，避免 recorder 关闭失败替换它。
新增 harness 时使用 `remember(error, stage)` 保存首次失败，以 `stage(name)` 包住独立的收尾步骤，
最后调用 `finish(result, recorder=..., session_id=...)`；无需复制异常格式、脱敏和写入流程。
SDK 的中断/关闭顺序、指标提取以及 recorder 的 session/run 关联仍由各适配模块负责。
`execute_resource(..., total_timeout=...)` 可让下载与脚本执行共用预算；共享工具层传入当前有效工具预算，
下载占用的时间从脚本时限中扣除。下载预算耗尽时不启动脚本，并返回可恢复超时。
容器传输和退出清理保留独立时限，因此调用的墙钟耗时可以超过执行预算；不会由同一计时器抢先取消清理。
不传 total_timeout 的既有调用仍沿用原来的脚本超时语义。
实验期间应固定 registry 数据，当前 API 没有版本快照；资源和输出均在内存处理。

## 现有 SkillsBench 索引

```python
from environment import materialize_skillsbench_task, DockerEnvironment
task = materialize_skillsbench_task(
    r"E:\lornd\research\data\task\skillsbench.json", "3d-scan-calc",
    r"E:\lornd\research\workspace\environment\trial-001\task",
)
env = DockerEnvironment(task, registry_url="http://127.0.0.1:5000")
```

目标目录需不存在；复制 task/environment/verifiler/oracle/extra_paths，不改原数据。
生成的 task.md 将 YAML 顶层 environment 键改为 sandbox，保留配置值和 Markdown 正文；environment/ 目录仍使用原名。
若同时声明 environment 和 sandbox 则报错，不合并或覆盖配置。转换会重新排版 YAML，并移除其中的注释。
BenchFlow 0.7.8 直接读取 task.md，不再生成 instruction.md 或 task.toml。
原索引 verifiler_dir 映射为 verifier/。这里不执行 agent、oracle 或评分，也不控制 with-skill/no-skill 条件。

生成副本时还会扫描 `environment/`（含子目录）中的 `Dockerfile*`，在每个有 RUN 的构建阶段
第一条 RUN 前注入清华 TUNA APT 换源命令；前置 USER 等指令保留原顺序。最后阶段没有 RUN 时
在末尾注入，确保随后追加的桌面安装也能使用新源；没有 RUN 的中间阶段及 scratch 阶段不注入。
Ubuntu/Debian 根据镜像内 `/etc/os-release` 识别，同时处理传统 `sources.list`、`.list` 和
DEB822 `.sources` 文件中的官方软件源、安全更新源；保留发行版代号、组件、签名配置及第三方仓库。
Ubuntu Ports 使用对应的 `ubuntu-ports` 镜像；使用 HTTP 避免最小镜像缺少 CA 证书导致首次换源失败，
APT 签名校验保持启用。源地址按[清华 Ubuntu](https://mirrors.tuna.tsinghua.edu.cn/help/ubuntu/)与
[Debian](https://mirrors.tuna.tsinghua.edu.cn/help/debian/)镜像说明配置。
非 Ubuntu/Debian 系统打印跳过信息；Ubuntu/Debian 在首条 RUN 时必须已使用 root，否则明确报错。
同一段 Dockerfile 解析还会在每个非 `scratch` 阶段的 FROM 后注入
`ENV PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple`，让该阶段后续的 `pip install`、
`python -m pip install` 以及镜像运行期间的 pip 默认使用清华 PyPI 源。
使用 ENV 不要求基础镜像预装 Python/pip，也不需要 root；HTTPS 地址按
[清华 PyPI 镜像说明](https://mirrors.tuna.tsinghua.edu.cn/help/pypi/)配置，保留证书校验。
任务显式指定的 `-i` / `--index-url`（如 PyTorch CPU 专用源）、直接下载 URL，以及后续重新设置的
`PIP_INDEX_URL` 仍优先；此处不配置 uv，也不删除第三方 extra-index-url。
不修改 `data/` 原件或已有任务副本，不更改镜像基础版本，也不配置 npm 或 Docker Hub 镜像。
现有 `materialize_skillsbench_task(...)` 调用无需调整；只有重新生成的副本才包含此操作。

副本的 `environment/Dockerfile` 会添加 `# research: isolated worker runtime`：
在原最终 FROM 前插入独立阶段，使用 `ghcr.io/astral-sh/uv:0.9.22-bookworm-slim`
下载固定 Python 3.12.12 的 python-build-standalone 发行包；uv 的 `--no-bin` 避免安装全局命令。
最终任务镜像只复制 `/opt/research-runtime`，不复制 uv、构建镜像的系统库或系统环境变量，
不为框架执行 APT/pip 安装，不修改系统 Python、PATH、USER、WORKDIR、虚拟环境或 Conda。
框架版本、下载镜像和路径集中在 `environment/runtime.py`。

构建阶段沿用最终 FROM 的 `--platform`；放在最终阶段前以保留原有前置阶段的编号，
镜像原有的换源逻辑复用同一份 Dockerfile 指令解析，不把 heredoc 中的数据误认作阶段。
复制前检查保留目录是否冲突，复制后以原最终 USER 运行依赖探测；运行时目录允许非 root 读取与执行。
这属于 Python 运行时隔离，仍共享目标镜像的内核、glibc 和动态链接环境；当前下载的是 glibc Linux 版本，
面向 Ubuntu/Debian，不能直接保证 Alpine/musl 或旧 glibc 兼容，构建与启动探测均不会自动升级系统库。
本次未构建镜像，下载可达性和目标镜像 ABI 兼容性仍需运行验证。

自动注入针对默认主 Dockerfile 的最终阶段；预构建镜像、其他构建文件或较早 target、辅助服务
需自行提供独立运行时，可在 `[environment]` 中用 `worker_python_executable` 指定绝对路径。
原始数据和历史 trial 不修改；旧副本中的系统包安装步骤不会自动清除，需重新生成副本并构建。

验证本次缺 Python 的任务（执行目录 `E:\lornd\research`，需现有项目依赖与 Docker）：

```powershell
.\.venv\Scripts\python.exe -X utf8 -m environment.demo --index data\task\skillsbench.json --task-id fix-build-google-auto
```

该命令会创建新副本、构建并启动容器、通过 worker 执行命令、下载文件，然后销毁容器；
会下载构建镜像、独立 Python 及任务原 Dockerfile 所需依赖，不调用模型、注册中心或评分器。
预期先输出框架解释器 `/opt/research-runtime/python3`、任务技能解释器 `python3`，
最后输出“命令执行和文件传输成功”；即使任务系统没有 python3，这个检查也应使用框架 Python 完成。
未执行测试，由用户自行验证。

只检查生成结果、不启动 Docker（项目已有 Python 环境与依赖即可；执行目录 `E:\lornd\research`）：

```powershell
@'
from pathlib import Path
from uuid import uuid4
from environment import materialize_skillsbench_task
task = materialize_skillsbench_task(
    "data/task/skillsbench.json", "3d-scan-calc",
    Path("workspace/environment") / uuid4().hex / "task",
)
print(task)
print((task / "environment/Dockerfile").read_text(encoding="utf-8"))
'@ | .\.venv\Scripts\python.exe -
```

预期每个非 `scratch` 阶段的 FROM 后出现 `# research: pip mirror (TUNA)` 及上述 ENV，
`# research: APT mirror (TUNA)` 及其 RUN 位于原始 `RUN apt-get update ...` 之前。
构建时应看到 `research: APT mirror configured for ubuntu (TUNA)`，APT 下载日志使用
`mirrors.tuna.tsinghua.edu.cn`；普通 pip 安装应显示
`Looking in indexes: https://pypi.tuna.tsinghua.edu.cn/simple`。
重点检查 Ubuntu 24.04、Python slim/Debian 以及添加桌面后的构建。
本次仅检查代码与 diff，未执行上述命令、测试或 Docker 构建。

## 给任务副本添加完整桌面

在任务生成后、创建 DockerEnvironment 前调用一次：

```python
from environment import configure_task_desktop

configure_task_desktop(task_dir, port=6080)
async with DockerEnvironment(task_dir=task_dir, startup_timeout=1800) as env:
    # 在这里运行你的任务；async with 结束后桌面也会关闭。
    ...
```

该函数只准备文件，不启动 Docker：在副本 Dockerfile 的最后一个构建阶段追加 Xfce、
Xvfb、VNC、noVNC 和中文字体安装指令，复制 research-desktop/start-desktop.sh，
合并 environment/docker-compose.yaml 的 main 服务。原有其他服务、挂载、网络、环境变量保留；
环境变量支持字典和列表两种写法。原 main.command 在桌面启动后继续执行，原 ENTRYPOINT 保留，
Compose 中已有的健康检查接在桌面检查之后。YAML 会重新排版并移除注释。

BenchFlow 的 compose up --wait 会等待虚拟屏幕、窗口管理器、VNC 和 noVNC 可用。
访问 http://127.0.0.1:6080/vnc.html?autoconnect=true&resize=scale，可看到 Xfce 面板、菜单和桌面图标；
文件管理器直接访问任务容器的文件系统。普通后台命令仍需通过执行日志查看。
当前 test.py 已在生成 Docker 任务副本后接入该函数；该示例原有 registry/技能前置条件仍需满足。

范围：root 用户的 Debian/Ubuntu 镜像，构建上下文为该任务的 environment 目录，使用最后一个
Dockerfile 构建阶段。预构建 sandbox.docker_image、指定 build.target、内联 Dockerfile、
非 root 服务和禁用网络的任务会明确报错。其他基础镜像在构建时拒绝。
若已有 .dockerignore，需允许 research-desktop/start-desktop.sh 进入构建上下文。
一个任务副本只能配置一次；需要换端口时重新生成副本。并发任务分别传 port=6080、6081 等，
端口占用由 Docker 在启动时报告；容器中的端口始终是 6080。这里只映射宿主 127.0.0.1，
VNC 使用无密码且只监听容器内部回环地址。桌面组件会增加镜像体积及构建时间。

不依赖 registry、模型或技能的手工查看方式（本次未执行）：在 E:\lornd\research 的 PowerShell 中，
使用已有 .venv、运行中的 Docker Desktop/Linux 容器和可访问 apt 软件源的网络执行：

```powershell
@'
import asyncio
from pathlib import Path
from uuid import uuid4
from environment import DockerEnvironment, configure_task_desktop, materialize_skillsbench_task

async def main():
    task = materialize_skillsbench_task(
        "data/task/skillsbench.json", "3d-scan-calc",
        Path("workspace/environment") / uuid4().hex / "task",
    )
    configure_task_desktop(task, port=6080)
    print(f"Task copy: {task}", flush=True)
    async with DockerEnvironment(task, startup_timeout=1800):
        print("Desktop ready: http://127.0.0.1:6080/vnc.html?autoconnect=true&resize=scale", flush=True)
        await asyncio.Event().wait()

asyncio.run(main())
'@ | .\.venv\Scripts\python.exe -
```

预期构建时持续显示安装进度，出现 Desktop ready 后打开上述网址，看到完整 Xfce 桌面。
按 Ctrl+C 结束并回收容器。首次安装桌面可能超过原先的 900 秒启动超时，示例显式给到 1800 秒。
未新增或执行测试、未安装依赖、未构建或启动容器。

## VMware / 云端扩展

目前没有假实现。新增环境需实现 Environment 协议，创建并持有对应 BoundExecutor 子类；
提供 `_require_running()` 和受生命周期锁保护的 `_run_worker(payload)`，负责在目标 guest 内运行监督进程并返回结果。
可以复用注册中心下载、参数校验及 ExecutionResult。VMware Guest Tools、SSH 或云端 SDK 的连接留在新环境的后端中。
清理要区分释放会话、停止实例和删除实例，不应照搬 Docker 的语义。

## 用户验证（未执行）

不启动 Docker/registry 的回归测试（HTTP 和 BenchFlow 模拟，本地夹具脚本真实执行）：

```powershell
Set-Location E:\lornd\research
python -m unittest environment.test_backend environment.test_integration -v
```

预期 OK。检查本地下载后的模块/模板、中文输出、产物保留、销毁后调用拒绝、Docker 使用环境通道、启动失败清理。

实际本地联调：先启动注册中心并注册 desktop-hello，使用已有 Python 3.12+：

```powershell
Set-Location E:\lornd\research
python test.py
```

预期 success=True，当前 Windows 用户桌面的 hello.txt 内容为 hello。该操作会覆盖同名文件。

真实 Docker 环境通道检查（不运行模型、registry skill 或 verifier）：
先用项目锁文件同步依赖，以保留 pyproject.toml 中的 LiteLLM 覆盖配置。

```powershell
uv sync --locked
python -m environment.demo --index data\task\skillsbench.json --task-id 3d-scan-calc
```

预期 workspace/environment/<唯一目录>/hello.txt 内容为 hello，随后回收容器。需要镜像构建所需的网络。
技能的真实 Docker 联调用上方 DockerEnvironment 示例，替换 registry 中可执行的 Linux 技能。
未安装依赖、未修改 uv.lock、未执行测试；不影响现有 harness 的 registry 工具调用。
