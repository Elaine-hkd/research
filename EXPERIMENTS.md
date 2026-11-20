# 实验表（第一版）

本文件是「任务 1：分析数据集 → 写第一张实验表 → 跑基线」的交付物。所有数字直接取自 `logs/exp/**/results.jsonl`，未手工修饰；口径见文末。

- 统计时间：2026-11-20（#1–#23 于 2026-11-19，#24–#36 于 2026-11-20 补入）
- 登记 trial：36 条（3 个数据集 / 2 个 harness / 3 个模型）
- 通过（reward = 1.0）：16 条
- 其中 2 条是**重跑验证批次**（`skillsbench-retry`）：用来区分「偶发抖动」和「稳定缺陷」
- **#24–#27 是 GitTaskBench 语义有效的 trial**：资源包已含 `code_base/PDFPlumber` 仓库快照。#9–#15 的资源包缺仓库快照，实际测的是「调 PyPI 上的库」，不能与上游结果比较（见 2 节末尾说明）
- **#28–#33 是技能增益对照**：同一批任务各跑 `no-skill` 与 `with-skill`，技能由生成器读同一份仓库产出。结论是**负面结果**——三题里两题基线已满分、一题卡在判分缺陷，技能增益无法观测（见 3.1 节）
- **#34–#36 是容器模式的链路验证**：同一批三题在 Linux 容器内跑通，结果与 #24–#27 的本机结论一致（1.0 / 0.0 / 1.0），用于确认「执行环境换了、评分链路不变」，不是技能对照

## 1. 数据集概览

| 数据集 | 上游任务量 | 已接入 | 资源包 | 评分方式 | 环境形态 |
|---|---|---|---|---|---|
| SkillsBench | 101 | 6 个任务跑过 trial | 611 MB，231 个技能 | verifier 脚本 + 单测 | Docker（benchflow） |
| GitTaskBench | 54 | **3 个**（`PDFPlumber_01/02/03`，同一仓库的全部题） | 19 MB（含 146 文件的仓库快照，三题共用） | 上游 `test_script.py` 输出 JSONL | 本机 `LocalEnvironment`（#9–#33）／Linux 容器（#34–#36） |
| email-demo | 1 | 1（全部） | 极小 | RPA 服务端回执 | 本机 + 技能注册中心 |

说明：GitTaskBench 上游有 54 个任务、18 个仓库，当前接入了 `PDFPlumber` 仓库的全部 3 题（01/02/03），共用一份仓库快照；其余 51 题、17 个仓库未接入。SkillsBench 资源包完整（101 个任务目录），只是按需挑选任务跑。

## 2. trial 明细

阶段耗时单位秒，`-` 表示该阶段未执行或为 0。`reward` 为 `[0,1]`，`None` 表示该 trial 未进入评分。

| # | 数据集 | 实验名 | 任务 | harness | 模型 | 状态 | reward | 环境 | agent | 评分 |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | email_demo | email-final | send-introduction-email | openhands | deepseek/deepseek-flash | completed | 0.0 | - | 33 | 0 |
| 2 | email_demo | email-full-flow | send-introduction-email | openhands | deepseek/deepseek-flash | error | None | - | - | - |
| 3 | email_demo | email-full-flow-2 | send-introduction-email | openhands | deepseek/deepseek-flash | completed | 0.0 | - | 24 | 0 |
| 4 | email_demo | email-full-flow-3 | send-introduction-email | openhands | deepseek/deepseek-flash | completed | 0.0 | - | 21 | 0 |
| 5 | email_demo | email-full-flow-4 | send-introduction-email | openhands | deepseek/deepseek-flash | completed | 0.0 | - | 26 | - |
| 6 | email_demo | email-full-flow-5 | send-introduction-email | openhands | deepseek/deepseek-flash | completed | 0.0 | - | 40 | 0 |
| 7 | email_demo | email-full-flow-6 | send-introduction-email | openhands | deepseek/deepseek-flash | completed | 0.0 | - | 56 | 0 |
| 8 | email_demo | email-trace-demo | send-introduction-email | openhands | deepseek/deepseek-flash | completed | **1.0** | - | 26 | - |
| 9 | gittaskbench | pdfplumber-code | PDFPlumber_01 | dsh | deepseek-v4-flash | error | None | - | 2 | - |
| 10 | gittaskbench | pdfplumber-run1 | PDFPlumber_01 | dsh | deepseek-v4-flash | error | None | - | 6 | - |
| 11 | gittaskbench | pdfplumber-run2 | PDFPlumber_01 | dsh | deepseek-v4-flash | error | None | - | 9 | - |
| 12 | gittaskbench | pdfplumber-run3 | PDFPlumber_01 | dsh | deepseek-v4-flash | completed | 0.0 | - | 91 | 0 |
| 13 | gittaskbench | pdfplumber-run4 | PDFPlumber_01 | dsh | deepseek-v4-flash | completed | **1.0** | - | 258 | 0 |
| 14 | gittaskbench | pdfplumber-smoke | PDFPlumber_01 | dsh | deepseek-v4-flash | error | None | - | 0 | - |
| 15 | gittaskbench | post-sync-smoke | PDFPlumber_01 | dsh | deepseek-v4-flash | completed | **1.0** | - | 17 | 0 |
| 16 | skillsbench | skillsbench-openhands-demo | 3d-scan-calc | openhands | deepseek/deepseek-flash | completed | **1.0** | 303 | 71 | 11 |
| 17 | skillsbench | skillsbench-stability | court-form-filling | openhands | deepseek/deepseek-flash | completed | **1.0** | 596 | 550 | 10 |
| 18 | skillsbench | skillsbench-stability | debug-trl-grpo | openhands | deepseek/deepseek-flash | completed | 0.0 | 657 | 382 | 10 |
| 19 | skillsbench | skillsbench-stability | python-scala-translation | openhands | deepseek/deepseek-flash | error | None | 508 | 371 | - |
| 20 | skillsbench | skillsbench-stability | weighted-gdp-calc | openhands | deepseek/deepseek-flash | error | None | 478 | - | - |
| 21 | skillsbench | skillsbench-stability | lab-unit-harmonization | openhands | deepseek/deepseek-flash | completed | **1.0** | 153 | 635 | 13 |
| 22 | skillsbench | skillsbench-retry | weighted-gdp-calc | openhands | deepseek/deepseek-flash | completed | **1.0** | 551 | 115 | 10 |
| 23 | skillsbench | skillsbench-retry | python-scala-translation | openhands | deepseek/deepseek-flash | error | None | 7 | 602 | - |
| 24 | gittaskbench | gtb-repo-baseline | PDFPlumber_01 | dsh | gpt-6-sol | completed | **1.0** | - | 46 | 0.3 |
| 25 | gittaskbench | gtb-pdfplumber-3tasks | PDFPlumber_01 | dsh | gpt-6-sol | completed | **1.0** | - | 93 | 0.2 |
| 26 | gittaskbench | gtb-pdfplumber-3tasks | PDFPlumber_02 | dsh | gpt-6-sol | completed | 0.0 | - | 150 | 0.2 |
| 27 | gittaskbench | gtb-pdfplumber-3tasks | PDFPlumber_03 | dsh | gpt-6-sol | completed | **1.0** | - | 78 | 0.2 |
| 28 | gittaskbench | gtb-skill-tune | PDFPlumber_01 | dsh | gpt-6-sol | completed | **1.0** | - | 53 | 0.2 |
| 29 | gittaskbench | gtb-skill-tune | PDFPlumber_01 | dsh | gpt-6-sol | completed | **1.0** | - | 40 | 0.2 |
| 30 | gittaskbench | gtb-skill-tune | PDFPlumber_02 | dsh | gpt-6-sol | completed | 0.0 | - | 123 | 0.2 |
| 31 | gittaskbench | gtb-skill-tune | PDFPlumber_02 | dsh | gpt-6-sol | completed | 0.0 | - | 179 | 0.2 |
| 32 | gittaskbench | gtb-skill-holdout | PDFPlumber_03 | dsh | gpt-6-sol | completed | **1.0** | - | 71 | 0.2 |
| 33 | gittaskbench | gtb-skill-holdout | PDFPlumber_03 | dsh | gpt-6-sol | completed | **1.0** | - | 92 | 0.2 |
| 34 | gittaskbench | gtb-docker-3tasks | PDFPlumber_01 | dsh | gpt-6-sol | completed | **1.0** | 6.9 | 55 | 6.1 |
| 35 | gittaskbench | gtb-docker-3tasks | PDFPlumber_02 | dsh | gpt-6-sol | completed | 0.0 | 5.8 | 149 | 6.8 |
| 36 | gittaskbench | gtb-docker-3tasks | PDFPlumber_03 | dsh | gpt-6-sol | completed | **1.0** | 7.0 | 82 | 6.9 |

**#9–#15 语义无效**：当时的资源包把 `code_base` 置为 `null`，`prepare_task` 只复制 `assets/`，`setup_environment` 又从 PyPI 装了同名 `pdfplumber`。agent 全程没读过任何仓库（`post-sync-smoke`：3 次 terminal、0 次 read_file，最终自述“使用工具 pdfplumber 0.11.10”），题面的“先读代码库 README”“严禁自行另写实现”无法执行。**所以那两条 `reward=1.0` 只能证明“会调库”，不能作为 GitTaskBench 基线**，保留在表里是为了记录接入过程。

**#25–#27 是同仓库三题同批运行**（资源包已含仓库快照，三题共用 `code_base/PDFPlumber`）。三条的 trace 都先 `read_file code_base/PDFPlumber/README.md`、再 grep 源码（`page.py` / `table.py`），全程没有自行 `pip install pdfplumber`，符合验收口径。

**#26 是假阴性，不是模型失败**：上游 `groundtruth/PDFPlumber_02/gt.csv` 里有 2 个空行（第 38、82 行），评分脚本按索引逐行比对，从第 38 行起整表错位；同时 gt 保留了提取时的字符间距（`0 3 / 2 5 / 2 0 16`），而 agent 输出的是规范化日期（`03/25/2016`）。我把 agent 的 `output.csv` 与真值按公司名对齐后，**剔除空行并忽略单元格内空白，79 行 × 7 列全部一致（100%）**。43.22% 反映的是上游真值与评分口径的缺陷，不是“没做对”，详见第 4 节 E 类。

**#24** 在补入仓库快照后重跑：`prepare` 0.9 s（复制 146 文件 / 19 MB）、`setup` 13.5 s（`pip install -e` 装仓库并校验 import 落点在仓库内）、agent 46 s。轨迹里先读 `code_base/PDFPlumber/README.md`，再 grep `pdfplumber/page.py` 的 `extract_text`，最后一次命令直接 `import pdfplumber` 完成提取——**没有执行任何 `pip install`**，`import` 到的是仓库内的 0.11.6。评分仍由上游 `test_script.py` 给出：`Process=true`、`Result=true`、`similarity=100.00%`。

第 20 行与第 22 行是同一个任务的两次尝试：第一次卡在 `apt-get` 拉包时清华 TUNA 返回 `502`，重跑一次就过了（环境阶段 551 s）。**同一份配置、同一个任务、只重跑一次，结论从「失败」变成「通过」**，说明环境阶段的外部依赖抖动必须靠重试消化。

第 19 行与第 23 行也是同一任务：第一次是 `litellm.Timeout`（120 s），重跑时环境镜像已缓存（7 s），agent 跑满 600 s 预算后被判 `agent_timeout`。两次失败原因不同，但都指向**该任务在这台机器上单次执行成本过高**，而不是偶发抖动（见第 4 节 G 类）。

## 3. 按数据集汇总

| 数据集 | trial | 通过 | reward=0 | error | 进入评分<sup>注1</sup> | 环境均值<sup>注2</sup> | agent 均值<sup>注2</sup> |
|---|---|---|---|---|---|---|---|
| SkillsBench | 8 | 4 | 1 | 3 | 5/8 | 407 s（8 次） | 389 s（7 次） |
| GitTaskBench | 20 | 11 | 5 | 4 | 16/20 | 6.6 s（3 次，仅容器模式） | 80 s（20 次）<sup>注3</sup> |
| email-demo | 8 | 1 | 6 | 1 | 7/8 | 本机执行，无此阶段 | 32 s（7 次） |

<sup>注1</sup>「进入评分」= 有多少条 trial 拿到了 reward（非 `None`）。GitTaskBench 的前 4 条是**接入踩坑期**造成的：第一次跑通花了 4 次尝试（`--plan` 报错 → 401 → 工具通道失败 → 成功），稳定后单次只需 17 s。#24–#36 共 13 条全部进入评分，无一 error。

<sup>注2</sup>均值只统计该阶段**实际执行过**的 trial，因此分母各不相同。例如第 20 行的 `weighted-gdp-calc` 在环境阶段就失败，没有产生 agent 耗时；重跑（第 22 行）才把这一格补上。

<sup>注3</sup>第 24–27 行是补入仓库快照后的重跑，模型换成 `gpt-6-sol`，与 #9–#15 的 `deepseek-v4-flash` 不同；两者除“仓库在场”外还多了模型差异，因此不能直接读成“补仓库让耗时变化”。第 25 行与第 24 行同为 `PDFPlumber_01`，agent 耗时 46 s → 93 s，属同配置下的正常波动（#24 只跑一次，不足以定基线）。第 34–36 行是容器模式，环境阶段仅统计有该阶段的 trial。

## 3.1 技能增益对照（#28–#33）

设计：技能由 `datasets/skillgen.py` 读 `code_base/PDFPlumber` 的 README、`docs/` 与公开 API 摘要生成，
不含真值与评分脚本。`PDFPlumber_01/02` 是校准集（技能可迭代），`03` 是留出集（技能定稿后只跑一次）。
两组共用同一条评分链路，唯一变量是有没有注入 `skills/PDFPlumber/SKILL.md`。

| 任务 | 集合 | no-skill | with-skill | with-skill 是否读了技能 | agent 调用数（ns → ws） |
|---|---|---|---|---|---|
| `PDFPlumber_01` | 校准 | **1.0**（53 s） | **1.0**（40 s） | **否** | 7 → 2 |
| `PDFPlumber_02` | 校准 | 0.0（123 s） | 0.0（179 s） | 是 | 16 → 20 |
| `PDFPlumber_03` | 留出 | **1.0**（71 s） | **1.0**（92 s） | 是 | 17 → 17 |

**结论：这批题测不出技能增益，原因是「天花板 + 判分缺陷」双重挤压。**

1. `01` 与 `03` 在 `no-skill` 下已经满分（都是 100% / F1 100%），提升空间为零。`01` 的 `with-skill`
   甚至只在第 3 个动作才拿到答案，agent **根本没读技能**（2 次工具调用：`cat README.md` + 一条 python 一行流），
   所以这一格是「技能给了但没用」，不能读成「技能没用」。
2. `02` 的 `with-skill` 确实读了技能（第 3 个调用 `read_file skills/PDFPlumber/SKILL.md`），随后按技能指引
   去仓库 grep `extract_tables`，但仍被判 0.0——它卡的是 #26 已经定位的上游真值缺陷（`gt.csv` 含空行导致
   整表错位），技能再准也补不了判分口径。这一格证明的是「技能无法覆盖评分链路的缺陷」，不是模型能力。
3. 唯一可读的效率信号：`01` 的工具调用从 7 次降到 2 次，但 n=1，且任务本身太简单，不足以作为结论。

**要做技能增益实验，必须先换掉评测题**：需要「`no-skill` 明显吃力、但 `with-skill` 有明确可抄的路径」的题。
当前 `PDFPlumber` 三题不满足该条件，`SpeechBrain`（5 个实例）或需要读懂源码改行为的题才合适。

## 4. 失败分类（≥5 类）

这是任务书要求的「理清 ≥5 类问题」，实际归纳出 8 类（A–G，其中 D 与 D' 同源）。下面每一类都有本次实验中真实发生过的事件作为证据，不是推测。

| 类别 | 典型现象 | 实际根因 | 归属 |
|---|---|---|---|
| **A. 模型端点与密钥** | `401`；`SSE stream ended without [DONE]`；`404` 拿到 HTML | `base_url` 缺 `/v1`，dsh / litellm 各自拼接路径；`.env` 里的 key 是**中转站**的，而配置指向 DeepSeek 官方端点；上游默认模型 `qwen3.8-max-0902` 在本机 key 下 `503 model_not_found` | 配置 |
| **B. 执行环境与平台差异** | 工具全部 `exit 1`、stderr 乱码；`WinError 267` | Windows 的 `bash` 是 WSL 启动器，而框架硬编码 `["bash","-c",cmd]` 且忽略 `bash_executable`；agent 按 Linux 语境传 `cwd="/workspace"`，在 Windows 被解析成 `C:\workspace` | 框架 bug |
| **C. 依赖与平台兼容** | `ModuleNotFoundError: No module named 'fcntl'` | benchflow 有两处无条件 `import fcntl`（POSIX 专有），但其余代码都写了 `sys.platform == "win32"` 分支，属上游疏漏 | 上游依赖 |
| **D. 资源包与数据契约** | prepare 阶段 `FileNotFoundError: skills\SKILL.md` | `index.json` 的 `skills_dir` 写成 `skills`（父目录），而上游文档与适配器实现都要求指向技能目录本身 | 数据 |
| **D'. 资源包行尾污染** | 反作弊检查报「文件被修改」，但 agent 从未写入 | 资源包被整体转成 CRLF：3476 个文件里 3359 个含 CRLF，217 个 `.sh`、604/607 个 `.py` 是纯 CRLF 行尾。`train_grpo.py` 的 LF 版本哈希与 verifier 期望值完全一致，是 CRLF 导致哈希失配 | 数据 |
| **E. 评分链路** | 产物存在却 `reward=0` | ①评分器读相对路径，在临时目录下解析错误（加 `.resolve()` 后变回 1.0）；②agent 声明「环境坏了」只给代码不给文件；③**上游真值缺陷导致假阴性（#26）**：`PDFPlumber_02` 的 `gt.csv` 含 2 个空行，评分脚本按索引逐行比对 → 从第 38 行起整表错位；且真值保留提取时的字符间距（`0 3 / 2 5 / 2 0 16`），与任何规范化输出都不相等。对齐后 79 行 × 7 列 100% 一致，仍只得 43.22% | 适配器 / agent / **数据集** |
| **F. 外部服务与网络** | `502 Bad Gateway`；`litellm.Timeout 120s`；`403` | 清华 TUNA 镜像拉 `libreoffice` 时 502（重跑即过）；中转站模型调用 120 s 硬超时；RPA 服务端偶发 403，同一参数走注册中心 HTTP 接口却能成功 | 外部 |
| **G. 任务预算与执行成本错配** | `TimeoutError: OpenHands 超过了 600.0 秒的智能体时间预算` | `python-scala-translation` 单次 `sbt -batch test` 实测约 400 s，而任务元数据自带的 `agent.timeout_sec: 600` 只够跑一次编译+测试；该任务声明 1 CPU，本机执行成本与预算不匹配 | 数据集 / 机器 |

## 5. 观察

1. **环境构建是长尾瓶颈。** SkillsBench 环境阶段 8 次合计 3253 s、均值 407 s、最大 657 s，与 agent 阶段合计（2726 s）相比已占全部耗时的约 54%；单条 trial 里最高占到 62%（`debug-trl-grpo`：657 s / 1049 s）。但**镜像一旦缓存，环境阶段可以降到 7 s**（重跑 `python-scala-translation` 时复用了上一轮的镜像）。所以压缩总时长的关键是镜像复用，不是升级机器。
2. **同一个任务重跑结果可以完全不同。** `PDFPlumber_01` 在 7 次尝试里依次经历了 `ValueError`（工具集含注册中心工具但环境未配 `registry_url`）→ SSE 断流 → 两次工具通道失败 → `reward=0`（agent 声称环境坏了，只给代码不给文件）→ `reward=1.0`，最终稳定在 17 s 通过。失败次数反映的是**接入工作量**，不是模型能力。但这 7 次都跑在缺仓库快照的包上，通过与否都不代表 GitTaskBench 要测的能力（见 2 节说明与观察 6）。
3. **`debug-trl-grpo` 的 0 分不能算模型失败。** 三个 bug 里 agent 修好了两个（0.35 + 0.25），第三个单测 `test_decode_handles_incomplete_think` 确实没过；但因 CRLF 触发的反作弊失败直接把总分清零。真实分数应在 0.6 左右。
4. **email-demo 的 6 次 `reward=0` 与 1 次 `reward=1.0` 差异在评分口径。** `reward_basis = rpa_service_success`，只有 RPA 服务端返回成功的 `task_id` 才算过；`recipient_delivery_verified` 始终为 `false`，说明当前无法验证收件人是否真正收到邮件。
5. **重跑是必要的实验动作，不是补救。** `weighted-gdp-calc` 第一次 `error`、重跑 `reward=1.0`；`python-scala-translation` 两次 `error` 但原因不同（一次网络超时、一次预算耗尽）。前者说明「失败」里混着可自愈的抖动，后者说明「同一个失败标签」可能掩盖两种完全不同的原因——**只按 status 统计会失真，必须看 `failure_type` 和根因**。
6. **资源包丢掉任务本体时，链路照样能满分通过。** GitTaskBench 的题面要求「先读仓库 README」「严禁自行重写实现」，但 #9–#15 的包里 `code_base` 为 `null`，`setup_environment` 又用 `pip install` 从 PyPI 装了同名库，于是 agent 三步就交付了答案。**这类缺陷不会报错，只会静默改变任务语义**，跑得越顺越危险。修法不是补 instruction，而是把契约做成硬约束：`prepare_task` 缺仓库直接失败、`setup_environment` 校验 `import` 落点必须在仓库内，缺一不可。
7. **同一仓库的三道题，分数差异来自题目本身而不是链路。** `PDFPlumber` 三题共用一份仓库快照、同一条适配器和同一个模型，结果却是 1.0 / 0.0 / 1.0，agent 耗时 93 / 150 / 78 s。#27（抽邮箱）只需 `extract_text` + 正则；#25（抽第一页文本）只需 `extract_text`；#26（前两页表格合并成 CSV）要处理跨页表头、合并单元格与字符间距。**三题的轨迹都读了 README 和源码**，所以差异是任务复杂度与判分口径，不是接入质量——这正是「同仓库多题」对照的价值：固定仓库、固定链路，只看题目。
8. **判分口径比模型能力更容易制造 0 分。** #26 的 agent 输出与真值逐格一致（对齐后 100%），却因为上游 `gt.csv` 里两个空行造成整表错位、以及真值保留字符间距，被判 43.22%。若不看产物只读 `verifier/result.json`，会直接得出「模型做不了表格抽取」的错误结论。**GitTaskBench 的分数必须配合产物复核**，尤其是在上游真值质量未知的情况下。
9. **天花板效应会让技能实验白跑。** #28–#33 里三题中有两题（`01`、`03`）在 `no-skill` 下已经满分，`with-skill` 再怎样也只能打平。**做「技能是否有用」的对照，必须先筛掉基线已饱和的题**，否则跑出来的全是 0 delta，且无法判断是技能无效还是任务太简单。筛选标准应当前置：先跑 no-skill 基线，`reward=1.0` 的题直接进不了增益实验。
10. **「给了技能」与「用了技能」是两件事，必须分开记录。** #29 提供了技能但 agent 只用了 2 次工具调用就交卷，**全程没读 `SKILL.md`**；#31 与 #33 则确实读了。若只看 `condition=with-skill` 就统计，会把「未使用」混进「使用后无效」。所以 `evaluate` 额外记录 `skill_catalog` 与 `skill_files_read`（从 tool-calls 里解析），**条件标签 ≠ 干预发生**。
11. **换执行环境不改判分链路，也不改结论。** #34–#36 与 #24–#27 是同批题、同模型、同一条上游 `test_script.py`，唯一差别是本机 Git Bash 换成 Linux 容器：三题分数完全相同（1.0 / 0.0 / 1.0），`PDFPlumber_01` 的 `similarity` 都是 100%，agent 也都在容器内先读 README 再 grep 源码。代价是每 trial 多 16–20 s（19 MB 任务目录复制 + `pip install -e`）与一次性的镜像构建；换来的是 `fcntl`、WSL bash、`C:\workspace`、CRLF 行尾这些干扰全部消失——**第 4 节的 B、C、D' 三类失败因此可以被判定为接入噪声，而不是任务难度**。要验证这一点必须比对同一批题，只跑容器模式无法区分「环境变干净了」和「题目变简单了」。

## 6. 口径与限制

- **reward**：`[0,1]`。SkillsBench 由任务的 verifier 脚本给出；GitTaskBench 由上游 `test_script.py` 的 JSONL 输出解析；email-demo 由 RPA 服务端回执判定。三者尺度不同，**不能横向比较数值大小**，只能比较各自的通过与否。
- **条件**：`repeats = 1`，即每个任务只跑一次，没有方差估计。表中「通过」不等于稳定通过。重跑批次（#22/#23）是**定性验证**，不是重复实验，不能用于计算方差。
- **技能条件**：SkillsBench 走 `with-skill`（注册中心扫描到 231 个技能，环境只暴露该任务需要的子集）。按 `EXPERIMENT_PRINCIPLES.md` 第 3 节，`with-skill` 的得分**不能**用于推断技能带来的增益，需要另行设计 `no-skill` 对照。GitTaskBench 自 #28 起支持成对条件（`no-skill` / `with-skill`），是本表唯一能直接读 delta 的一组；但三个任务的 delta 全为 0，原因见 3.1 节。
- **模型**：GitTaskBench 的 #9–#15 用 `deepseek-v4-flash`（dsh 原生路由），#24–#33 用 `gpt-6-sol`（同一 dsh 路由，`intlapi` 端点）；技能生成器用 `deepseek/deepseek-flash`（litellm + `uniapi` 端点），只生成技能、不参与解题；其余 trial 用 `deepseek/deepseek-flash`。同名模型的不同路由需要核对服务端实际版本，本表未做该核对。
- **执行环境**：GitTaskBench 的 #9–#33 跑在本机 `LocalEnvironment`（Windows + Git Bash），#34–#36 跑在 Linux 容器（共享镜像、CPU、可联网、评分脚本在容器内执行）。两种模式共用同一条评分链路；自 #34 起 `evaluate` 的 metrics 里带 `sandbox` 字段（更早的 trial 没有这个字段）。分数可比，但环境阶段耗时不可比。
- **本地补丁**：本机 Windows 缺少 `fcntl`，在 `.venv/Lib/site-packages/fcntl.py` 放了兼容层，否则 benchflow 无法 import。该补丁不在仓库内，`uv sync` 后可能被清除，复现时需先确认。容器模式不依赖这份补丁。
- **复现**：结果目录 `logs/exp/<数据集>/<harness>/<模型>/<实验名>/`，其中 `summary.json` 是聚合、`results.jsonl` 是逐 trial 记录、`trials/<trial_id>/result.json` 是单次终态。
