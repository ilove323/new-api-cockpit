/* Real Chromium + real Flask ChatService, entirely local and synthetic.
 * Install Playwright in a local development environment, then run:
 *   node tests/browser/chat-integration.cjs
 * Only these tests bypass identity. No mocked chat HTTP responses, paid relay,
 * user Chrome profile, deployed site, database or real API key is permitted.
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
const executablePath = process.env.COCKPIT_BROWSER_EXECUTABLE || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const artifacts = '/tmp/new-api-cockpit-chat-browser-artifacts';
const temp = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'cockpit-chat-browser-')));
const MEMORY = '浏览器整合测试：偏好中文，先结论后依据。仅为合成资料。';
const SKILL = '---\nname: browser-summary\ndescription: "整理合成测试材料"\n---\n只使用本轮合成资料，输出简短结论和依据。\n';
const UPLOAD = '合成输入材料\n本文件仅用于本地浏览器整合测试，不包含真实用户资料。\n';
const OUTPUT = '# 合成报告\n\n浏览器离线文件助手生成的新输出。\n上传原件不被覆盖；不调用真实模型。\n';
const children = [], requests = [], errors = [], failures = [], checks = [], screenshots = [], modelCalls = [];
const lockFailures = [];
let gateway, browser, desktop, origin;

function sourceFingerprint() {
  const result = {};
  for (const entry of ['src/new_api_cockpit/app.py', 'src/new_api_cockpit/composition.py',
    'src/new_api_cockpit/features/chat', 'src/new_api_cockpit/static/chat', 'src/new_api_cockpit/templates/chat']) {
    const absolute = path.join(root, entry);
    const files = fs.statSync(absolute).isDirectory() ? fs.readdirSync(absolute).filter(name => /\.(py|js|css|html)$/.test(name)).map(name => path.join(entry, name)) : [entry];
    for (const name of files) result[name] = createHash('sha256').update(fs.readFileSync(path.join(root, name))).digest('hex');
  }
  return result;
}
const baseline = sourceFingerprint();

function fixture(role) {
  const dataDir = path.join(temp, role);
  fs.mkdirSync(dataDir, {mode: 0o700});
  const child = spawn(python, ['-B', path.join(__dirname, 'chat_fixture.py'), '--role', role, '--data-dir', dataDir], {
    cwd: root, stdio: ['ignore', 'pipe', 'pipe'],
    env: {PATH: process.env.PATH || '/usr/bin:/bin', LANG: 'en_US.UTF-8', TZ: 'Asia/Shanghai',
      PYTHONPATH: [path.join(root, 'src'), path.join(root, 'tests')].join(path.delimiter),
      PYTHONUNBUFFERED: '1', PYTHONDONTWRITEBYTECODE: '1'},
  });
  const record = {child, role, stderr: '', messages: []}; children.push(record);
  child.stderr.on('data', chunk => { record.stderr = (record.stderr + chunk.toString()).slice(-24000); });
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => { reject(new Error(`${role} fixture startup timed out: ${record.stderr}`)); }, 30000);
    let buffer = '';
    child.stdout.on('data', chunk => {
      buffer += chunk.toString();
      while (buffer.includes('\n')) {
        const index = buffer.indexOf('\n'), line = buffer.slice(0, index); buffer = buffer.slice(index + 1);
        if (!line.startsWith('CHAT_BROWSER_FIXTURE ')) continue;
        const message = JSON.parse(line.slice('CHAT_BROWSER_FIXTURE '.length)); record.messages.push(message);
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
    const entry = {method: req.method, path: req.url, role: req.url.startsWith('/cockpit/api/chat/') ? 'chat' : 'web'};
    requests.push(entry);
    const target = http.request({hostname: '127.0.0.1', port: entry.role === 'chat' ? chat.port : web.port,
      method: req.method, path: req.url, headers: {...req.headers, connection: 'close'}}, upstream => {
      entry.status = upstream.statusCode;
      entry.content_type = upstream.headers['content-type'] || '';
      if (upstream.statusCode >= 400) {
        entry.response_body = '';
        upstream.on('data', chunk => { entry.response_body = (entry.response_body + chunk.toString()).slice(0, 8192); });
      }
      res.writeHead(upstream.statusCode, upstream.headers);
      upstream.pipe(res);
    });
    target.on('error', error => { entry.error = error.message; if (!res.headersSent) res.writeHead(502); res.end(); });
    req.on('aborted', () => target.destroy());
    res.on('close', () => { if (!res.writableFinished) target.destroy(); });
    req.pipe(target);
  });
  gateway.listen(0, '127.0.0.1'); await once(gateway, 'listening');
  return `http://127.0.0.1:${gateway.address().port}`;
}

function watch(page, label) {
  page.on('pageerror', error => errors.push({page: label, type: 'pageerror', message: error.message}));
  page.on('console', message => { if (message.type() === 'error') errors.push({page: label, type: 'console', message: message.text()}); });
  page.on('dialog', dialog => dialog.dismiss());
}

async function snap(page, name) {
  const file = path.join(artifacts, name + '.png');
  await page.screenshot({path: file, fullPage: true, animations: 'disabled'});
  screenshots.push(file);
}

async function stage(name, action) {
  try { await action(); checks.push({name, passed: true}); console.log('PASS ' + name); return true; }
  catch (error) {
    const diagnostic = {name, message: error.message};
    diagnostic.http_errors = requests.filter(r => r.status >= 400 || r.error).slice(-4);
    diagnostic.lock_failures = lockFailures.slice(-4);
    if (desktop && !desktop.isClosed()) {
      diagnostic.ui = await desktop.locator('#chat-status, #chat-list-status, #settings-status, #chat-file-status').allTextContents().catch(() => []);
      await snap(desktop, 'failure-' + name).catch(() => {});
    }
    failures.push(diagnostic); checks.push({name, passed: false}); console.error('FAIL ' + name + ': ' + error.message); return false;
  }
}

async function enabled(page, selector) {
  await page.waitForFunction(selector => { const element = document.querySelector(selector); return element && !element.disabled; }, selector);
}

async function loadedChat(page, url = origin + '/cockpit/chat/') {
  await page.goto(url);
  await page.waitForFunction(() => document.querySelector('#chat-list-info')?.textContent.includes('已显示') && document.querySelector('#chat-context-info')?.textContent.includes('个人设置'));
  // renderList also paints the initial 0/0 placeholder before its request ends.
  // Do not navigate again while that genuine GET is still in flight.
  await enabled(page, '#chat-list-refresh');
}

async function json(page, suffix) {
  return page.evaluate(async suffix => {
    const response = await window.fetch('/cockpit/api/chat' + suffix);
    const value = await response.json();
    if (!response.ok) throw new Error(`HTTP ${response.status}: ${JSON.stringify(value)}`);
    return value;
  }, suffix);
}

async function chooseModel(page) {
  await enabled(page, '#chat-token'); await page.selectOption('#chat-token', '7');
  await page.click('#chat-model-load');
  await page.waitForFunction(() => document.querySelector('#chat-model')?.value === 'test-chat' && !document.querySelector('#chat-send').disabled);
}

async function newChat(page) {
  const previous = new URL(page.url()).searchParams.get('conversation');
  await page.click('#chat-new');
  await page.waitForFunction(previous => { const cid = new URL(location.href).searchParams.get('conversation'); return cid && cid !== previous && !document.querySelector('#chat-text').disabled; }, previous);
  await enabled(page, '#chat-new');
  return new URL(page.url()).searchParams.get('conversation');
}

async function finished(page, expectedRuns) {
  await page.waitForFunction(expectedRuns => {
    const rows = [...document.querySelectorAll('#chat-messages .chat-run')];
    return rows.length === expectedRuns && rows.every(row => row.querySelector('.chat-run-footer strong')?.textContent === '已完成') && !document.querySelector('#chat-text').disabled;
  }, expectedRuns);
  await enabled(page, '#chat-list-refresh');
}

async function expectWrite(page, pathSuffix, action, method = 'POST', timeout = 12000) {
  const response = page.waitForResponse(response => response.request().method() === method && new URL(response.url()).pathname.endsWith(pathSuffix), {timeout});
  await action();
  const result = await response;
  if (!result.ok()) assert.fail(`${method} ${pathSuffix}: HTTP ${result.status()} ${await result.text()}`);
}

async function noOverflow(page, label) {
  const dimensions = await page.evaluate(() => ({width: innerWidth, scroll: document.documentElement.scrollWidth,
    offending: [...document.querySelectorAll('main *')].filter(node => {
      const box = node.getBoundingClientRect(); return box.width > 0 && box.right > innerWidth + 1;
    }).slice(0, 10).map(node => ({tag: node.tagName, id: node.id, className: node.className}))}));
  assert(dimensions.scroll <= dimensions.width + 1, `${label} horizontal overflow: ${JSON.stringify(dimensions)}`);
}

async function stopFixture(record) {
  const {child} = record;
  if (child.exitCode !== null) return;
  const exited = once(child, 'exit'); child.kill('SIGTERM');
  const timeout = setTimeout(() => child.kill('SIGKILL'), 8000);
  try { await exited; } finally { clearTimeout(timeout); }
}

async function main() {
  fs.mkdirSync(artifacts, {recursive: true});
  let cid, sid, fileRun, initialRuns = 0;
  const [web, chat] = await Promise.all([fixture('web'), fixture('chat')]);
  assert.equal(chat.seeded, 53); assert.equal(web.seeded, 0);
  origin = await proxy(web, chat);
  browser = await chromium.launch({headless: true, executablePath,
    args: ['--disable-background-networking', '--disable-component-update', '--disable-sync', '--no-first-run']});
  const context = await browser.newContext({viewport: {width: 1440, height: 1000}, locale: 'zh-CN', acceptDownloads: true});
  // Route only blocks outside requests; no chat response is fabricated here.
  await context.route('**/*', route => {
    const url = new URL(route.request().url());
    if (url.origin === origin || ['blob:', 'data:'].includes(url.protocol)) return route.continue();
    errors.push({type: 'off-origin', url: url.href}); return route.abort();
  });
  desktop = await context.newPage(); desktop.setDefaultTimeout(12000); watch(desktop, 'desktop');

  await stage('all-53-history-pagination', async () => {
    await loadedChat(desktop);
    assert.equal(await desktop.locator('#chat-list > button').count(), 50);
    assert.equal(modelCalls.length, 0);
    await desktop.click('#chat-more');
    await desktop.waitForFunction(() => document.querySelectorAll('#chat-list > button').length === 53);
    assert.equal(await desktop.locator('#chat-more').isVisible(), false);
    const rows = await desktop.locator('#chat-list > button').allTextContents();
    for (let i = 1; i <= 53; i++) assert(rows.some(row => row.includes(`离线空聊天 ${String(i).padStart(2, '0')}`)));
    await noOverflow(desktop, 'desktop-history'); await snap(desktop, 'desktop-history-53');
  });

  await stage('empty-page-file-picker-auto-creates-chat-and-persists-50-MB-without-generation', async () => {
    assert.equal(new URL(desktop.url()).searchParams.get('conversation'), null);
    await enabled(desktop, '#chat-upload');
    assert((await desktop.locator('#chat-upload-hint').innerText()).includes('自动新建'));
    assert((await desktop.locator('#chat-file-limits').innerText()).includes('50 MB'));
    const before = modelCalls.length, creates = requests.filter(r => r.method === 'POST' && r.path === '/cockpit/api/chat/conversations').length;
    const file = Buffer.alloc(50 * 1048576, 'x'), digest = createHash('sha256').update(file).digest('hex');
    // Playwright's in-memory transport is capped at 50,000,000 bytes, not 50 MiB.
    const source = path.join(temp, 'fifty-megabytes.txt'); fs.writeFileSync(source, file);
    await expectWrite(desktop, '/files', () => desktop.setInputFiles('#chat-upload', source), 'POST', 60000);
    await desktop.waitForFunction(() => document.querySelector('#chat-files-list').textContent.includes('fifty-megabytes.txt') && !document.querySelector('#chat-upload').disabled);
    const created = new URL(desktop.url()).searchParams.get('conversation'); assert(created);
    const files = (await json(desktop, `/conversations/${created}/files`)).files;
    assert.equal(files.length, 1); assert.equal(files[0].size_bytes, file.length); assert.equal(files[0].sha256, digest);
    assert.equal(requests.filter(r => r.method === 'POST' && r.path === '/cockpit/api/chat/conversations').length, creates + 1);
    const content = await desktop.evaluate(async url => {
      const response = await fetch(url); if (!response.ok) throw new Error('Download failed');
      const bytes = await response.arrayBuffer(); const hash = await crypto.subtle.digest('SHA-256', bytes);
      return {size: bytes.byteLength, sha256: [...new Uint8Array(hash)].map(b => b.toString(16).padStart(2, '0')).join('')};
    }, `/cockpit/api/chat/conversations/${created}/files/${files[0].id}/download`);
    assert.deepEqual(content, {size: file.length, sha256: digest});
    assert.equal(modelCalls.length, before); assert.equal(requests.filter(r => /\/runs$/.test(r.path) && r.method === 'POST').length, 0);
    await noOverflow(desktop, 'desktop-50-MB-upload'); await snap(desktop, 'desktop-50-MB-upload');
  });

  await stage('ordinary-chat-create-send-revisit', async () => {
    if (!desktop.url().includes('/cockpit/chat/')) await loadedChat(desktop);
    cid = await newChat(desktop); await chooseModel(desktop);
    assert.equal(await desktop.locator('#chat-candidate-count, #chat-context-strategy, #chat-web-enabled, #chat-auto-memory').count(), 0);
    assert.equal(await desktop.locator('#chat-defaults-info, #chat-cost-notice, #chat-model-info, .chat-runtime-details, #chat-selection a, #chat-selection p').count(), 0);
    assert.equal(await desktop.getByText('管理我的 KEY', {exact: true}).count(), 0);
    await desktop.fill('#chat-text', '请确认这是普通聊天的合成测试。');
    await expectWrite(desktop, '/runs', () => desktop.click('#chat-send'));
    await finished(desktop, 1); initialRuns = 1;
    assert((await desktop.locator('#chat-messages').innerText()).includes('你好，浏览器离线整合回答。'));
    assert.equal(await desktop.locator('.chat-tool').count(), 0);
    const accepted = (await json(desktop, `/conversations/${cid}/events?after_seq=0&limit=200`)).events.find(e => e.type === 'run.accepted');
    assert.deepEqual({candidate_count: 1, web_enabled: false, ...accepted.data.options}, {candidate_count: 1, web_enabled: false, auto_memory: true, context_strategy: 'adaptive'});
    const counts = modelCalls.length;
    await desktop.click('#chat-list > button:not([aria-current])');
    await desktop.waitForFunction(() => document.querySelector('#chat-messages').textContent.includes('暂无消息'));
    await desktop.locator('#chat-list > button').filter({hasText: '请确认这是普通聊天的合成测试。'}).click();
    await finished(desktop, 1); assert.equal(modelCalls.length, counts);
    await noOverflow(desktop, 'desktop-chat'); await snap(desktop, 'desktop-ordinary-chat');
  });

  await stage('memory-save-skill-create-and-view', async () => {
    await desktop.goto(origin + '/cockpit/chat/settings/'); await enabled(desktop, '#settings-content');
    await desktop.fill('#settings-content', MEMORY); await desktop.click('#settings-save');
    await desktop.waitForFunction(() => document.querySelector('#settings-status').textContent.startsWith('已保存') && document.querySelector('#settings-context-info').textContent.includes('v1'));
    assert.equal(await desktop.locator('#settings-content').inputValue(), MEMORY);
    await snap(desktop, 'desktop-memory-saved');
    await desktop.click('#settings-skills-tab'); await desktop.click('#settings-new-skill');
    await desktop.fill('#settings-content', SKILL); await desktop.click('#settings-save');
    await desktop.waitForFunction(() => document.querySelector('#settings-status').textContent.startsWith('已保存') && document.querySelector('#settings-skill-list').textContent.includes('browser-summary'));
    await desktop.click('[data-profile="MEMORY.md"]');
    await desktop.click('#settings-skills-tab'); await desktop.locator('#settings-skill-list > button').click();
    await desktop.waitForFunction(() => document.querySelector('#settings-status').textContent.includes('Skill 原文已载入'));
    assert.equal(await desktop.locator('#settings-content').inputValue(), SKILL);
    const value = await json(desktop, '/context'); sid = value.skills[0].id;
    assert.equal(value.context_revision, 2);
    await noOverflow(desktop, 'desktop-settings'); await snap(desktop, 'desktop-skill-view');
  });

  await stage('explicit-skill-upload-and-file-assistant-output', async () => {
    await loadedChat(desktop, origin + '/cockpit/chat/' + (cid ? '?conversation=' + cid : ''));
    if (!cid) cid = await newChat(desktop);
    await enabled(desktop, '#chat-text'); await chooseModel(desktop);
    await desktop.selectOption('#chat-mode', 'files');
    assert.equal(await desktop.locator('#chat-skills-dialog').isVisible(), false);
    assert.equal(await desktop.locator('.chat-main #chat-skill-choices').count(), 0);
    assert.deepEqual((await json(desktop, `/conversations/${cid}`)).conversation.loaded_skill_ids, []);
    assert(sid, 'Skill creation must have succeeded');
    await desktop.click('#chat-skills-open');
    await desktop.locator('#chat-skill-choices').getByRole('button', {name: '查看正文', exact: true}).click();
    await desktop.locator('#chat-dialog[open]').waitFor();
    assert((await desktop.locator('#chat-dialog').innerText()).includes(SKILL));
    await desktop.locator('#chat-dialog').getByRole('button', {name: '关闭', exact: true}).click();
    const beforeLoadCalls = modelCalls.length;
    await expectWrite(desktop, `/conversations/${cid}`, () => desktop.getByRole('button', {name: '载入 browser-summary', exact: true}).click(), 'PATCH');
    await desktop.waitForFunction(() => !document.querySelector('#chat-skills-clear').disabled);
    assert.deepEqual((await json(desktop, `/conversations/${cid}`)).conversation.loaded_skill_ids, [sid]);
    assert.equal(modelCalls.length, beforeLoadCalls, 'Loading Skills must not call the model');
    assert((await desktop.locator('#chat-skill-order').innerText()).includes('browser-summary'));
    await snap(desktop, 'desktop-skills-window-loaded');
    await desktop.click('#chat-skills-close');
    await expectWrite(desktop, '/files', () => desktop.setInputFiles('#chat-upload', {name: 'synthetic-input.txt', mimeType: 'text/plain', buffer: Buffer.from(UPLOAD)}));
    await desktop.locator('#chat-files-list .chat-file').filter({hasText: 'synthetic-input.txt'}).waitFor();
    await enabled(desktop, '#chat-text');
    await desktop.locator('#chat-files-list .chat-file').filter({hasText: 'synthetic-input.txt'}).locator('input[type=checkbox]').check();
    await desktop.fill('#chat-text', '根据本轮合成附件，生成一个新的报告文件，不覆盖原件。');
    await expectWrite(desktop, '/runs', () => desktop.click('#chat-send')); await finished(desktop, initialRuns + 1);
    const rows = await json(desktop, `/conversations/${cid}/files`);
    assert.equal(rows.files.length, 2); assert.equal(rows.files.filter(f => f.kind === 'upload').length, 1); assert.equal(rows.files.filter(f => f.kind === 'output').length, 1);
    assert((await desktop.locator('#chat-messages').innerText()).includes('工具 write_file · 成功'));
    assert.deepEqual((await json(desktop, `/conversations/${cid}`)).conversation.loaded_skill_ids, [sid]);
    assert.match(await desktop.locator('#chat-skills-open').innerText(), /1/);
    for (const file of rows.files) {
      const downloadEvent = desktop.waitForEvent('download');
      await desktop.locator('#chat-files-list .chat-file').filter({hasText: file.name}).getByRole('button', {name: '下载', exact: true}).click();
      const download = await downloadEvent, filePath = await download.path();
      assert.equal(download.suggestedFilename(), file.name);
      assert.equal(fs.readFileSync(filePath, 'utf8'), file.kind === 'upload' ? UPLOAD : OUTPUT);
    }
    const page = await json(desktop, `/conversations/${cid}/events?after_seq=0&limit=200`);
    fileRun = page.events.filter(e => e.type === 'run.accepted').at(-1).run_id;
    const accept = page.events.find(e => e.type === 'run.accepted' && e.run_id === fileRun);
    assert.deepEqual({candidate_count: 1, web_enabled: false, ...accept.data.options}, {candidate_count: 1, web_enabled: true, auto_memory: true, context_strategy: 'adaptive'});
    assert.deepEqual(accept.data.context_snapshot.skill_ids, [sid]);
    assert.equal(accept.data.attachment_ids.length, 1);
    await noOverflow(desktop, 'desktop-file-assistant'); await snap(desktop, 'desktop-file-assistant');
  });

  await stage('frozen-context-and-refresh-persistence', async () => {
    assert(fileRun, 'File assistant must have accepted a run');
    const snapshot = (await json(desktop, `/conversations/${cid}/runs/${fileRun}/context`)).context_snapshot;
    assert.equal(snapshot.profiles.find(p => p.name === 'MEMORY.md').content, MEMORY);
    assert.equal(snapshot.skills[0].content, SKILL); assert.deepEqual(snapshot.skill_ids, [sid]);
    await desktop.locator(`#chat-messages .chat-run[data-run="${fileRun}"]`).getByRole('button', {name: /查看上下文/}).click();
    await desktop.locator('#chat-dialog[open]').waitFor();
    await desktop.locator('#chat-dialog details').filter({hasText: 'MEMORY.md'}).locator('summary').click();
    await desktop.locator('#chat-dialog details').filter({hasText: 'browser-summary'}).locator('summary').click();
    assert((await desktop.locator('#chat-dialog').innerText()).includes(MEMORY));
    assert((await desktop.locator('#chat-dialog').innerText()).includes(SKILL));
    await snap(desktop, 'desktop-frozen-context');
    await desktop.getByRole('button', {name: '关闭', exact: true}).click();
    const calls = modelCalls.length;
    await desktop.reload(); await enabled(desktop, '#chat-text'); await finished(desktop, initialRuns + 1);
    assert.equal(await desktop.locator('#chat-files-list .chat-file').count(), 2);
    assert.deepEqual((await json(desktop, `/conversations/${cid}`)).conversation.loaded_skill_ids, [sid]);
    assert.match(await desktop.locator('#chat-skills-open').innerText(), /1/);
    assert.equal(modelCalls.length, calls, 'Refresh must not call the model');
    await snap(desktop, 'desktop-refreshed-history');
  });

  await stage('skills-persist-per-chat-and-explicit-unload-without-model-calls', async () => {
    const before = modelCalls.length;
    const list = await json(desktop, '/conversations?limit=50');
    const other = list.conversations.find(row => row.id !== cid); assert(other);
    await loadedChat(desktop, origin + '/cockpit/chat/?conversation=' + other.id);
    await enabled(desktop, '#chat-text');
    assert.deepEqual((await json(desktop, `/conversations/${other.id}`)).conversation.loaded_skill_ids, []);
    assert.equal(await desktop.locator('#chat-skills-open').innerText(), 'Skills');
    await loadedChat(desktop, origin + '/cockpit/chat/?conversation=' + cid);
    await enabled(desktop, '#chat-text');
    assert.match(await desktop.locator('#chat-skills-open').innerText(), /1/);
    await desktop.click('#chat-skills-open');
    await desktop.click('#chat-context-refresh');
    await desktop.waitForFunction(() => !document.querySelector('#chat-context-refresh').disabled);
    assert((await desktop.locator('#chat-skill-order').innerText()).includes('browser-summary'));
    await expectWrite(desktop, `/conversations/${cid}`, () => desktop.getByRole('button', {name: '移除 browser-summary', exact: true}).click(), 'PATCH');
    await desktop.waitForFunction(() => [...document.querySelectorAll('#chat-skill-choices button')].some(b => b.getAttribute('aria-label') === '载入 browser-summary' && !b.disabled));
    assert.deepEqual((await json(desktop, `/conversations/${cid}`)).conversation.loaded_skill_ids, []);
    assert.equal((await json(desktop, '/context')).skills.length, 1, 'Removing loaded reference must retain personal library');
    assert.deepEqual((await json(desktop, `/conversations/${cid}/runs/${fileRun}/context`)).context_snapshot.skill_ids, [sid]);
    await snap(desktop, 'desktop-skills-window-unloaded');
    await desktop.click('#chat-skills-close');
    await loadedChat(desktop, origin + '/cockpit/chat/?conversation=' + cid);
    await enabled(desktop, '#chat-text');
    assert.equal(await desktop.locator('#chat-skills-open').innerText(), 'Skills');
    assert.equal(modelCalls.length, before, 'Reading, switching, refreshing and unloading Skills never generate');
  });

  await stage('global-selection-cache-survives-switch-new-chat-and-reload-without-generation', async () => {
    const calls = modelCalls.length;
    await desktop.waitForFunction(() => document.querySelector('#chat-model').value === 'test-chat' && !document.querySelector('#chat-model').disabled);
    const expected = {v: 1, token_id: 7, model: 'test-chat', mode: 'files'};
    const state = () => desktop.evaluate(() => ({token: document.querySelector('#chat-token').value, model: document.querySelector('#chat-model').value, mode: document.querySelector('#chat-mode').value, outsideConversation: !document.querySelector('#chat-selection').closest('.chat-main'), cache: JSON.parse(localStorage.getItem(CockpitChat.selectionStorageKey(12)))}));
    assert.deepEqual(await state(), {token: '7', model: 'test-chat', mode: 'files', outsideConversation: true, cache: expected});
    const modelReads = requests.filter(r => r.path === '/cockpit/api/chat/models').length;
    const created = await newChat(desktop); assert.notEqual(created, cid);
    assert.equal((await json(desktop, `/conversations/${created}`)).conversation.last_run_status, null);
    assert.deepEqual(await state(), {token: '7', model: 'test-chat', mode: 'files', outsideConversation: true, cache: expected});
    await enabled(desktop, '#chat-send');
    await desktop.locator('#chat-list > button').filter({hasText: '请确认这是普通聊天的合成测试。'}).click(); await finished(desktop, initialRuns + 1);
    assert.equal(requests.filter(r => r.path === '/cockpit/api/chat/models').length, modelReads, 'Switching chats reuses the in-memory catalog');
    await desktop.reload(); await finished(desktop, initialRuns + 1); await enabled(desktop, '#chat-send');
    assert.deepEqual(await state(), {token: '7', model: 'test-chat', mode: 'files', outsideConversation: true, cache: expected});
    assert.equal(requests.filter(r => r.path === '/cockpit/api/chat/models').length, modelReads + 1, 'Reload revalidates cached model permissions once');
    assert.equal(modelCalls.length, calls, 'Restoring selector preferences never generates');
    await noOverflow(desktop, 'desktop-global-selection'); await snap(desktop, 'desktop-global-selection-cache');
  });

  await stage('mobile-chat-and-settings-layout', async () => {
    const mobileContext = await browser.newContext({viewport: {width: 390, height: 844}, isMobile: true, hasTouch: true, deviceScaleFactor: 1, locale: 'zh-CN'});
    try {
      await mobileContext.route('**/*', route => {
        if (new URL(route.request().url()).origin === origin) return route.continue();
        errors.push({type: 'off-origin', url: route.request().url()}); return route.abort();
      });
      const mobile = await mobileContext.newPage(); mobile.setDefaultTimeout(12000); watch(mobile, 'mobile');
      await loadedChat(mobile, origin + '/cockpit/chat/' + (cid ? '?conversation=' + cid : ''));
      if (fileRun) await finished(mobile, initialRuns + 1);
      await noOverflow(mobile, 'mobile-chat'); await snap(mobile, 'mobile-chat');
      await mobile.click('#chat-skills-open');
      await noOverflow(mobile, 'mobile-skills-window'); await snap(mobile, 'mobile-skills-window');
      assert((await mobile.locator('#chat-skill-choices').innerText()).includes('browser-summary'));
      await mobile.click('#chat-skills-close');
      await mobile.goto(origin + '/cockpit/chat/settings/'); await enabled(mobile, '#settings-content');
      assert.equal(await mobile.locator('#settings-content').inputValue(), MEMORY);
      await noOverflow(mobile, 'mobile-settings'); await snap(mobile, 'mobile-settings');
    } finally { await mobileContext.close(); }
  });

  await stage('protocol-network-and-browser-errors', async () => {
    assert.deepEqual(errors, []);
    assert.deepEqual(requests.filter(r => r.status >= 400 || r.error), []);
    assert.deepEqual(modelCalls.map(call => call.phase), ['chat', 'memory', 'write', 'final', 'memory']);
    assert(modelCalls.filter(call => ['write', 'final'].includes(call.phase)).every(call => call.memory_present && call.skill_present));
    const writeCall = modelCalls.find(call => call.phase === 'write');
    for (const tool of ['http_fetch', 'download_url', 'web_search']) assert(writeCall.tool_names.includes(tool), `Default files mode provides ${tool}`);
    const runPosts = requests.filter(r => r.method === 'POST' && /\/runs$/.test(r.path));
    assert.equal(runPosts.length, 2); assert(runPosts.every(r => r.content_type.startsWith('text/event-stream')));
    assert(requests.filter(r => r.role === 'chat').every(r => r.path.startsWith('/cockpit/api/chat/')));
  });
  await context.close();
}

let cleanupTask;
function cleanup() {
  if (!cleanupTask) cleanupTask = (async () => {
    if (browser) await browser.close().catch(() => {});
    if (gateway) { gateway.closeAllConnections(); await new Promise(resolve => gateway.close(resolve)); }
    await Promise.all(children.map(record => stopFixture(record).catch(error => failures.push({name: 'cleanup', message: error.message}))));
    fs.rmSync(temp, {recursive: true, force: true});
    assert(!fs.existsSync(temp), 'Temporary data/profile/download directory must be removed');
  })();
  return cleanupTask;
}

process.once('SIGINT', () => { cleanup().finally(() => process.exit(130)); });
main().catch(error => { failures.push({name: 'harness', message: error.stack}); console.error(error); }).finally(async () => {
  await cleanup();
  const ending = sourceFingerprint();
  const sourceChanges = Object.keys(baseline).filter(name => baseline[name] !== ending[name]);
  if (sourceChanges.length) failures.push({name: 'source-changed-during-run', files: sourceChanges});
  const result = {passed: failures.length === 0, offline: true, real_chromium: true, real_flask_chat_service: true,
    synthetic_only: true, checks, failures, browser_errors: errors, model_calls: modelCalls,
    http_requests: requests, lock_failures: lockFailures, baseline, source_changes: sourceChanges, screenshots,
    cleanup: {temporary_data_removed: !fs.existsSync(temp),
      fixtures_stopped: children.every(({child}) => child.exitCode !== null || child.signalCode !== null)}};
  fs.writeFileSync(path.join(artifacts, 'result.json'), JSON.stringify(result, null, 2));
  console.log(JSON.stringify({passed: result.passed, checks: checks.length,
    failures: failures.map(({name, message, files}) => ({name, message, ...(files ? {files} : {})})),
    screenshots, cleanup: result.cleanup}, null, 2));
  process.exitCode = result.passed ? 0 : 1;
});
