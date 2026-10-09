/* Focused transport tests with fake responses, never a browser or live API. */
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {createTransport, policy, buildRequest, example} = require('../src/new_api_cockpit/static/api-docs-client.js');
const origin = 'https://fixture.test:24443';
const op = (effect = 'read', extras = {}) => ({'x-cockpit-effect': effect, 'x-cockpit-testable': true, 'x-cockpit-confirm-headers': {}, ...extras});
const spec = {paths: {
  '/cockpit/api/users': {get: op()},
  '/cockpit/api/statistics/alert': {get: op('notify', {'x-cockpit-pat-only': true})},
  '/cockpit/api/keys/{id}': {get: op('pat')},
  '/cockpit/api/users/quota/apply': {post: op('write', {'x-cockpit-confirm-headers': {'X-Quota-Action': 'confirm'}})},
  '/cockpit/api/auth/session': {post: op('session', {'x-cockpit-testable': false})},
}};
const request = (path, method = 'GET', body) => ({url: origin + path, init: {method, headers: {}, ...(body ? {body} : {})}});
const options = extras => ({spec, origin, authenticate: () => ({mode: 'session'}), confirm: async () => true, fetchOnce: async () => new Response('{}'), ...extras});

test('only current-origin documented and testable operations can send', () => {
  assert.equal(policy(spec, origin + '/cockpit/api/keys/23', origin, 'GET')['x-cockpit-effect'], 'pat');
  for (const url of ['https://other.test/cockpit/api/users', origin + '/cockpit/api/missing', origin + '/api/user/', 'https://user:pass@fixture.test:24443/cockpit/api/users']) assert.throws(() => policy(spec, url, origin, 'GET'));
  assert.throws(() => policy(spec, origin + '/cockpit/api/auth/session', origin, 'POST'));
});
test('GET alert and PAT-building GET both require confirmation; cancel sends nothing', async () => {
  let sends = 0, confirms = 0;
  const transport = createTransport(options({authenticate: () => ({mode: 'pat', token: 'fixture-pat'}), fetchOnce: async () => { sends++; }, confirm: async () => { confirms++; return false; }}));
  assert.equal(await transport(request('/cockpit/api/statistics/alert')), null);
  assert.equal(await transport(request('/cockpit/api/keys/23')), null);
  assert.equal(confirms, 2); assert.equal(sends, 0);
});
test('write sends exactly once after confirmation with required header and explicit PAT', async () => {
  let count = 0;
  const transport = createTransport(options({authenticate: () => ({mode: 'pat', token: 'fixture+pat/='}), fetchOnce: async (url, init) => {
    count++; assert.equal(init.headers.get('X-Quota-Action'), 'confirm'); assert.equal(init.headers.get('Authorization'), 'Bearer fixture+pat/='); assert.equal(init.redirect, 'error'); return new Response('{}');
  }}));
  await transport(request('/cockpit/api/users/quota/apply', 'POST', '{"user_ids":[23]}'));
  assert.equal(count, 1);
});
test('in-flight duplicates are blocked and a failed write is never retried', async () => {
  let reject, count = 0;
  const transport = createTransport(options({fetchOnce: () => { count++; return new Promise((_, fail) => { reject = fail; }); }}));
  const first = transport(request('/cockpit/api/users/quota/apply', 'POST', '{}'));
  await new Promise(resolve => setImmediate(resolve));
  await assert.rejects(transport(request('/cockpit/api/users/quota/apply', 'POST', '{}')), /上一请求/);
  reject(new Error('fixture disconnected'));
  await assert.rejects(first, /disconnected/); assert.equal(count, 1);
});
test('session mode cannot call PAT-only balance/alert', async () => {
  let sends = 0;
  const transport = createTransport(options({fetchOnce: async () => { sends++; }}));
  await assert.rejects(transport(request('/cockpit/api/statistics/alert')), /只接受管理员 PAT/);
  assert.equal(sends, 0);
});
test('parameter encoding preserves repeated and empty groups and refuses invalid IDs', () => {
  const operation = {method: 'GET', path: '/cockpit/api/users/{id}', parameters: [{name: 'id', in: 'path', required: true, schema: {type: 'integer', minimum: 1}}, {name: 'group', in: 'query', schema: {type: 'array'}}]};
  const output = buildRequest(operation, {'path:id': '23', 'query:group': '["","a+b"]'}, '', origin);
  assert.deepEqual(new URL(output.url).searchParams.getAll('group'), ['', 'a+b']);
  assert.throws(() => buildRequest(operation, {'path:id': '../api'}, '', origin));
});
test('examples use environment credentials and confirmation display redacts secrets', async () => {
  const outgoing = request('/cockpit/api/users/quota/apply', 'POST', '{"password":"fixture-secret","user_ids":[23]}');
  let target;
  await createTransport(options({confirm: async (_, text) => { target = text; return false; }}))(outgoing);
  assert.ok(!target.includes('fixture-secret')); assert.ok(target.includes('23'));
  for (const language of ['curl', 'python', 'javascript']) assert.ok(example(outgoing, language).includes('ADMIN_PAT'));
});
