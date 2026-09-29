#!/usr/bin/env python3
"""模型网关协议探针 —— 判定网关到底能说哪种话，决定 harness 走哪条适配器路由。

背景：harness 的模型请求由 dsh 插件直发，没有转译代理。
  - `@deepseek-ai/dsh-llm-deepseek` 只会 POST {baseURL}/chat/completions，且必须是
    SSE 流式 + 以 `data: [DONE]` 收尾；
  - `@deepseek-ai/dsh-llm-pi-ai` 支持 openai-completions / openai-responses /
    anthropic-messages 三种协议（profile 里声明手写 provider 路由即可切换）。
所以「网关只出 Responses API」并不需要写代理，改 profile 声明即可；但**必须实测**，
不能靠网关文档或「探针 ping 通」推断（HTTP 200 有 choices 但非流式，harness 一样跑挂）。

零依赖（仅标准库），可在目标机 backend 容器里直接跑：

    docker compose exec -T backend python /tmp/llm-protocol-probe.py
    # 或带参：
    docker compose exec -T backend python /tmp/llm-protocol-probe.py \
        --base http://10.56.35.240:8002/v1 --model ascend-qwen3.6-27b

参数缺省从容器环境变量取：DEEPSEEK_BASE_URL / DEEPSEEK_MODEL；
密钥缺省读 DEEPSEEK_API_KEY，其次 /run/secrets/llm_api_key（只读、不回显）。

退出码：0 = 至少一条 harness 可用链路；1 = 两条都不通。
"""

from __future__ import annotations

import argparse
import json
import os
import ssl
import sys
import urllib.error
import urllib.request
from pathlib import Path

SNIFF = 400  # 只读前若干字节就关流，避免把整段长答复拉完
TIMEOUT = 60.0


def read_key(args: argparse.Namespace) -> str:
    if args.key:
        return args.key.strip()
    env = os.environ.get("DEEPSEEK_API_KEY")
    if env:
        return env.strip()
    for candidate in (os.environ.get("DEEPSEEK_API_KEY_FILE"), "/run/secrets/llm_api_key"):
        if not candidate:
            continue
        try:
            value = Path(candidate).read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if value:
            return value
    return ""


def probe(url: str, key: str, payload: dict | None, method: str = "POST") -> dict:
    """发一次请求，只读前 SNIFF 字节。返回 status/content_type/head/streamed。"""
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT, context=ctx) as resp:
            ctype = (resp.headers.get("content-type") or "").lower()
            head = resp.read(SNIFF)
            return {
                "status": resp.status,
                "content_type": ctype,
                "head": head.decode("utf-8", "replace"),
                "streamed": "text/event-stream" in ctype,
            }
    except urllib.error.HTTPError as exc:
        body = b""
        try:
            body = exc.read(SNIFF)
        except Exception:  # noqa: BLE001 - 读错误体失败不影响判定
            pass
        return {
            "status": exc.code,
            "content_type": (exc.headers.get("content-type") or "").lower(),
            "head": body.decode("utf-8", "replace"),
            "streamed": False,
        }
    except Exception as exc:  # noqa: BLE001 - 网络层失败即「不通」
        return {"status": 0, "content_type": "", "head": f"{type(exc).__name__}: {exc}", "streamed": False}


def report(title: str, url: str, result: dict, markers: tuple[str, ...]) -> bool:
    status = result["status"]
    ok = status == 200
    print(f"\n[{title}] {url}")
    print(f"  HTTP {status or '—'}   content-type: {result['content_type'] or '(空)'}")
    print(f"  前 {SNIFF} 字节: {result['head'].strip()[:300] or '(空)'}")
    if ok:
        hits = [m for m in markers if m in result["head"]]
        print(f"  协议标记: {'命中 ' + ', '.join(hits) if hits else '未命中任何标记 ' + str(list(markers))}")
        ok = bool(hits)
    return ok


def main() -> int:
    parser = argparse.ArgumentParser(description="模型网关协议探针")
    parser.add_argument("--base", default=os.environ.get("DEEPSEEK_BASE_URL", ""), help="网关 baseURL（含 /v1 之类前缀）")
    parser.add_argument("--model", default=os.environ.get("DEEPSEEK_MODEL", ""), help="模型名")
    parser.add_argument("--key", default="", help="密钥（缺省读环境变量/密钥文件，不回显）")
    parser.add_argument("--vision-model", default=os.environ.get("DWP_VISION_MODEL", ""), help="附带探测视觉模型")
    args = parser.parse_args()

    base = (args.base or "").rstrip("/")
    if not base:
        print("缺少 --base 或 DEEPSEEK_BASE_URL", file=sys.stderr)
        return 2
    key = read_key(args)
    if not key:
        print("找不到密钥：--key / DEEPSEEK_API_KEY / /run/secrets/llm_api_key 都为空", file=sys.stderr)
        return 2
    model = args.model or "(未指定)"

    print(f"baseURL = {base}")
    print(f"model   = {model}")
    print(f"key     = {'已就绪（不回显）' if key else '缺失'}")

    chat_ok = False
    resp_ok = False

    # 1) /models：只做连通性参考，不作为任何判定依据
    report("连通性参考", f"{base}/models", probe(f"{base}/models", key, None, method="GET"), ('"data"',))

    # 2) chat/completions 流式 —— dsh-llm-deepseek 的唯一路径
    chat_stream = probe(
        f"{base}/chat/completions",
        key,
        {"model": args.model, "stream": True, "messages": [{"role": "user", "content": "ping"}]},
    )
    chat_ok = report("chat/completions（流式，harness 默认路由）", f"{base}/chat/completions", chat_stream, ("data: [DONE]", "choices"))

    # 3) chat/completions 非流式 —— 只为区分「路径存在但不出流」
    report(
        "chat/completions（非流式，仅诊断用）",
        f"{base}/chat/completions",
        probe(f"{base}/chat/completions", key, {"model": args.model, "messages": [{"role": "user", "content": "ping"}]}),
        ("choices",),
    )

    # 4) responses 流式 —— dsh-llm-pi-ai 的 openai-responses 路由
    resp_stream = probe(
        f"{base}/responses",
        key,
        {"model": args.model, "stream": True, "input": "ping", "store": False},
    )
    resp_ok = report("responses（流式，pi-ai 路由）", f"{base}/responses", resp_stream, ("response.completed", "response.output_text.delta", "response.created"))

    # 5) responses 非流式 —— 诊断用（后端 llm.py 若走 Responses 是非流式的）
    report(
        "responses（非流式，仅诊断用）",
        f"{base}/responses",
        probe(f"{base}/responses", key, {"model": args.model, "input": "ping", "store": False}),
        ("output_text", '"output"'),
    )

    print("\n===== 结论 =====")
    if chat_ok and resp_ok:
        print("两条链路都可用 → 保持现有 profile（llm-deepseek 路由），不用改。")
    elif chat_ok:
        print("只有 chat/completions 可用 → 保持现有 profile（llm-deepseek 路由）。")
    elif resp_ok:
        print("只有 Responses API 可用 → 改 profile：agent-default-model 指向手写 pi-ai 路由")
        print("  （api: openai-responses）；backend/app/services/llm.py 也要加 Responses 分支，")
        print("   否则视觉/结构化输出等后端侧调用仍会打在 /chat/completions 上。")
    else:
        print("两条都不通 → 先排查网关地址/密钥/模型名，或确认网关是否要求流式以外的开关。")
        print("注意：探针 ping 通不代表 harness 能跑 —— 必须看 content-type 是否 text/event-stream。")

    if args.vision_model:
        print(f"\n[视觉参考] DWP_VISION_MODEL={args.vision_model}（多模态 content 数组只能走 chat/completions）")

    return 0 if (chat_ok or resp_ok) else 1


if __name__ == "__main__":
    raise SystemExit(main())
