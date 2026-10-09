/* Passwords and verification codes go straight to official, same-origin New API. */
(() => {
  'use strict';
  const auth = window.CockpitAuth, $ = id => document.getElementById(id);
  const next = auth.safeNext(document.querySelector('.login-card').dataset.next);
  $('official-login').href = '/sign-in?' + new URLSearchParams({redirect: '/cockpit/login?' + new URLSearchParams({next})});
  let settings = null, challenge = null, busy = false, forbiddenSID = '';
  const status = (text, error = false) => { $('login-status').textContent = text; $('login-status').className = error ? 'error' : ''; };
  const enabled = value => { busy = !value; $('login-submit').disabled = !value; $('verification-submit').disabled = !value; $('login-retry').disabled = !value; $('login-switch').disabled = !value; };
  function resetChallenge() {
    challenge = null; $('verification-code').value = ''; $('verification-form').hidden = true;
    $('login-form').hidden = !settings || settings.password_login_enabled === false || Boolean(settings.turnstile_check || settings.user_agreement_enabled || settings.privacy_policy_enabled);
  }
  async function start() {
    enabled(false); status('正在检查已有登录…'); $('login-retry').hidden = true;
    try {
      const user = await auth.refresh();
      if (user) { status('登录成功，正在进入…'); location.replace(next); return; }
      const result = await auth.json('/api/status'); settings = result.data;
      if (!settings || typeof settings !== 'object') throw new Error('New API 站点配置读取失败。');
      resetChallenge();
      status($('login-form').hidden ? '本站启用了额外登录验证，请使用下方 New API 登录页，完成后将自动返回。' : '请使用 New API 管理员账号登录。');
    } catch (error) {
      status(error.message, true); $('login-retry').hidden = false;
      if (error.code === 'AUTH_FORBIDDEN' && error.sessionID) {
        forbiddenSID = error.sessionID; $('login-switch').hidden = false;
        status('当前 New API 账号没有管理权限，请退出当前登录后切换管理员账号。', true);
      }
    }
    finally { enabled(true); }
  }
  const base64 = bytes => btoa(String.fromCharCode(...new Uint8Array(bytes)));
  async function passwordFields(password) {
    if (!settings.password_login_encryption_enabled) return {password};
    if (!crypto.subtle) throw new Error('当前环境不支持密码加密，请使用 HTTPS 或 New API 登录页。');
    const {data} = await auth.json('/api/user/login/encryption-key');
    if (!data?.kid || !data.public_key) throw new Error('密码加密公钥不可用，请稍后重试。');
    const der = Uint8Array.from(atob(data.public_key.replace(/-----[^-]+-----|\s/g, '')), c => c.charCodeAt(0));
    const key = await crypto.subtle.importKey('spki', der, {name: 'RSA-OAEP', hash: 'SHA-256'}, false, ['encrypt']);
    const bytes = new TextEncoder().encode(password);
    let encrypted;
    if (bytes.length <= key.algorithm.modulusLength / 8 - 66) encrypted = base64(await crypto.subtle.encrypt({name: 'RSA-OAEP'}, key, bytes));
    else {
      const secret = crypto.getRandomValues(new Uint8Array(32)), iv = crypto.getRandomValues(new Uint8Array(12));
      const aes = await crypto.subtle.importKey('raw', secret, 'AES-GCM', false, ['encrypt']);
      const wrapped = await crypto.subtle.encrypt({name: 'RSA-OAEP', label: new TextEncoder().encode('password-v2')}, key, secret);
      const ciphertext = await crypto.subtle.encrypt({name: 'AES-GCM', iv, additionalData: new TextEncoder().encode('password-v2:' + data.kid)}, aes, bytes);
      encrypted = ['v2', base64(wrapped), base64(iv), base64(ciphertext)].join('.');
    }
    return {password_encrypted: encrypted, encryption_key_id: data.kid};
  }
  async function finish(result) {
    const data = result.data;
    if (data?.require_verification === true) {
      if (!data.flow_token || !Number.isFinite(data.expires_at) || data.expires_at <= Date.now() / 1000) throw new Error('二次验证已过期，请重新登录。');
      if (!data.methods?.some(item => item.method === '2fa' && item.available === true)) {
        status('当前账号要求通行密钥等验证，请使用下方 New API 登录页。');
        return;
      }
      challenge = data; $('login-form').hidden = true; $('verification-form').hidden = false;
      $('verification-code').focus(); status('请输入动态验证码或恢复码。');
      return;
    }
    await auth.accept(data, true);
    challenge = null; $('verification-code').value = ''; status('登录成功，正在进入…'); location.replace(next);
  }
  $('login-form').addEventListener('submit', async event => {
    event.preventDefault(); if (busy || !settings || !event.currentTarget.reportValidity()) return;
    enabled(false); status('正在登录…');
    let password = $('login-password').value; $('login-password').value = '';
    try {
      const fields = await passwordFields(password); password = '';
      const result = await auth.json('/api/user/login', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({username: $('login-username').value.trim(), ...fields})});
      await finish(result);
    } catch (error) { status(error.message, true); }
    finally { password = ''; $('login-password').value = ''; enabled(true); }
  });
  $('verification-form').addEventListener('submit', async event => {
    event.preventDefault(); if (busy || !event.currentTarget.reportValidity()) return;
    if (!challenge || challenge.expires_at <= Date.now() / 1000) { resetChallenge(); status('二次验证已过期，请重新登录。', true); return; }
    enabled(false); const code = $('verification-code').value.trim(); $('verification-code').value = '';
    try {
      await finish(await auth.json('/api/user/login/verify', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({flow_token: challenge.flow_token, method: '2fa', code})}));
    } catch (error) { status(error.message, true); }
    finally { $('verification-code').value = ''; enabled(true); }
  });
  $('verification-cancel').addEventListener('click', () => { if (!busy) { resetChallenge(); status('请重新输入账号密码。'); } });
  $('login-retry').addEventListener('click', start);
  $('login-switch').addEventListener('click', async () => {
    if (busy || !forbiddenSID) return;
    enabled(false);
    try {
      await auth.json('/api/user/auth/logout', {method: 'POST', headers: {'X-Auth-Session': forbiddenSID}});
      await auth.clear(); auth.publish('signed_out', forbiddenSID);
      forbiddenSID = ''; $('login-switch').hidden = true; await start();
    } catch (error) { status(error.message, true); }
    finally { enabled(true); }
  });
  window.addEventListener('pagehide', () => { challenge = null; $('login-password').value = ''; $('verification-code').value = ''; });
  start();
})();
