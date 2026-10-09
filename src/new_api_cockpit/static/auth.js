/* Shared New API session bridge. Credentials never enter local/session storage.
 * Refresh uses the official cookie path and the same cross-tab Web Lock as New API.
 * Mutating requests are never automatically replayed after an auth failure.
 */
(() => {
  'use strict';
  const rawFetch = window.fetch.bind(window);
  const meta = name => document.querySelector(`meta[name="${name}"]`)?.content;
  let sid = meta('cockpit-session') || '';
  let expires = Number(meta('cockpit-expires') || 0);
  let pending = null, blocked = false, navigating = false;
  const identifier = () => crypto.randomUUID?.() || Array.from(crypto.getRandomValues(new Uint8Array(16)), n => n.toString(16).padStart(2, '0')).join('');
  const source = identifier();
  const channelName = 'new-api:auth-session';
  const storageKey = 'new-api:auth-session:event';
  const channel = typeof BroadcastChannel === 'function' ? new BroadcastChannel(channelName) : null;
  const pagePaths = new Set(['/cockpit', '/cockpit/', '/cockpit/statistics', '/cockpit/statistics/', '/cockpit/users', '/cockpit/users/', '/cockpit/keys', '/cockpit/keys/', '/cockpit/operations', '/cockpit/operations/', '/cockpit/api/statistics/export']);

  function safeNext(value) {
    if (typeof value !== 'string' || /[\\\x00-\x1f\x7f]/.test(value) || !value.startsWith('/cockpit')) return '/cockpit/statistics/';
    const url = new URL(value, location.origin);
    return url.origin === location.origin && pagePaths.has(url.pathname) ? value : '/cockpit/statistics/';
  }
  function loginURL() {
    return '/cockpit/login?' + new URLSearchParams({next: safeNext(location.pathname + location.search)});
  }
  function toLogin() {
    blocked = true;
    if (!navigating) { navigating = true; location.replace(loginURL()); }
  }
  async function json(url, init = {}) {
    let response;
    try {
      response = await rawFetch(url, {credentials: 'same-origin', cache: 'no-store', redirect: 'error', signal: AbortSignal.timeout(10000), ...init});
    } catch { throw new Error('登录服务连接失败，请稍后重试；未自动重发任何操作。'); }
    let data;
    try { data = await response.json(); }
    catch { throw new Error('New API 登录接口不可用，请检查版本与入口配置。'); }
    if (!response.ok || data?.success === false) {
      const error = new Error(String(data?.error || data?.message || '登录服务暂不可用，请稍后重试。').slice(0, 300));
      error.status = response.status;
      error.code = data?.code;
      throw error;
    }
    return data;
  }
  const bridge = (method, body = {}) => json('/cockpit/api/auth/session', {
    method, headers: {'Content-Type': 'application/json', 'X-Cockpit-Auth': '1'}, body: JSON.stringify(body),
  });
  function publish(kind, sessionID) {
    const event = {kind, sid: sessionID, source, nonce: identifier(), timestamp: Date.now()};
    if (channel) channel.postMessage(event);
    else {
      try { localStorage.setItem(storageKey, JSON.stringify(event)); localStorage.removeItem(storageKey); }
      catch { /* Events contain no token and are only best-effort UI synchronization. */ }
    }
  }
  async function accept(bundle, announce = false) {
    if (typeof bundle?.access_token !== 'string' || !bundle?.session?.sid) throw new Error('登录响应不完整，请重新登录。');
    // New API is revalidated server-side; frontend user/role fields grant nothing.
    let user;
    try { user = await bridge('POST', {access_token: bundle.access_token}); }
    catch (error) { error.sessionID = bundle.session.sid; throw error; }
    if (user.session_id !== bundle.session.sid) throw new Error('登录会话不一致，请重新加载页面。');
    sid = user.session_id; expires = user.expires_at;
    if (announce) publish('authenticated', sid);
    return user;
  }
  async function runRefresh() {
    for (let attempt = 0; attempt < 2; attempt++) {
      try {
        const result = await json('/api/user/auth/refresh', {method: 'POST', headers: sid ? {'X-Auth-Session': sid} : {}});
        return await accept(result.data);
      } catch (error) {
        if (error.code === 'AUTH_REFRESH_RACE' && attempt === 0) {
          await new Promise(resolve => setTimeout(resolve, 350));
          continue;
        }
        if (error.status === 401) return null;
        if (error.code === 'AUTH_SESSION_MISMATCH') { toLogin(); throw error; }
        throw error;
      }
    }
  }
  function refresh() {
    if (!pending) {
      pending = (navigator.locks?.request
        ? navigator.locks.request('new-api:auth-refresh', {mode: 'exclusive'}, runRefresh)
        : runRefresh()).finally(() => { pending = null; });
    }
    return pending;
  }
  async function ensure(force = false) {
    if (blocked) throw new Error('登录状态已变化，请重新加载页面。');
    if (!force && sid && expires > Date.now() / 1000 + 30) return;
    const previous = sid;
    const user = await refresh();
    if (!user) { toLogin(); throw new Error('请重新登录后再操作；未自动重发请求。'); }
    if (previous && previous !== user.session_id) { toLogin(); throw new Error('登录账号已切换，请重新加载页面。'); }
  }
  function isProtected(url) {
    return url.origin === location.origin && url.pathname.startsWith('/cockpit/api/')
      && !['/cockpit/api/statistics/balance', '/cockpit/api/statistics/alert'].includes(url.pathname);
  }
  window.fetch = async (input, init = {}) => {
    const url = new URL(typeof input === 'string' || input instanceof URL ? input : input.url, location.href);
    if (!isProtected(url)) return rawFetch(input, init);
    await ensure();
    const headers = new Headers(init.headers || (typeof input === 'object' ? input.headers : undefined));
    headers.set('X-Cockpit-Session', sid);
    const options = {...init, headers, credentials: 'same-origin'};
    const response = await rawFetch(input, options);
    if (response.status === 409) {
      const data = await response.clone().json().catch(() => ({}));
      if (data.code === 'AUTH_SESSION_CHANGED') toLogin();
    }
    if (response.status !== 401) return response;
    const method = (init.method || (typeof input === 'object' ? input.method : '') || 'GET').toUpperCase();
    if (!['GET', 'HEAD'].includes(method)) { toLogin(); return response; }
    await ensure(true);
    headers.set('X-Cockpit-Session', sid);
    return rawFetch(input, options); // Read-only retry, at most once.
  };
  async function sync(event) {
    if (!event || event.source === source || typeof event.sid !== 'string' || !event.sid
        || typeof event.timestamp !== 'number' || Math.abs(Date.now() - event.timestamp) > 60000) return;
    const relevant = event.kind === 'signed_out' && event.sid === sid
      || event.kind === 'authenticated' && sid && event.sid !== sid;
    if (!relevant) return;
    blocked = true;
    try { await bridge('DELETE'); } finally { toLogin(); }
  }
  channel?.addEventListener('message', event => { sync(event.data).catch(toLogin); });
  window.addEventListener('storage', event => {
    if (event.key === storageKey && event.newValue) {
      try { sync(JSON.parse(event.newValue)).catch(toLogin); } catch { /* Untrusted event. */ }
    }
  });
  document.getElementById('logout')?.addEventListener('click', async event => {
    if (blocked) return;
    const button = event.currentTarget, status = document.getElementById('logout-status');
    button.disabled = true; blocked = true;
    try {
      // Browser sends New API's HttpOnly refresh cookie only to its official path.
      await json('/api/user/auth/logout', {method: 'POST', headers: sid ? {'X-Auth-Session': sid} : {}});
      await bridge('DELETE');
      publish('signed_out', sid);
      toLogin();
    } catch (error) {
      if (status) status.textContent = error.message;
      button.disabled = false; blocked = false;
    }
  });
  async function clear() { await bridge('DELETE'); sid = ''; expires = 0; }
  window.CockpitAuth = {refresh, accept, ensure, safeNext, json, publish, clear};
})();
