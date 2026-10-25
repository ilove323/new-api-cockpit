/* In-memory state only. No credentials or message bodies are persisted by this UI. */
(function (root, factory) {
  'use strict';
  const value = factory();
  if (typeof module === 'object' && module.exports) module.exports = value;
  else Object.assign(root.CockpitChat ||= {}, value);
})(typeof window === 'object' ? window : globalThis, function () {
  'use strict';
  const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;
  const TERMINAL = new Set(['completed', 'failed', 'cancelled', 'interrupted']);
  const TYPES = new Set(['conversation.created', 'conversation.renamed', 'conversation.skills.updated', 'file.added', 'run.accepted', 'run.dispatching', 'assistant.delta', 'assistant.message', 'assistant.reasoning', 'assistant.protocol', 'candidate.selected', 'run.notice', 'context.compacted', 'context.selected', 'memory.proposed', 'memory.updated', 'memory.reverted', 'tool.call', 'tool.result', 'run.usage', 'run.terminal']);
  const PURPOSES = ['main', 'summary', 'native_compact', 'memory'];
  const purposeOf = value => PURPOSES.includes(value) ? value : 'main';
  const candidateIndex = value => Number.isInteger(value) && value >= 0 && value < 4;
  const isSHA256 = value => typeof value === 'string' && /^(?:sha256:)?[0-9a-f]{64}$/.test(value);
  function ownerMemoryAudit(run, ownerUserId) {
    if (!Number.isInteger(ownerUserId) || ownerUserId < 1) return null;
    const audit = run.memory_audit;
    const validVersion = value => value && typeof value.content === 'string' && isSHA256(value.sha256)
      && Number.isSafeInteger(value.context_revision) && value.context_revision >= 0 && Number.isSafeInteger(value.memory_revision) && value.memory_revision >= 0;
    return audit?.v === 1 && audit.kind === 'auto_memory_update' && audit.profile === 'MEMORY.md' && audit.owner_user_id === ownerUserId
      && typeof audit.changed === 'boolean' && validVersion(audit.before) && validVersion(audit.after)
      && Array.isArray(audit.source_ids) && audit.source_ids.every(isUUID) && Array.isArray(audit.fact_ids) && audit.fact_ids.every(id => typeof id === 'string' && id.startsWith('fact_')) ? audit : null;
  }
  function candidateCount(run) {
    const count = run.accepted?.options?.candidate_count ?? run.candidate_count ?? 1;
    return Number.isInteger(count) && count >= 1 && count <= 4 ? count : 1;
  }
  function selectedCandidate(run) {
    return candidateIndex(run.selected_candidate) ? run.selected_candidate : candidateCount(run) === 1 ? 0 : null;
  }
  function needsCandidateSelection(runs) {
    const last = runs.at(-1);
    return !!last && last.status === 'completed' && candidateCount(last) > 1 && selectedCandidate(last) === null;
  }
  function canSelectCandidate(runs, rid, index) {
    const last = runs.at(-1);
    return !!last && last.id === rid && last.status === 'completed' && candidateCount(last) > 1
      && candidateIndex(index) && index < candidateCount(last) && !runs.some(r => !TERMINAL.has(r.status))
      && [...last.messages.values()].some(m => m.candidate_index === index && m.complete && !m.tool_calls?.length && (!m.finish_reason || m.finish_reason === 'stop'));
  }
  const isUUID = value => typeof value === 'string' && UUID.test(value);
  const bytes = value => new TextEncoder().encode(value).length;
  function normalizeText(value) {
    if (typeof value !== 'string') throw new Error('请输入文本。');
    const normalized = value.replace(/^\uFEFF/, '').replace(/\r\n?/g, '\n');
    if (/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/.test(normalized)) throw new Error('文本含不允许的控制字符。');
    // Reject unpaired UTF-16 rather than silently replacing it when encoding.
    for (let i = 0; i < normalized.length; i++) {
      const n = normalized.charCodeAt(i);
      if (n >= 0xd800 && n <= 0xdbff) {
        const next = normalized.charCodeAt(++i);
        if (!(next >= 0xdc00 && next <= 0xdfff)) throw new Error('文本不是有效 Unicode。');
      } else if (n >= 0xdc00 && n <= 0xdfff) throw new Error('文本不是有效 Unicode。');
    }
    return normalized;
  }
  function uuid(cryptoObject = globalThis.crypto) {
    if (cryptoObject?.randomUUID) return cryptoObject.randomUUID();
    if (!cryptoObject?.getRandomValues) throw new Error('此浏览器无法生成安全请求标识，请升级浏览器。');
    const data = cryptoObject.getRandomValues(new Uint8Array(16));
    data[6] = (data[6] & 15) | 64; data[8] = (data[8] & 63) | 128;
    const hex = Array.from(data, n => n.toString(16).padStart(2, '0')).join('');
    return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
  }
  class StaleResponse extends Error {
    constructor() { super('页面身份或聊天已变化，已忽略旧响应。'); this.name = 'StaleResponse'; }
  }
  class Scope {
    constructor(revoke = url => URL.revokeObjectURL(url)) {
      this.epoch = 0; this.view = 0; this.cid = null; this.blocked = false;
      this.requests = new Map(); this.urls = new Set(); this.revoke = revoke;
    }
    ticket(global = false) { return {epoch: this.epoch, view: global ? null : this.view, cid: global ? null : this.cid}; }
    current(t) { return !this.blocked && t.epoch === this.epoch && (t.view === null || t.view === this.view && t.cid === this.cid); }
    assert(t) { if (!this.current(t)) throw new StaleResponse(); }
    track(controller, ticket) { this.assert(ticket); this.requests.set(controller, ticket); }
    release(controller) { this.requests.delete(controller); }
    addURL(url, ticket) { this.assert(ticket); this.urls.add(url); return url; }
    dropURL(url) { if (this.urls.delete(url)) this.revoke(url); }
    clearURLs() { for (const url of [...this.urls]) this.dropURL(url); }
    switch(cid) {
      this.view++; this.cid = cid;
      for (const [controller, t] of this.requests) if (t.view !== null) { controller.abort(); this.release(controller); }
      this.clearURLs(); return this.ticket();
    }
    invalidate() {
      this.epoch++; this.view++; this.cid = null; this.blocked = true;
      for (const controller of this.requests.keys()) controller.abort();
      this.requests.clear(); this.clearURLs();
    }
  }
  function mergeEvents(existing, incoming, cid) {
    if (!Array.isArray(incoming)) throw new Error('历史事件响应无效。');
    const result = existing.slice(); let last = result.at(-1)?.seq || 0;
    for (const event of incoming) {
      if (!event || event.v !== 1 || !Number.isSafeInteger(event.seq) || event.seq < 1 || event.conversation_id !== cid || !TYPES.has(event.type) || !event.data || typeof event.data !== 'object' || Array.isArray(event.data)) throw new Error('历史事件格式或聊天归属无效。');
      if (event.type === 'conversation.skills.updated' && (event.run_id !== null || Object.keys(event.data).length !== 1 || !Array.isArray(event.data.skill_ids) || event.data.skill_ids.length > 3 || new Set(event.data.skill_ids).size !== event.data.skill_ids.length || event.data.skill_ids.some(id => !isUUID(id)))) throw new Error('聊天 Skills 历史设置无效。');
      if (event.seq <= last) continue;
      if (event.seq !== last + 1) throw new Error('历史事件有缺口，请只读刷新恢复。');
      result.push(event); last = event.seq;
    }
    return result;
  }
  function createEventProjector() {
    let runs = new Map(), files = new Map(), title = null, count = 0, first = null, last = null;
    function run(id) {
      if (!runs.has(id)) runs.set(id, {id, status: 'accepted', messages: new Map(), tools: new Map(), timeline: [], requests: new Map(), messageCandidates: new Map(), requestUsage: new Map(), notices: [], usage: null, usage_complete: false, accepted: null, uncertain: false, selected_candidate: null});
      return runs.get(id);
    }
    function message(r, d) {
      if (!r.messages.has(d.message_id)) {
        r.timeline.push({kind: 'message', id: d.message_id});
        r.messages.set(d.message_id, {id: d.message_id, text: '', complete: false, reasoning: []});
      }
      const m = r.messages.get(d.message_id), index = d.candidate_index ?? r.messageCandidates.get(d.message_id);
      if (candidateIndex(index)) m.candidate_index = index;
      return m;
    }
    function apply(event) {
      const d = event.data;
      if (event.type === 'conversation.created' || event.type === 'conversation.renamed') title = d.title;
      if (event.type === 'file.added') files.set(d.file.id, d.file);
      if (!event.run_id) return;
      const r = run(event.run_id);
      if (!Object.hasOwn(r, '_seq')) Object.defineProperties(r, {_seq: {value: 0, writable: true}, _identity: {value: Symbol('run-view')}});
      r._seq = event.seq;
      if (event.type === 'run.accepted') { r.accepted = d; r.candidate_count = candidateCount(r); }
      if (event.type === 'run.dispatching') {
        r.status = 'running'; r.model_requests = d.request_index;
        r.requests.set(d.request_index, {purpose: purposeOf(d.purpose), candidate_index: d.candidate_index});
        if (candidateIndex(d.candidate_index)) r.messageCandidates.set(d.assistant_message_id, d.candidate_index);
        if (Array.isArray(d.candidate_message_ids)) d.candidate_message_ids.forEach((id, i) => r.messageCandidates.set(id, (d.candidate_index ?? 0) + i));
      }
      if (event.type === 'assistant.delta' || event.type === 'assistant.message') {
        const m = message(r, d);
        if (event.type === 'assistant.message') {
          // Snapshots replace deltas, but an omitted/empty auxiliary snapshot must
          // not erase already streamed text or public reasoning.
          if (typeof d.text === 'string' && (d.text || !m.text)) m.text = d.text;
          Object.assign(m, {complete: d.complete, tool_calls: d.tool_calls, finish_reason: d.finish_reason});
        } else if (typeof d.text === 'string') m.text += d.text;
      }
      if (event.type === 'assistant.reasoning') {
        const m = message(r, d);
        if (['raw', 'summary'].includes(d.kind) && ['reasoning_content', 'reasoning', 'thinking'].includes(d.field) && typeof d.text === 'string') {
          let part = m.reasoning.find(p => p.kind === d.kind && p.field === d.field);
          if (!part) { part = {kind: d.kind, field: d.field, text: ''}; m.reasoning.push(part); }
          part.text += d.text;
        }
      }
      // assistant.protocol is deliberately not projected: opaque state is never UI content.
      if (event.type === 'candidate.selected' && candidateIndex(d.candidate_index)) r.selected_candidate = d.candidate_index;
      if (event.type === 'run.notice') r.notices.push({code: typeof d.code === 'string' ? d.code : '', message: typeof d.message === 'string' ? d.message : '本轮有新的运行提示。'});
      if (event.type === 'context.compacted' || event.type === 'context.selected') {
        let message = event.type === 'context.selected' ? '本轮复用已有有效压缩检查点；此次复用没有新增辅助模型调用，完整原始聊天历史仍保留。' : '本轮新生成上下文压缩检查点；相关辅助请求单独记录，完整原始聊天历史仍保留。';
        if (['summary', 'native'].includes(d.mode) && Array.isArray(d.covered_run_ids) && d.covered_run_ids.every(isUUID) && isSHA256(d.source_sha256)) {
          message += ` 模式 ${d.mode} · 覆盖 ${d.covered_run_ids.length} 轮 · 来源 SHA256 ${d.source_sha256}`;
          if (isSHA256(d.checkpoint?.sha256)) message += ` · 检查点 SHA256 ${d.checkpoint.sha256}`;
        }
        r.notices.push({code: event.type, message});
      }
      if (event.type === 'memory.proposed') r.notices.push({code: 'memory.proposed', message: '已记录自动记忆更新意向（仅追踪）；这不是 MEMORY 已写入的确认。'});
      if (event.type === 'memory.updated') { r.memory_audit = d.audit; r.notices.push({code: 'memory.updated', message: '个人 MEMORY 更新审计已记录；变更详情仅对本人展示。'}); }
      if (event.type === 'memory.reverted') { r.memory_reverted = true; r.memory_reverted_context_revision = d.context_revision; r.notices.push({code: 'memory.reverted', message: `本轮自动记忆已撤销${Number.isSafeInteger(d.context_revision) ? ` · 个人上下文 v${d.context_revision}` : ''}。`}); }
      if (event.type === 'tool.call' || event.type === 'tool.result') {
        if (!r.tools.has(d.call_id)) r.timeline.push({kind: 'tool', id: d.call_id});
        r.tools.set(d.call_id, {...r.tools.get(d.call_id), id: d.call_id, name: d.name, ...(event.type === 'tool.call' ? {arguments: d.arguments} : {ok: d.ok, result: d.result})});
      }
      if (event.type === 'run.usage') {
        r.requestUsage.set(d.request_index, {...d, purpose: purposeOf(d.purpose ?? r.requests.get(d.request_index)?.purpose)});
        r.usage = [...r.requestUsage.values()].reduce((sum, item) => ({prompt_tokens: sum.prompt_tokens + item.prompt_tokens, completion_tokens: sum.completion_tokens + item.completion_tokens, total_tokens: sum.total_tokens + item.total_tokens}), {prompt_tokens: 0, completion_tokens: 0, total_tokens: 0});
      }
      if (event.type === 'run.terminal') Object.assign(r, d);
    }
    return {update(events) {
      // mergeEvents appends immutable records. Replacing/truncating a history
      // resets this view, including on conversation/account switches.
      if (events.length < count || count && (events[0] !== first || events[count - 1] !== last)) {
        runs = new Map(); files = new Map(); title = null; count = 0;
      }
      for (let index = count; index < events.length; index++) apply(events[index]);
      count = events.length; first = events[0]; last = events.at(-1);
      return {runs: [...runs.values()], files: [...files.values()], title, lastSeq: last?.seq || 0};
    }};
  }
  function projectEvents(events) { return createEventProjector().update(events); }
  function buildRunRequest({meta, token, model, mode, text, attachments, skillIds, context, config, requestId, options}) {
    if (!meta || meta.storage_state === 'corrupt' || meta.active_run_id) throw new Error('聊天不可写或仍有运行，请先只读刷新状态。');
    if (!token?.usable || !Number.isInteger(token.id) || token.id < 1 || token.id > 2147483647) throw new Error('请选择本人可用 KEY。');
    if (!model?.chat_enabled || !model.id || !/^sha256:[0-9a-f]{64}$/.test(model.capability_revision || '')) throw new Error('请先载入并选择已确认能力的模型。');
    if (!config?.modes?.includes(mode)) throw new Error('当前服务不支持此请求，请刷新页面。');
    if (typeof text !== 'string' || !text.trim() || bytes(text) > config.limits.user_text_bytes) throw new Error('请输入非空正文，并遵守正文大小限制。');
    normalizeText(text);
    if (!context || !Number.isSafeInteger(context.context_revision)) throw new Error('请先载入个人上下文。');
    if (!Array.isArray(skillIds) || skillIds.length > context.limits.skills_per_run || new Set(skillIds).size !== skillIds.length || skillIds.some(id => !isUUID(id) || !context.skills.some(s => s.id === id))) throw new Error('已载入 Skills 不可用或超限，请打开 Skills 核对或移除。');
    if (!Array.isArray(attachments) || attachments.length > config.limits.attachments_per_turn || new Set(attachments).size !== attachments.length || attachments.some(id => !isUUID(id))) throw new Error('附件选择无效。');
    if (!isUUID(requestId)) throw new Error('请求标识无效。');
    const body = {client_request_id: requestId, expected_revision: meta.revision, token_id: token.id, model: model.id, capability_revision: model.capability_revision, context_revision: context.context_revision, skill_ids: [...skillIds], mode, text, attachment_ids: [...attachments]};
    // Omission is intentionally the exact legacy DTO (including its key set).
    if (options !== undefined) {
      if (!options || typeof options !== 'object' || Array.isArray(options) || Object.keys(options).some(k => !['candidate_count', 'web_enabled', 'auto_memory', 'context_strategy'].includes(k))) throw new Error('本轮扩展选项无效。');
      const count = options.candidate_count ?? 1;
      const limit = Number.isInteger(model.candidate_limit) ? Math.min(4, Math.max(1, model.candidate_limit)) : 4;
      if (!Number.isInteger(count) || count < 1 || count > limit || mode === 'files' && count !== 1 || config.features?.multiple_candidates === false && count > 1) throw new Error('候选数超限；文件 Agent 仅支持 1 个执行分支。');
      for (const key of ['web_enabled', 'auto_memory']) if (options[key] !== undefined && typeof options[key] !== 'boolean') throw new Error('联网与自动记忆参数必须为布尔值。');
      if (options.auto_memory && config.features?.auto_memory === false) throw new Error('服务端未开启自动记忆。');
      if (options.web_enabled && mode !== 'files') throw new Error('联网工具仅在文件 Agent 模式可用。');
      if (options.context_strategy !== undefined && !['recent', 'adaptive'].includes(options.context_strategy)) throw new Error('上下文策略无效。');
      if (Array.isArray(config.features?.context_strategies) && !config.features.context_strategies.includes(options.context_strategy ?? 'adaptive')) throw new Error('服务端未开启此上下文策略。');
      Object.assign(body, {candidate_count: count, web_enabled: options.web_enabled ?? false, auto_memory: options.auto_memory ?? false, context_strategy: options.context_strategy ?? 'adaptive'});
    }
    return body;
  }
  function pageCursor(page, seen) {
    if (typeof page.has_more !== 'boolean' || page.has_more && (typeof page.next_cursor !== 'string' || !page.next_cursor || seen.has(page.next_cursor))) throw new Error('聊天分页游标无效，请重新加载列表。');
    if (page.has_more) seen.add(page.next_cursor);
    return page.has_more ? page.next_cursor : null;
  }
  return {isUUID, isSHA256, ownerMemoryAudit, bytes, normalizeText, uuid, TERMINAL, PURPOSES, purposeOf, candidateCount, selectedCandidate, needsCandidateSelection, canSelectCandidate, StaleResponse, Scope, mergeEvents, projectEvents, createEventProjector, buildRunRequest, pageCursor};
});
