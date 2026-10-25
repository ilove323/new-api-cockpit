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
  function fileIcon(doc, file) {
    const extension = /\.([a-z0-9]+)$/i.exec(file.name)?.[1].toUpperCase().slice(0, 4) || 'TXT';
    const icon = el(doc, 'span', extension, 'chat-file-icon'); icon.setAttribute('aria-hidden', 'true'); return icon;
  }
  function attachmentCard(doc, file, {onDownloadFile, onReferenceFile, onDeleteFile, attachmentIds = [], filesDisabled = false, deleteDisabled = false} = {}) {
    const card = el(doc, 'div', null, 'chat-attachment'); card.dataset.file = file.id;
    if (file.deleted_at) {
      card.classList.add('chat-attachment-deleted');
      card.append(fileIcon(doc, file), el(doc, 'span', file.name, 'chat-file-name'), el(doc, 'small', '已删除', 'hint'));
      return card;
    }
    const download = button(doc, '', () => onDownloadFile?.(file), 'chat-file-entry');
    download.setAttribute('aria-label', `下载附件 ${file.name}`); download.title = file.name; download.disabled = !onDownloadFile;
    download.append(fileIcon(doc, file), el(doc, 'span', file.name, 'chat-file-name'));
    const selected = attachmentIds.includes(file.id), reference = button(doc, selected ? '已引用' : '引用', () => onReferenceFile?.(file), 'button-small chat-reference');
    reference.setAttribute('aria-label', `${selected ? '取消引用' : '引用附件'} ${file.name}`); reference.setAttribute('aria-pressed', String(selected));
    reference.disabled = filesDisabled || !onReferenceFile; card.append(download, reference);
    if (file.kind === 'upload' && onDeleteFile) {
      const remove = button(doc, '删除', () => onDeleteFile(file), 'button-small danger chat-attachment-delete');
      remove.setAttribute('aria-label', `删除附件 ${file.name}`); remove.disabled = deleteDisabled; card.append(remove);
    }
    return card;
  }
  function usageText(run) {
    if (!run.usage) return '用量未知；费用以 New API 记录为准';
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
    }).join('；');
  }
  const runViews = new WeakMap();
  function syncChildren(target, nodes) {
    if (!target.insertBefore || !target.removeChild) {
      if (target.children.length !== nodes.length || nodes.some((node, index) => target.children[index] !== node)) target.replaceChildren(...nodes);
      return;
    }
    let cursor = target.firstChild;
    for (const node of nodes) {
      if (node === cursor) cursor = cursor.nextSibling;
      else target.insertBefore(node, cursor);
    }
    while (cursor) { const next = cursor.nextSibling; target.removeChild(cursor); cursor = next; }
  }
  const sameSignature = (a, b) => a?.length === b.length && b.every((value, index) => Object.is(value, a[index]));
  function rememberSelection(doc, target) {
    const selection = doc.defaultView?.getSelection?.();
    if (!selection || selection.isCollapsed || !target.contains(selection.anchorNode) || !target.contains(selection.focusNode)) return () => {};
    const {anchorNode, anchorOffset, focusNode, focusOffset} = selection;
    function offset(node, index) {
      const block = node.parentElement?.closest('[data-selection-key]');
      const root = block && target.contains(block) ? block : target;
      const range = doc.createRange(); range.selectNodeContents(root); range.setEnd(node, index);
      return {key: root.dataset.selectionKey, index: range.toString().length};
    }
    const anchor = offset(anchorNode, anchorOffset), focus = offset(focusNode, focusOffset);
    return () => {
      if (anchorNode.isConnected && focusNode.isConnected) return;
      const point = ({key, index}) => {
        const root = key ? [...target.querySelectorAll('[data-selection-key]')].find(node => node.dataset.selectionKey === key) || target : target;
        const walker = doc.createTreeWalker(root, 4); let node, last;
        while ((node = walker.nextNode())) {
          if (index <= node.length) return [node, index];
          index -= node.length; last = node;
        }
        return last ? [last, last.length] : [root, 0];
      };
      selection.setBaseAndExtent(...point(anchor), ...point(focus));
    };
  }
  function renderRuns(doc, target, runs, {onSelectCandidate, selectionDisabled = false, ownerUserId = null, onUndoMemory, memoryDisabled = false, files = [], attachmentIds = [], onReferenceFile, onDownloadFile, onDeleteFile, filesDisabled = false, deleteDisabled = false} = {}) {
    const restoreSelection = rememberSelection(doc, target);
    let oldDetails;
    const wasOpen = key => (oldDetails ||= new Set([...target.querySelectorAll('details[open]')].map(node => node.dataset.key))).has(key);
    const nodes = [], used = new Set();
    let view = runViews.get(target);
    if (!view) { view = {parts: new Map(), runs: new Map()}; runViews.set(target, view); }
    view.actions = {onSelectCandidate, onUndoMemory, onReferenceFile, onDownloadFile, onDeleteFile};
    function part(key, signature, build) {
      used.add(key);
      const prior = view.parts.get(key);
      if (prior && sameSignature(prior.signature, signature)) return prior.node;
      const node = build(); view.parts.set(key, {signature, node}); return node;
    }
    const filesSignature = JSON.stringify(files.map(file => [file.id, file.name, file.kind, file.deleted_at, file.version]));
    const fileUI = [filesSignature, attachmentIds.join(','), filesDisabled, deleteDisabled, !!onDownloadFile, !!onReferenceFile, !!onDeleteFile];
    const fileMap = new Map(files.map(file => [file.id, file])), referenced = new Set();
    const fileOptions = {onDownloadFile: onDownloadFile && (file => view.actions.onDownloadFile?.(file)), onReferenceFile: onReferenceFile && (file => view.actions.onReferenceFile?.(file)), onDeleteFile: onDeleteFile && (file => view.actions.onDeleteFile?.(file)), attachmentIds, filesDisabled, deleteDisabled};
    for (const run of runs) {
      for (const id of run.accepted?.attachment_ids || []) referenced.add(id);
      const signature = [run._seq, run.status, run.cancel_requested, run.uncertain, run.model_requests, run.tool_calls, run.error_code, run.reason, run.usage?.prompt_tokens, run.usage?.completion_tokens, run.usage?.total_tokens, run.usage_complete, selectionDisabled, memoryDisabled, ownerUserId, !!onSelectCandidate, !!onUndoMemory, runs.at(-1)?.id, ...fileUI];
      let record = view.runs.get(run.id);
      if (Number.isSafeInteger(run._seq) && record?.identity === run._identity && sameSignature(record.signature, signature)) {
        for (const key of record.keys) used.add(key);
        nodes.push(record.article); continue;
      }
      const keysBefore = new Set(used), content = [];
      const article = record?.article || el(doc, 'article', null, 'chat-run'); article.dataset.run = run.id;
      if (run.accepted) {
        const user = part(`${run.id}:user`, [run.accepted.text, run.accepted.attachment_ids?.join(','), ...fileUI], () => {
          const user = el(doc, 'section', null, 'chat-message chat-user');
          user.append(el(doc, 'h3', '你'), el(doc, 'div', run.accepted.text, 'chat-plain'));
          if (run.accepted.attachment_ids?.length) {
            const attachments = el(doc, 'div', null, 'chat-message-attachments');
            for (const id of run.accepted.attachment_ids) {
              referenced.add(id); const file = fileMap.get(id);
              attachments.append(file ? attachmentCard(doc, file, fileOptions) : el(doc, 'span', '附件暂不可用', 'hint'));
            }
            user.append(attachments);
          }
          return user;
        });
        content.push(user);
      }
      for (const entry of run.timeline) {
        if (entry.kind === 'message') {
          const message = run.messages.get(entry.id);
          const multi = C.candidateCount(run) > 1, index = message.candidate_index;
          const candidate = multi && Number.isInteger(index), selected = candidate && C.selectedCandidate(run) === index;
          const selectable = C.canSelectCandidate(runs, run.id, index);
          const section = part(`${run.id}:message:${message.id}`, [message.text, message.complete, multi, index, selected, selectable, selectionDisabled, !!onSelectCandidate, ...(message.reasoning || []).flatMap(p => [p.kind, p.field, p.text])], () => {
            const section = el(doc, 'section', null, 'chat-message chat-assistant');
            section.dataset.message = message.id;
            if (candidate) { section.className += ' chat-candidate'; section.dataset.candidate = String(index); section.setAttribute('aria-label', `候选 ${index + 1}`); }
            const title = candidate ? `候选 ${index + 1}${selected ? ' · 已选为后续分支' : ''}${message.complete ? '' : ' · 部分内容'}` : message.complete ? '助手' : '助手 · 部分内容';
            const body = el(doc, 'div', null, 'chat-message-body'); body.dataset.selectionKey = message.id;
            body.append(markdown(doc, message.text)); section.append(el(doc, 'h3', title), body);
            for (const part of message.reasoning || []) {
              const details = el(doc, 'details', null, 'chat-reasoning'); details.dataset.key = `${run.id}:${message.id}:${part.kind}:${part.field}`; details.open = wasOpen(details.dataset.key);
              details.append(el(doc, 'summary', `${part.kind === 'summary' ? '公开推理摘要' : '公开推理文本'} · ${part.kind} / ${part.field}`), el(doc, 'p', '仅展示模型接口公开返回的内容；不是未公开的隐藏思考原文。', 'hint'), el(doc, 'pre', part.text));
              section.append(details);
            }
            if (candidate) {
              const choose = button(doc, selected ? `已选择候选 ${index + 1}` : `选择候选 ${index + 1} 继续`, () => view.actions.onSelectCandidate?.(run.id, index), 'button-small chat-candidate-select');
              choose.disabled = selectionDisabled || !onSelectCandidate || !selectable;
              choose.setAttribute('aria-pressed', selected ? 'true' : 'false'); section.append(choose);
            }
            return section;
          });
          content.push(section);
        } else {
          const tool = run.tools.get(entry.id);
          const details = part(`${run.id}:tool:${tool.id}`, [tool.name, tool.ok, tool.arguments, tool.result], () => {
            const details = el(doc, 'details', null, 'chat-tool'); details.dataset.key = `${run.id}:${tool.id}`; details.open = wasOpen(details.dataset.key);
            details.append(el(doc, 'summary', `工具 ${tool.name} · ${tool.ok === undefined ? '调用已记录' : tool.ok ? '成功' : '失败'}`));
            details.append(el(doc, 'pre', JSON.stringify({arguments: tool.arguments, result: tool.result}, null, 2)));
            return details;
          });
          content.push(details);
        }
      }
      const footer = el(doc, 'div', null, 'chat-run-footer');
      footer.append(el(doc, 'strong', STATUS[run.status] || '状态待确认'));
      if (run.accepted) {
        footer.append(el(doc, 'span', run.accepted.model, 'hint'));
      }
      if (C.candidateCount(run) > 1) footer.append(el(doc, 'p', C.canSelectCandidate(runs, run.id, [...run.messages.values()].find(m => m.complete)?.candidate_index)
        ? C.selectedCandidate(run) === null ? '请选择一个候选后再发送；后续只延续所选分支。' : '后续只延续所选分支；产生后续运行后，本轮选择锁定。'
        : '仅最新已完成且没有后续运行的多候选轮次可选择；其他候选只供查阅。', 'hint'));
      for (const notice of run.notices || []) footer.append(el(doc, 'p', `${notice.code ? `${notice.code} · ` : ''}${notice.message}`, 'warning chat-notice'));
      const audit = C.ownerMemoryAudit(run, ownerUserId);
      if (audit) {
        const details = el(doc, 'details', null, 'chat-memory-audit'); details.dataset.key = `${run.id}:memory`; details.open = wasOpen(details.dataset.key);
        details.append(el(doc, 'summary', `本人的自动记忆审计 · ${run.memory_reverted ? '已撤销' : audit.changed ? '已更新' : '内容未变化'}`), el(doc, 'p', `来源 ${audit.source_ids.length} 条消息 · ${audit.fact_ids.length} 条事实。仅为本人个人 MEMORY 的变更，不是会话摘要。`, 'hint'));
        for (const [key, label] of [['before', '变更前'], ['after', '变更后']]) {
          const version = audit[key];
          details.append(el(doc, 'h4', `${label} · MEMORY v${version.memory_revision} · 上下文 v${version.context_revision}`), el(doc, 'p', `SHA256 ${version.sha256}`, 'hint'), el(doc, 'pre', version.content || '（空文档）'));
        }
        details.append(el(doc, 'p', '撤销将恢复变更前的 MEMORY；若记忆已被后续修改，将拒绝覆盖。只在点击后读取最新版本并提交一次撤销，不会调用模型。', 'warning'));
        const undo = button(doc, '撤销本次自动记忆更新', () => view.actions.onUndoMemory?.(run.id), 'button-small chat-memory-undo');
        undo.disabled = memoryDisabled || !onUndoMemory || !audit.changed || run.memory_reverted || !C.TERMINAL.has(run.status);
        details.append(undo); footer.append(details);
      }
      if (run.reason || run.error_code) footer.append(el(doc, 'p', `${run.reason || ''}${run.error_code ? ` (${run.error_code})` : ''}`, 'warning'));
      if (run.uncertain) footer.append(el(doc, 'p', '结果不确定，可能已产生费用；未自动重发，请核对 New API 用量。', 'warning'));
      if (run.cancel_requested && !['completed', 'failed', 'cancelled', 'interrupted'].includes(run.status)) footer.append(el(doc, 'p', '停止请求已记录，等待执行者终止；202 不代表已停止或不计费。', 'warning'));
      footer.append(el(doc, 'p', usageText(run), 'hint'));
      const requests = requestUsageText(run); if (requests) footer.append(el(doc, 'p', requests, 'hint chat-request-usage'));
      content.push(footer); syncChildren(article, content); nodes.push(article);
      record = {article, signature, identity: run._identity, keys: [...used].filter(key => !keysBefore.has(key))}; view.runs.set(run.id, record);
    }
    if (!runs.length) nodes.push(part('empty', [], () => el(doc, 'p', '暂无消息。新建聊天不会调用模型。', 'hint')));
    // Keep uploads that have not appeared in a sent message reachable after
    // removing a draft chip or reloading, without inventing a sent user message.
    const unused = files.filter(file => file.kind === 'upload' && !file.deleted_at && !referenced.has(file.id) && !attachmentIds.includes(file.id));
    if (unused.length) {
      const uploads = part('uploads', [...fileUI, unused.map(file => file.id).join(',')], () => {
        const uploads = el(doc, 'section', null, 'chat-uploaded-files'); uploads.setAttribute('aria-label', '已上传附件');
        uploads.append(el(doc, 'h3', '已上传附件'));
        for (const file of unused) uploads.append(attachmentCard(doc, file, fileOptions));
        return uploads;
      });
      nodes.push(uploads);
    }
    syncChildren(target, nodes);
    restoreSelection();
    for (const key of view.parts.keys()) if (!used.has(key)) view.parts.delete(key);
    const ids = new Set(runs.map(run => run.id));
    for (const id of view.runs.keys()) if (!ids.has(id)) view.runs.delete(id);
  }
  return {el, button, fileIcon, attachmentCard, safeLink, markdown, renderRuns, usageText, requestUsageText, STATUS};
});
