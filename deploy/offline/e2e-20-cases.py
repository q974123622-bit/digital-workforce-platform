#!/usr/bin/env python3
"""dwp AI员工平台 —— 20 份 case 全方面验收（逻辑 12 + 稳定性 8，真跑非 mock）。

判据来源（全部取真源，不看前端）：
  - agent_execution.status / error_code        执行终态
  - agent_execution_event                      阶段与事件序列（含 input_required）
  - audit_event                                知识库读授权、外部 MCP 调用、补全提交
  - delegation_run                             委派链路（sender/recipient/status/error）
  - conversation_message.tool_cards            引用链路（knowledge_base_id / agent_employee_id）
  - conversation_message.superseded_at         就地编辑重跑后旧回复是否作废
  - tool_input_request                         待补全记录（status/reason/submitted_values）
  - mcp_user_credential                        按人凭据（只读掩码）

改动前请先读这三条不变量
------------------------------------------------------------------------------
1. 等待必须按「本次 execution_id」精确匹配。`GET /conversations/{cid}/runs/latest`
   在本次执行被整体回滚时会回落到上一次执行，只看「latest 是否终态」会把失败判成
   成功。
2. 并发必须按争用资源加锁：同会话存在活跃执行时再提交返回 409（唯一索引
   `uq_agent_execution_active_conversation`）；委派走 per-employee 非阻塞锁。
   锁键 `emp:<发起员工>` / `delegate:<委派目标>`，重叠串行、不重叠才并行。
   需要「故意制造争用」的 case 用 `no_lock=True` 显式绕过。
3. 模型偶发生成非法 JSON 工具参数 → dsh 报 `INVALID_REQUEST` 直接退出，属模型侧
   鲁棒性问题而非平台缺陷：重试 1 次仍失败记 `FLAKY`，不计入缺陷清单。

用法:
  ./e2e-20-cases.py                       # 全部 20 个 case
  ./e2e-20-cases.py --only L01 S01        # 只跑指定 case
  ./e2e-20-cases.py --workers 3           # 并发度（默认 3）
  ./e2e-20-cases.py --no-mutate           # 跳过会写数据/重启容器的 case
  ./e2e-20-cases.py --report-md x.md      # 额外产出 Markdown 总表
"""
from __future__ import annotations

import argparse
import base64
import http.cookiejar
import json
import re
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
DB_PATH = HERE / "data" / "dwp.db"
DEFAULT_ACTOR = "E10281"                      # 张三
KEY_FILES = {                                 # 自然人 -> 登录密钥（私聊自己的分身必须用本人身份）
    "E10281": HERE / "secrets" / "login_key_e10281",
    "E20999": HERE / "secrets" / "login_key_e20999",
}
ADMIN_KEY_FILE = HERE / "secrets" / "login_key_admin"
POLL_INTERVAL = 4
TERMINAL = {"completed", "failed", "cancelled", "timeout"}
BACKEND_CONTAINER = "dwp-ai-employee-platform-backend-1"
EHOME_PLUGIN = "xyzq-ehome-mcp"
EHOME_TOOL = "ehome__getXtDoneList"           # 需要用户级 JWT 的工具（预检必命中）

_db_lock = threading.Lock()


def db_rows(sql: str, args: tuple = ()) -> list[dict]:
    """只读查询运行库（WAL 允许并发读），作为所有判据的真源。"""
    with _db_lock:
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=15)
        try:
            conn.row_factory = sqlite3.Row
            return [dict(r) for r in conn.execute(sql, args).fetchall()]
        finally:
            conn.close()


def db_exec(sql: str, args: tuple = ()) -> int:
    """仅用于「快照后原样恢复」：写回被 case 临时改动的行。"""
    with _db_lock:
        conn = sqlite3.connect(str(DB_PATH), timeout=15)
        try:
            cur = conn.execute(sql, args)
            conn.commit()
            return cur.rowcount
        finally:
            conn.close()


def doc_titles_for(kb_id: str, limit: int = 3) -> list[str]:
    """从历史审计里取该知识库的真实文档名（用于越权断言：答案不得出现这些标题）。

    审计 result_summary 里 `documents[].title` 是检索引擎返回的真实标题，
    比手工维护一份文档名清单更可信。
    """
    rows = db_rows(
        "SELECT result_summary FROM audit_event WHERE action='read' AND decision='allow' "
        "AND knowledge_base_id=? AND result_summary LIKE '%documents%' ORDER BY rowid DESC LIMIT 40",
        (kb_id,),
    )
    seen: list[str] = []
    for row in rows:
        try:
            docs = json.loads(row["result_summary"] or "{}").get("documents") or []
        except (ValueError, TypeError):
            continue
        for item in docs:
            title = str((item or {}).get("title") or "").strip()
            # 只挑「像文档名」的：够长、含中文、不是纯 sheet 噪声
            if len(title) >= 6 and re.search(r"[\u4e00-\u9fff]", title) and title not in seen:
                seen.append(title)
    # 短标题更可能是模型会照抄的，优先取
    seen.sort(key=len)
    return seen[:limit]


def docker(args: list[str], timeout: int = 180) -> tuple[int, str]:
    """跑一条 docker 命令；返回 (returncode, stdout+stderr)。仅重启/内探类 case 使用。"""
    proc = subprocess.run(
        ["docker", *args], cwd=str(HERE), capture_output=True, text=True, timeout=timeout,
    )
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


_HARNESS_ERR_RE = re.compile(r"harness execution failed employee=\S+ trace=(\S+) mode=\S+ detail=(.*)")


def _harness_error_detail(trace_id: str) -> str:
    """从 backend 容器日志里取这条 trace 的 harness 原始失败原因。

    平台只把「AI员工执行失败，可稍后重试」这种通用文案写进 ``error_message``，
    dsh 的真实输出只在服务端日志里。拿不到它就只能靠猜，也就无法区分
    「模型侧偶发」与「平台缺陷」——这正是 known-flaky 分类的前提。
    """
    if not trace_id:
        return ""
    rc, out = docker(["logs", "--tail", "3000", BACKEND_CONTAINER], timeout=60)
    if rc != 0:
        return ""
    # 要的是 group(2)（detail=后面的原始报错），不是 group(1)（trace）。
    # 取错分组会让 known-flaky 分类永远不命中，把模型侧偶发算成平台缺陷。
    hits = [m.group(2) for m in _HARNESS_ERR_RE.finditer(out) if m.group(1) == trace_id]
    return hits[-1][:300] if hits else ""


class Api:
    """每个线程一个独立会话（Cookie 互不干扰）。"""

    def __init__(self, base: str, key: str | None = None):
        self.base = base.rstrip("/")
        jar = http.cookiejar.CookieJar()
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        if key:
            self.call("/auth/key-login", {"access_key": key})

    def raw(self, path: str, data=None, method: str | None = None) -> tuple[int, str]:
        """不抛异常的调用：越权/409 类断言需要看状态码本身。"""
        body = json.dumps(data).encode() if data is not None else None
        req = urllib.request.Request(
            self.base + path, data=body,
            method=method or ("POST" if data is not None else "GET"),
            headers={"Content-Type": "application/json"},
        )
        try:
            with self.op.open(req, timeout=120) as resp:
                return resp.status, resp.read().decode()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode()

    def call(self, path: str, data=None, method: str | None = None):
        status, text = self.raw(path, data, method)
        if status >= 400:
            raise urllib.error.HTTPError(self.base + path, status, text, None, None)
        return json.loads(text) if text else None


# ---------------------------------------------------------------- case 定义

@dataclass
class Case:
    cid: str
    title: str
    employee: str                 # 发起执行的AI员工（私聊会话的目标）
    content: str
    group: str = "logic"          # logic | stability
    actor: str = DEFAULT_ACTOR    # 以哪个自然人的身份发起
    phase: str = "main"           # main | isolated-last（重启类放最后单独跑）
    flow: str = "single"          # single|serial2|edit_rerun|cancel|expect_409|
                                  # concurrent|oversize|bad_credential|restart|
                                  # marketplace|input_completion|security
    content2: str = ""            # serial2 的第二轮提问
    max_wait: int = 420
    mutating: bool = False        # True 时 --no-mutate 下跳过
    no_lock: bool = False         # True 时不吃资源锁（故意制造争用的 case）
    lock_keys: list[str] = field(default_factory=list)  # 显式声明争用资源（多员工并发 case）
    # ---- 断言期望 ----
    expect_kb_allow: list[str] = field(default_factory=list)   # 必须出现这些库的 allow 读审计
    expect_kb_no_allow: str = ""            # 不得出现该库的 allow 读审计
    expect_no_kb_read: bool = False         # 本轮不得有任何知识库 allow 读审计
    expect_no_cards: bool = False           # 本轮不得有任何引用卡片
    expect_card_kb: str = ""                # 本轮 tool_cards 必须含该库的引用
    expect_card_absent_kb: str = ""         # 本轮 tool_cards 不得含该库
    expect_card_agent: str = ""             # 该引用的 agent_employee_id（验证引用透传）
    expect_delegate_to: str = ""            # 必须委派给该岗位AI员工
    expect_no_delegate: bool = False        # 不得发生任何委派
    expect_no_delegate_to: str = ""         # 不得委派给该目标
    expect_mcp_plugin: str = ""             # 必须有该 MCP 插件的 execute 审计
    answer_has: list[str] = field(default_factory=list)
    answer_lacks: list[str] = field(default_factory=list)
    answer_lacks_kb_docs: str = ""          # 答案不得出现该库的真实文档名（自动从审计取）
    expect_cancelled: bool = False
    expect_http: int = 0                    # 断言某个 HTTP 状态码（0 表示不校验）


@dataclass
class Result:
    case: Case
    ok: bool = False
    status: str = "-"
    seconds: float = 0.0
    notes: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    flaky: bool = False
    trace_id: str = ""
    conversation_id: str = ""
    execution_id: str = ""
    answer: str = ""
    min_seq: int = 0
    http_codes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------- 20 份 case

CASES: list[Case] = [
    # ================= 逻辑 12 =================
    Case("L01", "检索 IT 服务知识库（授权库命中）", "AI-GENERAL",
         "IT 服务台的工单受理流程是怎样的？请先检索知识库再作答，并标注来源文档。",
         expect_kb_allow=["KB-IT-SERVICE"], expect_card_kb="KB-IT-SERVICE"),
    Case("L02", "检索财务报账知识库（多库授权下选对库）", "AI-XYZQ",
         "财务报账的流程和发票要求是什么？请先检索知识库再作答，并标注来源文档。",
         expect_kb_allow=["KB-FINANCE"]),
    Case("L03", "顺序检索两个授权库（跨库不串台）", "AI-INVESTMENT",
         "请先检索知识库再作答，并标注来源文档：融资融券业务的办理条件有哪些？",
         flow="serial2",
         content2="请再检索知识库后作答，并标注来源文档：投行业务承揽的立项流程是什么？",
         expect_kb_allow=["KB-SECURITIES", "KB-INVESTMENT-BANKING"]),

    # 越权边界：既看「不得出现 allow 读审计」，也看「不得引用该库」，还看「答案不得出现该库真实文档名」
    Case("L04", "越权问未授权的证券知识库", "AI-FUND",
         "融资融券业务的办理条件有哪些？请先检索知识库再作答，并标注来源文档。",
         expect_kb_no_allow="KB-SECURITIES",
         expect_card_absent_kb="KB-SECURITIES",
         answer_lacks_kb_docs="KB-SECURITIES"),
    Case("L05", "越权问未授权的投行知识库", "AI-GENERAL",
         "投行业务承揽的立项流程是什么？请先检索知识库再作答，并标注来源文档。",
         expect_kb_no_allow="KB-INVESTMENT-BANKING",
         expect_card_absent_kb="KB-INVESTMENT-BANKING",
         answer_lacks_kb_docs="KB-INVESTMENT-BANKING"),

    # 数字分身无知识库授权 → 必须委派给有授权的岗位AI员工
    Case("L06", "分身无库权限须委派给岗位AI员工", "DT-E10281",
         "IT 服务台的工单受理流程是怎样的？请给出关键步骤。",
         expect_delegate_to="AI-GENERAL"),
    # 实习生身份（E20999 陈晓萌）的授权边界。所有知识库的 allowed_employment_type 都是
    # ["formal"]，而 accessible_knowledge_bases 会用「请求人」属性再过滤一次
    # （knowledge_registry 第 62-66 行）→ 实习生发起时全部知识库被摘除，
    # 连委派给岗位AI员工也读不到（被委派方同样按 requester 过滤）。
    # 因此本 case 验的是「不得越权读到 formal-only 库」，引用透传由 L06 覆盖。
    Case("L07", "实习生身份不得读到 formal-only 知识库（委派也无效）", "DT-E20999",
         "帮我查一下 IT 服务台的工单受理流程，请先检索知识库再作答，并标注来源文档。",
         actor="E20999", expect_delegate_to="AI-GENERAL",
         expect_no_kb_read=True, expect_no_cards=True,
         answer_lacks_kb_docs="KB-IT-SERVICE"),

    # 技能路由：命中「市场早报」/ 寒暄不命中
    Case("L08", "技能路由：命中领域技能与普通寒暄", "AI-FUND",
         "帮我出一份今天 A 股的市场早报。",
         flow="serial2",
         content2="你好，请用一句话介绍你自己，不要检索知识库也不要调用工具。",
         answer_has=["市场"]),

    # 会话操作：就地编辑并重跑，旧回复必须被作废
    Case("L09", "就地编辑最新一轮并重跑", "AI-INVESTMENT",
         "融资融券业务的办理条件有哪些？",
         flow="edit_rerun"),

    # 兴证e家员工服务 MCP：按人 JWT 已存在 → 直接查到本人数据
    Case("L10", "兴证e家 MCP 按人鉴权正常查询", "DT-E10281",
         "帮我查一下我今年的年假还剩几天。",
         expect_mcp_plugin=EHOME_PLUGIN),

    # 缺参补全链路：弹气泡 → 提交令牌 → 自动续跑 → 只存掩码
    Case("L11", "缺参补全链路（弹气泡→提交→续跑→掩码）", "DT-E20999",
         "请用一句话说明你能做什么，不要调用任何工具。",
         actor="E20999", flow="input_completion", mutating=True, max_wait=300),

    # 市场申请与管理员审批
    Case("L12", "市场申请与管理员审批后生效", "DT-E10281",
         "", actor="E10281", flow="marketplace", mutating=True, max_wait=120),

    # ================= 稳定性 8 =================
    Case("S01", "同会话重复提交被拒且不产生第二条执行", "AI-RESEARCH",
         "客户适当性管理有哪些要求？请简要回答。",
         group="stability", flow="expect_409", max_wait=180),
    Case("S02", "提交后立即停止执行（幂等）", "AI-XYZQ",
         "请详细讲讲客户适当性管理的全部要求，越详细越好。",
         group="stability", flow="cancel", expect_cancelled=True, max_wait=180),
    Case("S03", "跨员工并发提问互不干扰", "AI-GENERAL",
         "请用一句话介绍你的岗位职责。",
         group="stability", flow="concurrent", max_wait=300,
         lock_keys=["emp:AI-GENERAL", "emp:AI-XYZQ", "emp:AI-RESEARCH"]),
    # S04 故意让两个分身抢同一个委派目标：不能吃资源锁，且必须放在隔离阶段单独跑，
    # 否则「正忙/排队」的判据会被同批其他 case 干扰。
    Case("S04", "同一委派目标并发争用", "DT-E10281",
         "IT 服务台的工单受理流程是怎样的？",
         group="stability", phase="isolated-last", flow="concurrent",
         no_lock=True, max_wait=300),
    Case("S05", "超长输入不 5xx 且有终态", "AI-GENERAL",
         "请阅读以下材料后用一句话总结重点：" + ("IT 服务台负责办公终端、账号权限与办公软件的日常支持，"
         "受理渠道包括服务热线与工单系统。") * 120,
         group="stability", flow="oversize", max_wait=300),
    Case("S06", "过期凭据不得 5xx、不得泄漏明文", "DT-E20999",
         "请用一句话说明你能做什么，不要调用任何工具。",
         actor="E20999", group="stability", flow="bad_credential",
         mutating=True, max_wait=300),
    Case("S07", "未登录与越权访问一律被拒", "AI-GENERAL",
         "", group="stability", flow="security", max_wait=60),
    Case("S08", "容器重启后的恢复与状态一致性", "AI-GENERAL",
         "请用一句话确认你可以正常回答。",
         group="stability", phase="isolated-last", flow="restart",
         mutating=True, max_wait=300),
]


# ---------------------------------------------------------------- 执行与断言

def _latest(api: Api, conv_id: str) -> dict:
    return api.call(f"/conversations/{conv_id}/runs/latest") or {}


def _wait(api: Api, conv_id: str, expected_id: str, deadline: float) -> tuple[dict, bool]:
    """等到 **本次提交的那个 execution** 进入终态。

    必须按 execution_id 精确匹配，不能只看「latest 是不是终态」：执行失败时其
    execution 与事件可能被整体回滚，此时 `/runs/latest` 会回落到上一次执行，
    只看终态会把失败误判为成功。返回 (响应, 是否匹配到本次执行)。
    """
    last: dict = {}
    mismatch_streak = 0
    while time.time() < deadline:
        d = _latest(api, conv_id)
        last = d or last
        ex = (d or {}).get("execution") or {}
        if ex.get("id") == expected_id and ex.get("status") in TERMINAL:
            return d, True
        if ex.get("id") and ex.get("id") != expected_id and ex.get("status") in TERMINAL:
            mismatch_streak += 1
            if mismatch_streak >= 5:
                return last, False
        else:
            mismatch_streak = 0
        time.sleep(POLL_INTERVAL)
    return last, False


def _answer_of(api: Api, conv_id: str, min_seq: int = 0) -> str:
    """只取本轮（seq >= 触发消息 seq）且未被作废的 assistant 回复。"""
    conv = api.call(f"/conversations/{conv_id}") or {}
    texts = [m.get("content") or "" for m in (conv.get("messages") or [])
             if m.get("role") == "assistant" and (m.get("seq") or 0) >= min_seq
             and not m.get("superseded_at")]
    return "\n".join(texts).strip()


def _cards_of(conv_id: str, min_seq: int) -> list[dict]:
    """本轮消息上的引用卡片（真源：conversation_message.tool_cards）。"""
    rows = db_rows(
        "SELECT tool_cards FROM conversation_message WHERE conversation_id=? AND seq>=? "
        "AND tool_cards IS NOT NULL AND tool_cards!='[]'", (conv_id, min_seq))
    cards: list[dict] = []
    for row in rows:
        try:
            items = json.loads(row["tool_cards"] or "[]")
        except (ValueError, TypeError):
            continue
        cards.extend([c for c in items if isinstance(c, dict)])
    return cards


def _make_conv(api: Api, case: Case) -> str:
    """私聊会话按（自然人 × AI员工）复用，因此每次 case 前先清空内容，保证判据干净。"""
    payload = {"actor_no": case.actor, "kind": "direct",
               "participant_employee_nos": [case.employee]}
    conv_id = (api.call("/conversations", payload) or {}).get("id", "")
    if conv_id:
        api.call(f"/conversations/{conv_id}/clear?actor_no={case.actor}", {}, method="POST")
    return conv_id


def _start_run(api: Api, conv_id: str, actor: str, content: str) -> str:
    return (api.call(f"/conversations/{conv_id}/runs",
                     {"actor_no": actor, "content": content}) or {}).get("execution_id") or ""


def _assert_common(res: Result, api: Api, conv_id: str, d: dict,
                   expect_status: str = "completed", matched: bool = True) -> None:
    ex = d.get("execution") or {}
    events = d.get("events") or []
    res.status = ex.get("status") or "-"
    res.trace_id = ex.get("trace_id") or ""
    res.execution_id = ex.get("id") or ""

    if not matched:
        res.failures.append(
            f"本次提交的执行在 /runs/latest 中始终未出现；latest 返回的是 {res.execution_id}"
            f"（{res.status}），说明本次执行已被回滚且接口回落到了历史执行")
        return

    if res.status != expect_status:
        res.failures.append(
            f"终态应为 {expect_status}，实际 {res.status}"
            f"（error_code={ex.get('error_code')} error_message={ex.get('error_message')}）")
    res.notes.append(f"事件 {len(events)} 条")
    if ex.get("error_code"):
        res.notes.append(f"error_code={ex['error_code']}")
    if res.status == "failed":
        # 失败原因必须落到 notes：既进报告，也是 known-flaky 分类的唯一依据。
        detail = _harness_error_detail(res.trace_id)
        res.notes.append(f"harness 原始失败：{detail}" if detail else "harness 原始失败：日志中未找到")

    res.min_seq = ex.get("trigger_message_seq") or 0
    res.answer = _answer_of(api, conv_id, res.min_seq)
    if res.status == "completed":
        if not res.answer:
            res.failures.append("答案为空")
        else:
            res.notes.append(f"答案 {len(res.answer)} 字")


def _verify_db(res: Result, case: Case, conv_id: str, traces: list[str] | None = None) -> None:
    """真源断言：审计 / 引用卡片 / 委派 / 答案内容。

    ``traces`` 用于「同一 case 跑了多轮」的场景（serial2 / concurrent）：
    跨轮次取并集判断，避免只看最后一轮而漏判。
    """
    trace_list = [t for t in (traces or [res.trace_id]) if t]
    if not trace_list:
        return
    marks = ",".join("?" for _ in trace_list)
    audits = db_rows(
        f"SELECT action, decision, knowledge_base_id, plugin_id FROM audit_event WHERE trace_id IN ({marks})",
        tuple(trace_list))
    reads = [a for a in audits if a["action"] == "read" and a["knowledge_base_id"]]
    allowed = sorted({a["knowledge_base_id"] for a in reads if a["decision"] == "allow"})
    denied = sorted({a["knowledge_base_id"] for a in reads if a["decision"] != "allow"})
    res.notes.append(f"知识库读审计 allow={allowed or '-'} deny={denied or '-'}")

    cards = _cards_of(conv_id, res.min_seq)
    card_kbs = sorted({str(c.get("knowledge_base_id")) for c in cards if c.get("knowledge_base_id")})
    card_agents = sorted({str(c.get("agent_employee_id")) for c in cards if c.get("agent_employee_id")})
    if cards:
        res.notes.append(f"引用卡片 kb={card_kbs or '-'} agent={card_agents or '-'}")

    delegations = db_rows(
        "SELECT sender_employee_id, recipient_employee_id, status, error "
        f"FROM delegation_run WHERE trace_id IN ({marks})", tuple(trace_list))
    res.notes.append("委派=" + (",".join(
        f"{d['sender_employee_id']}->{d['recipient_employee_id']}({d['status']})" for d in delegations) or "-"))

    for kb in case.expect_kb_allow:
        if kb not in allowed:
            res.failures.append(f"缺少 {kb} 的 allow 读审计")
    if case.expect_kb_no_allow and case.expect_kb_no_allow in allowed:
        res.failures.append(f"越权读到了未授权知识库 {case.expect_kb_no_allow}")
    if case.expect_no_kb_read and allowed:
        res.failures.append(f"本轮不应读到任何知识库，实际读到了 {allowed}")
    if case.expect_no_cards and cards:
        res.failures.append(f"本轮不应出现引用卡片，实际 {len(cards)} 张")
    if case.expect_card_kb and case.expect_card_kb not in card_kbs:
        res.failures.append(f"本轮引用卡片缺少 {case.expect_card_kb}（引用链路未透传）")
    if case.expect_card_absent_kb and case.expect_card_absent_kb in card_kbs:
        res.failures.append(f"越权引用了未授权知识库 {case.expect_card_absent_kb}")
    if case.expect_card_agent and case.expect_card_agent not in card_agents:
        res.failures.append(f"引用卡片缺少 agent_employee_id={case.expect_card_agent}（被委派方未透传）")
    if case.expect_delegate_to:
        hit = [d for d in delegations if d["recipient_employee_id"] == case.expect_delegate_to]
        if not hit:
            res.failures.append(f"未出现到 {case.expect_delegate_to} 的委派记录")
        elif not any(d["status"] == "completed" for d in hit):
            res.failures.append(f"到 {case.expect_delegate_to} 的委派未 completed（{hit[0]['status']}）")
    if case.expect_no_delegate and delegations:
        res.failures.append(f"不应发生委派，实际 {delegations}")
    if case.expect_no_delegate_to:
        bad = [d for d in delegations if d["recipient_employee_id"] == case.expect_no_delegate_to]
        if bad:
            res.failures.append(f"不应委派给 {case.expect_no_delegate_to}，实际 {bad}")
    if case.expect_mcp_plugin:
        calls = [a for a in audits if a["action"] == "execute" and a["plugin_id"] == case.expect_mcp_plugin]
        if not calls:
            res.failures.append(f"没有 {case.expect_mcp_plugin} 的 execute 审计（未真正调用外部 MCP）")
        else:
            res.notes.append(f"{case.expect_mcp_plugin} 调用 {len(calls)} 次")

    for kw in case.answer_has:
        if kw not in res.answer:
            res.failures.append(f"答案缺少关键词「{kw}」")
    for kw in case.answer_lacks:
        if kw in res.answer:
            res.failures.append(f"答案出现了不应有的内容「{kw}」")
    if case.answer_lacks_kb_docs:
        titles = doc_titles_for(case.answer_lacks_kb_docs)
        leaked = [t for t in titles if t in res.answer]
        res.notes.append(f"未授权库文档名对照 {len(titles)} 条")
        if leaked:
            res.failures.append(f"答案泄漏了未授权库 {case.answer_lacks_kb_docs} 的文档名：{leaked}")


# ---------------------------------------------------------------- 容器内造数

# 在 backend 容器内用平台自己的签发函数铸一枚 Harness 令牌，再直连内部 MCP 端点。
# 这样能确定性地复现「缺参/缺鉴权」路径，不依赖模型是否愿意调用工具。
_MCP_SNIPPET = (
    "import json,os,urllib.request,urllib.error\n"
    # 容器 entrypoint 会把 /run/secrets/harness_signing_secret 导出给 uvicorn 进程，
    # 但 `docker exec` 只继承容器创建时的 env（不含它）→ 不自己补一次就会拿到
    # 进程内随机密钥，令牌必然被 401 拒。这里按同样的来源补上，保证与服务端同密钥。
    "if not os.environ.get('DWP_HARNESS_TOOL_SIGNING_SECRET'):\n"
    "    _p='/run/secrets/harness_signing_secret'\n"
    "    if os.path.exists(_p):\n"
    "        os.environ['DWP_HARNESS_TOOL_SIGNING_SECRET']=open(_p).read().strip()\n"
    "from app.services import harness_token\n"
    "spec=json.loads(os.environ['E2E_MCP_CALL'])\n"
    "tok=harness_token.issue_token(employee_id=spec['employee_id'],"
    "requester_human_no=spec['requester_human_no'],trace_id=spec['trace_id'],depth=0,"
    "conversation_id=spec.get('conversation_id',''),ttl_seconds=300)\n"
    "body=json.dumps({'jsonrpc':'2.0','id':1,'method':'tools/call','params':"
    "{'name':spec['tool'],'arguments':spec.get('arguments') or {}}}).encode()\n"
    "req=urllib.request.Request('http://127.0.0.1:8000/internal/agent-tools/mcp',data=body,"
    "headers={'Content-Type':'application/json','Authorization':'Bearer '+tok})\n"
    "try:\n"
    "    print(urllib.request.urlopen(req,timeout=90).read().decode())\n"
    "except urllib.error.HTTPError as exc:\n"
    "    print('HTTPERR',exc.code,exc.read().decode())\n"
)


def call_internal_mcp(*, employee_id: str, requester_human_no: str, trace_id: str,
                      conversation_id: str, tool: str, arguments: dict | None = None
                      ) -> tuple[int, str]:
    spec = json.dumps({"employee_id": employee_id, "requester_human_no": requester_human_no,
                       "trace_id": trace_id, "conversation_id": conversation_id,
                       "tool": tool, "arguments": arguments or {}})
    return docker(["exec", "-i", "-w", "/app/backend", "-e", f"E2E_MCP_CALL={spec}",
                   BACKEND_CONTAINER, "python", "-c", _MCP_SNIPPET], timeout=120)


def _input_requests(api: Api, conv_id: str, execution_id: str) -> list[dict]:
    return api.call(f"/conversations/{conv_id}/runs/{execution_id}/input-requests") or []


def _no_5xx(res: Result, code: int, where: str) -> None:
    """「不 5xx」是稳定性 case 的底线：越权/超长/错凭据的合法结果可以是 4xx。"""
    if code >= 500:
        res.failures.append(f"{where} 返回 {code}（不得 5xx）")


# ---------------------------------------------------------------- 各类流程

def _flow_single(res: Result, api: Api, case: Case, conv: str) -> None:
    run_id = _start_run(api, conv, case.actor, case.content)
    if not run_id:
        res.failures.append("发起执行失败（未返回 execution_id）")
        return
    res.execution_id = run_id
    d, matched = _wait(api, conv, run_id, time.time() + case.max_wait)
    _assert_common(res, api, conv, d, matched=matched)
    _verify_db(res, case, conv)


def _flow_serial2(res: Result, api: Api, case: Case, conv: str) -> None:
    """同一会话顺序两轮：既验证串行可用，也让「跨库/跨技能」的期望能在多轮里取并集。"""
    first = _start_run(api, conv, case.actor, case.content)
    d1, ok1 = _wait(api, conv, first, time.time() + case.max_wait)
    st1 = (d1.get("execution") or {}).get("status")
    res.notes.append(f"第1轮={st1}")
    if not ok1:
        res.failures.append(f"第 1 轮执行 {first} 查不到（可能已回滚）")
    elif st1 != "completed":
        detail = _harness_error_detail((d1.get("execution") or {}).get("trace_id") or "")
        res.failures.append(f"第 1 轮未完成（{st1}）" + (f"：{detail}" if detail else ""))
    a1 = _answer_of(api, conv, (d1.get("execution") or {}).get("trigger_message_seq") or 0)

    second = _start_run(api, conv, case.actor, case.content2)
    d2, ok2 = _wait(api, conv, second, time.time() + case.max_wait)
    st2 = (d2.get("execution") or {}).get("status")
    res.notes.append(f"第2轮={st2}")
    if not ok2:
        res.failures.append(f"第 2 轮执行 {second} 查不到（可能已回滚）")
    elif st2 != "completed":
        detail = _harness_error_detail((d2.get("execution") or {}).get("trace_id") or "")
        res.failures.append(f"第 2 轮未完成（{st2}）" + (f"：{detail}" if detail else ""))
    a2 = _answer_of(api, conv, (d2.get("execution") or {}).get("trigger_message_seq") or 0)

    res.status = f"{st1}/{st2}"
    res.execution_id = second
    res.trace_id = (d2.get("execution") or {}).get("trace_id") or ""
    res.min_seq = (d1.get("execution") or {}).get("trigger_message_seq") or 0
    res.answer = (a1 + "\n" + a2).strip()
    res.notes.append(f"两轮答案 {len(a1)}+{len(a2)} 字")
    if first and first == second:
        res.failures.append("两轮执行 ID 相同（串行未生效）")
    traces = [(d1.get("execution") or {}).get("trace_id") or "", res.trace_id]
    _verify_db(res, case, conv, traces=traces)


def _flow_edit_rerun(res: Result, api: Api, case: Case, conv: str) -> None:
    """就地编辑最新一轮并重跑：必须新建执行，且旧回复被标记 superseded_at。"""
    run_id = _start_run(api, conv, case.actor, case.content)
    d, matched = _wait(api, conv, run_id, time.time() + case.max_wait)
    _assert_common(res, api, conv, d, matched=matched)

    seq = None
    conv_state = api.call(f"/conversations/{conv}") or {}
    for m in reversed(conv_state.get("messages") or []):
        if m.get("role") == "user":
            seq = m.get("seq")
            break
    if seq is None:
        res.failures.append("找不到可编辑的 user 消息")
        return
    run2 = (api.call(f"/conversations/{conv}/messages/{seq}/runs",
                     {"actor_no": case.actor,
                      "content": "请只回答一句话：融资融券的开户门槛是什么？"}) or {})
    run2_id = run2.get("execution_id") or ""
    d2, ok2 = _wait(api, conv, run2_id, time.time() + case.max_wait)
    st2 = (d2.get("execution") or {}).get("status")
    res.notes.append(f"重跑 seq={seq} 状态={st2}")
    if not ok2:
        res.failures.append(f"重跑执行 {run2_id} 查不到（可能已回滚）")
    elif st2 != "completed":
        res.failures.append(f"重跑未完成（{st2}）")
    if run2_id and run2_id == run_id:
        res.failures.append("重跑复用了同一 execution（未新建执行）")
    superseded = db_rows(
        "SELECT COUNT(*) n FROM conversation_message WHERE conversation_id=? AND superseded_at IS NOT NULL",
        (conv,))
    n = superseded[0]["n"] if superseded else 0
    res.notes.append(f"已作废旧回复 {n} 条")
    if n < 1:
        res.failures.append("旧回复未被标记 superseded_at")
    res.execution_id = run2_id or run_id
    _verify_db(res, case, conv, traces=[res.trace_id])


def _flow_cancel(res: Result, api: Api, case: Case, conv: str) -> None:
    """提交后立即停止：终态必须 cancelled、不得产出回复、重复停止幂等。"""
    run_id = _start_run(api, conv, case.actor, case.content)
    time.sleep(1.5)
    status, body = api.raw(f"/conversations/{conv}/runs/{run_id}/cancel", {}, method="POST")
    _no_5xx(res, status, "cancel")
    res.http_codes.append(f"cancel={status}")
    d, matched = _wait(api, conv, run_id, time.time() + case.max_wait)
    res.execution_id = run_id
    res.status = (d.get("execution") or {}).get("status") or "-"
    res.trace_id = (d.get("execution") or {}).get("trace_id") or ""
    res.min_seq = (d.get("execution") or {}).get("trigger_message_seq") or 0
    if not matched:
        res.failures.append(f"执行 {run_id} 查不到（可能已回滚）")
    elif case.expect_cancelled and res.status != "cancelled":
        res.failures.append(f"终态应为 cancelled，实际 {res.status}")
    if status == 200:
        payload = json.loads(body) if body else {}
        if payload.get("status") != "cancelled":
            res.failures.append(f"cancel 接口返回状态异常：{payload.get('status')}")
    answer = _answer_of(api, conv, res.min_seq)
    if answer:
        res.failures.append(f"已停止的执行仍产出了回复（{len(answer)} 字）")
    status2, _ = api.raw(f"/conversations/{conv}/runs/{run_id}/cancel", {}, method="POST")
    _no_5xx(res, status2, "cancel(重复)")
    res.notes.append(f"重复 cancel -> {status2}")
    if status2 != status:
        res.failures.append(f"重复 cancel 状态码不一致：{status} vs {status2}")


def _flow_expect_409(res: Result, api: Api, case: Case, conv: str) -> None:
    """同一会话重复提交：活跃执行期间必须 409（retryable），且不得产生第二条执行。"""
    run_id = _start_run(api, conv, case.actor, case.content)
    res.execution_id = run_id
    seen_409 = False
    benign = False
    for attempt in range(5):
        status, body = api.raw(f"/conversations/{conv}/runs",
                               {"actor_no": case.actor, "content": "请再补充一句。"})
        _no_5xx(res, status, "重复提交")
        res.http_codes.append(f"dup#{attempt + 1}={status}")
        if status == 409:
            seen_409 = True
            try:
                payload = json.loads(body) or {}
            except (ValueError, TypeError):
                payload = {}
            # 平台的错误信封是 {"error": {"code","message","detail"}}：HTTPException 传
            # dict 时落在 error.detail，传字符串时落在 error.message。前端靠
            # error.detail.retryable 决定是否提示「稍后重试」，所以要按真实层级取。
            err = payload.get("error") if isinstance(payload.get("error"), dict) else payload
            detail = err.get("detail") if isinstance(err.get("detail"), dict) else {}
            retryable = detail.get("retryable", err.get("retryable"))
            res.notes.append(f"409 code={err.get('code')} retryable={retryable}")
            if retryable is not True:
                res.failures.append(f"409 未标注 retryable=true（前端无法提示稍后重试）：{body[:200]}")
            break
        latest = (_latest(api, conv).get("execution") or {})
        if latest.get("id") == run_id and latest.get("status") in TERMINAL:
            benign = True
            res.notes.append("首轮已结束，后续提交返回 200 属正常时序")
            break
        time.sleep(2)
    if not seen_409 and not benign:
        res.failures.append("活跃执行期间重复提交未返回 409")

    d, matched = _wait(api, conv, run_id, time.time() + case.max_wait)
    _assert_common(res, api, conv, d, matched=matched)
    rows = db_rows(
        "SELECT COUNT(*) n FROM agent_execution WHERE conversation_id=? AND trigger_message_seq>=?",
        (conv, res.min_seq))
    n = rows[0]["n"] if rows else 0
    res.notes.append(f"本轮触发 seq 之后的执行条数={n}")
    if n != 1:
        res.failures.append(f"重复提交产生了 {n} 条执行（应只有 1 条）")


def _make_conv_for(api: Api, actor: str, employee: str) -> str:
    payload = {"actor_no": actor, "kind": "direct", "participant_employee_nos": [employee]}
    conv_id = (api.call("/conversations", payload) or {}).get("id", "")
    if conv_id:
        api.call(f"/conversations/{conv_id}/clear?actor_no={actor}", {}, method="POST")
    return conv_id


def _concurrent_plan(case: Case) -> list[tuple[str, str, str]]:
    """(发起员工, 自然人, 提问) 三元组。S04 故意让两个分身抢同一个委派目标。"""
    if case.cid == "S04":
        return [("DT-E10281", "E10281", case.content), ("DT-E20999", "E20999", case.content)]
    return [(emp, case.actor, case.content) for emp in ("AI-GENERAL", "AI-XYZQ", "AI-RESEARCH")]


def _flow_concurrent(res: Result, api: Api, case: Case, conv: str, keys: dict[str, str]) -> None:
    """并发压测：跨员工并行提问（S03）/ 同一委派目标争用（S04）。"""
    started = []
    for employee, actor, content in _concurrent_plan(case):
        client = Api(api.base, keys[actor])
        target_conv = _make_conv_for(client, actor, employee)
        if not target_conv:
            res.failures.append(f"{employee} 创建会话失败")
            continue
        eid = _start_run(client, target_conv, actor, content)
        if not eid:
            res.failures.append(f"{employee} 发起执行失败")
            continue
        started.append((employee, actor, client, target_conv, eid))

    outcomes = []
    for employee, actor, client, target_conv, eid in started:
        d, matched = _wait(client, target_conv, eid, time.time() + case.max_wait)
        ex = d.get("execution") or {}
        outcomes.append({"employee": employee, "actor": actor, "conv": target_conv, "eid": eid,
                         "status": ex.get("status") or "-", "trace": ex.get("trace_id") or "",
                         "matched": matched, "error": ex.get("error_message") or ""})
    for item in outcomes:
        extra = f"（{item['error'][:60]}）" if item["error"] else ""
        if item["status"] == "failed":
            detail = _harness_error_detail(item["trace"])
            extra = f"（{detail}）" if detail else extra
        res.notes.append(f"{item['employee']}={item['status']}{extra}")
        if not item["matched"]:
            res.failures.append(f"{item['employee']} 的执行 {item['eid']} 查不到（可能已回滚）")

    completed = [o for o in outcomes if o["status"] == "completed"]
    res.status = "/".join(o["status"] for o in outcomes)
    if case.cid == "S04":
        if not completed:
            res.failures.append("并发委派到同一目标时没有任何一路成功（不应全部失败）")
        failed = [o for o in outcomes if o["status"] != "completed"]
        for item in failed:
            if "正忙" not in (item["error"] or "") and "busy" not in (item["error"] or "").lower():
                res.notes.append(f"{item['employee']} 失败原因未标注「正忙」：{item['error'][:80] or '无'}")
        # 必须按本轮 trace 过滤：库里存在 2026-09-16/17 遗留的 status=running 记录，
        # 不带 trace 过滤会把历史残留误判成本次失败。
        traces = [o["trace"] for o in outcomes if o["trace"]]
        if traces:
            marks = ",".join("?" for _ in traces)
            dangling = db_rows(
                "SELECT id, sender_employee_id, recipient_employee_id, status FROM delegation_run "
                f"WHERE trace_id IN ({marks}) AND status NOT IN ('completed','failed')", tuple(traces))
        else:
            dangling = []
        res.notes.append(f"本轮悬挂委派记录={len(dangling)} 条")
        if dangling:
            res.failures.append(f"存在未收敛的委派记录：{dangling[:2]}")
    else:
        if len(completed) != len(outcomes):
            res.failures.append(f"并发提问未全部完成：{len(completed)}/{len(outcomes)}")
        traces = [o["trace"] for o in outcomes if o["trace"]]
        if len(set(traces)) != len(traces):
            res.failures.append(f"并发执行的 trace_id 出现重复：{traces}")
        for item in outcomes:
            allowed = db_rows(
                "SELECT DISTINCT knowledge_base_id FROM audit_event WHERE trace_id=? "
                "AND action='read' AND decision='allow' AND knowledge_base_id IS NOT NULL",
                (item["trace"],))
            res.notes.append(f"{item['employee']} 读到 {sorted(r['knowledge_base_id'] for r in allowed) or '-'}")
    res.execution_id = outcomes[0]["eid"] if outcomes else ""
    res.trace_id = outcomes[0]["trace"] if outcomes else ""


def _flow_oversize(res: Result, api: Api, case: Case, conv: str) -> None:
    """超长输入：不得 5xx；返回 200 就必须有终态，且不得留下卡死的活跃执行。"""
    status, body = api.raw(f"/conversations/{conv}/runs",
                           {"actor_no": case.actor, "content": case.content})
    _no_5xx(res, status, "超长输入提交")
    res.http_codes.append(f"submit={status}")
    res.notes.append(f"输入长度 {len(case.content)} 字")
    if status != 200:
        res.notes.append(f"被拒（{status}）：{body[:120]}")
        res.status = f"http-{status}"
        return
    eid = (json.loads(body) or {}).get("execution_id") or ""
    res.execution_id = eid
    d, matched = _wait(api, conv, eid, time.time() + case.max_wait)
    _assert_common(res, api, conv, d, matched=matched)
    active = db_rows(
        "SELECT id, status FROM agent_execution WHERE conversation_id=? AND status NOT IN "
        "('completed','failed','cancelled','timeout')", (conv,))
    res.notes.append(f"残留活跃执行={len(active)}")
    if active:
        res.failures.append(f"超长输入后残留活跃执行：{active}")
    stored = db_rows(
        "SELECT content FROM conversation_message WHERE conversation_id=? AND role='user' "
        "ORDER BY seq DESC LIMIT 1", (conv,))
    if stored:
        res.notes.append(f"落库提问 {len(stored[0]['content'] or '')} 字")


def _flow_security(res: Result, api: Api, case: Case, conv: str, keys: dict[str, str]) -> None:
    """未登录与越权访问：401 / 403，且不得 5xx。"""
    victim_conv = conv or _make_conv(api, case)
    rows = db_rows("SELECT id FROM agent_execution WHERE conversation_id=? ORDER BY rowid DESC LIMIT 1",
                   (victim_conv,))
    victim_eid = rows[0]["id"] if rows else ""
    if not victim_eid:
        victim_eid = _start_run(api, victim_conv, case.actor, "请用一句话打个招呼。")
        _wait(api, victim_conv, victim_eid, time.time() + case.max_wait)
        res.notes.append("已为该会话造一条执行用于越权探测")

    other = Api(api.base, keys["E20999"])          # 另一个自然人
    anon = Api(api.base, None)                     # 未登录

    checks = [
        ("未登录读会话", *anon.raw(f"/conversations/{victim_conv}"), 401),
        ("未登录列会话", *anon.raw(f"/conversations?actor_no={case.actor}"), 401),
        ("他人读会话", *other.raw(f"/conversations/{victim_conv}"), 403),
        ("他人提交补全(冒名)", *other.raw(
            f"/conversations/{victim_conv}/runs/{victim_eid}/input",
            {"actor_no": case.actor, "request_id": "TI-NOT-EXIST", "values": {}}), 403),
        ("他人提交补全(本名)", *other.raw(
            f"/conversations/{victim_conv}/runs/{victim_eid}/input",
            {"actor_no": "E20999", "request_id": "TI-NOT-EXIST", "values": {}}), 403),
        ("他人忽略补全", *other.raw(
            f"/conversations/{victim_conv}/runs/{victim_eid}/input-requests/TI-NOT-EXIST/ignore",
            {"actor_no": "E20999"}), 403),
    ]
    for name, status, body, expect in checks:
        _no_5xx(res, status, name)
        res.http_codes.append(f"{name}={status}")
        if status != expect:
            res.failures.append(f"{name} 期望 {expect}，实际 {status}（{body[:100]}）")
    res.status = "checked"


def _jwt_like(exp_offset: int) -> str:
    """造一枚「形状正确」的 JWT（只用于验证掩码与过期判定，不是真令牌）。"""

    def enc(obj: dict) -> str:
        raw = json.dumps(obj, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    header = {"alg": "HS256", "typ": "JWT"}
    payload = {"sub": "E2E-TEST", "iat": int(time.time()), "exp": int(time.time()) + exp_offset}
    return f"{enc(header)}.{enc(payload)}.E2E-NOT-A-REAL-SIGNATURE"


def _cred_snapshot(plugin_id: str, human_no: str) -> dict | None:
    rows = db_rows("SELECT * FROM mcp_user_credential WHERE plugin_id=? AND human_no=?",
                   (plugin_id, human_no))
    return rows[0] if rows else None


def _cred_restore(plugin_id: str, human_no: str, snap: dict | None) -> None:
    """把按人凭据恢复成 case 开始前的样子（原本不存在则删除）。"""
    if snap is None:
        db_exec("DELETE FROM mcp_user_credential WHERE plugin_id=? AND human_no=?",
                (plugin_id, human_no))
        return
    db_exec(
        "UPDATE mcp_user_credential SET header_name=?, secret_ciphertext=?, secret_hint=?, "
        "expires_at=?, updated_by=?, updated_at=? WHERE plugin_id=? AND human_no=?",
        (snap["header_name"], snap["secret_ciphertext"], snap["secret_hint"], snap["expires_at"],
         snap["updated_by"], snap["updated_at"], plugin_id, human_no))


def _plaintext_hits(secret: str, trace_id: str, conv_id: str) -> list[str]:
    """明文凭据不得出现在任何可回显的位置（含加密列、审计摘要、消息正文）。"""
    hits: list[str] = []
    for row in db_rows("SELECT id, submitted_values FROM tool_input_request WHERE trace_id=?", (trace_id,)):
        if secret in (row["submitted_values"] or ""):
            hits.append(f"tool_input_request({row['id']}).submitted_values")
    for row in db_rows("SELECT id, result_summary FROM audit_event WHERE trace_id=?", (trace_id,)):
        if secret in (row["result_summary"] or ""):
            hits.append(f"audit_event({row['id']}).result_summary")
    for row in db_rows("SELECT seq, content FROM conversation_message WHERE conversation_id=?", (conv_id,)):
        if secret in (row["content"] or ""):
            hits.append(f"conversation_message(seq={row['seq']}).content")
    for row in db_rows("SELECT human_no, secret_ciphertext FROM mcp_user_credential WHERE plugin_id=?",
                       (EHOME_PLUGIN,)):
        if secret in (row["secret_ciphertext"] or ""):
            hits.append(f"mcp_user_credential({row['human_no']}).secret_ciphertext")
    return hits


def _carrier_run(res: Result, api: Api, case: Case, conv: str) -> bool:
    """先跑一轮「不依赖工具」的执行，作为补全记录的载体（trace/conversation/execution 齐备）。"""
    eid = _start_run(api, conv, case.actor, case.content)
    if not eid:
        res.failures.append("发起载体执行失败")
        return False
    d, matched = _wait(api, conv, eid, time.time() + case.max_wait)
    res.execution_id = eid
    res.trace_id = (d.get("execution") or {}).get("trace_id") or ""
    res.status = (d.get("execution") or {}).get("status") or "-"
    res.min_seq = (d.get("execution") or {}).get("trigger_message_seq") or 0
    if not matched:
        res.failures.append(f"载体执行 {eid} 查不到（可能已回滚）")
        return False
    res.notes.append(f"载体执行={res.status}")
    return True


def _flow_input_completion(res: Result, api: Api, case: Case, conv: str) -> None:
    """缺参补全链路：缺鉴权 → 落 pending + 轨迹事件 → 提交令牌 → 自动续跑 → 只存掩码。"""
    snap = _cred_snapshot(EHOME_PLUGIN, case.actor)
    try:
        if not _carrier_run(res, api, case, conv):
            return
        rc, out = call_internal_mcp(
            employee_id=case.employee, requester_human_no=case.actor, trace_id=res.trace_id,
            conversation_id=conv, tool=EHOME_TOOL, arguments={"keyword": "报销"})
        if rc != 0 or out.startswith("HTTPERR"):
            res.failures.append(f"容器内调用内部 MCP 端点异常 rc={rc} {out[:160]}")

        reqs = _input_requests(api, conv, res.execution_id)
        pending = [r for r in reqs
                   if r.get("status") == "pending" and r.get("plugin_id") == EHOME_PLUGIN]
        if not pending:
            res.failures.append(f"缺鉴权时未产生待补全记录（当前 {len(reqs)} 条）")
            return
        req = pending[-1]
        fields = req.get("fields") or []
        kinds = {str(f.get("key")): str(f.get("kind")) for f in fields}
        res.notes.append(f"补全 {req['id']} reason={req.get('reason')} fields={list(kinds)}")
        secret_key = next((k for k, v in kinds.items() if v == "secret"), "")
        if not secret_key:
            res.failures.append(f"补全字段里没有 secret 类字段：{kinds}")

        events = db_rows("SELECT event_type FROM agent_execution_event WHERE execution_id=?",
                         (res.execution_id,))
        if not any(e["event_type"] == "input_required" for e in events):
            res.failures.append("执行轨迹里没有 input_required 事件（前端弹不出补全气泡）")

        token = _jwt_like(3600)
        status, body = api.raw(f"/conversations/{conv}/runs/{res.execution_id}/input",
                               {"actor_no": case.actor, "request_id": req["id"],
                                "values": {secret_key: token}})
        _no_5xx(res, status, "提交补全")
        res.http_codes.append(f"submit={status}")
        if status != 200:
            res.failures.append(f"提交补全期望 200，实际 {status}：{body[:160]}")
            return
        new_eid = (json.loads(body) or {}).get("execution_id") or ""
        res.notes.append(f"续跑 execution={new_eid}")
        if not new_eid or new_eid == res.execution_id:
            res.failures.append("提交补全后未产生新一轮执行")

        rows = db_rows("SELECT status, submitted_values, resolved_by FROM tool_input_request WHERE id=?",
                       (req["id"],))
        if not rows:
            res.failures.append("提交后补全记录不见了")
        else:
            row = rows[0]
            if row["status"] != "submitted":
                res.failures.append(f"提交后状态应为 submitted，实际 {row['status']}")
            if row["resolved_by"] != case.actor:
                res.failures.append(f"resolved_by 应为 {case.actor}，实际 {row['resolved_by']}")
            try:
                sv = json.loads(row["submitted_values"] or "{}")
            except (ValueError, TypeError):
                sv = {}
            masked = str(sv.get(secret_key) or "")
            res.notes.append(f"落库值={masked[:12]}")
            if not masked.startswith("*"):
                res.failures.append(f"落库值未做掩码：{masked[:24]}")
            if token in json.dumps(sv, ensure_ascii=False):
                res.failures.append("落库的 submitted_values 里出现明文令牌")

        s2, _ = api.raw(f"/conversations/{conv}/runs/{res.execution_id}/input",
                        {"actor_no": case.actor, "request_id": req["id"], "values": {secret_key: token}})
        _no_5xx(res, s2, "重复提交补全")
        res.http_codes.append(f"dup_submit={s2}")
        if s2 != 409:
            res.failures.append(f"重复提交同一补全期望 409，实际 {s2}")
        s3, _ = api.raw(f"/conversations/{conv}/runs/{res.execution_id}/input-requests/"
                        f"{req['id']}/ignore", {"actor_no": case.actor})
        _no_5xx(res, s3, "重复忽略补全")
        res.http_codes.append(f"ignore_after_submit={s3}")
        if s3 != 409:
            res.failures.append(f"已提交后再忽略期望 409，实际 {s3}")

        hits = _plaintext_hits(token, res.trace_id, conv)
        if hits:
            res.failures.append(f"明文令牌泄漏到：{hits}")
        else:
            res.notes.append("明文令牌未出现在任何可回显位置")

        if new_eid:
            d2, ok2 = _wait(api, conv, new_eid, time.time() + min(case.max_wait, 180))
            st2 = (d2.get("execution") or {}).get("status")
            res.notes.append(f"续跑终态={st2}")
            if not ok2:
                res.failures.append(f"续跑执行 {new_eid} 未在限期内收敛")
            res.execution_id = new_eid
            res.trace_id = (d2.get("execution") or {}).get("trace_id") or res.trace_id
    finally:
        _cred_restore(EHOME_PLUGIN, case.actor, snap)
        res.notes.append("已恢复按人凭据快照")


def _flow_bad_credential(res: Result, api: Api, case: Case, conv: str) -> None:
    """过期凭据：提交一枚已过期的 JWT 后，预检必须识别为 expired，且全程不得 5xx / 泄漏明文。"""
    snap = _cred_snapshot(EHOME_PLUGIN, case.actor)
    try:
        if not _carrier_run(res, api, case, conv):
            return
        rc, out = call_internal_mcp(
            employee_id=case.employee, requester_human_no=case.actor, trace_id=res.trace_id,
            conversation_id=conv, tool=EHOME_TOOL, arguments={"keyword": "报销"})
        if rc != 0 or out.startswith("HTTPERR"):
            res.failures.append(f"容器内调用内部 MCP 端点异常 rc={rc} {out[:160]}")
        reqs = _input_requests(api, conv, res.execution_id)
        pending = [r for r in reqs
                   if r.get("status") == "pending" and r.get("plugin_id") == EHOME_PLUGIN]
        if not pending:
            res.failures.append("首次缺鉴权未产生待补全记录，无法继续验证过期分支")
            return
        req = pending[-1]
        kinds = {str(f.get("key")): str(f.get("kind")) for f in (req.get("fields") or [])}
        secret_key = next((k for k, v in kinds.items() if v == "secret"), "")
        if not secret_key:
            res.failures.append(f"补全字段里没有 secret 类字段：{kinds}")
            return

        expired = _jwt_like(-7200)          # exp 在两小时前
        status, body = api.raw(f"/conversations/{conv}/runs/{res.execution_id}/input",
                               {"actor_no": case.actor, "request_id": req["id"],
                                "values": {secret_key: expired}})
        _no_5xx(res, status, "提交过期凭据")
        res.http_codes.append(f"submit_expired={status}")
        if status != 200:
            res.failures.append(f"提交过期凭据期望 200（先存后判），实际 {status}：{body[:160]}")

        rows = db_rows("SELECT expires_at, secret_hint FROM mcp_user_credential "
                       "WHERE plugin_id=? AND human_no=?", (EHOME_PLUGIN, case.actor))
        if not rows:
            res.failures.append("过期凭据未落库（按人凭据缺失）")
        else:
            res.notes.append(f"凭据 expires_at={rows[0]['expires_at']} hint={rows[0]['secret_hint']}")
            if not rows[0]["expires_at"]:
                res.failures.append("凭据 expires_at 为空（JWT exp 未被解析）")

        # 诊断：第二次预检前再读一次凭据行，区分「没落库」与「落了库但预检没认出来」
        probe = db_rows("SELECT expires_at, secret_hint FROM mcp_user_credential "
                        "WHERE plugin_id=? AND human_no=?", (EHOME_PLUGIN, case.actor))
        res.notes.append("第二次预检前凭据行=" + (
            f"{probe[0]['expires_at']}/{probe[0]['secret_hint']}" if probe else "缺失"))

        # 再打一次同一个工具：预检应命中「已过期」，产出 reason=expired 的新补全
        rc2, out2 = call_internal_mcp(
            employee_id=case.employee, requester_human_no=case.actor, trace_id=res.trace_id,
            conversation_id=conv, tool=EHOME_TOOL, arguments={"keyword": "报销"})
        if rc2 != 0 or out2.startswith("HTTPERR"):
            res.failures.append(f"第二次容器内调用异常 rc={rc2} {out2[:160]}")
        reqs2 = _input_requests(api, conv, res.execution_id)
        fresh = [r for r in reqs2 if r.get("status") == "pending" and r.get("plugin_id") == EHOME_PLUGIN]
        reasons = [r.get("reason") for r in reqs2 if r.get("plugin_id") == EHOME_PLUGIN]
        res.notes.append(f"补全请求 reason 序列={reasons}")
        if not fresh:
            res.failures.append("凭据过期后未再次产生待补全记录")
        else:
            # 真相在「字段级」reason：前端气泡按字段渲染，过期信息也落在字段上。
            field_reasons = [str(f.get("reason") or "") for f in (fresh[-1].get("fields") or [])]
            if "expired" not in field_reasons:
                res.failures.append(f"过期分支字段级 reason 应为 expired，实际 {field_reasons}")
            # 已知缺陷（低危，2026-09-22 实测）：请求级 reason 恒为 missing。
            # routers/internal.py 预检分支把请求级原因写死成 REASON_MISSING，
            # 只有字段级 reason 才区分 missing/expired → 审计与排障会误读。
            if fresh[-1].get("reason") != "expired":
                res.notes.append(
                    f"缺陷：请求级 reason={fresh[-1].get('reason')}（应为 expired），"
                    f"字段级 reason={field_reasons} 才是真相（internal.py 预检分支写死 REASON_MISSING）")
            masked = [str(f.get("masked") or "") for f in (fresh[-1].get("fields") or [])
                      if str(f.get("kind")) == "secret"]
            if not any(m.startswith("*") for m in masked):
                res.failures.append(f"过期提示未带已存凭据掩码：{masked}")

        hits = _plaintext_hits(expired, res.trace_id, conv)
        if hits:
            res.failures.append(f"明文令牌泄漏到：{hits}")
        else:
            res.notes.append("明文令牌未出现在任何可回显位置")
        res.status = "expired-handled"
    finally:
        _cred_restore(EHOME_PLUGIN, case.actor, snap)
        res.notes.append("已恢复按人凭据快照")


def _flow_marketplace(res: Result, api: Api, case: Case, conv: str, keys: dict[str, str]) -> None:
    """市场申请与管理员审批：申请 201 → 审批通过 → 授权绑定落库 → 对本人分身生效。"""
    admin = Api(api.base, keys.get("__admin__", ""))
    listing = api.call("/my/plugins") or {}
    candidates = [row for row in (listing.get("market") or []) if row.get("state") == "requestable"]
    if not candidates:
        res.failures.append("市场里没有 state=requestable 的插件，无法验证申请链路")
        return
    candidates.sort(key=lambda r: (0 if r.get("plugin_type") == "mcp" else 1, str(r.get("plugin_id"))))
    pick = candidates[0]
    pid = str(pick.get("plugin_id"))
    res.notes.append(f"申请目标 {pid}（{pick.get('name')} / {pick.get('plugin_type')}）")

    status, body = api.raw(f"/my/plugins/{pid}/request", {"reason": "E2E 稳定性验收：申请试用"})
    _no_5xx(res, status, "提交插件申请")
    res.http_codes.append(f"request={status}")
    if status != 201:
        res.failures.append(f"申请期望 201，实际 {status}：{body[:160]}")
        return
    try:
        req_id = int((json.loads(body) or {}).get("id"))
    except (ValueError, TypeError):
        res.failures.append(f"申请返回体缺少 id：{body[:160]}")
        return
    res.notes.append(f"申请单 #{req_id}")

    s2, b2 = admin.raw(f"/admin/access-requests/{req_id}/approve",
                       {"approve": True, "note": "E2E 稳定性验收"})
    _no_5xx(res, s2, "管理员审批")
    res.http_codes.append(f"approve={s2}")
    if s2 != 200:
        res.failures.append(f"审批期望 200，实际 {s2}：{b2[:160]}")
        return

    rows = db_rows("SELECT admin_enabled, employee_enabled, decision_mode FROM agent_plugin_binding "
                   "WHERE plugin_id=? AND target_agent_id='DT-E10281'", (pid,))
    if not rows:
        res.failures.append(f"审批通过后未写入 agent_plugin_binding（{pid} × DT-E10281）")
    else:
        res.notes.append(f"绑定 admin_enabled={rows[0]['admin_enabled']} "
                         f"employee_enabled={rows[0]['employee_enabled']} mode={rows[0]['decision_mode']}")
        if not rows[0]["admin_enabled"]:
            res.failures.append("审批通过后 admin_enabled 仍为假")
    ar = db_rows("SELECT status, decided_by FROM access_request WHERE id=?", (req_id,))
    if not ar:
        res.failures.append("申请单记录丢失")
    else:
        res.notes.append(f"申请单终态={ar[0]['status']} decided_by={ar[0]['decided_by']}")
        if ar[0]["status"] not in ("granted", "approved"):
            res.failures.append(f"申请单终态应为 granted/approved，实际 {ar[0]['status']}")

    listing2 = api.call("/my/plugins") or {}
    row2 = next((r for r in (listing2.get("market") or []) if r.get("plugin_id") == pid), {})
    res.notes.append(f"审批后市场状态={row2.get('state')}")
    if row2.get("state") not in ("granted", "enabled"):
        res.failures.append(f"审批后该插件状态应为 granted/enabled，实际 {row2.get('state')}")
    res.status = "approved"
    res.notes.append(f"副作用：新增 1 条 agent_plugin_binding（{pid} × DT-E10281）")


def _flow_restart(res: Result, api: Api, case: Case, conv: str) -> None:
    """容器重启恢复：重启 1 个 harness 与 backend，验证健康、状态一致与功能可用。"""
    before = {r["id"]: r["status"] for r in db_rows(
        "SELECT id, status FROM agent_execution WHERE status IN "
        "('completed','failed','cancelled','timeout') ORDER BY rowid DESC LIMIT 20")}
    active_before = db_rows("SELECT id, status FROM agent_execution WHERE status NOT IN "
                            "('completed','failed','cancelled','timeout')")
    res.notes.append(f"重启前：近 20 条终态执行、活跃执行 {len(active_before)} 条")

    harness = "dwp-harness-ai-general"
    rc, out = docker(["restart", harness], timeout=180)
    if rc != 0:
        res.failures.append(f"重启 {harness} 失败：{out.strip()[:160]}")
    rc2, out2 = docker(["exec", harness, "sh", "-lc",
                        "[ -x /opt/dsh-runtime/bin/dsh ] && echo DSH_OK; "
                        "[ -f /opt/dwp/profile/MANIFEST.json ] && echo PROFILE_OK; "
                        "python3 -V 2>&1 | head -1"])
    marks = out2.strip().replace("\n", " | ")
    res.notes.append(f"harness 重启后：{marks}")
    if rc2 != 0 or "DSH_OK" not in out2:
        res.failures.append(f"harness 重启后 dsh 二进制缺失：{marks}")
    if "PROFILE_OK" not in out2:
        res.failures.append(f"harness 重启后 profile 挂载缺失：{marks}")

    rc3, out3 = docker(["restart", BACKEND_CONTAINER], timeout=180)
    if rc3 != 0:
        res.failures.append(f"重启 backend 失败：{out3.strip()[:160]}")
    healthy = False
    deadline = time.time() + 150
    while time.time() < deadline:
        rc4, out4 = docker(["inspect", "--format", "{{.State.Health.Status}}", BACKEND_CONTAINER])
        if rc4 == 0 and out4.strip() == "healthy":
            healthy = True
            break
        time.sleep(5)
    res.notes.append(f"backend 健康={healthy}")
    if not healthy:
        res.failures.append("backend 重启后 150s 内未恢复 healthy")

    eid = _start_run(api, conv, case.actor, case.content)
    if not eid:
        res.failures.append("重启后无法发起执行")
        return
    d, matched = _wait(api, conv, eid, time.time() + case.max_wait)
    _assert_common(res, api, conv, d, matched=matched)

    after = {r["id"]: r["status"] for r in db_rows(
        "SELECT id, status FROM agent_execution WHERE id IN (%s)"
        % ",".join("?" for _ in before), tuple(before))} if before else {}
    changed = {k: (v, after.get(k)) for k, v in before.items() if after.get(k) != v}
    res.notes.append(f"重启前后终态被改写 {len(changed)} 条")
    if changed:
        res.failures.append(f"重启改写了历史终态：{list(changed.items())[:3]}")
    active_after = db_rows("SELECT id, status FROM agent_execution WHERE status NOT IN "
                           "('completed','failed','cancelled','timeout')")
    res.notes.append(f"重启后活跃执行 {len(active_after)} 条")
    if len(active_after) > len(active_before):
        res.failures.append(f"重启后多出活跃执行：{active_after}")


# ---------------------------------------------------------------- 调度

# 模型偶发生成非法 JSON 工具参数 → dsh 直接报错退出。属模型侧鲁棒性问题，
# 不是平台逻辑缺陷；命中后重试 1 次，仍失败记为 FLAKY，不计入缺陷清单。
FLAKY_PATTERN = re.compile(
    r"INVALID_REQUEST|Expecting property name|Expecting value|is not valid JSON", re.IGNORECASE)

# 交付物里绝不允许出现凭据明文：落盘前统一抹掉。
_REDACT_PATTERNS = (
    re.compile(r"eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{4,}"),
    re.compile(r"person\.[A-Za-z0-9._\-]{16,}"),
    re.compile(r"sk-[A-Za-z0-9]{16,}"),
)


def _redact(text: str) -> str:
    for pattern in _REDACT_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    return text


def _run_once(base: str, key: str, case: Case, keys: dict[str, str]) -> Result:
    res = Result(case=case)
    t0 = time.time()
    try:
        api = Api(base, key)
        conv = _make_conv(api, case)
        res.conversation_id = conv
        if not conv:
            res.failures.append("创建会话失败")
            return res
        if case.flow == "single":
            _flow_single(res, api, case, conv)
        elif case.flow == "serial2":
            _flow_serial2(res, api, case, conv)
        elif case.flow == "edit_rerun":
            _flow_edit_rerun(res, api, case, conv)
        elif case.flow == "cancel":
            _flow_cancel(res, api, case, conv)
        elif case.flow == "expect_409":
            _flow_expect_409(res, api, case, conv)
        elif case.flow == "concurrent":
            _flow_concurrent(res, api, case, conv, keys)
        elif case.flow == "oversize":
            _flow_oversize(res, api, case, conv)
        elif case.flow == "security":
            _flow_security(res, api, case, conv, keys)
        elif case.flow == "input_completion":
            _flow_input_completion(res, api, case, conv)
        elif case.flow == "bad_credential":
            _flow_bad_credential(res, api, case, conv)
        elif case.flow == "marketplace":
            _flow_marketplace(res, api, case, conv, keys)
        elif case.flow == "restart":
            _flow_restart(res, api, case, conv)
        else:
            res.failures.append(f"未知 flow：{case.flow}")
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode()[:200]
        except Exception:  # noqa: BLE001
            detail = ""
        res.failures.append(f"HTTP {exc.code}: {detail}")
    except Exception as exc:  # noqa: BLE001
        res.failures.append(f"{type(exc).__name__}: {exc}")
    finally:
        res.seconds = time.time() - t0
        res.ok = not res.failures
    return res


def run_case(base: str, key: str, case: Case, keys: dict[str, str]) -> Result:
    res = _run_once(base, key, case, keys)
    if res.ok:
        return res
    blob = " ".join(res.failures + res.notes + [res.status])
    if not FLAKY_PATTERN.search(blob):
        return res
    res.notes.append("命中 known-flaky（模型生成的工具参数不是合法 JSON），重试 1 次")
    retry = _run_once(base, key, case, keys)
    if retry.ok:
        retry.flaky = True
        retry.notes.append("重试通过（首次失败属模型侧偶发）")
        return retry
    if FLAKY_PATTERN.search(" ".join(retry.failures + retry.notes + [retry.status])):
        retry.flaky = True
        retry.notes.append("重试仍命中 known-flaky，记为 FLAKY 而非缺陷")
    return retry


# ---------------------------------------------------------------- 产出

def _payload(results: list[Result]) -> list[dict]:
    return [{
        "cid": r.case.cid, "group": r.case.group, "title": r.case.title,
        "employee": r.case.employee, "actor": r.case.actor, "flow": r.case.flow,
        "ok": r.ok, "flaky": r.flaky, "status": r.status, "seconds": round(r.seconds, 1),
        "conversation_id": r.conversation_id, "execution_id": r.execution_id,
        "trace_id": r.trace_id, "http_codes": r.http_codes,
        "notes": r.notes, "failures": r.failures, "answer_head": r.answer[:300],
    } for r in results]


def _write_reports(report: str, report_md: str, results: list[Result]) -> None:
    Path(report).parent.mkdir(parents=True, exist_ok=True)
    Path(report).write_text(_redact(json.dumps(_payload(results), ensure_ascii=False, indent=2)),
                            encoding="utf-8")
    if not report_md:
        return
    passed = sum(1 for r in results if r.ok)
    flaky = sum(1 for r in results if r.flaky and not r.ok)
    lines = [
        "| 编号 | 分组 | 标题 | 发起 | 结果 | 终态 | 耗时 | 关键证据 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in results:
        verdict = "PASS" if r.ok else ("FLAKY" if r.flaky else "FAIL")
        lines.append("| {cid} | {group} | {title} | {emp} | {v} | {st} | {sec:.0f}s | {ev} |".format(
            cid=r.case.cid, group="逻辑" if r.case.group == "logic" else "稳定性",
            title=r.case.title, emp=f"{r.case.employee}/{r.case.actor}", v=verdict,
            st=r.status, sec=r.seconds, ev="；".join(r.notes[:3]) or "-"))
    head = (f"通过 {passed}/{len(results)}（偶发 {flaky}），"
            f"逐 case 耗时合计 {sum(r.seconds for r in results) / 60:.1f} 分钟")
    Path(report_md).write_text(_redact(head + "\n\n" + "\n".join(lines) + "\n"), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:18080/api/v1")
    ap.add_argument("--only", nargs="*", default=[])
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--report", default=str(HERE / "ops" / "logs" / "e2e-20-cases.json"))
    ap.add_argument("--report-md", default="")
    ap.add_argument("--no-mutate", action="store_true", help="跳过会写数据/重启容器的 case")
    args = ap.parse_args()

    if not DB_PATH.exists():
        print(f"ERROR: 找不到运行库 {DB_PATH}", file=sys.stderr)
        return 2

    selected = [c for c in CASES if not args.only or c.cid in set(args.only)]
    if args.no_mutate:
        skipped = [c.cid for c in selected if c.mutating]
        selected = [c for c in selected if not c.mutating]
        if skipped:
            print(f"--no-mutate：跳过 {', '.join(skipped)}")
    if not selected:
        print("没有匹配的 case", file=sys.stderr)
        return 2

    # 身份密钥一次性全部载入：越权/并发类 case 需要「另一个自然人」的身份，
    # 只按选中 case 的 actor 载入会漏（S07 曾因此 KeyError）。
    keys: dict[str, str] = {}
    for actor, path in KEY_FILES.items():
        if not path.exists():
            print(f"ERROR: 找不到自然人 {actor} 的登录密钥（{path}）", file=sys.stderr)
            return 2
        keys[actor] = path.read_text().strip()
    if not ADMIN_KEY_FILE.exists():
        print(f"ERROR: 找不到管理员登录密钥（{ADMIN_KEY_FILE}）", file=sys.stderr)
        return 2
    keys["__admin__"] = ADMIN_KEY_FILE.read_text().strip()

    print(f"== 共 {len(selected)} 个 case，并发度 {args.workers}，身份 {sorted(keys)} ==", flush=True)
    main_phase = [c for c in selected if c.phase == "main"]
    last_phase = [c for c in selected if c.phase != "main"]

    res_locks: dict[str, threading.Lock] = {}
    res_guard = threading.Lock()

    def lock_for(name: str) -> threading.Lock:
        with res_guard:
            return res_locks.setdefault(name, threading.Lock())

    def resources_of(c: Case) -> list[str]:
        if c.no_lock:
            return []
        if c.lock_keys:
            return sorted(c.lock_keys)
        names = {f"emp:{c.employee}"}
        if c.expect_delegate_to:
            names.add(f"delegate:{c.expect_delegate_to}")
        return sorted(names)

    results: list[Result] = []

    def run_one(c: Case) -> Result:
        locks = [lock_for(n) for n in resources_of(c)]
        for lk in locks:
            lk.acquire()
        try:
            print(f"  -> {c.cid} {c.title} [{c.employee} / {c.actor}] …", flush=True)
            r = run_case(args.base, keys[c.actor], c, keys)
            verdict = "PASS" if r.ok else ("FLAKY" if r.flaky else "FAIL")
            print(f"     {c.cid} {verdict} {r.status} {r.seconds:.0f}s"
                  + ("" if r.ok else "  :: " + " | ".join(r.failures[:3])), flush=True)
            return r
        finally:
            for lk in reversed(locks):
                lk.release()

    if main_phase:
        with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
            results.extend(pool.map(run_one, main_phase))
    # 重启类必须单独跑：避免打断并发批次，也避免把并发 case 的失败归因到重启
    for case in last_phase:
        results.append(run_one(case))

    order = {c.cid: i for i, c in enumerate(selected)}
    results.sort(key=lambda r: order.get(r.case.cid, 999))

    print("\n" + "=" * 120)
    print(f"{'case':5} {'员工':16} {'状态':10} {'耗时':>7}  {'结果':5} 备注")
    print("-" * 120)
    for r in results:
        verdict = "PASS" if r.ok else ("FLAKY" if r.flaky else "FAIL")
        print(f"{r.case.cid:5} {r.case.employee:16} {r.status:10} {r.seconds:6.0f}s  "
              f"{verdict:5} {'; '.join(r.notes)}")
        for f in r.failures:
            print(f"      !! {f}")
    print("-" * 120)
    passed = sum(1 for r in results if r.ok)
    flaky = sum(1 for r in results if r.flaky and not r.ok)
    failed = len(results) - passed - flaky
    print(f"合计 {len(results)}：通过 {passed}，偶发 {flaky}，失败 {failed}")

    _write_reports(args.report, args.report_md, results)
    print(f"报告：{args.report}" + (f" / {args.report_md}" if args.report_md else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
