import { applyEdits, modify, Node, parseTree, ParseError } from 'jsonc-parser';
import { AST, parseTOML } from 'toml-eslint-parser';
import { BalanceError, normalizeEndpoint } from './client';

export type ClientKind = 'claude' | 'codex';
const object = (value: unknown): value is Record<string, unknown> => !!value && typeof value === 'object' && !Array.isArray(value);
const eol = (text: string) => text.includes('\r\n') ? '\r\n' : '\n';

function jsonTree(text: string): Node {
  const errors: ParseError[] = [], tree = parseTree(text, errors, {allowTrailingComma: true});
  if (errors.length || !tree || tree.type !== 'object') throw new BalanceError('现有 JSON 配置格式有误，未生成覆盖内容；请先修正原文件。');
  const visit = (node: Node) => {
    if (node.type === 'object') {
      const keys = new Set<string>();
      for (const property of node.children || []) {
        const key = property.children?.[0].value as string;
        if (keys.has(key)) throw new BalanceError('现有 JSON 配置有重复字段，请先清理重复项。');
        keys.add(key);
      }
    }
    for (const child of node.children || []) visit(child);
  };
  visit(tree); return tree;
}
function jsonValue(node: Node): unknown {
  if (node.type === 'array') return (node.children || []).map(jsonValue);
  if (node.type === 'object') return Object.fromEntries((node.children || []).map(p => [p.children![0].value, jsonValue(p.children![1])]));
  return node.value;
}
function patchJson(text: string, changes: Record<string, unknown>): string {
  jsonTree(text);
  const indent = text.match(/\n([\t ]+)"/u)?.[1];
  for (const [key, value] of Object.entries(changes)) text = applyEdits(text, modify(text, [key], value, {
    formattingOptions: {insertSpaces: !indent?.includes('\t'), tabSize: indent?.length || 2, eol: eol(text)}
  }));
  jsonTree(text); return text;
}
function origin(endpoint: string): string {return new URL(normalizeEndpoint(endpoint)).origin;}
function modelKey(key: string): string {
  if (!/^sk-[A-Za-z0-9_-]{16,256}$/.test(key)) throw new BalanceError('未取得有效的模型 KEY，未生成配置。');
  return key;
}

export function claudeConfig(existing: string, endpoint: string, key: string): string {
  const text = existing.trim() ? existing : '{}' + eol(existing), value = jsonValue(jsonTree(text)) as Record<string, unknown>;
  const old = value['claudeCode.environmentVariables'];
  if (old !== undefined && (!Array.isArray(old) || old.some(v => !object(v) || typeof v.name !== 'string' || typeof v.value !== 'string'))) {
    throw new BalanceError('Claude 环境变量配置格式有误，未生成覆盖内容。');
  }
  const replaced = new Set(['ANTHROPIC_BASE_URL', 'ANTHROPIC_AUTH_TOKEN', 'ANTHROPIC_API_KEY',
    'CLAUDE_CODE_USE_BEDROCK', 'CLAUDE_CODE_USE_VERTEX', 'CLAUDE_CODE_USE_FOUNDRY']);
  const variables = (old as {name: string; value: string}[] | undefined || []).filter(v => !replaced.has(v.name));
  variables.push({name: 'ANTHROPIC_BASE_URL', value: origin(endpoint)}, {name: 'ANTHROPIC_AUTH_TOKEN', value: modelKey(key)},
    // An inherited API key must not override the selected Bearer token.
    {name: 'ANTHROPIC_API_KEY', value: ''}, {name: 'CLAUDE_CODE_USE_BEDROCK', value: '0'},
    {name: 'CLAUDE_CODE_USE_VERTEX', value: '0'}, {name: 'CLAUDE_CODE_USE_FOUNDRY', value: '0'});
  return patchJson(text, {'claudeCode.environmentVariables': variables, 'claudeCode.disableLoginPrompt': true});
}

type TomlPath = (string | number)[];
const starts = (value: TomlPath, prefix: TomlPath) => prefix.every((key, i) => value[i] === key);
const equal = (a: TomlPath, b: TomlPath) => a.length === b.length && starts(a, b);
const keys = (key: AST.TOMLKey): string[] => key.keys.map(k => k.type === 'TOMLBare' ? k.name : k.value);
function toml(text: string): AST.TOMLProgram {
  try {return parseTOML(text, {tomlVersion: '1.0'});}
  catch {throw new BalanceError('现有 config.toml 格式有误或有重复字段，未生成覆盖内容；请先修正原文件。');}
}
function values(ast: AST.TOMLProgram): {node: AST.TOMLKeyValue; path: TomlPath}[] {
  const result: {node: AST.TOMLKeyValue; path: TomlPath}[] = [];
  const visit = (node: AST.TOMLKeyValue, parent: TomlPath) => {
    const path = [...parent, ...keys(node.key)]; result.push({node, path});
    if (node.value.type === 'TOMLInlineTable') for (const child of node.value.body) visit(child, path);
  };
  for (const node of ast.body[0].body) {
    if (node.type === 'TOMLTable') for (const child of node.body) visit(child, node.resolvedKey);
    else visit(node, []);
  }
  return result;
}
function replace(text: string, range: [number, number], content: string): string {return text.slice(0, range[0]) + content + text.slice(range[1]);}
function rootSetting(text: string, name: string, value: string): string {
  const ast = toml(text), match = values(ast).find(v => equal(v.path, [name]));
  if (match) return replace(text, match.node.value.range, JSON.stringify(value));
  return name + ' = ' + JSON.stringify(value) + eol(text) + text;
}
export function codexConfig(existing: string, endpoint: string): string {
  let text = existing;
  text = rootSetting(text, 'model_provider', 'newapi');
  text = rootSetting(text, 'cli_auth_credentials_store', 'file');
  if (values(toml(text)).some(v => equal(v.path, ['forced_login_method']))) text = rootSetting(text, 'forced_login_method', 'api');
  const provider = {name: 'New API', base_url: origin(endpoint) + '/v1', wire_api: 'responses', requires_openai_auth: true};
  const inline = '{ ' + Object.entries(provider).map(([k, v]) => k + ' = ' + JSON.stringify(v)).join(', ') + ' }';
  const ast = toml(text), all = values(ast), path = ['model_providers', 'newapi'];
  const exact = all.find(v => equal(v.path, path));
  if (exact) text = replace(text, exact.node.value.range, inline);
  else {
    const parent = all.find(v => starts(path, v.path) && v.path.length < path.length);
    if (parent) {
      if (parent.node.value.type !== 'TOMLInlineTable') throw new BalanceError('config.toml 的 model_providers 不是配置表，未生成覆盖内容。');
      const at = parent.node.value.range[1] - 1, suffix = path.slice(parent.path.length).join('.');
      text = text.slice(0, at) + (parent.node.value.body.length ? ', ' : '') + suffix + ' = ' + inline + ' ' + text.slice(at);
    } else {
      // Replace this provider's complete branch, including obsolete auth/header
      // subtables. Other providers, comments and settings stay byte-for-byte.
      const edits: [number, number][] = [];
      for (const node of ast.body[0].body) {
        if (node.type === 'TOMLTable' && starts(node.resolvedKey, path)) edits.push(node.range);
        else if (node.type === 'TOMLKeyValue' && starts(keys(node.key), path)) edits.push(node.range);
        else if (node.type === 'TOMLTable') for (const child of node.body) {
          if (starts([...node.resolvedKey, ...keys(child.key)], path)) edits.push(child.range);
        }
      }
      for (const range of edits.sort((a, b) => b[0] - a[0])) text = replace(text, range, '');
      text = text.trimEnd() + eol(text) + eol(text) + '[model_providers.newapi]' + eol(text)
        + Object.entries(provider).map(([k, v]) => k + ' = ' + JSON.stringify(v)).join(eol(text)) + eol(text);
    }
  }
  toml(text); return text;
}
export function codexAuth(existing: string, key: string): string {
  // Replace the authentication document, rather than mixing API-key login with
  // obsolete ChatGPT OAuth tokens or refresh metadata.
  if (existing.trim()) jsonTree(existing);
  return JSON.stringify({auth_mode: 'apikey', OPENAI_API_KEY: modelKey(key)}, null, 2).replace(/\n/g, eol(existing)) + eol(existing);
}
