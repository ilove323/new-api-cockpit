/* Session/login behavior with synthetic New API responses; no real accounts. */
const test = require('node:test'), assert = require('node:assert/strict');
const fs = require('node:fs'), vm = require('node:vm'), path = require('node:path');
const {webcrypto} = require('node:crypto');
const root = path.join(__dirname, '../src/new_api_cockpit/static');
const bundle = (sid = 'session-a') => ({access_token: 'fixture-access-' + sid, session: {sid}, user: {id: 12, username: 'admin', role: 100}});
const identity = (sid = 'session-a') => ({id: 12, username: 'admin', role: 100, session_id: sid, expires_at: Math.floor(Date.now() / 1000) + 900});
const response = (body, status = 200) => new Response(JSON.stringify(body), {status, headers: {'Content-Type': 'application/json'}});
const settle = async () => { for (let i = 0; i < 5; i++) await new Promise(resolve => setImmediate(resolve)); };
class Element {
  constructor() { this.events = {}; this.value = ''; this.hidden = false; this.disabled = false; this.textContent = ''; this.dataset = {}; }
  addEventListener(name, fn) { this.events[name] = fn; }
  focus() { this.focused = true; }
  reportValidity() { return true; }
  async fire(name) { return this.events[name]?.({currentTarget: this, preventDefault() {}}); }
}
function environment({sid = 'session-a', expires = Date.now() / 1000 + 900, login = false, next = '/cockpit/keys/?user_id=2', locks = true} = {}) {
  const calls = [], redirects = [], events = {}, storage = [], channels = [], locksUsed = [];
  const elements = new Map(['logout', 'logout-status', 'official-login', 'login-status', 'login-form', 'login-username', 'login-password', 'login-submit', 'verification-form', 'verification-code', 'verification-submit', 'verification-cancel', 'login-retry', 'login-switch'].map(id => [id, new Element()]));
  const h = {calls, redirects, events, elements, storage, channels, locksUsed};
  h.handle = (url, init) => {
    if (url === '/api/user/auth/refresh') return response({success: true, data: bundle()});
    if (url === '/cockpit/api/auth/session') return response(init.method === 'DELETE' ? {ok: true} : identity());
    if (url === '/api/user/auth/logout') return response({success: true});
    if (url === '/api/status') return response({success: true, data: {password_login_enabled: true}});
    if (url === '/api/user/login' || url === '/api/user/login/verify') return response({success: true, data: bundle()});
    return response({ok: true});
  };
  const context = {
    URL, URLSearchParams, Headers, Response, AbortSignal, Promise, Date, TextEncoder, Uint8Array, crypto: webcrypto, btoa, atob, setTimeout,
    location: {origin: 'https://site.test', href: 'https://site.test/cockpit/' + (login ? 'login' : 'keys/'), pathname: login ? '/cockpit/login' : '/cockpit/keys/', search: '', replace(url) {redirects.push(url);}},
    document: {getElementById: id => elements.get(id), querySelector: selector => selector === '.login-card' ? {dataset: {next}} : selector.includes('cockpit-session') ? {content: sid} : selector.includes('cockpit-expires') ? {content: String(expires)} : null},
    navigator: locks ? {locks: {async request(name, options, fn) {locksUsed.push(name); return fn();}}} : {},
    localStorage: {setItem(k, v) {storage.push([k, v]);}, removeItem(k) {storage.push([k, null]);}},
    BroadcastChannel: class {constructor(name) {this.name = name; this.events = {}; this.messages = []; channels.push(this);} addEventListener(name, fn) {this.events[name] = fn;} postMessage(message) {this.messages.push(message);}},
    addEventListener(name, fn) {events[name] = fn;},
    async fetch(url, init = {}) {calls.push({url: String(url), init, body: init.body ? JSON.parse(init.body) : undefined}); return h.handle(String(url), init);},
  };
  context.window = context; h.context = context;
  vm.createContext(context);
  h.run = name => vm.runInContext(fs.readFileSync(path.join(root, name), 'utf8'), context);
  h.run('auth.js');
  h.api = context.CockpitAuth;
  return h;
}

test('shared session uses official refresh path, same browser lock and verified bridge, never persistent token storage', async () => {
  const h = environment({sid: '', expires: 0});
  const user = await h.api.refresh();
  assert.equal(user.session_id, 'session-a');
  assert.deepEqual(h.calls.map(c => c.url), ['/api/user/auth/refresh', '/cockpit/api/auth/session']);
  assert.deepEqual(h.locksUsed, ['new-api:auth-refresh']);
  assert.equal(h.calls[0].init.credentials, 'same-origin');
  assert.equal(h.calls[1].init.headers['X-Cockpit-Auth'], '1');
  assert.equal(h.calls[1].body.access_token, bundle().access_token);
  assert.deepEqual(h.storage, []);
});

test('concurrent requests share one renewal and carry current session identity', async () => {
  const h = environment({expires: 0});
  await Promise.all([h.context.fetch('/cockpit/api/keys/options'), h.context.fetch('/cockpit/api/users')]);
  assert.equal(h.calls.filter(c => c.url === '/api/user/auth/refresh').length, 1);
  assert.equal(h.calls.filter(c => c.url === '/cockpit/api/auth/session').length, 1);
  for (const call of h.calls.filter(c => c.url.startsWith('/cockpit/api/') && c.url !== '/cockpit/api/auth/session')) {
    assert.equal(call.init.headers.get('X-Cockpit-Session'), 'session-a');
  }
});

test('writes are never replayed after 401, reads can renew and retry once', async () => {
  const h = environment();
  h.handle = url => url === '/cockpit/api/users/quota/apply' ? response({error: 'expired'}, 401) : response({ok: true});
  const r = await h.context.fetch('/cockpit/api/users/quota/apply', {method: 'POST', body: '{}'});
  assert.equal(r.status, 401); assert.equal(h.calls.length, 1); assert.equal(h.redirects.length, 1);
  const read = environment(); let reads = 0;
  const fallback = read.handle;
  read.handle = (url, init) => url === '/cockpit/api/users' ? response({ok: true}, ++reads === 1 ? 401 : 200) : fallback(url, init);
  assert.equal((await read.context.fetch('/cockpit/api/users')).status, 200);
  assert.equal(reads, 2); assert.equal(read.redirects.length, 0);
});

test('user and KEY detail reads that may create PATs are not replayed', async () => {
  for (const path of ['/cockpit/api/users/23', '/cockpit/api/keys/42']) {
    const h = environment();
    h.handle = () => response({error: 'expired'}, 401);
    assert.equal((await h.context.fetch(path)).status, 401);
    assert.equal(h.calls.length, 1, path);
    assert.equal(h.redirects.length, 1, path);
  }
});

test('explicit PAT business calls bypass renewal and never inherit browser identity', async () => {
  const h = environment({expires: 0});
  h.handle = () => response({error: 'invalid PAT'}, 401);
  const result = await h.context.fetch('/cockpit/api/users', {
    headers: {'Authorization': 'Bearer fixture-pat', 'X-Cockpit-Session': 'old-page'},
  });
  assert.equal(result.status, 401);
  assert.equal(h.calls.length, 1);
  assert.equal(h.calls[0].init.headers.get('Authorization'), 'Bearer fixture-pat');
  assert.equal(h.calls[0].init.headers.has('X-Cockpit-Session'), false);
  assert.equal(h.calls[0].init.credentials, 'omit');
  assert.equal(h.redirects.length, 0);
});

test('switching accounts during renewal blocks the old page before any mutation is sent', async () => {
  const h = environment({expires: 0});
  h.handle = (url, init) => url === '/api/user/auth/refresh' ? response({success: true, data: bundle('session-b')}) : response(identity('session-b'));
  await assert.rejects(h.context.fetch('/cockpit/api/users/quota/apply', {method: 'POST', body: '{}'}), /切换/);
  assert.ok(!h.calls.some(c => c.url.endsWith('/apply'))); assert.equal(h.redirects.length, 1);
});

test('server-detected late account switch aborts old UI without retrying its write', async () => {
  const h = environment(); h.handle = () => response({code: 'AUTH_SESSION_CHANGED'}, 409);
  const r = await h.context.fetch('/cockpit/api/keys/2/action', {method: 'POST', body: '{}'});
  assert.equal(r.status, 409); assert.equal(h.calls.length, 1); assert.equal(h.redirects.length, 1);
});

test('upstream outage and rate limits preserve existing session without redirect or mutation', async () => {
  for (const status of [429, 503]) {
    const h = environment({expires: 0}); h.handle = () => response({message: 'temporarily unavailable'}, status);
    await assert.rejects(h.context.fetch('/cockpit/api/users/quota/apply', {method: 'POST', body: '{}'}), /unavailable/);
    assert.equal(h.calls.length, 1); assert.deepEqual(h.redirects, []);
  }
});

test('external PAT APIs and unrelated fetches are not intercepted', async () => {
  const h = environment({sid: '', expires: 0});
  for (const url of ['/cockpit/api/statistics/balance', '/cockpit/api/statistics/alert', '/api/status', 'https://other.test/api/example']) await h.context.fetch(url);
  assert.equal(h.calls.length, 4); assert.ok(h.calls.every(c => !c.init.headers));
});

test('documentation fetchOnce never replays side-effecting GETs or mixes PAT with browser session', async () => {
  const session = environment(); session.handle = () => response({error: 'expired'}, 401);
  assert.equal((await session.api.fetchOnce('/cockpit/api/keys/23')).status, 401);
  assert.equal(session.calls.length, 1);
  assert.equal(session.calls[0].init.headers.get('X-Cockpit-Session'), 'session-a');
  const pat = environment({sid: '', expires: 0});
  await pat.api.fetchOnce('/cockpit/api/users', {headers: {'Authorization': 'Bearer fixture-pat', 'X-Cockpit-Session': 'stale-session'}});
  assert.equal(pat.calls.length, 1);
  assert.equal(pat.calls[0].init.credentials, 'omit');
  assert.equal(pat.calls[0].init.headers.has('X-Cockpit-Session'), false);
  assert.equal(pat.api.safeNext('/cockpit/docs/'), '/cockpit/docs/');
});

test('logout revokes official browser session before clearing bridge and publishing sign-out', async () => {
  const h = environment(); await h.elements.get('logout').fire('click');
  assert.deepEqual(h.calls.map(c => c.url), ['/api/user/auth/logout', '/cockpit/api/auth/session']);
  assert.equal(h.calls[0].init.headers['X-Auth-Session'], 'session-a');
  assert.equal(h.calls[1].init.method, 'DELETE');
  assert.equal(h.channels[0].messages[0].kind, 'signed_out');
  assert.equal(h.channels[0].messages[0].sid, 'session-a');
  assert.equal(h.redirects.length, 1);
  const fail = environment(); fail.handle = () => response({message: 'failed'}, 503);
  await fail.elements.get('logout').fire('click');
  assert.equal(fail.calls.length, 1); assert.deepEqual(fail.redirects, []);
  assert.match(fail.elements.get('logout-status').textContent, /failed/);
});

test('official cross-tab sign-out clears only matching current session and event cannot grant access', async () => {
  const h = environment(); const receive = h.channels[0].events.message;
  receive({data: {kind: 'signed_out', sid: 'other-session', timestamp: Date.now(), source: 'other'}}); await settle();
  assert.equal(h.calls.length, 0);
  receive({data: {kind: 'signed_out', sid: 'session-a', timestamp: Date.now(), source: 'other'}}); await settle();
  assert.equal(h.calls[0].init.method, 'DELETE'); assert.equal(h.redirects.length, 1);
});

test('login automatically reuses an existing session, sanitizes next path and never prompts for a password', async () => {
  const h = environment({sid: '', expires: 0, login: true, next: '//foreign.test/'});
  h.run('login.js'); await settle();
  assert.deepEqual(h.redirects, ['/cockpit/statistics/']);
  assert.ok(!h.calls.some(c => c.url === '/api/user/login'));
  for (const value of ['https://foreign.test', '//foreign.test', '/cockpit/login', '/cockpit/keys/\n', '/cockpit\\bad']) assert.equal(h.api.safeNext(value), '/cockpit/statistics/');
});

async function loginEnvironment(settings = {}) {
  const h = environment({sid: '', expires: 0, login: true}); const fallback = h.handle;
  h.handle = (url, init) => url === '/api/user/auth/refresh' ? response({code: 'AUTH_UNAUTHORIZED'}, 401)
    : url === '/api/status' ? response({success: true, data: {password_login_enabled: true, ...settings}}) : fallback(url, init);
  h.run('login.js'); await settle(); return h;
}

test('normal login sends credentials only to official New API, clears password, bridges and returns to original page', async () => {
  const h = await loginEnvironment(); assert.equal(h.elements.get('login-form').hidden, false);
  h.elements.get('login-username').value = 'admin'; h.elements.get('login-password').value = 'fixture-password';
  await h.elements.get('login-form').fire('submit');
  const login = h.calls.find(c => c.url === '/api/user/login'); assert.equal(login.body.password, 'fixture-password');
  assert.equal(h.elements.get('login-password').value, '');
  assert.deepEqual(h.redirects, ['/cockpit/keys/?user_id=2']);
  assert.ok(!h.calls.filter(c => c.url.startsWith('/cockpit/')).some(c => c.body?.password));
});

test('2FA challenge cannot establish a session until official verification succeeds', async () => {
  const h = await loginEnvironment(); const fallback = h.handle;
  h.handle = (url, init) => url === '/api/user/login' ? response({success: true, data: {require_verification: true, flow_token: 'fixture-flow', expires_at: Date.now() / 1000 + 300, methods: [{method: '2fa', available: true}]}}) : fallback(url, init);
  h.elements.get('login-username').value = 'admin'; h.elements.get('login-password').value = 'fixture-password';
  await h.elements.get('login-form').fire('submit');
  assert.equal(h.elements.get('verification-form').hidden, false);
  assert.ok(!h.calls.some(c => c.url === '/cockpit/api/auth/session'));
  h.elements.get('verification-code').value = '123456'; await h.elements.get('verification-form').fire('submit');
  const verify = h.calls.find(c => c.url === '/api/user/login/verify');
  assert.deepEqual(verify.body, {flow_token: 'fixture-flow', method: '2fa', code: '123456'});
  assert.equal(h.elements.get('verification-code').value, ''); assert.equal(h.redirects.length, 1);
});

test('captcha/legal or disabled password login uses official login page rather than bypassing policy', async () => {
  for (const settings of [{turnstile_check: true}, {user_agreement_enabled: true}, {password_login_enabled: false}]) {
    const h = await loginEnvironment(settings);
    assert.equal(h.elements.get('login-form').hidden, true);
    const target = new URL(h.elements.get('official-login').href, 'https://site.test');
    assert.equal(target.pathname, '/sign-in');
    assert.ok(target.searchParams.get('redirect').startsWith('/cockpit/login?next='));
    assert.ok(!h.calls.some(c => c.url === '/api/user/login'));
  }
});

test('encrypted password protocol covers RSA and hybrid long passwords without a plaintext password field', async () => {
  const pair = await webcrypto.subtle.generateKey({name: 'RSA-OAEP', modulusLength: 2048, publicExponent: new Uint8Array([1, 0, 1]), hash: 'SHA-256'}, true, ['encrypt', 'decrypt']);
  const der = await webcrypto.subtle.exportKey('spki', pair.publicKey);
  const pem = '-----BEGIN PUBLIC KEY-----\n' + Buffer.from(der).toString('base64') + '\n-----END PUBLIC KEY-----';
  for (const password of ['fixture-password', 'long-password-'.repeat(30)]) {
    const h = await loginEnvironment({password_login_encryption_enabled: true}); const fallback = h.handle;
    h.handle = (url, init) => url === '/api/user/login/encryption-key' ? response({success: true, data: {kid: 'fixture-kid', public_key: pem}}) : fallback(url, init);
    h.elements.get('login-username').value = 'admin'; h.elements.get('login-password').value = password;
    await h.elements.get('login-form').fire('submit');
    const login = h.calls.find(c => c.url === '/api/user/login'); assert.ok(login); assert.equal(login.body.password, undefined);
    const encrypted = login.body.password_encrypted; let plaintext;
    if (encrypted.startsWith('v2.')) {
      const [, wrapped, iv, ciphertext] = encrypted.split('.');
      const secret = await webcrypto.subtle.decrypt({name: 'RSA-OAEP', label: new TextEncoder().encode('password-v2')}, pair.privateKey, Buffer.from(wrapped, 'base64'));
      const aes = await webcrypto.subtle.importKey('raw', secret, 'AES-GCM', false, ['decrypt']);
      plaintext = await webcrypto.subtle.decrypt({name: 'AES-GCM', iv: Buffer.from(iv, 'base64'), additionalData: new TextEncoder().encode('password-v2:fixture-kid')}, aes, Buffer.from(ciphertext, 'base64'));
    } else plaintext = await webcrypto.subtle.decrypt({name: 'RSA-OAEP'}, pair.privateKey, Buffer.from(encrypted, 'base64'));
    assert.equal(Buffer.from(plaintext).toString(), password);
    assert.equal(h.elements.get('login-password').value, '');
  }
});

test('ordinary upstream user can explicitly sign out and switch accounts instead of entering a login loop', async () => {
  const h = environment({sid: '', expires: 0, login: true}); let signedOut = false;
  h.handle = (url, init) => {
    if (url === '/api/user/auth/refresh') return signedOut ? response({code: 'AUTH_UNAUTHORIZED'}, 401) : response({success: true, data: bundle('ordinary-session')});
    if (url === '/cockpit/api/auth/session') return init.method === 'DELETE' ? response({ok: true}) : response({code: 'AUTH_FORBIDDEN', error: '仅管理员可访问'}, 403);
    if (url === '/api/user/auth/logout') {signedOut = true; return response({success: true});}
    if (url === '/api/status') return response({success: true, data: {password_login_enabled: true}});
    return response({ok: true});
  };
  h.run('login.js'); await settle();
  assert.equal(h.elements.get('login-switch').hidden, false); assert.deepEqual(h.redirects, []);
  await h.elements.get('login-switch').fire('click');
  assert.equal(h.elements.get('login-form').hidden, false);
  assert.equal(h.calls.find(c => c.url === '/api/user/auth/logout').init.headers['X-Auth-Session'], 'ordinary-session');
  assert.ok(h.channels[0].messages.some(event => event.kind === 'signed_out' && event.sid === 'ordinary-session'));
});

test('no Web Lock refresh conflict is retried once, while stale/malformed cross-tab events grant nothing', async () => {
  const h = environment({sid: '', expires: 0, locks: false}); let count = 0;
  const fallback = h.handle;
  h.handle = (url, init) => url === '/api/user/auth/refresh' && ++count === 1 ? response({code: 'AUTH_REFRESH_RACE'}, 409) : fallback(url, init);
  assert.equal((await h.api.refresh()).session_id, 'session-a'); assert.equal(count, 2);
  const before = h.calls.length;
  h.channels[0].events.message({data: {kind: 'signed_out', sid: 'session-a', timestamp: Date.now() - 70000, source: 'other'}});
  h.channels[0].events.message({data: {kind: 'admin', sid: 'session-a', timestamp: Date.now(), source: 'other'}});
  await settle(); assert.equal(h.calls.length, before); assert.deepEqual(h.redirects, []);
});
