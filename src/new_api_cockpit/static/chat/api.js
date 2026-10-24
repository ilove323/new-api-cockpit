(function (root, factory) {
  'use strict';
  const value = factory(typeof module === 'object' && module.exports ? {...require('./state.js'), ...require('./stream.js')} : root.CockpitChat);
  if (typeof module === 'object' && module.exports) module.exports = value;
  else Object.assign(root.CockpitChat, value);
})(typeof window === 'object' ? window : globalThis, function (C) {
  'use strict';
  class ChatAPIError extends Error {
    constructor(status, value) {
      super(typeof value.error === 'string' ? value.error : '请求失败；未自动重试。');
      this.name = 'ChatAPIError'; this.status = status; this.code = value.code || ''; this.runId = value.run_id || null; this.uncertain = value.uncertain === true;
    }
  }
  function createAPI({scope, fetch: fetcher = (...args) => window.fetch(...args), onAuth = () => {}}) {
    const base = '/cockpit/api/chat';
    const id = value => { if (!C.isUUID(value)) throw new Error('资源标识无效。'); return value; };
    async function request(path, {method = 'GET', body, ticket = scope.ticket(), controller = new AbortController(), kind = 'json', onEvent, maxFrameBytes, timeoutMs} = {}) {
      scope.track(controller, ticket);
      const timeout = setTimeout(() => controller.abort(), timeoutMs ?? (kind === 'run' ? 210000 : 30000));
      const headers = {Accept: kind === 'run' ? 'text/event-stream, application/json' : kind === 'blob' ? 'application/octet-stream' : 'application/json'};
      if (method !== 'GET') headers['X-Cockpit-Chat'] = '1';
      const multipart = typeof FormData !== 'undefined' && body instanceof FormData;
      if (body !== undefined && !multipart) headers['Content-Type'] = 'application/json';
      try {
        const response = await fetcher(base + path, {method, headers, credentials: 'same-origin', cache: 'no-store', redirect: 'error', signal: controller.signal, ...(body === undefined ? {} : {body: multipart ? body : JSON.stringify(body)})});
        scope.assert(ticket);
        if (!response.ok) {
          const value = await response.json().catch(() => ({})); scope.assert(ticket);
          const error = new ChatAPIError(response.status, value);
          if (error.code.startsWith('AUTH_') || error.code === 'CHAT_SESSION_REQUIRED' || response.status === 401) { onAuth(); throw new C.StaleResponse(); }
          throw error;
        }
        if (kind === 'run') return await C.consumeRun(response, {onEvent, assertCurrent: () => scope.assert(ticket), signal: controller.signal, maxFrameBytes});
        const value = response.status === 204 ? null : kind === 'blob' ? await response.blob() : await response.json();
        scope.assert(ticket); return value;
      } finally { clearTimeout(timeout); scope.release(controller); }
    }
    const conv = cid => `/conversations/${id(cid)}`;
    const run = (cid, rid) => `${conv(cid)}/runs/${id(rid)}`;
    return {
      config: options => request('/config', options), context: options => request('/context', options), tokens: options => request('/tokens', options),
      models: (tokenId, options) => request('/models', {...options, method: 'POST', body: {token_id: tokenId}}),
      conversations: (limit, cursor, options) => request(`/conversations?${new URLSearchParams({limit: String(limit), ...(cursor ? {cursor} : {})})}`, options),
      create: (cid, options) => request('/conversations', {...options, method: 'POST', body: {conversation_id: id(cid)}}),
      conversation: (cid, options) => request(conv(cid), options),
      rename: (cid, title, revision, options) => request(conv(cid), {...options, method: 'PATCH', body: {title, expected_revision: revision}}),
      setSkills: (cid, ids, revision, contextRevision, options) => {
        if (!Array.isArray(ids) || ids.length > 3 || new Set(ids).size !== ids.length || ids.some(value => !C.isUUID(value))
          || !Number.isSafeInteger(revision) || revision < 0 || !Number.isSafeInteger(contextRevision) || contextRevision < 0) throw new Error('聊天 Skills 或版本无效。');
        return request(conv(cid), {...options, method: 'PATCH', body: {expected_revision: revision, context_revision: contextRevision, loaded_skill_ids: [...ids]}});
      },
      remove: (cid, revision, options) => request(conv(cid), {...options, method: 'DELETE', body: {expected_revision: revision, confirm: true}}),
      events: (cid, after, until, limit, options) => request(`${conv(cid)}/events?${new URLSearchParams({after_seq: String(after), limit: String(limit), ...(until === undefined ? {} : {until_seq: String(until)})})}`, options),
      files: (cid, options) => request(`${conv(cid)}/files`, options),
      upload: (cid, file, clientId, options) => { const body = new FormData(); body.append('file', file); body.append('client_file_id', id(clientId)); return request(`${conv(cid)}/files`, {timeoutMs: 210000, ...options, method: 'POST', body}); },
      download: (cid, fid, options) => request(`${conv(cid)}/files/${id(fid)}/download`, {...options, kind: 'blob'}),
      deleteUpload: (cid, fid, options) => request(`${conv(cid)}/files/${id(fid)}`, {...options, method: 'DELETE', body: {confirm: true}}),
      start: (cid, body, options) => request(`${conv(cid)}/runs`, {...options, method: 'POST', body, kind: 'run'}),
      run: (cid, rid, options) => request(run(cid, rid), options),
      cancel: (cid, rid, options) => request(`${run(cid, rid)}/cancel`, {...options, method: 'POST', body: {}}),
      selectCandidate: (cid, rid, revision, candidate, options) => {
        if (!Number.isSafeInteger(revision) || revision < 0 || !Number.isInteger(candidate) || candidate < 0 || candidate > 3) throw new Error('候选选择参数无效。');
        return request(`${run(cid, rid)}/candidate`, {...options, method: 'POST', body: {expected_revision: revision, candidate_index: candidate}});
      },
      undoMemory: (cid, rid, contextRevision, revision, options) => {
        if (!Number.isSafeInteger(contextRevision) || contextRevision < 0 || !Number.isSafeInteger(revision) || revision < 0) throw new Error('记忆撤销版本无效。');
        return request(`${run(cid, rid)}/memory/undo`, {...options, method: 'POST', body: {context_revision: contextRevision, expected_revision: revision}});
      },
      snapshot: (cid, rid, options) => request(`${run(cid, rid)}/context`, options),
      profile: (name, body, options) => { if (!['MEMORY.md', 'SOUL.md', 'IDENTITY.md'].includes(name)) throw new Error('文档名无效。'); return request(`/profiles/${name}`, {...options, method: 'PATCH', body}); },
      skills: options => request('/skills', options),
      skill: (sid, revision, options) => request(`/skills/${id(sid)}${revision === undefined ? '' : `?context_revision=${revision}`}`, options),
      createSkill: (body, options) => request('/skills', {...options, method: 'POST', body}),
      updateSkill: (sid, body, options) => request(`/skills/${id(sid)}`, {...options, method: 'PATCH', body}),
      deleteSkill: (sid, body, options) => request(`/skills/${id(sid)}`, {...options, method: 'DELETE', body}),
    };
  }
  return {createAPI, ChatAPIError};
});
