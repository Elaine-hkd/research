# GitTaskBench 资源包

本目录是 [QuantaAlpha/GitTaskBench](https://github.com/QuantaAlpha/GitTaskBench) 在 research 框架下的资源包，
用来跑「读代码仓库 → 用仓库已有能力完成任务」这一类题。上游固定提交记录在 `dataset.json` 的
`provenance.upstream_commit`（当前 `df188a73013393d036442bfb0f323c4c995fa5e1`）。

- 上游共 54 题，本包已接入 3 题：`PDFPlumber_01`、`PDFPlumber_02`、`PDFPlumber_03`（同一仓库 `PDFPlumber` 的全部题）
- 任务格式：`research-gittaskbench-v1`
- 只支持 `no-skill` 条件（GitTaskBench 没有技能维度）
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
| `prepare_task` | 复制 `assets_dir` → `input/`；复制 `code_base` → 任务目录同名路径；缺仓库或含符号链接直接失败 |
| `create_environment` | 本机 `LocalEnvironment`，工作目录为任务目录 |
| `setup_environment` | 执行 `install` 命令（`pip install -e`），再校验 `import` 落点必须在仓库内 |
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
| `groundtruth`、`test_script` | evaluate | 真值与评分脚本，不进入 agent 环境 |
| `agent_timeout_sec` | load | agent 总预算，与环境配置的单条命令超时不是一回事 |
| `task_info` | 不读取 | 上游元数据，`task_info.yaml` 里的路径与包内布局不同，以索引为准 |

### 2.3 指令是怎么拼的

上游 `prompt.md` 一字不改，适配器只在末尾追加五段：任务 ID 与描述、工作目录与输入素材、代码仓库、产物要求、运行环境。两处替换：

- `{remote_repo_path}` → `$(pwd)/code_base/<Repo>`，由 shell 运行时展开成绝对路径；
- 上游的 `\"` 归一成 `"`，避免模型照抄出多余反斜杠。

运行环境段说明本机是 Windows + Git Bash：`terminal` 的 cwd 就是任务目录，需要绝对路径用 `$(pwd)`；
`read_file` / `write_file` 的 path 不做 shell 展开，只能写相对路径；解释器用 `python` 不用 `python3`。

### 2.4 硬校验（关键设计）

两条约束保证「测的是仓库能力」而不是「调 PyPI 上的同名库」：

1. `prepare_task`：声明了 `code_base` 但目录缺失 → 直接失败；
2. `setup_environment`：`install` 之后运行探针，检查 `python_packages` 里每个模块的 `__file__`
   是否位于任务目录的仓库副本内 → 不在就判定 setup 失败。

因此 `reward=1.0` 的含义是：agent 在仓库副本存在、依赖来自仓库的前提下，产出了通过上游评分脚本的结果。

### 2.5 评分口径

`evaluate` 以任务目录为 cwd 执行：

```
python verifier/<ID>/test_script.py \
  --output <任务目录>/<expected_output> \
  --groundtruth <包内真值> \
  --result <试验目录>/verifier/results.jsonl
```

规则：JSONL 取最后一行，`Process` 与 `Result` 同时为真、未超时、退出码为 0 → `reward=1`。
`metrics` 保留 `process`、`result`、`prediction_exists`、`prediction_bytes`、`similarity`（从 stdout
正则提取，仅文本相似度类任务有）、`exit_code`、`timed_out`、`comments`。
上游 54 题的评分脚本都接受这三个参数（已抽查 `AnimeGANv3_01`），评分侧不需要按题改写。

### 2.6 与上游的差异

| 项 | 上游 | 本包 |
|---|---|---|
| 执行环境 | Linux 容器 / conda | 本机 Windows + Git Bash，不走 Docker |
| 产物命名 | 只给 `output_dir`，由 agent 自己定文件名 | 索引固定 `expected_output`，指令里明确写出 |
| 仓库 | `code_base/` 与 `queries/` 同级 | 同样保持 `code_base/<Repo>`，只放已接入的仓库 |
| 技能条件 | 无 | 仅 `no-skill` |
| prompt | 原样 | 原样保留，差异全部写在适配器追加段 |

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

### 第 2 步：落位五类文件

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

### 第 3 步：索引加一行

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
  "expected_output": "output/PDFPlumber_02/output.csv",
  "python_packages": ["pdfplumber"],
  "agent_timeout_sec": 1800
}
```

字段取值规则：

- `expected_output`：上游只给 `output_dir`，**文件名由这里定**，适配器会把它写进指令并在评分时按它取产物；
- `install`：能 `pip install -e` 的仓库用上面这条；不可安装的脚本型仓库改成
  `["{python} -m pip install -r {repo}/requirements.txt"]` 或留空；
- `python_packages`：只填「应当由仓库提供、且要被 import」的模块，写 import 名（`Pillow` 要写 `PIL`）；
  脚本型仓库留空，此时不做落点校验，仓库仍通过 `read_file` / `terminal` 使用；
- `agent_timeout_sec`：视频 / 大模型类任务建议调大，默认 1800。

### 第 4 步：预览并单题实跑

先 `--plan --task <任务ID>` 看 `planned_trials: 1`，再去掉 `--plan` 实跑。新题建议用独立实验名：
混跑会让 `manifest` 的任务列表变化，和旧结果对比不直观。

### 第 5 步：验收清单

一条接入正确的 trial 应该同时满足：

1. `setup` 成功执行 `install`，耗时在十几秒到几分钟；
2. trace 里出现 `read_file .../code_base/<Repo>/README.md`；
3. trace 里**没有** `pip install <同名包>`；
4. `verifier/result.json` 的 `verdict` 来自上游脚本，`Process` / `Result` 有明确值；
5. `metrics.prediction_bytes > 0`，且 `similarity`（若脚本输出）与真值量级相符。

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
- **单产物**：`multi_output: true` 的题需要改 `evaluate` 的产物约定。
- **Windows 依赖**：需要 apt / GPU / Linux 系统库的题跑不了；要跑得换 Docker 环境（`benchflow`
  在 Windows 上 import 失败，需 Linux 机器）。
- **外网型任务**：`Trafilatura` 这类要现场抓网页的题，失败率主要来自网络而不是模型，结果要单独看。
- **运行期覆盖**：`setup` 只在开跑前校验 import 落点，agent 中途自行 `pip install` 同名包不会被拦。
- **版本控制**：`.gitignore` 忽略 `/data/*`，本包中**只有这份 README 入库**（`.gitignore` 里为它开了
  单独例外），19 MB 仓库快照、`index.json`、`dataset.json` 与各题文件都需要按第 6 节重建；
  `datasets/gittaskbench.py` 与 2 节列出的四份配置是正常跟踪的文件。

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
