# GitTaskBench 资源包

本目录是 [QuantaAlpha/GitTaskBench](https://github.com/QuantaAlpha/GitTaskBench) 在 research 框架下的资源包，
用来跑「读代码仓库 → 用仓库已有能力完成任务」这一类题。上游固定提交记录在 `dataset.json` 的
`provenance.upstream_commit`（当前 `df188a73013393d036442bfb0f323c4c995fa5e1`）。

- 上游共 54 题，本包已接入 3 题：`PDFPlumber_01`、`PDFPlumber_02`、`PDFPlumber_03`（同一仓库 `PDFPlumber` 的全部题）
- 任务格式：`research-gittaskbench-v1`
- 两种条件：`no-skill`（基线）与 `with-skill`（注入由本仓库生成的 `SKILL.md`）
- 评分：直接运行上游的 `test_script.py`，以它写出的 JSONL 结论为准

| 任务 | 要求 | 产物 | 上游判分 | 实测（`gpt-6-sol`，一次） |
|---|---|---|---|---|
| `PDFPlumber_01` | 提取 PDF 第 1 页文本 | `output/PDFPlumber_01/output.txt` | 文本相似度 ≥ 98% | **1.0**（100%） |
| `PDFPlumber_02` | 前 2 页表格合并成一张 CSV | `output/PDFPlumber_02/output.csv` | 逐单元格匹配率 ≥ 75% | **0.0**（43.22%，见第 5 节「上游真值缺陷」） |
| `PDFPlumber_03` | 提取全文邮箱 | `output/PDFPlumber_03/output.txt` | 召回率 ≥ 98% | **1.0**（F1 100%） |

---

## 1. 目录结构

```
data/gittaskbench/
├── dataset.json                     # 数据集 ID / 版本 / 任务格式 / 上游来源
├── index.json                       # 任务清单，task_count 必须与 tasks 长度一致
├── tasks/<任务ID>/
│   ├── query.json                   # 上游 queries/<ID>/query.json，原样
│   ├── prompt.md                    # 上游 queries/<ID>/task_meta/openhands_prompt.md，原样
│   └── task_info.yaml               # 上游 config/<ID>/task_info.yaml，原样（当前适配器不读取）
├── assets/<任务ID>/                 # 输入素材，prepare 时复制为试验目录的 input/
├── groundtruth/<任务ID>/gt.*        # 真值，扩展名随题变化（txt / csv / md / 图片）
├── verifier/<任务ID>/test_script.py # 上游评分脚本，原样
├── skills/<Repo>/                   # 生成器读仓库产出的技能（SKILL.md + provenance.json）
└── code_base/<Repo>/                # 仓库快照，同仓库的题共用一份
```

`code_base/PDFPlumber` 的仓库快照有 146 个文件、约 19 MB，包含 `README.md`、`pdfplumber/` 源码和 `tests/`。
三题共用这一份快照，`index.json` 里三行的 `code_base` 都指向它。
**仓库是任务本体**：没有它，题面的「先读代码库 README」「严禁自行另写实现」就无法执行，任务会退化成
「装个同名库调 API」。

---

## 2. 适配器怎么接的

适配代码在 `datasets/gittaskbench.py`，依赖三份包外配置：

| 配置 | 作用 |
|---|---|
| `config/datasets/gittaskbench.toml` | 指向本包，声明 loader / prepare / setup |
| `config/environments/gittaskbench.toml` | 本机执行，指定 `bash_executable`（Git Bash）与单条命令超时 |
| `config/evaluation/gittaskbench.toml` | `no-skill`、`repeats=1`、评分函数 |
| `config/agents/dsh-code.toml` | 只暴露 `terminal` / `read_file` / `write_file` 的 agent |

### 2.1 五个函数

| 函数 | 行为 |
|---|---|
| `load_tasks` | 读 `index.json`，校验 `query`、`prompt`、`expected_output`；传 `task_ids` 时按传入顺序返回，重复 / 未知 / 非法 ID 直接报错 |
| `prepare_task` | 复制 `assets_dir` → `input/`；复制 `code_base` → 任务目录同名路径；`with-skill` 时再把 `skills_dir` 复制成 `skills/<Repo>/` 并生成技能目录清单；缺仓库或含符号链接直接失败 |
| `create_environment` | 本机 `LocalEnvironment`，工作目录为任务目录 |
| `setup_environment` | 执行 `install` 命令（`pip install -e`），再校验 `import` 落点必须在仓库内；同时核对技能「该有则有、不该有则无」 |
| `evaluate` | 运行上游 `test_script.py`，解析 JSONL 最后一行判定 |

### 2.2 索引字段与读取阶段

| 字段 | 读取阶段 | 含义 |
|---|---|---|
| `id`、`query`、`prompt` | load | `query.task_id` 必须与 `id` 一致 |
| `expected_output` | load / prepare / evaluate | 产物相对路径，三处共用同一值 |
| `assets_dir` | prepare | 复制为 `input/` |
| `code_base` | prepare / setup | 包内仓库路径，复制到任务目录 |
| `install` | setup | 命令列表，`{python}`、`{repo}` 会被替换成解释器与仓库绝对路径 |
| `python_packages` | setup | 应来自仓库的模块名；有 `code_base` 时只校验落点，不从 PyPI 补装 |
| `groundtruth`、`test_script` | evaluate | 真值与评分脚本，不进入 agent 环境；真值仅在模板用到 `{groundtruth}` 时必需 |
| `verifier_args` | load / evaluate | 评分脚本参数模板，可选；加载期校验占位符，未声明时用默认三参数 |
| `skills_dir` | prepare | 生成器产出的技能目录，仅 `with-skill` 读取；声明它必须有 `code_base` |
| `agent_timeout_sec` | load | agent 总预算，与环境配置的单条命令超时不是一回事 |
| `task_info` | 不读取 | 上游元数据，`task_info.yaml` 里的路径与包内布局不同，以索引为准 |

### 2.3 指令是怎么拼的

上游 `prompt.md` 一字不改，适配器只在末尾追加五段：任务 ID 与描述、工作目录与输入素材、代码仓库、产物要求、运行环境。两处替换：

- `{remote_repo_path}` → `$(pwd)/code_base/<Repo>`，由 shell 运行时展开成绝对路径；
- 上游的 `\"` 归一成 `"`，避免模型照抄出多余反斜杠。

运行环境段说明本机是 Windows + Git Bash：`terminal` 的 cwd 就是任务目录，需要绝对路径用 `$(pwd)`；
`read_file` / `write_file` 的 path 不做 shell 展开，只能写相对路径；解释器用 `python` 不用 `python3`。

### 2.4 硬校验（关键设计）

三条约束保证「测的是仓库能力」而不是「调 PyPI 上的同名库」或「技能悄悄泄漏」：

1. `prepare_task`：声明了 `code_base` 但目录缺失 → 直接失败；
2. `setup_environment`：`install` 之后运行探针，检查 `python_packages` 里每个模块的 `__file__`
   是否位于任务目录的仓库副本内 → 不在就判定 setup 失败；
3. 技能按条件双向校验：`with-skill` 要求 `skills_dir` 与清单都在场且每个文件存在，
   `no-skill` 则要求任务目录里**没有** `skills/`、`skill_catalog` 为空 → 任一不满足即 setup 失败。

因此 `reward=1.0` 的含义是：agent 在仓库副本存在、依赖来自仓库的前提下，产出了通过上游评分脚本的结果。

### 2.5 评分口径

`evaluate` 以任务根目录为 cwd，按索引里的 `verifier_args` 模板拼命令行：

```
python verifier/<ID>/test_script.py \
  --output <任务目录>/<expected_output> \
  --groundtruth <包内真值> \
  --result <试验目录>/verifier/results.jsonl
```

上面是**默认模板**，未声明 `verifier_args` 时使用。可用占位符五个：

| 占位符 | 含义 |
|---|---|
| `{output}` | `expected_output` 的绝对路径（任务根目录下） |
| `{output_dir}` | 上一条的父目录 |
| `{input_dir}` | 任务目录下的 `input/` |
| `{groundtruth}` | 真值文件，仅该题真值可用时才能出现 |
| `{result}` | 评分脚本写 JSONL 的位置，**必需** |

规则：JSONL 取最后一行，`Process` 与 `Result` 同时为真、未超时、退出码为 0 → `reward=1`。

**上游参数并不统一。** 54 个评分脚本里 48 个是上面这三参数，6 个例外：

- `AnimeGANv3_03` 用 `-i`（输入目录）/ `-o`（输出目录）加 `--ssim_threshold`、`--fid_threshold`，
  既没有 `--output` 也没有 `--groundtruth`；
- `Faker_01`、`Faker_02`、`DeOldify_03`、`DeScratch_01`、`VideoPose3D_01` 不吃 `--groundtruth`
  （前两题有真值但不参与判分，后三题没有真值，走无参考指标）。

所以参数按题声明，例如：

```json
"verifier_args": ["-i", "{input_dir}", "-o", "{output_dir}", "--result", "{result}",
                  "--ssim_threshold", "0.8", "--fid_threshold", "50"]
```

阈值写进索引而不是代码：跟着题走、可 diff、可追溯。加载期会做五项校验——占位符必须在上面五个之内、
必须含 `{result}`、必须含 `{output}` 或 `{output_dir}`、用了 `{groundtruth}` 就必须真有真值文件、
只用 `{output_dir}` 时 `expected_output` 必须在子目录下（否则会把整个任务根目录连同 `code_base`
拷回宿主）。全部在 `load_tasks` 阶段失败，不拖到评分阶段。

`metrics` 保留 `process`、`result`、`prediction_exists`、`prediction_bytes`、`similarity`、
`exit_code`、`timed_out`、`comments`，新增 `output_mode`（`file` / `directory`）、`output_files`、
`uses_groundtruth` 与 `verifier_argv`（实际执行的参数，便于复核）。

### 2.6 与上游的差异

| 项 | 上游 | 本包（本机模式） | 本包（容器模式） |
|---|---|---|---|
| 执行环境 | Linux + conda | Windows + Git Bash | 共享镜像的 Linux 容器 |
| 任务根目录 | `$REMOTE_REPO_PATH` 的父级 | 准备出来的任务目录 | `/task` |
| 产物命名 | 只给 `output_dir`，由 agent 自己定文件名 | 索引固定 `expected_output`，指令里明确写出 | 同左 |
| 仓库 | `code_base/` 与 `queries/` 同级 | 同样保持 `code_base/<Repo>`，只放已接入的仓库 | 同左 |
| 技能条件 | 无 | `no-skill` / `with-skill` | 同左 |
| prompt | 原样 | 原样保留，差异全部写在适配器追加段 | 同左 |

容器模式与上游对齐的是**执行形态**：CPU、可联网、一个共享环境跑多题、评分脚本在环境内执行。
上游用一份 conda 环境，这里用一份镜像；每个 trial 仍起独立容器，题目依赖由
`install` / `python_packages` 在 setup 阶段装进容器，镜像本身不预装任务库。

#### 容器模式怎么跑

```bash
python -m evaluation \
  --dataset     config/datasets/gittaskbench-docker.toml \
  --environment config/environments/gittaskbench-docker.toml \
  --agent       config/agents/dsh-code.toml \
  --evaluation  config/evaluation/gittaskbench.toml \
  --name        gtb-docker-<实验名>
```

两处 `sandbox = "docker"` 必须同时写：`dataset.sandbox` 决定指令按哪种环境描述
（`/task` 还是任务目录），`environment.sandbox` 决定工厂建哪种环境。
两者不一致时，配置加载和 `setup` 阶段各有一道硬校验，不会跑出「指令说相对路径、
实际在容器里找不到」的脏结果。

镜像与路由：

| 环节 | 落点 |
|---|---|
| Dockerfile | `environment/Dockerfile`（包内一份，题目共用） |
| 构建上下文 | 准备出来的任务目录；`prepare_task` 会复制 Dockerfile 并注入 pip/apt 镜像源与框架 worker 运行时 |
| 任务文件 | 容器起来后整体 `docker cp` 到 `/task`（benchflow 的 `upload_dir` 顺带把 `.sh`/`.py` 的 CRLF 修成 LF） |
| 评分材料 | agent 结束后才复制到 `/task/.evaluation/`，运行期间不在容器里 |
| 产物 | 评分后从容器取回宿主任务目录，两种模式的目录布局一致 |

`benchflow` 的 `upload_file` 用宿主 `Path` 解析容器路径，在 Windows 上会把
`/task/.evaluation` 拼成反斜杠目录名，所以评分目录由适配器先 `mkdir -p` 建好再上传。
这道绕过只影响复制动作，不改变容器内布局。

实测（三题同批，`gpt-6-sol`，无技能）：01 / 02 / 03 分别 1.0 / 0.0 / 1.0，与本机模式完全一致
（02 的 0 分是已知的上游真值缺陷，见第 8 节）。镜像已缓存时 environment 阶段 5.8–7.0 s、
setup 16.5–19.9 s（含 19 MB 任务目录复制与 `pip install -e`）、agent 55–149 s、评分 6.1–6.9 s；
`PDFPlumber_01` 的 `similarity` 为 100.00%，`import pdfplumber` 落到 `/task/code_base/PDFPlumber/pdfplumber`。
首次构建镜像约 124 s，改 Dockerfile 后重建约 139 s，之后每个 trial 只付启动成本。



### 2.7 技能从哪来（生成器）

`datasets/skillgen.py` 只读 `code_base/<Repo>`，产出 `skills/<Repo>/SKILL.md`：

```bash
python -m datasets.skillgen \
  --repo data/gittaskbench/code_base/PDFPlumber \
  --out  data/gittaskbench/skills/PDFPlumber \
  --commit df188a73013393d036442bfb0f323c4c995fa5e1
```

- **材料**：`README.md`、`docs/*.md`（最多 3 篇）、示例文件清单、公开 API 摘要（AST 抽 12 个模块、60 个符号）。
- **不读** `groundtruth/` 与 `test_script/`：生成器拿不到答案，技能才不是抄答案。
- **两种模式**：`--mode llm`（默认，把材料交给模型写操作说明）与 `--mode template`（离线确定性骨架，
  用于跑通链路或做对照）。
- **产物校验**（不过就报错，不写半成品）：YAML 前置元数据可解析、正文 ≥300 字、不含评分材料相关字样、
  不含本机绝对路径、**不得出现 `pip install <包名>`**——README 里常写这句，照抄会让 agent 用 PyPI 包覆盖仓库。
- **`provenance.json`**：记录仓库名、commit、模式、模型、源文件 sha256、技能 sha256，便于复算。

`with-skill` 时技能如何送达：`prepare_task` 把技能复制成任务目录下的 `skills/<Repo>/SKILL.md`，
harness 把这个目录清单（name / description / path）追加到 prompt 末尾，path 是相对任务根目录的路径，
agent 用 `read_file` 直接读。

### 2.8 校准集与留出集

同一仓库有 3 个实例，按「技能可在部分实例上迭代、另一部分只测一次」拆开：

| 配置 | 任务 | 用途 |
|---|---|---|
| `config/datasets/gittaskbench-tune.toml` | `01`、`02` | 校准：技能可以反复改，改完重跑 |
| `config/datasets/gittaskbench-holdout.toml` | `03` | 留出：技能定稿后只跑一次，**不允许据其结果再改技能** |

留出集只有 1 个实例，单次结论方差大；要看稳定性就重复跑，或等接入 `SpeechBrain`（5 题）这类多实例仓库。

---

## 3. 怎么跑

```powershell
# 预览（不调用模型）
uv run python -X utf8 -m evaluation `
  --dataset config\datasets\gittaskbench.toml `
  --environment config\environments\gittaskbench.toml `
  --agent config\agents\dsh-code.toml `
  --evaluation config\evaluation\gittaskbench.toml `
  --name "gittaskbench-plan" --plan

# 实跑全部已接入任务
uv run python -X utf8 -m evaluation `
  --dataset config\datasets\gittaskbench.toml `
  --environment config\environments\gittaskbench.toml `
  --agent config\agents\dsh-code.toml `
  --evaluation config\evaluation\gittaskbench.toml `
  --name "gtb-baseline"

# 只跑指定任务（`--task` 覆盖 dataset.task_ids）
uv run python -X utf8 -m evaluation ... --name "gtb-pdf02" --task PDFPlumber_02
```

技能增益对照：把 `--dataset` 换成校准集或留出集，`--evaluation` 换成同时跑两种条件的配置。

```powershell
# 校准集：01 + 02，各跑 no-skill 与 with-skill（4 条 trial）
uv run python -X utf8 -m evaluation `
  --dataset config\datasets\gittaskbench-tune.toml `
  --environment config\environments\gittaskbench.toml `
  --agent config\agents\dsh-code.toml `
  --evaluation config\evaluation\gittaskbench-skill.toml `
  --name "gtb-skill-tune"

# 留出集：技能定稿后只跑一次
uv run python -X utf8 -m evaluation ... `
  --dataset config\datasets\gittaskbench-holdout.toml `
  --name "gtb-skill-holdout"
```

结果在 `logs/exp/gittaskbench/<harness>/<模型>/<实验名>/`：`summary.json` 是聚合，`results.jsonl`
逐 trial，`trials/<trial_id>/result.json` 是单次终态，`verifier/result.json` 保留评分脚本的
`verdict` 与 stdout/stderr 尾部。失败重跑用 `--resume`。

模型端点写在 `config/agents/dsh-code.toml`，换密钥或服务地址时 `base_url` 与 `api_key_env` 要一起改。

---

## 4. 导入一个新任务

以下步骤不需要改适配器代码。

### 第 0 步：判断这道题能不能在本机跑

| 检查项 | 怎么查 | 影响 |
|---|---|---|
| 仓库是否已存在 | `query.json` 的 `repositories[0].path` | 同仓库（如 `PDFPlumber_02/03`）不用再拉 19 MB |
| 运行期依赖 | 仓库的 `requirements.txt`、`task_info.yaml` | 需要 torch / CLIP / GPU / 系统包的题（`AnimeGANv3`、`DeOldify`、`VideoPose3D`、`SpeechBrain`）本机跑不了 |
| 是否依赖外网 | `task_description` 是否给的是 URL | `Trafilatura_01` 要现场抓网页，网络抖动会变成失败 |
| 是否多产物 | `task_info.yaml` 的 `multi_output` | 为 `true` 时当前适配器不支持，需要先改 `evaluate` |

### 第 1 步：取仓库（同仓库可跳过）

沿用导入时的稀疏克隆目录（没有就按第 6 节重建），只增加目标仓库：

```bash
cd /tmp/gtb
git sparse-checkout add code_base/<Repo>
git checkout FETCH_HEAD
cp -r code_base/<Repo> <research>/data/gittaskbench/code_base/
```

### 第 2 步：落位六类文件

```bash
cd /tmp/gtb
T=<任务ID>
R=<research>/data/gittaskbench

mkdir -p $R/tasks/$T $R/assets/$T $R/groundtruth/$T $R/verifier/$T
cp queries/$T/query.json                    $R/tasks/$T/
cp queries/$T/task_meta/openhands_prompt.md $R/tasks/$T/prompt.md
cp config/$T/task_info.yaml                 $R/tasks/$T/
cp queries/$T/input/*                       $R/assets/$T/
cp test_scripts/$T/test_script.py           $R/verifier/$T/
cp groundtruth/$T/gt.*                      $R/groundtruth/$T/
```

注意：

- `task_info.yaml` 里的 `groundtruth` 字段不可靠（`Faker_01` 就没写，但目录里确实有 `gt.csv`）；以 `groundtruth/<ID>/` 里的实际文件为准，没有真值的题不能按当前评分链路接。
- 输入素材在 `query.json` 里是容器绝对路径，适配器会按 basename 改写成 `input/<文件名>`；
  一题多个输入且同名时会静默错配，这类题要先改名。

### 第 3 步：生成技能（新仓库才需要）

仓库落位后，用生成器读它产出技能；同一仓库的后续题直接复用这份技能，不需要重跑。

```bash
cd <research>
python -m datasets.skillgen \
  --repo data/gittaskbench/code_base/<Repo> \
  --out  data/gittaskbench/skills/<Repo> \
  --commit <上游 commit>
```

生成失败（校验不过）时会直接报错，不会留下半成品。若换了 commit 或补了文档，重跑一次即可，
新旧差异可用 `provenance.json` 里的 `skill_sha256` 比对。

### 第 4 步：索引加一行

在 `index.json` 的 `tasks` 里追加，并把 `task_count` 加 1：

```json
{
  "id": "PDFPlumber_02",
  "task_info": "tasks/PDFPlumber_02/task_info.yaml",
  "query": "tasks/PDFPlumber_02/query.json",
  "prompt": "tasks/PDFPlumber_02/prompt.md",
  "assets_dir": "assets/PDFPlumber_02",
  "groundtruth": "groundtruth/PDFPlumber_02/gt.csv",
  "test_script": "verifier/PDFPlumber_02/test_script.py",
  "code_base": "code_base/PDFPlumber",
  "install": ["{python} -m pip install --disable-pip-version-check -e {repo}"],
  "skills_dir": "skills/PDFPlumber",
  "expected_output": "output/PDFPlumber_02/output.csv",
  "python_packages": ["pdfplumber"],
  "agent_timeout_sec": 1800
}
```

字段取值规则：

- `expected_output`：上游只给 `output_dir`，**文件名由这里定**，适配器会把它写进指令并在评分时按它取产物；
- `verifier_args`：评分脚本的参数模板，**可选**，不写则用 2.5 节的默认三参数。只有该题评分脚本的
  参数名与默认不同、或不接受 `--groundtruth` 时才需要写（54 题里有 6 题属于这种）；
- `skills_dir`：指向该仓库的技能目录；声明了就自动支持 `with-skill`，不声明则只能跑 `no-skill`；
- `install`：能 `pip install -e` 的仓库用上面这条；不可安装的脚本型仓库改成
  `["{python} -m pip install -r {repo}/requirements.txt"]` 或留空；
- `python_packages`：只填「应当由仓库提供、且要被 import」的模块，写 import 名（`Pillow` 要写 `PIL`）；
  脚本型仓库留空，此时不做落点校验，仓库仍通过 `read_file` / `terminal` 使用；
- `agent_timeout_sec`：视频 / 大模型类任务建议调大，默认 1800。

### 第 5 步：预览并单题实跑

先 `--plan --task <任务ID>` 看 `planned_trials: 1`，再去掉 `--plan` 实跑。新题建议用独立实验名：
混跑会让 `manifest` 的任务列表变化，和旧结果对比不直观。

### 第 6 步：验收清单

一条接入正确的 trial 应该同时满足：

1. `setup` 成功执行 `install`，耗时在十几秒到几分钟；
2. trace 里出现 `read_file .../code_base/<Repo>/README.md`；
3. trace 里**没有** `pip install <同名包>`；
4. `verifier/result.json` 的 `verdict` 来自上游脚本，`Process` / `Result` 有明确值；
5. `metrics.prediction_bytes > 0`，且 `similarity`（若脚本输出）与真值量级相符；
6. **`with-skill` 额外看两条**：`metrics.skill_catalog` 非空，且 `metrics.skill_files_read`
   里出现 `skills/<Repo>/SKILL.md`——没读到就说明技能没被用上，这次的增益不成立；
7. `no-skill` 的 `metrics.skill_catalog` 必须为空（适配器在 setup 阶段已强制，出现即配置错误）。

若第 2 条不满足，先看指令是否被截断、仓库是否真的复制进任务目录；若第 3 条不满足，说明 `install`
没生效或 `python_packages` 写错，检查 `setup` 的 stderr。

---

## 5. 已知限制

- **上游真值缺陷（`PDFPlumber_02`）**：`groundtruth/PDFPlumber_02/gt.csv` 的第 38、82 行是空行，
  而评分脚本按索引逐行比对，于是从第 38 行起整表错位；同时真值保留了 PDF 提取时的字符间距
  （`0 3 / 2 5 / 2 0 16`），与任何规范化输出（`03/25/2016`）都不相等。实测把 agent 产物按公司名
  对齐、剔除空行并忽略空白后，79 行 × 7 列 **100% 一致**，但仍被判 43.22%。
  **跑这题时不要把 0 分直接当成模型失败**；要改判分口径需另写评分脚本（不要改上游原件）。
- **只有 1 个仓库**：已接入 `PDFPlumber` 的全部 3 题，但跨仓库的任务（`PyPDF2`、`Faker` 等）还没接，
  无法比较「不同仓库类型」的难度差异。
- **多产物**：上游 5 题 `multi_output: true`（`DeScratch_02/03`、`PyPDF2_02`、`SpeechBrain_01/02`）。
  适配器支持「把整个产物目录取回」——`verifier_args` 只用 `{output_dir}` 时会自动切目录模式，
  但指令里的产物约定仍是单个文件名，接入前要把 `expected_output` 写成该目录下的路径。
- **评分脚本的依赖不会自动安装**：`setup` 只装 `install` 与 `python_packages`，`evaluate` 直接跑
  `test_script.py`。上游 39/54 题的评分脚本 import numpy、PIL、cv2、torch 等，这些不会进容器，
  接入前要么把它们加进 `python_packages`，要么在 `evaluate` 里另装。`PDFPlumber` 三题没暴露
  这个问题，因为它们的评分脚本只用标准库加仓库本身。
- **依赖自相矛盾的仓库跑不了**：`AnimeGANv3` 要 `tensorflow-GPU==1.15.0`（只支持到 Python 3.7，
  而镜像与上游环境都是 3.10），`DeOldify` 同时要 `fastai==1.0.60`（只兼容 torch 1.x）与
  `torch>=2.2.0`。这不是 GPU 不足，换镜像也解决不了。
- **容器内网络是通的**：pip、apt、GitHub 在容器里均已实测可达（GitHub 还比宿主机稳），
  所以「装依赖」「现场抓网页」这两类不再受宿主机 443 干扰影响。
- **外网型任务**：`Trafilatura` 这类要现场抓网页的题，失败率主要来自网络而不是模型，结果要单独看。
- **运行期覆盖**：`setup` 只在开跑前校验 import 落点，agent 中途自行 `pip install` 同名包不会被拦。
- **版本控制**：`.gitignore` 忽略 `/data/*`，本包只有**这份 README 与 `skills/` 入库**（两处单独例外），
  19 MB 仓库快照、`index.json`、`dataset.json` 与各题文件都需要按第 6 节重建；
  `datasets/gittaskbench.py`、`datasets/skillgen.py` 与 2 节列出的四份配置是正常跟踪的文件。
- **技能覆盖面有限**：生成器只读 README、`docs/` 前 3 篇与公开 API 摘要，不读 `tests/`；
  文档写得薄的仓库，产出的技能会很单薄，这时要么补文档、要么换材料源（例如把 README 的示例段单独提出来）。
- **技能不是答案**：技能只描述「怎么用仓库」，不含任何真值或评分信息；若发现技能里出现
  `groundtruth`、`test_script` 字样，说明生成链路被污染，必须重生成而不是手改。

---

## 6. 重建仓库快照

```bash
git clone --filter=blob:none --no-checkout --depth 1 \
  git@github.com:QuantaAlpha/GitTaskBench.git /tmp/gtb
cd /tmp/gtb
git sparse-checkout init --cone
git sparse-checkout set code_base/PDFPlumber
git fetch --depth 1 origin df188a73013393d036442bfb0f323c4c995fa5e1
git checkout FETCH_HEAD
cp -r code_base/PDFPlumber <research>/data/gittaskbench/code_base/
```

复制后应为 146 个文件、约 19 MB。若目录里执行过 `pip install -e`，先删掉生成的 `*.egg-info` 与
`__pycache__`，保持与上游一致。资源包整体可以搬移，只需要改 `config/datasets/gittaskbench.toml`
里的 `package` 路径。
