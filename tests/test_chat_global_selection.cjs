'use strict';
/* Synthetic selector metadata only. No network, credentials or real user data. */
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {randomUUID} = require('node:crypto');
const {createChatApp} = require('../src/new_api_cockpit/static/chat/app.js');
const {emptySelection, selectionStorageKey, normalizeSelection, createSelectionCache} = require('../src/new_api_cockpit/static/chat/selection.js');
const CID = randomUUID(), OTHER = randomUUID(), CAP = 'sha256:' + 'a'.repeat(64);
const clone = value => JSON.parse(JSON.stringify(value));
const preference = (patch = {}) => ({v: 1, token_id: 7, model: 'model-a', mode: 'files', ...patch});
const deferred = () => { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b; }); return {promise, resolve, reject}; };
const tick = () => new Promise(resolve => setImmediate(resolve));
function storage() {
  const values = new Map();
  return {values, getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, String(value)), removeItem: key => values.delete(key)};
}
function cache(target, ownerUserId = 1) { return createSelectionCache({ownerUserId, storage: () => target}); }

test('cache stores exactly the four selector metadata fields and round-trips by account', () => {
  const target = storage(), c = cache(target); assert.equal(c.write(preference()), true);
  assert.deepEqual(JSON.parse(target.getItem(c.key)), preference()); assert.deepEqual(c.read(), preference());
  assert.deepEqual(cache(target, 2).read(), emptySelection()); assert.notEqual(c.key, cache(target, 2).key);
  assert.deepEqual([...target.values.keys()], [selectionStorageKey(1)]);
});
for (const [name, value] of Object.entries({array: [], missing: {}, version: preference({v: 2}), unknown: {...preference(), credential: 'must-not-store'}, bool: preference({token_id: true}), negative: preference({token_id: -1}), huge: preference({token_id: 2147483648}), nullTokenModel: preference({token_id: null}), longModel: preference({model: '中'.repeat(86)}), control: preference({model: 'model\nname'}), wrongMode: preference({mode: 'agent'})})) {
  test(`cache rejects invalid ${name} without storing it`, () => {
    const target = storage(); assert.equal(normalizeSelection(value), null); assert.equal(cache(target).write(value), false); assert.equal(target.values.size, 0);
  });
}
test('cache ignores malformed/oversized JSON and survives blocked storage', () => {
  const target = storage(), c = cache(target);
  for (const raw of ['{invalid', 'x'.repeat(2049), 'null', JSON.stringify({...preference(), text: 'private message'})]) { target.setItem(c.key, raw); assert.deepEqual(c.read(), emptySelection()); }
  const blocked = createSelectionCache({ownerUserId: 1, storage() { throw new Error('blocked'); }});
  assert.deepEqual(blocked.read(), emptySelection()); assert.equal(blocked.write(preference()), false);
  for (const ownerUserId of [0, -1, NaN, '1', 2147483648]) {
    const invalid = createSelectionCache({ownerUserId, storage() { assert.fail('Invalid owner must not access browser storage'); }});
    assert.deepEqual(invalid.read(), emptySelection()); assert.equal(invalid.write(preference()), false);
  }
});

class Node {
  constructor(tag = 'div') { this.tagName = tag.toUpperCase(); this.value = ''; this.children = []; this.dataset = {}; this.attrs = {}; this.handlers = new Map(); this.disabled = false; this._text = ''; this.classList = {toggle() {}}; }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map(n => n.textContent).join(''); }
  get firstChild() { return this.children[0]; }
  append(...nodes) { for (const n of nodes) this.children.push(...(n.tagName === '#FRAGMENT' ? n.children : [n])); }
  replaceChildren(...nodes) { this._text = ''; this.children = []; this.append(...nodes); }
  addEventListener(name, fn) { this.handlers.set(name, fn); }
  setAttribute(name, value) { this.attrs[name] = value; }
  querySelectorAll() { return []; }
  scrollIntoView() {}
  close() { this.open = false; }
  fire(name) { return this.handlers.get(name)?.({preventDefault() {}}); }
}
async function fixture(t, {target = storage(), owner = 1, seed, models, tokens, modelHook, createHook, uploadHook, configHook, ready = true, snapshot = false} = {}) {
  if (seed) cache(target, owner).write(seed);
  const nodes = new Map(), handlers = new Map();
  const doc = {getElementById(id) { if (!nodes.has(id)) nodes.set(id, new Node()); return nodes.get(id); }, createElement: tag => new Node(tag), createTextNode: text => { const n = new Node('#text'); n.textContent = text; return n; }, createDocumentFragment: () => new Node('#fragment'), documentElement: {scrollHeight: 0}};
  const $ = id => doc.getElementById(id); $('chat-root').dataset.ownerUserId = String(owner);
  const win = {localStorage: target, location: {href: 'http://offline.test/cockpit/chat/', pathname: '/cockpit/chat/'}, history: {pushState() {}, replaceState() {}}, crypto: {randomUUID}, clearTimeout() {}, setTimeout() {}, requestAnimationFrame: fn => setImmediate(fn), cancelAnimationFrame: clearImmediate, innerHeight: 900, scrollY: 0, addEventListener: (name, fn) => handlers.set(name, fn), confirm: () => true};
  const model = (id, patch = {}) => ({id, chat_enabled: true, tools_enabled: true, capability_revision: CAP, ...patch});
  const f = {target, doc, win, $, handlers, modelsCalls: [], starts: [], creates: [], uploads: [], files: new Map(), rows: new Map(), events: new Map()};
  const event = (cid, type, data, rid = null) => { const events = f.events.get(cid), row = f.rows.get(cid), e = {v: 1, seq: events.length + 1, conversation_id: cid, run_id: rid, at: '2026-10-16T00:00:00Z', type, data}; events.push(e); row.revision = row.last_seq = e.seq; return e; };
  const addChat = cid => { f.rows.set(cid, {id: cid, title: 'synthetic chat', revision: 0, last_seq: 0, storage_state: 'ok', active_run_id: null, loaded_skill_ids: [], last_selection: {token_id: 8, model: 'historical-model', mode: 'chat'}}); f.events.set(cid, []); f.files.set(cid, []); event(cid, 'conversation.created', {title: 'synthetic chat'}); };
  addChat(CID); addChat(OTHER);
  f.api = {
    config: async () => { if (configHook) await configHook(); return {features: {recovery_snapshot: snapshot}, modes: ['chat', 'files'], allowed_file_extensions: ['.txt'], limits: {user_text_bytes: 32768, attachments_per_turn: 8, event_page_max: 200, upload_file_bytes: 50 * 1048576}}; },
    context: async () => ({context_revision: 0, skills: [], limits: {skills_per_run: 3}}),
    tokens: async () => ({tokens: tokens || [{id: 7, usable: true, name: 'private-key-label', key_masked: 'never-cache-this'}, {id: 8, usable: true}]}),
    models: async (tokenId, options) => { f.modelsCalls.push({tokenId, ticket: clone(options.ticket)}); return modelHook ? modelHook(tokenId, options) : {models: models || [model('model-a'), model('model-b')]}; },
    conversations: async () => ({conversations: [...f.rows.values()].map(clone), total: f.rows.size, has_more: false, next_cursor: null}),
    conversation: async cid => ({conversation: clone(f.rows.get(cid))}),
    create: async cid => { f.creates.push(cid); if (createHook) await createHook(); addChat(cid); return {conversation: clone(f.rows.get(cid))}; },
    upload: async (cid, file, fid, options) => { f.uploads.push({cid, file, fid, ticket: clone(options.ticket)}); if (uploadHook) await uploadHook(); const entry = {id: fid, name: file.name, size_bytes: file.size, kind: 'upload', version: null}; f.files.get(cid).push(entry); return {file: entry}; },
    remove: async cid => { f.rows.delete(cid); f.events.delete(cid); },
    events: async (cid, after, until) => { const items = f.events.get(cid), high = until ?? items.length; return {events: clone(items.filter(e => e.seq > after && e.seq <= high)), until_seq: high, next_after_seq: high, has_more: false}; },
    files: async cid => ({files: clone(f.files.get(cid))}), run: async (_cid, rid) => ({run: {id: rid, status: 'completed'}}),
    start: async (cid, body, options) => { f.starts.push(clone(body)); const rid = randomUUID(); options.onEvent(event(cid, 'run.accepted', {...body, message_id: randomUUID(), context_snapshot: {context_revision: 0, skill_ids: []}}, rid)); options.onEvent(event(cid, 'run.terminal', {status: 'completed'}, rid)); return {terminal: true}; },
  };
  f.syncCalls = [];
  f.api.sync = async (cid, after, _limit, filesRevision) => {
    f.syncCalls.push({cid, after, filesRevision});
    const events = f.events.get(cid), high = events.length, rid = events.findLast(e => e.run_id)?.run_id;
    return {conversation: clone(f.rows.get(cid)), events: {events: clone(events.filter(e => e.seq > after)), until_seq: high, next_after_seq: high, has_more: false}, run: rid ? {id: rid, status: 'completed'} : null,
      files_revision: 'a'.repeat(64), files: filesRevision ? null : clone(f.files.get(cid)), context: null};
  };
  f.app = createChatApp({window: win, document: doc, api: f.api}); t.after(() => f.app.clearAll());
  if (ready) await f.app.ready;
  f.select = async (tokenId = 7, modelId = 'model-a', mode = 'files') => { $('chat-token').value = String(tokenId); await $('chat-token').fire('change'); await $('chat-model-load').fire('click'); $('chat-model').value = modelId; await $('chat-model').fire('change'); };
  f.send = async () => { $('chat-text').value = 'private synthetic message'; await f.app.send({preventDefault() {}}); };
  return f;
}

test('advertised recovery snapshot replaces four requests and preserves draft plus unchanged files', async t => {
  const f = await fixture(t, {snapshot: true});
  for (const name of ['conversation', 'events', 'run', 'files']) f.api[name] = async () => assert.fail('legacy recovery request: ' + name);
  await f.app.openConversation(CID); f.$('chat-text').value = 'unsent draft';
  await f.app.recover();
  assert.equal(f.syncCalls.length, 2);
  assert.equal(f.syncCalls[1].after, 1); assert.equal(f.syncCalls[1].filesRevision, 'a'.repeat(64));
  assert.equal(f.$('chat-text').value, 'unsent draft'); assert.equal(f.app.state.historyStatus, 'ready');
});

test('failed active recovery backs off and cannot poll before Retry-After', async t => {
  const f = await fixture(t, {snapshot: true}); await f.app.openConversation(CID);
  f.app.state.meta.active_run_id = randomUUID(); f.$('chat-text').value = 'keep this draft';
  const timers = []; f.win.setTimeout = (callback, delay) => { timers.push({callback, delay}); return timers.length; };
  let reads = 0;
  f.api.sync = async () => { reads++; throw Object.assign(new Error('rate limited'), {retryAfterMs: 20000}); };
  await f.app.recover();
  assert.equal(reads, 1); assert.ok(timers.at(-1).delay >= 19000);
  timers.at(-1).callback();
  assert.equal(reads, 1, 'an early timer must only reschedule, not call the server');
  assert.equal(f.$('chat-text').value, 'keep this draft'); assert.equal(f.starts.length, 0);
});

test('conversation last_selection never chooses or overrides the global selectors', async t => {
  const f = await fixture(t); await f.app.openConversation(CID);
  assert.equal(f.$('chat-token').value, ''); assert.equal(f.modelsCalls.length, 0);
  await f.select(); await f.app.openConversation(OTHER);
  assert.equal(f.$('chat-token').value, '7'); assert.equal(f.$('chat-model').value, 'model-a'); assert.equal(f.modelsCalls.length, 1);
});
test('new/switch/delete/back-to-no-chat retain global selectors and in-memory catalog', async t => {
  const f = await fixture(t); await f.select(); const catalog = f.app.state.models;
  await f.app.openConversation(CID); await f.app.openConversation(OTHER); await f.$('chat-new').fire('click'); await f.$('chat-delete').fire('click');
  await f.handlers.get('popstate')();
  assert.equal(f.$('chat-token').value, '7'); assert.equal(f.$('chat-model').value, 'model-a');
  assert.equal(f.app.state.models, catalog); assert.equal(f.modelsCalls.length, 1); assert.equal(f.starts.length, 0);
});
test('reload restores choices only after owned token and fresh model capabilities are validated', async t => {
  const target = storage(), first = await fixture(t, {target}); await first.select(7, 'model-b', 'files'); first.app.clearAll();
  const fresh = 'sha256:' + 'b'.repeat(64), f = await fixture(t, {target, models: [{id: 'model-b', chat_enabled: true, tools_enabled: true, capability_revision: fresh}]});
  assert.equal(f.$('chat-token').value, '7'); assert.equal(f.$('chat-model').value, 'model-b');
  assert.equal(f.modelsCalls.length, 1); assert.equal(f.modelsCalls[0].ticket.view, null); assert.equal(f.modelsCalls[0].ticket.cid, null); assert.equal(f.starts.length, 0);
  await f.app.openConversation(CID); await f.send(); assert.equal(f.starts[0].capability_revision, fresh); assert.equal(f.starts[0].web_enabled, true);
});
test('another account cannot inherit the cached KEY/model/mode; logout retains only the original preference record', async t => {
  const target = storage(), first = await fixture(t, {target, seed: preference()}); first.app.clearAll();
  assert.deepEqual(cache(target).read(), preference()); assert.equal(first.app.state.models.length, 0); assert.equal(first.$('chat-token').value, '');
  const second = await fixture(t, {target, owner: 2}); assert.equal(second.$('chat-token').value, ''); assert.equal(second.modelsCalls.length, 0);
});
test('deleted/unusable cached token is cleared without silently choosing another KEY', async t => {
  for (const tokens of [[{id: 8, usable: true}], [{id: 7, usable: false}, {id: 8, usable: true}]]) {
    const f = await fixture(t, {seed: preference(), tokens}); assert.equal(f.$('chat-token').value, ''); assert.equal(f.modelsCalls.length, 0);
    assert.deepEqual(cache(f.target).read(), preference({token_id: null, model: ''})); await f.app.openConversation(CID); assert.equal(f.$('chat-send').disabled, true);
  }
});
test('missing cached model does not silently fall back; explicit replacement is cached', async t => {
  const f = await fixture(t, {seed: preference({model: 'deleted-model'})}); await f.app.openConversation(CID);
  assert.equal(f.$('chat-model').value, ''); assert.equal(f.$('chat-send').disabled, true); await f.send(); assert.equal(f.starts.length, 0);
  f.$('chat-model').value = 'model-b'; await f.$('chat-model').fire('change');
  assert.equal(f.$('chat-send').disabled, false); assert.equal(cache(f.target).read().model, 'model-b');
});
test('cached mode is ignored by the default full assistant and missing tool metadata cannot disable its model', async t => {
  const f = await fixture(t, {seed: preference(), models: [{id: 'model-a', chat_enabled: true, tools_enabled: false, capability_revision: CAP}]}); await f.app.openConversation(CID);
  assert.equal(f.$('chat-model').value, 'model-a'); assert.equal(f.$('chat-send').disabled, false);
  assert.equal(cache(f.target).read().mode, 'files'); assert.equal(f.starts.length, 0);
});
test('global catalog restoration survives conversation switching but never generates', async t => {
  const gate = deferred(), f = await fixture(t, {seed: preference(), modelHook: () => gate.promise, ready: false}); await tick();
  assert.equal(f.modelsCalls.length, 1); await f.app.openConversation(CID); await f.app.openConversation(OTHER); assert.equal(f.$('chat-send').disabled, true);
  gate.resolve({models: [{id: 'model-a', chat_enabled: true, tools_enabled: true, capability_revision: CAP}]}); await f.app.ready;
  assert.equal(f.$('chat-model').value, 'model-a'); assert.equal(f.$('chat-send').disabled, false); assert.equal(f.starts.length, 0);
});
test('an auth change drops late catalog responses without rewriting saved preferences', async t => {
  const gate = deferred(), f = await fixture(t, {seed: preference(), modelHook: () => gate.promise, ready: false}); await tick(); f.app.clearAll();
  const before = f.target.getItem(selectionStorageKey(1)); gate.resolve({models: [{id: 'late-secret-label', chat_enabled: true, tools_enabled: true}]}); await f.app.ready;
  assert.equal(f.app.state.models.length, 0); assert.equal(f.$('chat-model').children.length, 0); assert.equal(f.target.getItem(selectionStorageKey(1)), before); assert.equal(f.starts.length, 0);
});
test('old KEY catalog cannot replace a newer KEY choice and direct send rejects mismatched catalogs', async t => {
  const gate = deferred(), f = await fixture(t, {seed: preference(), modelHook: id => id === 7 ? gate.promise : {models: [{id: 'new-key-model', chat_enabled: true, tools_enabled: true, capability_revision: CAP}]}, ready: false}); await tick();
  f.$('chat-token').value = '8'; await f.$('chat-token').fire('change'); await f.$('chat-model-load').fire('click');
  gate.resolve({models: [{id: 'old-key-model', chat_enabled: true, tools_enabled: true, capability_revision: CAP}]}); await f.app.ready;
  assert.equal(f.$('chat-model').value, 'new-key-model'); assert.equal(f.app.state.modelsTokenId, 8); assert.equal(cache(f.target).read().token_id, 8);
  await f.app.openConversation(CID); f.$('chat-token').value = '7'; await f.send(); assert.equal(f.starts.length, 0); assert.match(f.$('chat-status').textContent, /当前 KEY 的模型目录/);
});
test('storage denied does not break selection or conversation switching', async t => {
  const target = {getItem() { throw new Error('denied'); }, setItem() { throw new Error('quota'); }}, f = await fixture(t, {target});
  await f.select(); await f.app.openConversation(CID); await f.app.openConversation(OTHER); await f.send(); assert.equal(f.starts.length, 1);
});
test('failed catalog restoration stops loading and never retries or generates', async t => {
  const f = await fixture(t, {seed: preference(), modelHook: async () => { throw new Error('permission revoked'); }}); await f.app.openConversation(CID);
  assert.equal(f.modelsCalls.length, 1); assert.equal(f.starts.length, 0); assert.equal(f.app.state.modelsLoading, false); assert.equal(f.$('chat-send').disabled, true);
  assert.doesNotMatch(f.$('chat-model').textContent, /正在载入/); await f.app.recover(); assert.equal(f.modelsCalls.length, 1);
});
test('late failure from an old KEY cannot report an error for the new KEY', async t => {
  const gate = deferred(), f = await fixture(t, {seed: preference(), ready: false, modelHook: id => id === 7 ? gate.promise : {models: [{id: 'new-model', chat_enabled: true, tools_enabled: true, capability_revision: CAP}]}}); await tick();
  f.$('chat-token').value = '8'; await f.$('chat-token').fire('change'); await f.$('chat-model-load').fire('click'); gate.reject(new Error('old-key failure')); await f.app.ready;
  assert.equal(f.$('chat-model').value, 'new-model'); assert.doesNotMatch(f.$('chat-status').textContent, /old-key failure/); assert.equal(cache(f.target).read().token_id, 8);
});
test('a successful explicit catalogue reload clears the old load error without sending', async t => {
  let attempts = 0;
  const f = await fixture(t, {seed: preference(), modelHook: async () => {
    if (++attempts === 1) throw new Error('New API 请求过于频繁，请稍后再试。');
    return {models: [{id: 'model-a', chat_enabled: true, tools_enabled: true, capability_revision: CAP}]};
  }});
  assert.match(f.$('chat-status').textContent, /模型目录未载入/);
  assert.equal(f.$('chat-send').disabled, true);
  await f.$('chat-model-load').fire('click');
  assert.equal(f.$('chat-model').value, 'model-a');
  assert.doesNotMatch(f.$('chat-status').textContent, /模型目录未载入|频繁/);
  assert.equal(f.$('chat-send').disabled, false); assert.equal(f.starts.length, 0);
});
test('cached metadata never includes catalog, capabilities, labels, drafts, messages, Skills or personal context', async t => {
  const f = await fixture(t); await f.select(); await f.app.openConversation(CID); f.$('chat-text').value = 'private-unsent-draft'; f.app.state.context.profiles = [{content: 'private-memory-content'}];
  assert.deepEqual(JSON.parse(f.target.getItem(selectionStorageKey(1))), preference({mode: 'files'}));
  assert.doesNotMatch(f.target.getItem(selectionStorageKey(1)), /private|sha256|catalog|capability|Skills|content|label|never-cache/);
});
test('global selector is outside the conversation layout and its cache script is bundled before the app', () => {
  const root = path.join(__dirname, '../src/new_api_cockpit'), html = fs.readFileSync(path.join(root, 'templates/chat/index.html'), 'utf8'), base = fs.readFileSync(path.join(root, 'templates/chat/_base.html'), 'utf8');
  assert(html.indexOf('id="chat-selection"') < html.indexOf('<div class="chat-layout">'));
  assert.equal((html.match(/id="chat-selection"/g) || []).length, 1); assert.doesNotMatch(html.split('<section class="page-panel chat-main"')[1], /id="chat-selection"/);
  assert(base.indexOf('/chat/selection.js') > base.indexOf('/chat/state.js')); assert(base.indexOf('/chat/selection.js') < base.indexOf('{% block scripts %}'));
});

test('file picker works on the empty page, creates one chat and uploads without any model call', async t => {
  const f = await fixture(t);
  assert.equal(f.$('chat-upload').disabled, false); assert.equal(f.$('chat-attach').disabled, false); assert.equal(f.$('chat-upload-hint').hidden, true);
  assert.match(f.$('chat-attach').title, /50 MB/); assert.doesNotMatch(f.$('chat-attach').title, /52428800/);
  const file = {name: 'source.txt', size: 50 * 1048576}; f.$('chat-upload').files = [file]; await f.$('chat-upload').fire('change');
  assert.equal(f.creates.length, 1); assert.equal(f.uploads.length, 1); assert.equal(f.uploads[0].cid, f.creates[0]); assert.equal(f.uploads[0].file, file);
  assert.equal(f.app.scope.cid, f.creates[0]); assert.equal(f.uploads[0].ticket.cid, f.creates[0]); assert.equal(f.starts.length, 0); assert.equal(f.modelsCalls.length, 0);
  assert.deepEqual(f.app.state.attachments, [f.uploads[0].fid]); assert.match(f.$('chat-attachments').textContent, /source.txt/); assert.doesNotMatch(f.$('chat-files-list').textContent, /source.txt/);
});
test('upload to an existing chat never creates a different chat', async t => {
  const f = await fixture(t); await f.app.openConversation(CID); f.$('chat-upload').files = [{name: 'source.txt', size: 3}]; await f.$('chat-upload').fire('change');
  assert.equal(f.creates.length, 0); assert.equal(f.uploads.length, 1); assert.equal(f.uploads[0].cid, CID);
});
test('over-50-MB and unsupported files do not create an empty chat or send an upload', async t => {
  for (const file of [{name: 'source.txt', size: 50 * 1048576 + 1}, {name: 'source.exe', size: 2}]) {
    const f = await fixture(t); f.$('chat-upload').files = [file]; await f.$('chat-upload').fire('change');
    assert.equal(f.creates.length, 0); assert.equal(f.uploads.length, 0); assert.equal(f.starts.length, 0); assert.ok(f.$('chat-status').textContent);
  }
});
test('pending auto-create disables the picker and cannot create duplicate chats/uploads', async t => {
  const gate = deferred(), f = await fixture(t, {createHook: () => gate.promise}); f.$('chat-upload').files = [{name: 'source.txt', size: 2}];
  const first = f.$('chat-upload').fire('change'); await tick(); assert.equal(f.$('chat-upload').disabled, true); await f.$('chat-upload').fire('change');
  assert.equal(f.creates.length, 1); gate.resolve(); await first; assert.equal(f.uploads.length, 1); assert.equal(f.$('chat-upload').disabled, false);
});
test('switching chat or logging out during auto-create never uploads into the wrong identity/chat', async t => {
  for (const action of ['switch', 'logout']) {
    const gate = deferred(), f = await fixture(t, {createHook: () => gate.promise}); f.$('chat-upload').files = [{name: 'source.txt', size: 2}];
    const pending = f.$('chat-upload').fire('change'); await tick();
    if (action === 'switch') await f.app.openConversation(OTHER); else f.app.clearAll();
    gate.resolve(); await pending; assert.equal(f.uploads.length, 0); assert.equal(f.starts.length, 0);
    if (action === 'switch') assert.equal(f.app.scope.cid, OTHER); else assert.equal(f.$('chat-upload').disabled, true);
  }
});
test('failed auto-create is not retried and never uploads to a null conversation', async t => {
  const f = await fixture(t, {createHook: async () => { throw new Error('synthetic uncertain create'); }}); f.$('chat-upload').files = [{name: 'source.txt', size: 2}]; await f.$('chat-upload').fire('change');
  assert.equal(f.creates.length, 1); assert.equal(f.uploads.length, 0); assert.match(f.$('chat-status').textContent, /未自动重试/); assert.equal(f.$('chat-upload').disabled, false);
});
test('config loading, active runs and corrupt chat keep upload disabled with an explicit reason', async t => {
  const gate = deferred(), f = await fixture(t, {configHook: () => gate.promise, ready: false});
  assert.equal(f.$('chat-upload').disabled, true); assert.match(f.$('chat-upload-hint').textContent, /配置/); gate.resolve(); await f.app.ready;
  f.rows.get(CID).active_run_id = randomUUID(); f.api.run = async (_cid, rid) => ({run: {id: rid, status: 'running'}}); await f.app.openConversation(CID);
  assert.equal(f.$('chat-upload').disabled, true); assert.match(f.$('chat-upload-hint').textContent, /正在处理/);
  f.rows.get(OTHER).storage_state = 'corrupt'; await f.app.openConversation(OTHER); assert.equal(f.$('chat-upload').disabled, true); assert.match(f.$('chat-upload-hint').textContent, /待修复/);
});

const descendants = node => node.children.flatMap(child => [child, ...descendants(child)]);
const buttonNamed = (node, label) => descendants(node).find(child => child.tagName === 'BUTTON' && child.attrs['aria-label'] === label);
test('multiple attachments create one chat, auto-reference confirmed files, and never call a model', async t => {
  const f = await fixture(t), files = [{name: 'one.txt', size: 2}, {name: 'two.txt', size: 4}];
  f.$('chat-upload').files = files; await f.$('chat-upload').fire('change');
  assert.equal(f.creates.length, 1); assert.equal(f.uploads.length, 2); assert.equal(f.starts.length, 0);
  assert.deepEqual(f.app.state.attachments, f.uploads.map(item => item.fid)); assert.deepEqual(f.app.state.pendingUploads, []);
  assert.equal(f.$('chat-attachments').hidden, false); assert.match(f.$('chat-attachments').textContent, /one.txt.*two.txt/);
  assert.equal(f.$('chat-files-list').textContent, '暂无输出');
});
test('batch checks all types and attachment count before creating a chat or uploading any file', async t => {
  for (const files of [[{name: 'ok.txt', size: 2}, {name: 'bad.exe', size: 3}], Array.from({length: 9}, () => ({name: 'a.txt', size: 1}))]) {
    const f = await fixture(t); f.$('chat-upload').files = files; await f.$('chat-upload').fire('change');
    assert.equal(f.creates.length, 0); assert.equal(f.uploads.length, 0); assert.equal(f.starts.length, 0);
  }
});
test('removing a chip keeps the stored file available to reference; accepted send clears the draft chips only', async t => {
  const f = await fixture(t); await f.app.openConversation(CID); await f.select();
  f.$('chat-upload').files = [{name: 'source.txt', size: 3}]; await f.$('chat-upload').fire('change');
  const fid = f.uploads[0].fid;
  await buttonNamed(f.$('chat-attachments'), '移除附件 source.txt').fire('click');
  assert.deepEqual(f.app.state.attachments, []); assert.equal(f.files.get(CID).length, 1);
  await buttonNamed(f.$('chat-messages'), '引用附件 source.txt').fire('click');
  assert.deepEqual(f.app.state.attachments, [fid]); await f.send();
  assert.deepEqual(f.starts[0].attachment_ids, [fid]); assert.deepEqual(f.app.state.attachments, []); assert.equal(f.$('chat-attachments').hidden, true);
  assert.match(f.$('chat-messages').textContent, /source.txt/); assert.ok(buttonNamed(f.$('chat-messages'), '引用附件 source.txt'));
});
test('late reference buttons from another chat cannot add an attachment to the new chat', async t => {
  const f = await fixture(t); await f.app.openConversation(CID); f.$('chat-upload').files = [{name: 'private.txt', size: 3}]; await f.$('chat-upload').fire('change');
  await buttonNamed(f.$('chat-attachments'), '移除附件 private.txt').fire('click');
  const old = buttonNamed(f.$('chat-messages'), '引用附件 private.txt'); await f.app.openConversation(OTHER); await old.fire('click');
  assert.deepEqual(f.app.state.attachments, []); assert.doesNotMatch(f.$('chat-messages').textContent + f.$('chat-attachments').textContent, /private.txt/);
});
test('a lost upload response is not retried or auto-referenced; GET recovers the saved attachment for explicit reuse', async t => {
  const f = await fixture(t); await f.app.openConversation(CID); const upload = f.api.upload;
  f.api.upload = async (...args) => { await upload(...args); throw new Error('lost response'); };
  f.$('chat-upload').files = [{name: 'lost.txt', size: 3}]; await f.$('chat-upload').fire('change');
  assert.equal(f.uploads.length, 1); assert.deepEqual(f.app.state.attachments, []); assert.match(f.$('chat-file-status').textContent, /未自动重传/);
  await buttonNamed(f.$('chat-messages'), '引用附件 lost.txt').fire('click'); assert.deepEqual(f.app.state.attachments, [f.uploads[0].fid]); assert.equal(f.starts.length, 0);
});
test('account change during upload clears pending filenames and discards the late result', async t => {
  const gate = deferred(), f = await fixture(t, {uploadHook: () => gate.promise}); await f.app.openConversation(CID);
  f.$('chat-upload').files = [{name: 'private-pending.txt', size: 3}]; const pending = f.$('chat-upload').fire('change'); await tick();
  assert.match(f.$('chat-attachments').textContent, /private-pending.txt/); f.app.clearAll(); gate.resolve(); await pending;
  assert.deepEqual(f.app.state.attachments, []); assert.deepEqual(f.app.state.pendingUploads, []); assert.equal(f.$('chat-attachments').textContent, ''); assert.equal(f.$('chat-attach').disabled, true);
});
test('an unaccepted send keeps attachment chips and text for manual correction, without automatic resend', async t => {
  const f = await fixture(t); await f.app.openConversation(CID); await f.select();
  f.$('chat-upload').files = [{name: 'retry.txt', size: 3}]; await f.$('chat-upload').fire('change');
  let calls = 0; f.api.start = async () => { calls++; throw new Error('rejected before acceptance'); }; await f.send();
  assert.equal(calls, 1); assert.equal(f.$('chat-text').value, 'private synthetic message'); assert.deepEqual(f.app.state.attachments, [f.uploads[0].fid]); assert.match(f.$('chat-attachments').textContent, /retry.txt/);
});
test('partial batch failure keeps successful chips and never uploads the rest or starts generation', async t => {
  let count = 0; const f = await fixture(t, {uploadHook: async () => { if (++count === 2) throw new Error('upload failed'); }}); await f.app.openConversation(CID);
  f.$('chat-upload').files = ['one.txt', 'two.txt', 'three.txt'].map(name => ({name, size: 3})); await f.$('chat-upload').fire('change');
  assert.equal(f.uploads.length, 2); assert.deepEqual(f.app.state.attachments, [f.uploads[0].fid]); assert.deepEqual(f.app.state.pendingUploads, []); assert.match(f.$('chat-file-status').textContent, /已添加 1 个附件/); assert.equal(f.starts.length, 0);
});
test('an unavailable draft attachment can still be removed without adding an unknown file', async t => {
  const f = await fixture(t); await f.app.openConversation(CID); const fid = randomUUID(); f.app.state.attachments = [fid]; await f.app.recover();
  await buttonNamed(f.$('chat-attachments'), `移除附件 ${fid}`).fire('click'); assert.deepEqual(f.app.state.attachments, []); assert.equal(f.uploads.length, 0);
});
