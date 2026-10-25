'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs');
const root = path.join(__dirname, '../src/new_api_cockpit/static/chat');
const S = require(path.join(root, 'state.js'));
const {createAPI} = require(path.join(root, 'api.js'));
const {consumeRun, createSSEParser} = require(path.join(root, 'stream.js'));
const {parseSkill, makeSkill} = require(path.join(root, 'settings.js'));
const {safeLink, markdown, usageText} = require(path.join(root, 'render.js'));
const {checkUpload, fileSizeLabel, readSkillFile, downloadFile, safeFilename} = require(path.join(root, 'files.js'));
const CID = '11111111-1111-4111-8111-111111111111', RID = '22222222-2222-4222-8222-222222222222', MID = '33333333-3333-4333-8333-333333333333', SID = '44444444-4444-4444-8444-444444444444';
const event = (seq, type, data = {}, extra = {}) => ({v: 1, conversation_id: CID, run_id: type.startsWith('conversation.') ? null : RID, at: '2026-10-15T00:00:00Z', seq, type, data, ...extra});
const frame = e => `id: ${e.seq}\r\nevent: ${e.type}\r\ndata: ${JSON.stringify(e)}\r\n\r\n`;
const limits = {skill_file_bytes: 8192, skill_name_chars: 64, skill_description_chars: 256};
const defer = () => { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b; }); return {promise, resolve, reject}; };

test('history is continuous, all pages append, duplicates ignored and final message replaces deltas', () => {
  const first = [event(1, 'conversation.created', {title: 'old'}), event(2, 'run.accepted', {text: 'question', message_id: MID, context_snapshot: {context_revision: 2, skill_ids: []}}), event(3, 'assistant.delta', {message_id: MID, text: '部分'})];
  const next = [first[2], event(4, 'assistant.delta', {message_id: MID, text: '文字'}), event(5, 'assistant.message', {message_id: MID, text: '最终快照', complete: true, tool_calls: []}), event(6, 'run.terminal', {status: 'interrupted', uncertain: true, usage: null})];
  const events = S.mergeEvents(S.mergeEvents([], first, CID), next, CID), p = S.projectEvents(events);
  assert.equal(events.length, 6); assert.equal(p.lastSeq, 6); assert.equal(p.runs[0].messages.get(MID).text, '最终快照');
  assert.equal(p.runs[0].status, 'interrupted'); assert.equal(p.runs[0].uncertain, true); assert.equal(p.runs[0].usage, null);
  assert.throws(() => S.mergeEvents(first, [event(5, 'assistant.delta')], CID), /缺口/);
  assert.throws(() => S.mergeEvents([], [event(1, 'conversation.created', {}, {conversation_id: RID})], CID), /归属/);
  assert.throws(() => S.mergeEvents([], [event(1, 'unknown')], CID), /格式/);
  assert.throws(() => S.mergeEvents([], [event(1, 'run.accepted', {}, {v: 2})], CID), /格式/);
});
test('all output versions, empty conversations and tool results survive event projection', () => {
  const files = [1, 2].map((version, index) => event(index + 1, 'file.added', {file: {id: index ? SID : MID, kind: 'output', name: 'report.md', version}}));
  const p = S.projectEvents([...files, event(3, 'tool.call', {call_id: 'call-a', name: 'read_file', arguments: {file_id: MID}}), event(4, 'tool.result', {call_id: 'call-a', name: 'read_file', ok: false, result: {code: 'TOOL_FILE_NOT_FOUND'}})]);
  assert.equal(p.files.length, 2); assert.equal(p.runs[0].tools.get('call-a').ok, false); assert.equal(S.projectEvents([]).runs.length, 0);
  assert.equal(usageText({usage: null}), '用量未知；费用以 New API 记录为准');
  assert.equal(usageText({usage: {prompt_tokens: 0, completion_tokens: 1, total_tokens: 1}, usage_complete: false}), '用量部分：输入 0 / 输出 1 / 合计 1 Token；费用以 New API 记录为准');
  assert.equal(usageText({usage: {prompt_tokens: 0, completion_tokens: 0, total_tokens: 0}, usage_complete: true}), '用量完整：输入 0 / 输出 0 / 合计 0 Token；费用以 New API 记录为准');
});
test('tool timeline stays interleaved with assistant messages; per-request usage deduplicates and remains partial', () => {
  const events = [event(1, 'assistant.message', {message_id: MID, text: 'before tool'}), event(2, 'tool.call', {call_id: 'a', name: 'list_files', arguments: {}}), event(3, 'tool.result', {call_id: 'a', name: 'list_files', ok: true, result: {}}), event(4, 'assistant.message', {message_id: SID, text: 'after tool'}), event(5, 'run.usage', {request_index: 1, prompt_tokens: 2, completion_tokens: 3, total_tokens: 5}), event(6, 'run.usage', {request_index: 1, prompt_tokens: 2, completion_tokens: 3, total_tokens: 5}), event(7, 'run.usage', {request_index: 2, prompt_tokens: 5, completion_tokens: 8, total_tokens: 13})];
  const run = S.projectEvents(events).runs[0];
  assert.deepEqual(run.timeline, [{kind: 'message', id: MID}, {kind: 'tool', id: 'a'}, {kind: 'message', id: SID}]);
  assert.equal(run.usage.total_tokens, 18); assert.equal(run.usage_complete, false);
});
test('scope clears blobs and aborts old view; account change blocks even late global responses', () => {
  const revoked = [], scope = new S.Scope(url => revoked.push(url)); scope.switch(CID);
  const old = scope.ticket(), global = scope.ticket(true), controller = new AbortController(), globalController = new AbortController();
  scope.track(controller, old); scope.track(globalController, global); scope.addURL('blob:old', old);
  scope.switch(RID); assert.equal(controller.signal.aborted, true); assert.equal(scope.current(old), false); assert.equal(scope.current(global), true); assert.deepEqual(revoked, ['blob:old']);
  scope.invalidate(); assert.equal(globalController.signal.aborted, true); assert.equal(scope.current(global), false); assert.throws(() => scope.assert(global), S.StaleResponse);
});
test('UUID secure fallback and Unicode validation do not corrupt content', () => {
  const id = S.uuid({getRandomValues: a => a.fill(0xab)}); assert.equal(S.isUUID(id), true);
  assert.throws(() => S.uuid({}), /安全/); assert.equal(S.isUUID('true'), false);
  assert.equal(S.normalizeText('\uFEFF中文\r\n😀\r尾'), '中文\n😀\n尾'); assert.equal(S.bytes('中文😀'), 10);
  for (const bad of ['\0', '\ud800', '\udc00', '\ud800a', '\u000b']) assert.throws(() => S.normalizeText(bad));
});
test('run request is exact, context/Skills explicit 0..3 and capability revision required', () => {
  const input = {meta: {revision: 9, storage_state: 'ok', active_run_id: null}, token: {id: 7, usable: true}, model: {id: 'fixture', chat_enabled: true, tools_enabled: false, capability_revision: 'sha256:' + 'a'.repeat(64)}, mode: 'chat', text: '中文', attachments: [], skillIds: [], context: {context_revision: 0, limits: {skills_per_run: 3}, skills: [{id: SID}]}, config: {modes: ['chat', 'files'], limits: {user_text_bytes: 6, attachments_per_turn: 8}}, requestId: CID};
  const body = S.buildRunRequest(input); assert.equal(body.context_revision, 0); assert.deepEqual(body.skill_ids, []); assert.equal(body.expected_revision, 9); assert.equal(body.token_id, 7);
  assert.deepEqual(Object.keys(body).sort(), ['attachment_ids', 'capability_revision', 'client_request_id', 'context_revision', 'expected_revision', 'mode', 'model', 'skill_ids', 'text', 'token_id'].sort());
  assert.throws(() => S.buildRunRequest({...input, token: {id: true, usable: true}}));
  assert.equal(S.buildRunRequest({...input, mode: 'files'}).mode, 'files');
  assert.throws(() => S.buildRunRequest({...input, skillIds: [SID, SID]}));
  assert.throws(() => S.buildRunRequest({...input, skillIds: [MID]}));
  assert.throws(() => S.buildRunRequest({...input, text: '中文a'}));
  assert.deepEqual(S.buildRunRequest({...input, attachments: [MID]}).attachment_ids, [MID]);
  assert.throws(() => S.buildRunRequest({...input, attachments: [MID, MID]}));
  assert.throws(() => S.buildRunRequest({...input, model: {...input.model, capability_revision: 'fixture'}}));
});
test('cursor traversal never treats has_more as complete and detects repeated cursors', () => {
  const seen = new Set(); assert.equal(S.pageCursor({has_more: true, next_cursor: 'a'}, seen), 'a');
  assert.throws(() => S.pageCursor({has_more: true, next_cursor: 'a'}, seen));
  assert.throws(() => S.pageCursor({has_more: true, next_cursor: null}, seen));
  assert.equal(S.pageCursor({has_more: false, next_cursor: null}, seen), null);
});

test('POST stream handles one-byte UTF-8/CRLF splits and snapshots without automatic reconnection', async () => {
  const events = [event(1, 'run.accepted', {text: '中文😀'}), event(2, 'run.terminal', {status: 'completed'})];
  const encoded = new TextEncoder().encode(': heartbeat\r\n\r\n' + events.map(frame).join(''));
  const seen = []; let cursor = 0;
  const body = new ReadableStream({pull(controller) { if (cursor < encoded.length) controller.enqueue(encoded.slice(cursor, ++cursor)); else controller.close(); }});
  const result = await consumeRun(new Response(body, {headers: {'Content-Type': 'text/event-stream; charset=utf-8'}}), {onEvent: e => seen.push(e)});
  assert.deepEqual(seen, events); assert.equal(result.terminal, true);
});
test('SSE multiline data, CR-only lines, unknown fields and heartbeats', () => {
  const seen = [], parser = createSSEParser(e => seen.push(e));
  parser.push('retry: 1\r: beat\r\r');
  parser.push('id: 1\revent: run.terminal\rdata: {"seq":1,\rdata: "type":"run.terminal"}\r\r', true);
  assert.deepEqual(seen, [{seq: 1, type: 'run.terminal'}]);
});
test('committed assistant snapshots use event budget, not the smaller upstream SSE frame budget', async () => {
  const value = event(1, 'assistant.message', {message_id: MID, text: '中'.repeat(87380), complete: true, tool_calls: []}), seen = [];
  assert.equal(Buffer.byteLength(frame(value)) > 262144, true);
  await consumeRun(new Response(frame(value), {headers: {'Content-Type': 'text/event-stream'}}), {onEvent: e => seen.push(e)});
  assert.equal(seen[0].data.text, value.data.text);
});
test('JSON replay is parsed by Content-Type, never as SSE', async () => {
  const value = {replayed: true, run_id: RID, status: 'completed', last_seq: 12};
  const result = await consumeRun(new Response(JSON.stringify(value), {headers: {'Content-Type': 'Application/JSON; charset=utf-8'}}), {onEvent: () => assert.fail('must not consume SSE')});
  assert.equal(result.run_id, RID); assert.equal(result.replayed, true);
});
test('EOF without terminal is uncertain; malformed/truncated frames and unsupported content type reject', async () => {
  const options = {onEvent() {}};
  const response = text => new Response(text, {headers: {'Content-Type': 'text/event-stream'}});
  assert.equal((await consumeRun(response(frame(event(1, 'run.accepted'))), options)).terminal, false);
  await assert.rejects(consumeRun(response('id: 1\nevent: run.terminal\ndata: {}'), options), /不完整/);
  await assert.rejects(consumeRun(response('id: 1\nevent: run.terminal\ndata: {}\n\n'), options), /序号/);
  await assert.rejects(consumeRun(new Response('html'), options), /不是事件流/);
  await assert.rejects(consumeRun(new Response(new Uint8Array([0xff]), {headers: {'Content-Type': 'text/event-stream'}}), options));
  assert.throws(() => createSSEParser(() => {}, 4).push('data: abcdefg'), /限制/);
});
test('aborted SSE reader and late asynchronous API JSON cannot return old account/chat data', async () => {
  const scope = new S.Scope(), ticket = scope.ticket(), json = defer();
  const api = createAPI({scope, fetch: async () => ({ok: true, status: 200, json: () => json.promise})});
  const pending = api.context({ticket}); await Promise.resolve(); scope.invalidate(); json.resolve({profiles: [{content: 'private-old'}]});
  await assert.rejects(pending, S.StaleResponse);
  const controller = new AbortController(); let cancelled = false;
  const reader = {read: async () => { controller.abort(); return {done: false, value: new TextEncoder().encode(frame(event(1, 'run.accepted')))}; }, cancel: async () => { cancelled = true; }, releaseLock() {}};
  await assert.rejects(consumeRun({headers: new Headers({'Content-Type': 'text/event-stream'}), body: {getReader: () => reader}}, {signal: controller.signal, onEvent: () => assert.fail('old event')}), {name: 'AbortError'});
  assert.equal(cancelled, true);
});
test('API uses wrapped fetch/cookie, write marker, exact cancellation and no automatic retries', async () => {
  const calls = [], scope = new S.Scope(); scope.switch(CID);
  const api = createAPI({scope, fetch: async (url, init) => { calls.push({url, init}); return new Response(JSON.stringify({code: 'CHAT_CONTEXT_CHANGED', error: '冲突'}), {status: 409, headers: {'Content-Type': 'application/json'}}); }});
  await assert.rejects(api.cancel(CID, RID)); assert.equal(calls.length, 1); assert.equal(calls[0].init.method, 'POST'); assert.equal(calls[0].init.body, '{}');
  assert.equal(calls[0].init.headers['X-Cockpit-Chat'], '1'); assert.equal(calls[0].init.credentials, 'same-origin'); assert.equal(calls[0].init.cache, 'no-store'); assert.equal(calls[0].init.redirect, 'error');
  assert.equal('Authorization' in calls[0].init.headers, false); assert.equal('X-Cockpit-Session' in calls[0].init.headers, false, 'stamp belongs to existing fetch wrapper');
  await assert.rejects(api.context()); assert.equal(calls[1].init.method, 'GET'); assert.equal('X-Cockpit-Chat' in calls[1].init.headers, false);
  await assert.rejects(api.start(CID, {}, {onEvent() {}})); assert.equal(calls.length, 3);
  await assert.rejects(api.createSkill({content: 'fixture'})); assert.equal(calls.length, 4);
  assert.throws(() => api.skill('../../other')); assert.throws(() => api.profile('../SOUL.md', {}));
});
test('authentication failure clears state, never falls back or retries paid requests', async () => {
  let clears = 0, calls = 0; const scope = new S.Scope();
  const api = createAPI({scope, onAuth: () => { clears++; scope.invalidate(); }, fetch: async () => { calls++; return new Response('{"code":"AUTH_SESSION_CHANGED"}', {status: 409}); }});
  await assert.rejects(api.start(CID, {}, {onEvent() {}}), S.StaleResponse); assert.equal(calls, 1); assert.equal(clears, 1);
});
test('temporary authentication outages preserve the current scope and never replay a request', async () => {
  for (const status of [429, 503]) {
    let clears = 0, calls = 0; const scope = new S.Scope(); scope.switch(CID); const ticket = scope.ticket();
    const api = createAPI({scope, onAuth: () => { clears++; scope.invalidate(); }, fetch: async () => {
      calls++; return new Response(JSON.stringify({code: 'AUTH_UNAVAILABLE', error: '暂不可用'}), {status});
    }});
    await assert.rejects(api.start(CID, {}, {onEvent() {}}), error => error.status === status);
    assert.equal(calls, 1); assert.equal(clears, 0); assert.equal(scope.current(ticket), true);
  }
});

test('Skill strict frontmatter permits text only and rejects extra permissions/YAML features', () => {
  const valid = makeSkill('weekly-summary', '整理中文😀', 'read /etc/anything; <script>not executable</script>', limits);
  assert.equal(parseSkill('\uFEFF' + valid.replace(/\n/g, '\r\n'), limits).name, 'weekly-summary');
  for (const invalid of [valid.replace('name: weekly-summary', 'name: "weekly-summary"'), valid.replace('---\nread', 'tools: [bash]\n---\nread'), valid.replace('description: "整理中文😀"', 'description: &alias unsafe'), valid.replace('name: weekly-summary', 'name: Bad_Name'), valid.split('\n').slice(0, 4).join('\n') + '\n', valid.replace('name: weekly-summary', 'name: a--b'), valid.replace('description: "整理中文😀"', 'description: "line\\nline"')]) assert.throws(() => parseSkill(invalid, limits));
  assert.throws(() => makeSkill('a'.repeat(65), 'x', 'body', limits));
  assert.throws(() => makeSkill('a', 'd'.repeat(257), 'body', limits));
  assert.throws(() => parseSkill(valid, {...limits, skill_file_bytes: 1}));
  assert.equal(parseSkill(valid, {...limits, skill_file_bytes: S.bytes(valid)}).content, valid);
  assert.throws(() => parseSkill('\uFEFF\uFEFF' + valid, limits), /第 1/);
});
test('file checks, strict import and authenticated Blob download use IDs, not server download_path', async () => {
  const config = {allowed_file_extensions: ['.md', '.html'], limits: {upload_file_bytes: 20}};
  checkUpload({name: 'REPORT.MD', size: 20}, config); assert.throws(() => checkUpload({name: 'a.md', size: 21}, config)); assert.throws(() => checkUpload({name: 'a.pdf', size: 1}, config));
  const file = {name: 'SKILL.md', size: 3, arrayBuffer: async () => new TextEncoder().encode('abc').buffer};
  assert.equal(await readSkillFile(file, 3), 'abc'); await assert.rejects(readSkillFile({...file, name: 'skill.md'}, 3));
  const bom = {...file, size: 6, arrayBuffer: async () => new TextEncoder().encode('\uFEFFabc').buffer};
  assert.equal(await readSkillFile(bom, 6), '\uFEFFabc', 'import decode leaves exactly-once BOM normalization to parser');
  await assert.rejects(readSkillFile({...file, arrayBuffer: async () => new Uint8Array([255]).buffer}, 3));
  assert.equal(safeFilename('../a\r\nb.html'), '.._a__b.html');
  const scope = new S.Scope(), ticket = scope.switch(CID), revoked = [], clicked = [], timers = [];
  scope.revoke = url => revoked.push(url);
  const a = {click() { clicked.push([this.href, this.download]); }, remove() {}};
  let request;
  await downloadFile({scope, ticket, cid: CID, file: {id: MID, name: 'report.md', download_path: 'https://evil.test/steal'}, api: {download: async (...args) => { request = args; return new Blob(['fixture']); }}, doc: {createElement: () => a, body: {append() {}}}, urlAPI: {createObjectURL: () => 'blob:fixture'}, schedule: fn => timers.push(fn)});
  assert.deepEqual(request.slice(0, 2), [CID, MID]); assert.deepEqual(clicked, [['blob:fixture', 'report.md']]);
  scope.invalidate(); assert.deepEqual(revoked, ['blob:fixture']); timers[0](); assert.equal(revoked.length, 1);
});
test('50-MB file boundary and human-readable size errors match the published config', () => {
  const config = {allowed_file_extensions: ['.txt'], limits: {upload_file_bytes: 50 * 1048576}};
  for (const size of [0, 2097153, 50 * 1048576 - 1, 50 * 1048576]) checkUpload({name: 'input.txt', size}, config);
  assert.throws(() => checkUpload({name: 'input.txt', size: 50 * 1048576 + 1}, config), /50 MB/);
  assert.equal(fileSizeLabel(50 * 1048576), '50 MB'); assert.equal(fileSizeLabel(65536), '64 KB'); assert.equal(fileSizeLabel(20), '20 字节'); assert.equal(fileSizeLabel(undefined), '');
});
test('large uploads have a 210-second client deadline without widening ordinary request deadlines', async () => {
  const original = global.setTimeout, deadlines = [], scope = new S.Scope();
  global.setTimeout = (fn, ms, ...args) => { deadlines.push(ms); return original(fn, ms, ...args); };
  try {
    const api = createAPI({scope, fetch: async () => ({ok: true, status: 200, json: async () => ({})})});
    await api.upload(CID, new Blob(['synthetic text']), SID); await api.files(CID);
    assert.deepEqual(deadlines, [210000, 30000]); assert.equal(scope.requests.size, 0);
  } finally { global.setTimeout = original; }
});

class Node {
  constructor(tag, text = '') { this.tagName = tag.toUpperCase(); this.children = []; this._text = text; }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map(n => n.textContent).join(''); }
  append(...children) { this.children.push(...children); }
  addEventListener() {}
}
const fakeDoc = {createElement: tag => new Node(tag), createTextNode: text => new Node('#text', text), createDocumentFragment: () => new Node('#fragment')};
test('Markdown renderer never creates raw HTML, image, SVG, iframe or active URL nodes', () => {
  const source = '# 标题\n<script>alert(1)</script>\n![exfil](https://evil.test/?secret=x)\n[bad](javascript:alert) [good](https://example.test/a)\n```html\n<img src=x onerror=alert(1)>\n```\n- **粗体**\n- `code`';
  const result = markdown(fakeDoc, source), nodes = [];
  function visit(n) { nodes.push(n); n.children.forEach(visit); } visit(result);
  assert.equal(nodes.some(n => ['SCRIPT', 'IMG', 'SVG', 'IFRAME', 'STYLE'].includes(n.tagName)), false);
  const links = nodes.filter(n => n.tagName === 'A'); assert.equal(links.length, 1); assert.equal(links[0].rel, 'noopener noreferrer'); assert.equal(links[0].referrerPolicy, 'no-referrer');
  assert.match(result.textContent, /<script>/); assert.match(result.textContent, /!\[exfil\]/);
  for (const bad of ['javascript:1', 'data:text/html,a', '//evil.test', 'https://u:p@evil.test', 'https://evil.test/\nsecret', 'file:///etc/passwd', 'https%3a//evil.test']) assert.equal(safeLink(bad), null);
});
test('assets have no browser body persistence, raw HTML injection, EventSource or foreign feature dependency', () => {
  for (const file of fs.readdirSync(root).filter(name => name.endsWith('.js'))) {
    const source = fs.readFileSync(path.join(root, file), 'utf8');
    for (const bad of ['sessionStorage', 'indexedDB', 'innerHTML', 'outerHTML', 'insertAdjacentHTML', 'EventSource', '/image-studio', '/intelligence', 'CockpitAuth.json']) assert.equal(source.includes(bad), false, `${file}: ${bad}`);
    if (file === 'app.js') { assert.equal((source.match(/localStorage/g) || []).length, 1); assert.match(source, /storage: \(\) => win\.localStorage/); }
    else assert.equal(source.includes('localStorage'), false, `${file}: unscoped persistence`);
    if (file !== 'selection.js') assert.doesNotMatch(source, /\.(?:setItem|removeItem)\s*\(/, `${file}: writes outside selector whitelist`);
  }
});
test('templates reuse existing partials, include all local assets and declare every controller DOM target once', () => {
  const templateRoot = path.join(root, '../../templates/chat'), base = fs.readFileSync(path.join(templateRoot, '_base.html'), 'utf8');
  for (const name of ['auth', 'header', 'sidebar']) assert.equal(base.includes(`partials/${name}.html`), true);
  for (const [page, script] of [['index', 'app'], ['settings', 'settings']]) {
    const markup = base + fs.readFileSync(path.join(templateRoot, `${page}.html`), 'utf8'), ids = [...markup.matchAll(/\bid="([^"]+)"/g)].map(m => m[1]);
    assert.equal(new Set(ids).size, ids.length, `${page}: duplicate ID`);
    const source = fs.readFileSync(path.join(root, `${script}.js`), 'utf8');
    const optional = page === 'index' ? new Set(['chat-skills', 'chat-skills-clear', 'chat-context-refresh', 'chat-context-info', 'chat-skill-choices', 'chat-skill-order']) : new Set();
    for (const [, id] of source.matchAll(/\$\('([^']+)'\)/g)) assert.equal(ids.includes(id) || optional.has(id), true, `${script}: missing #${id}`);
    for (const [, asset] of markup.matchAll(/(?:src|href)="\/cockpit\/static\/chat\/([^\"]+)"/g)) assert.equal(fs.existsSync(path.join(root, asset)), true, `missing ${asset}`);
  }
});
test('persistent Skills history is bounded non-run metadata and never becomes a model run', () => {
  const created = event(1, 'conversation.created', {title: 'persistent'});
  const loaded = event(2, 'conversation.skills.updated', {skill_ids: [SID, MID]});
  const events = S.mergeEvents([created], [loaded], CID);
  assert.deepEqual(events[1].data.skill_ids, [SID, MID]);
  assert.equal(S.projectEvents(events).runs.length, 0);
  for (const data of [{skill_ids: [SID, SID]}, {skill_ids: [CID, RID, MID, SID]}, {skill_ids: ['../skill']}, {skill_ids: null}, {skill_ids: [], owner_user_id: 99}]) assert.throws(() => S.mergeEvents([created], [{...loaded, data}], CID), /Skills/);
  assert.throws(() => S.mergeEvents([created], [{...loaded, run_id: RID}], CID), /Skills/);
});
