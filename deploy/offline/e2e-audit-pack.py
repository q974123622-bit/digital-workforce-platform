#!/usr/bin/env python3
"""审核材料包端到端验收（走 API，不需要开浏览器）。

验的是一条完整链路：
    材料包 zip → 安全解包并按业务号分组 → 图片放行 → 上传即转写 →
    发起执行时物化进目标员工工作区 → dsh 自主规划并跑技能工具箱 →
    轨迹里能看到容器内的步骤 → 答复里有核对表

用法：
    python3 make-audit-pack.py --out /tmp/audit-pack.zip
    python3 e2e-audit-pack.py                      # 默认读 /tmp/audit-pack.zip
    python3 e2e-audit-pack.py --pack /tmp/x.zip --employee AI-AUDIT --actor E10281
    python3 e2e-audit-pack.py --keep-conversation  # 跑完不清理，方便去界面上看

前置：平台已在 18080 跑起来（`docker compose ps` 全 healthy）。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

OFFLINE = Path(__file__).resolve().parent
FAILS: list[str] = []


class Api:
    def __init__(self, base: str):
        self.base = base.rstrip("/")
        self.cookies: dict[str, str] = {}

    def raw(self, path, payload=None, body=None, ctype=None, method=None, timeout=180):
        url = path if path.startswith("http") else self.base + path
        data, headers = None, {}
        if body is not None:
            data = body
            if ctype:
                headers["Content-Type"] = ctype
        elif payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if self.cookies:
            headers["Cookie"] = "; ".join("%s=%s" % kv for kv in self.cookies.items())
        request = urllib.request.Request(
            url, data=data, method=method or ("POST" if data else "GET"), headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as resp:
                return resp.status, resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode("utf-8", "replace")
        except (urllib.error.URLError, OSError) as exc:
            return 0, str(exc)

    def call(self, path, payload=None, method=None, timeout=180):
        status, text = self.raw(path, payload=payload, method=method, timeout=timeout)
        try:
            return json.loads(text) if text else None
        except json.JSONDecodeError:
            return None

    def login(self, key: str) -> bool:
        self.cookies.clear()
        request = urllib.request.Request(
            self.base + "/auth/key-login",
            data=json.dumps({"access_key": key}).encode("utf-8"),
            method="POST", headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=30) as resp:
            for header in resp.headers.get_all("Set-Cookie") or []:
                name, _, rest = header.partition("=")
                self.cookies[name.strip()] = rest.split(";")[0]
        return bool(self.cookies)


def check(name, ok, detail=""):
    print("%s %s%s" % ("PASS" if ok else "FAIL", name,
                       ("  ← " + str(detail)[:200]) if (not ok and detail != "") else ""))
    if not ok:
        FAILS.append(name)
    return ok


def upload_pack(api: Api, conv_id: str, pack: Path):
    """材料包上传走 multipart/form-data（和单文件上传同一个字段名 file）。"""
    boundary = "----dwppack%s" % uuid.uuid4().hex
    head = ("--%s\r\nContent-Disposition: form-data; name=\"file\"; filename=\"%s\"\r\n"
            "Content-Type: application/zip\r\n\r\n" % (boundary, pack.name)).encode("utf-8")
    tail = ("\r\n--%s--\r\n" % boundary).encode("utf-8")
    return api.raw("/conversations/%s/attachments/pack" % conv_id,
                   body=head + pack.read_bytes() + tail,
                   ctype="multipart/form-data; boundary=%s" % boundary)


def upload_pack_files(api: Api, conv_id: str, paths: list[Path]):
    """多文件直接上传（不打包）：与 zip 通道落库/物化完全同路，只是不用先自己压包。"""
    boundary = "----dwpfiles%s" % uuid.uuid4().hex
    parts: list[bytes] = []
    for path in paths:
        parts.append((
            "--%s\r\nContent-Disposition: form-data; name=\"files\"; filename=\"%s\"\r\n"
            "Content-Type: application/octet-stream\r\n\r\n" % (boundary, path.name)
        ).encode("utf-8"))
        parts.append(path.read_bytes())
        parts.append(b"\r\n")
    parts.append(("--%s--\r\n" % boundary).encode("utf-8"))
    return api.raw("/conversations/%s/attachments/pack/files" % conv_id,
                   body=b"".join(parts),
                   ctype="multipart/form-data; boundary=%s" % boundary)


def latest(api: Api, conv_id: str) -> dict:
    """runs/latest 返回 {execution, events}。

    注意别用 `/runs/{eid}/events` 读事件：那个接口是 SSE 流（`id:/event:/data:`），
    不是 JSON，按 JSON 解析会静默拿到空列表。
    """
    data = api.call("/conversations/%s/runs/latest" % conv_id)
    return data if isinstance(data, dict) else {}


def default_base() -> str:
    """入口端口以 `.env` 的 `DWP_HTTP_PORT` 为准（开发机上 18080 可能被别的隧道占用）。"""
    port = "18080"
    env_file = OFFLINE / ".env"
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            key, _, value = line.partition("=")
            if key.strip() == "DWP_HTTP_PORT" and value.strip():
                port = value.strip()
                break
    return "http://127.0.0.1:%s/api/v1" % port


def main() -> int:
    ap = argparse.ArgumentParser(description="审核材料包端到端验收")
    ap.add_argument("--base", default=default_base())
    ap.add_argument("--pack", default="/tmp/audit-pack.zip")
    ap.add_argument("--employee", default="AI-AUDIT")
    ap.add_argument("--actor", default="E10281")
    ap.add_argument("--key-file", default="")
    ap.add_argument("--extra-files", default="",
                    help="额外走「多文件直接上传」的目录（验不打包通道）")
    ap.add_argument("--wait", type=int, default=480, help="等执行结束的最长秒数")
    ap.add_argument("--max-parse-seconds", type=float, default=0.0,
                    help="材料解析段阈值（秒）；0 = 按材料份数取默认（≤6 份 45s，否则 90s）")
    ap.add_argument("--max-total-seconds", type=float, default=0.0,
                    help="全流程阈值（秒）；0 = 按材料份数取默认（≤6 份 120s，否则 180s）")
    ap.add_argument("--keep-conversation", action="store_true")
    args = ap.parse_args()

    pack = Path(args.pack)
    if not pack.is_file():
        raise SystemExit("材料包不存在：%s（先跑 python3 make-audit-pack.py）" % pack)
    key_file = Path(args.key_file) if args.key_file else (
        OFFLINE / "secrets" / ("login_key_%s" % args.actor.lower()))
    if not key_file.is_file():
        raise SystemExit("找不到登录密钥：%s" % key_file)

    api = Api(args.base)

    print("=== 1. 登录（%s）" % args.actor)
    check("登录成功", api.login(key_file.read_text(encoding="utf-8").strip()))

    print("\n=== 2. 建会话（%s → %s）" % (args.actor, args.employee))
    conv = api.call("/conversations", {"actor_no": args.actor, "kind": "direct",
                                       "participant_employee_nos": [args.employee]})
    conv_id = (conv or {}).get("id", "")
    if not check("会话已创建", bool(conv_id), conv):
        return 1
    api.call("/conversations/%s/clear?actor_no=%s" % (conv_id, args.actor), {}, method="POST")

    print("\n=== 3. 上传材料包（%s）" % pack.name)
    t_upload = time.time()
    status, text = upload_pack(api, conv_id, pack)
    try:
        rows = json.loads(text)
    except json.JSONDecodeError:
        rows = None
    if not check("材料包接口返回附件清单", status == 200 and isinstance(rows, list) and rows,
                 "status=%s body=%s" % (status, text[:200])):
        return 1
    check("每份附件带 pack_id 与业务号提示",
          all(r.get("pack_id") and r.get("business_no") for r in rows),
          [(r.get("filename"), r.get("business_no")) for r in rows])
    check("业务号只是提示（平台不再据此分笔）",
          len({r["business_no"] for r in rows}) >= 1,
          sorted({r["business_no"] for r in rows}))
    suffixes = {r["file_type"] for r in rows}
    print("  材料 %d 份，类型：%s" % (len(rows), sorted(suffixes)))
    check("图片被放行（file_type 里有图片后缀）",
          any(s in {"png", "jpg", "jpeg", "webp", "bmp", "tif", "tiff", "gif"} for s in suffixes),
          sorted(suffixes))

    if args.extra_files:
        extra_dir = Path(args.extra_files)
        supported = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff", ".gif",
                     ".pdf", ".docx", ".xlsx", ".csv", ".tsv", ".txt", ".md", ".markdown"}
        extra = sorted(p for p in extra_dir.iterdir()
                       if p.is_file() and p.suffix.lower() in supported)[:8]
        print("\n=== 3.5 多文件直接上传（不打包，%d 份）" % len(extra))
        status, text = upload_pack_files(api, conv_id, extra)
        try:
            file_rows = json.loads(text)
        except json.JSONDecodeError:
            file_rows = None
        check("多文件上传接口返回附件清单",
              status == 200 and isinstance(file_rows, list) and len(file_rows) == len(extra),
              "status=%s body=%s" % (status, text[:200]))
        check("多文件上传与 zip 通道同路（都带 pack_id，落同一物化链路）",
              all(r.get("pack_id") for r in file_rows or []),
              [(r.get("filename"), r.get("pack_id")) for r in file_rows or []])
        rows = rows + (file_rows or [])

    print("\n=== 4. 等上传即转写完成")
    deadline = time.time() + 600
    state: dict[str, str] = {}
    while time.time() < deadline:
        listing = api.call("/conversations/%s/attachments" % conv_id) or []
        state = {r["filename"]: r["status"] for r in listing if r.get("pack_id")}
        if state and all(v in ("ready", "failed", "ocr_required") for v in state.values()):
            break
        time.sleep(6)
    for name, status_text in sorted(state.items()):
        print("  %-40s %s" % (name, status_text))
    check("全部附件转写完成且 ready",
          bool(state) and all(v == "ready" for v in state.values()), state)

    listing = api.call("/conversations/%s/attachments" % conv_id) or []
    ready = [r for r in listing if r.get("status") == "ready"]
    t_transcribe = time.time()
    check("每份材料都有文本片段（转写非空）",
          bool(ready) and all((r.get("chunk_count") or 0) > 0 for r in ready),
          [(r["filename"], r.get("chunk_count")) for r in ready])

    print("\n=== 5. 发起执行")
    t_run = time.time()
    run = api.call("/conversations/%s/runs" % conv_id,
                   {"actor_no": args.actor,
                    "content": "审核一下下面的材料信息是否准确，按你的方法出一份核对表。",
                    "attachment_ids": [r["attachment_id"] for r in ready]})
    execution_id = (run or {}).get("execution_id", "")
    if not check("执行已发起", bool(execution_id), run):
        return 1

    print("\n=== 6. 等执行结束（最多 %d 秒）" % args.wait)
    deadline = time.time() + args.wait
    detail: dict = {}
    while time.time() < deadline:
        detail = latest(api, conv_id)
        status_text = (detail.get("execution") or {}).get("status")
        if status_text in ("completed", "failed", "cancelled"):
            break
        time.sleep(8)
    execution = detail.get("execution") or {}
    events = detail.get("events") or []
    kinds = [e.get("event_type") for e in events]
    t_exec_done = time.time()
    print("  状态=%s  事件数=%d" % (execution.get("status"), len(events)))
    check("执行正常结束", execution.get("status") == "completed",
          execution.get("error_message") or execution.get("status"))

    print("\n=== 7. 轨迹里能看到规划出来的步骤")
    for event in events:
        if event.get("event_type") in ("materials_ready", "skill_routed", "skill_step",
                                       "answer_from_workspace"):
            print("  · [%s] %s ｜ %s" % (event.get("event_type"), event.get("title"),
                                         (event.get("detail") or "")[:60]))
    check("轨迹有「材料已就绪」", "materials_ready" in kinds, kinds)
    check("轨迹有技能匹配（技能已投放时才有）",
          "skill_routed" in kinds or "skill_step" in kinds,
          "若失败请确认技能已绑定给 %s 并已投放" % args.employee)
    check("轨迹有技能脚本上报的步骤（容器内动作可见）", "skill_step" in kinds, kinds)
    timing_events = [e for e in events if e.get("event_type") == "stage_timing"]
    check("轨迹有本轮耗时小结（分段耗时可见）", bool(timing_events), kinds)
    for event in timing_events:
        print("  · [stage_timing] %s ｜ %s" % (event.get("title"), event.get("detail")))

    print("\n=== 8. 材料已物化到工作区")
    workspace = OFFLINE / "harness" / args.employee / "workspace" / "materials"
    # 只看本次材料包：历史轮次的目录会留在工作区，rglob 会把它们的产物当成材料文件读。
    pack_id = next((r.get("pack_id") for r in rows if r.get("pack_id")), "")
    pack_dirs = [workspace / pack_id] if pack_id and (workspace / pack_id).is_dir() else []
    check("本次材料包已物化到工作区", bool(pack_dirs), str(workspace / pack_id))
    files = sorted(p for p in pack_dirs[0].glob("*.json") if p.name != "materials.json") \
        if pack_dirs else []
    check("工作区里有材料文件", len(files) >= len(ready), [p.name for p in files])
    check("材料包目录内是平铺的（不再按业务号分子目录）",
          bool(pack_dirs) and not any(p.is_dir() for p in pack_dirs[0].iterdir()),
          [p.name for p in pack_dirs])
    if pack_dirs:
        manifest = json.loads((pack_dirs[0] / "materials.json").read_text(encoding="utf-8"))
        check("materials.json 声明平铺布局与提示口径",
              manifest.get("layout") == "flat"
              and all("hint_business_no" in item for item in manifest.get("materials") or []),
              manifest.get("layout"))
    if files:
        body = json.loads(files[0].read_text(encoding="utf-8"))
        check("材料文件带事实层 text_lines", bool(body.get("text_lines")),
              list(body.keys())[:8])
        # 材料类型没有预设，是 agent 自己判断后写回的 —— 写回了才说明它真读了材料
        check("agent 已自行判定并写回材料类型", bool(body.get("material_type")),
              body.get("material_type"))

    print("\n=== 9. 答复里有核对表")
    conversation = api.call("/conversations/%s" % conv_id) or {}
    answer = "\n".join((m.get("content") or "") for m in (conversation.get("messages") or [])
                       if m.get("role") == "assistant")
    print("  答复 %d 字符" % len(answer))
    check("答复非空", bool(answer.strip()), answer[:120])
    check("答复含核对表结构（表格或分级结论）",
          ("|" in answer and "---" in answer) or "不一致" in answer, answer[:150])
    check("答复带免责声明（以人工审核为准）", "人工审核" in answer, answer[-200:])
    if answer:
        print("\n--- 答复前 600 字 ---")
        print(answer[:600])

    print("\n=== 10. 耗时（3 分钟预算）")
    count = len(ready) or len(rows)
    parse_seconds = t_transcribe - t_upload
    run_seconds = t_exec_done - t_run
    total_seconds = t_exec_done - t_upload
    parse_limit = args.max_parse_seconds or (45.0 if count <= 6 else 90.0)
    total_limit = args.max_total_seconds or (120.0 if count <= 6 else 180.0)
    print("  材料 %d 份" % count)
    print("  材料解析（上传 → 全部转写完成）：%.1f 秒（阈值 %.0f）" % (parse_seconds, parse_limit))
    print("  执行（发起 → 结束）：%.1f 秒" % run_seconds)
    print("  全流程（上传 → 结束）：%.1f 秒（阈值 %.0f）" % (total_seconds, total_limit))
    check("材料解析段在预算内（并发化生效，不再是 N × 单份）",
          parse_seconds <= parse_limit, "%.1fs > %.0fs" % (parse_seconds, parse_limit))
    check("全流程在预算内", total_seconds <= total_limit,
          "%.1fs > %.0fs" % (total_seconds, total_limit))

    print("\n" + "=" * 64)
    print("验收：%s（失败 %d 项）" % ("通过" if not FAILS else "不通过", len(FAILS)))
    for name in FAILS:
        print("  失败：%s" % name)
    print("会话 %s ｜ 执行 %s" % (conv_id, execution_id))
    if args.keep_conversation:
        print("（已保留会话，可在界面上打开 %s 查看）" % conv_id)
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(main())
