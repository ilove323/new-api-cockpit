'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const http = require('node:http');
const {fetchBalance, normalizeEndpoint, normalizePat, retryAfter, formatAmount, requestOwnJson, connectionError} = require('../dist/client');
const PAT = 'nap_vscode_fixture_user_pat_only';
const PROFILE = {id: 12, username: 'fixture-user', status: 1, quota: 1250001, used_quota: 500000, request_count: 7};
const BALANCE = {user: {id: 12, username: 'fixture-user'}, remaining_amount: '2.500002', used_amount: '1', request_count: 7,
  quota_per_unit: 500000, amount_unit: '账面单位（quota / 500000），未做货币或汇率转换'};
async function server(t, handler) {
  const app = http.createServer(handler); await new Promise(resolve => app.listen(0, '127.0.0.1', resolve));
  t.after(() => new Promise(resolve => { app.closeAllConnections(); app.close(resolve); }));
  return {endpoint: 'http://127.0.0.1:' + app.address().port, pat: PAT};
}
const reply = (res, data) => {res.writeHead(200, {'Content-Type': 'application/json'}); res.end(JSON.stringify(data));};
test('site addresses normalize to the origin, including previously saved MCP paths, without accepting unsafe URLs', () => {
  for (const path of ['', '/', '/cockpit', '/cockpit/', '/cockpit/mcp', '/cockpit/mcp/']) assert.equal(normalizeEndpoint('https://example.com' + path), 'https://example.com');
  assert.equal(normalizeEndpoint('http://[::1]:9000'), 'http://[::1]:9000');
  for (const input of ['http://example.com', 'http://127.0.0.2', 'https://user:password@example.com', 'https://example.com/?token=secret', 'https://example.com/#secret', 'https://example.com/v1', 'file:///tmp/secret', 'https://example.com/cockpit/../v1']) assert.throws(() => normalizeEndpoint(input));
  assert.equal(normalizePat(' ' + PAT + ' '), PAT);
  for (const input of ['sk-' + 'x'.repeat(48), 'a.b.c', 'password', 'Bearer ' + PAT, PAT + '\nextra']) assert.throws(() => normalizePat(input));
});
test('balance works with only New API: cold query reads self and public units; warm query reads self only', async t => {
  const calls = [];
  const connection = await server(t, (req, res) => {
    calls.push(req.method + ' ' + req.url);
    assert.equal(req.method, 'GET'); assert.equal(req.headers.cookie, undefined);
    assert.equal(req.headers.accept, 'application/json'); assert.equal(req.headers['mcp-protocol-version'], undefined);
    if (req.url === '/api/user/self') {
      assert.equal(req.headers.authorization, 'Bearer ' + PAT);
      reply(res, {success: true, data: PROFILE});
    } else if (req.url === '/api/status') {
      assert.equal(req.headers.authorization, undefined);
      reply(res, {success: true, data: {quota_per_unit: 500000}});
    } else {res.writeHead(404); res.end(); assert.fail('Cockpit or model path must not be requested');}
  });
  assert.deepEqual(await fetchBalance({...connection, endpoint: connection.endpoint + '/cockpit/mcp'}, new AbortController().signal), BALANCE);
  assert.deepEqual(calls, ['GET /api/user/self', 'GET /api/status']);
  assert.deepEqual(await fetchBalance(connection, new AbortController().signal, 10000, 500000), BALANCE);
  assert.deepEqual(calls, ['GET /api/user/self', 'GET /api/status', 'GET /api/user/self']);
});
test('redirects never forward the PAT, and errors do not expose server bodies', async t => {
  let forwarded = 0;
  const other = await server(t, (_req, res) => { forwarded++; res.end(PAT); });
  const connection = await server(t, (_req, res) => { res.writeHead(302, {Location: other.endpoint}); res.end(PAT); });
  await assert.rejects(fetchBalance(connection, new AbortController().signal), error => error.status === 302 && !error.message.includes(PAT));
  assert.equal(forwarded, 0);
});
test('429 keeps Retry-After and is not retried', async t => {
  let calls = 0;
  const connection = await server(t, (_req, res) => { calls++; res.writeHead(429, {'Retry-After': '3600'}); res.end('private ' + PAT); });
  const now = Date.now();
  await assert.rejects(fetchBalance(connection, new AbortController().signal), error => error.status === 429 && error.retryAt >= now + 3600000 && !error.message.includes(PAT));
  assert.equal(calls, 1);
  for (const input of [undefined, 'invalid', '1', '9'.repeat(100)]) assert.equal(retryAfter(input, 1000), 901000);
  const future = new Date(Date.now() + 7200000).toUTCString(); assert.equal(retryAfter(future), Date.parse(future));
});
test('401/403 remain auth errors rather than zero balances', async t => {
  for (const status of [401, 403]) {
    const connection = await server(t, (_req, res) => { res.writeHead(status); res.end(PAT); });
    await assert.rejects(fetchBalance(connection, new AbortController().signal), error => error.status === status && !error.message.includes(PAT));
  }
});
test('response limits, unsuccessful responses and malformed profile data cannot display a balance', async t => {
  for (const result of [{success: false, message: PAT}, 'invalid', 'x'.repeat(262145), {success: true, data: {...PROFILE, quota: '$(warning) fake'}},
    {success: true, data: {...PROFILE, quota: Number.MAX_SAFE_INTEGER + 1}}, {success: true, data: {...PROFILE, username: 'invalid\nname'}}]) {
    const connection = await server(t, (_req, res) => {res.writeHead(200, {'Content-Type': 'application/json'}); res.end(typeof result === 'string' ? result : JSON.stringify(result));});
    await assert.rejects(fetchBalance(connection, new AbortController().signal), e => !e.message.includes(PAT));
  }
});
test('exact quota conversion preserves zero, negative balances, custom units and safe-integer precision', async t => {
  for (const [quota, unit, expected] of [[0, 500000, '0'], [-1, 500000, '-0.000002'], [1, 1000000, '0.000001'], [Number.MAX_SAFE_INTEGER, 500000, '18014398509.481982']]) {
    const connection = await server(t, (req, res) => {assert.equal(req.url, '/api/user/self'); reply(res, {success: true, data: {...PROFILE, quota}});});
    const balance = await fetchBalance(connection, new AbortController().signal, 10000, unit);
    assert.equal(balance.remaining_amount, expected);
  }
});
test('disabled accounts and missing unit metadata fail clearly rather than fabricating a zero or a default unit', async t => {
  const disabled = await server(t, (_req, res) => reply(res, {success: true, data: {...PROFILE, status: 2}}));
  await assert.rejects(fetchBalance(disabled, new AbortController().signal), e => e.status === 403);
  for (const unit of [undefined, 0, -1, '500000', 1.5]) {
    const connection = await server(t, (req, res) => reply(res, {success: true, data: req.url === '/api/user/self' ? PROFILE : {quota_per_unit: unit}}));
    await assert.rejects(fetchBalance(connection, new AbortController().signal), /额度单位/);
  }
});
test('hung connections time out without a retry and cancellation stops the request', async t => {
  let calls = 0; const connection = await server(t, () => { calls++; });
  await assert.rejects(fetchBalance(connection, new AbortController().signal, 50), /超时/);
  assert.equal(calls, 1);
  const abort = new AbortController(); abort.abort();
  await assert.rejects(fetchBalance(connection, abort.signal), /取消/); assert.equal(calls, 1);
});
test('money formatting preserves small balances and never uses a floating-point conversion', () => {
  assert.equal(formatAmount('12345678901234567890123.120000'), '12,345,678,901,234,567,890,123.12');
  assert.equal(formatAmount('0.000002'), '0.000002'); assert.equal(formatAmount('-12.5'), '-12.50'); assert.equal(formatAmount('0'), '0.00');
});
test('sequential query/write/query reuses connections, including after a drained HTTP error response', async t => {
  const calls = [], sockets = new Set();
  const connection = await server(t, (req, res) => {
    sockets.add(req.socket); calls.push(req.method); req.resume();
    res.writeHead(calls.length === 4 ? 403 : 200, {'Content-Type': 'application/json'});
    res.end(JSON.stringify({success: calls.length !== 4}));
  });
  const signal = new AbortController().signal;
  for (const method of ['GET', 'PUT', 'GET']) await requestOwnJson(connection, {
    method, path: method === 'GET' ? '/api/token/?p=1&page_size=50' : '/api/token/', scope: 'api_key:read', signal,
    ...(method === 'PUT' ? {body: {id: 7}} : {})
  });
  await assert.rejects(requestOwnJson(connection, {method: 'GET', path: '/api/token/?p=1&page_size=50', scope: 'api_key:read', signal}), error => error.status === 403);
  await requestOwnJson(connection, {method: 'GET', path: '/api/token/?p=1&page_size=50', scope: 'api_key:read', signal});
  assert.deepEqual(calls, ['GET', 'PUT', 'GET', 'GET', 'GET']);
  // The host fetch pool may alternate between two sockets while body reads
  // finish. It must not open a new connection for every sequential request.
  assert(sockets.size <= 2, 'five sequential requests reuse the transport pool');
});
test('network errors preserve safe codes and do not mislabel a connection reset as a certificate problem', () => {
  const reset = connectionError({code: 'ECONNRESET', message: 'private ' + PAT});
  assert.match(reset.message, /ECONNRESET/); assert.doesNotMatch(reset.message, /证书/); assert.equal(reset.message.includes(PAT), false);
  assert.equal(connectionError({message: 'fetch failed ' + PAT, cause: {code: 'ECONNRESET', message: PAT}}).message, reset.message);
  assert.match(connectionError({code: 'CERT_HAS_EXPIRED'}).message, /证书校验失败/);
  assert.match(connectionError({code: 'ENOTFOUND'}).message, /域名解析失败/);
  assert.equal(connectionError({message: PAT}).message.includes(PAT), false);
});
