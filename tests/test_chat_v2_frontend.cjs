'use strict';
/* Synthetic, offline fixtures only: no backend/model calls or credentials. */
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {randomUUID} = require('node:crypto');
const S = require('../src/new_api_cockpit/static/chat/state.js');
const R = require('../src/new_api_cockpit/static/chat/render.js');
const {createAPI} = require('../src/new_api_cockpit/static/chat/api.js');
const {createChatApp} = require('../src/new_api_cockpit/static/chat/app.js');
const CID = '11111111-1111-4111-8111-111111111111', RID = '22222222-2222-4222-8222-222222222222';
const MIDS = [3, 4, 5, 6].map(n => `${n}${'0'.repeat(7)}-1111-4111-8111-111111111111`);
const CAP = 'sha256:' + 'a'.repeat(64);
const clone = value => JSON.parse(JSON.stringify(value));
const defer = () => { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b; }); return {promise, resolve, reject}; };
const model = (protocol = 'chat_completions') => ({id: protocol, protocol, chat_enabled: true, tools_enabled: true, native_candidates: protocol === 'chat_completions', candidate_limit: 4, compaction: protocol === 'responses' ? 'native' : 'summary', max_input_bytes: 65536, capability_revision: CAP});
const input = () => ({meta: {revision: 9, storage_state: 'ok', active_run_id: null}, token: {id: 7, usable: true}, model: model(), mode: 'chat', text: 'question', attachments: [], skillIds: [], context: {context_revision: 0, skills: [], limits: {skills_per_run: 3}}, config: {modes: ['chat', 'files'], limits: {user_text_bytes: 32768, attachments_per_turn: 8}}, requestId: CID});
const event = (seq, type, data = {}, rid = RID, cid = CID) => ({v: 1, seq, type, data, run_id: rid, conversation_id: cid, at: '2026-10-16T00:00:00Z'});
function round({count = 4, protocol = 'chat_completions', terminal = true} = {}) {
  const events = [event(1, 'conversation.created', {title: 'v2 history'}, null)];
  const push = (type, data) => events.push(event(events.length + 1, type, data));
  push('run.accepted', {client_request_id: randomUUID(), text: 'private question', model: protocol, mode: 'chat', protocol, options: {candidate_count: count, web_enabled: false, auto_memory: false, context_strategy: 'adaptive'}, context_snapshot: {context_revision: 0, skill_ids: []}});
  if (protocol === 'chat_completions') push('run.dispatching', {request_index: 1, assistant_message_id: MIDS[0], purpose: 'main', candidate_index: 0, candidate_message_ids: MIDS.slice(0, count)});
  for (let i = 0; i < count; i++) {
    if (protocol !== 'chat_completions') push('run.dispatching', {request_index: i + 1, assistant_message_id: MIDS[i], purpose: 'main', candidate_index: i, candidate_message_ids: [MIDS[i]]});
    // Deliberately omit candidate_index: dispatch mapping must retain it.
    push('assistant.delta', {message_id: MIDS[i], text: `answer-${i}`});
    push('assistant.reasoning', {message_id: MIDS[i], text: `raw-${i}`, kind: 'raw', field: 'reasoning_content'});
    push('assistant.reasoning', {message_id: MIDS[i], text: '-continued', kind: 'raw', field: 'reasoning_content'});
    push('assistant.reasoning', {message_id: MIDS[i], text: `summary-${i}`, kind: 'summary', field: 'thinking'});
    push('assistant.protocol', {message_id: MIDS[i], protocol, state: {secret: 'OPAQUE-NEVER-RENDER', url: 'https://evil.test/opaque'}});
    push('assistant.message', {message_id: MIDS[i], text: `final-${i}`, complete: true, tool_calls: [], finish_reason: 'stop'});
  }
  if (terminal) push('run.terminal', {status: 'completed', uncertain: false, usage: null, usage_complete: false});
  return events;
}

test('v2 explicit options are flat, bounded and opt-in; omitted options keep exact legacy DTO', () => {
  const args = input(), legacy = S.buildRunRequest(args);
  assert.deepEqual(legacy, {client_request_id: CID, expected_revision: 9, token_id: 7, model: 'chat_completions', capability_revision: CAP, context_revision: 0, skill_ids: [], mode: 'chat', text: 'question', attachment_ids: []});
  assert.deepEqual(S.buildRunRequest({...args, options: {}}), {...legacy, candidate_count: 1, web_enabled: false, auto_memory: false, context_strategy: 'adaptive'});
  assert.equal(S.buildRunRequest({...args, options: {candidate_count: 4}}).candidate_count, 4);
  const files = S.buildRunRequest({...args, mode: 'files', options: {web_enabled: true, auto_memory: true, context_strategy: 'recent'}});
  assert.equal(files.web_enabled, true); assert.equal(files.auto_memory, true);
  for (const options of [{candidate_count: 0}, {candidate_count: 5}, {candidate_count: true}, {candidate_count: '2'}, {web_enabled: 'true'}, {auto_memory: 1}, {web_enabled: true}, {context_strategy: 'all'}, {unknown: true}, null]) assert.throws(() => S.buildRunRequest({...args, options}));
  assert.throws(() => S.buildRunRequest({...args, mode: 'files', options: {candidate_count: 2}}), /Agent/);
  assert.throws(() => S.buildRunRequest({...args, model: {...args.model, candidate_limit: 2}, options: {candidate_count: 3}}), /超限/);
});

test('v1 extended events preserve separate candidate text and public reasoning across snapshots/replay', () => {
  const events = round();
  const runs = S.projectEvents(S.mergeEvents([], events, CID)).runs;
  assert.equal(runs.length, 1); const run = runs[0];
  assert.equal(run.messages.size, 4); assert.equal(run.requests.size, 1);
  for (let i = 0; i < 4; i++) {
    const m = run.messages.get(MIDS[i]);
    assert.equal(m.candidate_index, i); assert.equal(m.text, `final-${i}`);
    assert.deepEqual(m.reasoning, [{kind: 'raw', field: 'reasoning_content', text: `raw-${i}-continued`}, {kind: 'summary', field: 'thinking', text: `summary-${i}`}]);
    assert.equal('state' in m, false); assert.equal('protocol' in m, false);
  }
  const withEmpty = [...events.slice(0, -1), event(events.length, 'assistant.message', {message_id: MIDS[0], text: '', complete: true, candidate_index: 0}), event(events.length + 1, 'run.terminal', {status: 'completed'})];
  assert.equal(S.projectEvents(clone(withEmpty)).runs[0].messages.get(MIDS[0]).text, 'final-0');
  assert.deepEqual(S.projectEvents(clone(events)).runs[0].messages, run.messages);
  assert.throws(() => S.mergeEvents([], [{...events[0], v: 2}], CID));
  assert.throws(() => S.mergeEvents([], [{...events[0], conversation_id: RID}], CID));
});

test('candidate.selected after terminal unlocks only latest completed run; follow-up freezes older choices', () => {
  let events = round(), runs = S.projectEvents(events).runs;
  assert.equal(S.needsCandidateSelection(runs), true);
  assert.equal(S.canSelectCandidate(runs, RID, 3), true);
  for (const index of [-1, 4, null, true]) assert.equal(S.canSelectCandidate(runs, RID, index), false);
  events.push(event(events.length + 1, 'candidate.selected', {candidate_index: 2}));
  runs = S.projectEvents(events).runs;
  assert.equal(runs[0].status, 'completed'); assert.equal(S.selectedCandidate(runs[0]), 2);
  assert.equal(S.needsCandidateSelection(runs), false);
  assert.equal(S.canSelectCandidate(runs, RID, 0), true, 'selection can change until a follow-up exists');
  events.push(event(events.length + 1, 'run.accepted', {text: 'follow-up'}, randomUUID()));
  assert.equal(S.canSelectCandidate(S.projectEvents(events).runs, RID, 0), false);
  assert.equal(S.canSelectCandidate(S.projectEvents(round({terminal: false})).runs, RID, 0), false);
  assert.equal(S.needsCandidateSelection(S.projectEvents(round({count: 1})).runs), false);
});

// Same minimal fake-DOM convention as test_review_chat_history.cjs, with details tracking.
class Node {
  constructor(tag = 'div') { this.tagName = tag.toUpperCase(); this.children = []; this.dataset = {}; this.attrs = {}; this.value = ''; this._text = ''; this.disabled = false; this.checked = false; this.open = false; this.handlers = new Map(); this.classList = {toggle() {}}; }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map(child => child.textContent).join(''); }
  get firstChild() { return this.children[0]; }
  append(...nodes) { for (const node of nodes) this.children.push(...(node.tagName === '#FRAGMENT' ? node.children : [node])); }
  replaceChildren(...nodes) { this._text = ''; this.children = []; this.append(...nodes); }
  addEventListener(name, fn) { this.handlers.set(name, fn); }
  setAttribute(name, value) { this.attrs[name] = value; }
  querySelectorAll(selector) { return descendants(this).filter(n => selector === 'details[open]' && n.tagName === 'DETAILS' && n.open); }
  scrollIntoView() {}
  close() { this.open = false; }
  async fire(name, event = {preventDefault() {}}) { return this.handlers.get(name)?.(event); }
}
function descendants(node) { return node.children.flatMap(n => [n, ...descendants(n)]); }
function surfaces(v2 = true) {
  const nodes = new Map();
  const doc = {getElementById(id) { if (!nodes.has(id)) nodes.set(id, new Node()); return nodes.get(id); }, createElement: tag => new Node(tag), createTextNode: text => { const n = new Node('#text'); n.textContent = text; return n; }, createDocumentFragment: () => new Node('#fragment'), documentElement: {scrollHeight: 0}};
  doc.getElementById('chat-mode').value = 'chat';
  if (v2) {
    const count = new Node('select'); count.value = '1'; count.append(...[1, 2, 3, 4].map(n => { const o = new Node('option'); o.value = String(n); return o; })); nodes.set('chat-candidate-count', count);
    const strategy = new Node('select'); strategy.value = 'adaptive'; nodes.set('chat-context-strategy', strategy);
    for (const id of ['chat-web-enabled', 'chat-auto-memory']) nodes.set(id, new Node('input'));
  }
  const win = {location: {href: 'http://offline.test/cockpit/chat/', pathname: '/cockpit/chat/'}, history: {pushState() {}, replaceState() {}}, crypto: {randomUUID}, clearTimeout() {}, setTimeout() { return 1; }, requestAnimationFrame: fn => setImmediate(fn), cancelAnimationFrame: clearImmediate, innerHeight: 800, scrollY: 0, addEventListener() {}};
  return {doc, win, $: id => doc.getElementById(id)};
}

test('renderer keeps candidate cards independent, folds labeled raw/summary, and excludes opaque state/remote images', async () => {
  const {doc} = surfaces(), target = new Node(), events = round(), calls = [];
  events.splice(-1, 0, event(0, 'run.notice', {code: '<script>notice</script>', message: '![remote](https://evil.test/img) <img src=x>'}));
  events.splice(-1, 0, event(0, 'context.compacted', {mode: 'native', checkpoint: {opaque: 'SECRET-CHECKPOINT'}}), event(0, 'memory.updated', {before: 'SECRET-BEFORE', after: 'SECRET-AFTER'}));
  const runs = S.projectEvents(events).runs;
  R.renderRuns(doc, target, runs, () => {}, {onSelectCandidate: (...args) => calls.push(args)});
  const nodes = descendants(target), cards = nodes.filter(n => n.dataset.candidate !== undefined);
  assert.equal(cards.length, 4);
  for (let i = 0; i < 4; i++) { assert.match(cards[i].textContent, new RegExp(`final-${i}`)); assert.doesNotMatch(cards[i].textContent, new RegExp(`final-${(i + 1) % 4}`)); }
  const details = nodes.filter(n => n.tagName === 'DETAILS');
  assert.equal(details.length, 8); assert.equal(details.every(n => !n.open), true);
  assert.match(target.textContent, /raw \/ reasoning_content/); assert.match(target.textContent, /summary \/ thinking/);
  assert.doesNotMatch(target.textContent, /OPAQUE-NEVER-RENDER|SECRET-CHECKPOINT|SECRET-BEFORE|SECRET-AFTER/);
  assert.equal(nodes.some(n => ['SCRIPT', 'IMG', 'SVG', 'IFRAME'].includes(n.tagName)), false);
  assert.match(target.textContent, /<script>notice<\/script>/);
  const choose = nodes.find(n => n.className?.includes('chat-candidate-select')); assert.equal(choose.disabled, false);
  await choose.fire('click'); assert.deepEqual(calls, [[RID, 0]]);
  details[0].open = true;
  R.renderRuns(doc, target, S.projectEvents(clone(events)).runs, () => {}, {onSelectCandidate() {}, selectionDisabled: true});
  assert.equal(descendants(target).find(n => n.tagName === 'DETAILS').open, true);
  assert.equal(descendants(target).filter(n => n.className?.includes('chat-candidate-select')).every(n => n.disabled), true);
  const withFollowup = [...events, event(0, 'run.accepted', {text: 'next'}, randomUUID())];
  R.renderRuns(doc, target, S.projectEvents(withFollowup).runs, () => {}, {onSelectCandidate() {}});
  assert.equal(descendants(target).filter(n => n.className?.includes('chat-candidate-select')).every(n => n.disabled), true);
});

test('native n counts one request, other protocols count separate requests, auxiliary usage stays purpose-separated', () => {
  assert.equal(S.projectEvents(round()).runs[0].requests.size, 1);
  for (const protocol of ['responses', 'anthropic']) assert.equal(S.projectEvents(round({protocol})).runs[0].requests.size, 4);
  const events = round({count: 1}).slice(0, -1);
  for (const [i, purpose] of ['main', 'summary', 'native_compact', 'memory'].entries()) {
    events.push(event(0, 'run.dispatching', {request_index: i + 1, purpose}));
    if (purpose !== 'memory') {
      const usage = event(0, 'run.usage', {request_index: i + 1, prompt_tokens: 1, completion_tokens: 2, total_tokens: 3});
      events.push(usage, clone(usage));
    }
  }
  const run = S.projectEvents(events).runs[0];
  assert.equal(run.usage.total_tokens, 9); assert.equal(run.requestUsage.size, 3);
  const text = R.requestUsageText(run);
  for (const name of ['主回答', '摘要', '原生压缩', '自动记忆']) assert.match(text, new RegExp(`${name} 1 次请求`));
  assert.match(text, /自动记忆 1 次请求 · 用量未知/); assert.match(text, /单独计费/);
});

test('single-answer footer no longer labels it a candidate or claims a user explicitly toggled defaults', () => {
  const {doc} = surfaces(false), target = new Node(), events = round({count: 1});
  events.find(e => e.type === 'run.accepted').data.options = {auto_memory: true, web_enabled: true, context_strategy: 'adaptive'};
  R.renderRuns(doc, target, S.projectEvents(events).runs, () => {});
  assert.match(target.textContent, /自适应上下文 · 自动记忆已开启 · 联网按需可用/);
  assert.doesNotMatch(target.textContent, /个候选|显式开启|本轮选项/);
});

test('context.selected reuses a checkpoint without counting an auxiliary request, unlike newly compacted notice', () => {
  const events = round({count: 1}), data = {mode: 'summary', covered_run_ids: [RID], source_sha256: 'a'.repeat(64), checkpoint: {file: 'opaque-checkpoint-file', sha256: 'b'.repeat(64), size_bytes: 100}};
  events.push(event(events.length + 1, 'context.compacted', data), event(events.length + 2, 'context.selected', data));
  const run = S.projectEvents(S.mergeEvents([], events, CID)).runs[0];
  assert.equal(run.requests.size, 1); assert.equal(run.requestUsage.size, 0);
  assert.match(run.notices[0].message, /新生成/); assert.match(run.notices[1].message, /复用已有/);
  assert.match(run.notices[1].message, /没有新增辅助模型调用/); assert.match(run.notices[1].message, /summary · 覆盖 1 轮/);
  assert.doesNotMatch(run.notices[1].message, /opaque-checkpoint-file/);
});

test('selectCandidate API sends one exact revision-guarded POST using existing authentication/scope', async () => {
  const scope = new S.Scope(); scope.switch(CID); const calls = [];
  const api = createAPI({scope, fetch: async (url, init) => { calls.push({url, init}); return new Response(JSON.stringify({error: 'conflict', code: 'CHAT_REVISION_CONFLICT'}), {status: 409}); }});
  await assert.rejects(api.selectCandidate(CID, RID, 99, 3));
  assert.equal(calls.length, 1); const {url, init} = calls[0];
  assert.equal(url, `/cockpit/api/chat/conversations/${CID}/runs/${RID}/candidate`);
  assert.equal(init.method, 'POST'); assert.deepEqual(JSON.parse(init.body), {expected_revision: 99, candidate_index: 3});
  assert.equal(init.credentials, 'same-origin'); assert.equal(init.headers['X-Cockpit-Chat'], '1'); assert.equal('Authorization' in init.headers, false);
  for (const [revision, candidate] of [[-1, 0], [1, -1], [1, 4], [1, true]]) assert.throws(() => api.selectCandidate(CID, RID, revision, candidate));
  assert.equal(calls.length, 1);
});

async function fixture(t, {protocol = 'chat_completions', v2 = true, history = null} = {}) {
  const surface = surfaces(v2), {doc, win, $} = surface;
  const events = history || [event(1, 'conversation.created', {title: 'v2 history'}, null)];
  const meta = {id: CID, title: 'v2 history', revision: events.length, last_seq: events.length, active_run_id: null, storage_state: 'ok', last_selection: {token_id: 7, model: protocol, mode: 'chat'}};
  const f = {...surface, meta, events, posts: [], choices: [], reads: []};
  f.append = (type, data, rid = RID) => { const e = event(events.length + 1, type, data, rid); events.push(e); meta.revision = meta.last_seq = e.seq; return e; };
  f.api = {
    config: async () => ({modes: ['chat', 'files'], allowed_file_extensions: ['.txt'], limits: {user_text_bytes: 32768, attachments_per_turn: 8, event_page_max: 5, max_model_requests: 6, max_aux_model_requests: 4}}),
    tokens: async () => ({tokens: [{id: 7, usable: true}]}), context: async () => input().context,
    conversations: async () => ({conversations: [clone(meta)], total: 1, has_more: false, next_cursor: null}),
    conversation: async cid => { f.reads.push('conversation'); return {conversation: cid === CID ? clone(meta) : {...clone(meta), id: cid, title: 'other', revision: 1, last_seq: 1}}; },
    models: async () => ({models: [model(protocol)]}),
    events: async (cid, after, until, limit) => {
      f.reads.push('events'); const history = cid === CID ? events : [event(1, 'conversation.created', {title: 'other'}, null, cid)];
      const high = until ?? history.length, page = history.filter(e => e.seq > after && e.seq <= high).slice(0, limit), next = page.at(-1)?.seq ?? after;
      return {events: clone(page), until_seq: high, next_after_seq: next, has_more: next < high};
    },
    run: async (_cid, rid) => { f.reads.push('run'); return {run: {id: rid, status: 'completed'}}; },
    files: async () => { f.reads.push('files'); return {files: []}; },
    selectCandidate: async (cid, rid, revision, index) => {
      f.choices.push({cid, rid, revision, index}); f.append('candidate.selected', {candidate_index: index});
      return {run: {id: rid, status: 'completed', selected_candidate: index}, conversation: clone(meta)};
    },
    start: async (_cid, body, options) => {
      f.posts.push(clone(body)); const rid = randomUUID(), mid = randomUUID();
      const items = [f.append('run.accepted', {...body, options: {candidate_count: body.candidate_count ?? 1}, context_snapshot: {context_revision: 0, skill_ids: []}}, rid), f.append('assistant.message', {message_id: mid, candidate_index: 0, text: 'follow-up final', complete: true, tool_calls: [], finish_reason: 'stop'}, rid), f.append('run.terminal', {status: 'completed'}, rid)];
      for (const e of items) options.onEvent(e);
      return {terminal: true};
    },
  };
  f.app = createChatApp({window: win, document: doc, api: f.api}); t.after(() => f.app.clearAll());
  await f.app.ready; await f.app.openConversation(CID);
  $('chat-token').value = '7'; await $('chat-model-load').fire('click'); $('chat-text').value = 'new question';
  return f;
}
const submit = f => f.app.send({preventDefault() {}});

test('controller always sends single/adaptive/auto-memory defaults, independent of removed controls and optional config', async t => {
  for (const v2 of [false, true]) {
    const f = await fixture(t, {v2}); await submit(f); assert.equal(f.posts.length, 1);
    const body = f.posts[0];
    assert.equal(body.candidate_count, 1); assert.equal(body.web_enabled, false); assert.equal(body.auto_memory, true); assert.equal(body.context_strategy, 'adaptive');
  }
});

test('all protocols still use one answer and default web/memory without checkboxes or explanatory UI', async t => {
  for (const protocol of ['chat_completions', 'responses', 'anthropic']) {
    const f = await fixture(t, {protocol}), {$} = f;
    // Even an obsolete/cached control cannot override the new UI defaults.
    $('chat-candidate-count').value = '4'; $('chat-context-strategy').value = 'recent';
    await $('chat-model').fire('change');
    $('chat-mode').value = 'files'; await $('chat-mode').fire('change');
    assert.equal(f.posts.length, 0, 'Changing mode must never generate');
    await submit(f); assert.equal(f.posts[0].candidate_count, 1); assert.equal(f.posts[0].web_enabled, true); assert.equal(f.posts[0].auto_memory, true); assert.equal(f.posts[0].context_strategy, 'adaptive');
    $('chat-mode').value = 'chat'; await $('chat-mode').fire('change'); $('chat-text').value = 'follow up'; await submit(f);
    assert.equal(f.posts[1].web_enabled, false); assert.equal(f.posts[1].auto_memory, true); assert.equal(f.posts[1].candidate_count, 1);
  }
});

test('candidate selection blocks direct send, uses one POST then paginated GET, preserving text/reasoning and freezing after follow-up', async t => {
  const f = await fixture(t, {history: round()}), {$, app} = f;
  assert.equal($('chat-send').disabled, true); await submit(f); assert.equal(f.posts.length, 0);
  assert.match($('chat-candidate-status').textContent, /选择一个分支/);
  const old = clone(app.state.events), gate = defer(), entered = defer(), original = f.api.conversation;
  f.api.conversation = async (...args) => { entered.resolve(); await gate.promise; return original(...args); };
  const selecting = app.selectCandidate(RID, 2); await entered.promise;
  assert.equal(app.state.selecting, true); assert.equal($('chat-send').disabled, true);
  await submit(f); await app.selectCandidate(RID, 1); assert.equal(f.choices.length, 1); assert.equal(f.posts.length, 0);
  assert.deepEqual(f.choices[0], {cid: CID, rid: RID, revision: old.length, index: 2});
  gate.resolve(); await selecting;
  assert.equal(app.state.historyStatus, 'ready'); assert.equal(app.state.selecting, false); assert.equal($('chat-send').disabled, false);
  assert.deepEqual(app.state.events.slice(0, old.length), old);
  assert.equal(S.projectEvents(app.state.events).runs[0].messages.get(MIDS[2]).reasoning.length, 2);
  assert.match($('chat-messages').textContent, /候选 3 · 已选/);
  const before = f.reads.length; await app.recover(); assert.ok(f.reads.length > before); assert.equal(f.posts.length, 0); assert.equal(f.choices.length, 1);
  await submit(f); assert.equal(f.posts.length, 1);
  await app.selectCandidate(RID, 1); assert.equal(f.choices.length, 1);
  assert.equal(descendants($('chat-messages')).filter(n => n.className?.includes('chat-candidate-select')).every(n => n.disabled), true);
});

test('lost/conflicting selection responses only GET-reconcile and never automatically repeat the write', async t => {
  for (const committed of [false, true]) {
    const f = await fixture(t, {history: round()}), original = f.api.selectCandidate;
    f.api.selectCandidate = async (...args) => { if (committed) await original(...args); else f.choices.push(args); throw new Error(committed ? 'lost response' : 'revision conflict'); };
    await f.app.selectCandidate(RID, 1);
    assert.equal(f.choices.length, 1); assert.equal(f.posts.length, 0); assert.match(f.$('chat-status').textContent, /未自动重试/);
    assert.equal(f.$('chat-send').disabled, !committed);
  }
});

for (const change of ['chat', 'account']) test(`late candidate selection cannot restore previous ${change} content or options`, async t => {
  const f = await fixture(t, {history: round()}), gate = defer();
  f.api.selectCandidate = () => gate.promise;
  const selecting = f.app.selectCandidate(RID, 1);
  if (change === 'account') f.app.clearAll(); else await f.app.openConversation(randomUUID());
  gate.resolve({run: {id: RID, status: 'completed', selected_candidate: 1}, conversation: clone(f.meta)}); await selecting;
  assert.equal(f.app.state.events.some(e => e.run_id === RID), false);
  assert.doesNotMatch(f.$('chat-messages').textContent, /private question|final-0|raw-0/);
  assert.equal(f.app.state.selecting, false); assert.equal(f.posts.length, 0);
});

const memoryAudit = () => ({v: 1, kind: 'auto_memory_update', owner_user_id: 42, profile: 'MEMORY.md', changed: true,
  before: {content: 'private BEFORE <script>never execute</script>', sha256: 'a'.repeat(64), context_revision: 5, memory_revision: 2},
  after: {content: 'private AFTER ![remote](https://evil.test/memory)', sha256: 'b'.repeat(64), context_revision: 6, memory_revision: 3},
  source_ids: [MIDS[0]], fact_ids: ['fact_example']});
function memoryRound({proposedOnly = false} = {}) {
  const events = round({count: 1}), audit = memoryAudit();
  events.push(event(events.length + 1, 'memory.proposed', {intent: {v: 1, profile: 'MEMORY.md', before: 'UNCONFIRMED-BEFORE', after: 'UNCONFIRMED-AFTER', before_sha256: audit.before.sha256, after_sha256: audit.after.sha256, source_message_ids: [MIDS[0]], expected_context_revision: 5, expected_memory_revision: 2}}));
  if (!proposedOnly) events.push(event(events.length + 1, 'memory.updated', {audit}));
  return events;
}

test('Memory audit contents/undo are owner-only; proposed intent is trace-only and compaction shows metadata not checkpoint contents', () => {
  const events = memoryRound(), hash = 'c'.repeat(64), checkpointHash = 'd'.repeat(64);
  events.push(event(events.length + 1, 'context.compacted', {mode: 'native', covered_run_ids: [RID, CID], source_sha256: hash, checkpoint: {file: 'DO-NOT-RENDER-CHECKPOINT', sha256: checkpointHash, size_bytes: 50}}));
  const runs = S.projectEvents(S.mergeEvents([], events, CID)).runs, {doc} = surfaces(), target = new Node();
  for (const ownerUserId of [null, 0, 41]) {
    R.renderRuns(doc, target, runs, () => {}, {ownerUserId, onUndoMemory() {}});
    assert.doesNotMatch(target.textContent, /private BEFORE|private AFTER/);
    assert.equal(descendants(target).some(n => n.className?.includes('chat-memory-undo')), false);
  }
  R.renderRuns(doc, target, runs, () => {}, {ownerUserId: 42, onUndoMemory() {}});
  assert.match(target.textContent, /private BEFORE/); assert.match(target.textContent, /private AFTER/);
  assert.match(target.textContent, /MEMORY v3 · 上下文 v6/); assert.match(target.textContent, /模式 native · 覆盖 2 轮/);
  assert.match(target.textContent, new RegExp(hash)); assert.match(target.textContent, new RegExp(checkpointHash));
  assert.doesNotMatch(target.textContent, /UNCONFIRMED|DO-NOT-RENDER-CHECKPOINT/);
  assert.equal(descendants(target).some(n => ['SCRIPT', 'IMG'].includes(n.tagName)), false);
  assert.equal(descendants(target).find(n => n.className?.includes('chat-memory-undo')).disabled, false);
  const trace = S.projectEvents(memoryRound({proposedOnly: true})).runs;
  assert.equal(S.ownerMemoryAudit(trace[0], 42), null);
  R.renderRuns(doc, target, trace, () => {}, {ownerUserId: 42, onUndoMemory() {}});
  assert.match(target.textContent, /不是 MEMORY 已写入的确认/);
  assert.doesNotMatch(target.textContent, /UNCONFIRMED-AFTER/);
  events.push(event(events.length + 1, 'memory.reverted', {after_sha256: 'a'.repeat(64), context_revision: 10, memory_revision: 4}));
  const reverted = S.projectEvents(events).runs;
  assert.equal(reverted[0].status, 'completed'); assert.equal(reverted[0].memory_reverted, true);
  R.renderRuns(doc, target, reverted, () => {}, {ownerUserId: 42, onUndoMemory() {}});
  assert.equal(descendants(target).find(n => n.className?.includes('chat-memory-undo')).disabled, true);
});

test('undoMemory API uses exact latest context/conversation revisions, authenticated POST and no automatic replay', async () => {
  const scope = new S.Scope(); scope.switch(CID); const calls = [];
  const api = createAPI({scope, fetch: async (url, init) => { calls.push({url, init}); return new Response('{"error":"conflict"}', {status: 409}); }});
  await assert.rejects(api.undoMemory(CID, RID, 29, 50));
  assert.equal(calls.length, 1); assert.equal(calls[0].url, `/cockpit/api/chat/conversations/${CID}/runs/${RID}/memory/undo`);
  assert.equal(calls[0].init.method, 'POST'); assert.equal(calls[0].init.headers['X-Cockpit-Chat'], '1');
  assert.deepEqual(JSON.parse(calls[0].init.body), {context_revision: 29, expected_revision: 50});
  for (const args of [[-1, 1], [1, true], [1, -1]]) assert.throws(() => api.undoMemory(CID, RID, ...args));
  assert.equal(calls.length, 1);
});

async function memoryFixture(t) {
  const f = await fixture(t, {history: memoryRound()}), audit = memoryAudit();
  f.$('chat-root').dataset.ownerUserId = '42';
  // Context changed after the run due to another profile edit, not MEMORY:
  // undo must use this fresh revision, never audit.after.context_revision.
  f.memoryContext = {...input().context, context_revision: 29, profiles: [{name: 'MEMORY.md', content: audit.after.content, sha256: audit.after.sha256, revision: audit.after.memory_revision}]};
  f.undoCalls = []; f.order = [];
  f.api.context = async () => { f.order.push('GET context'); return clone(f.memoryContext); };
  const metadata = f.api.conversation;
  f.api.conversation = async (...args) => { f.order.push('GET conversation'); return metadata(...args); };
  f.api.undoMemory = async (cid, rid, contextRevision, revision) => {
    f.order.push('POST undo'); f.undoCalls.push({cid, rid, contextRevision, revision});
    f.memoryContext = {...f.memoryContext, context_revision: 30, profiles: [{name: 'MEMORY.md', content: audit.before.content, sha256: audit.before.sha256, revision: 4}]};
    f.append('memory.reverted', {after_sha256: audit.before.sha256, context_revision: 30, memory_revision: 4});
    return {context: clone(f.memoryContext), conversation: clone(f.meta)};
  };
  await f.app.recover(); f.order = [];
  return f;
}

test('Memory undo is click-only, reads current context/conversation, writes once then refreshes context and history', async t => {
  const f = await memoryFixture(t);
  assert.equal(f.undoCalls.length, 0); const old = clone(f.app.state.events);
  const undo = descendants(f.$('chat-messages')).find(n => n.className?.includes('chat-memory-undo'));
  assert.ok(undo); assert.equal(undo.disabled, false);
  await undo.fire('click');
  assert.deepEqual(f.order.slice(0, 4), ['GET context', 'GET conversation', 'POST undo', 'GET context']);
  assert.deepEqual(f.undoCalls, [{cid: CID, rid: RID, contextRevision: 29, revision: old.length}]);
  assert.equal(f.app.state.context.context_revision, 30); assert.equal(f.app.state.historyStatus, 'ready');
  assert.deepEqual(f.app.state.events.slice(0, old.length), old); assert.match(f.$('chat-messages').textContent, /已撤销/);
  await f.app.undoMemory(RID); assert.equal(f.undoCalls.length, 1); assert.equal(f.posts.length, 0);
});

test('Memory undo never overwrites a later memory edit and never retries a conflicting write', async t => {
  const changed = await memoryFixture(t);
  changed.memoryContext.profiles[0].sha256 = 'c'.repeat(64); changed.memoryContext.profiles[0].revision = 4;
  await changed.app.undoMemory(RID);
  assert.equal(changed.undoCalls.length, 0); assert.match(changed.$('chat-status').textContent, /拒绝覆盖/);
  const conflict = await memoryFixture(t);
  conflict.api.undoMemory = async (...args) => { conflict.undoCalls.push(args); throw new Error('revision conflict'); };
  await conflict.app.undoMemory(RID);
  assert.equal(conflict.undoCalls.length, 1); assert.equal(conflict.posts.length, 0);
  assert.match(conflict.$('chat-status').textContent, /未自动重试/);
  assert.ok(conflict.order.filter(x => x === 'GET context').length >= 2);
});

test('auto-memory read-only recovery refreshes stale personal context; lost undo response reconciles without a second write', async t => {
  const f = await memoryFixture(t);
  f.app.state.context = {...input().context, context_revision: 0};
  await f.app.recover(); assert.equal(f.app.state.context.context_revision, 29);
  const original = f.api.undoMemory;
  f.api.undoMemory = async (...args) => { await original(...args); throw new Error('lost undo response'); };
  await f.app.undoMemory(RID);
  assert.equal(f.undoCalls.length, 1); assert.equal(f.posts.length, 0); assert.equal(f.app.state.context.context_revision, 30);
  assert.match(f.$('chat-status').textContent, /只读历史已确认自动记忆已撤销/);
});

for (const phase of ['context', 'write']) test(`account invalidation during Memory undo ${phase} drops private results`, async t => {
  const f = await memoryFixture(t), gate = defer(), entered = defer();
  if (phase === 'context') f.api.context = async () => { entered.resolve(); return gate.promise; };
  else f.api.undoMemory = async () => { entered.resolve(); return gate.promise; };
  const undoing = f.app.undoMemory(RID); await entered.promise;
  assert.equal(f.app.state.undoing, true); await f.app.undoMemory(RID);
  f.app.clearAll();
  gate.resolve(phase === 'context' ? clone(f.memoryContext) : {context: clone(f.memoryContext), conversation: clone(f.meta)});
  await undoing;
  assert.deepEqual(f.app.state.events, []); assert.equal(f.app.state.context, null); assert.equal(f.app.state.undoing, false);
  assert.doesNotMatch(f.$('chat-messages').textContent, /private BEFORE|private AFTER/);
  assert.equal(f.undoCalls.length, 0); assert.equal(f.posts.length, 0);
});

test('unavailable search does not disable fetch/download defaults; unsupported adaptive never silently downgrades', async t => {
  const f = await fixture(t);
  f.app.state.config.features = {protocols: ['chat_completions', 'responses', 'anthropic'], multiple_candidates: true, auto_memory: true, context_strategies: ['recent', 'adaptive'], web: {http_fetch: true, download_url: true, web_search: false, search_backend: null, search_status: 'not_configured'}};
  f.$('chat-mode').value = 'files'; await f.$('chat-mode').fire('change');
  await submit(f); assert.equal(f.posts[0].web_enabled, true);
  f.app.state.config.features.web.search_status = 'invalid_configuration'; await f.$('chat-mode').fire('change'); f.$('chat-text').value = 'fetch without search';
  await submit(f); assert.equal(f.posts[1].web_enabled, true);
  f.app.state.config.features.web.web_search = true; await f.$('chat-mode').fire('change'); f.$('chat-text').value = 'search now available';
  await submit(f); assert.equal(f.posts[2].web_enabled, true);
  f.app.state.config.features.multiple_candidates = false; f.app.state.config.features.context_strategies = ['recent'];
  f.$('chat-mode').value = 'chat'; await f.$('chat-mode').fire('change');
  assert.equal(f.$('chat-send').disabled, true); assert.match(f.$('chat-status').textContent, /暂不支持自适应上下文/);
  f.$('chat-text').value = 'must not send'; await submit(f); assert.equal(f.posts.length, 3); assert.match(f.$('chat-status').textContent, /未开启此上下文策略/);
});

test('server-disabled web/memory are never forced on; no search backend still permits fetch/download', async t => {
  const f = await fixture(t, {v2: false});
  f.app.state.config.features = {auto_memory: false, web: {http_fetch: false, download_url: false, web_search: false}};
  f.$('chat-mode').value = 'files'; await f.$('chat-mode').fire('change');
  await submit(f); assert.equal(f.posts[0].auto_memory, false); assert.equal(f.posts[0].web_enabled, false);
  f.app.state.config.features = {web: {http_fetch: true, download_url: true, web_search: false, search_status: 'not_configured'}};
  await f.$('chat-mode').fire('change'); f.$('chat-text').value = 'fetch a page'; await submit(f);
  assert.equal(f.posts[1].web_enabled, true); assert.equal(f.posts[1].auto_memory, true);
});

test('only real model errors use status; switching to a supported mode clears that error without boilerplate', async t => {
  const f = await fixture(t);
  f.app.state.models[0].tools_enabled = false;
  f.$('chat-mode').value = 'files'; await f.$('chat-mode').fire('change');
  assert.equal(f.$('chat-send').disabled, true); assert.match(f.$('chat-status').textContent, /此模式暂无可用模型/);
  f.$('chat-mode').value = 'chat'; await f.$('chat-mode').fire('change');
  assert.equal(f.$('chat-send').disabled, false); assert.equal(f.$('chat-status').textContent, '');
  f.$('chat-status').textContent = '另一项操作尚未确认'; await f.$('chat-mode').fire('change');
  assert.equal(f.$('chat-status').textContent, '另一项操作尚未确认');
});

test('template and controller remove the settings, boilerplate and key link rather than hiding or collapsing them', () => {
  const fs = require('node:fs'), path = require('node:path');
  const html = fs.readFileSync(path.join(__dirname, '../src/new_api_cockpit/templates/chat/index.html'), 'utf8');
  assert.doesNotMatch(html, /chat-candidate-count|chat-context-strategy|chat-web-enabled|chat-auto-memory|候选回答数|主动勾选|所选上下文策略/);
  const app = fs.readFileSync(path.join(__dirname, '../src/new_api_cockpit/static/chat/app.js'), 'utf8');
  assert.doesNotMatch(html + app, /chat-cost-notice|chat-defaults-info|chat-model-info|chat-runtime-details|管理我的 KEY|能力与计费说明|发送前核对协议|辅助请求合计每轮最多/);
  assert.match(html, /id="chat-skills-open"/); assert.match(html, /id="chat-model-load"/);
  const controls = html.match(/<fieldset id="chat-selection"[^>]*>([\s\S]*?)<\/fieldset>/)[1];
  assert.doesNotMatch(controls, /<p\b|<details\b|<a\b/);
});
