'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const http = require('node:http');
const {KeysController} = require('../dist/keys-controller');

test('real HTTP flow is query, action, query for create/delete/group, with no duplicate config or secret reads', async t => {
  const calls = [], records = new Map([[7, {id: 7, user_id: 12, name: 'fixture', key: 'fixture_only_model_secret_0123456789',
    status: 1, group: 'custom', expired_time: -1, unlimited_quota: false, remain_quota: 5000000,
    model_limits_enabled: true, model_limits: 'custom-model', allow_ips: '203.0.113.8', cross_group_retry: false, allow_user_quota_query: false}]]);
  let id = 7, loseAcknowledgement = false; const seed = {...records.get(7)};
  const server = http.createServer(async (req, res) => {
    let text = ''; for await (const chunk of req) text += chunk;
    const body = text ? JSON.parse(text) : undefined; calls.push({method: req.method, path: req.url, body});
    assert.equal(req.headers.authorization, 'Bearer nap_local_key_flow_fixture_only');
    let data;
    if (req.url === '/api/user/self/groups') data = {custom: {desc: ''}, auto: {desc: ''}};
    else if (req.method === 'GET' && req.url.startsWith('/api/token/?')) data = {total: records.size, items: [...records.values()]};
    else if (req.method === 'POST' && req.url === '/api/token/') {const value = ++id; records.set(value, {...seed, ...body, id: value});}
    else if (req.method === 'PUT' && req.url === '/api/token/') records.set(body.id, {...records.get(body.id), ...body});
    else if (req.method === 'DELETE') records.delete(Number(req.url.split('/').at(-1)));
    else assert.fail('unexpected request ' + req.method + ' ' + req.url);
    if (loseAcknowledgement && ['PUT', 'DELETE'].includes(req.method)) {loseAcknowledgement = false; res.destroy(); return;}
    res.writeHead(200, {'Content-Type': 'application/json'}); res.end(JSON.stringify({success: true, data}));
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(() => new Promise(resolve => {server.closeAllConnections(); server.close(resolve);}));
  const controller = new KeysController(() => {}); t.after(() => controller.dispose());
  controller.setConnection({endpoint: 'http://127.0.0.1:' + server.address().port + '/cockpit/mcp', pat: 'nap_local_key_flow_fixture_only'});
  assert.equal(calls.length, 0); await controller.refresh(); await controller.groups(); calls.length = 0;
  const paths = () => calls.map(c => c.method + ' ' + c.path);
  assert.equal((await controller.create({name: 'new', group: 'custom', expired_time: -1, unlimited_quota: false, remain_quota: 1000000})).applied, true);
  assert.deepEqual(paths(), ['GET /api/token/?p=1&page_size=50', 'POST /api/token/', 'GET /api/token/?p=1&page_size=50']);
  const selected = controller.state.page.items.find(k => k.id === 7); records.get(7).remain_quota = 3456789; calls.length = 0;
  assert.equal((await controller.changeGroup(selected, 'auto')).applied, true);
  assert.deepEqual(paths(), ['GET /api/token/?p=1&page_size=50', 'PUT /api/token/', 'GET /api/token/?p=1&page_size=50']);
  assert.equal(calls[1].body.remain_quota, 3456789); assert.equal(calls[1].body.model_limits, 'custom-model');
  assert.equal(calls[1].body.allow_ips, '203.0.113.8'); assert.equal(records.get(7).group, 'auto'); calls.length = 0;
  assert.equal((await controller.remove(controller.state.page.items.find(k => k.id === 8))).applied, true);
  assert.deepEqual(paths(), ['GET /api/token/?p=1&page_size=50', 'DELETE /api/token/8', 'GET /api/token/?p=1&page_size=50']);
  assert.equal(controller.state.page.total, 1); assert.equal(records.has(8), false); calls.length = 0;
  await controller.refresh(); await controller.refresh();
  assert.deepEqual(paths(), ['GET /api/token/?p=1&page_size=50', 'GET /api/token/?p=1&page_size=50']);
  calls.length = 0; await controller.groups(); calls.length = 0; loseAcknowledgement = true;
  assert.equal((await controller.changeGroup(controller.state.page.items[0], 'custom')).applied, true);
  assert.deepEqual(paths(), ['GET /api/token/?p=1&page_size=50', 'PUT /api/token/', 'GET /api/token/?p=1&page_size=50']);
  assert.equal(controller.state.page.items[0].group, 'custom'); calls.length = 0; loseAcknowledgement = true;
  assert.equal((await controller.remove(controller.state.page.items[0])).applied, true);
  assert.deepEqual(paths(), ['GET /api/token/?p=1&page_size=50', 'DELETE /api/token/7', 'GET /api/token/?p=1&page_size=50']);
  assert.equal(controller.state.page.total, 0); assert.equal(controller.state.error, undefined);
  assert.equal((await controller.create({name: 'after lost response', group: 'custom', expired_time: -1, unlimited_quota: true, remain_quota: 0})).applied, true);
});
