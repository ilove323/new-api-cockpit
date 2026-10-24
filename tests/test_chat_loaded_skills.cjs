'use strict';
/* Synthetic frontend regression only: no backend/model/network or browser storage. */
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {randomUUID} = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const {createChatApp} = require('../src/new_api_cockpit/static/chat/app.js');
const {createAPI} = require('../src/new_api_cockpit/static/chat/api.js');
const {Scope} = require('../src/new_api_cockpit/static/chat/state.js');
const id = n => `${String(n).padStart(8, '0')}-1111-4111-8111-111111111111`;
const CID = id(1), OTHER = id(2), SKILLS = [11, 12, 13, 14].map(id), CAP = 'sha256:' + 'a'.repeat(64);
const clone = value => JSON.parse(JSON.stringify(value));
const deferred = () => { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b; }); return {promise, resolve, reject}; };
class Node {
  constructor(tag = 'div', doc) { this.tagName = tag.toUpperCase(); this.doc = doc; this.children = []; this.dataset = {}; this.attrs = {}; this.handlers = new Map(); this.value = ''; this._text = ''; this.disabled = false; this.open = false; this.classList = {toggle() {}}; }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map(child => child.textContent).join(''); }
  set innerHTML(_) { throw new Error('HTML must not be used for Skills'); }
  get firstChild() { return this.children[0]; }
  append(...nodes) { for (const node of nodes) this.children.push(...(node.tagName === '#FRAGMENT' ? node.children : [node])); }
  replaceChildren(...nodes) { this._text = ''; this.children = []; this.append(...nodes); }
  addEventListener(type, fn) { this.handlers.set(type, fn); }
  setAttribute(name, value) { this.attrs[name] = value; }
  querySelectorAll() { return []; }
  scrollIntoView() {}
  focus() { this.doc.activeElement = this; }
  showModal() { assert.equal(this.tagName, 'DIALOG'); this.open = true; }
  close() { this.open = false; }
  async fire(type, event = {preventDefault() {}}) { return this.handlers.get(type)?.(event); }
}
function surfaces(optional = 'absent') {
  const nodes = new Map(), removed = new Set(['chat-skills-open', 'chat-skills-dialog', 'chat-skills-close', 'chat-skills-status', 'chat-skills', 'chat-skills-clear', 'chat-context-refresh', 'chat-context-info', 'chat-skill-order', 'chat-skill-choices', 'chat-recover']);
  const doc = {getElementById(name) { if (optional === 'absent' && removed.has(name)) return null; if (!nodes.has(name)) nodes.set(name, new Node('div', doc)); return nodes.get(name); }, createElement: tag => new Node(tag, doc), createTextNode: text => { const node = new Node('#text', doc); node.textContent = text; return node; }, createDocumentFragment: () => new Node('#fragment', doc), documentElement: {scrollHeight: 0}};
  if (optional && optional !== 'absent') for (const [name, tag] of [['chat-skills-open', 'button'], ['chat-skills-dialog', 'dialog'], ['chat-skills-close', 'button'], ['chat-skills-status', 'p'], ['chat-dialog', 'dialog']]) nodes.set(name, new Node(tag, doc));
  doc.getElementById('chat-mode').value = 'chat'; doc.getElementById('chat-root').dataset.ownerUserId = '1';
  const win = {location: {href: 'http://offline.test/cockpit/chat/', pathname: '/cockpit/chat/'}, history: {pushState() {}, replaceState() {}}, crypto: {randomUUID}, clearTimeout() {}, setTimeout() { return 1; }, requestAnimationFrame: fn => setImmediate(fn), cancelAnimationFrame: clearImmediate, innerHeight: 800, scrollY: 0, addEventListener() {}};
  return {doc, win, $: name => doc.getElementById(name)};
}
const event = (cid, seq, type, data, rid = null) => ({v: 1, seq, type, data, run_id: rid, conversation_id: cid, at: '2026-10-16T00:00:00Z'});
async function fixture(t, {loaded = [], select = true, optional = 'absent', legacy = false} = {}) {
  const f = {...surfaces(optional), saves: [], posts: [], reads: [], replay: false, failReads: false};
  f.context = {context_revision: 5, profiles: [], skills: SKILLS.map((sid, n) => ({id: sid, name: `skill-${n + 1}`, description: 'Description', revision: 1})), limits: {skills_per_run: 3}};
  f.metas = new Map([CID, OTHER].map(cid => [cid, {id: cid, title: cid === CID ? 'current' : 'other', revision: 1, last_seq: 1, active_run_id: null, storage_state: 'ok', last_selection: {token_id: 7, model: 'fixture', mode: 'chat'}, ...(!legacy ? {loaded_skill_ids: cid === CID ? [...loaded] : []} : {})}]));
  f.events = new Map([CID, OTHER].map(cid => [cid, [event(cid, 1, 'conversation.created', {title: 'current'})]]));
  f.append = (cid, type, data, rid = null) => { const events = f.events.get(cid), meta = f.metas.get(cid), item = event(cid, events.length + 1, type, data, rid); events.push(item); meta.revision = meta.last_seq = item.seq; return item; };
  f.commit = (cid, ids) => { f.metas.get(cid).loaded_skill_ids = [...ids]; f.append(cid, 'conversation.skills.updated', {skill_ids: [...ids]}); return {conversation: clone(f.metas.get(cid))}; };
  f.api = {
    config: async () => ({modes: ['chat', 'files'], allowed_file_extensions: ['.txt'], limits: {user_text_bytes: 32768, attachments_per_turn: 8, event_page_max: 2}}),
    tokens: async () => ({tokens: [{id: 7, usable: true}]}), context: async () => { f.reads.push('context'); return clone(f.context); },
    conversations: async () => ({conversations: [...f.metas.values()].map(clone), total: 2, has_more: false, next_cursor: null}),
    conversation: async cid => { f.reads.push('conversation'); if (f.failReads) throw new Error('read offline'); return {conversation: clone(f.metas.get(cid))}; },
    events: async (cid, after, until, limit) => { f.reads.push('events'); if (f.eventsHook) return f.eventsHook(cid, after, until, limit); const events = f.events.get(cid), high = until ?? events.length, page = events.filter(e => e.seq > after && e.seq <= high).slice(0, limit), next = page.at(-1)?.seq ?? after; return {events: clone(page), until_seq: high, next_after_seq: next, has_more: next < high}; },
    files: async () => ({files: []}), run: async (_cid, rid) => ({run: {id: rid, status: 'completed'}}),
    models: async () => ({models: [{id: 'fixture', chat_enabled: true, tools_enabled: true, capability_revision: CAP}]}),
    setSkills: async (cid, ids, revision, contextRevision, options) => { f.saves.push({cid, ids: [...ids], revision, contextRevision, options}); return f.saveHook ? f.saveHook(cid, ids, revision, contextRevision) : f.commit(cid, ids); },
    start: async (cid, body, options) => { f.posts.push(clone(body)); if (f.replay) return {replayed: true}; const rid = randomUUID(), mid = randomUUID(); const accepted = f.append(cid, 'run.accepted', {...body, message_id: mid, context_snapshot: {context_revision: 5, skill_ids: [...body.skill_ids]}}, rid); options.onEvent(accepted); if (f.acceptHook) await f.acceptHook(); options.onEvent(f.append(cid, 'assistant.message', {message_id: mid, text: 'answer', complete: true, tool_calls: [], finish_reason: 'stop'}, rid)); options.onEvent(f.append(cid, 'run.terminal', {status: 'completed'}, rid)); return {terminal: true}; },
  };
  f.app = createChatApp({window: f.win, document: f.doc, api: f.api}); t.after(() => f.app.clearAll()); await f.app.ready;
  if (select) { await f.app.openConversation(CID); f.$('chat-token').value = '7'; await f.$('chat-model-load').fire('click'); f.$('chat-text').value = 'question'; }
  return f;
}

test('chat template removes Skills controls instead of keeping a hidden or collapsed entry', () => {
  const root = path.join(__dirname, '../src/new_api_cockpit'), html = fs.readFileSync(path.join(root, 'templates/chat/index.html'), 'utf8');
  assert.doesNotMatch(html, /id="chat-skills|chat-skill-choices|chat-skill-order|id="chat-context-refresh"|id="chat-recover"/);
  assert.match(html, /href="\/cockpit\/chat\/settings\/"/);
  const source = fs.readFileSync(path.join(root, 'static/chat/app.js'), 'utf8'); assert.doesNotMatch(source, /sessionStorage|indexedDB|localStorage\.(?:setItem|removeItem)/);
  assert.doesNotMatch(source, /skillsUI|showSkill|saveSkills|chat-dialog|chat-context-refresh/);
  assert.match(source, /createSelectionCache/); // Only account-scoped selector preferences may persist; never Skills or content.
});

test('absent toolbar and library nodes do not break restore, draft recovery, sending or account cleanup', async t => {
  const f = await fixture(t, {optional: 'absent', loaded: [SKILLS[1], SKILLS[0]]});
  assert.equal(f.$('chat-skills-open'), null); assert.equal(f.$('chat-recover'), null);
  assert.equal(f.$('chat-send').disabled, false);
  f.$('chat-text').value = 'unsent draft'; await f.app.recover();
  assert.equal(f.$('chat-text').value, 'unsent draft'); assert.deepEqual(f.app.state.skills, [SKILLS[1], SKILLS[0]]);
  await f.app.send({preventDefault() {}});
  assert.deepEqual(f.posts[0].skill_ids, [SKILLS[1], SKILLS[0]]); assert.equal(f.posts[0].auto_memory, true); assert.equal(f.posts[0].web_enabled, true);
  assert.deepEqual(f.app.state.skills, [SKILLS[1], SKILLS[0]]); assert.equal(f.saves.length, 0);
  await f.app.openConversation(OTHER); assert.deepEqual(f.app.state.skills, []);
  await f.app.openConversation(CID); assert.deepEqual(f.app.state.skills, [SKILLS[1], SKILLS[0]]);
  f.app.clearAll(); assert.deepEqual(f.app.state.skills, []); assert.equal(f.app.state.context, null); assert.equal(f.$('chat-send').disabled, true);
});

test('setSkills sends exactly one scoped PATCH with independent ordered ids and no title/credentials/model request', async () => {
  const scope = new Scope(); scope.switch(CID); const calls = [];
  const api = createAPI({scope, fetch: async (url, init) => { calls.push({url, init}); return new Response(JSON.stringify({conversation: {id: CID, loaded_skill_ids: [SKILLS[1], SKILLS[0]]}})); }});
  const ids = [SKILLS[1], SKILLS[0]], result = await api.setSkills(CID, ids, 8, 5); ids.reverse();
  assert.equal(calls.length, 1); assert.deepEqual(result.conversation.loaded_skill_ids, [SKILLS[1], SKILLS[0]]);
  assert.equal(calls[0].url, `/cockpit/api/chat/conversations/${CID}`); const init = calls[0].init;
  assert.equal(init.method, 'PATCH'); assert.equal(init.credentials, 'same-origin'); assert.equal(init.headers['X-Cockpit-Chat'], '1'); assert.equal('Authorization' in init.headers, false);
  assert.deepEqual(JSON.parse(init.body), {expected_revision: 8, context_revision: 5, loaded_skill_ids: [SKILLS[1], SKILLS[0]]});
  for (const bad of [[null, 1, 5], [[SKILLS[0], SKILLS[0]], 1, 5], [SKILLS, 1, 5], [['bad'], 1, 5], [[], -1, 5], [[], true, 5], [[], 1, '5']]) assert.throws(() => api.setSkills(CID, ...bad));
  assert.equal(calls.length, 1);
});

test('legacy metadata defaults to no loaded Skills and optional fake nodes are never opened as dialogs', async t => {
  const f = await fixture(t, {optional: false, legacy: true}); assert.deepEqual(f.app.state.skills, []);
  assert.equal(f.$('chat-skills-open').handlers.has('click'), false); await f.app.send({preventDefault() {}}); assert.deepEqual(f.posts[0].skill_ids, []);
});

test('opening and refreshing a chat restores ordered loaded ids; personal context refresh does not clear them', async t => {
  const f = await fixture(t, {loaded: [SKILLS[1], SKILLS[0]]}); assert.deepEqual(f.app.state.skills, [SKILLS[1], SKILLS[0]]);
  await f.app.recover();
  assert.deepEqual(f.app.state.skills, [SKILLS[1], SKILLS[0]]); assert.equal(f.saves.length, 0);
});

test('run.accepted and terminal recovery retain loaded ids and freeze the existing run skill_ids DTO', async t => {
  const f = await fixture(t, {loaded: [SKILLS[0]]}), hold = deferred(); f.acceptHook = () => hold.promise;
  const pending = f.app.send({preventDefault() {}}); await Promise.resolve(); assert.deepEqual(f.app.state.skills, [SKILLS[0]]);
  hold.resolve(); await pending; assert.deepEqual(f.app.state.skills, [SKILLS[0]]); assert.deepEqual(f.posts[0].skill_ids, [SKILLS[0]]); assert.equal(f.saves.length, 0);
});

test('idempotent replay and personal Memory auto-refresh preserve chat-loaded Skills', async t => {
  const f = await fixture(t, {loaded: [SKILLS[0]]}); f.replay = true; await f.app.send({preventDefault() {}});
  assert.deepEqual(f.app.state.skills, [SKILLS[0]]); f.context.context_revision = 6;
  const rid = randomUUID(); f.append(CID, 'run.accepted', {text: 'old', context_snapshot: {context_revision: 5, skill_ids: [SKILLS[0]]}}, rid);
  f.append(CID, 'run.terminal', {status: 'completed'}, rid); f.append(CID, 'memory.reverted', {context_revision: 6}, rid);
  await f.app.recover(); assert.equal(f.app.state.context.context_revision, 6); assert.deepEqual(f.app.state.skills, [SKILLS[0]]);
});

test('malformed metadata loaded ids leaves history failed and cannot unlock send', async t => {
  const f = await fixture(t); f.metas.get(CID).loaded_skill_ids = [SKILLS[0], SKILLS[0]]; await f.app.recover(); assert.equal(f.app.state.historyStatus, 'failed'); assert.equal(f.$('chat-send').disabled, true); await f.app.send({preventDefault() {}}); assert.equal(f.posts.length, 0);
});

test('pending pagination cannot enable Skills writes or let an older skills event replace latest metadata ids', async t => {
  const f = await fixture(t), hold = deferred(), reached = deferred();
  f.commit(CID, [SKILLS[0]]); f.commit(CID, [SKILLS[1]]); f.append(CID, 'conversation.renamed', {title: 'latest'});
  const original = f.api.events;
  f.api.events = async (cid, after, until, limit) => { if (after >= 2) { reached.resolve(); await hold.promise; } return original(cid, after, until, 1); };
  const pending = f.app.recover(); await reached.promise;
  assert.equal(f.app.state.meta.revision, 4); assert.deepEqual(f.app.state.skills, [SKILLS[1]], 'seq2 cannot downgrade rev4 metadata');
  assert.equal(f.app.state.historyStatus, 'loading'); assert.equal(f.$('chat-send').disabled, true);
  await f.app.send({preventDefault() {}});
  assert.equal(f.saves.length, 0); assert.equal(f.posts.length, 0); assert.deepEqual(f.app.state.skills, [SKILLS[1]]);
  hold.resolve(); await pending; assert.equal(f.app.state.historyStatus, 'ready'); assert.deepEqual(f.app.state.skills, [SKILLS[1]]); assert.equal(f.$('chat-send').disabled, false);
});

test('Memory undo reads/updates personal context without clearing the chat-loaded Skill selection', async t => {
  const f = await fixture(t, {loaded: [SKILLS[0]]}), rid = randomUUID(), before = {content: 'old', sha256: 'b'.repeat(64), context_revision: 4, memory_revision: 1}, after = {content: 'new', sha256: 'c'.repeat(64), context_revision: 5, memory_revision: 2};
  f.context.profiles = [{name: 'MEMORY.md', revision: 2, content: 'new', sha256: after.sha256}];
  const audit = {v: 1, kind: 'auto_memory_update', profile: 'MEMORY.md', owner_user_id: 1, changed: true, before, after, source_ids: [randomUUID()], fact_ids: ['fact_test']};
  f.append(CID, 'run.accepted', {text: 'old question', context_snapshot: {context_revision: 4, skill_ids: [SKILLS[0]]}}, rid); f.append(CID, 'memory.updated', {audit}, rid); f.append(CID, 'run.terminal', {status: 'completed'}, rid);
  let undos = 0; f.api.undoMemory = async () => { undos++; f.context.context_revision = 6; f.context.profiles[0] = {...f.context.profiles[0], revision: 3, content: 'old', sha256: before.sha256}; f.append(CID, 'memory.reverted', {context_revision: 6}, rid); return {context: clone(f.context), conversation: clone(f.metas.get(CID))}; };
  await f.app.recover(); await f.app.undoMemory(rid); assert.equal(undos, 1); assert.deepEqual(f.app.state.skills, [SKILLS[0]]); assert.equal(f.app.state.context.context_revision, 6); assert.equal(f.saves.length, 0); assert.equal(f.posts.length, 0);
});

test('legacy missing loaded ids is allowed but an explicit null metadata field is not silently cleared', async t => {
  const f = await fixture(t, {loaded: [SKILLS[0]]}); f.metas.get(CID).loaded_skill_ids = null; await f.app.recover(); assert.equal(f.app.state.historyStatus, 'failed'); assert.deepEqual(f.app.state.skills, [SKILLS[0]]); assert.equal(f.$('chat-send').disabled, true);
});

test('unavailable loaded Skills cannot silently disappear from a subsequent run', async t => {
  const missing = id(99), f = await fixture(t, {loaded: [missing]});
  assert.equal(f.$('chat-send').disabled, true);
  await f.app.send({preventDefault() {}});
  assert.equal(f.posts.length, 0); assert.deepEqual(f.app.state.skills, [missing]);
  assert.match(f.$('chat-status').textContent, /个人设置/);
});
