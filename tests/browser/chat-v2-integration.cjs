/* Real Chrome + real Flask/ChatService v2 acceptance, local/synthetic only.
 * Install Playwright in a local development environment, then run:
 *   node tests/browser/chat-v2-integration.cjs
 * No mocked chat HTTP responses, off-origin traffic, real KEY/profile, package
 * download or production state. Only the synthetic upstream stream is injected.
 */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const http = require('node:http');
const {createHash} = require('node:crypto');
const {spawn} = require('node:child_process');
const {once} = require('node:events');
const {chromium} = require('playwright');

const root = path.resolve(__dirname, '../..');
const python = process.env.COCKPIT_PYTHON || path.join(root, '.venv/bin/python');
const executablePath = process.env.COCKPIT_BROWSER_EXECUTABLE || ['/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', '/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge'].find(file => fs.existsSync(file));
const artifacts = '/tmp/new-api-cockpit-chat-v2-browser-artifacts';
const temp = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'cockpit-chat-v2-browser-')));
const MEMORY = '浏览器整合测试：偏好中文，先结论后依据。仅为合成资料。';
const FACT = '我长期偏好中文回复。';
const SOUL = '合成 SOUL：保持测试简洁，不修改手工 MEMORY。';
const OPAQUE = 'OPAQUE_REPLAY_V2_CANDIDATE_';
const children = [], requests = [], errors = [], failures = [], checks = [], screenshots = [], modelCalls = [], lockFailures = [];
const evidence = {};
let gateway, browser, desktop, origin;

function sourceFingerprint() {
  const result = {};
  function visit(relative) {
    const absolute = path.join(root, relative), stat = fs.statSync(absolute);
    if (stat.isDirectory()) {
      for (const name of fs.readdirSync(absolute).sort()) if (name !== '__pycache__') visit(path.join(relative, name));
    } else if (stat.isFile() && !/\.py[co]$/.test(relative)) result[relative] = createHash('sha256').update(fs.readFileSync(absolute)).digest('hex');
  }
  // All runtime code/templates/assets, recursively: not only chat's top level.
  for (const entry of ['src/new_api_cockpit', 'pyproject.toml', 'tests/chat_fixtures.py', 'tests/browser/chat_fixture.py', 'tests/browser/chat-integration.cjs', 'tests/browser/chat_v2_fixture.py', 'tests/browser/chat-v2-integration.cjs']) visit(entry);
  return result;
}
const baseline = sourceFingerprint();

function fixture(role) {
  const dataDir = path.join(temp, role); fs.mkdirSync(dataDir, {mode: 0o700});
  const child = spawn(python, ['-B', path.join(__dirname, 'chat_v2_fixture.py'), '--role', role, '--data-dir', dataDir], {
    cwd: root, stdio: ['ignore', 'pipe', 'pipe'],
    env: {PATH: process.env.PATH || '/usr/bin:/bin', LANG: 'en_US.UTF-8', TZ: 'Asia/Shanghai', PYTHONPATH: [path.join(root, 'src'), path.join(root, 'tests')].join(path.delimiter), PYTHONUNBUFFERED: '1', PYTHONDONTWRITEBYTECODE: '1'},
  });
  const record = {child, role, stderr: '', messages: []}; children.push(record);
  child.stderr.on('data', chunk => { record.stderr = (record.stderr + chunk.toString()).slice(-24000); });
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error(`${role} fixture startup timed out: ${record.stderr}`)), 30000);
    let buffer = '';
    child.stdout.on('data', chunk => {
      buffer += chunk.toString();
      while (buffer.includes('\n')) {
        const index = buffer.indexOf('\n'), line = buffer.slice(0, index); buffer = buffer.slice(index + 1);
        if (!line.startsWith('CHAT_BROWSER_FIXTURE ')) continue;
        let message;
        try { message = JSON.parse(line.slice('CHAT_BROWSER_FIXTURE '.length)); }
        catch (error) { clearTimeout(timer); reject(error); continue; }
        record.messages.push(message);
        if (message.kind === 'model_call') modelCalls.push(message);
        if (message.kind === 'lock_failure') lockFailures.push(message);
        if (message.kind === 'ready') { clearTimeout(timer); resolve({...message, record}); }
      }
    });
    child.once('error', error => { clearTimeout(timer); reject(error); });
    child.once('exit', code => { clearTimeout(timer); if (!record.messages.some(m => m.kind === 'ready')) reject(new Error(`${role} fixture exited ${code}: ${record.stderr}`)); });
  });
}

async function proxy(web, chat) {
  gateway = http.createServer((req, res) => {
    if (req.url === '/favicon.ico') { res.writeHead(204); res.end(); return; }
    const entry = {method: req.method, path: req.url, role: req.url.startsWith('/cockpit/api/chat/') ? 'chat' : 'web', session_stamped: !!req.headers['x-cockpit-session'], write_marker: req.headers['x-cockpit-chat'] === '1'};
    requests.push(entry);
    const chunks = [];
    if (entry.role === 'chat' && req.method !== 'GET') {
      req.on('data', chunk => chunks.push(chunk));
      req.on('end', () => { try { entry.body = JSON.parse(Buffer.concat(chunks).toString()); } catch { entry.body_invalid = true; } });
    }
    const target = http.request({hostname: '127.0.0.1', port: entry.role === 'chat' ? chat.port : web.port, method: req.method, path: req.url, headers: {...req.headers, connection: 'close'}}, upstream => {
      entry.status = upstream.statusCode; entry.content_type = upstream.headers['content-type'] || '';
      if (upstream.statusCode >= 400) { entry.response_body = ''; upstream.on('data', chunk => { entry.response_body = (entry.response_body + chunk.toString()).slice(0, 8192); }); }
      res.writeHead(upstream.statusCode, upstream.headers); upstream.pipe(res);
    });
    target.on('error', error => { entry.error = error.message; if (!res.headersSent) res.writeHead(502); res.end(); });
    req.on('aborted', () => target.destroy()); res.on('close', () => { if (!res.writableFinished) target.destroy(); }); req.pipe(target);
  });
  gateway.listen(0, '127.0.0.1'); await once(gateway, 'listening');
  return `http://127.0.0.1:${gateway.address().port}`;
}

function watch(page, label) {
  page.on('pageerror', error => errors.push({page: label, type: 'pageerror', message: error.message}));
  page.on('console', message => { if (message.type() === 'error') errors.push({page: label, type: 'console', message: message.text()}); });
  page.on('dialog', dialog => dialog.dismiss());
}
async function offlineContext(options) {
  const context = await browser.newContext({...options, locale: 'zh-CN'});
  await context.route('**/*', route => {
    const url = new URL(route.request().url());
    if (url.origin === origin || ['blob:', 'data:'].includes(url.protocol)) return route.continue();
    errors.push({type: 'off-origin', url: url.href}); return route.abort();
  });
  context.on('serviceworker', () => errors.push({type: 'unexpected-serviceworker'}));
  return context;
}
async function snap(page, name) {
  const file = path.join(artifacts, name + '.png'); await page.screenshot({path: file, fullPage: true, animations: 'disabled'}); screenshots.push(file);
}
async function stage(name, action) {
  try { await action(); checks.push({name, passed: true}); console.log('PASS ' + name); return true; }
  catch (error) {
    const diagnostic = {name, message: error.stack || error.message, http_errors: requests.filter(r => r.status >= 400 || r.error).slice(-5), lock_failures: lockFailures.slice(-5)};
    if (desktop && !desktop.isClosed()) {
      diagnostic.ui = await desktop.locator('#chat-status, #chat-list-status, #settings-status').allTextContents().catch(() => []);
      await snap(desktop, 'failure-' + name).catch(() => {});
    }
    failures.push(diagnostic); checks.push({name, passed: false}); console.error('FAIL ' + name + ': ' + error.message); return false;
  }
}
async function enabled(page, selector) { await page.waitForFunction(selector => { const e = document.querySelector(selector); return e && !e.disabled; }, selector); }
async function loadedChat(page, cid = '') {
  await page.goto(origin + '/cockpit/chat/' + (cid ? '?conversation=' + cid : ''));
  await page.waitForFunction(() => document.querySelector('#chat-list-info')?.textContent.includes('已显示') && document.querySelector('#chat-context-info')?.textContent.includes('个人设置'));
}
async function api(page, suffix, options = {}) {
  return page.evaluate(async ({suffix, options}) => {
    const init = options.method ? {method: options.method, headers: {'X-Cockpit-Chat': '1', 'Content-Type': 'application/json'}, body: JSON.stringify(options.body)} : undefined;
    const response = await window.fetch('/cockpit/api/chat' + suffix, init), value = await response.json();
    if (!response.ok) throw new Error(`HTTP ${response.status}: ${JSON.stringify(value)}`);
    return value;
  }, {suffix, options});
}
async function allEvents(page, cid) {
  const events = []; let after = 0, until;
  do {
    const pageValue = await api(page, `/conversations/${cid}/events?after_seq=${after}&limit=7${until === undefined ? '' : '&until_seq=' + until}`);
    until ??= pageValue.until_seq; assert.equal(pageValue.until_seq, until);
    for (const e of pageValue.events) { assert.equal(e.conversation_id, cid); assert.equal(e.seq, events.length + 1); events.push(e); }
    assert.equal(pageValue.next_after_seq, events.at(-1)?.seq || 0);
    after = pageValue.next_after_seq;
    if (!pageValue.has_more) { assert.equal(after, until); return events; }
  } while (after < until);
  assert.fail('Event pagination ended without an explicit completed page');
}
// Seed legacy multi-candidate history through the still-supported API, not a UI
// setting. All user-interface sends below must use the new fixed defaults.
async function seedLegacyCandidates(page, cid) {
  const meta = (await api(page, `/conversations/${cid}`)).conversation;
  const context = await api(page, '/context');
  const model = (await api(page, '/models', {method: 'POST', body: {token_id: 7}})).models.find(m => m.id === 'test-chat');
  const result = await page.evaluate(async ({cid, meta, context, model}) => {
    const response = await window.fetch(`/cockpit/api/chat/conversations/${cid}/runs`, {method: 'POST', headers: {'X-Cockpit-Chat': '1', 'Content-Type': 'application/json'}, body: JSON.stringify({client_request_id: crypto.randomUUID(), expected_revision: meta.revision, token_id: 7, model: model.id, capability_revision: model.capability_revision, context_revision: context.context_revision, skill_ids: [], mode: 'chat', text: '合成旧客户端生成三个独立候选。', attachment_ids: [], candidate_count: 3, web_enabled: false, auto_memory: false, context_strategy: 'adaptive'})});
    return {status: response.status, text: await response.text()};
  }, {cid, meta, context, model});
  assert.equal(result.status, 200, result.text); assert.match(result.text, /run.terminal/);
  await page.click('#chat-recover');
}
async function newChat(page) {
  const previous = new URL(page.url()).searchParams.get('conversation'); await page.click('#chat-new');
  await page.waitForFunction(previous => { const cid = new URL(location.href).searchParams.get('conversation'); return cid && cid !== previous && !document.querySelector('#chat-text').disabled; }, previous);
  return new URL(page.url()).searchParams.get('conversation');
}
async function chooseModel(page) {
  await enabled(page, '#chat-token'); await page.selectOption('#chat-token', '7'); await page.click('#chat-model-load');
  await page.waitForFunction(() => document.querySelector('#chat-model')?.value === 'test-chat' && !document.querySelector('#chat-send').disabled);
}
async function finished(page, expectedRuns) {
  await page.waitForFunction(expectedRuns => { const rows = [...document.querySelectorAll('#chat-messages .chat-run')]; return rows.length === expectedRuns && rows.every(row => row.querySelector('.chat-run-footer strong')?.textContent === '已完成') && !document.querySelector('#chat-text').disabled; }, expectedRuns);
}
async function write(page, method, suffix, action) {
  const pending = page.waitForResponse(r => r.request().method() === method && new URL(r.url()).pathname.endsWith(suffix));
  await action(); const response = await pending;
  if (!response.ok()) assert.fail(`${method} ${suffix}: ${response.status()} ${await response.text()}`);
  return response;
}
async function safeUI(page) {
  assert.equal(await page.locator('#chat-messages img, #chat-messages script, #chat-messages svg, #chat-messages iframe, #chat-messages object, #chat-messages embed').count(), 0);
  assert.equal(await page.evaluate(() => window.__v2Injected), undefined);
  assert(!(await page.locator('#chat-messages').textContent()).includes(OPAQUE), 'Opaque replay state must not be in any visible/collapsed UI text');
}
async function noOverflow(page, label) {
  const configuredWidth = page.viewportSize().width;
  const dimensions = await page.evaluate(configuredWidth => ({configured_width: configuredWidth, width: innerWidth, client: document.documentElement.clientWidth, visual_width: visualViewport?.width, scroll_x: scrollX, scroll: document.documentElement.scrollWidth,
    offending: [...document.querySelectorAll('main *')].filter(node => { const box = node.getBoundingClientRect(); return box.width > 0 && (box.right + scrollX > configuredWidth + 1 || node.scrollWidth > node.clientWidth + 1); }).slice(0, 12).map(node => ({tag: node.tagName, id: node.id, className: node.className, right: node.getBoundingClientRect().right + scrollX, width: node.getBoundingClientRect().width, client: node.clientWidth, scroll: node.scrollWidth, wrap: getComputedStyle(node).overflowWrap, preview: node.tagName === 'P' ? node.textContent.slice(0, 100) : undefined}))}), configuredWidth);
  evidence[label] = dimensions;
  if (dimensions.scroll > configuredWidth + 1 || dimensions.width > configuredWidth + 1) await snap(page, 'failure-' + label);
  assert(dimensions.scroll <= configuredWidth + 1 && dimensions.width <= configuredWidth + 1, `${label} horizontal overflow: ${JSON.stringify(dimensions)}`);
}
const runPosts = () => requests.filter(r => r.method === 'POST' && /\/runs$/.test(r.path));

async function main() {
  fs.mkdirSync(artifacts, {recursive: true});
  assert(executablePath && fs.existsSync(executablePath), 'Use an installed Chrome/Edge; never download a browser');
  const [web, chat] = await Promise.all([fixture('web'), fixture('chat')]);
  assert.equal(chat.seeded, 53); assert.equal(web.seeded, 0); origin = await proxy(web, chat);
  browser = await chromium.launch({headless: true, executablePath, args: ['--disable-background-networking', '--disable-component-update', '--disable-sync', '--no-first-run']});
  const context = await offlineContext({viewport: {width: 1440, height: 1000}, serviceWorkers: 'block'});
  desktop = await context.newPage(); desktop.setDefaultTimeout(15000); watch(desktop, 'desktop');
  let candidateCid, candidateRid, memoryCid, memoryRid, beforeMemory, updatedMemory, restoredMemory;

  if (!await stage('real-owner-config-and-53-empty-chats', async () => {
    await loadedChat(desktop); assert.equal(await desktop.locator('#chat-root').getAttribute('data-owner-user-id'), '12');
    assert.equal(await desktop.locator('#chat-list > button').count(), 50); await desktop.click('#chat-more');
    await desktop.waitForFunction(() => document.querySelectorAll('#chat-list > button').length === 53);
    const config = await api(desktop, '/config'); assert.equal(config.schema_version, 1); assert.equal(config.features.multiple_candidates, true); assert.equal(config.features.auto_memory, true);
    assert.deepEqual(config.features.protocols, ['chat_completions', 'responses', 'anthropic']);
    assert.equal(modelCalls.length, 0); evidence.config = config.features;
  })) return;

  if (!await stage('legacy-api-native-n3-safe-public-reasoning-still-renders-without-ui-settings', async () => {
    candidateCid = await newChat(desktop); await chooseModel(desktop);
    assert.equal(await desktop.locator('#chat-candidate-count, #chat-context-strategy, #chat-web-enabled, #chat-auto-memory').count(), 0);
    assert.equal(await desktop.locator('#chat-defaults-info, #chat-cost-notice, #chat-model-info, .chat-runtime-details, #chat-selection a, #chat-selection p').count(), 0);
    await seedLegacyCandidates(desktop, candidateCid); await finished(desktop, 1);
    assert.equal(await desktop.locator('.chat-candidate').count(), 3); assert.equal(modelCalls.length, 1); assert.equal(modelCalls[0].n, 3); assert.equal(modelCalls[0].candidate_count, 3);
    for (let i = 0; i < 3; i++) {
      const card = desktop.locator(`.chat-candidate[data-candidate="${i}"]`), text = await card.textContent();
      assert(text.includes(`v2-candidate-${i + 1}`)); assert(text.includes(`v2-raw-${i + 1}`)); assert(text.includes(`v2-summary-${i + 1}`));
      for (let j = 0; j < 3; j++) if (j !== i) assert(!text.includes(`v2-candidate-${j + 1}`));
      assert.equal(await card.locator('.chat-reasoning').count(), 2); assert.equal(await card.locator('.chat-reasoning[open]').count(), 0);
      const labels = await card.locator('.chat-reasoning > summary').allTextContents(); assert(labels.some(s => s.includes('raw / reasoning_content'))); assert(labels.some(s => s.includes('summary / reasoning')));
    }
    const events = await allEvents(desktop, candidateCid); candidateRid = events.find(e => e.type === 'run.accepted').run_id;
    const accepted = events.find(e => e.type === 'run.accepted').data; assert.equal(accepted.protocol, 'chat_completions'); assert.equal(accepted.options.candidate_count, 3); assert.equal(accepted.options.auto_memory ?? false, false);
    assert.equal(runPosts()[0].body.auto_memory, false); assert.equal(runPosts()[0].body.web_enabled, false);
    const dispatch = events.filter(e => e.type === 'run.dispatching'); assert.equal(dispatch.length, 1); assert.equal(dispatch[0].data.purpose, 'main'); assert.equal(dispatch[0].data.candidate_message_ids.length, 3);
    assert.equal(events.filter(e => e.type === 'assistant.message').length, 3); assert.equal(events.filter(e => e.type === 'assistant.reasoning').length, 6);
    assert.equal(events.filter(e => e.type === 'assistant.protocol').length, 3); assert(events.filter(e => e.type === 'assistant.protocol').every(e => e.data.state.reasoning_content.startsWith(OPAQUE)));
    const run = (await api(desktop, `/conversations/${candidateCid}/runs/${candidateRid}`)).run; assert.equal(run.model_requests, 1); assert.equal(run.main_model_requests, 1); assert.equal(run.aux_model_requests, 0);
    assert.equal(run.usage.total_tokens, 25); assert.equal(runPosts().length, 1); assert.equal(await desktop.locator('#chat-send').isDisabled(), true);
    await desktop.locator('.chat-candidate[data-candidate="1"] .chat-reasoning > summary').first().click();
    await desktop.locator('.chat-candidate[data-candidate="1"] .chat-reasoning > summary').last().click();
    await safeUI(desktop); await noOverflow(desktop, 'desktop-candidates'); await snap(desktop, 'desktop-three-candidates-reasoning');
    evidence.native_candidates = {conversation_id: candidateCid, run_id: candidateRid, n: 3, actual_requests: 1, total_tokens: 25};
  })) return;

  if (!await stage('must-select-before-followup-and-backend-uses-only-selected-branch', async () => {
    const posts = runPosts().length, calls = modelCalls.length;
    await desktop.fill('#chat-text', '选择前不得发送这段合成草稿。');
    await desktop.evaluate(async () => { document.querySelector('#chat-compose').dispatchEvent(new Event('submit', {bubbles: true, cancelable: true})); await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))); });
    assert.equal(runPosts().length, posts); assert.equal(modelCalls.length, calls); assert.match(await desktop.locator('#chat-status').innerText(), /先选择/);
    const response = await write(desktop, 'POST', `/runs/${candidateRid}/candidate`, () => desktop.locator('.chat-candidate[data-candidate="1"] .chat-candidate-select').click());
    const value = await response.json(); assert.equal(value.run.selected_candidate, 1); await enabled(desktop, '#chat-send'); assert.equal(modelCalls.length, calls);
    await chooseModel(desktop); await desktop.fill('#chat-text', '仅沿第二个已选分支继续。');
    await write(desktop, 'POST', '/runs', () => desktop.click('#chat-send')); await finished(desktop, 2);
    assert.equal(modelCalls.length, calls + 2); const call = modelCalls.at(-2);
    assert.equal(modelCalls.at(-1).phase, 'memory');
    assert.equal(runPosts().at(-1).body.candidate_count, 1); assert.equal(runPosts().at(-1).body.context_strategy, 'adaptive'); assert.equal(runPosts().at(-1).body.auto_memory, true);
    assert.equal(call.n, 1); assert.equal(call.assistant_contents.length, 1); assert(call.assistant_contents[0].startsWith('v2-candidate-2 '));
    assert.deepEqual(call.native_markers, [OPAQUE + '2']);
    const events = await allEvents(desktop, candidateCid), accepts = events.filter(e => e.type === 'run.accepted');
    assert.deepEqual(accepts[1].data.context_from_run_ids, [candidateRid]); assert.equal(events.find(e => e.type === 'candidate.selected').data.candidate_index, 1);
    assert.equal(await desktop.locator('.chat-candidate-select:enabled').count(), 0); await safeUI(desktop);
    evidence.selected_branch = {candidate_index: 1, assistant_messages_in_next_request: call.assistant_contents.length, only_selected_native_state: true};
  })) return;

  if (!await stage('reload-retains-selection-public-reasoning-no-opaque-ui-no-model-call', async () => {
    const count = modelCalls.length; await desktop.reload(); await finished(desktop, 2);
    assert.equal(await desktop.locator('.chat-candidate').count(), 3); assert.equal(await desktop.locator('.chat-candidate .chat-reasoning').count(), 6);
    assert.match(await desktop.locator('.chat-candidate[data-candidate="1"] h3').innerText(), /已选为后续分支/);
    for (let i = 1; i <= 3; i++) { const text = await desktop.locator('#chat-messages').textContent(); assert(text.includes(`v2-raw-${i}`)); assert(text.includes(`v2-summary-${i}`)); }
    assert.equal(modelCalls.length, count); await safeUI(desktop); await snap(desktop, 'desktop-selected-branch-reloaded');
  })) return;

  if (!await stage('default-auto-memory-without-checkbox-real-intent-audit-cas-and-purpose-usage', async () => {
    await desktop.goto(origin + '/cockpit/chat/settings/'); await enabled(desktop, '#settings-content');
    await desktop.fill('#settings-content', MEMORY); await desktop.click('#settings-save');
    await desktop.waitForFunction(() => document.querySelector('#settings-status').textContent.startsWith('已保存') && !document.querySelector('#settings-content').disabled);
    beforeMemory = await api(desktop, '/context'); assert.equal(beforeMemory.profiles.find(p => p.name === 'MEMORY.md').content, MEMORY);
    const count = modelCalls.length;
    await loadedChat(desktop); memoryCid = await newChat(desktop); await chooseModel(desktop);
    assert.equal(await desktop.locator('#chat-auto-memory').count(), 0);
    await desktop.fill('#chat-text', FACT);
    await write(desktop, 'POST', '/runs', () => desktop.click('#chat-send')); await finished(desktop, 1);
    await desktop.locator('.chat-memory-audit').waitFor(); await desktop.locator('.chat-memory-audit > summary').click();
    const events = await allEvents(desktop, memoryCid), accepted = events.find(e => e.type === 'run.accepted'); memoryRid = accepted.run_id;
    assert.equal(accepted.data.options.auto_memory, true); assert.equal(accepted.data.context_snapshot.context_revision, beforeMemory.context_revision);
    const proposed = events.find(e => e.type === 'memory.proposed'), updated = events.find(e => e.type === 'memory.updated'); assert(proposed && updated); assert(proposed.seq < updated.seq);
    const audit = updated.data.audit; assert.equal(audit.owner_user_id, 12); assert.equal(audit.changed, true); assert.equal(audit.before.content, MEMORY); assert(audit.after.content.includes(FACT));
    assert.deepEqual(audit.source_ids, [accepted.data.message_id]); assert(audit.fact_ids.every(id => /^fact_[a-f0-9]{24}$/.test(id)));
    assert.deepEqual(proposed.data.intent.source_message_ids, [accepted.data.message_id]);
    updatedMemory = await api(desktop, '/context'); assert.equal(updatedMemory.context_revision, beforeMemory.context_revision + 1);
    assert.equal(updatedMemory.profiles.find(p => p.name === 'MEMORY.md').content, audit.after.content);
    const diff = await desktop.locator('.chat-memory-audit pre').allTextContents(); assert.deepEqual(diff, [MEMORY, audit.after.content]);
    assert.match(await desktop.locator('#chat-messages').textContent(), /不是 MEMORY 已写入的确认/);
    assert.equal(modelCalls.length, count + 2); assert.deepEqual(modelCalls.slice(count).map(c => c.phase), ['main', 'memory']); assert.equal(modelCalls.at(-1).source_message_id, accepted.data.message_id);
    assert.deepEqual(events.filter(e => e.type === 'run.dispatching').map(e => e.data.purpose), ['main', 'memory']);
    assert.deepEqual(events.filter(e => e.type === 'run.usage').map(e => [e.data.purpose, e.data.total_tokens]), [['main', 15], ['memory', 37]]);
    const usage = await desktop.locator('.chat-request-usage').innerText(); assert.match(usage, /主回答 1 次请求 · 15 Token/); assert.match(usage, /自动记忆 1 次请求 · 37 Token/);
    const run = (await api(desktop, `/conversations/${memoryCid}/runs/${memoryRid}`)).run; assert.equal(run.main_model_requests, 1); assert.equal(run.aux_model_requests, 1); assert.equal(run.usage.total_tokens, 52);
    await desktop.waitForFunction(revision => document.querySelector('#chat-context-info').textContent.includes(`个人设置 v${revision}`), updatedMemory.context_revision);
    await safeUI(desktop); await noOverflow(desktop, 'desktop-memory-audit'); await snap(desktop, 'desktop-memory-audit-before-undo');
    evidence.memory_update = {conversation_id: memoryCid, run_id: memoryRid, owner_user_id: audit.owner_user_id, source_message_id: accepted.data.message_id, before_context_revision: beforeMemory.context_revision, after_context_revision: updatedMemory.context_revision, purposes: ['main', 'memory'], total_tokens: 52};
  })) return;

  if (!await stage('explicit-memory-undo-fetches-current-revisions-preserves-manual-memory-no-model-call', async () => {
    const count = modelCalls.length;
    // An unrelated profile update makes the UI's context older than live state.
    // The undo click must GET current context, not reuse the run/audit revision.
    const soul = updatedMemory.profiles.find(p => p.name === 'SOUL.md');
    await api(desktop, '/profiles/SOUL.md', {method: 'PATCH', body: {context_revision: updatedMemory.context_revision, expected_revision: soul.revision, content: SOUL}});
    const live = await api(desktop, '/context'); assert.equal(live.context_revision, updatedMemory.context_revision + 1);
    const meta = (await api(desktop, `/conversations/${memoryCid}`)).conversation, offset = requests.length;
    await write(desktop, 'POST', `/runs/${memoryRid}/memory/undo`, () => desktop.locator('.chat-memory-undo').click());
    await desktop.waitForFunction(() => document.querySelector('.chat-memory-audit > summary')?.textContent.includes('已撤销') && !document.querySelector('#chat-text').disabled);
    restoredMemory = await api(desktop, '/context'); assert.equal(restoredMemory.context_revision, live.context_revision + 1);
    assert.equal(restoredMemory.profiles.find(p => p.name === 'MEMORY.md').content, MEMORY); assert.equal(restoredMemory.profiles.find(p => p.name === 'SOUL.md').content, SOUL);
    assert.equal(modelCalls.length, count); assert.equal(await desktop.locator('.chat-memory-undo').isDisabled(), true);
    const subsequent = requests.slice(offset), undoIndex = subsequent.findIndex(r => r.method === 'POST' && r.path.endsWith('/memory/undo'));
    assert(undoIndex > 0); assert(subsequent.slice(0, undoIndex).some(r => r.method === 'GET' && r.path === '/cockpit/api/chat/context'));
    assert(subsequent.slice(0, undoIndex).some(r => r.method === 'GET' && r.path === `/cockpit/api/chat/conversations/${memoryCid}`));
    assert.deepEqual(subsequent[undoIndex].body, {context_revision: live.context_revision, expected_revision: meta.revision});
    assert(subsequent.slice(undoIndex + 1).some(r => r.method === 'GET' && r.path === '/cockpit/api/chat/context'));
    const events = await allEvents(desktop, memoryCid), reverted = events.find(e => e.type === 'memory.reverted'); assert(reverted.seq > events.find(e => e.type === 'run.terminal').seq); assert.equal(reverted.data.context_revision, restoredMemory.context_revision);
    assert.equal(subsequent.filter(r => r.method === 'POST' && r.path.endsWith('/memory/undo')).length, 1);
    await desktop.waitForFunction(revision => document.querySelector('#chat-context-info').textContent.includes(`个人设置 v${revision}`), restoredMemory.context_revision);
    await noOverflow(desktop, 'desktop-memory-restored'); await snap(desktop, 'desktop-memory-audit-after-undo');
    evidence.memory_undo = {frozen_context_revision: beforeMemory.context_revision, live_context_revision: live.context_revision, restored_context_revision: restoredMemory.context_revision, explicit_posts: 1, model_calls: 0};
  })) return;

  if (!await stage('next-send-uses-refreshed-context-and-undo-read-history-never-generate', async () => {
    const count = modelCalls.length; await desktop.fill('#chat-text', '撤销后继续普通聊天，这里没有新的长期偏好。');
    await write(desktop, 'POST', '/runs', () => desktop.click('#chat-send')); await finished(desktop, 2);
    const request = runPosts().at(-1); assert.equal(request.body.context_revision, restoredMemory.context_revision); assert.equal(request.body.auto_memory, true);
    assert.equal(modelCalls.length, count + 2); assert.equal(modelCalls.at(-2).manual_memory_present, true); assert.equal(modelCalls.at(-2).managed_fact_present, false);
    assert.equal((await api(desktop, '/context')).context_revision, restoredMemory.context_revision, 'No durable fact means no Memory write');
    const events = await allEvents(desktop, memoryCid), accepted = events.filter(e => e.type === 'run.accepted').at(-1); assert.equal(accepted.data.context_snapshot.context_revision, restoredMemory.context_revision);
    const readCount = modelCalls.length; await desktop.reload(); await finished(desktop, 2); await allEvents(desktop, memoryCid); await api(desktop, '/context');
    assert.equal(modelCalls.length, readCount); await safeUI(desktop);
    evidence.fresh_continuation = {context_revision: request.body.context_revision, manual_memory_present: true, removed_managed_fact_absent: true};
  })) return;

  await stage('mobile-390px-candidates-memory-audit-settings-no-horizontal-overflow', async () => {
    const mobileContext = await offlineContext({viewport: {width: 390, height: 844}, isMobile: true, hasTouch: true, deviceScaleFactor: 1, serviceWorkers: 'block'});
    try {
      const mobile = await mobileContext.newPage(); mobile.setDefaultTimeout(15000); watch(mobile, 'mobile'); const count = modelCalls.length, layoutFailures = [];
      await loadedChat(mobile, candidateCid); await finished(mobile, 2); assert.equal(await mobile.locator('.chat-candidate').count(), 3);
      await mobile.locator('.chat-candidate[data-candidate="1"] .chat-reasoning > summary').first().click();
      await safeUI(mobile); await snap(mobile, 'mobile-390-candidates-reasoning');
      try { await noOverflow(mobile, 'mobile-candidates-390'); } catch (error) { layoutFailures.push(error.message); }
      await loadedChat(mobile, memoryCid); await finished(mobile, 2); await mobile.locator('.chat-memory-audit > summary').click();
      assert.match(await mobile.locator('.chat-memory-audit > summary').innerText(), /已撤销/);
      await safeUI(mobile); await snap(mobile, 'mobile-390-memory-audit');
      try { await noOverflow(mobile, 'mobile-memory-390'); } catch (error) { layoutFailures.push(error.message); }
      await mobile.goto(origin + '/cockpit/chat/settings/'); await enabled(mobile, '#settings-content'); assert.equal(await mobile.locator('#settings-content').inputValue(), MEMORY);
      await snap(mobile, 'mobile-390-manual-memory-restored');
      try { await noOverflow(mobile, 'mobile-settings-390'); } catch (error) { layoutFailures.push(error.message); }
      assert.equal(modelCalls.length, count);
      assert.equal(layoutFailures.length, 0, layoutFailures.join('\n'));
    } finally { await mobileContext.close(); }
  });

  await stage('actual-routes-purpose-accounting-zero-off-origin-and-source-unchanged', async () => {
    assert.deepEqual(errors, []); assert.deepEqual(requests.filter(r => r.status >= 400 || r.error), []);
    // _state deliberately probes the long writer lease, catches BUSY and serves
    // its published prefix. The inherited diagnostic records that expected
    // internal probe too. Retain every record, but reject HTTP-visible or
    // unrelated contention rather than treating the documented probe as failure.
    const unexpectedLocks = lockFailures.filter(l => l.code !== 'CHAT_CONVERSATION_BUSY' || l.requester?.path !== null || l.owner?.method !== 'POST' || !/\/runs$/.test(l.owner?.path || '') || !l.stack.some(s => s.file === 'storage.py' && s.function === '_state') || !l.stack.some(s => s.file === 'runner.py'));
    assert.equal(unexpectedLocks.length, 0, `Unexpected lock failures: ${JSON.stringify(unexpectedLocks.slice(0, 2))}`);
    evidence.expected_internal_writer_lease_probes = lockFailures.length;
    assert.deepEqual(modelCalls.map(c => c.phase), ['candidates', 'continuation', 'memory', 'main', 'memory', 'continuation', 'memory']); assert.deepEqual(modelCalls.map(c => c.n), [3, 1, 1, 1, 1, 1, 1]);
    assert.equal(runPosts().length, 4); assert(runPosts().every(r => r.content_type.startsWith('text/event-stream')));
    const candidatePosts = requests.filter(r => r.method === 'POST' && r.path.endsWith('/candidate')), undoPosts = requests.filter(r => r.method === 'POST' && r.path.endsWith('/memory/undo'));
    assert.equal(candidatePosts.length, 1); assert.equal(undoPosts.length, 1);
    assert(requests.filter(r => r.role === 'chat').every(r => r.session_stamped && (r.method === 'GET' || r.write_marker)));
    assert.deepEqual(sourceFingerprint(), baseline, 'All runtime files and original tests remain unchanged during acceptance');
    evidence.routes = {run_posts: 4, candidate_posts: 1, memory_undo_posts: 1, actual_model_requests: 7, auxiliary_memory_requests: 3, off_origin_requests: 0};
  });
  await context.close();
}

async function stopFixture(record) {
  const {child} = record; if (child.exitCode !== null || child.signalCode !== null) return;
  const exited = once(child, 'exit'); child.kill('SIGTERM'); const timeout = setTimeout(() => child.kill('SIGKILL'), 8000);
  try { await exited; } finally { clearTimeout(timeout); }
}
let cleanupTask;
function cleanup() {
  if (!cleanupTask) cleanupTask = (async () => {
    if (browser) await browser.close().catch(() => {});
    if (gateway) { gateway.closeAllConnections(); await new Promise(resolve => gateway.close(resolve)); }
    await Promise.all(children.map(record => stopFixture(record).catch(error => failures.push({name: 'cleanup', message: error.message}))));
    fs.rmSync(temp, {recursive: true, force: true});
  })();
  return cleanupTask;
}
process.once('SIGINT', () => { cleanup().finally(() => process.exit(130)); });
process.once('SIGTERM', () => { cleanup().finally(() => process.exit(143)); });
main().catch(error => { failures.push({name: 'harness', message: error.stack}); console.error(error); }).finally(async () => {
  await cleanup(); const ending = sourceFingerprint();
  const sourceChanges = [...new Set([...Object.keys(baseline), ...Object.keys(ending)])].filter(name => baseline[name] !== ending[name]);
  if (sourceChanges.length) failures.push({name: 'source-changed-during-run', files: sourceChanges});
  const result = {passed: failures.length === 0 && checks.length === 9 && checks.every(c => c.passed), offline: true, real_chromium: true, real_flask_chat_service: true, synthetic_only: true, checks, failures, evidence, browser_errors: errors, model_calls: modelCalls, http_requests: requests, lock_failures: lockFailures, source_fingerprint: baseline, source_changes: sourceChanges, screenshots,
    cleanup: {temporary_data_removed: !fs.existsSync(temp), fixtures_stopped: children.every(({child}) => child.exitCode !== null || child.signalCode !== null)}};
  if (!result.cleanup.temporary_data_removed || !result.cleanup.fixtures_stopped) { result.passed = false; failures.push({name: 'cleanup-incomplete'}); }
  fs.mkdirSync(artifacts, {recursive: true}); fs.writeFileSync(path.join(artifacts, 'result.json'), JSON.stringify(result, null, 2));
  console.log(JSON.stringify({passed: result.passed, checks: checks.length, failures: failures.map(({name, message, files}) => ({name, message, ...(files ? {files} : {})})), screenshots, cleanup: result.cleanup}, null, 2));
  process.exitCode = result.passed ? 0 : 1;
});
