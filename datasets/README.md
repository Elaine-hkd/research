# 数据集资源包

第一次接入数据集时，请先阅读 [资源包转换指南：以 email-demo 为例](CONVERTING_DATASETS.md)。
该指南从原始任务、文件整理、适配接口讲到实验配置和验证；本文用于查阅当前包格式与迁移约定。

每个数据集使用一个独立资源目录。框架通过目录根部的 `dataset.json` 和 `index.json`
识别资源包，实验配置通过 `dataset.package` 显式选择目录。资源包可以单独搬动，
也可以放在仓库之外；资源路径必须相对包根目录，不能引用包外文件或链接。

目前的两个包是：

| 资源包 | 任务格式 | 内容 |
| --- | --- | --- |
| `data/skillsbench/` | `research-skillsbench-v1` | 原有 101 个 SkillsBench 任务及其资源 |
| `data/email-demo/` | `research-email-demo-v1` | 一个 RPA 邮件任务、rpa-email 技能和 desktop-hello 示例技能 |

## 两个固定入口

`dataset.json` 描述整份包，必需字段如下：

```json
{
  "schema_version": 1,
  "id": "skillsbench",
  "version": "r1",
  "task_format": "research-skillsbench-v1"
}
```

`schema_version` 是包描述格式版本，`version` 是这批资源的版本。任务、技能、评分器或
输入数据变化后应更新资源版本。`task_format` 必须与配置的 loader 所支持的格式一致；
框架不会根据包中的字符串导入代码。可用 `provenance` 附加来源信息，两个现有包记录了
抽取前的 research 提交。固定索引名为 `index.json`，无需在包描述中重复配置。

`index.json` 的公共部分是 `schema_version: 1` 和非空 `tasks` 列表，每项必须有唯一且
可用于路径的 `id`。若提供 `task_count`，数值须与列表长度一致。其余任务字段由相应
适配器解释。公共读取只检查两个入口文件及基本字段，不要求大型素材已经下载。

SkillsBench 的一项索引示例：

```json
{
  "schema_version": 1,
  "tasks": [
    {
      "id": "3d-scan-calc",
      "task_file": "tasks/3d-scan-calc.md",
      "environment_dir": "environment/3d-scan-calc",
      "verifiler_dir": "verifier/3d-scan-calc",
      "oracle_dir": "oracle/3d-scan-calc",
      "skills_dir": "skills/3d-scan-calc",
      "skills": [
        {"name": "mesh-analysis", "relative_path": "mesh-analysis"}
      ],
      "extra_paths": []
    }
  ]
}
```

`verifiler_dir` 暂沿用现有字段名，目录使用 `verifier/`。SkillsBench 包内还有 `assets/`，
索引的 `extra_paths` 保留五个任务所需的额外资源。`source_task_dir` 是上游相对位置，
用于溯源，不作为本地资源入口。题目、Dockerfile、评分器和二进制素材按原内容迁移，
技能副本也先完整保留。

邮件包的索引只需一个任务项：

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

收件人、主题、指令和时限继续保存在原 TOML 任务文件中；评分仍由
`datasets.email_demo:evaluate` 读取工具执行证据。`desktop-hello` 供本地环境示例使用，
不增加新的邮件实验任务。

## 选择与准备

`config/datasets/skillsbench.toml` 中的资源包路径相对该 TOML 文件解析：

```toml
[dataset]
package = "../../data/skillsbench"
loader = "datasets.skillsbench:load_tasks"
prepare = "datasets.skillsbench:prepare_task"
setup = "datasets.skillsbench:setup_environment"
```

将完整目录搬到别处时，只修改 `package`。抽样、忽略列表和随机种子继续放在数据集
实验配置中；模型、环境、重复次数和输出目录仍由各自组件配置负责。

执行流程是：读取包描述与索引 → 用适配器加载任务 → 排除和抽样 → 准备选中任务的副本
→ 创建环境 → 注入技能 → 执行和评分。查看计划需要题目文件；未选任务的大型环境素材
不在此时检查，选中任务缺少资源时在准备阶段明确失败。框架目前不自动下载缺失资源。

`datasets/package.py` 负责包描述、公共索引校验和路径边界；各适配器负责具体任务格式。
底层 `materialize_skillsbench_task(index, id, destination)` 继续接受索引路径，但索引必须是
标准包的 `index.json`。任务元数据转换、镜像源设置、worker runtime 注入和技能隔离
继续作用于试验副本，包内原件不修改。邮件任务准备阶段也会保存实际任务和技能副本。

实验配置快照记录包 ID、版本、格式和两个入口的 SHA-256；任务准备会复核入口与题目
哈希，阻止计划生成后切换资源定义。运行期间应保持源数据稳定，当前不对所有大型素材
执行全量哈希。历史实验的源路径与本次迁移前不同，请使用新实验名，已有结果可继续查看。

## 技能注册中心与验证

注册中心仍是独立服务。运行哪个数据集，就让其扫描哪个包的 `skills/`：

```powershell
Set-Location D:\Projects\research
$env:SKILLS_DIR = (Resolve-Path .\data\skillsbench\skills).Path
uv run --project ..\skill_registry skill-registry
```

邮件示例将路径改为 `.\data\email-demo\skills`；本地 desktop-hello 示例也使用该包。
切换目录后重新启动服务，或在环境配置中指定对应的独立服务地址。设置步骤仍按任务
限制可用技能，服务提供的正文和资源必须与所选包一致。

在项目依赖已经准备好的 Python 3.12+ 环境中，从 research 根目录分别预览两个实验：

```powershell
.\.venv\Scripts\python.exe -X utf8 -m evaluation --dataset config\datasets\skillsbench.toml --environment config\environments\skillsbench.toml --agent config\agents\openhands.toml --evaluation config\evaluation\skillsbench.toml --name "skillsbench-package-check" --plan
.\.venv\Scripts\python.exe -X utf8 -m evaluation --dataset config\datasets\email-demo.toml --environment config\environments\email-demo.toml --agent config\agents\openhands-email.toml --evaluation config\evaluation\email-demo.toml --name "email-package-check" --plan
```

预期分别为 10 个任务、30 个 trial，以及 `send-introduction-email` 一个任务、一个 trial；
输出同时显示 `dataset_package` 和 `dataset_identity`。预览不调用模型、注册中心或
RPA 服务，也不启动 Docker。移除 `--plan` 后会调用付费模型；邮件实验会真实发信。

本次只调整资源组织和加载入口，未启用 LFS、发布资源包或重写 Git 历史。
