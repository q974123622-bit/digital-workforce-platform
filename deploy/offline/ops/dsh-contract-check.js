#!/usr/bin/env node
'use strict';
/*
 * 把 dsh-dump-parse.js 的结构化输出与 baseline/contract.json 逐条比对。
 *
 * 用法:
 *   node dsh-contract-check.js <parsed.json> <contract.json> \
 *        [--expected-model <name>] [--stderr-file <path>] [--report <out.json>]
 *
 * 退出码: 0 全部通过 / 1 有契约违规 / 2 用法错误
 * 纯 Node 实现（底座镜像自带 node），不依赖宿主机 python。
 */

const fs = require('fs');

function argValue(flag) {
  const i = process.argv.indexOf(flag);
  return i > 0 && i + 1 < process.argv.length ? process.argv[i + 1] : null;
}

const parsedPath = process.argv[2];
const contractPath = process.argv[3];
if (!parsedPath || !contractPath) {
  console.error('用法: dsh-contract-check.js <parsed.json> <contract.json> [--expected-model M] [--stderr-file F] [--report OUT]');
  process.exit(2);
}

const parsed = JSON.parse(fs.readFileSync(parsedPath, 'utf8'));
const contract = JSON.parse(fs.readFileSync(contractPath, 'utf8'));
const expectedModel = argValue('--expected-model');
const stderrFile = argValue('--stderr-file');
const reportPath = argValue('--report');

const checks = [];
const failures = [];
const warnings = [];

function record(ok, label, detail, level) {
  const item = { ok, label, detail: detail || '', level: level || 'fail' };
  checks.push(item);
  if (!ok) {
    if (item.level === 'warn') warnings.push(label + (detail ? ' :: ' + detail : ''));
    else failures.push(label + (detail ? ' :: ' + detail : ''));
  }
}

const entryOf = (id) => parsed.by_id[id];
function getPath(obj, path) {
  let cur = obj;
  for (const key of path) {
    if (cur === null || typeof cur !== 'object') return undefined;
    cur = cur[key];
  }
  return cur;
}

// ---- 1. 基本形状 ----
const minEntries = contract.top_level_min_entries || 0;
record(
  parsed.top_level_count >= minEntries,
  `顶层条目数 >= ${minEntries}`,
  `实际 ${parsed.top_level_count}`,
);
record(
  parsed.duplicate_ids.length === 0,
  '合成树无重复 id',
  parsed.duplicate_ids.join(', '),
);

// ---- 2. 必须禁用 ----
const danglingAllow = new Set(contract.dangling_patch_ids_allowlist || []);
for (const id of contract.must_be_disabled || []) {
  const entry = entryOf(id);
  if (!entry) {
    record(false, `禁用 ${id}`, '不在合成树中（patch 悬空）', 'fail');
  } else {
    record(entry.disabled === true, `禁用 ${id}`, entry.disabled ? '' : '实际为启用状态');
  }
}

// ---- 3. 必须启用 ----
for (const id of contract.must_be_enabled || []) {
  const entry = entryOf(id);
  if (!entry) {
    record(false, `启用 ${id}`, '不在合成树中');
  } else {
    record(entry.disabled !== true, `启用 ${id}`, entry.disabled ? '实际为禁用状态' : '');
  }
}

// ---- 4. 工具面收敛：启用的 tool-* 必须落在白名单内 ----
const toolAllow = new Set(contract.enabled_tool_allowlist || []);
const unexpectedEnabledTools = (parsed.enabled_tool_ids || []).filter((id) => !toolAllow.has(id));
record(
  unexpectedEnabledTools.length === 0,
  '启用的 tool-* 均在白名单内',
  unexpectedEnabledTools.length ? `越权启用: ${unexpectedEnabledTools.join(', ')}` : `白名单: ${[...toolAllow].join(', ') || '(空)'}`,
);

// ---- 5. 模型绑定 ----
const modelBinding = contract.model_binding;
if (modelBinding) {
  const entry = entryOf(modelBinding.entry);
  if (!entry) {
    record(false, '模型绑定', `找不到条目 ${modelBinding.entry}`);
  } else {
    const actual = getPath(entry, modelBinding.config_path);
    const want = expectedModel || modelBinding.fallback;
    record(
      actual === want,
      `模型绑定 ${modelBinding.entry}.${modelBinding.config_path.join('.')}`,
      `期望 ${want}，实际 ${JSON.stringify(actual)}`,
    );
  }
}

// ---- 6. 平台 MCP 通道 ----
const mcp = contract.mcp_channel;
if (mcp) {
  const entry = entryOf(mcp.entry);
  if (!entry) {
    record(false, `MCP 通道 ${mcp.entry}`, '不在合成树中');
  } else {
    record(entry.disabled !== true, `MCP 通道 ${mcp.entry} 启用`, entry.disabled ? '被禁用' : '');
    if (mcp.require_name) {
      record(entry.name === mcp.require_name, `MCP 通道 name`, `期望 ${mcp.require_name}，实际 ${entry.name}`);
    }
    const transport = getPath(entry, ['config', 'transport']);
    if (mcp.require_transport) {
      record(transport === mcp.require_transport, 'MCP 通道 transport', `期望 ${mcp.require_transport}，实际 ${transport}`);
    }
    for (const key of mcp.require_config_keys || []) {
      const value = getPath(entry, ['config', key]);
      record(value !== undefined && value !== null, `MCP 通道 config.${key} 存在`, String(value));
    }
  }
}

// ---- 7. patch 悬空 warning 白名单 ----
if (stderrFile && fs.existsSync(stderrFile)) {
  const lines = fs.readFileSync(stderrFile, 'utf8').split('\n').filter((l) => l.trim().length > 0);
  const notFound = [];
  for (const line of lines) {
    const m = line.match(/patch: entry "([^"]+)" not found/);
    if (m) notFound.push(m[1]);
    else warnings.push(`stderr 非 patch 提示: ${line.trim()}`);
  }
  const unexpected = notFound.filter((id) => !danglingAllow.has(id));
  record(
    unexpected.length === 0,
    'patch 悬空 id 均在白名单内',
    unexpected.length ? `未在白名单: ${unexpected.join(', ')}` : `悬空: ${notFound.join(', ') || '(无)'}`,
  );
}

// ---- 8. 超时链不变量（键不存在则跳过，兼容旧契约）----
// 后端五处超时数字互相约束（唯一真相见 backend/app/services/config.py 顶部）。
// 这里只校验「profile 容得下后端链路」这一条：委派等待 + 被委派方单轮 + 缓冲
// 必须小于 MCP 工具调用超时，否则被委派方的执行还没跑完、上游工具调用就先超时，
// 表现为「委派静默失败、发起方仍返回 completed，用户拿到没有知识库依据的答案」。
const invariant = contract.timeout_invariant;
if (invariant && typeof invariant.tool_call_timeout_ms !== 'number') {
  const mcpEntry = entryOf('dwp-platform-tools');
  const actualMs = mcpEntry ? getPath(mcpEntry, ['config', 'toolCallTimeoutMs']) : undefined;
  const role = Number(invariant.harness_timeout_role);
  const twin = Number(invariant.harness_timeout_twin);
  const slotWait = Number(invariant.delegation_slot_wait_seconds);
  const buffer = Number(invariant.buffer_seconds);
  if ([role, twin, slotWait, buffer].some((n) => !Number.isFinite(n))) {
    record(false, '超时链不变量', 'timeout_invariant 字段缺失或非数值', 'fail');
  } else {
    const requiredMs = (slotWait + Math.max(role, twin) + buffer) * 1000;
    record(
      typeof actualMs === 'number' && actualMs >= requiredMs,
      'profile 工具调用超时 >= 后端超时链',
      `要求 >= ${requiredMs}ms（slot_wait ${slotWait} + max(role ${role}, twin ${twin}) + 缓冲 ${buffer}），实际 ${JSON.stringify(actualMs)}`,
    );
  }
}

// ---- 输出 ----
const lines = [];
lines.push(`顶层条目 ${parsed.top_level_count} / 全部条目 ${parsed.entry_count} / 禁用 ${parsed.disabled_ids.length}`);
lines.push(`启用的 tool-*：${(parsed.enabled_tool_ids || []).join(', ') || '(无)'}`);
for (const c of checks) {
  const tag = c.ok ? '[ OK ]' : c.level === 'warn' ? '[WARN]' : '[FAIL]';
  lines.push(`${tag} ${c.label}${c.detail ? '  -- ' + c.detail : ''}`);
}
const summary = failures.length === 0
  ? `契约校验通过（${checks.length} 项；${warnings.length} 条告警）`
  : `契约校验失败：${failures.length} 项不通过`;
lines.push('');
lines.push(summary);
process.stdout.write(lines.join('\n') + '\n');

if (reportPath) {
  fs.writeFileSync(reportPath, JSON.stringify({
    ok: failures.length === 0,
    checked_at: new Date().toISOString(),
    top_level_count: parsed.top_level_count,
    disabled_count: parsed.disabled_ids.length,
    enabled_tool_ids: parsed.enabled_tool_ids,
    checks,
    failures,
    warnings,
  }, null, 2) + '\n');
}

process.exit(failures.length === 0 ? 0 : 1);
