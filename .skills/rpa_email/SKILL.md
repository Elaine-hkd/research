---
name: rpa-email
description: 通过随附的 rpa_email.py 调用 RPA 任务服务接口，下发并等待"SMTP发送邮件"任务至最终结果。用于用户明确要求发送邮件，并提供或准备提供收件人、主题和正文时；不用于仅草拟、润色或预览邮件。
---

# RPA 邮件发送

通过本 Skill 目录下的 `scripts/rpa_email.py` 调用 RPA 任务服务接口（rpa_api_service），下发"SMTP发送邮件"任务并等待最终执行结果。把包含本文件的目录记为 `SKILL_DIR`，脚本位置为 `SKILL_DIR/scripts/rpa_email.py`。不要依赖调用时的当前工作目录，也不要修改脚本中固定的服务调用配置。

## 参数

运行脚本时使用以下命令行参数：

| 参数 | 必填 | 规则 |
| --- | --- | --- |
| `--receiver` | 是 | 收件人邮箱；多个地址用英文逗号 `,` 分隔 |
| `--cc` | 否 | 抄送邮箱；多个地址用英文逗号 `,` 分隔 |
| `--subject` | 是 | 邮件主题，不能为空 |
| `--body` | 是 | 邮件正文，不能为空；保留用户给出的换行 |
| `--attachments` | 否 | 附件路径；多个路径用英文分号 `;` 分隔 |

不要猜测或臆造缺失的必填参数。缺少收件人、主题或正文时，脚本返回 `status=PARAM_INVALID`（`retryable=true`）并在 message 指明缺少项：此时任务无法完成，向用户说明缺少的参数，用户补充后直接重新运行脚本即可，无需其他确认。可选参数为空时完全省略对应选项，不要传递空字符串。多个地址之间不要使用中文逗号；多个附件之间不要使用逗号。

可选的服务控制参数（一般无需使用）：`--api`（RPA 服务地址，默认环境变量 `RPA_API_BASE_URL`，兜底 `http://120.26.116.226:8686`）、`--wait`（服务端长查询秒数，默认 300，服务端上限 600）、`--poll-interval`（长查询超时后的本地轮询间隔秒数，默认 5）、`--timeout`（整体最长等待秒数，默认 1800）、`--task-id`（不下发新任务，只查询既有任务，用于超时或服务异常后继续查询真实结果，无需用户介入）。另有联调排查参数 `--dry-run`：只打印将要提交的下发请求体并退出、不下发任务，输出不是标准结果 JSON，正常执行任务时不要使用。

## 附件路径

附件路径不做存在性检查、原样传给执行器：服务端无法判断执行器机器上的路径是否有效，路径错误时任务会在执行器上失败。调用链路为：另行部署的智能体 Skill 通过 API 调用部署在阿里云公网服务器上的 rpa_api_service，rpa_api_service 调用控制器（controller）功能，把任务下发到另外单独的 Windows 服务器上的执行器执行。这些服务分布在不同服务器上，附件路径只与**执行器机器**有关，必须是执行器机器上的本地路径，调用方机器上是否存在同名文件没有意义。用户给出相对路径时，先解析为执行器机器上的明确绝对路径；无法唯一确定时判定任务无法完成，向用户说明原因（附件路径无法解析为执行器机器上的明确绝对路径），请用户提供绝对路径后重新发起请求。未经用户要求，不要读取或概述附件内容。2026-09-10 实测：附件传相对路径 `Downloads\etabBill20260904.xls`（缺盘符）时任务在执行器上执行失败（`status=EXECUTION_FAILED`，详见"典型情形与处理"情形 2），改为绝对路径 `D:\Downloads\etabBill20260904.xls` 后成功。

## 授权边界

发送邮件是外部副作用。只有当前请求明确要求"发送""立即发送"或表达同等明确的发送意图时，才运行脚本。草拟、改写、预览或准备邮件不构成发送授权；这类请求只返回内容，不运行脚本。发送指令或最终参数存在歧义时（如收件人不明确），判定任务无法完成，向用户说明歧义点，由用户澄清后重新发起请求。

一次明确的发送请求只下发一次任务。脚本调用超时、连接中断或输出无法判断时，不要自动重新下发（第一次下发可能已成功、邮件可能已发出）；先用 `--task-id` 查询既有任务的真实结果，查询确认任务未成功的，向用户报告任务无法完成及原因，由用户重新发起请求。

## 执行

使用项目配置的 Python 解释器直接运行脚本文件。保证所有用户值作为独立参数传入并进行适合当前 shell 的安全引用，不要把收件人、主题、正文或附件路径拼接成可执行的 shell 语句。脚本零第三方依赖（Python 3.7+ 即可），无需安装 tbotcontroller。

Windows PowerShell 示例：

```powershell
python "<SKILL_DIR>\scripts\rpa_email.py" `
  --receiver "alice@example.com,bob@example.com" `
  --cc "manager@example.com" `
  --subject "项目进度" `
  --body "本周项目进展见附件。" `
  --attachments "C:\Reports\progress.pdf;C:\Reports\schedule.xlsx"
```

没有抄送或附件时省略相应选项：

```powershell
python "<SKILL_DIR>\scripts\rpa_email.py" `
  --receiver "alice@example.com" `
  --subject "会议提醒" `
  --body "请于明天上午十点参加会议。"
```

路径占位符 `<SKILL_DIR>` 仅用于说明。实际执行时必须替换为当前 Skill 的绝对目录，不能原样传给脚本。正文包含引号、美元符号或多行文本时，优先使用 shell 的参数数组或等效的结构化参数传递方式，避免内容被 shell 解释。

## 结果判定

本 Skill 流程运行过程中没有人在回路：任何失败或歧义分支一律按"任务无法完成+原因"收尾（原因见 message 与下方状态判定表），不在运行过程中向用户征询确认，由用户排除原因后重新发起请求。唯一例外是 `PARAM_INVALID`（`retryable=true`，任务未下发、无副作用）：按 message 补齐参数后直接重新运行，无需用户参与。

脚本最终在 stdout 仅输出标准结果 JSON 一段（成功与失败均输出）。顶层判定字段让调用方智能体不必解析 message 文本即可决策：

| 字段 | 说明 |
| --- | --- |
| `code` | 状态码（兼容保留）：200 成功；400 参数校验失败（未下发）；500 终态失败（下发失败/执行失败/执行超时）；502 调用服务异常（结果未知）；504 等待超时未到终态（结果未知） |
| `status` | 判定结论枚举，见下方状态判定表，与 `code` 对应 |
| `success` | 布尔，`status == "SUCCESS"`，任务成败的最终判定依据 |
| `retryable` | 布尔，是否可不征得用户同意直接重跑：`PARAM_INVALID`（任务未下发、无副作用）与已知失败模式 `ATTACHMENT_NOT_FOUND`（原任务已失败、邮件未发出，修正附件路径后重跑无重复发送风险）为 `true`，其余均为 `false`（发送有外部副作用，失败后不要自动重发） |
| `next_action` | 下一步指令枚举，见状态判定表 |
| `message` | 人读结论：成败原因与后续处置；任务标识只在 `data`（失败/超时的 message 仍带 task_id 便于排查），成功时不重复携带 |
| `data` | 结构化任务信息：`task_id`、`controller_task_id`、`execution_status`；失败时附 `failure_reason`（执行失败时为从任务日志 ERROR 行提取的真实原因，形如 `邮件发送失败：[SMTP发送邮件][第52行]未能找到文件"D:\..."`，多条用`；`连接，日志取不到时退回控制器状态文案"失败"；下发失败时另附 `status=DISPATCH_FAILED`），识别到已知失败模式时附 `failure_pattern`；成功时附 `taskResult` |

成功输出样例（本流程无输出变量，`taskResult` 为 `null` 属正常现象）：

```json
{
  "code": 200,
  "status": "SUCCESS",
  "success": true,
  "retryable": false,
  "next_action": "REPORT_SUCCESS",
  "message": "成功：邮件已发送",
  "data": {
    "task_id": "rpa_email-20260910190802-c6dea3",
    "controller_task_id": "84574",
    "execution_status": "SUCCESS",
    "taskResult": null
  }
}
```

状态判定表（每个判定结果的明确含义与下一步）：

| status | code | 含义 | 下一步（next_action） |
| --- | --- | --- | --- |
| `SUCCESS` | 200 | 邮件发送任务已在执行器上执行成功，邮件已发出，终态 | `REPORT_SUCCESS`：以 `success=true` 为"邮件已发送成功"的判定依据向用户报告 |
| `PARAM_INVALID` | 400 | 参数校验失败（缺少收件人/主题/正文等），任务未下发、无副作用 | `FIX_PARAMS_AND_RERUN`：按 message 补齐参数后直接重新运行，无需用户确认 |
| `DISPATCH_FAILED` | 500 | 任务下发失败，未执行，邮件未发出，终态 | `REPORT_FAILURE`：报告任务无法完成及 `data.failure_reason`，由用户重新发起请求 |
| `EXECUTION_FAILED` | 500 | 任务已执行、失败，终态；最常见原因：附件路径不是执行器机器上存在的本地绝对路径（`failure_pattern=ATTACHMENT_NOT_FOUND`，见"典型情形与处理"情形 2 与"已知失败模式"） | 命中已知失败模式按其 `next_action` 处置（`ATTACHMENT_NOT_FOUND` → `FIX_PARAMS_AND_RERUN`，`retryable=true`，修正附件路径后直接重跑）；未命中 → `REPORT_FAILURE`：报告任务无法完成及 `data.failure_reason`，由用户重新发起请求 |
| `EXECUTION_TIMEOUT` | 500 | 任务在执行器上执行超时，终态失败 | `REPORT_FAILURE`：同上 |
| `SERVICE_UNAVAILABLE` | 502 | 调用 RPA 服务异常，任务结果未知（邮件可能已发出） | `data.task_id` 非空 → `QUERY_TASK_LATER`：勿重新下发，用 `--task-id` 查询真实结果；`data.task_id` 为 `null` → `CONTACT_ADMIN_THEN_RERUN`：任务未提交、无重复风险，管理员恢复 rpa_api_service 后重新发起请求 |
| `WAIT_TIMEOUT` | 504 | 整体等待超时，任务未到终态、仍在执行（邮件可能已发出） | `QUERY_TASK_LATER`：直接用 `--task-id <data.task_id>` 重新运行本脚本续查，无需用户介入；切勿重新下发 |

判定原则：以顶层 `success` / `status` / `next_action` 为判定依据，不要仅凭进程退出码为零就宣称邮件已送达。进程退出码与 code 对应（兼容保留）：0↔200，1↔400/500，2↔504，3↔502。

脚本头部 `FLOW_HAS_OUTPUT = False`（本流程无输出变量）：任务执行成功后不调用日志接口取 taskResult，直接以 `taskResult=null` 收尾，省一次服务往返；流程日后增加输出变量时把它改为 `True`，即恢复终态后经日志接口（`GET /api/tasks/<task_id>/log`，服务端实时透传控制器 `/taskapi/plus/log`）取流程输出的取数路径。

回复用户时发生异常、code 非零时给出简洁错误原因。不要泄露服务或 tbotcontroller 中的访问密钥、私钥、令牌等凭据。

## 典型情形与处理

### 情形 1：`status=SERVICE_UNAVAILABLE`（code=502），RPA 服务未启动

服务进程未运行时的实测输出（HTTP 层 503，脚本归一为 502）：

```json
{
  "code": 502,
  "status": "SERVICE_UNAVAILABLE",
  "success": false,
  "retryable": false,
  "next_action": "CONTACT_ADMIN_THEN_RERUN",
  "message": "调用 RPA 服务异常：调用 RPA 服务失败（http://120.26.116.226:8686/api/dispatch?wait=300）：HTTP Error 503: Service Unavailable（任务未下发或下发状态未知，本次任务无法完成；确认 rpa_api_service 已启动且网络可达后重新发起请求，若怀疑任务已提交可由管理员在控制器管理端核查）",
  "data": null
}
```

处理：这是基础设施故障，不是任务失败。`data` 为 `null`、无 `task_id`，说明任务根本没有提交，不存在邮件重复发送风险。按 `next_action=CONTACT_ADMIN_THEN_RERUN` 处理：向用户说明 RPA 服务当前不可用、本次任务无法完成，由管理员启动服务器上的 rpa_api_service 后重新发起请求，不要自动反复重试。注意区分：若 502 出现在等待阶段（`data.task_id` 非空，任务可能已下发），按 `QUERY_TASK_LATER` 改用 `--task-id` 查询真实结果，不要重新下发。

### 情形 2：`status=EXECUTION_FAILED`（code=500），流程执行失败（常见于附件路径错误）

附件传执行器上不存在的路径 `D:\Downloads\浦发银行网银流水明细2026.xls` 的实测输出（2026-09-17，message 为脚本实际文案格式）：

```json
{
  "code": 500,
  "status": "EXECUTION_FAILED",
  "success": false,
  "retryable": true,
  "next_action": "FIX_PARAMS_AND_RERUN",
  "message": "任务执行失败：邮件发送失败：[SMTP发送邮件][第52行]未能找到文件“D:\\Downloads\\浦发银行网银流水明细2026.xls”。（task_id=rpa_email-20260917162307-cec8d2，controller_task_id=84774）。附件在执行器机器上不存在：附件路径须为执行器电脑上的本地绝对路径（发送流水文件时应使用 rpa-spdb-download-detail 成功结果 data.taskResult.output_resultFile 的路径），修正 --attachments 后直接重新运行本脚本（原任务已失败、邮件未发出，无重复发送风险）",
  "data": {
    "task_id": "rpa_email-20260917162307-cec8d2",
    "controller_task_id": "84774",
    "execution_status": "FAILED",
    "failure_reason": "邮件发送失败：[SMTP发送邮件][第52行]未能找到文件“D:\\Downloads\\浦发银行网银流水明细2026.xls”。",
    "failure_pattern": "ATTACHMENT_NOT_FOUND"
  }
}
```

处理：任务已真实下发并执行过，失败即终态，邮件未发出。`failure_reason` 为任务日志 ERROR 行提取的真实原因（本例即附件文件不存在及其路径），`failure_pattern=ATTACHMENT_NOT_FOUND`、`retryable=true`：按 `next_action=FIX_PARAMS_AND_RERUN` 修正附件路径（发送流水文件时使用 rpa-spdb-download-detail 成功结果 `data.taskResult.output_resultFile` 的路径）后直接重新运行本脚本，无需用户确认——原任务已失败、邮件未发出，无重复发送风险。未命中已知模式的失败（`failure_pattern` 缺失）：向用户说明任务无法完成及 `data.failure_reason`，由用户重新发起请求，不要自动重发；需要深入定位时，凭 `data.controller_task_id` 在控制器管理端查看该任务的执行日志与录屏。

### 情形 3：`status=SUCCESS`（code=200），执行成功但无输出结果（`taskResult=null`）

本流程正常成功时的输出即上文"结果判定"的成功样例：`taskResult` 为 `null`，`next_action=REPORT_SUCCESS`。

处理：仍按 `status=SUCCESS` 判定发送成功，向用户正常报告即可。`taskResult=null` 表示流程没写输出（本流程无输出变量，属正常现象而非故障），无需排查。

## 已知失败模式

任务执行失败（`EXECUTION_FAILED` / `EXECUTION_TIMEOUT`）时，脚本自动取一次任务日志：从 ERROR 行提取真实失败原因到 `data.failure_reason`（控制器任务接口只回"失败"等状态文案），并识别已知失败模式，命中即在 `data.failure_pattern` 带回模式码、`message` 带处置指引、`next_action` 带对应下一步——调用方智能体按 `next_action` 直接执行即可，不必解析日志：

| failure_pattern | 日志特征（实测样例） | 判定含义 | 下一步（next_action） |
| --- | --- | --- | --- |
| `ATTACHMENT_NOT_FOUND` | `邮件发送失败：[SMTP发送邮件][第52行]未能找到文件“D:\...xls”。` | 附件文件在执行器机器上不存在（路径错误或非执行器本地路径） | `FIX_PARAMS_AND_RERUN`（`retryable=true`）：修正 `--attachments` 为执行器电脑上存在的本地绝对路径（发送流水文件时用 rpa-spdb-download-detail 成功结果 `data.taskResult.output_resultFile` 的路径）后直接重新运行本脚本；原任务已失败、邮件未发出，无重复发送风险 |

未命中任何已知模式的失败（`failure_pattern` 缺失），按状态判定表 `EXECUTION_FAILED` 行处置。新增已知失败模式：在脚本头部 `KNOWN_FAILURE_PATTERNS` 列表追加 `(日志正则, 模式码, 处置指引文案, next_action)` 即可，无需改动其他代码。

## taskResult 输出参数

输出参数不预置固定清单，按实际执行结果动态理解。判定规则：`data.taskResult` 为对象且其层级下存在 `output_` 开头的键，即代表本次执行有输出参数——每个 `output_XXX` 键为一个输出参数，按键名与实际值向用户解读，不要臆造含义；`taskResult` 为 `null` 或不含 `output_` 开头的键时，本次执行无输出参数，向用户报告任务成败即可。

键名语义约定（遇到即按此解读，未列出的按键名与值原样转述）：

| 键名 | 含义 |
| --- | --- |
| `output_resultFile` | 流程输出文件路径，出现即表示流程有文件输出。值为执行器机器上的本地绝对路径，多个文件用英文逗号 `,` 分隔 |

本流程（SMTP发送邮件）设计上无输出变量，正常成功时 `taskResult` 为 `null`（情形 3）。
