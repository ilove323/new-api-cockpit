(function (root, factory) {
  'use strict';
  const value = factory(typeof module === 'object' && module.exports ? require('./state.js') : root.CockpitChat);
  if (typeof module === 'object' && module.exports) module.exports = value;
  else Object.assign(root.CockpitChat, value);
})(typeof window === 'object' ? window : globalThis, function (C) {
  'use strict';
  function el(doc, tag, text, className) {
    const node = doc.createElement(tag);
    if (text !== undefined && text !== null) node.textContent = String(text);
    if (className) node.className = className;
    return node;
  }
  function button(doc, text, action, className) {
    const node = el(doc, 'button', text, className); node.type = 'button';
    node.addEventListener('click', action); return node;
  }
  function safeLink(value) {
    if (typeof value !== 'string' || /[\s\u0000-\u001f\u007f]/.test(value) || !/^https?:\/\//i.test(value)) return null;
    try { const url = new URL(value); return ['http:', 'https:'].includes(url.protocol) && !url.username && !url.password ? url.href : null; } catch { return null; }
  }
  function inline(doc, target, value, depth = 0) {
    // Deliberately small, non-HTML parser. Unsupported syntax remains literal text.
    if (depth > 3) { target.append(doc.createTextNode(value)); return; }
    const pattern = /!\[[^\]\n]*\]\([^\n)]*\)|`([^`\n]+)`|\[([^\]\n]+)\]\(([^\s)]+)\)|\*\*([^*\n]+)\*\*|\*([^*\n]+)\*/g;
    let last = 0, match;
    while ((match = pattern.exec(value))) {
      target.append(doc.createTextNode(value.slice(last, match.index)));
      if (match[0].startsWith('![')) target.append(doc.createTextNode(match[0]));
      else if (match[1] !== undefined) target.append(el(doc, 'code', match[1]));
      else if (match[2] !== undefined) {
        const href = safeLink(match[3]);
        if (href) { const a = el(doc, 'a', match[2]); a.href = href; a.target = '_blank'; a.rel = 'noopener noreferrer'; a.referrerPolicy = 'no-referrer'; target.append(a); }
        else target.append(doc.createTextNode(match[0]));
      } else { const node = el(doc, match[4] !== undefined ? 'strong' : 'em'); inline(doc, node, match[4] ?? match[5], depth + 1); target.append(node); }
      last = pattern.lastIndex;
    }
    target.append(doc.createTextNode(value.slice(last)));
  }
  function markdown(doc, value) {
    const fragment = doc.createDocumentFragment();
    const lines = String(value ?? '').replace(/\r\n?/g, '\n').split('\n');
    let i = 0;
    while (i < lines.length) {
      const line = lines[i];
      if (!line.trim()) { i++; continue; }
      const fence = /^\s{0,3}(`{3,}|~{3,})([^\n]*)$/.exec(line);
      if (fence) {
        const codeLines = []; i++;
        while (i < lines.length && !new RegExp(`^\\s{0,3}${fence[1][0]}{${fence[1].length},}\\s*$`).test(lines[i])) codeLines.push(lines[i++]);
        if (i < lines.length) i++;
        const box = el(doc, 'div', null, 'chat-code'), pre = el(doc, 'pre'), code = el(doc, 'code', codeLines.join('\n'));
        pre.append(code); box.append(pre);
        const copy = button(doc, '复制代码', async () => {
          try { if (!doc.defaultView?.navigator?.clipboard?.writeText) throw new Error('Clipboard unavailable'); await doc.defaultView.navigator.clipboard.writeText(code.textContent); copy.textContent = '已复制'; } catch { copy.textContent = '复制不可用，请手动选择'; }
        }, 'button-small');
        box.append(copy); fragment.append(box); continue;
      }
      const heading = /^(#{1,4})\s+(.+)$/.exec(line);
      if (heading) { const node = el(doc, `h${heading[1].length + 1}`); inline(doc, node, heading[2]); fragment.append(node); i++; continue; }
      const item = /^\s*([-*+] |\d+\. )(.+)$/.exec(line);
      if (item) {
        const list = el(doc, /^\d/.test(item[1]) ? 'ol' : 'ul');
        const ordered = list.tagName.toLowerCase() === 'ol';
        while (i < lines.length) {
          const next = /^\s*([-*+] |\d+\. )(.+)$/.exec(lines[i]);
          if (!next || /^\d/.test(next[1]) !== ordered) break;
          const li = el(doc, 'li'); inline(doc, li, next[2]); list.append(li); i++;
        }
        fragment.append(list); continue;
      }
      const p = el(doc, 'p'); inline(doc, p, line); fragment.append(p); i++;
    }
    return fragment;
  }
  const STATUS = {accepted: '已接受', running: '运行中', completed: '已完成', failed: '失败', cancelled: '已取消', interrupted: '已中断'};
  function usageText(run) {
    if (!run.usage) return '用量未知（不是 0）；费用以 New API 记录为准';
    return `用量${run.usage_complete ? '完整' : '部分'}：输入 ${run.usage.prompt_tokens} / 输出 ${run.usage.completion_tokens} / 合计 ${run.usage.total_tokens} Token；费用以 New API 记录为准`;
  }
  const PURPOSE_LABELS = {main: '主回答', summary: '摘要', native_compact: '原生压缩', memory: '自动记忆'};
  function requestUsageText(run) {
    const requests = new Map(run.requests || []);
    for (const [index, usage] of run.requestUsage || []) if (!requests.has(index)) requests.set(index, {purpose: C.purposeOf(usage.purpose)});
    if (!requests.size) return '';
    return C.PURPOSES.filter(p => [...requests.values()].some(r => r.purpose === p)).map(p => {
      const entries = [...requests].filter(([, r]) => r.purpose === p);
      const known = entries.map(([i]) => run.requestUsage?.get(i)).filter(Boolean);
      const total = known.reduce((sum, u) => sum + u.total_tokens, 0);
      return `${PURPOSE_LABELS[p]} ${entries.length} 次请求 · ${known.length ? `${total} Token${known.length < entries.length ? '（部分）' : ''}` : '用量未知'}`;
    }).join('；') + '。辅助请求单独计费，受每轮上限约束；不是免费或隐藏重试。';
  }
  function renderRuns(doc, target, runs, onSnapshot, {onSelectCandidate, selectionDisabled = false, ownerUserId = null, onUndoMemory, memoryDisabled = false} = {}) {
    const oldDetails = new Set([...target.querySelectorAll('details[open]')].map(node => node.dataset.key));
    const fragment = doc.createDocumentFragment();
    for (const run of runs) {
      const article = el(doc, 'article', null, 'chat-run'); article.dataset.run = run.id;
      if (run.accepted) {
        const user = el(doc, 'section', null, 'chat-message chat-user');
        user.append(el(doc, 'h3', '你'), el(doc, 'div', run.accepted.text, 'chat-plain'));
        if (run.accepted.attachment_ids?.length) user.append(el(doc, 'p', `本轮引用 ${run.accepted.attachment_ids.length} 个文件`, 'hint'));
        article.append(user);
      }
      for (const entry of run.timeline) {
        if (entry.kind === 'message') {
          const message = run.messages.get(entry.id), section = el(doc, 'section', null, 'chat-message chat-assistant');
          const multi = C.candidateCount(run) > 1, index = message.candidate_index;
          const candidate = multi && Number.isInteger(index), selected = candidate && C.selectedCandidate(run) === index;
          section.dataset.message = message.id;
          if (candidate) { section.className += ' chat-candidate'; section.dataset.candidate = String(index); section.setAttribute('aria-label', `候选 ${index + 1}`); }
          const title = candidate ? `候选 ${index + 1}${selected ? ' · 已选为后续分支' : ''}${message.complete ? '' : ' · 部分内容'}` : message.complete ? '助手' : '助手 · 部分内容';
          section.append(el(doc, 'h3', title), markdown(doc, message.text));
          for (const part of message.reasoning || []) {
            const details = el(doc, 'details', null, 'chat-reasoning'); details.dataset.key = `${run.id}:${message.id}:${part.kind}:${part.field}`; details.open = oldDetails.has(details.dataset.key);
            details.append(el(doc, 'summary', `${part.kind === 'summary' ? '公开推理摘要' : '公开推理文本'} · ${part.kind} / ${part.field}`), el(doc, 'p', '仅展示模型接口公开返回的内容；不是未公开的隐藏思考原文。', 'hint'), el(doc, 'pre', part.text));
            section.append(details);
          }
          if (candidate) {
            const choose = button(doc, selected ? `已选择候选 ${index + 1}` : `选择候选 ${index + 1} 继续`, () => onSelectCandidate?.(run.id, index), 'button-small chat-candidate-select');
            choose.disabled = selectionDisabled || !onSelectCandidate || !C.canSelectCandidate(runs, run.id, index);
            choose.setAttribute('aria-pressed', selected ? 'true' : 'false'); section.append(choose);
          }
          article.append(section);
        } else {
          const tool = run.tools.get(entry.id), details = el(doc, 'details', null, 'chat-tool'); details.dataset.key = `${run.id}:${tool.id}`; details.open = oldDetails.has(details.dataset.key);
          details.append(el(doc, 'summary', `工具 ${tool.name} · ${tool.ok === undefined ? '调用已记录' : tool.ok ? '成功' : '失败'}`));
          details.append(el(doc, 'pre', JSON.stringify({arguments: tool.arguments, result: tool.result}, null, 2))); article.append(details);
        }
      }
      const footer = el(doc, 'div', null, 'chat-run-footer');
      footer.append(el(doc, 'strong', STATUS[run.status] || '状态待确认'));
      if (run.accepted) {
        const snapshot = run.accepted.context_snapshot;
        footer.append(button(doc, `查看上下文 v${snapshot?.context_revision ?? '?'} · ${snapshot?.skill_ids?.length ?? 0} Skills`, () => onSnapshot(run.id), 'button-small'));
        footer.append(el(doc, 'span', `${run.accepted.model} · ${run.accepted.mode === 'files' ? '文件助手' : '普通聊天'}`, 'hint'));
        if (run.accepted.options) {
          const options = run.accepted.options;
          const count = C.candidateCount(run);
          footer.append(el(doc, 'p', `${count > 1 ? `${count} 个历史候选 · ` : ''}${options.context_strategy === 'adaptive' ? '自适应上下文' : '最近语义轮次'} · 自动记忆${options.auto_memory ? '已开启' : '未启用'}${options.web_enabled ? ' · 联网按需可用' : ''}`, 'hint'));
        }
      }
      if (C.candidateCount(run) > 1) footer.append(el(doc, 'p', C.canSelectCandidate(runs, run.id, [...run.messages.values()].find(m => m.complete)?.candidate_index)
        ? C.selectedCandidate(run) === null ? '请选择一个候选后再发送；后续只延续所选分支。' : '后续只延续所选分支；产生后续运行后，本轮选择锁定。'
        : '仅最新已完成且没有后续运行的多候选轮次可选择；其他候选只供查阅。', 'hint'));
      for (const notice of run.notices || []) footer.append(el(doc, 'p', `${notice.code ? `${notice.code} · ` : ''}${notice.message}`, 'warning chat-notice'));
      const audit = C.ownerMemoryAudit(run, ownerUserId);
      if (audit) {
        const details = el(doc, 'details', null, 'chat-memory-audit'); details.dataset.key = `${run.id}:memory`; details.open = oldDetails.has(details.dataset.key);
        details.append(el(doc, 'summary', `本人的自动记忆审计 · ${run.memory_reverted ? '已撤销' : audit.changed ? '已更新' : '内容未变化'}`), el(doc, 'p', `来源 ${audit.source_ids.length} 条消息 · ${audit.fact_ids.length} 条事实。仅为本人个人 MEMORY 的变更，不是会话摘要。`, 'hint'));
        for (const [key, label] of [['before', '变更前'], ['after', '变更后']]) {
          const version = audit[key];
          details.append(el(doc, 'h4', `${label} · MEMORY v${version.memory_revision} · 上下文 v${version.context_revision}`), el(doc, 'p', `SHA256 ${version.sha256}`, 'hint'), el(doc, 'pre', version.content || '（空文档）'));
        }
        details.append(el(doc, 'p', '撤销将恢复变更前的 MEMORY；若记忆已被后续修改，将拒绝覆盖。只在点击后读取最新版本并提交一次撤销，不会调用模型。', 'warning'));
        const undo = button(doc, '撤销本次自动记忆更新', () => onUndoMemory?.(run.id), 'button-small chat-memory-undo');
        undo.disabled = memoryDisabled || !onUndoMemory || !audit.changed || run.memory_reverted || !C.TERMINAL.has(run.status);
        details.append(undo); footer.append(details);
      }
      if (run.reason || run.error_code) footer.append(el(doc, 'p', `${run.reason || ''}${run.error_code ? ` (${run.error_code})` : ''}`, 'warning'));
      if (run.uncertain) footer.append(el(doc, 'p', '结果不确定，可能已产生费用；未自动重发，请核对 New API 用量。', 'warning'));
      if (run.cancel_requested && !['completed', 'failed', 'cancelled', 'interrupted'].includes(run.status)) footer.append(el(doc, 'p', '停止请求已记录，等待执行者终止；202 不代表已停止或不计费。', 'warning'));
      footer.append(el(doc, 'p', usageText(run), 'hint'));
      const requests = requestUsageText(run); if (requests) footer.append(el(doc, 'p', requests, 'hint chat-request-usage'));
      article.append(footer); fragment.append(article);
    }
    if (!runs.length) fragment.append(el(doc, 'p', '暂无消息。新建聊天不会调用模型。', 'hint'));
    target.replaceChildren(fragment);
  }
  function showDialog(doc, title, contents) {
    const dialog = doc.getElementById('chat-dialog');
    dialog.replaceChildren(el(doc, 'h2', title), ...contents, button(doc, '关闭', () => { dialog.close(); dialog.replaceChildren(); }));
    if (!dialog.open) dialog.showModal();
    return dialog;
  }
  function snapshotNodes(doc, snapshot) {
    const nodes = [el(doc, 'p', `上下文 v${snapshot.context_revision} · 冻结时间 ${snapshot.captured_at} · ${snapshot.policy_version}`, 'hint'), el(doc, 'p', '以下是该轮不可变副本，不是当前设置。删除 Skill 或清空个人设置不会追溯删除此副本。', 'warning')];
    for (const item of [...snapshot.profiles, ...snapshot.skills]) {
      const details = el(doc, 'details', null, 'chat-tool');
      details.append(el(doc, 'summary', `${item.name} · v${item.revision} · ${item.size_bytes} 字节`), el(doc, 'p', `SHA256 ${item.sha256}`, 'hint'), el(doc, 'pre', item.content || '（空文档）'));
      nodes.push(details);
    }
    return nodes;
  }
  return {el, button, safeLink, markdown, renderRuns, usageText, requestUsageText, showDialog, snapshotNodes, STATUS};
});
