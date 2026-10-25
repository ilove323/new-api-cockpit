'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const http = require('node:http');
const {KeysClient, KeyWriteError, amountToQuota, validateName} = require('../dist/keys-client');
const {requestOwnJson} = require('../dist/client');
const PAT = 'nap_key_manager_fixture_not_real';
const SECRET = 'fixture_model_key_01234567890123456789';
const GROUPS = ['', '研发', '海外', 'auto'].map(name => ({name, description: ''}));
const raw = changes => ({id: 7, user_id: 12, name: 'fixture key', key: SECRET, group: '研发', status: 1, expired_time: -1,
  unlimited_quota: false, remain_quota: 2345678, model_limits_enabled: true, model_limits: 'custom-model',
  allow_ips: '203.0.113.1\n203.0.113.2', cross_group_retry: false, allow_user_quota_query: false, ...changes});
async function fixture(t, hook) {
  const calls = [], server = http.createServer(async (req, res) => {
    let text = ''; for await (const chunk of req) text += chunk;
    const body = text ? JSON.parse(text) : undefined; calls.push({path: req.url, method: req.method, body});
    assert.equal(req.headers.authorization, 'Bearer ' + PAT); assert.equal(req.headers['new-api-user'], undefined);
    assert.equal(req.headers.cookie, undefined);
    const reply = value => {res.writeHead(200, {'Content-Type': 'application/json'}); res.end(JSON.stringify(value));};
    if (hook && await hook({req, res, body, reply})) return;
    if (req.url === '/api/user/self/groups') reply({success: true, data: {'研发': {desc: '开发专用'}, '海外': {desc: ''}, auto: {desc: '自动'}}});
    else if (req.url.startsWith('/api/token/?')) reply({success: true, data: {total: 51, items: [raw()]}});
    else if (req.url === '/api/token/7/key') reply({success: true, data: {key: SECRET}});
    else if (req.url === '/api/token/7' && req.method === 'GET') reply({success: true, data: raw()});
    else reply({success: true});
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(() => new Promise(resolve => {server.closeAllConnections(); server.close(resolve);}));
  const connection = {endpoint: 'http://127.0.0.1:' + server.address().port + '/cockpit/mcp', pat: PAT};
  return {calls, connection, client: new KeysClient(connection), signal: new AbortController().signal};
}
test('list is one owner-scoped page, never reads full keys and retains only masked values', async t => {
  const f = await fixture(t); const page = await f.client.list(1, f.signal);
  assert.equal(f.calls.length, 1); assert.equal(f.calls[0].path, '/api/token/?p=1&page_size=50');
  assert.equal(page.total, 51); assert.equal(page.items[0].group, '研发');
  assert.equal(JSON.stringify(page).includes(SECRET), false);
  await f.client.list(2, f.signal); assert.equal(f.calls[1].path, '/api/token/?p=2&page_size=50');
  assert.equal(f.calls.some(c => c.path.endsWith('/key')), false);
});
test('groups come from the current user route, with custom names, default and auto', async t => {
  const f = await fixture(t), groups = await f.client.groups(f.signal);
  assert.deepEqual(groups.map(g => g.name), ['', '研发', '海外', 'auto']);
  assert.equal(f.calls.length, 1); assert.equal(f.calls[0].path, '/api/user/self/groups');
});
test('create sends one personal-key write with chosen limits, without account quota/user fields or key reveal', async t => {
  const f = await fixture(t); await f.client.create({name: 'new key', group: '海外', remain_quota: 5000000, unlimited_quota: false, expired_time: -1}, f.signal, GROUPS);
  const write = f.calls.find(c => c.method === 'POST'); assert.equal(write.path, '/api/token/');
  assert.equal(write.body.remain_quota, 5000000); assert.equal(write.body.group, '海外'); assert.equal(write.body.unlimited_quota, false);
  assert.equal(write.body.cross_group_retry, false); assert.equal(write.body.expired_time, -1);
  for (const field of ['user_id', 'used_quota', 'key', 'status']) assert.equal(field in write.body, false);
  assert.equal(f.calls.filter(c => c.method === 'POST').length, 1); assert.equal(f.calls.some(c => c.path.endsWith('/key')), false);
  assert.equal(f.calls.length, 1, 'creation uses the chosen cached groups without querying them again');
});
test('unavailable groups and invalid names cannot dispatch a mutation', async t => {
  const f = await fixture(t);
  await assert.rejects(f.client.create({name: 'fixture', group: 'not-authorized', remain_quota: 1, unlimited_quota: false, expired_time: -1}, f.signal, GROUPS));
  assert.equal(f.calls.some(c => c.method !== 'GET'), false);
  assert.throws(() => validateName('中'.repeat(17)));
  assert.throws(() => validateName('bad\nname'));
});
test('group changes read latest configuration and preserve exact quota, expiry, model and IP restrictions', async t => {
  const latest = raw({remain_quota: 1234567, expired_time: 2100000000});
  const f = await fixture(t, async ({req, reply}) => {
    if (req.url.startsWith('/api/token/?')) {reply({success: true, data: {total: 1, items: [latest]}}); return true;}
  });
  const key = (await f.client.list(1, f.signal)).items[0]; await f.client.changeGroup(key, 'auto', f.signal, GROUPS);
  const writes = f.calls.filter(c => c.method === 'PUT'); assert.equal(writes.length, 1);
  const body = writes[0].body;
  for (const field of ['name', 'expired_time', 'remain_quota', 'unlimited_quota', 'model_limits_enabled', 'model_limits', 'allow_ips', 'allow_user_quota_query']) assert.equal(body[field], latest[field]);
  assert.equal(body.group, 'auto'); assert.equal(body.cross_group_retry, false); assert.equal(body.auto_groups, null);
  for (const field of ['key', 'user_id', 'used_quota', 'status']) assert.equal(field in body, false);
  assert.deepEqual(f.calls.map(c => c.method + ' ' + c.path), ['GET /api/token/?p=1&page_size=50', 'PUT /api/token/']);
});
test('incomplete queried configurations block the group write instead of inventing defaults', async t => {
  for (const change of [{model_limits_enabled: null}, {allow_user_quota_query: undefined}, {cross_group_retry: undefined}, {allow_ips: 1}]) {
    const f = await fixture(t, async ({req, reply}) => {
      if (req.url.startsWith('/api/token/?')) {reply({success: true, data: {total: 1, items: [raw(change)]}}); return true;}
    });
    const key = (await f.client.list(1, f.signal)).items[0];
    await assert.rejects(f.client.changeGroup(key, 'auto', f.signal, GROUPS)); assert.equal(f.calls.some(c => c.method === 'PUT'), false);
  }
});
test('delete is one owner-scoped request; complete keys are read only by the separate explicit reveal', async t => {
  const f = await fixture(t); await f.client.remove(7, f.signal);
  assert.deepEqual(f.calls.map(c => [c.method, c.path]), [['DELETE', '/api/token/7']]);
  assert.equal(await f.client.reveal(7, f.signal), 'sk-' + SECRET);
  assert.equal(f.calls[1].path, '/api/token/7/key'); assert.equal(f.calls[1].method, 'POST');
});
test('a lost mutation response is uncertain, not retried; explicit rejection is not reported as success', async t => {
  const f = await fixture(t, async ({req, res}) => {if (req.method === 'DELETE') {res.destroy(); return true;}});
  await assert.rejects(f.client.remove(7, f.signal), error => error instanceof KeyWriteError && error.uncertain);
  assert.equal(f.calls.length, 1);
  const denied = await fixture(t, async ({reply}) => {reply({success: false, message: 'sensitive ' + PAT}); return true;});
  await assert.rejects(denied.client.remove(7, denied.signal), error => error instanceof KeyWriteError && !error.uncertain && !error.message.includes(PAT));
});
test('permission failures identify the required scope without revealing server content', async t => {
  const f = await fixture(t, async ({res}) => {res.writeHead(403); res.end(PAT); return true;});
  await assert.rejects(f.client.reveal(7, f.signal), e => e.requiredScope === 'api_key:reveal' && !e.message.includes(PAT));
  await assert.rejects(f.client.list(1, f.signal), e => e.requiredScope === 'api_key:read');
});
test('transport cannot be used as an arbitrary/admin API proxy', async t => {
  const f = await fixture(t);
  for (const path of ['/api/user/', '/api/user/99', '//evil.invalid/api/token/', '/api/token/7/key?other=1']) {
    await assert.rejects(requestOwnJson(f.connection, {method: 'GET', path, scope: 'api_key:read', signal: f.signal}));
  }
  assert.equal(f.calls.length, 0);
});
test('quota conversion is decimal-exact and rejects silent rounding', () => {
  assert.equal(amountToQuota('10.123456', 500000), 5061728); assert.equal(amountToQuota('0.000002', 500000), 1);
  for (const value of ['0.000001', '0', '-1', '1e3', '10000000000000000']) assert.throws(() => amountToQuota(value, 500000));
});
