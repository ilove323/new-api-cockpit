'use strict';
/* Fully intercepted, synthetic browser acceptance. Never connects to a backend/model. */
const {test, before, after} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const ROOT = path.join(__dirname, '..'), STATIC = path.join(ROOT, 'src/new_api_cockpit/static');
let playwright;
try { playwright = require('playwright'); } catch { /* Optional harness; resolve through normal Node paths. */ }
const executable = [process.env.COCKPIT_BROWSER_EXECUTABLE, playwright?.chromium.executablePath(), '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome', '/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge'].find(p => p && fs.existsSync(p));
let browser;
before(async () => { if (playwright && executable) browser = await playwright.chromium.launch({executablePath: executable, headless: true}); });
after(async () => { await browser?.close(); });
const id = n => `${n.toString(16).padStart(8, '0')}-1111-4111-8111-111111111111`;
const CID = id(1), RID = id(2), MID = id(3), RID2 = id(4), MID2 = id(5);
const cap = 'sha256:' + 'a'.repeat(64);
const clone = value => JSON.parse(JSON.stringify(value));
const profile = (name, content = '') => ({name, content, revision: content ? 1 : 0, sha256: 'a'.repeat(64), size_bytes: Buffer.byteLength(content), updated_at: content ? '2026-10-15T00:00:00Z' : null});
const skill = (n, name = `skill-${n}`) => ({id: id(n), name, description: `模板 ${n}`, revision: 1, sha256: 'b'.repeat(64), size_bytes: 90, created_at: '2026-10-15T00:00:00Z', updated_at: '2026-10-15T00:00:00Z', content: `---\nname: ${name}\ndescription: "模板 ${n}"\n---\nprivate-skill-${n}`});
const summary = s => { const {content: _, ...rest} = s; return rest; };
const block = (text, name) => new RegExp(`\\{% block ${name} %\\}([\\s\\S]*?)\\{% endblock %\\}`).exec(text)?.[1];
function html(name) {
  const template = fs.readFileSync(path.join(ROOT, `src/new_api_cockpit/templates/chat/${name}.html`), 'utf8');
  let base = fs.readFileSync(path.join(ROOT, 'src/new_api_cockpit/templates/chat/_base.html'), 'utf8');
  base = base.replace(/\{% block (\w+) %\}([\s\S]*?)\{% endblock %\}/g, (_, key, fallback) => block(template, key) ?? fallback);
  base = base.replace('{% include "partials/auth.html" %}', `<meta name="cockpit-session" content="fixture-session"><meta name="cockpit-expires" content="${Math.floor(Date.now() / 1000) + 3600}"><script defer src="/cockpit/static/auth.js"></script>`);
  return base.replace(/\{%[\s\S]*?%\}/g, '').replace(/\{\{[\s\S]*?\}\}/g, 'Fixture');
}
function fixture(options = {}) {
  const f = {requests: [], errors: [], external: [], skills: [20, 21, 22, 23].map(n => skill(n)), contextRevision: 5, profiles: [profile('MEMORY.md', 'private-memory-A'), profile('SOUL.md'), profile('IDENTITY.md')], mode: options.mode || 'sse', forceConflict: false, unknownSave: false, unknownCreate: false, hold: null};
  f.limits = {profile_bytes: {'MEMORY.md': 8192, 'SOUL.md': 4096, 'IDENTITY.md': 2048}, skill_file_bytes: 8192, skill_name_chars: 64, skill_description_chars: 256, skills_per_user: 64, skills_per_run: 3, personal_context_bytes: 32768};
  f.config = {schema_version: 1, limits: {user_text_bytes: 32768, attachments_per_turn: 8, title_chars: 80, upload_file_bytes: 2097152, event_page_max: 200, list_page_default: 50, poll_interval: 2, sse_frame_bytes: 262144}, allowed_file_extensions: ['.md', '.txt', '.html', '.pdf', '.doc', '.docx', '.xls', '.xlsx', '.png', '.jpg', '.jpeg', '.webp', '.gif', '.bmp'], modes: ['chat', 'files']};
  if (options.snapshot) f.config.features = {recovery_snapshot: true};
  f.files = [10, 11, 12].map((n, i) => ({id: id(n), name: i ? 'report.md' : 'source.md', kind: i ? 'output' : 'upload', version: i || null, size_bytes: 50, created_at: '2026-10-15T00:00:00Z', download_path: 'https://evil.test/do-not-use'}));
  f.conversations = Array.from({length: 105}, (_, n) => ({id: n ? id(100 + n) : CID, title: n ? `历史聊天 ${n}` : '完整历史', created_at: '2026-10-15T00:00:00Z', updated_at: '2026-10-15T00:00:00Z', revision: n ? 1 : 205, last_seq: n ? 1 : 205, active_run_id: null, storage_state: 'ok', last_run_status: n % 3 === 0 ? 'failed' : n % 3 === 1 ? 'interrupted' : 'completed', last_selection: {token_id: 7, model: 'fixture-chat', mode: 'chat'}}));
  for (const meta of f.conversations) meta.loaded_skill_ids = [];
  f.conversations[0].loaded_skill_ids = [...(options.loadedSkills || [])];
  f.otherEvents = new Map(); f.otherFiles = new Map();
  const e = (seq, type, data, run = RID, cid = CID) => ({v: 1, seq, type, data, conversation_id: cid, run_id: run, at: '2026-10-15T00:00:00Z'});
  f.events = [e(1, 'conversation.created', {title: '完整历史'}, null), e(2, 'run.accepted', {client_request_id: id(8), message_id: MID, text: 'private-question-A', token_id: 7, model: 'fixture-chat', mode: 'chat', attachment_ids: [], context_from_run_ids: [], context_snapshot: {context_revision: 5, skill_ids: [id(20)], file: `contexts/${RID}.json`, sha256: 'a'.repeat(64)}}), ...Array.from({length: 201}, (_, n) => e(n + 3, 'assistant.delta', {message_id: MID, text: '重复增量'})), e(204, 'assistant.message', {message_id: MID, text: '最终快照\n![外泄](https://evil.test/secret)\n<script>alert(1)</script>\n[正常链接](https://example.test/)\n```html\n<img src=x>\n```', tool_calls: [], complete: true, finish_reason: 'stop'}), e(205, 'run.terminal', {status: 'completed', reason: '', error_code: null, uncertain: false, usage: null, usage_complete: false, model_requests: 1, tool_calls: 0})];
  f.runs = new Map([[RID, {id: RID, status: 'completed', uncertain: false, usage: null, usage_complete: false}]]);
  f.frozen = new Map([[RID, {profiles: clone(f.profiles), skills: [clone(f.skills[0])], skill_ids: [id(20)]}]]);
  f.context = () => ({schema_version: 1, context_revision: f.contextRevision, profiles: clone(f.profiles), skills: f.skills.map(summary), limits: f.limits, updated_at: '2026-10-15T00:00:00Z'});
  f.makeRun = (body, status = 'completed', cid = CID) => {
    const previous = cid === CID ? f.events : f.otherEvents.get(cid) || [e(1, 'conversation.created', {title: '新聊天'}, null, cid)];
    const emit = (seq, type, data) => e(seq, type, data, RID2, cid);
    const start = previous.at(-1).seq, snapshot = {context_revision: body.context_revision, skill_ids: body.skill_ids || [], file: `contexts/${RID2}.json`, sha256: 'a'.repeat(64)};
    const events = [emit(start + 1, 'run.accepted', {...body, context_snapshot: snapshot, message_id: MID2, context_from_run_ids: cid === CID ? [RID] : []}), emit(start + 2, 'run.dispatching', {request_index: 1, assistant_message_id: MID2}), emit(start + 3, 'assistant.delta', {message_id: MID2, text: 'partial-new-answer'})];
    if (status === 'completed') events.push(emit(start + 4, 'assistant.message', {message_id: MID2, text: 'new-answer-final', tool_calls: [], complete: true, finish_reason: 'stop'}));
    if (status !== 'running') events.push(emit(events.at(-1).seq + 1, 'run.terminal', {status, uncertain: status !== 'completed', reason: status === 'completed' ? '' : '连接中断或取消', error_code: status === 'completed' ? null : 'CHAT_UPSTREAM_INTERRUPTED', usage: null, usage_complete: false, model_requests: 1, tool_calls: 0}));
    previous.push(...events); if (cid !== CID) f.otherEvents.set(cid, previous);
    Object.assign(f.conversations.find(c => c.id === cid), {revision: previous.at(-1).seq, last_seq: previous.at(-1).seq, active_run_id: status === 'running' ? RID2 : null, last_run_status: status});
    f.runs.set(RID2, {id: RID2, status, uncertain: status !== 'completed', usage: null, usage_complete: false, cancel_requested: false}); f.frozen.set(RID2, {profiles: clone(f.profiles), skills: clone(f.skills.filter(s => body.skill_ids?.includes(s.id))), skill_ids: body.skill_ids || []});
    return events;
  };
  if (options.active) f.makeRun({client_request_id: id(9), text: 'active-question', context_revision: 5, skill_ids: [], model: 'fixture-chat', mode: 'files', attachment_ids: []}, 'running');
  f.handle = async route => {
    const request = route.request(), url = new URL(request.url()), method = request.method();
    if (url.origin !== 'http://cockpit.test') { f.external.push(url.href); return route.abort(); }
    if (url.pathname.startsWith('/cockpit/static/')) {
      const relative = url.pathname.slice('/cockpit/static/'.length), file = path.join(STATIC, relative);
      if (!file.startsWith(STATIC + path.sep) || !fs.existsSync(file)) return route.fulfill({status: 404, body: ''});
      return route.fulfill({body: fs.readFileSync(file), contentType: file.endsWith('.js') ? 'application/javascript' : file.endsWith('.css') ? 'text/css' : 'application/octet-stream'});
    }
    if (url.pathname === '/cockpit/chat/' || url.pathname === '/cockpit/chat/settings/') return route.fulfill({contentType: 'text/html', body: html(url.pathname.includes('settings') ? 'settings' : 'index')});
    if (!url.pathname.startsWith('/cockpit/api/chat/')) return route.fulfill({status: 204, body: ''});
    const p = url.pathname.slice('/cockpit/api/chat'.length), body = request.postData() && request.headers()['content-type']?.includes('application/json') ? request.postDataJSON() : null;
    f.requests.push({method, path: p, query: url.search, body, headers: request.headers()});
    if (f.hold && f.hold.match(p, method)) await new Promise(resolve => { f.hold.release = resolve; f.hold.entered?.(); });
    const json = (value, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(value)}).catch(() => {});
    if (p === '/config') return json(f.config);
    if (p === '/context') return json(f.context());
    if (p === '/tokens') return json({tokens: options.noTokens ? [] : [{id: 7, name: 'fixture-key', key_masked: '***fixture', group: 'group', usable: true, status: 1}], token_manager_path: '/keys'});
    if (p === '/models') return json({models: [{id: 'fixture-chat', protocol: 'chat_completions', chat_enabled: true, tools_enabled: !options.noTools, capability_revision: cap, reason: ''}, {id: 'unknown', chat_enabled: false, tools_enabled: false, reason: '协议未确认', capability_revision: cap}]});
    if (p === '/conversations' && method === 'GET') {
      const offset = Number(url.searchParams.get('cursor') || 0), rows = f.conversations.slice(offset, offset + 50), next = offset + 50;
      return json({conversations: rows, total: f.conversations.length, next_cursor: next < f.conversations.length ? String(next) : null, has_more: next < f.conversations.length, warnings: []});
    }
    if (p === '/conversations' && method === 'POST') {
      if (f.failCreate) return json({error: 'synthetic creation failure', code: 'CHAT_STORAGE_UNAVAILABLE'}, 503);
      const meta = {id: body.conversation_id, title: '新聊天', revision: 1, last_seq: 1, active_run_id: null, storage_state: 'ok', last_run_status: null, loaded_skill_ids: []}; f.conversations.unshift(meta); return json({conversation: meta}, 201);
    }
    let match;
    if ((match = /^\/conversations\/([^/]+)$/.exec(p))) {
      const meta = f.conversations.find(c => c.id === match[1]);
      if (!meta) return json({error: '不存在', code: 'CHAT_NOT_FOUND'}, 404);
      if (method === 'DELETE') { f.conversations = f.conversations.filter(c => c !== meta); return route.fulfill({status: 204}); }
      if (method === 'PATCH') {
        if (body.expected_revision !== meta.revision) return json({error: '聊天已变化', code: 'CHAT_REVISION_CONFLICT'}, 409);
        let type, data;
        if (Array.isArray(body.loaded_skill_ids)) {
          if (body.context_revision !== f.contextRevision) return json({error: '设置已变化', code: 'CHAT_CONTEXT_CHANGED'}, 409);
          if (body.loaded_skill_ids.length > 3 || new Set(body.loaded_skill_ids).size !== body.loaded_skill_ids.length || body.loaded_skill_ids.some(sid => !f.skills.some(s => s.id === sid))) return json({error: 'Skill 不存在或超限', code: 'CHAT_NOT_FOUND'}, 404);
          meta.loaded_skill_ids = [...body.loaded_skill_ids]; type = 'conversation.skills.updated'; data = {skill_ids: [...body.loaded_skill_ids]};
        } else { meta.title = body.title; type = 'conversation.renamed'; data = {title: body.title}; }
        const events = meta.id === CID ? f.events : f.otherEvents.get(meta.id) || [e(1, 'conversation.created', {title: meta.title}, null, meta.id)];
        events.push(e(events.at(-1).seq + 1, type, data, null, meta.id));
        if (meta.id !== CID) f.otherEvents.set(meta.id, events);
        meta.revision = events.at(-1).seq; meta.last_seq = meta.revision;
      }
      return json({conversation: clone(meta)});
    }
    if ((match = /^\/conversations\/([^/]+)\/sync$/.exec(p))) {
      const cid = match[1], meta = f.conversations.find(c => c.id === cid), events = cid === CID ? f.events : f.otherEvents.get(cid) || [], files = cid === CID ? f.files : f.otherFiles.get(cid) || [];
      const after = Number(url.searchParams.get('after_seq') || 0), until = events.at(-1)?.seq || 0, page = events.filter(e => e.seq > after).slice(0, 200), next = page.at(-1)?.seq || after;
      const filesRevision = require('node:crypto').createHash('sha256').update(JSON.stringify(files)).digest('hex');
      const rid = meta.active_run_id || events.findLast(e => e.run_id)?.run_id;
      return json({conversation: clone(meta), events: {events: page, until_seq: until, next_after_seq: next, has_more: next < until, storage_state: 'ok'}, run: clone(f.runs.get(rid) || null), files: url.searchParams.get('files_revision') === filesRevision ? null : clone(files), files_revision: filesRevision, context: null});
    }
    if ((match = /^\/conversations\/([^/]+)\/events$/.exec(p))) {
      const events = match[1] === CID ? f.events : f.otherEvents.get(match[1]) || [e(1, 'conversation.created', {title: '其他聊天'}, null, match[1])], after = Number(url.searchParams.get('after_seq') || 0), until = Number(url.searchParams.get('until_seq') || events.at(-1).seq);
      const page = events.filter(e => e.seq > after && e.seq <= until).slice(0, 200), next = page.at(-1)?.seq || after;
      return json({events: page, until_seq: until, next_after_seq: next, has_more: next < until, storage_state: 'ok'});
    }
    if ((match = /^\/conversations\/([^/]+)\/files\/([^/]+)$/.exec(p)) && method === 'DELETE') {
      const files = match[1] === CID ? f.files : f.otherFiles.get(match[1]) || [], file = files.find(file => file.id === match[2]);
      if (!file || file.kind !== 'upload') return json({error: '附件不存在'}, 404);
      file.deleted_at ||= '2026-10-24T00:00:00Z';
      if (f.lostFileDelete) return route.abort('failed');
      return json({file});
    }
    if (/\/files\/[^/]+\/download$/.test(p)) return route.fulfill({contentType: 'text/plain', body: 'download fixture'});
    if (/\/files$/.test(p)) {
      const cid = /^\/conversations\/([^/]+)\/files$/.exec(p)[1], files = cid === CID ? f.files : f.otherFiles.get(cid) || [];
      if (method === 'POST') { const multipart = request.postDataBuffer().toString(); const fid = /name="client_file_id"\r\n\r\n([^\r]+)/.exec(multipart)[1], name = /filename="([^"]+)"/.exec(multipart)[1]; const file = {...f.files[0], id: fid, name}; files.push(file); if (cid !== CID) f.otherFiles.set(cid, files); return json({file}, 201); }
      return json({files, total: files.length});
    }
    if (/\/runs$/.test(p) && method === 'POST') {
      if (f.mode === 'replay') return json({replayed: true, run_id: RID, status: 'completed', last_seq: 205});
      const events = f.makeRun(body, f.mode === 'eof' ? 'interrupted' : 'completed', /^\/conversations\/([^/]+)\/runs$/.exec(p)[1]);
      if (f.mode === 'lost') return route.abort('failed');
      const wire = f.mode === 'eof' ? events.filter(e => e.type !== 'run.terminal') : events;
      return route.fulfill({contentType: 'text/event-stream; charset=utf-8', body: wire.map(e => `id: ${e.seq}\nevent: ${e.type}\ndata: ${JSON.stringify(e)}\n\n`).join('')});
    }
    if ((match = /\/runs\/([^/]+)\/cancel$/.exec(p))) {
      const r = f.runs.get(match[1]); r.status = 'cancelled'; r.cancel_requested = true; f.conversations[0].active_run_id = null;
      f.events.push(e(f.events.at(-1).seq + 1, 'run.terminal', {status: 'cancelled', uncertain: true, reason: '用户停止', usage: null, usage_complete: false}, match[1])); f.conversations[0].revision = f.events.at(-1).seq;
      return json({run_id: match[1], status: 'running', cancel_requested: true}, 202);
    }
    if ((match = /\/runs\/([^/]+)\/context$/.exec(p))) {
      return json({context_snapshot: {v: 1, conversation_id: CID, run_id: match[1], captured_at: '2026-10-15T00:00:00Z', context_revision: 5, policy_version: 'personal-context-v1', ...clone(f.frozen.get(match[1]))}});
    }
    if ((match = /\/runs\/([^/]+)$/.exec(p))) return json({run: clone(f.runs.get(match[1]))});
    if ((match = /^\/profiles\/(.+)$/.exec(p))) {
      const doc = f.profiles.find(p => p.name === match[1]);
      if (f.forceConflict) { f.forceConflict = false; f.contextRevision++; doc.revision++; doc.content = 'external-new-version'; return json({error: '设置已在其他页面更新', code: 'CHAT_CONTEXT_CHANGED'}, 409); }
      doc.content = body.content; doc.revision++; doc.size_bytes = Buffer.byteLength(body.content); f.contextRevision++; doc.updated_at = '2026-10-15T00:01:00Z';
      if (f.unknownSave) { f.unknownSave = false; return route.abort('failed'); }
      return json({context_revision: f.contextRevision, changed: true, profile: clone(doc)});
    }
    if (p === '/skills' && method === 'GET') return json({context_revision: f.contextRevision, skills: f.skills.map(summary), total: f.skills.length});
    if (p === '/skills' && method === 'POST') {
      const created = skill(50 + f.skills.length, /^name: (.+)$/m.exec(body.content)[1]); created.content = body.content; f.skills.push(created); f.contextRevision++;
      if (f.unknownCreate) { f.unknownCreate = false; return route.abort('failed'); }
      return json({context_revision: f.contextRevision, changed: true, skill: created}, 201);
    }
    if ((match = /^\/skills\/([^/]+)$/.exec(p))) {
      const item = f.skills.find(s => s.id === match[1]); if (!item) return json({error: '不存在', code: 'CHAT_NOT_FOUND'}, 404);
      if (method === 'DELETE') { f.skills = f.skills.filter(s => s !== item); f.contextRevision++; return json({context_revision: f.contextRevision, deleted_id: item.id, deleted: true}); }
      if (method === 'PATCH') { item.content = body.content; item.revision++; f.contextRevision++; }
      return json(method === 'GET' ? {context_revision: f.contextRevision, skill: item} : {context_revision: f.contextRevision, changed: true, skill: item});
    }
    f.errors.push(`Unhandled ${method} ${p}`); return json({error: 'unhandled fixture'}, 500);
  };
  return f;
}
async function setup(t, options = {}, settings = false) {
  if (!browser) { t.skip('Optional Playwright/Chromium runtime unavailable'); return null; }
  const context = await browser.newContext({viewport: {width: 1540, height: 1000}}), f = fixture(options), page = await context.newPage();
  t.after(async () => { f.hold?.release?.(); await context.close(); assert.deepEqual(f.errors, []); assert.deepEqual(f.external, []); });
  page.on('pageerror', error => f.errors.push(error.message)); page.on('dialog', dialog => dialog.accept());
  await page.route('**/*', route => f.handle(route));
  await page.goto(`http://cockpit.test/cockpit/chat/${settings ? 'settings/' : options.blank ? '' : `?conversation=${CID}`}`);
  if (settings) await page.waitForFunction(() => !document.getElementById('settings-content').disabled);
  else if (options.blank) await page.waitForFunction(() => !document.getElementById('chat-text').disabled && !document.getElementById('chat-attach').disabled);
  else await page.waitForFunction(() => document.getElementById('chat-messages').textContent.includes('最终快照'));
  return {page, f};
}
const countWrites = (f, path, method = 'POST') => f.requests.filter(r => r.path === path && r.method === method).length;
async function chooseModel(page) { await page.selectOption('#chat-token', '7'); await page.click('#chat-model-load'); await page.waitForFunction(() => !document.getElementById('chat-send').disabled); }

test('browser: all 105 chats and 205 events restore without a KEY, no redundant chat controls', async t => {
  const env = await setup(t, {noTokens: true}); if (!env) return; const {page, f} = env;
  assert.equal(await page.locator('#chat-messages').getByText('最终快照', {exact: false}).count(), 1);
  assert.equal((await page.locator('#chat-messages').innerText()).includes('重复增量'), false);
  assert.equal(await page.locator('#chat-send').isDisabled(), true);
  await page.click('#chat-more'); await page.waitForFunction(() => document.getElementById('chat-list').children.length === 100);
  await page.click('#chat-more'); await page.waitForFunction(() => document.getElementById('chat-list').children.length === 105);
  assert.equal(await page.locator('#chat-more').isHidden(), true);
  assert.equal(await page.locator('.chat-files .chat-file').count(), 2);
  assert.match(await page.locator('#chat-files-list').innerText(), /v1/); assert.match(await page.locator('#chat-files-list').innerText(), /v2/);
  assert.equal(await page.locator('#chat-skills-open, #chat-skills-dialog, #chat-recover').count(), 0);
  assert.equal(await page.getByRole('button', {name: /查看上下文/}).count(), 0);
  assert.doesNotMatch(await page.locator('#chat-messages').innerText(), /自适应上下文|自动记忆已开启|联网按需可用/);
  assert.match(await page.locator('.chat-run-footer').innerText(), /已完成/);
  assert.match(await page.locator('.chat-run-footer').innerText(), /fixture-chat/);
  assert.match(await page.locator('.chat-run-footer').innerText(), /用量未知/);
  assert.equal(f.requests.some(r => /\/runs\/[^/]+\/context$/.test(r.path)), false);
  assert.equal(await page.locator('#chat-messages img, #chat-messages script, #chat-messages iframe, #chat-messages svg').count(), 0);
  assert.equal(await page.locator('#chat-messages a').getAttribute('rel'), 'noopener noreferrer');
  assert.equal(f.requests.every(r => r.method === 'GET'), true, 'refresh/history must never POST models or runs');
  assert.equal(f.requests.every(r => r.headers['x-cockpit-session'] === 'fixture-session'), true);
});
test('browser: ordinary attachments work with a no-tools model; Agent cannot disable its model option', async t => {
  const env = await setup(t, {noTools: true}); if (!env) return; const {page, f} = env;
  await chooseModel(page);
  assert.equal(await page.locator('#chat-model option[value="fixture-chat"]').isDisabled(),false);
  assert.doesNotMatch(await page.locator('#chat-model').innerText(),/工具能力/);
  assert.equal(await page.locator('#chat-mode, #chat-agent-mode').count(),0);
  await page.setInputFiles('#chat-upload',{name:'ordinary-report.md',mimeType:'text/plain',buffer:Buffer.from('ordinary attachment fixture')});
  await page.waitForFunction(() => document.getElementById('chat-attachments').textContent.includes('ordinary-report.md'));
  await page.fill('#chat-text','请分析本轮引用的附件');
  if (process.env.COCKPIT_CHAT_ARTIFACT_DIR) {
    fs.mkdirSync(process.env.COCKPIT_CHAT_ARTIFACT_DIR,{recursive:true});
    await page.screenshot({path:path.join(process.env.COCKPIT_CHAT_ARTIFACT_DIR,'ordinary-attachments.png')});
  }
  await page.click('#chat-send');await page.waitForFunction(() => document.getElementById('chat-messages').textContent.includes('new-answer-final'));
  const runs=f.requests.filter(r=>r.method==='POST' && /\/runs$/.test(r.path));
  assert.equal(runs.length,1);assert.equal(runs[0].body.mode,'files');
  assert.equal(runs[0].body.attachment_ids.length,1);assert.equal(runs[0].body.attachment_ids[0],f.files.at(-1).id);assert.equal(runs[0].body.web_enabled,true);
});

test('browser: preloaded Skills stay ordered across send and reload without their removed toolbar', async t => {
  for (const loadedSkills of [[], [id(20)], [id(21), id(20), id(22)]]) await t.test(`${loadedSkills.length} loaded`, async st => {
    const env = await setup(st, {loadedSkills}); if (!env) return; const {page, f} = env; await chooseModel(page);
    assert.equal(await page.locator('#chat-skills-open, #chat-skills-dialog, #chat-recover').count(), 0);
    await page.getByRole('button', {name: '引用附件 source.md', exact: true}).click(); await page.fill('#chat-text', 'new-user-task');
    await page.click('#chat-send'); await page.waitForFunction(() => document.getElementById('chat-messages').textContent.includes('new-answer-final') && !document.getElementById('chat-text').disabled);
    const runs = f.requests.filter(r => /\/runs$/.test(r.path) && r.method === 'POST'); assert.equal(runs.length, 1);
    assert.deepEqual(runs[0].body.skill_ids, loadedSkills); assert.equal(runs[0].body.context_revision, 5); assert.deepEqual(runs[0].body.attachment_ids, [id(10)]); assert.equal(runs[0].body.mode, 'files');
    assert.equal(runs[0].body.auto_memory, true); assert.equal(runs[0].body.web_enabled, true); assert.equal(runs[0].body.context_strategy, 'adaptive');
    assert.equal(runs[0].headers['x-cockpit-chat'], '1'); assert.equal(runs[0].headers['x-cockpit-session'], 'fixture-session');
    assert.equal(await page.inputValue('#chat-text'), '');
    assert.doesNotMatch(await page.locator('#chat-messages').innerText(), /查看上下文|自适应上下文|自动记忆已开启|联网按需可用|partial-new-answer/);
    const count = f.requests.length; await page.reload();
    await page.waitForFunction(() => document.getElementById('chat-messages').textContent.includes('new-answer-final') && !document.getElementById('chat-text').disabled);
    assert.equal(f.requests.slice(count).every(r => r.method === 'GET'), true);
    assert.deepEqual(f.conversations[0].loaded_skill_ids, loadedSkills);
    assert.equal(f.requests.filter(r => /\/runs$/.test(r.path) && r.method === 'POST').length, 1);
    assert.equal(f.requests.some(r => r.path === `/conversations/${CID}` && r.method === 'PATCH'), false);
  });
});
test('browser: JSON replay and EOF without terminal recover using GET only, reload never regenerates', async t => {
  for (const mode of ['replay', 'eof', 'lost']) await t.test(mode, async st => {
    const env = await setup(st, {mode}); if (!env) return; const {page, f} = env; await chooseModel(page); await page.fill('#chat-text', 'once-only'); await page.click('#chat-send');
    await page.waitForFunction(() => !document.getElementById('chat-text').disabled && /命中原有运行|连接结束但未收到终态|结果待确认/.test(document.getElementById('chat-status').textContent));
    assert.equal(countWrites(f, `/conversations/${CID}/runs`), 1);
    if (mode === 'eof') { assert.match(await page.locator('#chat-messages').innerText(), /结果不确定/); assert.match(await page.locator('#chat-messages').innerText(), /partial-new-answer/); }
    if (mode === 'lost') { assert.equal(await page.inputValue('#chat-text'), '', 'GET confirms accepted request and clears its draft without replay'); assert.match(await page.locator('#chat-messages').innerText(), /new-answer-final/); }
    const count = f.requests.length; await page.reload(); await page.waitForFunction(() => document.getElementById('chat-messages').textContent.includes('最终快照'));
    assert.equal(f.requests.slice(count).every(r => r.method === 'GET'), true);
  });
});
test('browser: cancel 202 is followed by GET confirmation, preserving partial answer/files and unknown usage', async t => {
  const env = await setup(t, {active: true}); if (!env) return; const {page, f} = env;
  await page.waitForFunction(() => !document.getElementById('chat-stop').disabled); await page.click('#chat-stop');
  await page.waitForFunction(() => document.getElementById('chat-messages').textContent.includes('用户停止'));
  const cancel = f.requests.filter(r => r.path.endsWith('/cancel')); assert.equal(cancel.length, 1); assert.deepEqual(cancel[0].body, {});
  assert.equal(countWrites(f, `/conversations/${CID}/runs`), 0); assert.equal(await page.locator('.chat-files .chat-file').count(), 2);
  assert.match(await page.locator('#chat-messages').innerText(), /用量未知/); assert.match(await page.locator('#chat-messages').innerText(), /partial-new-answer/);
});
test('browser: conversation/account switches preserve server Skills and clear old private content', async t => {
  const env = await setup(t, {loadedSkills: [id(20)]}); if (!env) return; const {page, f} = env;
  await page.fill('#chat-text', 'private-draft-A');
  await page.locator('#chat-list button').nth(1).click(); await page.waitForFunction(() => document.getElementById('chat-title').textContent.includes('历史聊天 1'));
  assert.equal(await page.inputValue('#chat-text'), '');
  assert.equal(await page.locator('#chat-skills-open, #chat-skills-dialog').count(), 0);
  assert.doesNotMatch(await page.locator('#chat-messages').innerText(), /private-question-A/);
  assert.deepEqual(f.conversations.find(c => c.id === CID).loaded_skill_ids, [id(20)]);
  await page.evaluate(cid => { history.pushState(null, '', '/cockpit/chat/?conversation=' + cid); dispatchEvent(new PopStateEvent('popstate')); }, CID);
  await page.waitForFunction(() => document.getElementById('chat-title').textContent === '完整历史' && document.getElementById('chat-messages').textContent.includes('最终快照'));
  await page.evaluate(() => window.dispatchEvent(new Event('cockpit-auth-changed')));
  assert.doesNotMatch(await page.locator('body').innerText(), /private-memory|private-question|private-draft|skill-20/);
  assert.equal(await page.locator('#chat-list button').count(), 0); assert.equal(await page.locator('#chat-send').isDisabled(), true);
  assert.equal(f.requests.every(r => r.method === 'GET'), true);
});
test('browser: personal documents clear/save, single-file Skills import/CRUD, safe preview and no autosave', async t => {
  const env = await setup(t, {}, true); if (!env) return; const {page, f} = env;
  assert.equal(await page.inputValue('#settings-content'), 'private-memory-A');
  await page.fill('#settings-content', '手动记忆 <script>alert(1)</script> ![no](https://evil.test/x)');
  assert.equal(countWrites(f, '/profiles/MEMORY.md', 'PATCH'), 0);
  await page.click('#settings-preview-toggle'); assert.equal(await page.locator('#settings-preview script, #settings-preview img').count(), 0);
  await page.click('#settings-save'); await page.waitForFunction(() => document.getElementById('settings-status').textContent.includes('已保存'));
  assert.equal(countWrites(f, '/profiles/MEMORY.md', 'PATCH'), 1);
  await page.click('#settings-clear'); await page.waitForFunction(() => document.getElementById('settings-content').value === '' && !document.getElementById('settings-content').disabled);
  assert.equal(f.profiles[0].content, '');
  for (const name of ['SOUL.md', 'IDENTITY.md']) { await page.locator(`[data-profile="${name}"]`).click(); await page.fill('#settings-content', `中文-${name}`); await page.click('#settings-save'); await page.waitForFunction(() => !document.getElementById('settings-content').disabled); assert.equal(countWrites(f, `/profiles/${name}`, 'PATCH'), 1); }
  await page.click('#settings-skills-tab'); await page.click('#settings-new-skill');
  const raw = '---\nname: imported-test\ndescription: "导入模板"\n---\n只整理明确输入。\n';
  await page.setInputFiles('#settings-import-skill', {name: 'SKILL.md', mimeType: 'text/plain', buffer: Buffer.from(raw)});
  await page.waitForFunction(() => document.getElementById('settings-content').value.includes('imported-test'));
  assert.equal(countWrites(f, '/skills'), 0); await page.click('#settings-save'); await page.waitForFunction(() => !document.getElementById('settings-content').disabled);
  assert.equal(countWrites(f, '/skills'), 1); const created = f.skills.find(s => s.name === 'imported-test'); assert.ok(created);
  await page.fill('#settings-content', raw + '再加一个步骤。'); await page.click('#settings-save'); await page.waitForFunction(() => !document.getElementById('settings-content').disabled);
  assert.equal(countWrites(f, `/skills/${created.id}`, 'PATCH'), 1);
  await page.click('#settings-delete-skill'); await page.waitForFunction(() => document.getElementById('settings-status').textContent.includes('已从当前库删除'));
  assert.equal(countWrites(f, `/skills/${created.id}`, 'DELETE'), 1); assert.equal(f.skills.some(s => s.id === created.id), false);
  assert.deepEqual(await page.evaluate(() => ({local: {...localStorage}, session: {...sessionStorage}})), {local: {}, session: {}});
});
test('browser: save conflict preserves draft, compares current document, requires explicit rebase and save', async t => {
  const env = await setup(t, {}, true); if (!env) return; const {page, f} = env; f.forceConflict = true;
  await page.fill('#settings-content', 'my-unsaved-draft'); await page.click('#settings-save'); await page.waitForFunction(() => !document.getElementById('settings-conflict').hidden);
  assert.equal(await page.inputValue('#settings-content'), 'my-unsaved-draft'); assert.match(await page.locator('#settings-latest-content').innerText(), /external-new-version/);
  assert.equal(await page.locator('#settings-save').isDisabled(), true); assert.equal(countWrites(f, '/profiles/MEMORY.md', 'PATCH'), 1);
  await page.click('#settings-rebase'); assert.equal(await page.inputValue('#settings-content'), 'my-unsaved-draft'); assert.equal(countWrites(f, '/profiles/MEMORY.md', 'PATCH'), 1);
  await page.click('#settings-save'); await page.waitForFunction(() => document.getElementById('settings-status').textContent.includes('已保存'));
  const writes = f.requests.filter(r => r.path === '/profiles/MEMORY.md' && r.method === 'PATCH'); assert.equal(writes.length, 2); assert.equal(writes[1].body.expected_revision, 2); assert.equal(writes[1].body.context_revision, 6);
});
test('browser: lost save/create responses are compared by GET without a second write', async t => {
  const env = await setup(t, {}, true); if (!env) return; const {page, f} = env; f.unknownSave = true;
  await page.fill('#settings-content', 'saved-with-lost-response'); await page.click('#settings-save'); await page.waitForFunction(() => document.getElementById('settings-status').textContent.includes('GET 确认'));
  assert.equal(countWrites(f, '/profiles/MEMORY.md', 'PATCH'), 1); assert.equal(await page.locator('#settings-save').isDisabled(), true);
  await page.click('#settings-skills-tab'); await page.click('#settings-new-skill'); f.unknownCreate = true;
  await page.fill('#settings-content', '---\nname: lost-create\ndescription: "unknown"\n---\nnew content'); await page.click('#settings-save'); await page.waitForFunction(() => document.getElementById('settings-status').textContent.includes('GET 确认'));
  assert.equal(countWrites(f, '/skills'), 1); assert.equal(f.skills.filter(s => s.name === 'lost-create').length, 1);
});
test('browser: account switch erases settings draft and drops a delayed private response', async t => {
  const env = await setup(t, {}, true); if (!env) return; const {page, f} = env;
  await page.fill('#settings-content', 'private-unsaved-A'); let entered;
  const arrival = new Promise(resolve => { entered = resolve; }); f.hold = {match: (p, m) => p === '/profiles/MEMORY.md' && m === 'PATCH', entered};
  await page.click('#settings-save'); await page.waitForFunction(() => document.getElementById('settings-content').disabled);
  await arrival;
  await page.evaluate(() => window.dispatchEvent(new Event('cockpit-auth-changed'))); f.hold.release?.();
  await page.waitForTimeout(100);
  assert.equal(await page.inputValue('#settings-content'), ''); assert.doesNotMatch(await page.locator('body').innerText(), /private-unsaved|private-memory|private-skill/);
  assert.equal(await page.locator('#settings-content').isDisabled(), true); assert.equal(await page.locator('#settings-skill-list button').count(), 0);
});
test('browser: upload/download is conversation-scoped multipart/Blob, never a public server-supplied URL', async t => {
  const env = await setup(t); if (!env) return; const {page, f} = env;
  await page.setInputFiles('#chat-upload', {name: 'new-source.md', mimeType: 'text/plain', buffer: Buffer.from('synthetic text')});
  await page.waitForFunction(() => document.getElementById('chat-attachments').textContent.includes('new-source.md'));
  const uploads = f.requests.filter(r => r.path === `/conversations/${CID}/files` && r.method === 'POST');
  assert.equal(uploads.length, 1); assert.match(uploads[0].headers['content-type'], /^multipart\/form-data; boundary=/); assert.equal(uploads[0].headers['x-cockpit-chat'], '1');
  await page.evaluate(() => {
    window.fixtureURLs = {created: [], revoked: []}; const create = URL.createObjectURL.bind(URL), revoke = URL.revokeObjectURL.bind(URL);
    URL.createObjectURL = blob => { const url = create(blob); window.fixtureURLs.created.push(url); return url; };
    URL.revokeObjectURL = url => { window.fixtureURLs.revoked.push(url); revoke(url); };
  });
  const downloaded = page.waitForEvent('download'); await page.getByRole('button', {name: '下载附件 source.md', exact: true}).click(); const download = await downloaded;
  assert.equal(download.suggestedFilename(), 'source.md');
  assert.equal(f.requests.filter(r => r.path.endsWith(`/files/${id(10)}/download`)).length, 1);
  await page.evaluate(() => window.dispatchEvent(new Event('cockpit-auth-changed')));
  const urls = await page.evaluate(() => window.fixtureURLs); assert.equal(urls.created.length, 1); assert.deepEqual(urls.revoked, urls.created);
});
test('browser: switching chats aborts old pending metadata and ignores its late response', async t => {
  const env = await setup(t); if (!env) return; const {page, f} = env; let entered;
  const arrival = new Promise(resolve => { entered = resolve; });
  f.hold = {match: (p, m) => p === `/conversations/${id(101)}` && m === 'GET', entered};
  await page.locator('#chat-list button').nth(1).click(); await arrival;
  await page.locator('#chat-list button').nth(2).click(); await page.waitForFunction(() => document.getElementById('chat-title').textContent.includes('历史聊天 2'));
  f.hold.release(); await page.waitForTimeout(50);
  assert.equal(await page.locator('#chat-title').textContent(), '历史聊天 2'); assert.doesNotMatch(await page.locator('#chat-messages').innerText(), /private-question-A/);
});
test('browser: reading latest settings with a dirty draft compares without discarding or writing', async t => {
  const env = await setup(t, {}, true); if (!env) return; const {page, f} = env;
  await page.fill('#settings-content', 'draft-stays'); f.profiles[0].content = 'remote-current'; f.profiles[0].revision++; f.contextRevision++;
  await page.click('#settings-refresh'); await page.waitForFunction(() => !document.getElementById('settings-conflict').hidden);
  assert.equal(await page.inputValue('#settings-content'), 'draft-stays'); assert.match(await page.locator('#settings-latest-content').innerText(), /remote-current/);
  assert.equal(f.requests.every(r => r.method === 'GET'), true);
});
test('browser: creating, renaming and explicitly deleting an empty chat never starts generation', async t => {
  const env = await setup(t); if (!env) return; const {page, f} = env;
  await page.click('#chat-new'); await page.waitForFunction(() => document.getElementById('chat-title').textContent === '新聊天');
  const created = f.conversations.find(c => c.title === '新聊天'); assert.ok(created);
  assert.equal(countWrites(f, '/conversations'), 1); assert.equal(await page.locator('#chat-skills-open, #chat-skills-dialog, #chat-recover').count(), 0);
  page.removeAllListeners('dialog'); page.on('dialog', dialog => dialog.accept(dialog.type() === 'prompt' ? 'renamed-empty-chat' : undefined));
  await page.click('#chat-rename'); await page.waitForFunction(() => document.getElementById('chat-title').textContent === 'renamed-empty-chat');
  assert.equal(countWrites(f, `/conversations/${created.id}`, 'PATCH'), 1);
  await page.click('#chat-delete'); await page.waitForFunction(() => document.getElementById('chat-title').textContent.includes('选择或新建'));
  assert.equal(countWrites(f, `/conversations/${created.id}`, 'DELETE'), 1); assert.equal(f.conversations.some(c => c.id === created.id), false);
  assert.equal(f.requests.some(r => r.method === 'POST' && r.path.endsWith('/runs')), false);
});
test('browser: live POST stream sends cancel before aborting reader, then recovers by GET without replay', async t => {
  const env = await setup(t); if (!env) return; const {page, f} = env; await chooseModel(page);
  await page.exposeFunction('fixtureStart', body => f.makeRun(body, 'running'));
  await page.evaluate(() => {
    const previous = window.fetch.bind(window); window.fixtureOrder = [];
    window.fetch = async (url, init) => {
      if (String(url).endsWith('/runs') && init?.method === 'POST') {
        const events = await window.fixtureStart(JSON.parse(init.body)), encoder = new TextEncoder();
        return new Response(new ReadableStream({
          start(controller) { for (const e of events) controller.enqueue(encoder.encode(`id: ${e.seq}\nevent: ${e.type}\ndata: ${JSON.stringify(e)}\n\n`)); },
          cancel() { window.fixtureOrder.push('reader-cancelled'); },
        }), {headers: {'Content-Type': 'text/event-stream'}});
      }
      if (String(url).endsWith('/cancel')) { window.fixtureOrder.push('cancel-post'); const result = await previous(url, init); window.fixtureOrder.push('cancel-ack'); return result; }
      return previous(url, init);
    };
  });
  await page.fill('#chat-text', 'live-run-test'); await page.click('#chat-send'); await page.waitForFunction(() => document.getElementById('chat-messages').textContent.includes('partial-new-answer') && !document.getElementById('chat-stop').disabled);
  await page.click('#chat-stop'); await page.waitForFunction(() => document.getElementById('chat-messages').textContent.includes('用户停止') && !document.getElementById('chat-text').disabled);
  assert.deepEqual(await page.evaluate(() => window.fixtureOrder), ['cancel-post', 'cancel-ack', 'reader-cancelled']);
  assert.equal(f.requests.filter(r => r.path.endsWith('/cancel')).length, 1);
  assert.equal(f.requests.filter(r => r.path.endsWith('/runs') && r.method === 'POST').length, 0, 'no replay reached the intercepted HTTP fixture');
});

test('browser: upload beside send creates removable composer chips, history references, and output-only downloads', async t => {
  const env = await setup(t); if (!env) return; const {page, f} = env; await chooseModel(page);
  assert.equal(await page.locator('#chat-attach').evaluate(node => node.nextElementSibling.id), 'chat-send');
  assert.equal(await page.locator('.chat-files input[type=file], .chat-files input[type=checkbox]').count(), 0);
  assert.equal(await page.locator('.chat-files h2').innerText(), '输出内容');
  const chooserEvent = page.waitForEvent('filechooser'); await page.click('#chat-attach'); const chooser = await chooserEvent;
  assert.equal(chooser.isMultiple(), true);
  await chooser.setFiles([{name: 'cost-report.md', mimeType: 'text/plain', buffer: Buffer.from('synthetic report')}, {name: 'allocation.md', mimeType: 'text/plain', buffer: Buffer.from('synthetic allocation')}]);
  await page.waitForFunction(() => document.querySelectorAll('#chat-attachments .chat-attachment-chip').length === 2 && !document.getElementById('chat-attach').disabled);
  assert.equal(await page.locator('.chat-files .chat-file').count(), 2); assert.doesNotMatch(await page.locator('.chat-files').innerText(), /cost-report|allocation/);
  const uploaded = f.files.slice(-2).map(file => file.id); assert.equal(f.requests.filter(r => /\/files$/.test(r.path) && r.method === 'POST').length, 2);
  await page.getByRole('button', {name: '移除附件 cost-report.md', exact: true}).click(); assert.equal(await page.locator('#chat-attachments .chat-attachment-chip').count(), 1);
  await page.getByRole('button', {name: '引用附件 cost-report.md', exact: true}).click(); assert.equal(await page.locator('#chat-attachments .chat-attachment-chip').count(), 2);
  await page.fill('#chat-text', '请整理附件并生成报告。'); await page.click('#chat-send');
  await page.waitForFunction(() => document.getElementById('chat-messages').textContent.includes('new-answer-final') && !document.getElementById('chat-attach').disabled);
  const runs = f.requests.filter(r => r.method === 'POST' && /\/runs$/.test(r.path)); assert.equal(runs.length, 1); assert.deepEqual(new Set(runs[0].body.attachment_ids), new Set(uploaded));
  assert.equal(await page.locator('#chat-attachments').isHidden(), true); assert.equal(await page.locator('.chat-user').last().locator('.chat-attachment').count(), 2);
  await page.getByRole('button', {name: '引用附件 allocation.md', exact: true}).click(); assert.equal(await page.locator('#chat-attachments .chat-attachment-chip').count(), 1);
  const downloaded = page.waitForEvent('download'); await page.getByRole('button', {name: '下载输出 report.md v2', exact: true}).click(); assert.equal((await downloaded).suggestedFilename(), 'report.md');
  assert.equal(f.requests.filter(r => r.path.endsWith(`/files/${id(12)}/download`)).length, 1);
  const before = f.requests.length; await page.reload(); await page.waitForFunction(() => document.getElementById('chat-messages').textContent.includes('new-answer-final'));
  assert.equal(f.requests.slice(before).every(r => r.method === 'GET'), true); assert.equal(await page.locator('#chat-attachments').isHidden(), true);
  assert.equal(await page.locator('.chat-user').last().locator('.chat-attachment').count(), 2); assert.equal(await page.locator('.chat-files .chat-file').count(), 2);
  if (process.env.COCKPIT_CHAT_ARTIFACT_DIR) { fs.mkdirSync(process.env.COCKPIT_CHAT_ARTIFACT_DIR, {recursive: true}); await page.screenshot({path: path.join(process.env.COCKPIT_CHAT_ARTIFACT_DIR, 'composer-desktop.png'), fullPage: true}); }
});
test('browser: composer accepts file drops; failed batches do not disappear into the output list', async t => {
  const env = await setup(t); if (!env) return; const {page, f} = env;
  await page.evaluate(() => {
    const transfer = new DataTransfer(); transfer.items.add(new File(['hello'], 'dropped.md', {type: 'text/plain'}));
    document.getElementById('chat-compose').dispatchEvent(new DragEvent('drop', {bubbles: true, cancelable: true, dataTransfer: transfer}));
  });
  await page.waitForFunction(() => document.getElementById('chat-attachments').textContent.includes('dropped.md') && !document.getElementById('chat-attach').disabled);
  assert.equal(f.requests.filter(r => /\/files$/.test(r.path) && r.method === 'POST').length, 1); assert.equal(f.requests.some(r => /\/runs$/.test(r.path)), false);
  await page.setInputFiles('#chat-upload', [{name: 'valid.md', mimeType: 'text/plain', buffer: Buffer.from('ok')}, {name: 'unsupported.exe', mimeType: 'application/octet-stream', buffer: Buffer.from('not accepted')}]);
  await page.waitForFunction(() => document.getElementById('chat-status').textContent.includes('不支持'));
  assert.equal(f.requests.filter(r => /\/files$/.test(r.path) && r.method === 'POST').length, 1); assert.match(await page.locator('#chat-attachments').innerText(), /dropped.md/);
  assert.doesNotMatch(await page.locator('.chat-files').innerText(), /dropped|valid|unsupported/);
});
test('browser: narrow composer and long file names stay inside the viewport with accessible attachment actions', async t => {
  const env = await setup(t); if (!env) return; const {page, f} = env; await page.setViewportSize({width: 390, height: 844});
  const name = '附件文件名称很长但不应该撑开聊天输入框或下载列表-2026-10-report.md';
  await page.setInputFiles('#chat-upload', {name, mimeType: 'text/plain', buffer: Buffer.from('synthetic')});
  await page.waitForFunction(() => document.getElementById('chat-attachments').textContent.includes('report.md') && !document.getElementById('chat-attach').disabled);
  await page.locator('#chat-compose').scrollIntoViewIfNeeded();
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
  const upload = await page.locator('#chat-attach').boundingBox(), send = await page.locator('#chat-send').boundingBox();
  assert.ok(Math.abs(upload.y - send.y) < 2, 'upload is adjacent to send on narrow screens');
  await page.getByRole('button', {name: `移除附件 ${name}`, exact: true}).click(); assert.equal(await page.locator('#chat-attachments').isHidden(), true);
  await page.getByRole('button', {name: `引用附件 ${name}`, exact: true}).click(); assert.equal(await page.locator('#chat-attachments').isVisible(), true);
  assert.equal(f.requests.some(r => /\/runs$/.test(r.path)), false);
  if (process.env.COCKPIT_CHAT_ARTIFACT_DIR) { fs.mkdirSync(process.env.COCKPIT_CHAT_ARTIFACT_DIR, {recursive: true}); await page.screenshot({path: path.join(process.env.COCKPIT_CHAT_ARTIFACT_DIR, 'composer-mobile.png')}); }
});

test('browser: PDF Word Excel and image uploads become ordinary reference cards and send all selected IDs', async t => {
  const env = await setup(t); if (!env) return; const {page, f} = env; await chooseModel(page);
  const names = ['report.pdf', 'report.docx', 'report.xlsx', 'photo.png'];
  await page.setInputFiles('#chat-upload', names.map(name => ({name, mimeType: 'application/octet-stream', buffer: Buffer.from('synthetic browser metadata; real parsers tested separately')})));
  await page.waitForFunction(() => document.querySelectorAll('#chat-attachments .chat-attachment-chip').length === 4 && !document.getElementById('chat-attach').disabled);
  for (const name of names) assert.match(await page.locator('#chat-attachments').innerText(), new RegExp(name.replace('.', '\\.')));
  assert.doesNotMatch(await page.locator('.chat-files').innerText(), /report.pdf|report.docx|report.xlsx|photo.png/);
  await page.fill('#chat-text', '读取这些文档和图片'); await page.click('#chat-send');
  await page.waitForFunction(() => document.getElementById('chat-messages').textContent.includes('new-answer-final'));
  const run = f.requests.find(r => r.method === 'POST' && /\/runs$/.test(r.path));
  assert.equal(run.body.attachment_ids.length, 4); assert.equal(await page.locator('.chat-user').last().locator('.chat-attachment').count(), 4);
});

test('browser: PDF Word Excel and image outputs download from the right output list by scoped ID', async t => {
  const env = await setup(t); if (!env) return; const {page, f} = env;
  f.files = [f.files[0], ...['报告.pdf', '文档.docx', '预算.xlsx', '图表.png'].map((name, i) => ({...f.files[1], id: id(500 + i), name, version: 1, size_bytes: 4096}))];
  await page.reload(); await page.getByRole('button', {name: '下载输出 图表.png v1', exact: true}).waitFor();
  assert.equal(await page.locator('.chat-files .chat-file').count(), 4);
  for (const file of f.files.slice(1)) {
    const waiting = page.waitForEvent('download');
    await page.getByRole('button', {name: `下载输出 ${file.name} v1`, exact: true}).click();
    const download = await waiting; assert.equal(download.suggestedFilename(), file.name); assert.equal(await download.failure(), null);
    assert.equal(f.requests.filter(r => r.path.endsWith(`/files/${file.id}/download`)).length, 1);
  }
  assert.equal(countWrites(f, `/conversations/${CID}/runs`), 0);
});

test('browser: blank page accepts typing without a KEY or attachment and sends nothing by itself', async t => {
  const env = await setup(t, {blank: true, noTokens: true}); if (!env) return; const {page, f} = env;
  await page.fill('#chat-text', '没有附件也可以先输入');
  assert.equal(await page.inputValue('#chat-text'), '没有附件也可以先输入');
  assert.equal(await page.locator('#chat-send').innerText(), '发送');
  assert.equal(await page.locator('#chat-send').isDisabled(), true);
  assert.equal(await page.locator('#chat-rename').isDisabled(), true); assert.equal(await page.locator('#chat-delete').isDisabled(), true);
  assert.equal(f.requests.filter(r => r.method !== 'GET').length, 0);
});

test('browser: first plain-text send creates one chat and submits exactly once with zero attachments', async t => {
  const env = await setup(t, {blank: true}); if (!env) return; const {page, f} = env; await chooseModel(page);
  await page.fill('#chat-text', '直接发送第一条消息，不上传任何附件');
  await page.click('#chat-send');
  await page.waitForFunction(() => document.getElementById('chat-messages').textContent.includes('new-answer-final'));
  const created = f.requests.filter(r => r.method === 'POST' && r.path === '/conversations');
  const sent = f.requests.filter(r => r.method === 'POST' && /\/runs$/.test(r.path));
  assert.equal(created.length, 1); assert.equal(sent.length, 1);
  assert.equal(sent[0].path, `/conversations/${created[0].body.conversation_id}/runs`);
  assert.equal(sent[0].body.text, '直接发送第一条消息，不上传任何附件'); assert.deepEqual(sent[0].body.attachment_ids, []);
  assert.equal(sent[0].body.expected_revision, 1); assert.equal(await page.inputValue('#chat-text'), '');
  assert.equal(f.requests.filter(r => r.method === 'POST' && /\/files$/.test(r.path)).length, 0);
});

test('browser: empty draft is rejected before automatic creation; failed creation retains the typed draft and never sends', async t => {
  const env = await setup(t, {blank: true}); if (!env) return; const {page, f} = env; await chooseModel(page);
  await page.click('#chat-send'); await page.waitForFunction(() => document.getElementById('chat-status').textContent.includes('非空正文'));
  assert.equal(countWrites(f, '/conversations'), 0);
  f.failCreate = true; await page.fill('#chat-text', '创建失败时也不能丢这段正文'); await page.click('#chat-send');
  await page.waitForFunction(() => document.getElementById('chat-status').textContent.includes('创建结果待确认'));
  assert.equal(await page.inputValue('#chat-text'), '创建失败时也不能丢这段正文'); assert.equal(await page.locator('#chat-text').isDisabled(), false);
  assert.equal(countWrites(f, '/conversations'), 1); assert.equal(f.requests.filter(r => r.method === 'POST' && /\/runs$/.test(r.path)).length, 0);
});

test('browser: attaching the first file preserves an already typed blank-page draft', async t => {
  const env = await setup(t, {blank: true}); if (!env) return; const {page, f} = env;
  await page.fill('#chat-text', '这段正文不能被上传自动建聊天清掉');
  await page.setInputFiles('#chat-upload', {name: 'input.md', mimeType: 'text/plain', buffer: Buffer.from('fixture')});
  await page.waitForFunction(() => !document.getElementById('chat-attach').disabled && document.getElementById('chat-attachments').textContent.includes('input.md'));
  assert.equal(await page.inputValue('#chat-text'), '这段正文不能被上传自动建聊天清掉');
  assert.equal(countWrites(f, '/conversations'), 1); assert.equal(f.requests.filter(r => r.method === 'POST' && /\/runs$/.test(r.path)).length, 0);
});

test('browser: duplicate submits while automatic creation is pending cannot create or send twice', async t => {
  const env = await setup(t, {blank: true}); if (!env) return; const {page, f} = env; await chooseModel(page);
  let entered; const arrival = new Promise(resolve => { entered = resolve; });
  f.hold = {match: (p, method) => p === '/conversations' && method === 'POST', entered};
  await page.fill('#chat-text', '只发一次'); await page.click('#chat-send'); await arrival;
  await page.locator('#chat-compose').evaluate(form => { form.dispatchEvent(new Event('submit', {bubbles: true, cancelable: true})); form.dispatchEvent(new Event('submit', {bubbles: true, cancelable: true})); });
  f.hold.release(); await page.waitForFunction(() => document.getElementById('chat-messages').textContent.includes('new-answer-final'));
  assert.equal(countWrites(f, '/conversations'), 1); assert.equal(f.requests.filter(r => r.method === 'POST' && /\/runs$/.test(r.path)).length, 1);
});

test('browser: auth change during automatic creation clears the draft and blocks stale send', async t => {
  const env = await setup(t, {blank: true}); if (!env) return; const {page, f} = env; await chooseModel(page);
  let entered; const arrival = new Promise(resolve => { entered = resolve; });
  f.hold = {match: (p, method) => p === '/conversations' && method === 'POST', entered};
  await page.fill('#chat-text', '不能跨账号恢复的草稿'); await page.click('#chat-send'); await arrival;
  await page.evaluate(() => window.dispatchEvent(new Event('cockpit-auth-changed'))); f.hold.release();
  await page.waitForFunction(() => document.getElementById('chat-text').disabled && document.getElementById('chat-text').value === '');
  assert.equal(f.requests.filter(r => r.method === 'POST' && /\/runs$/.test(r.path)).length, 0);
});

test('browser: deleting a composer upload removes the real reference and cannot reappear after reload', async t => {
  const env = await setup(t); if (!env) return; const {page, f} = env;
  await page.setInputFiles('#chat-upload', {name:'remove-me.pdf',mimeType:'application/pdf',buffer:Buffer.from('fixture')});
  await page.waitForFunction(()=>!document.getElementById('chat-attach').disabled && document.getElementById('chat-attachments').textContent.includes('remove-me.pdf'));
  await page.getByRole('button',{name:'删除附件 remove-me.pdf',exact:true}).click();
  await page.waitForFunction(()=>document.getElementById('chat-status').textContent==='附件已删除。');
  assert.equal(await page.locator('#chat-attachments .chat-attachment-chip').count(),0);
  assert.equal(await page.getByRole('button',{name:'引用附件 remove-me.pdf',exact:true}).count(),0);
  const deletes=f.requests.filter(r=>r.method==='DELETE'&&r.path.includes('/files/'));assert.equal(deletes.length,1);assert.deepEqual(deletes[0].body,{confirm:true});
  await page.reload();await page.getByText('最终快照',{exact:false}).first().waitFor();assert.equal(await page.getByRole('button',{name:'引用附件 remove-me.pdf',exact:true}).count(),0);
  assert.equal(f.requests.filter(r=>r.method==='POST'&&r.path.endsWith('/runs')).length,0);
});

test('browser: deleting a sent attachment preserves its name with deleted status and no download/reference actions', async t => {
  const env = await setup(t); if (!env) return; const {page,f}=env;await chooseModel(page);
  await page.getByRole('button',{name:'引用附件 source.md',exact:true}).click();await page.fill('#chat-text','引用原件');await page.click('#chat-send');
  await page.waitForFunction(()=>document.getElementById('chat-messages').textContent.includes('new-answer-final'));
  await page.getByRole('button',{name:'删除附件 source.md',exact:true}).click();await page.waitForFunction(()=>document.querySelector('.chat-attachment-deleted'));
  const card=page.locator('.chat-user .chat-attachment-deleted');assert.match(await card.innerText(),/source.md.*已删除/s);assert.equal(await card.locator('button').count(),0);
  assert.equal(await page.getByRole('button',{name:'下载附件 source.md',exact:true}).count(),0);
  assert.equal(f.requests.filter(r=>r.method==='POST'&&r.path.endsWith('/runs')).length,1);
});

test('browser: uploaded attachments can be deleted during an active run, without cancel or regeneration',async t=>{
  const env=await setup(t,{active:true});if(!env)return;const{page,f}=env;
  assert.equal(await page.getByRole('button',{name:'删除附件 source.md',exact:true}).isDisabled(),false);
  await page.getByRole('button',{name:'删除附件 source.md',exact:true}).click();await page.waitForFunction(()=>document.getElementById('chat-status').textContent==='附件已删除。');
  assert.equal(await page.locator('#chat-text').isDisabled(),true);
  assert.equal(f.requests.filter(r=>r.method==='POST'&&(/\/runs$/.test(r.path)||r.path.endsWith('/cancel'))).length,0);
});

test('browser: canceling deletion makes no write; lost delete response is GET-confirmed once',async t=>{
  const env=await setup(t);if(!env)return;const{page,f}=env;
  page.removeAllListeners('dialog');page.once('dialog',d=>d.dismiss());
  await page.getByRole('button',{name:'删除附件 source.md',exact:true}).click();assert.equal(f.requests.filter(r=>r.method==='DELETE').length,0);
  page.on('dialog',d=>d.accept());f.lostFileDelete=true;
  await page.getByRole('button',{name:'删除附件 source.md',exact:true}).click();await page.waitForFunction(()=>document.getElementById('chat-status').textContent==='附件已删除。');
  assert.equal(f.requests.filter(r=>r.method==='DELETE'&&r.path.includes('/files/')).length,1);assert.ok(f.requests.some(r=>r.method==='GET'&&r.path.endsWith('/files')));
});

test('browser: delayed delete response cannot repopulate another chat or account',async t=>{
  const env=await setup(t);if(!env)return;const{page,f}=env;let entered;const arrival=new Promise(r=>{entered=r;});
  f.hold={match:(p,m)=>m==='DELETE'&&p.endsWith(`/files/${id(10)}`),entered};
  await page.getByRole('button',{name:'删除附件 source.md',exact:true}).click();await arrival;
  await page.evaluate(()=>window.dispatchEvent(new Event('cockpit-auth-changed')));f.hold.release();
  await page.waitForFunction(()=>document.getElementById('chat-text').disabled);assert.equal(await page.locator('.chat-attachment').count(),0);assert.equal(await page.inputValue('#chat-text'),'');
});

test('browser: snapshot recovery uses one state request plus only required event pages', async t => {
  const env = await setup(t, {snapshot: true}); if (!env) return;
  const {page, f} = env;
  const reads = f.requests.filter(r => r.path.startsWith(`/conversations/${CID}`));
  assert.equal(reads.filter(r => r.path.endsWith('/sync')).length, 1);
  assert.equal(reads.filter(r => r.path.endsWith('/events')).length, 1);
  assert.equal(reads.some(r => r.path === `/conversations/${CID}` || r.path.endsWith('/files') || /\/runs\/[^/]+$/.test(r.path)), false);
  assert.equal(await page.locator('.chat-assistant').count(), 1);
  assert.equal(f.requests.every(r => r.method === 'GET'), true);
});

test('browser: streaming patches retain old message nodes, selection, details and reading position', async t => {
  const env = await setup(t); if (!env) return;
  const result = await env.page.evaluate(() => {
    const C = window.CockpitChat, target = document.createElement('div'); target.id = 'incremental-fixture'; document.body.append(target);
    const projector = C.createEventProjector(), events = [], ids = n => `${n.toString(16).padStart(8, '0')}-2222-4222-8222-222222222222`;
    const emit = (type, data, run_id) => events.push({v: 1, seq: events.length + 1, conversation_id: ids(999), type, data, run_id});
    for (let n = 1; n <= 20; n++) {
      emit('run.accepted', {text: `question ${n}`, attachment_ids: [], model: 'synthetic'}, ids(n));
      emit('assistant.message', {message_id: ids(100 + n), text: `saved answer ${n}\n` + 'paragraph\n'.repeat(15), complete: true, tool_calls: []}, ids(n));
      emit('assistant.reasoning', {message_id: ids(100 + n), text: 'public summary', kind: 'summary', field: 'thinking'}, ids(n));
      emit('run.terminal', {status: 'completed', uncertain: false}, ids(n));
    }
    emit('run.accepted', {text: 'current question', attachment_ids: [], model: 'synthetic'}, ids(21));
    emit('assistant.delta', {message_id: ids(121), text: 'current'}, ids(21));
    // Recovery overlays create fresh view objects, while the underlying
    // journal-derived identity and revision stay unchanged between polls.
    const draw = () => C.renderRuns(document, target, projector.update(events).runs.map(run => ({...run, _seq: run._seq, _identity: run._identity})));
    draw();
    const article = target.children[0], saved = article.querySelector('.chat-assistant'), detail = saved.querySelector('details'); detail.open = true;
    const text = saved.querySelector('p').firstChild, range = document.createRange(); range.setStart(text, 0); range.setEnd(text, 5);
    const selection = window.getSelection(); selection.removeAllRanges(); selection.addRange(range);
    window.scrollTo(0, saved.getBoundingClientRect().top + window.scrollY - 50); const scroll = window.scrollY;
    const changes = [], observer = new MutationObserver(items => changes.push(...items)); observer.observe(target, {childList: true, subtree: true, characterData: true});
    emit('assistant.delta', {message_id: ids(121), text: '-next'}, ids(21)); draw();
    const records = observer.takeRecords();
    const retained = saved === target.children[0].querySelector('.chat-assistant') && article === target.children[0];
    const selected = selection.toString(), open = detail.open, readingDelta = Math.abs(window.scrollY - scroll);
    const oldTouched = records.some(r => article.contains(r.target));
    draw(); const unchangedWrites = observer.takeRecords().length; observer.disconnect();
    const streamingText = target.lastElementChild.querySelector('.chat-assistant p').firstChild;
    selection.setBaseAndExtent(streamingText, 0, streamingText, 7);
    emit('assistant.delta', {message_id: ids(121), text: '-again'}, ids(21)); draw();
    const streamingSelection = selection.toString();
    emit('assistant.message', {message_id: ids(121), text: 'current-next-again', complete: true, tool_calls: []}, ids(21)); draw();
    return {retained, selected, streamingSelection, finalSelection: selection.toString(), open, readingDelta, oldTouched, unchangedWrites, latest: target.lastElementChild.querySelector('.chat-assistant').textContent};
  });
  assert.equal(result.retained, true); assert.equal(result.selected, 'saved'); assert.equal(result.open, true);
  assert.equal(result.oldTouched, false); assert.equal(result.unchangedWrites, 0); assert.ok(result.readingDelta < 2);
  assert.equal(result.streamingSelection, 'current'); assert.equal(result.finalSelection, 'current'); assert.match(result.latest, /current-next-again/);
});
