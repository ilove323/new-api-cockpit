/* Focused DOM/VM fixtures only: node --test tests/test_manual_frontend.cjs
 * No browser, new dependency, live account, model call, clipboard or deployment.
 */
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.join(__dirname, '../src/new_api_cockpit');
const html = fs.readFileSync(path.join(root, 'templates/manual.html'), 'utf8');
const source = fs.readFileSync(path.join(root, 'static/manual.js'), 'utf8');
const css = fs.readFileSync(path.join(root, 'static/manual.css'), 'utf8');
const SECRET = 'sk-fixture-private-never-render';
const tokens = [
  {id: 1, name: '我的日常令牌', key_masked: 'sk-ab****yz', group: 'default', status: 1,
    expired_time: 0, remain_quota: 1234, unlimited_quota: false, usable: true, unavailable_reason: ''},
  {id: 2, name: '已停用', key_masked: 'sk-cd****ef', group: 'research', status: 2,
    expired_time: 0, remain_quota: 0, unlimited_quota: true, usable: false, unavailable_reason: '令牌已禁用'},
  {id: 3, name: '备用令牌', key_masked: 'sk-gh****ij', group: 'research', status: 1,
    expired_time: 2000000000, remain_quota: 0, unlimited_quota: true, usable: true, unavailable_reason: ''},
];
const models = [
  {id: 'model-one', protocols: ['chat', 'responses', 'anthropic']},
  {id: 'model-two', protocols: ['responses']},
  {id: 'name-looks-like-anthropic', protocols: []},
];
const response = (data, status = 200) => ({ok: status >= 200 && status < 300, status, json: async () => data});
const deferred = () => { let resolve, reject; const promise = new Promise((r, j) => { resolve = r; reject = j; }); return {promise, resolve, reject}; };
const settle = async () => { for (let i = 0; i < 5; i++) await new Promise(resolve => setImmediate(resolve)); };
class Element {
  constructor(tag = 'div') {
    this.tagName = tag.toUpperCase(); this.children = []; this.attrs = {}; this.events = {};
    this.value = ''; this.textContent = ''; this.hidden = false; this.disabled = false;
    this.classes = new Set();
    this.classList = {toggle: (name, force) => { if (force === undefined) force = !this.classes.has(name); if (force) this.classes.add(name); else this.classes.delete(name); }};
  }
  set innerHTML(_) { throw new Error('innerHTML is forbidden in this fixture'); }
  setAttribute(name, value) { this.attrs[name] = String(value); }
  addEventListener(name, fn, options) { (this.events[name] ??= []).push({fn, options}); }
  async fire(name, detail = {}) { for (const {fn} of this.events[name] || []) await fn({target: this, currentTarget: this, ...detail}); }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; this.textContent = ''; if (this.tagName === 'SELECT') this.value = children[0]?.value || ''; }
}
async function harness({clipboard = 'promise', handler, guides: suppliedGuides, missingModule = false, actualGuides = false, build} = {}) {
  const elements = new Map([...html.matchAll(/<([a-z][\w-]*)[^>]*\bid="([^"]+)"/g)].map(match => [match[2], new Element(match[1])]));
  elements.set('logout', new Element('button')); elements.get('manual-platform').value = 'posix';
  const meta = {content: 'fixture-session-a'}, events = {}, calls = [], writes = [], buildCalls = [], timers = new Map();
  const channels = [], observers = [], clipboardCalls = [], forbidden = [];
  let timerID = 0;
  const guides = suppliedGuides || Array.from({length: 9}, (_, index) => ({
    id: `tool-${index}`, title: `工具 ${index + 1}`, description: `工具 ${index + 1} 的教程`,
    protocols: ['chat', 'responses', 'anthropic'], defaultProtocol: 'chat',
    steps: [{title: '安装并打开工具', body: '在自己的设备上安装。\n这是配置说明，不是官方截图。'}, {title: '填写连接配置', body: '核对模型与地址后保存。'}],
    notes: ['验证连接可能收费。'], sources: [{title: '官方文档', url: 'https://platform.openai.com/docs/overview'}],
  }));
  const defaultHandler = async url => url.endsWith('/tokens') ? response({tokens, token_manager_path: '/keys'})
    : url.endsWith('/models') ? response({models}) : response({key: SECRET});
  const h = {elements, events, calls, writes, buildCalls, timers, channels, observers, clipboardCalls, forbidden, guides,
    handle: handler || defaultHandler, el: id => elements.get(`manual-${id}`),
    async fire(id, event = 'click', value) { if (value !== undefined) this.el(id).value = value; await this.el(id).fire(event); await settle(); },
    async choose() { await this.fire('token', 'change', '1'); await this.fire('model', 'change', 'model-one'); },
    async event(name, detail = {}) { for (const fn of events[name] || []) await fn(detail); await settle(); },
    async tool(index) { await this.el('tools').children[index].fire('click'); await settle(); },
    dom() {
      const walk = node => [node.textContent, JSON.stringify(node.attrs), node.value, node.href, ...node.children.flatMap(walk)];
      return [...elements.values()].flatMap(walk).filter(Boolean).join('\n');
    },
    timeout() { for (const [, {fn, ms}] of [...timers]) { assert.equal(ms, 15000); fn(); } },
  };
  const clip = clipboard === 'none' ? undefined : {
    async writeText(value) { clipboardCalls.push({type: 'text', value}); if (clipboard === 'reject-text') throw new Error(`denied ${SECRET}`); writes.push(value); },
  };
  if (clipboard === 'promise' || clipboard === 'reject-promise') clip.write = async items => {
    clipboardCalls.push({type: 'promise', items});
    if (clipboard === 'reject-promise') throw new Error(`denied ${SECRET}`);
    const blob = await items[0].types['text/plain'];
    writes.push(await blob.text());
  };
  const ctx = {
    document: {getElementById: id => elements.get(id), createElement: tag => new Element(tag),
      querySelector: query => query === 'meta[name="cockpit-session"]' ? meta : null},
    navigator: {clipboard: clip}, location: {origin: 'https://models.example.test', href: 'https://models.example.test/cockpit/manual'},
    AbortController, URL, Blob, Date,
    setTimeout(fn, ms) { const id = ++timerID; timers.set(id, {fn, ms}); return id; }, clearTimeout(id) { timers.delete(id); },
    addEventListener(name, fn) { (events[name] ??= []).push(fn); },
    ClipboardItem: class { constructor(types) { this.types = types; } },
    BroadcastChannel: class { constructor(name) { this.name = name; this.events = {}; channels.push(this); } addEventListener(name, fn) { this.events[name] = fn; } close() { this.closed = true; } },
    MutationObserver: class { constructor(fn) { this.fn = fn; observers.push(this); } observe() {} disconnect() { this.closed = true; } },
    fetch: async (url, init) => {
      // Synthetic equivalent of the pre-existing global auth wrapper.
      const bridged = {...init, headers: {...init.headers, 'X-Cockpit-Session': meta.content}};
      calls.push({url, init, bridged, body: init.body ? JSON.parse(init.body) : undefined});
      return h.handle(url, bridged);
    },
    localStorage: {getItem() { forbidden.push('storage'); throw new Error('storage'); }, setItem() { forbidden.push('storage'); throw new Error('storage'); }},
    sessionStorage: {setItem() { forbidden.push('storage'); throw new Error('storage'); }},
    console: {log() { forbidden.push('log'); }, error() { forbidden.push('log'); }},
  };
  ctx.window = ctx;
  if (!missingModule) ctx.CockpitManualGuides = {guides, build(id, options) {
    buildCalls.push({id, ...options});
    if (build) return build(id, options);
    return [{title: '设置配置', language: options.platform === 'posix' ? 'sh' : 'powershell', content: JSON.stringify({id, ...options})},
      {title: '配置文件', language: 'json', content: JSON.stringify({second: true, ...options})}];
  }};
  if (actualGuides) {
    vm.runInNewContext(fs.readFileSync(path.join(root, 'static/manual-guides.js'), 'utf8'), ctx, {filename: 'manual-guides.js'});
    const actual = ctx.CockpitManualGuides;
    h.guides = actual.guides;
    ctx.CockpitManualGuides = {guides: actual.guides, build(id, options) {
      buildCalls.push({id, ...options}); return actual.build(id, options);
    }};
  }
  h.ctx = ctx; h.meta = meta;
  vm.runInNewContext(source, ctx, {filename: 'manual.js'}); await settle();
  return h;
}

test('template reuses the app shell, loads guides first, and omits the redundant setup diagram', () => {
  for (const partial of ['auth', 'sidebar']) assert.match(html, new RegExp(`include "partials/${partial}\\.html"`));
  assert.match(html, /app_header\(site_name\)/);
  assert.match(html, /ui\.css/); assert.match(html, /navigation\.css/);
  assert.ok(html.indexOf('defer src="/cockpit/static/manual-guides.js"') < html.indexOf('defer src="/cockpit/static/manual.js"'));
  assert.match(html, /模型使用/);
  for (const old of ['配置示意', 'manual-figure', 'manual-flow', 'manual-settings', 'manual-diagram-', 'manual-origin']) {
    assert.ok(!html.includes(old)); assert.ok(!css.includes(old));
  }
  assert.ok(!source.includes("$('diagram-")); assert.ok(!source.includes("$('origin')"));
  assert.match(html, /href="\/keys"/); assert.ok(!html.includes('href="/cockpit/keys'));
  assert.match(css, /var\(--ui-/); assert.match(css, /max-width: 540px/);
  assert.ok(!html.includes('manual-guide-limit'));
  assert.ok(!html.includes('资料核对不等于'));
  assert.ok(!html.includes('manual-token-help')); assert.ok(!html.includes('manual-model-help'));
  assert.ok(!source.includes('已按本人令牌权限读取模型'));
  assert.ok(!html.includes('manual-copy-example')); assert.ok(!source.includes("$('copy-example')"));
  assert.ok(!html.includes('manual-privacy')); assert.ok(!css.includes('.manual-privacy'));
  assert.ok(!html.includes('复制示例')); assert.ok(!source.includes('复制示例'));
  assert.ok(!html.includes('粘贴后请清理剪贴板'));
});
test('tokens are personal, masked, grouped and gated; selecting a disabled token cannot request models', async () => {
  const h = await harness();
  assert.equal(h.el('tools').children.length, 9);
  assert.match(h.el('token').children[1].textContent, /我的日常令牌.*sk-ab\*\*\*\*yz.*default.*可用/);
  assert.equal(h.calls.length, 1); assert.equal(h.el('copy-key').disabled, true);
  await h.fire('token', 'change', '2');
  assert.equal(h.calls.length, 1); assert.equal(h.el('model').disabled, true);
  assert.equal(h.el('refresh-models').disabled, true); assert.match(h.el('load-status').textContent, /令牌已禁用/);
  assert.match(h.dom(), /不限额/); assert.equal(h.el('token-manager').href, '/keys');
});
test('POSTs use the required guard, safe fetch options and the global session bridge; no paid calls', async () => {
  const h = await harness(); await h.choose(); await h.fire('copy-key');
  assert.deepEqual(h.calls.map(call => call.url), ['/cockpit/api/manual/tokens', '/cockpit/api/manual/models', '/cockpit/api/manual/key']);
  for (const call of h.calls) {
    assert.equal(call.init.cache, 'no-store'); assert.equal(call.init.redirect, 'error');
    assert.equal(call.init.credentials, 'same-origin'); assert.ok(call.init.signal);
    assert.equal(call.bridged.headers['X-Cockpit-Session'], 'fixture-session-a');
    if (call.init.method === 'POST') {
      assert.equal(call.init.headers['X-Cockpit-Manual'], '1'); assert.equal(call.init.headers['Content-Type'], 'application/json');
      assert.deepEqual(call.body, {token_id: 1});
      assert.ok(!Object.hasOwn(call.init.headers, 'X-Cockpit-Session'));
    }
  }
  assert.equal(h.timers.size, 0);
});
test('one copy button fills the clipboard with KEY while the preview keeps API_KEY', async () => {
  const h = await harness(); await h.choose();
  assert.equal(h.el('copy-example'), undefined);
  assert.match(h.el('code').textContent, /API_KEY/);
  assert.ok(h.buildCalls.every(call => call.key === 'API_KEY'));
  assert.equal(h.calls.filter(call => call.url.endsWith('/key')).length, 0);
  await h.fire('copy-key');
  assert.match(h.writes[0], new RegExp(SECRET)); assert.equal(h.buildCalls.at(-1).key, SECRET);
  assert.ok(!h.dom().includes(SECRET)); assert.deepEqual(h.forbidden, []);
  assert.match(h.el('code').textContent, /API_KEY/);
  await h.fire('copy-key'); assert.equal(h.calls.filter(call => call.url.endsWith('/key')).length, 2);
  assert.match(h.writes[1], new RegExp(SECRET));
});
test('ClipboardItem promise starts writing in the click before the key response settles', async () => {
  const pending = deferred(), h = await harness(); await h.choose();
  h.handle = async () => ({ok: true, status: 200, json: () => pending.promise});
  const copying = h.el('copy-key').fire('click'); await settle();
  assert.equal(h.clipboardCalls.length, 1); assert.equal(h.writes.length, 0);
  assert.equal(h.el('copy-key').disabled, true);
  await h.el('copy-key').fire('click'); assert.equal(h.calls.filter(call => call.url.endsWith('/key')).length, 1);
  pending.resolve({key: SECRET}); await copying; await settle();
  assert.match(h.writes[0], new RegExp(SECRET));
});
test('system, protocol and snippet selection are captured by the configuration builder', async () => {
  const h = await harness(); await h.choose();
  await h.fire('platform', 'change', 'powershell'); await h.fire('protocol', 'change', 'anthropic');
  await h.fire('snippet', 'change', '1'); await h.fire('copy-key');
  const built = JSON.parse(h.writes[0]);
  assert.equal(built.second, true); assert.equal(built.platform, 'powershell'); assert.equal(built.protocol, 'anthropic');
  assert.equal(built.origin, 'https://models.example.test'); assert.equal(built.model, 'model-one');
  assert.match(h.el('code-language').textContent, /json/);
});
test('all three protocols stay visible before choosing a model and with missing model metadata', async () => {
  const h = await harness(); await h.fire('token', 'change', '1');
  assert.deepEqual(h.el('protocol').children.map(item => item.value), ['chat', 'responses', 'anthropic']);
  assert.equal(h.el('load-status').textContent, ''); assert.equal(h.el('load-status').hidden, true);
  await h.fire('model', 'change', 'name-looks-like-anthropic');
  assert.equal(h.el('protocol-help').textContent, ''); assert.equal(h.el('protocol-help').hidden, true);
  assert.ok(h.el('protocol').children.every(item => !item.disabled));
  assert.equal(h.el('protocol').children.length, 3); assert.equal(h.el('protocol').value, 'chat');
  await h.fire('protocol', 'change', 'responses'); assert.equal(h.buildCalls.at(-1).protocol, 'responses');
});
test('missing tool protocol configuration shows three disabled options and cannot copy', async () => {
  const h = await harness(); h.guides[0].protocols = []; await h.choose();
  assert.match(h.el('protocol-help').textContent, /工具协议配置缺失/);
  assert.equal(h.el('protocol').children.length, 3);
  assert.ok(h.el('protocol').children.every(item => item.disabled));
  assert.equal(h.el('protocol').disabled, true); assert.equal(h.el('copy-key').disabled, true);
  assert.equal(h.el('preview').hidden, true);
});
test('contradictory model metadata does not hide the tool protocol or block its configurations', async () => {
  const h = await harness(); h.guides[0].protocols = ['anthropic'];
  await h.fire('token', 'change', '1'); await h.fire('model', 'change', 'model-two');
  assert.deepEqual(h.el('protocol').children.map(item => [item.value, item.disabled]),
    [['chat', true], ['responses', true], ['anthropic', false]]);
  assert.equal(h.el('protocol').value, 'anthropic'); assert.equal(h.el('protocol').disabled, false);
  assert.equal(h.el('protocol-help').textContent, ''); assert.equal(h.el('protocol-help').hidden, true);
  assert.equal(h.el('copy-key').disabled, false);
  assert.equal(h.el('preview').hidden, false);
  assert.ok(!source.includes('没有共同声明的协议'));
  await h.fire('protocol', 'change', 'responses');
  assert.equal(h.el('protocol').value, 'anthropic'); assert.equal(h.buildCalls.at(-1).protocol, 'anthropic');
});
test('tool cards omit obsolete support badges and allow configured tools to copy', async () => {
  const h = await harness(); await h.choose(); await h.tool(7);
  assert.equal(h.el('guide-badge'), undefined);
  assert.ok(!css.includes('.manual-badge'));
  assert.ok(h.el('tools').children.every(card => card.children.length === 3));
  assert.ok(!h.dom().includes('文档已核对')); assert.ok(!h.dom().includes('有限支持'));
  assert.equal(h.el('copy-key').disabled, false);
  assert.equal(h.el('protocol-help').textContent, ''); assert.equal(h.el('protocol-help').hidden, true);
  await h.tool(8);
  assert.equal(h.el('copy-key').disabled, false);
  assert.equal(h.calls.filter(call => call.url.endsWith('/key')).length, 0);
});
test('backend model errors are displayed verbatim as text without IP bypass or global fallback', async () => {
  const h = await harness(); h.handle = async () => response({error: 'IP 不在允许列表 <b>denied</b>'}, 403);
  await h.fire('token', 'change', '1');
  assert.equal(h.el('load-status').textContent, 'IP 不在允许列表 <b>denied</b>');
  assert.equal(h.el('load-status').hidden, false);
  assert.equal(h.el('copy-key').disabled, true); assert.equal(h.calls.length, 2);
  assert.equal(h.calls.at(-1).url, '/cockpit/api/manual/models');
  assert.equal(h.el('refresh-models').disabled, false);
  h.handle = async () => response({models}); await h.fire('refresh-models');
  await h.fire('model', 'change', 'model-one');
  assert.equal(h.el('protocol').disabled, false); assert.equal(h.el('copy-key').disabled, false);
});
test('backend key errors show safe detail, never leak an echoed KEY, and never retry', async () => {
  const h = await harness(); await h.choose();
  h.handle = async () => response({error: `令牌权限不足 ${SECRET} <script>bad</script>`}, 403);
  await h.fire('copy-key');
  assert.match(h.el('copy-status').textContent, /令牌权限不足/); assert.ok(!h.dom().includes(SECRET));
  assert.equal(h.calls.filter(call => call.url.endsWith('/key')).length, 1); assert.equal(h.writes.length, 0);
});
for (const transition of ['token', 'model', 'protocol', 'tool', 'platform', 'target', 'snippet', 'auth', 'logout', 'pagehide']) {
  test(`late KEY cannot build or copy after ${transition} changes`, async () => {
    const h = await harness(); await h.choose();
    const pending = deferred(); h.handle = async url => url.endsWith('/key') ? {ok: true, status: 200, json: () => pending.promise} : response({models});
    const copying = h.el('copy-key').fire('click'); await settle();
    const request = h.calls.at(-1);
    if (transition === 'token') await h.fire('token', 'change', '3');
    else if (transition === 'model') await h.fire('model', 'change', 'model-two');
    else if (transition === 'protocol') await h.fire('protocol', 'change', 'responses');
    else if (transition === 'tool') await h.tool(1);
    else if (transition === 'platform') await h.fire('platform', 'change', 'powershell');
    else if (transition === 'snippet') await h.fire('snippet', 'change', '1');
    else if (transition === 'target') await h.fire('target', 'change', 'codex');
    else if (transition === 'auth') await h.event('cockpit-auth-changed');
    else if (transition === 'logout') await h.elements.get('logout').fire('click');
    else await h.event('pagehide');
    assert.equal(request.init.signal.aborted, true);
    pending.resolve({key: SECRET}); await copying; await settle();
    assert.equal(h.writes.length, 0); assert.ok(h.buildCalls.every(call => call.key === 'API_KEY'));
    assert.ok(!h.dom().includes(SECRET));
    if (['auth', 'logout', 'pagehide'].includes(transition)) {
      assert.equal(h.el('token').value, ''); assert.equal(h.el('model').value, '');
      assert.equal(h.el('code').textContent, ''); assert.equal(h.el('preview').hidden, true);
      assert.equal(h.el('copy-key').disabled, true); assert.equal(h.el('refresh-tokens').disabled, true);
    }
  });
}
test('a stale model read cannot replace a newer token model list, or erase its status', async () => {
  const h = await harness(), pending = deferred();
  h.handle = async (url, init) => JSON.parse(init.body).token_id === 1
    ? {ok: true, status: 200, json: () => pending.promise} : response({models: [{id: 'new-token-model', protocols: ['chat']}]});
  h.el('token').value = '1'; const old = h.el('token').fire('change'); await settle();
  const oldRequest = h.calls.at(-1); await h.fire('token', 'change', '3');
  const status = h.el('load-status').textContent;
  pending.resolve({models: [{id: 'old-token-model', protocols: []}]}); await old; await settle();
  assert.equal(oldRequest.init.signal.aborted, true);
  assert.deepEqual(h.el('model').children.map(node => node.value), ['', 'new-token-model']);
  assert.equal(h.el('load-status').textContent, status);
});
test('changing tools while model loading cancels the response; no automatic POST restart', async () => {
  const h = await harness(), pending = deferred(); h.handle = async () => pending.promise;
  h.el('token').value = '1'; const old = h.el('token').fire('change'); await settle();
  await h.tool(2); pending.resolve(response({models})); await old; await settle();
  assert.equal(h.el('model').children.length, 1); assert.equal(h.el('model').disabled, true);
  assert.equal(h.calls.filter(call => call.url.endsWith('/models')).length, 1);
  assert.equal(h.el('refresh-models').disabled, false); assert.match(h.el('load-status').textContent, /读取已取消/);
});
test('auth change cancels a pending token list and cannot repopulate cleared account state', async () => {
  const pending = deferred(), h = await harness({handler: async () => pending.promise});
  await h.event('cockpit-auth-changed'); pending.resolve(response({tokens})); await settle();
  assert.equal(h.calls[0].init.signal.aborted, true); assert.equal(h.el('token').children.length, 1);
  assert.equal(h.el('token').disabled, true); assert.equal(h.el('token-details').hidden, true);
  assert.equal(h.el('copy-key').disabled, true);
});
test('cross-tab auth, storage events and session-meta changes invalidate immediately', async () => {
  for (const kind of ['channel', 'storage', 'meta']) {
    const h = await harness(); await h.choose();
    const event = {kind: 'authenticated', sid: 'another-account-session', timestamp: Date.now()};
    if (kind === 'channel') h.channels[0].events.message({data: event});
    else if (kind === 'storage') await h.event('storage', {key: 'new-api:auth-session:event', newValue: JSON.stringify(event)});
    else { h.meta.content = 'another-account-session'; h.observers[0].fn(); }
    assert.equal(h.el('copy-key').disabled, true); assert.equal(h.el('code').textContent, '');
    assert.equal(h.el('token').value, ''); assert.equal(h.channels[0].closed, true);
  }
});
test('unrelated and malformed cross-tab events do not invalidate the page', async () => {
  const h = await harness(); await h.choose();
  await h.event('storage', {key: 'new-api:auth-session:event', newValue: '{invalid'});
  h.channels[0].events.message({data: {kind: 'signed_out', sid: 'unrelated', timestamp: Date.now()}});
  h.channels[0].events.message({data: {kind: 'authenticated', sid: 'other', timestamp: Date.now() - 90000}});
  assert.equal(h.el('copy-key').disabled, false);
});
test('request deadline aborts a hung POST, has no automatic retries and leaves no stale render', async () => {
  const h = await harness(); await h.choose(); const pending = deferred();
  h.handle = async () => pending.promise;
  const copying = h.el('copy-key').fire('click'); await settle(); const request = h.calls.at(-1);
  h.timeout(); await copying; await settle();
  assert.equal(request.init.signal.aborted, true); assert.equal(h.timers.size, 0);
  assert.equal(h.calls.filter(call => call.url.endsWith('/key')).length, 1);
  assert.equal(h.el('copy-key').disabled, false);
  pending.resolve(response({key: SECRET})); await settle(); assert.equal(h.writes.length, 0);
});
for (const clipboard of ['reject-promise', 'reject-text']) test(`${clipboard} failure never exposes KEY or uses an unsafe fallback`, async () => {
  const h = await harness({clipboard}); await h.choose(); await h.fire('copy-key');
  assert.equal(h.writes.length, 0); assert.ok(!h.dom().includes(SECRET));
  assert.match(h.el('copy-status').textContent, /未展示 KEY.*未自动重发/);
  assert.equal(h.calls.filter(call => call.url.endsWith('/key')).length, 1); assert.deepEqual(h.forbidden, []);
});
test('no clipboard prevents fetching KEY, but leaves the placeholder preview readable', async () => {
  const h = await harness({clipboard: 'none'}); await h.choose(); await h.el('copy-key').fire('click');
  assert.equal(h.calls.filter(call => call.url.endsWith('/key')).length, 0);
  assert.equal(h.el('copy-key').disabled, true); assert.match(h.el('code').textContent, /API_KEY/);
  assert.match(h.el('copy-status').textContent, /安全剪贴板不可用/);
});
test('malicious dynamic text stays text and only HTTPS allowlisted official links are rendered', async () => {
  const h = await harness(); h.guides[0].title = '<img src=x onerror=bad>'; h.guides[0].steps[0].body = '<script>alert(1)</script>';
  h.guides[0].sources = [
    {title: '<b>official</b>', url: 'https://docs.qoder.com/zh/user-guide/chat/custom-models'},
    {title: 'repo', url: 'https://github.com/openai/codex'},
    ...['javascript:alert(1)', 'http://platform.openai.com/docs', 'https://platform.openai.com.evil.test/',
      'https://evil.test/', 'https://user:secret@openai.com/', 'https://github.com/untrusted/repo', 'https://openai.com:444/']
      .map(url => ({title: 'untrusted', url})),
  ];
  await h.choose();
  assert.equal(h.el('guide-title').textContent, '<img src=x onerror=bad>');
  assert.match(h.dom(), /<script>alert\(1\)<\/script>/);
  assert.equal(h.el('sources').children.length, 2);
  for (const row of h.el('sources').children) assert.equal(row.children[0].rel, 'noopener noreferrer');
  assert.ok(!/innerHTML|insertAdjacentHTML|execCommand|localStorage\.|sessionStorage\.|console\./.test(source));
});
test('empty/malformed responses and absent guides fail closed without inventing tools or models', async () => {
  const empty = await harness({handler: async () => response({tokens: []})});
  assert.equal(empty.el('empty').hidden, false); assert.equal(empty.el('copy-key').disabled, true);
  const malformed = await harness({handler: async () => response({rows: tokens})});
  assert.match(malformed.el('load-status').textContent, /未返回本人令牌列表/);
  const absent = await harness({missingModule: true}); await absent.choose();
  assert.equal(absent.el('tools').children.length, 0); assert.equal(absent.el('copy-key').disabled, true);
  assert.match(absent.el('guides-status').textContent, /教程内容暂未加载/);
});
test('even a mislabelled full key in key_masked is not displayed', async () => {
  const h = await harness({handler: async () => response({tokens: [{...tokens[0], key_masked: SECRET}]})});
  assert.ok(!h.dom().includes(SECRET)); assert.match(h.el('token').children[1].textContent, /已隐藏/);
});
test('unauthorized key response displays the backend error and disables the account', async () => {
  const h = await harness(); await h.choose(); h.handle = async () => response({error: '当前登录已失效'}, 401);
  await h.fire('copy-key'); assert.equal(h.el('load-status').textContent, '当前登录已失效');
  assert.equal(h.el('copy-key').disabled, true); assert.equal(h.el('code').textContent, '');
  assert.equal(h.calls.filter(call => call.url.endsWith('/key')).length, 1);
});
test('a token conflict does not log out the account; a changed session does', async () => {
  const h = await harness(); await h.choose();
  h.handle = async () => response({error: '所选令牌已过期，请换一个令牌。', code: 'MANUAL_ERROR'}, 409);
  await h.fire('copy-key');
  assert.match(h.el('copy-status').textContent, /所选令牌已过期/);
  assert.equal(h.el('refresh-tokens').disabled, false); assert.equal(h.writes.length, 0);
  assert.match(h.el('code').textContent, /API_KEY/);
  h.handle = async () => response({error: '登录账号已切换，请重新加载页面。', code: 'AUTH_SESSION_CHANGED'}, 409);
  await h.fire('copy-key');
  assert.equal(h.el('refresh-tokens').disabled, true); assert.equal(h.el('code').textContent, '');
});
test('the same Codex button copies TOML without reading KEY and fills KEY in the credential snippet', async () => {
  const h = await harness({actualGuides: true}); await h.choose();
  await h.tool(h.guides.findIndex(item => item.id === 'codex'));
  assert.equal(h.el('protocol-label').textContent, 'Codex 使用的协议');
  assert.equal(h.el('copy-key').disabled, false); assert.equal(h.el('copy-key-label').textContent, '复制配置');
  await h.fire('copy-key');
  assert.equal(h.calls.filter(call => call.url.endsWith('/key')).length, 0);
  assert.match(h.writes[0], /env_key = "NEW_API_MODEL_KEY"/); assert.ok(!h.writes[0].includes(SECRET));
  await h.fire('snippet', 'change', '1');
  assert.equal(h.el('copy-key-label').textContent, '复制配置（含 KEY）');
  assert.equal(h.el('copy-key').disabled, false); await h.fire('copy-key');
  assert.equal(h.calls.filter(call => call.url.endsWith('/key')).length, 1);
  assert.ok(h.writes[1].includes(SECRET)); assert.ok(!h.dom().includes(SECRET));
});
test('native defaults survive conflicting model metadata and tool changes', async () => {
  const h = await harness({actualGuides: true, handler: async url => url.endsWith('/models')
    ? response({models: [{id: 'model-one', protocols: ['chat']}]})
    : url.endsWith('/tokens') ? response({tokens, token_manager_path: '/keys'}) : response({key: SECRET})});
  await h.choose();
  assert.equal(h.el('protocol').value, 'anthropic');
  await h.tool(h.guides.findIndex(item => item.id === 'codex'));
  assert.deepEqual(h.el('protocol').children.map(item => [item.value, item.disabled]),
    [['chat', true], ['responses', false], ['anthropic', true]]);
  assert.equal(h.el('protocol').value, 'responses');
  assert.equal(h.el('copy-key').disabled, false); assert.match(h.el('code').textContent, /wire_api = "responses"/);
  await h.tool(h.guides.findIndex(item => item.id === 'curl'));
  assert.equal(h.el('protocol').value, 'responses');
  assert.ok(h.el('protocol').children.every(item => !item.disabled));
  await h.fire('protocol', 'change', 'chat');
  await h.tool(h.guides.findIndex(item => item.id === 'claude-code'));
  assert.equal(h.el('protocol').value, 'anthropic');
  assert.match(h.el('code').textContent, /ANTHROPIC_BASE_URL/);
});

test('CC Switch target choice updates routing and model launch without fetching a KEY', async () => {
  const h = await harness({actualGuides: true}); await h.choose();
  assert.equal(h.el('target-control').hidden, true);
  await h.tool(h.guides.findIndex(item => item.id === 'cc-switch'));
  assert.equal(h.el('target-control').hidden, false);
  assert.equal(h.el('protocol-label').textContent, 'CC Switch 上游协议');
  assert.ok(h.el('protocol').children.every(option => !option.disabled));
  await h.fire('protocol', 'change', 'chat');
  assert.match(h.el('code').textContent, /Routing/);
  await h.fire('target', 'change', 'codex');
  assert.match(h.el('code').textContent, /目标工具：Codex/);
  assert.equal(h.buildCalls.at(-1).target, 'codex');
  await h.fire('protocol', 'change', 'responses');
  assert.match(h.el('code').textContent, /Direct/);
  await h.fire('snippet', 'change', '1');
  assert.equal(h.el('copy-key-label').textContent, '复制配置');
  assert.match(h.el('code').textContent, /^codex --model='model-one'$/);
  await h.fire('copy-key');
  assert.equal(h.calls.filter(call => call.url.endsWith('/key')).length, 0);
  await h.tool(h.guides.findIndex(item => item.id === 'chatbox'));
  assert.equal(h.el('target-control').hidden, true);
  for (const protocol of ['chat', 'responses', 'anthropic']) {
    await h.fire('protocol', 'change', protocol);
    assert.equal(h.el('preview').hidden, false);
    assert.ok(h.el('protocol').children.every(option => !option.disabled));
  }
});

test('official screenshots render local assets with source links, without clipping or remote fetch', async () => {
  const h = await harness({actualGuides: true});
  await h.tool(h.guides.findIndex(item => item.id === 'trae'));
  const find = node => [node, ...node.children.flatMap(find)];
  const pictures = find(h.el('steps')).filter(node => node.tagName === 'IMG');
  assert.equal(pictures.length, 2);
  for (const img of pictures) {
    assert.match(img.src, /^\/cockpit\/static\/manual-images\/trae-/);
    assert.equal(img.loading, 'lazy'); assert.equal(img.decoding, 'async');
    assert.ok(img.alt && img.width > 0 && img.height > 0);
  }
  const sourceLinks = find(h.el('steps')).filter(node => node.textContent === '官方截图');
  assert.equal(sourceLinks.length, 2);
  assert.ok(sourceLinks.every(node => node.href === 'https://docs.trae.cn/ide_models' && node.rel === 'noopener noreferrer'));
  assert.match(css, /\.manual-screenshot img[^}]*height: auto[^}]*object-fit: contain/);
  assert.equal(h.calls.length, 1); // Only the personal token list, no image proxy or generation.
});

test('screenshot renderer rejects external images, path traversal and untrusted sources', async () => {
  const h = await harness();
  for (const image of [
    {src: 'https://fixture.test/track.png', source: 'https://docs.trae.cn/ide_models'},
    {src: '/cockpit/static/manual-images/../secret.png', source: 'https://docs.trae.cn/ide_models'},
    {src: '/cockpit/static/manual-images/fixture.webp', source: 'https://attacker.test/'},
  ]) {
    h.guides[1].steps[0].image = image;
    await h.tool(1);
    const row = h.el('steps').children[0], body = row.children[1];
    assert.equal(body.children.length, 2); // Only a text heading and body.
    await h.tool(0);
  }
});
test('generator exceptions cannot render secrets or create a clipboard fallback', async () => {
  const h = await harness({build(id, options) {
    if (options.key !== 'API_KEY') throw new Error(`bad config ${SECRET}`);
    return [{title: '配置', language: 'json', content: 'API_KEY'}];
  }}); await h.choose(); await h.fire('copy-key');
  assert.equal(h.writes.length, 0); assert.ok(!h.dom().includes(SECRET)); assert.match(h.el('code').textContent, /API_KEY/);
});
test('missing client protocols disables preview and copy without guessing chat', async () => {
  const h = await harness(); h.guides[8].protocols = []; h.guides[8].defaultProtocol = null;
  await h.choose(); const count = h.buildCalls.length; await h.tool(8);
  assert.equal(h.buildCalls.length, count); assert.equal(h.el('protocol').disabled, true);
  assert.equal(h.el('copy-key').disabled, true);
  assert.equal(h.el('preview').hidden, true); assert.match(h.el('protocol-help').textContent, /协议配置缺失/);
});
test('real content module: nine cards, every official source, both platforms and client protocols', async () => {
  const h = await harness({actualGuides: true}); await h.choose();
  assert.equal(h.guides.length, 9); assert.equal(h.el('tools').children.length, 9);
  for (const [index, item] of h.guides.entries()) {
    await h.tool(index);
    assert.equal(h.el('sources').children.length, item.sources.length, `${item.id}: every official source must pass the whitelist`);
    assert.deepEqual(h.el('sources').children.map(row => row.children[0].href), Array.from(item.sources, value => value.url));
    for (const row of h.el('sources').children) assert.equal(row.children[0].rel, 'noopener noreferrer');
    for (const platform of ['posix', 'powershell']) {
      await h.fire('platform', 'change', platform);
      assert.equal(h.el('copy-key').disabled, false, `${item.id}/${platform}: compatible configuration`);
      // Some blocks (e.g. Codex TOML) only reference the environment; the
      // companion credential block contains API_KEY, not every individual block.
      assert.ok(h.el('code').textContent.length > 0); assert.ok(!h.el('code').textContent.includes(SECRET));
      for (const protocol of item.protocols) {
        await h.fire('protocol', 'change', protocol);
        assert.equal(h.el('preview').hidden, false, `${item.id}/${platform}/${protocol}`);
        assert.equal(h.el('copy-key').disabled, false);
      }
    }
  }
  assert.ok(h.buildCalls.every(call => call.key === 'API_KEY')); assert.equal(h.calls.filter(call => call.url.endsWith('/key')).length, 0);
});
test('every manual color variable is defined by the actual shared ui stylesheet', () => {
  const shared = fs.readFileSync(path.join(root, 'static/ui.css'), 'utf8');
  const names = new Set([...css.matchAll(/var\((--[\w-]+)/g)].map(match => match[1]));
  for (const name of names) assert.match(shared, new RegExp(`${name}\\s*:`), `${name} must be defined`);
  assert.match(shared, /--ui-accent\s*:/);
});
test('manual icons exist in the shared local sprite', () => {
  const sprite = fs.readFileSync(path.join(root, 'static/icons.svg'), 'utf8');
  for (const [, name] of html.matchAll(/icon\('([^']+)'\)/g)) assert.ok(sprite.includes(`id="${name}"`), name);
});
