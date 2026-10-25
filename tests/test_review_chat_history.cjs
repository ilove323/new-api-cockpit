'use strict';
/* Deterministic controller race; no browser, network, credentials or dependencies. */
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {randomUUID} = require('node:crypto');
const {createChatApp} = require('../src/new_api_cockpit/static/chat/app.js');
const CID = '11111111-1111-4111-8111-111111111111';
const RID = '22222222-2222-4222-8222-222222222222';
const NEW_RID = '33333333-3333-4333-8333-333333333333';
const MID = '44444444-4444-4444-8444-444444444444';
const CAP = 'sha256:' + 'a'.repeat(64);
const clone = value => JSON.parse(JSON.stringify(value));
function defer() { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b; }); return {promise, resolve, reject}; }

// Minimal presentation-only DOM. Control logic and event projection are production code.
class Node {
  constructor(tag = 'div') {
    this.tagName = tag.toUpperCase(); this.children = []; this.dataset = {};
    this.value = ''; this._text = ''; this.disabled = false; this.handlers = new Map();
    this.classList = {toggle() {}};
  }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map(child => child.textContent).join(''); }
  get firstChild() { return this.children[0]; }
  append(...nodes) { for (const node of nodes) this.children.push(...(node.tagName === '#FRAGMENT' ? node.children : [node])); }
  replaceChildren(...nodes) { this._text = ''; this.children = []; this.append(...nodes); }
  addEventListener(name, fn) { this.handlers.set(name, fn); }
  setAttribute() {}
  querySelectorAll() { return []; }
  scrollIntoView() {}
  close() { this.open = false; }
  async fire(name, event = {preventDefault() {}}) { return this.handlers.get(name)?.(event); }
}
function surfaces() {
  const nodes = new Map();
  const doc = {
    getElementById(id) { if (!nodes.has(id)) nodes.set(id, new Node()); return nodes.get(id); },
    createElement: tag => new Node(tag), createTextNode: text => { const node = new Node('#text'); node.textContent = text; return node; },
    createDocumentFragment: () => new Node('#fragment'), documentElement: {scrollHeight: 0},
  };
  doc.getElementById('chat-mode').value = 'chat';
  const win = {
    location: {href: 'http://offline.test/cockpit/chat/', pathname: '/cockpit/chat/'},
    history: {pushState() {}, replaceState() {}}, crypto: {randomUUID},
    clearTimeout() {}, setTimeout() { return 1; },
    requestAnimationFrame(fn) { return setImmediate(fn); }, cancelAnimationFrame: clearImmediate,
    innerHeight: 800, scrollY: 0, addEventListener() {},
  };
  return {doc, win};
}
function event(seq, type, data, run_id = RID) {
  return {v: 1, seq, type, data, run_id, conversation_id: CID, at: '2026-10-15T00:00:00Z'};
}
function round(base, rid, text = 'old question') {
  const mid = randomUUID();
  return [
    event(base + 1, 'run.accepted', {text, model: 'offline', mode: 'chat', client_request_id: randomUUID(), context_snapshot: {context_revision: 0, skill_ids: []}}, rid),
    event(base + 2, 'run.dispatching', {request_index: 1, assistant_message_id: mid}, rid),
    event(base + 3, 'assistant.delta', {message_id: mid, text: 'old answer'}, rid),
    event(base + 4, 'assistant.message', {message_id: mid, text: 'old answer', complete: true, tool_calls: [], finish_reason: 'stop'}, rid),
    event(base + 5, 'run.terminal', {status: 'completed', usage: null, uncertain: false}, rid),
  ];
}
async function fixture(t) {
  const {doc, win} = surfaces();
  const history = [event(1, 'conversation.created', {title: 'existing'}, null)];
  for (let n = 0; n < 39; n++) history.push(...round(history.length, randomUUID()));
  for (let n = 0; n < 4; n++) history.push(event(history.length + 1, 'conversation.renamed', {title: 'existing'}, null));
  assert.equal(history.length, 200);
  history.push(...round(history.length, RID));
  const meta = {id: CID, title: 'existing', revision: 205, last_seq: 205, active_run_id: null, storage_state: 'ok', last_selection: {token_id: 7, model: 'offline', mode: 'chat'}};
  const f = {doc, win, history, meta, posts: [], streamErrors: []};
  f.page = (_cid, after, until, limit = 200) => {
    const high = until ?? history.at(-1).seq;
    const events = history.filter(e => e.seq > after && e.seq <= high).slice(0, limit);
    const next = events.at(-1)?.seq ?? after;
    return {events: clone(events), until_seq: high, next_after_seq: next, has_more: next < high};
  };
  const api = {
    config: async () => ({modes: ['chat', 'files'], allowed_file_extensions: ['.txt'], limits: {user_text_bytes: 32768, attachments_per_turn: 8, title_chars: 80, event_page_max: 200}}),
    tokens: async () => ({tokens: [{id: 7, usable: true}]}),
    context: async () => ({context_revision: 0, skills: [], limits: {skills_per_run: 3}}),
    conversations: async () => ({conversations: [clone(meta)], total: 1, has_more: false, next_cursor: null}),
    conversation: async () => ({conversation: clone(meta)}),
    models: async () => ({models: [{id: 'offline', chat_enabled: true, tools_enabled: false, capability_revision: CAP}]}),
    events: async (...args) => f.page(...args),
    run: async (_cid, rid) => ({run: {id: rid, status: 'completed'}}), files: async () => ({files: []}),
    start: async (_cid, body, options) => {
      f.posts.push(clone(body));
      const events = round(history.length, NEW_RID, body.text);
      Object.assign(events[0].data, body, {message_id: MID});
      history.push(...events);
      Object.assign(meta, {revision: history.length, last_seq: history.length});
      try { for (const item of events) options.onEvent(item); }
      catch (error) { f.streamErrors.push(error.message); throw error; }
      return {terminal: true};
    },
  };
  Object.assign(f, {api, app: createChatApp({window: win, document: doc, api})});
  t.after(() => f.app.clearAll());
  await f.app.ready;
  f.chooseModel = async () => {
    doc.getElementById('chat-token').value = '7';
    await doc.getElementById('chat-model-load').fire('click');
    doc.getElementById('chat-text').value = 'a new question';
  };
  return f;
}
function holdSecondPage(f) {
  const gate = defer(), entered = defer(), previous = f.api.events;
  f.api.events = async (...args) => {
    if (args[1] === 200) { entered.resolve(); await gate.promise; }
    return previous(...args);
  };
  return {gate, entered};
}
const submit = f => f.app.send({preventDefault() {}});
const enabled = f => !f.doc.getElementById('chat-send').disabled;

test('a delayed 200-event page blocks both the button and direct send, then enables exactly one paid POST', async t => {
  const f = await fixture(t), {gate, entered} = holdSecondPage(f);
  const opening = f.app.openConversation(CID);
  await entered.promise;
  assert.equal(f.app.state.events.length, 200);
  assert.equal(f.app.state.historyStatus, 'loading');
  assert.equal(f.app.state.historyUntilSeq, 205);
  await f.chooseModel();
  assert.equal(enabled(f), false);
  await submit(f); // Deliberately bypass disabled UI (e.g. Enter/programmatic submit).
  assert.equal(f.posts.length, 0);
  gate.resolve();
  await opening;
  assert.equal(f.app.state.historyStatus, 'ready');
  assert.equal(enabled(f), true);
  const original = clone(f.app.state.events);
  assert.equal(original.length, 205);
  await submit(f);
  assert.equal(f.posts.length, 1);
  assert.deepEqual(f.streamErrors, []);
  assert.equal(f.app.state.events.length, 210);
  assert.deepEqual(f.app.state.events.slice(0, 205), original, 'sending never trims UI history');
});

test('failed second page preserves the prefix and draft; only an explicit successful GET refresh re-enables sending', async t => {
  const f = await fixture(t), {gate, entered} = holdSecondPage(f);
  const opening = f.app.openConversation(CID);
  await entered.promise;
  await f.chooseModel();
  gate.reject(new Error('synthetic history read failure'));
  await opening;
  assert.equal(f.app.state.historyStatus, 'failed');
  assert.match(f.app.state.historyError, /synthetic/);
  assert.equal(f.app.state.events.length, 200);
  assert.equal(enabled(f), false);
  await submit(f);
  assert.equal(f.posts.length, 0);
  assert.equal(f.doc.getElementById('chat-text').value, 'a new question');
  f.api.events = async (...args) => f.page(...args);
  await f.app.recover();
  assert.equal(f.app.state.historyStatus, 'ready');
  assert.equal(f.app.state.historyError, null);
  assert.equal(f.app.state.events.length, 205);
  assert.equal(enabled(f), true);
  assert.equal(f.posts.length, 0);
});

test('refresh immediately revokes prior readiness while metadata is pending and does not discard visible history', async t => {
  const f = await fixture(t);
  await f.app.openConversation(CID); await f.chooseModel();
  assert.equal(enabled(f), true);
  const original = clone(f.app.state.events), gate = defer();
  f.api.conversation = () => gate.promise;
  const refreshing = f.app.recover();
  assert.equal(f.app.state.historyStatus, 'loading');
  assert.equal(f.app.state.historyUntilSeq, null);
  assert.equal(enabled(f), false);
  await submit(f);
  assert.deepEqual(f.app.state.events, original);
  assert.equal(f.posts.length, 0);
  gate.resolve({conversation: clone(f.meta)});
  await refreshing;
  assert.equal(f.app.state.historyStatus, 'ready');
  assert.equal(enabled(f), true);
});

test('initial metadata failure is explicit and retryable by GET without needing an existing meta object', async t => {
  const f = await fixture(t), original = f.api.conversation;
  f.api.conversation = async () => { throw new Error('synthetic metadata failure'); };
  await f.app.openConversation(CID);
  assert.equal(f.app.state.historyStatus, 'failed');
  assert.equal(f.app.state.meta, null);
  assert.equal(f.doc.getElementById('chat-recover').disabled, false);
  await submit(f);
  assert.equal(f.posts.length, 0);
  f.api.conversation = original;
  await f.app.recover();
  assert.equal(f.app.state.historyStatus, 'ready');
});

for (const late of ['metadata', 'page', 'failure']) test(`a late ${late} from an older same-chat refresh cannot overwrite newer readiness`, async t => {
  const f = await fixture(t);
  await f.app.openConversation(CID); await f.chooseModel();
  const gate = defer(), entered = defer();
  const method = late === 'page' ? 'events' : 'conversation', original = f.api[method];
  let hold = true;
  f.api[method] = async (...args) => {
    if (hold) { hold = false; entered.resolve(); return gate.promise; }
    return original(...args);
  };
  const older = f.app.recover(); await entered.promise;
  f.meta.title = 'newer metadata';
  await f.app.recover();
  assert.equal(f.app.state.historyStatus, 'ready');
  const originalEvents = clone(f.app.state.events);
  if (late === 'failure') gate.reject(new Error('late failed refresh'));
  else if (late === 'page') gate.resolve({events: [event(206, 'conversation.renamed', {title: 'late page'}, null)], until_seq: 206, next_after_seq: 206, has_more: false});
  else gate.resolve({conversation: {...clone(f.meta), title: 'obsolete metadata'}});
  await older;
  assert.equal(f.app.state.historyStatus, 'ready');
  assert.equal(f.app.state.historyError, null);
  assert.equal(f.app.state.meta.title, 'newer metadata');
  assert.equal(f.app.state.historyUntilSeq, 205);
  assert.deepEqual(f.app.state.events, originalEvents);
  assert.equal(enabled(f), true);
  assert.equal(f.posts.length, 0);
});

test('an old successful refresh cannot unlock a newer pending or failed refresh', async t => {
  const f = await fixture(t);
  await f.app.openConversation(CID); await f.chooseModel();
  const oldGate = defer(), newGate = defer();
  let calls = 0;
  f.api.conversation = () => ++calls === 1 ? oldGate.promise : newGate.promise;
  const older = f.app.recover(), newer = f.app.recover();
  oldGate.resolve({conversation: clone(f.meta)});
  await older;
  assert.equal(f.app.state.historyStatus, 'loading');
  assert.equal(f.app.state.historyUntilSeq, null);
  assert.equal(enabled(f), false);
  await submit(f);
  newGate.reject(new Error('newest refresh failed'));
  await newer;
  assert.equal(f.app.state.historyStatus, 'failed');
  assert.equal(enabled(f), false);
  await submit(f);
  assert.equal(f.posts.length, 0);
});

test('terminal confirmation catch-up must finish its newer high water before readiness', async t => {
  const f = await fixture(t), gate = defer(), entered = defer();
  let appended = false;
  f.api.run = async (_cid, rid) => {
    if (!appended) {
      appended = true;
      f.history.push(...round(f.history.length, randomUUID()));
      Object.assign(f.meta, {revision: f.history.length, last_seq: f.history.length});
    }
    return {run: {id: rid, status: 'completed'}};
  };
  f.api.events = async (...args) => {
    if (args[1] === 205) { entered.resolve(); await gate.promise; }
    return f.page(...args);
  };
  const opening = f.app.openConversation(CID); await entered.promise;
  await f.chooseModel();
  assert.equal(f.app.state.events.length, 205);
  assert.equal(f.app.state.historyStatus, 'loading');
  assert.equal(enabled(f), false);
  await submit(f);
  gate.resolve(); await opening;
  assert.equal(f.app.state.historyStatus, 'ready');
  assert.equal(f.app.state.historyUntilSeq, 210);
  assert.equal(f.app.state.events.length, 210);
  assert.equal(enabled(f), true);
  assert.equal(f.posts.length, 0);
});

test('a page claiming completion below the metadata high water cannot enable send', async t => {
  const f = await fixture(t);
  f.api.events = async (cid, after, _until, limit) => f.page(cid, after, 200, limit);
  await f.app.openConversation(CID); await f.chooseModel();
  assert.equal(f.app.state.events.length, 200);
  assert.equal(f.app.state.historyStatus, 'failed');
  assert.equal(enabled(f), false);
  await submit(f);
  assert.equal(f.posts.length, 0);
});

test('switching chats resets the high water and ignores the previous chat delayed page', async t => {
  const f = await fixture(t), {gate, entered} = holdSecondPage(f);
  const older = f.app.openConversation(CID); await entered.promise;
  const other = randomUUID(), metadata = defer();
  f.api.conversation = async cid => cid === other ? metadata.promise : {conversation: clone(f.meta)};
  const previous = f.api.events;
  f.api.events = async (cid, ...args) => cid === other
    ? {events: [{...event(1, 'conversation.created', {title: 'other'}, null), conversation_id: other}], until_seq: 1, next_after_seq: 1, has_more: false}
    : previous(cid, ...args);
  const newer = f.app.openConversation(other);
  assert.equal(f.app.state.historyStatus, 'loading');
  assert.equal(f.app.state.historyUntilSeq, null);
  assert.deepEqual(f.app.state.events, []);
  metadata.resolve({conversation: {...clone(f.meta), id: other, title: 'other', revision: 1, last_seq: 1}});
  await newer;
  gate.resolve(); await older;
  assert.equal(f.app.state.historyStatus, 'ready');
  assert.equal(f.app.state.historyUntilSeq, 1);
  assert.equal(f.app.state.events.length, 1);
  assert.equal(f.app.state.events[0].conversation_id, other);
  assert.equal(f.posts.length, 0);
});

test('account invalidation prevents an old page from restoring readiness or old content', async t => {
  const f = await fixture(t), {gate, entered} = holdSecondPage(f);
  const older = f.app.openConversation(CID); await entered.promise;
  f.app.clearAll(); gate.resolve(); await older;
  assert.equal(f.app.state.historyStatus, 'idle');
  assert.equal(f.app.state.historyUntilSeq, null);
  assert.deepEqual(f.app.state.events, []);
  assert.equal(enabled(f), false);
  await submit(f);
  assert.equal(f.posts.length, 0);
});

for (const malformed of ['cursor', 'early EOF', 'beyond high water', 'gap']) test(`a malformed history page (${malformed}) never grants readiness`, async t => {
  const f = await fixture(t), original = f.api.events;
  f.api.events = async (...args) => {
    const page = await original(...args);
    if (args[1] !== 200) return page;
    if (malformed === 'cursor') page.next_after_seq = 204;
    if (malformed === 'early EOF') { page.events.pop(); page.next_after_seq = 204; }
    if (malformed === 'beyond high water') { page.events.push(event(206, 'conversation.renamed', {title: 'bad'}, null)); page.next_after_seq = 206; }
    if (malformed === 'gap') page.events.shift();
    return page;
  };
  await f.app.openConversation(CID); await f.chooseModel();
  assert.equal(f.app.state.historyStatus, 'failed');
  assert.equal(f.app.state.events.length, 200);
  assert.equal(enabled(f), false);
  await submit(f);
  assert.equal(f.posts.length, 0);
});
