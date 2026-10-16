#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SMTP 发送邮件 —— 构建完整下发配置，通过 RPA 任务服务接口下发并等待最终结果。

本脚本自包含全部流程知识（flowId、任务参数映射、下发基础配置）：命令行
参数校验后组装为控制器下发配置，调用 rpa_api_service（默认
http://120.26.116.226:8686，可用 --api 或环境变量 RPA_API_BASE_URL 指定）
的通用下发接口 POST /api/dispatch。脚本一次调用即为"长查询"：服务端下发
任务后由其后台轮询器跟踪执行状态，到终态（SUCCESS / FAILED / TIMEOUT）
直接返回最终结果；长查询超时未终态时自动转为本地轮询，整体超时后可凭
task_id 用 --task-id 继续查询，切勿直接重发（邮件可能已发出）。

执行成功且 FLOW_HAS_OUTPUT 为 True 时随即调用任务日志接口（GET
/api/tasks/<task_id>/log，服务端实时透传控制器 /taskapi/plus/log?taskId=xxx；
2026-09-10 实测任务成功后日志即可获取、无需延迟等待），从日志文本解析
流程写入的 {"taskResult":{...}} 标准输出对象（任务记录里的 basicTask.log
字段经实测不稳定，取流程输出一律走日志接口）。FLOW_HAS_OUTPUT 为 False
（2026-09-12 起正式版流程只有输入没有输出变量）时跳过日志获取，直接以
taskResult=null 收尾，省一次服务往返（含日志为空时的重试等待）。执行失败
时取一次任务日志：控制器任务接口对失败只回"失败"等状态文案，真实原因在
日志 ERROR 行（如"未能找到文件"D:\...""），提取为 data.failure_reason；
同一份日志再识别已知失败模式（KNOWN_FAILURE_PATTERNS），命中即在 data 带
failure_pattern、message 带处置指引、next_action 带对应下一步指令。

标准结果结构（stdout 仅输出这一段 JSON，成功与失败均输出，供调用端解析；
进度与人读结论不另行打印，结论与处置提示并入 message，任务标识只在
data——成功时不重复携带，失败/超时时随 message 带出便于排查）：
  {"code":200, "status":"SUCCESS", "success":true, "retryable":false,
   "next_action":"REPORT_SUCCESS", "message":"成功：...",
   "data":{"task_id":..., "controller_task_id":..., "execution_status":"SUCCESS",
           "taskResult":null}}
顶层判定字段（调用方智能体按这些字段决策下一步，不必解析 message 文本）：
  code         200 成功；400 参数校验失败（未下发）；500 终态失败（下发失
               败 / 执行失败 / 执行超时）；502 调用服务异常（结果未知）；
               504 等待超时未到终态（结果未知）
  status       判定结论：SUCCESS / PARAM_INVALID / DISPATCH_FAILED /
               EXECUTION_FAILED / EXECUTION_TIMEOUT / SERVICE_UNAVAILABLE /
               WAIT_TIMEOUT
  success      即 status == "SUCCESS"
  retryable    是否可不征得用户同意直接重跑：PARAM_INVALID（任务未下发、
               无副作用）及已知失败模式命中（ATTACHMENT_NOT_FOUND →
               FIX_PARAMS_AND_RERUN，原任务已失败、邮件未发出，修正附件
               路径重跑无重复发送风险）为 true，其余均为 false
  next_action  下一步指令，本脚本可能出现：REPORT_SUCCESS /
               REPORT_FAILURE / FIX_PARAMS_AND_RERUN / QUERY_TASK_LATER /
               CONTACT_ADMIN_THEN_RERUN
taskResult 为 null 表示流程无输出（FLOW_HAS_OUTPUT=False 时跳过日志获取）
或日志未写输出、未解析到（均不影响成功判定）；其中文件路径为执行器机器
上的本地路径。

附件说明：多个附件用英文分号分隔；路径不做存在性检查、原样传给执行器，
须为执行器机器上可访问的本地路径。

零第三方依赖，Python 3.7+ 即可运行，无需安装 tbotcontroller。

退出码：0 成功；1 参数校验失败、下发失败、执行失败或执行超时（终态失
败）；2 等待超时未到终态；3 调用服务异常。
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

# -------------------- 本流程的下发配置（流程知识内聚于本脚本） --------------------
TASK_TYPE = "rpa_email"  # 标签统一取脚本名，不再单独命名
FLOW_ID = "c86936ce925c409ab31cdbd2b823edbb"
TASK_NAME = "SMTP发送邮件"
TASK_DESC = "通过传入收件人input_receiver、抄送input_Cc、主题input_subject、正文input_body、附件input_attachments发送邮件"

# 流程输出开关：正式版流程只有输入没有输出变量，终态后跳过日志接口取
# taskResult，省一次服务往返（含日志为空时的重试等待）；流程日后增加
# 输出变量改为 True 即恢复取数
FLOW_HAS_OUTPUT = False

# 已知失败模式（任务执行失败后在任务日志中识别，命中即返回结构化
# failure_pattern、处置指引与 next_action，让调用方智能体明确下一步）：
# (日志正则, 模式码, 处置指引文案, next_action)，按序匹配第一个命中。
# ATTACHMENT_NOT_FOUND 实测样例（2026-09-17）：日志 ERROR 行
# "邮件发送失败：[SMTP发送邮件][第52行]未能找到文件"D:\...xls"。"
KNOWN_FAILURE_PATTERNS = [
    (r"未能找到文件", "ATTACHMENT_NOT_FOUND",
     "附件在执行器机器上不存在：附件路径须为执行器电脑上的本地绝对路径"
     "（发送流水文件时应使用 rpa-spdb-download-detail 成功结果 "
     "data.taskResult.output_resultFile 的路径），修正 --attachments 后直接"
     "重新运行本脚本（原任务已失败、邮件未发出，无重复发送风险）",
     "FIX_PARAMS_AND_RERUN"),
]

# 下发基础配置：调整部署环境（部门、执行器等）改这里
APPOINT_TYPE = 1        # 指定方式 appointType 0:指定机器执行，1:自动执行（自动分配）
APPOINT_NUM = 1         # 自动分配执行器数量
APPOINT_WORKERS = "149,36"  # 指定执行器ID，appointType为1时忽略,为0时必填
DEPARTMENT_ID = 32      # 32 为本次测试的 T-BotSkill 部门 id
SCREEN_RECORD = 1       # 0:不录屏，1:录屏
TASK_KEY = "TASK"       # TASK 代表 API 接口触发任务

# 终态后取流程输出（仅 FLOW_HAS_OUTPUT 为 True 时使用）：直接调任务日志
# 接口（/api/tasks/<id>/log，服务端实时透传控制器 /taskapi/plus/log）解析
# taskResult——2026-09-10 实测任务执行成功后日志即可获取、无需延迟；日志
# 取到即定论（无输出变量的流程日志里没有 taskResult 行，立即收尾），仅接口
# 失败或日志为空时快速重试兜底，不拉长任务整体耗时
LOG_FETCH_RETRIES = 3   # 日志接口失败或日志为空时的尝试次数（含首次）
LOG_FETCH_INTERVAL = 1  # 两次尝试之间的间隔秒数

# 标准结果 code 码表：200 成功；400 参数校验失败（未下发）；500 终态失败
# （下发失败 / 执行失败 / 执行超时）；502 调用服务异常（结果未知）；504 等
# 待超时未到终态（结果未知）。status 枚举与之一并输出，语义见模块 docstring
CODE_OK, CODE_BAD_REQUEST, CODE_TASK_FAILED, CODE_SERVICE_ERROR, CODE_TIMEOUT = 200, 400, 500, 502, 504

DEFAULT_API = os.getenv("RPA_API_BASE_URL", "http://120.26.116.226:8686")

EXIT_OK, EXIT_FAILED, EXIT_TIMEOUT, EXIT_ERROR = 0, 1, 2, 3
TERMINAL_EXECUTION_STATUSES = ("SUCCESS", "FAILED", "TIMEOUT")


def build_dispatch_body(args):
    """命令行参数 → 服务通用下发接口请求体（task_config 与控制器 10.1 请求体一一对应）"""
    # 与原脚本一致：空值参数剔除（cc / attachments 未传时不提交该字段）
    task_param = {key: value for key, value in (
        ("input_receiver", args.receiver),
        ("input_Cc", args.cc),
        ("input_subject", args.subject),
        ("input_body", args.body),
        ("input_attachments", args.attachments),
    ) if value}
    return {
        "task_type": TASK_TYPE,
        "task_config": {
            "appointType": APPOINT_TYPE,
            "appointNum": APPOINT_NUM,
            "appointWorkers": APPOINT_WORKERS,
            "departmentId": DEPARTMENT_ID,
            "flowId": FLOW_ID,
            "screenRecord": SCREEN_RECORD,
            "taskAppName": TASK_NAME,
            "taskDesc": TASK_DESC,
            "taskKey": TASK_KEY,
            "taskParamJson": json.dumps({"taskParam": task_param}, ensure_ascii=False),
        },
    }


def http_json(method, url, body=None, timeout=60):
    """调用服务接口，返回响应中的 data；调用失败或服务返回失败时抛 RuntimeError"""
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json;charset=UTF-8"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        raise RuntimeError("调用RPA服务失败（%s）：%s" % (url, exc))
    except ValueError:
        raise RuntimeError("RPA服务返回内容不是合法 JSON（%s）" % url)
    if result.get("code") != 0:
        raise RuntimeError("RPA服务返回失败：code=%s message=%s" % (result.get("code"), result.get("message")))
    return result.get("data")


def is_final(task):
    """是否已到最终状态（与服务端判定一致）：下发明确失败，或执行状态到终态
    （SUCCESS / FAILED / TIMEOUT）"""
    return task.get("status") == "DISPATCH_FAILED" or task.get("execution_status") in TERMINAL_EXECUTION_STATUSES


def emit_result(code, status, message, data, retryable, next_action):
    """打印标准结果 JSON（stdout 唯一输出，供调用端解析）。

    顶层 code/status/success/retryable/next_action 为机器可判定字段，调用方
    智能体按它们直接决策下一步，不必解析 message 文本；data 携带结构化任务
    信息（task_id、controller_task_id、execution_status、失败原因、流程输出）。
    """
    print(json.dumps({
        "code": code,
        "status": status,
        "success": status == "SUCCESS",
        "retryable": retryable,
        "next_action": next_action,
        "message": message,
        "data": data,
    }, ensure_ascii=False, indent=2))


def _loads_task_result_fragment(fragment):
    """解析日志行截取的 taskResult JSON；流程侧若写出未转义的反斜杠路径
    （如 D:\Downloads 按单反斜杠写入，不是合法 JSON 转义），先把非法转义
    修复为双反斜杠再解析一次。"""
    try:
        return json.loads(fragment)
    except ValueError:
        try:
            return json.loads(re.sub(r'\\(?!["\\/bfnrtu])', r'\\\\', fragment))
        except ValueError:
            return None


def extract_task_result(log):
    """从任务日志文本（日志接口返回的日志行拼接）解析 RPA 流程写入的 taskResult JSON 对象。

    流程在执行完毕时写一行专用输出日志，形如：
      [14:00:07] [INFO] {"taskResult":{"output_resultfile":"D:\\Downloads\\x.xls"}}
    """
    if not log:
        return None
    for line in log.splitlines():
        start = line.find('{"taskResult"')
        if start < 0:
            continue
        parsed = _loads_task_result_fragment(line[start:])
        if isinstance(parsed, dict) and isinstance(parsed.get("taskResult"), dict):
            return parsed["taskResult"]
    return None


def extract_failure_reason(log):
    """从任务日志文本提取真实失败原因：收集 [ERROR]/[FATAL] 行，去掉时间戳
    与级别前缀，去重后用"；"拼接返回；无 ERROR 行返回 None。

    控制器任务接口对失败只回"失败"等状态文案，真实原因在日志 ERROR 行。
    流程失败重试会在明细 ERROR 之后重抛泛化文案（如先"邮件发送失败：
    [第52行]未能找到文件..."再"[第56行]邮件发送失败"），按去掉 [模块名]
    [第N行] 标记后的文本去重：新行是已有行的子串（更泛化）时丢弃，包含
    已有行（更明细）时替换，避免同一原因重复出现。
    """
    if not log:
        return None
    entries = []  # [(去重键, 原文)]
    for line in log.splitlines():
        matched = re.match(r"^\[[\d:\- ]+\]\s*\[(ERROR|FATAL)\]\s*(.+)$", line)
        if not matched:
            continue
        text = matched.group(2).strip()
        key = re.sub(r"\[[^\]]*\]", "", text).strip()  # 去掉 [模块名][第N行] 标记
        if not key or any(key in seen for seen, _ in entries):
            continue
        for index, (seen, _) in enumerate(entries):
            if seen in key:
                entries[index] = (key, text)
                break
        else:
            entries.append((key, text))
    return "；".join(text[:200] for _, text in entries[:3]) if entries else None


def fetch_task_result(api, task_id):
    """任务执行成功后直接调任务日志接口（GET /api/tasks/<task_id>/log，服务端
    实时透传控制器 /taskapi/plus/log），从日志文本解析 taskResult——2026-09-10
    实测任务成功后日志即可获取，无需延迟等待。

    日志取到即定论：其中有 taskResult 输出行就带回，没有（流程无输出变量，
    2026-09-10 情形 4 实测）立即返回 None，不做多余重试；仅日志接口调用失败
    或返回空日志（日志尚未生成）时按 LOG_FETCH_INTERVAL 快速重试兜底。
    查询失败不影响任务成败结论，仅可能导致 taskResult 缺失。
    返回 taskResult 对象或 None。
    """
    task_result = None
    for attempt in range(LOG_FETCH_RETRIES):
        try:
            data = http_json("GET", "%s/api/tasks/%s/log" % (api, task_id))
        except RuntimeError:
            data = None
        log_text = (data or {}).get("log")
        if log_text:
            task_result = extract_task_result(log_text)
            break
        if attempt + 1 < LOG_FETCH_RETRIES:
            time.sleep(LOG_FETCH_INTERVAL)
    return task_result


def fetch_task_log(api, task_id):
    """取任务日志原文（GET /api/tasks/<task_id>/log），供失败原因诊断。

    仅日志接口调用失败或返回空日志（日志尚未生成）时按 LOG_FETCH_INTERVAL
    快速重试兜底；取不到返回 None，不影响任务成败结论。
    """
    for attempt in range(LOG_FETCH_RETRIES):
        try:
            data = http_json("GET", "%s/api/tasks/%s/log" % (api, task_id))
        except RuntimeError:
            data = None
        log_text = (data or {}).get("log")
        if log_text:
            return log_text
        if attempt + 1 < LOG_FETCH_RETRIES:
            time.sleep(LOG_FETCH_INTERVAL)
    return None


def match_known_failure(log_text):
    """在任务日志文本中识别已知失败模式（KNOWN_FAILURE_PATTERNS）。

    命中返回 (模式码, 处置指引文案, next_action)，未命中或无日志返回
    (None, None, None)；KNOWN_FAILURE_PATTERNS 为空时直接跳过。
    """
    if not KNOWN_FAILURE_PATTERNS or not log_text:
        return None, None, None
    for pattern, failure_pattern, advice, next_action in KNOWN_FAILURE_PATTERNS:
        if re.search(pattern, log_text):
            return failure_pattern, advice, next_action
    return None, None, None


def finish(task, api=None, task_id=None):
    """输出标准结果结构，返回退出码。

    执行成功且 FLOW_HAS_OUTPUT 为 True 时随即取流程输出（fetch_task_result
    走日志接口），解析出的 taskResult 放入 data；FLOW_HAS_OUTPUT 为 False
    （流程无输出变量）时跳过日志获取，taskResult 固定为 null。执行失败时取
    一次任务日志：提取 ERROR 行作为真实失败原因（控制器任务接口只回状态文
    案），并识别已知失败模式（match_known_failure），命中即在 data 带
    failure_pattern、message 给出处置指引、next_action 给出明确下一步。
    stdout 只输出标准 JSON 一段，顶层 status/success/retryable/next_action
    给出明确判定，结论与提示信息（成败原因、后续处置）并入 message；任务
    标识只在 data，成功的 message 不重复携带，失败/超时的 message 仍带出
    便于排查。
    """
    execution_status = task.get("execution_status")
    task_result = None
    if execution_status == "SUCCESS" and FLOW_HAS_OUTPUT and api and task_id:
        task_result = fetch_task_result(api, task_id)
    data = {
        "task_id": task.get("task_id") or task_id,
        "controller_task_id": task.get("controller_task_id"),
        "execution_status": execution_status,
    }
    ids = "task_id=%s，controller_task_id=%s" % (data["task_id"], data["controller_task_id"])
    if execution_status == "SUCCESS":
        data["taskResult"] = task_result
        if task_result is not None:
            message = "成功：邮件已发送，流程输出taskResult"
            next_action = "REPORT_SUCCESS"
        elif not FLOW_HAS_OUTPUT:
            message = "成功：邮件已发送"
            next_action = "REPORT_SUCCESS"
        else:
            message = "成功：邮件已发送，流程输出taskResult为null"
            next_action = "REPORT_SUCCESS_NO_OUTPUT"

        emit_result(CODE_OK, "SUCCESS", message, data, False, next_action)
        return EXIT_OK
    failure_reason = task.get("execution_message") or task.get("message") or "无详细原因"
    data["failure_reason"] = failure_reason
    if task.get("status") == "DISPATCH_FAILED":
        data["status"] = "DISPATCH_FAILED"
        emit_result(CODE_TASK_FAILED, "DISPATCH_FAILED", "任务下发失败：%s（%s）" % (failure_reason, data["task_id"]), data, False, "REPORT_FAILURE")
        return EXIT_FAILED

    # 控制器任务接口对执行失败只回"失败"等状态文案（execution_message），
    # 真实原因在任务日志的 ERROR 行：取一次日志，提取 failure_reason 并识别
    # 已知失败模式（同一份日志两用）；日志取不到时保持控制器状态文案
    log_text = fetch_task_log(api, task_id) if (api and task_id) else None
    detail_reason = extract_failure_reason(log_text)
    if detail_reason:
        failure_reason = detail_reason
        data["failure_reason"] = failure_reason
    failure_pattern, advice, advice_action = match_known_failure(log_text)
    if failure_pattern:
        data["failure_pattern"] = failure_pattern
        message = "任务执行失败：%s（%s）。%s" % (failure_reason, ids, advice)
        # 已知失败模式命中且恢复路径明确（修正附件路径重跑，原邮件未发出）：
        # 可直接重跑，无需用户确认
        emit_result(CODE_TASK_FAILED, "EXECUTION_TIMEOUT" if execution_status == "TIMEOUT" else "EXECUTION_FAILED", message, data, True, advice_action or "REPORT_FAILURE")
        return EXIT_FAILED
    if execution_status == "TIMEOUT":
        message = "任务执行超时：%s（%s）" % (failure_reason, ids)
    else:
        message = "任务执行失败：%s（%s）" % (failure_reason, ids)

    emit_result(CODE_TASK_FAILED, "EXECUTION_TIMEOUT" if execution_status == "TIMEOUT" else "EXECUTION_FAILED", message, data, False, "REPORT_FAILURE")
    return EXIT_FAILED


def parse_args():
    parser = argparse.ArgumentParser(description="Send email via RPA service")
    # 业务必填项用运行时校验（见下），--task-id 只查询既有任务、--dry-run 排查时可不带
    parser.add_argument("--receiver", type=str, help="Email receiver")
    parser.add_argument("--cc", type=str, help="Email cc")
    parser.add_argument("--subject", type=str, help="Email subject")
    parser.add_argument("--body", type=str, help="Email body")
    parser.add_argument("--attachments", type=str, help="Email attachments, separated by ';'")
    parser.add_argument("--api", default=DEFAULT_API,
                        help="RPA 服务地址，默认环境变量 RPA_API_BASE_URL 或 http://120.26.116.226:8686")
    parser.add_argument("--dry-run", dest="dry_run", action="store_true",
                        help="只打印将要提交的下发请求体并退出，不下发任务（联调排查用）")
    parser.add_argument("--task-id", dest="task_id",
                        help="只查询既有任务（不下发新任务），用于超时后继续等待结果")
    parser.add_argument("--wait", type=int, default=120,
                        help="下发时服务端长查询秒数，默认 120（服务端上限 600；过长会让连接长时间静默，"
                             "可能被网络中间层当空闲连接断开报 socket.timeout，未终态时自动转本地短连接轮询，不必调大）")
    parser.add_argument("--poll-interval", dest="poll_interval", type=int, default=5,
                        help="长查询超时后的本地轮询间隔秒数，默认 5")
    parser.add_argument("--timeout", type=int, default=1800,
                        help="整体最长等待秒数，默认 1800")
    args = parser.parse_args()
    if not args.task_id and not args.dry_run:
        missing = [name for name, value in (
            ("--receiver", args.receiver), ("--subject", args.subject), ("--body", args.body)) if not value]
        if missing:
            emit_result(CODE_BAD_REQUEST, "PARAM_INVALID",
                        "参数校验失败：以下参数必填：%s（仅 --task-id 查询既有任务或 --dry-run 排查时可省略）。任务未下发、无副作用，本次任务无法完成；补齐参数后直接重新运行即可，无需用户确认" % "、".join(missing),
                        None, True, "FIX_PARAMS_AND_RERUN")
            sys.exit(EXIT_FAILED)
    return args


def main():
    args = parse_args()
    api = args.api.rstrip("/")
    task_id = args.task_id
    try:
        if args.dry_run:
            print("下发请求体（POST %s/api/dispatch）：" % api)
            print(json.dumps(build_dispatch_body(args), ensure_ascii=False, indent=2))
            return EXIT_OK
        if not task_id:
            body = build_dispatch_body(args)
            # 输出只保留标准结果 JSON，进度打印已注释（排查时可临时恢复）：
            # print("下发任务：%s/api/dispatch?wait=%d（%s）" % (api, args.wait, TASK_NAME))
            # HTTP 超时留出服务端长查询时长 + 60 秒余量
            task = http_json("POST", "%s/api/dispatch?wait=%d" % (api, args.wait), body=body, timeout=args.wait + 60)
            task_id = task["task_id"]
            if is_final(task):
                return finish(task, api, task_id)
            # print("服务端长查询 %d 秒未到终态，转为本地轮询（task_id=%s）" % (args.wait, task_id))

        deadline = time.time() + args.timeout
        while True:
            task = http_json("GET", "%s/api/tasks/%s" % (api, task_id))
            if is_final(task):
                return finish(task, api, task_id)
            if time.time() >= deadline:
                emit_result(CODE_TIMEOUT, "WAIT_TIMEOUT",
                            "等待超时：超过整体最长等待 %d 秒任务仍未结束（task_id=%s，execution_status=%s）。任务未到终态、结果未知，邮件可能已发出，切勿重新下发；稍后直接用 --task-id %s 重新运行本脚本继续查询真实结果，无需用户介入" % (
                                args.timeout, task_id, task.get("execution_status"), task_id), {
                        "task_id": task_id,
                        "controller_task_id": task.get("controller_task_id"),
                        "execution_status": task.get("execution_status"),
                    }, False, "QUERY_TASK_LATER")
                return EXIT_TIMEOUT
            time.sleep(args.poll_interval)
    except RuntimeError as exc:
        if task_id:
            emit_result(CODE_SERVICE_ERROR, "SERVICE_UNAVAILABLE",
                        "调用 RPA 服务异常：%s（任务可能已下发、邮件可能已发出，结果未知；不要重新下发，稍后用 --task-id %s 查询真实结果）" % (exc, task_id),
                        {"task_id": task_id}, False, "QUERY_TASK_LATER")
        else:
            emit_result(CODE_SERVICE_ERROR, "SERVICE_UNAVAILABLE",
                        "调用 RPA 服务异常：%s（任务未下发或下发状态未知，本次任务无法完成；确认 rpa_api_service 已启动且网络可达后重新发起请求，若怀疑任务已提交可由管理员在控制器管理端核查）" % exc,
                        None, False, "CONTACT_ADMIN_THEN_RERUN")
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
