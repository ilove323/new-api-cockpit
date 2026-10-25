'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const http = require('node:http');
const {fetchBalance, normalizeEndpoint, normalizePat, retryAfter, formatAmount, requestOwnJson, connectionError} = require('../dist/client');
const PAT = 'nap_vscode_fixture_user_pat_only';
const BALANCE = {user: {id: 12, username: 'fixture-user'}, remaining_amount: '2.500002', used_amount: '1', request_count: 7,
  quota_per_unit: 500000, amount_unit: 'Cockpit 账面单位（quota / 500000），未做货币或汇率转换'};
async function server(t, handler) {
  const app = http.createServer(handler); await new Promise(resolve => app.listen(0, '127.0.0.1', resolve));
  t.after(() => new Promise(resolve => { app.closeAllConnections(); app.close(resolve); }));
  return {endpoint: 'http://127.0.0.1:' + app.address().port + '/cockpit/mcp', pat: PAT};
}
const body = req => new Promise(resolve => { let text = ''; req.on('data', chunk => text += chunk); req.on('end', () => resolve(JSON.parse(text))); });
test('site/MCP addresses normalize; credential URLs, API paths and remote HTTP are rejected', () => {
  for (const path of ['', '/', '/cockpit', '/cockpit/', '/cockpit/mcp', '/cockpit/mcp/']) assert.equal(normalizeEndpoint('https://example.com' + path), 'https://example.com/cockpit/mcp');
  assert.equal(normalizeEndpoint('http://[::1]:9000'), 'http://[::1]:9000/cockpit/mcp');
  for (const input of ['http://example.com', 'http://127.0.0.2', 'https://user:password@example.com', 'https://example.com/?token=secret', 'https://example.com/#secret', 'https://example.com/v1', 'file:///tmp/secret', 'https://example.com/cockpit/../v1']) assert.throws(() => normalizeEndpoint(input));
  assert.equal(normalizePat(' ' + PAT + ' '), PAT);
  for (const input of ['sk-' + 'x'.repeat(48), 'a.b.c', 'password', 'Bearer ' + PAT, PAT + '\nextra']) assert.throws(() => normalizePat(input));
});
test('one refresh makes exactly one stateless get_balance HTTP call, without key reads or model endpoints', async t => {
  const calls = [];
  const connection = await server(t, async (req, res) => {
    calls.push(await body(req)); assert.equal(req.method, 'POST'); assert.equal(req.url, '/cockpit/mcp');
    assert.equal(req.headers.authorization, 'Bearer ' + PAT); assert.equal(req.headers.cookie, undefined);
    assert.equal(req.headers.accept, 'application/json, text/event-stream'); assert.equal(req.headers['mcp-protocol-version'], '2025-11-25');
    res.writeHead(200, {'Content-Type': 'application/json'}); res.end(JSON.stringify({jsonrpc: '2.0', id: calls[0].id, result: {structuredContent: BALANCE, isError: false}}));
  });
  assert.deepEqual(await fetchBalance(connection, new AbortController().signal), BALANCE);
  assert.equal(calls.length, 1); assert.equal(calls[0].method, 'tools/call'); assert.deepEqual(calls[0].params, {name: 'get_balance', arguments: {}});
  assert.equal(JSON.stringify(calls).includes(PAT), false);
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
test('response limits, malformed data and mismatched RPC ids cannot display a balance', async t => {
  for (const result of [{jsonrpc: '2.0', id: 'wrong', result: {structuredContent: BALANCE}}, 'invalid', 'x'.repeat(262145), {jsonrpc: '2.0', result: {isError: true}}]) {
    const connection = await server(t, (_req, res) => { res.writeHead(200, {'Content-Type': 'application/json'}); res.end(typeof result === 'string' ? result : JSON.stringify(result)); });
    await assert.rejects(fetchBalance(connection, new AbortController().signal));
  }
  const connection = await server(t, async (req, res) => {
    const query = await body(req); res.writeHead(200, {'Content-Type': 'application/json'});
    res.end(JSON.stringify({jsonrpc: '2.0', id: query.id, result: {structuredContent: {...BALANCE, remaining_amount: '$(warning) fake'}}}));
  });
  await assert.rejects(fetchBalance(connection, new AbortController().signal), /不完整/);
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
  for (const method of ['GET', 'PATCH', 'GET']) await requestOwnJson(connection, {
    method, path: method === 'GET' ? '/api/token/?p=1&page_size=50' : '/api/token/7', scope: 'api_key:write', signal,
    ...(method === 'PATCH' ? {body: {group: 'auto'}} : {})
  });
  await assert.rejects(requestOwnJson(connection, {method: 'GET', path: '/api/token/?p=1&page_size=50', scope: 'api_key:read', signal}), error => error.status === 403);
  await requestOwnJson(connection, {method: 'GET', path: '/api/token/?p=1&page_size=50', scope: 'api_key:read', signal});
  assert.deepEqual(calls, ['GET', 'PATCH', 'GET', 'GET', 'GET']);
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
