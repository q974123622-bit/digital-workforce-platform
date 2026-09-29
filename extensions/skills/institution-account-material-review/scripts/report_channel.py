#!/usr/bin/env python3
"""执行轨迹上报通道：技能脚本主动把「自己做了什么」写进平台执行轨迹。纯 stdlib。

为什么需要它：dsh 是一次性 CLI 调用，除了最终答复什么都不外露（canary 实测 stderr 为空、
stdout 只有最终答复），所以容器内跑脚本、读文件这些动作平台看不见。技能脚本调这个接口
主动上报，模型规划出来的步骤才能进 `agent_execution_event`、进界面执行轨迹。

环境变量（由平台 `runtime_adapter` 在执行时注入）：
  DWP_EXECUTION_EVENT_URL   上报地址，形如 http://backend:8080/internal/execution/events
  DWP_AGENT_TOOL_TOKEN      工具令牌（Bearer）
  DWP_TRACE_ID              本轮执行 trace，用于把事件绑到正确的那次执行

命令行用法（脚本里最常用）：
  python3 report_channel.py --title "清点材料：13 份" --detail "识别到 12 张图 1 个文档"
  python3 report_channel.py --title "比对字段 8 个" --status completed

失败只写 stderr、返回非零，**绝不阻断主流程** —— 轨迹是观测，不是业务前置条件。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request

EVENT_URL_ENV = "DWP_EXECUTION_EVENT_URL"
TOKEN_ENV = "DWP_AGENT_TOOL_TOKEN"
TRACE_ENV = "DWP_TRACE_ID"

TITLE_MAX = 160
DETAIL_MAX = 300
TIMEOUT = 5.0

VALID_STATUS = ("running", "completed", "failed")
VALID_EVENT_TYPE = ("skill_step",)

# 敏感信息兜底：证件号 / 银行账号这类长数字串一律打码，绝不进轨迹
_LONG_DIGITS = re.compile(r"\d{15,}")
_API_KEY = re.compile(r"\b(sk-[A-Za-z0-9_\-]{8,}|Bearer\s+\S+)")


def scrub(text):
    """去掉可能泄漏的证件号 / 账号 / 密钥。服务端还会再过滤一次，这里是第一道。"""
    if text is None:
        return ""
    out = _LONG_DIGITS.sub(lambda m: m.group(0)[:4] + "*" * (len(m.group(0)) - 8) + m.group(0)[-4:], str(text))
    return _API_KEY.sub("***", out)


def report(title, detail="", status="running", event_type="skill_step",
           stage="material_review", payload=None, url=None, token=None, trace_id=None,
           timeout=TIMEOUT):
    """上报一条执行事件。返回 (ok, info)。任何异常都被吞掉并写 stderr。"""
    url = url or os.environ.get(EVENT_URL_ENV, "")
    token = token or os.environ.get(TOKEN_ENV, "")
    trace_id = trace_id or os.environ.get(TRACE_ENV, "")
    if not url:
        sys.stderr.write("[report_channel] 未配置 %s，跳过上报\n" % EVENT_URL_ENV)
        return False, "no_url"
    if status not in VALID_STATUS:
        sys.stderr.write("[report_channel] 非法 status=%s，跳过上报\n" % status)
        return False, "bad_status"
    if event_type not in VALID_EVENT_TYPE:
        sys.stderr.write("[report_channel] 非法 event_type=%s，跳过上报\n" % event_type)
        return False, "bad_event_type"

    body = {
        "event_type": event_type,
        "stage": stage,
        "status": status,
        "title": scrub(title)[:TITLE_MAX],
        "detail": scrub(detail)[:DETAIL_MAX],
    }
    if trace_id:
        body["trace_id"] = trace_id
    if payload:
        body["payload"] = payload

    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=data, method="POST")
    request.add_header("Content-Type", "application/json; charset=utf-8")
    if token:
        request.add_header("Authorization", "Bearer %s" % token)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
        return True, raw.strip()
    except (urllib.error.URLError, OSError, ValueError) as exc:
        sys.stderr.write("[report_channel] 上报失败（不影响主流程）：%s\n" % exc)
        return False, str(exc)


def main(argv=None):
    ap = argparse.ArgumentParser(description="上报一条执行轨迹事件")
    ap.add_argument("--title", required=True)
    ap.add_argument("--detail", default="")
    ap.add_argument("--status", default="running", choices=VALID_STATUS)
    ap.add_argument("--event-type", default="skill_step", choices=VALID_EVENT_TYPE)
    ap.add_argument("--stage", default="material_review")
    ap.add_argument("--payload", help="附加 JSON（键需在服务端白名单内）")
    ap.add_argument("--dry-run", action="store_true", help="只打印请求体，不真的上报")
    args = ap.parse_args(argv)

    payload = json.loads(args.payload) if args.payload else None
    if args.dry_run:
        print(json.dumps({"title": scrub(args.title)[:TITLE_MAX],
                          "detail": scrub(args.detail)[:DETAIL_MAX],
                          "status": args.status, "event_type": args.event_type,
                          "stage": args.stage, "payload": payload},
                         ensure_ascii=False, indent=2))
        return 0

    ok, info = report(args.title, args.detail, status=args.status,
                      event_type=args.event_type, stage=args.stage, payload=payload)
    if ok:
        print(info)
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
