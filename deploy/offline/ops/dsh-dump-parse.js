#!/usr/bin/env node
'use strict';
/*
 * 解析 `dsh --profile <profile> --dump-config` 的合成配置树，输出结构化 JSON。
 *
 * 为什么需要它：合成树里既有顶层条目，也有 `insert:` 展开的嵌套条目，且值可能带
 * `!!js <表达式>` 自定义标签（未求值）。用 grep 启发式判断「启用/禁用」会漏判
 * （`disabled: true` 不一定紧跟在 `- id:` 之后 3 行内），必须用正规 YAML 解析器。
 *
 * 用法:
 *   node dsh-dump-parse.js <dump-file>      # 解析文件
 *   dsh --profile p --dump-config | node dsh-dump-parse.js    # 解析 stdin
 *
 * js-yaml 查找顺序：
 *   $DSH_YAML_PATH -> /opt/dsh-runtime/lib/node_modules/@deepseek-ai/dsh/node_modules/js-yaml
 *   -> /usr/local/lib/node_modules/@deepseek-ai/dsh/node_modules/js-yaml -> require('js-yaml')
 *
 * 退出码: 0 成功 / 2 YAML 解析失败 / 3 找不到 js-yaml / 4 dump 不是顶层序列
 */

const fs = require('fs');

function loadYaml() {
  const candidates = [
    process.env.DSH_YAML_PATH,
    '/opt/dsh-runtime/lib/node_modules/@deepseek-ai/dsh/node_modules/js-yaml',
    '/usr/local/lib/node_modules/@deepseek-ai/dsh/node_modules/js-yaml',
  ].filter(Boolean);
  for (const candidate of candidates) {
    try {
      return require(candidate);
    } catch (err) {
      /* 尝试下一个 */
    }
  }
  try {
    return require('js-yaml');
  } catch (err) {
    console.error('找不到 js-yaml；请用 DSH_YAML_PATH 指定路径');
    process.exit(3);
  }
}

const yaml = loadYaml();

// dump 里会出现未求值的 `!!js <表达式>`，注册为标量/序列/映射三种形态的宽松类型
function buildSchema() {
  const tag = 'tag:yaml.org,2002:js';
  const wrap = (key) => (data) => ({ [key]: data === undefined || data === null ? '' : data });
  return yaml.DEFAULT_SCHEMA.extend([
    new yaml.Type(tag, { kind: 'scalar', construct: wrap('$js') }),
    new yaml.Type(tag, { kind: 'sequence', construct: wrap('$jsSeq') }),
    new yaml.Type(tag, { kind: 'mapping', construct: wrap('$jsMap') }),
  ]);
}

const fileArg = process.argv[2];
const raw = fileArg && fileArg !== '-' ? fs.readFileSync(fileArg, 'utf8') : fs.readFileSync(0, 'utf8');

let doc;
try {
  doc = yaml.load(raw, { schema: buildSchema(), json: false });
} catch (err) {
  console.error('YAML 解析失败: ' + err.message);
  process.exit(2);
}

if (doc === null || doc === undefined) {
  console.error('dump 内容为空');
  process.exit(4);
}
if (!Array.isArray(doc)) {
  console.error('dump 顶层不是序列（实际为 ' + typeof doc + '）');
  process.exit(4);
}

const entries = [];

function visit(node, depth, source) {
  if (!node || typeof node !== 'object' || Array.isArray(node)) return;
  if (node.id !== undefined && node.id !== null) {
    entries.push({
      id: String(node.id),
      disabled: node.disabled === true,
      name: node.name === undefined || node.name === null ? null : String(node.name),
      depth,
      source,
      config: node.config === undefined ? null : node.config,
      keys: Object.keys(node).sort(),
    });
  }
  if (Array.isArray(node.insert)) {
    for (const child of node.insert) visit(child, depth + 1, 'insert');
  }
}

for (const item of doc) visit(item, 0, 'top');

const byId = {};
for (const entry of entries) byId[entry.id] = entry; // 后出现的覆盖先出现的（patch 语义）

const toolIds = entries.filter((e) => e.id.startsWith('tool-'));
const result = {
  top_level_count: doc.length,
  entry_count: entries.length,
  entries,
  by_id: byId,
  top_level_ids: entries.filter((e) => e.depth === 0).map((e) => e.id),
  inserted_ids: entries.filter((e) => e.depth > 0).map((e) => e.id),
  disabled_ids: entries.filter((e) => e.disabled).map((e) => e.id),
  enabled_tool_ids: toolIds.filter((e) => !e.disabled).map((e) => e.id),
  disabled_tool_ids: toolIds.filter((e) => e.disabled).map((e) => e.id),
  duplicate_ids: Object.keys(
    entries.reduce((acc, e) => {
      acc[e.id] = (acc[e.id] || 0) + 1;
      return acc;
    }, {}),
  ).filter((id) => entries.filter((e) => e.id === id).length > 1),
};

process.stdout.write(JSON.stringify(result, null, 2) + '\n');
