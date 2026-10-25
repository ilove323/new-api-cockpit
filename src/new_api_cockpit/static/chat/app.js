(function (root, factory) {
  'use strict';
  const value = factory(typeof module === 'object' && module.exports
    ? {...require('./state.js'), ...require('./selection.js'), ...require('./api.js'), ...require('./render.js'), ...require('./files.js')} : root.CockpitChat);
  if (typeof module === 'object' && module.exports) module.exports = value;
  else { Object.assign(root.CockpitChat, value); if (root.document.body.dataset.chatPage === 'chat') value.createChatApp({window: root, document: root.document}); }
})(typeof window === 'object' ? window : globalThis, function (C) {
  'use strict';
  function createChatApp({window: win, document: doc, api: injectedAPI, scope = new C.Scope()}) {
    const $ = id => doc.getElementById(id), status = (text, error = false) => { $('chat-status').textContent = text; $('chat-status').classList.toggle('error', error); };
    const selectionCache = C.createSelectionCache({ownerUserId: Number($('chat-root')?.dataset?.ownerUserId), storage: () => win.localStorage});
    const eventProjector = C.createEventProjector();
    let selection = {...selectionCache.read(), mode: 'files'};
    let timer = null, modelsVersion = 0, listVersion = 0, historyVersion = 0, contextVersion = 0, paint = null, listLoading = false, creating = false, filesRevision = '', outputView = '', attachmentView = '', pollFailures = 0, pollRetryAfter = 0;
    const s = {config: null, context: null, tokens: [], models: [], modelsTokenId: null, modelsLoading: false, conversations: [], cursor: null, cursors: new Set(), total: 0, meta: null, events: [], files: [], skills: [], attachments: [], pendingUploads: [], deletedFiles: new Map(), deletingFileId: null, runStates: new Map(), historyStatus: 'idle', historyUntilSeq: null, historyError: null, sending: false, stopping: false, uploading: false, selecting: false, undoing: false, contextLoading: false, uncertain: false, controller: null, pendingRequestId: null, pendingText: null};
    function rememberSelection() { if (!scope.blocked) selectionCache.write(selection); }
    function clearView() {
      win.clearTimeout(timer); timer = null; historyVersion++;
      filesRevision = ''; outputView = ''; attachmentView = ''; pollFailures = 0; pollRetryAfter = 0;
      if (paint !== null) win.cancelAnimationFrame(paint); paint = null;
      Object.assign(s, {meta: null, events: [], files: [], skills: [], attachments: [], pendingUploads: [], deletedFiles: new Map(), deletingFileId: null, runStates: new Map(), historyStatus: 'idle', historyUntilSeq: null, historyError: null, sending: false, stopping: false, uploading: false, selecting: false, undoing: false, uncertain: false, controller: null, pendingRequestId: null, pendingText: null});
      $('chat-candidate-status').textContent = '';
      $('chat-title').textContent = '选择或新建一个聊天'; $('chat-messages').replaceChildren(); $('chat-files-list').replaceChildren(); $('chat-attachments').replaceChildren(); $('chat-attachments').hidden = true;
      $('chat-text').value = ''; $('chat-file-status').textContent = ''; $('chat-upload').value = '';
      $('chat-storage-warning').hidden = true; $('chat-storage-warning').textContent = '';
    }
    function clearAll() {
      scope.invalidate(); listVersion++; contextVersion++; modelsVersion++; listLoading = false; creating = false; clearView();
      Object.assign(s, {config: null, context: null, contextLoading: false, tokens: [], models: [], modelsTokenId: null, modelsLoading: false, conversations: [], cursor: null, cursors: new Set(), total: 0});
      selection = {...C.emptySelection(), mode: 'files'};
      for (const id of ['chat-list', 'chat-token', 'chat-model']) $(id)?.replaceChildren();
      $('chat-token').value = ''; $('chat-model').value = '';
      for (const id of ['chat-list-info', 'chat-list-status', 'chat-text-count']) { const node = $(id); if (node) node.textContent = ''; }
      $('chat-more').hidden = true; status('登录状态已变化，旧内容已清空。请重新载入并登录。', true); updateControls();
    }
    const api = injectedAPI || C.createAPI({scope, onAuth: clearAll});
    const valid = ticket => scope.current(ticket);
    const currentHistory = (ticket, version) => valid(ticket) && version === historyVersion;
    function report(error, ticket, prefix = '') { if (valid(ticket) && error.name !== 'StaleResponse') status(prefix + error.message, true); }
    function projection() {
      const p = eventProjector.update(s.events);
      return {...p, runs: p.runs.map(run => {
        const state = s.runStates.get(run.id);
        return state && !C.TERMINAL.has(run.status) ? {...run, ...state, _seq: run._seq, _identity: run._identity} : run;
      })};
    }
    function currentRun() { return s.meta?.active_run_id || projection().runs.findLast(r => !C.TERMINAL.has(r.status))?.id || null; }
    function busy() { return s.sending || s.uploading || !!s.deletingFileId || s.selecting || s.undoing || !!currentRun(); }
    const ownerUserId = () => Number($('chat-root')?.dataset?.ownerUserId);
    function runOptions() {
      // UI defaults do not depend on optional form controls. The API retains its
      // explicit options for old clients and existing multi-candidate history.
      const features = s.config?.features, web = features?.web;
      const webAvailable = features?.web_enabled !== false && (!web || ['http_fetch', 'download_url', 'web_search'].some(name => web[name] === true));
      return {candidate_count: 1, web_enabled: webAvailable, auto_memory: features?.auto_memory !== false, context_strategy: 'adaptive'};
    }
    function adaptiveAvailable() {
      const strategies = s.config?.features?.context_strategies;
      return !Array.isArray(strategies) || strategies.includes('adaptive');
    }
    function updateCandidateStatus() {
      const candidateStatus = $('chat-candidate-status');
      if (candidateStatus) candidateStatus.textContent = C.needsCandidateSelection(projection().runs) ? '请先在最新完成的多候选回答中选择一个分支，才能继续发送。' : '';
    }
    function historyReady() {
      const metaSeq = s.meta?.last_seq ?? s.meta?.revision;
      return s.historyStatus === 'ready' && Number.isSafeInteger(s.historyUntilSeq) && Number.isSafeInteger(metaSeq)
        && (s.events.at(-1)?.seq || 0) >= Math.max(s.historyUntilSeq, metaSeq);
    }
    function loadedIDs(meta) {
      const ids = meta?.loaded_skill_ids === undefined ? [] : meta.loaded_skill_ids;
      if (!Array.isArray(ids) || ids.length > 3 || new Set(ids).size !== ids.length || ids.some(id => !C.isUUID(id))) throw new Error('服务器聊天 Skills 状态无效，请只读刷新。');
      return [...ids];
    }
    function restoreSkills(meta) { s.skills = loadedIDs(meta); }
    function unavailableSkills() { return s.skills.filter(id => !s.context?.skills?.some(skill => skill.id === id)); }
    function updateControls() {
      const disabled = scope.blocked, active = busy() || creating, blank = !scope.cid && !s.meta;
      const writable = !disabled && (blank || !!s.meta && s.meta.storage_state !== 'corrupt');
      $('chat-new').disabled = disabled || creating;
      $('chat-list-refresh').disabled = disabled || listLoading; $('chat-more').disabled = disabled || listLoading;
      $('chat-selection').disabled = disabled || active;
      $('chat-token').disabled = disabled || active || !s.tokens.length;
      const catalogCurrent = s.modelsTokenId === Number($('chat-token').value);
      $('chat-model-load').disabled = disabled || active || s.modelsLoading || !$('chat-token').value;
      $('chat-model').disabled = disabled || active || s.modelsLoading || !catalogCurrent || !s.models.length;
      $('chat-text').disabled = !writable || active || s.uncertain;
      const model = catalogCurrent && !s.modelsLoading ? s.models.find(m => m.id === $('chat-model').value) : null, token = s.tokens.find(t => t.id === Number($('chat-token').value));
      updateCandidateStatus();
      if (!disabled && s.config && !adaptiveAvailable()) status('当前服务暂不支持自适应上下文，请联系维护者。', true);
      $('chat-send').disabled = !writable || !blank && !historyReady() || active || s.uncertain || s.contextLoading || unavailableSkills().length > 0 || C.needsCandidateSelection(projection().runs) || !adaptiveAvailable() || !s.config || !s.context || !token?.usable || !model?.chat_enabled;
      $('chat-stop').disabled = disabled || s.stopping || !currentRun();
      $('chat-rename').disabled = !s.meta || !writable || active; $('chat-delete').disabled = !s.meta || !writable || active;
      const uploadHint = disabled ? '请重新登录后上传。' : !s.config ? '正在读取上传配置…' : creating ? '正在新建聊天…' : active ? '当前聊天正在处理，请稍后上传。' : s.meta?.storage_state === 'corrupt' ? '当前聊天存储待修复，暂不能上传。' : scope.cid && !s.meta ? '请先刷新当前聊天状态。' : '';
      $('chat-upload').disabled = disabled || !s.config || active || s.meta?.storage_state === 'corrupt' || !!scope.cid && !s.meta;
      $('chat-attach').disabled = $('chat-upload').disabled;
      $('chat-attach').title = uploadHint || (s.config ? `上传附件（单文件不超过 ${C.fileSizeLabel(s.config.limits.upload_file_bytes)}；${s.config.allowed_file_extensions.join(' ')}）` : '上传附件');
      $('chat-upload-hint').textContent = uploadHint; $('chat-upload-hint').hidden = !uploadHint;
      $('chat-text-count').textContent = s.config ? `${C.bytes($('chat-text').value)} / ${s.config.limits.user_text_bytes} 字节` : '';
      if (s.meta) {
        $('chat-title').textContent = s.meta.title;
        const state = s.meta.storage_state;
        $('chat-storage-warning').hidden = state === 'ok';
        $('chat-storage-warning').textContent = state === 'corrupt' ? '该聊天存储损坏，仅展示已验证前缀；禁止发送和上传，请联系维护者恢复备份。' : state === 'repaired' ? '该聊天曾被恢复；中断轮次不会自动重新执行。' : '';
      }
    }
    function renderList() {
      $('chat-list').replaceChildren(...s.conversations.map(item => {
        const node = C.button(doc, `${item.title} · ${item.last_run_status ? C.STATUS[item.last_run_status] || item.last_run_status : '空聊天'}${item.storage_state === 'corrupt' ? ' · 待修复' : ''}`, () => openConversation(item.id));
        if (item.id === scope.cid) node.setAttribute('aria-current', 'page'); return node;
      }));
      $('chat-list-info').textContent = `已显示 ${s.conversations.length} / ${s.total} 个本人聊天（包括空、失败和中断聊天）`;
      $('chat-more').hidden = !s.cursor; updateControls();
    }
    function updateListItem(meta) {
      const at = s.conversations.findIndex(item => item.id === meta.id);
      if (at >= 0) s.conversations[at] = {...s.conversations[at], ...meta};
      renderList();
    }
    async function loadList(reset = true) {
      if (scope.blocked || !reset && (listLoading || !s.cursor)) return;
      const version = ++listVersion, ticket = scope.ticket(true); listLoading = true; $('chat-list-status').textContent = '正在读取…'; updateControls();
      const cursors = reset ? new Set() : new Set(s.cursors);
      try {
        const value = await api.conversations(s.config?.limits.list_page_default || 50, reset ? null : s.cursor, {ticket});
        if (!valid(ticket) || version !== listVersion) return;
        const cursor = C.pageCursor(value, cursors);
        const rows = reset ? [] : s.conversations.slice(), ids = new Set(rows.map(row => row.id));
        for (const row of value.conversations) { if (!C.isUUID(row.id)) throw new Error('聊天列表含无效标识。'); if (!ids.has(row.id)) { rows.push(row); ids.add(row.id); } }
        Object.assign(s, {conversations: rows, cursor, cursors, total: value.total});
        $('chat-list-status').textContent = value.warnings?.length ? '部分聊天已修复或待修复；仍保留在列表中。' : '';
        renderList();
      } catch (error) { if (valid(ticket) && version === listVersion) $('chat-list-status').textContent = `列表未加载成功，不代表没有历史。${error.message}`; }
      finally { if (valid(ticket) && version === listVersion) { listLoading = false; updateControls(); } }
    }
    function renderTokens() {
      $('chat-token').replaceChildren(C.el(doc, 'option', s.tokens.length ? '请选择本人 KEY' : '没有可用 KEY；仍可查看全部历史'));
      $('chat-token').firstChild.value = '';
      for (const token of s.tokens) {
        const option = C.el(doc, 'option', `${token.name} · ${token.group || '默认组'} · ${token.key_masked || ''}${token.usable ? '' : `（${token.unavailable_reason || '不可用'}）`}`);
        option.value = String(token.id); option.disabled = !token.usable; $('chat-token').append(option);
      }
      const choice = s.tokens.find(t => t.usable && t.id === selection.token_id);
      $('chat-token').value = choice ? String(choice.id) : '';
      if (!choice && selection.token_id !== null) { selection = {...selection, token_id: null, model: ''}; rememberSelection(); }
      updateControls(); return choice;
    }
    function renderModels() {
      const desired = selection.model;
      $('chat-model').replaceChildren();
      for (const model of s.models) {
        const enabled = model.chat_enabled;
        const option = C.el(doc, 'option', `${model.id}${enabled ? '' : `（${model.reason || '协议未确认'}）`}`);
        option.value = model.id; option.disabled = !enabled; $('chat-model').append(option);
      }
      const selected = s.models.find(m => (!desired || m.id === desired) && m.chat_enabled);
      if (!selected) { const option = C.el(doc, 'option', s.modelsLoading ? '正在载入模型…' : s.models.length ? '请选择可用模型' : '选择 KEY 后载入模型目录'); option.value = ''; $('chat-model').append(option); }
      $('chat-model').value = selected?.id || '';
      const noModel = '暂无可用模型，请载入其他本人 KEY 的模型目录。', missingModel = '原选模型已不可用，请重新选择。';
      if (selected && s.modelsTokenId === selection.token_id) { selection.model = selected.id; rememberSelection(); }
      if (s.models.length && !selected) status(s.models.some(m => m.chat_enabled) ? missingModel : noModel, true);
      else if ([noModel, missingModel].includes($('chat-status').textContent)) status('');
      updateControls();
    }
    async function loadModels() {
      const tokenId = Number($('chat-token').value);
      if (scope.blocked || s.modelsLoading || !s.tokens.some(t => t.id === tokenId && t.usable)) return;
      if ($('chat-status').textContent.startsWith('模型目录未载入：')) status('');
      const ticket = scope.ticket(true), version = ++modelsVersion;
      if (selection.token_id !== tokenId) { selection = {...selection, token_id: tokenId, model: ''}; rememberSelection(); }
      Object.assign(s, {models: [], modelsTokenId: null, modelsLoading: true}); renderModels();
      try {
        const value = await api.models(tokenId, {ticket});
        if (!valid(ticket) || version !== modelsVersion || tokenId !== Number($('chat-token').value)) return;
        if (!Array.isArray(value?.models)) throw new Error('模型目录响应无效。');
        s.models = value.models; s.modelsTokenId = tokenId;
      } catch (error) { if (version === modelsVersion && tokenId === Number($('chat-token').value)) report(error, ticket, '模型目录未载入：'); }
      finally { if (valid(ticket) && version === modelsVersion) { s.modelsLoading = false; renderModels(); } }
    }
    async function loadContext({silent = false} = {}) {
      const ticket = scope.ticket(silent), version = ++contextVersion;
      s.contextLoading = true; updateControls();
      try {
        const value = await api.context({ticket}); if (!valid(ticket) || version !== contextVersion) return;
        if (!s.context || value.context_revision >= s.context.context_revision) s.context = value;
        updateControls();
        if (!silent) status('已刷新个人设置；当前聊天已载入 Skills 保持不变。');
      } catch (error) { if (version === contextVersion) report(error, ticket, '个人设置未载入：'); }
      finally { if (version === contextVersion) { s.contextLoading = false; if (valid(ticket)) updateControls(); } }
    }
    function allFiles() { return [...new Map([...projection().files, ...s.files, ...s.deletedFiles.values()].map(file => [file.id, file])).values()]; }
    function canReference() { return !!s.meta && !!s.config && !scope.blocked && !busy() && s.meta.storage_state !== 'corrupt'; }
    function referenceFile(file, ticket) {
      if (!valid(ticket) || !canReference() || allFiles().find(item => item.id === file.id)?.deleted_at) return;
      if (s.attachments.includes(file.id)) s.attachments = s.attachments.filter(id => id !== file.id);
      else {
        if (!allFiles().some(item => item.id === file.id)) return;
        if (s.attachments.length >= s.config.limits.attachments_per_turn) { status('本轮引用附件数量已达上限。', true); return; }
        s.attachments = [...s.attachments, file.id];
      }
      renderCurrent();
    }
    async function downloadCurrentFile(file, ticket) {
      if (!valid(ticket) || !allFiles().some(item => item.id === file.id)) return;
      try { await C.downloadFile({api, scope, cid: ticket.cid, file, doc, ticket}); }
      catch (error) { report(error, ticket, '下载失败：'); }
    }
    function canDeleteFiles() { return !!s.meta && !scope.blocked && !s.deletingFileId && s.meta.storage_state !== 'corrupt'; }
    async function deleteFile(file, ticket) {
      if (!valid(ticket) || !canDeleteFiles()) return;
      const current = allFiles().find(item => item.id === file.id);
      if (!current || current.kind !== 'upload' || current.deleted_at) return;
      if (!win.confirm(`删除附件「${current.name}」？文件将无法下载或再次引用，历史消息保留“已删除”标记。已发给模型的内容无法撤回。`)) return;
      const cid = scope.cid; s.deletingFileId = file.id; renderCurrent();
      let failure = '';
      try {
        const value = await api.deleteUpload(cid, file.id, {ticket}); if (!valid(ticket)) return;
        if (value?.file?.id !== file.id || !value.file.deleted_at) throw new Error('删除响应未确认。');
        s.deletedFiles.set(file.id, value.file);
      } catch (error) { if (valid(ticket)) failure = error.message; }
      finally {
        if (valid(ticket)) {
          try {
            const value = await api.files(cid, {ticket}); if (!valid(ticket)) return;
            s.files = value.files;
            for (const item of value.files) if (item.deleted_at) s.deletedFiles.set(item.id, item);
          } catch (error) { if (valid(ticket)) failure ||= error.message; }
          if (valid(ticket)) {
            s.attachments = s.attachments.filter(id => !s.deletedFiles.has(id)); s.deletingFileId = null;
            renderCurrent();
            status(s.deletedFiles.has(file.id) ? '附件已删除。' : `删除结果未确认：${failure || '请刷新查看'}。未自动重试。`, !s.deletedFiles.has(file.id));
          }
        }
      }
    }
    function renderFiles() {
      const files = allFiles(), ticket = scope.ticket(), outputs = files.filter(file => file.kind === 'output').reverse();
      const nextOutput = JSON.stringify(outputs.map(file => [file.id, file.name, file.version, file.size_bytes]));
      if (outputView !== nextOutput) {
        outputView = nextOutput;
        $('chat-files-list').replaceChildren();
        for (const file of outputs) {
          const item = C.button(doc, '', () => downloadCurrentFile(file, ticket), 'chat-file chat-file-entry'); item.dataset.file = file.id; item.title = file.name;
          item.setAttribute('aria-label', `下载输出 ${file.name} v${file.version}`);
          const text = C.el(doc, 'span', null, 'chat-file-description');
          text.append(C.el(doc, 'span', file.name, 'chat-file-name'), C.el(doc, 'small', `v${file.version} · ${C.fileSizeLabel(file.size_bytes)}`, 'hint'));
          item.append(C.fileIcon(doc, file), text); $('chat-files-list').append(item);
        }
        if (!outputs.length) $('chat-files-list').append(C.el(doc, 'p', '暂无输出', 'hint'));
      }
      const nextAttachments = JSON.stringify([s.attachments.map(id => files.find(file => file.id === id) || {id}), s.pendingUploads.map(file => [file.name, file.size]), canReference(), canDeleteFiles()]);
      if (attachmentView === nextAttachments) return;
      attachmentView = nextAttachments;
      const tray = $('chat-attachments'); tray.replaceChildren();
      for (const id of s.attachments) {
        const file = files.find(item => item.id === id);
        const chip = C.el(doc, 'div', null, 'chat-attachment-chip'); chip.dataset.file = id;
        chip.append(C.fileIcon(doc, file || {name: ''}));
        const text = C.el(doc, 'span', null, 'chat-file-description');
        text.append(C.el(doc, 'span', file?.name || '附件暂不可用', 'chat-file-name'), C.el(doc, 'small', file ? C.fileSizeLabel(file.size_bytes) : '', 'hint')); chip.append(text);
        const remove = C.button(doc, '×', () => referenceFile({id}, ticket), 'chat-attachment-remove');
        remove.setAttribute('aria-label', `移除附件 ${file?.name || id}`); remove.title = '取消本轮引用，不删除文件'; remove.disabled = !canReference(); chip.append(remove);
        if (file?.kind === 'upload' && !file.deleted_at) {
          const discard = C.button(doc, '删除', () => deleteFile(file, ticket), 'button-small danger chat-attachment-delete');
          discard.setAttribute('aria-label', `删除附件 ${file.name}`); discard.disabled = !canDeleteFiles(); chip.append(discard);
        }
        tray.append(chip);
      }
      for (const file of s.pendingUploads) {
        const chip = C.el(doc, 'div', null, 'chat-attachment-chip chat-attachment-uploading');
        chip.append(C.fileIcon(doc, file), C.el(doc, 'span', file.name, 'chat-file-name'), C.el(doc, 'small', '上传中…', 'hint')); tray.append(chip);
      }
      tray.hidden = !s.attachments.length && !s.pendingUploads.length;
    }
    function renderCurrent() {
      const fileTicket = scope.ticket();
      const nearBottom = win.innerHeight + win.scrollY >= doc.documentElement.scrollHeight - 140;
      const disabled = scope.blocked || busy() || !historyReady() || s.uncertain || s.meta?.storage_state === 'corrupt';
      C.renderRuns(doc, $('chat-messages'), projection().runs, {onSelectCandidate: selectCandidate, selectionDisabled: disabled, ownerUserId: ownerUserId(), onUndoMemory: undoMemory, memoryDisabled: disabled, files: allFiles(), attachmentIds: s.attachments, filesDisabled: !canReference(), onReferenceFile: file => referenceFile(file, fileTicket), onDownloadFile: file => downloadCurrentFile(file, fileTicket), onDeleteFile: file => deleteFile(file, fileTicket), deleteDisabled: !canDeleteFiles()}); renderFiles(); updateControls();
      if (nearBottom && s.sending) $('chat-compose').scrollIntoView({block: 'end'});
    }
    function ingest(events, immediate = false) {
      const oldSeq = s.events.at(-1)?.seq || 0;
      s.events = C.mergeEvents(s.events, events, scope.cid);
      let skillsChanged = false;
      for (const event of events.filter(item => item.seq > oldSeq)) {
        const priorRevision = s.meta?.revision ?? 0;
        if (s.meta) { s.meta.revision = Math.max(s.meta.revision, event.seq); s.meta.last_seq = s.meta.revision; }
        if (event.type === 'conversation.renamed' && s.meta) s.meta.title = event.data.title;
        if (event.type === 'conversation.skills.updated' && s.meta && event.seq >= priorRevision) {
          if (!Array.isArray(event.data.skill_ids)) throw new Error('聊天 Skills 更新事件无效。');
          s.meta.loaded_skill_ids = loadedIDs({loaded_skill_ids: event.data.skill_ids}); restoreSkills(s.meta); skillsChanged = true;
        }
        if (event.type === 'run.accepted') {
          s.meta.active_run_id = event.run_id;
          if (event.data.client_request_id === s.pendingRequestId) {
            if ($('chat-text').value === s.pendingText) { s.attachments = []; $('chat-text').value = ''; $('chat-file-status').textContent = ''; }
            s.pendingRequestId = null; s.pendingText = null; updateControls();
          }
        }
        if (event.type === 'run.terminal' && s.meta?.active_run_id === event.run_id) { s.meta.active_run_id = null; s.meta.last_run_status = event.data.status; }
      }
      if (skillsChanged) updateControls();
      if (immediate) renderCurrent();
      else if (paint === null) { const ticket = scope.ticket(); paint = win.requestAnimationFrame(() => { paint = null; if (valid(ticket)) renderCurrent(); }); }
    }
    async function loadEvents(ticket, version, firstPage = null) {
      let after = s.events.at(-1)?.seq || 0, until;
      while (true) {
        const page = firstPage || await api.events(scope.cid, after, until, s.config?.limits.event_page_max || 200, {ticket}); firstPage = null; if (!currentHistory(ticket, version)) return;
        if (!Number.isSafeInteger(page.until_seq) || until !== undefined && until !== page.until_seq || typeof page.has_more !== 'boolean' || !Number.isSafeInteger(page.next_after_seq) || page.next_after_seq < after || page.next_after_seq > page.until_seq) throw new Error('事件分页边界无效。');
        if (!Array.isArray(page.events) || (page.events.at(-1)?.seq ?? after) !== page.next_after_seq || page.has_more !== (page.next_after_seq < page.until_seq)
          || page.events.some(event => !Number.isSafeInteger(event?.seq) || event.seq <= after || event.seq > page.until_seq)) throw new Error('事件分页内容与边界不一致。');
        until = page.until_seq; s.historyUntilSeq = until; ingest(page.events);
        if (page.storage_state && s.meta) s.meta.storage_state = page.storage_state;
        if (!page.has_more) { if ((s.events.at(-1)?.seq || 0) < until) throw new Error('历史事件未完整恢复，请只读刷新。'); break; }
        if (page.next_after_seq <= after) throw new Error('事件分页未前进，已停止读取。');
        after = page.next_after_seq;
      }
    }
    function schedulePoll(ticket) {
      win.clearTimeout(timer);
      const base = (s.config?.limits.poll_interval || 2) * 1000;
      const delay = Math.max(Math.min(60000, base * 2 ** pollFailures), pollRetryAfter - Date.now(), doc.hidden ? 10000 : 0);
      if (valid(ticket) && currentRun() && !s.sending) timer = win.setTimeout(() => Date.now() < pollRetryAfter ? schedulePoll(ticket) : recover(ticket), Math.min(2147483647, delay));
    }
    async function recover(ticket = scope.ticket()) {
      if (!scope.cid || !valid(ticket)) return;
      // Scope tickets reject old chats/accounts; this generation also rejects
      // a slower refresh of the same chat after a newer refresh has started.
      const version = ++historyVersion;
      Object.assign(s, {historyStatus: 'loading', historyUntilSeq: null, historyError: null});
      win.clearTimeout(timer); timer = null; updateControls();
      try {
        let files;
        if (s.config?.features?.recovery_snapshot) {
          const value = await api.sync(scope.cid, s.events.at(-1)?.seq || 0, s.config?.limits.event_page_max || 200, filesRevision, s.context?.context_revision || 0, {ticket});
          if (!currentHistory(ticket, version)) return;
          if (value?.conversation?.id !== scope.cid || value.events?.until_seq !== value.conversation.last_seq || !/^[0-9a-f]{64}$/.test(value.files_revision) || !(value.files === null || Array.isArray(value.files)) || value.files === null && value.files_revision !== filesRevision) throw new Error('聊天恢复快照无效。');
          restoreSkills(value.conversation); s.meta = value.conversation;
          await loadEvents(ticket, version, value.events); if (!currentHistory(ticket, version)) return;
          const rid = s.meta.active_run_id || projection().runs.at(-1)?.id;
          if ((value.run?.id || undefined) !== rid) throw new Error('运行状态响应与聊天不一致。');
          if (value.run) {
            if (!C.isUUID(value.run.id) || !['accepted', 'running', ...C.TERMINAL].includes(value.run.status)) throw new Error('运行状态响应无效。');
            s.runStates.set(value.run.id, value.run);
          }
          files = {files: value.files === null ? s.files : value.files}; filesRevision = value.files_revision;
          if (value.context) {s.context = value.context; updateControls();}
        } else {
        // Older servers advertise no snapshot endpoint; never guess support by
        // sending a request and retrying after a failed response.
        const value = await api.conversation(scope.cid, {ticket}); if (!currentHistory(ticket, version)) return;
        if (value?.conversation?.id !== scope.cid) throw new Error('聊天状态响应归属无效。');
        restoreSkills(value.conversation); s.meta = value.conversation; updateControls();
        await loadEvents(ticket, version); if (!currentHistory(ticket, version)) return;
        const rid = currentRun() || projection().runs.at(-1)?.id;
        if (rid) {
          const value = await api.run(scope.cid, rid, {ticket}); if (!currentHistory(ticket, version)) return;
          if (value.run.id !== rid || !['accepted', 'running', ...C.TERMINAL].includes(value.run.status)) throw new Error('运行状态响应与请求不一致。');
          s.runStates.set(rid, value.run);
          if (C.TERMINAL.has(value.run.status)) {
            if (s.meta.active_run_id === rid) s.meta.active_run_id = null;
            await loadEvents(ticket, version); if (!currentHistory(ticket, version)) return;
          }
        }
        files = await api.files(scope.cid, {ticket}); if (!currentHistory(ticket, version)) return;
        }
        s.files = files.files;
        for (const file of s.files) if (file.deleted_at) s.deletedFiles.set(file.id, file);
        s.attachments = s.attachments.filter(id => !s.deletedFiles.has(id));
        const contextRevision = projection().runs.reduce((revision, run) => Math.max(revision, C.ownerMemoryAudit(run, ownerUserId())?.after.context_revision || 0, Number.isSafeInteger(run.memory_reverted_context_revision) ? run.memory_reverted_context_revision : 0), 0);
        if (!s.config?.features?.recovery_snapshot && s.context && s.context.context_revision < contextRevision) {
          const context = await api.context({ticket}); if (!currentHistory(ticket, version)) return;
          s.context = context; updateControls();
        }
        s.historyStatus = 'ready';
        if (!historyReady()) throw new Error('历史事件未达到已提交高水位，请只读刷新。');
        s.uncertain = false; updateControls(); renderCurrent(); updateListItem(s.meta);
        pollFailures = 0; pollRetryAfter = 0;
        if (!s.sending) {
          const last = projection().runs.at(-1);
          status(currentRun() ? '正在只读查询运行状态，不会继续或重发生成。' : last?.uncertain ? '历史已恢复。该轮结果不确定，可能已计费；请核对 New API 用量。' : '已完整恢复已提交历史；没有重新调用模型。');
        }
      } catch (error) {
        if (currentHistory(ticket, version)) {
          pollFailures = Math.min(6, pollFailures + 1); pollRetryAfter = Math.max(pollRetryAfter, Date.now() + (error.retryAfterMs || 0));
          s.historyStatus = 'failed'; s.historyError = error.message;
          report(error, ticket, '只读恢复未完成，现有内容保留；发送已禁用：');
        }
      }
      finally { if (currentHistory(ticket, version)) { updateControls(); schedulePoll(ticket); } }
    }
    async function openConversation(cid, push = true) {
      if (!C.isUUID(cid) || scope.blocked) return;
      const ticket = scope.switch(cid); clearView(); status('正在恢复完整历史…'); renderList(); renderModels(); updateControls();
      if (push) { const url = new URL(win.location.href); url.search = new URLSearchParams({conversation: cid}).toString(); win.history.pushState(null, '', url.pathname + url.search); }
      try { await recover(ticket); }
      finally { if (valid(ticket)) { renderCurrent(); updateControls(); } }
    }
    async function newConversation({preserveDraft = false} = {}) {
      if (creating || scope.blocked) return;
      const ticket = scope.ticket(), draft = preserveDraft ? $('chat-text').value : null; creating = true; updateControls();
      try {
        const value = await api.create(C.uuid(win.crypto), {ticket}); if (!valid(ticket)) return;
        // A fresh first page includes this chat; old cursor snapshots never hide older rows.
        await openConversation(value.conversation.id);
        const destination = scope.ticket();
        if (!valid(destination) || scope.cid !== value.conversation.id || s.meta?.id !== value.conversation.id) return null;
        if (preserveDraft) $('chat-text').value = draft;
        await loadList();
        return valid(destination) && scope.cid === value.conversation.id && s.meta?.id === value.conversation.id ? {cid: scope.cid, ticket: destination} : null;
      } catch (error) { report(error, ticket, '创建结果待确认，请重新读取列表；未自动重试。'); }
      finally { creating = false; updateControls(); }
    }
    async function send(event) {
      event.preventDefault(); if (busy() || creating || s.uncertain || scope.blocked || s.contextLoading) return;
      if (unavailableSkills().length) { status('当前聊天引用的 Skill 已不可用，请在个人设置中检查，或新建聊天。', true); return; }
      if (C.needsCandidateSelection(projection().runs)) { status('请先选择最新完成轮次的一个候选，再继续发送。', true); return; }
      if (scope.cid && !historyReady()) { status(s.historyStatus === 'loading' ? '历史仍在加载，完整恢复后才能发送。' : '历史尚未完整恢复，请只读刷新后再发送。', true); return; }
      if (s.modelsLoading || s.modelsTokenId !== Number($('chat-token').value)) { status('请先载入当前 KEY 的模型目录。', true); return; }
      const ticket = scope.ticket(), cid = scope.cid; let body;
      try {
        // Validate before creating anything. The placeholder is local only;
        // after creation, send revalidates using the committed chat revision.
        body = C.buildRunRequest({meta: s.meta || (!cid ? {revision: 1, storage_state: 'ok', active_run_id: null} : null), token: s.tokens.find(t => t.id === Number($('chat-token').value)), model: s.models.find(m => m.id === $('chat-model').value), mode: 'files', text: $('chat-text').value, attachments: s.attachments, skillIds: s.skills, context: s.context, config: s.config, requestId: C.uuid(win.crypto), options: runOptions()});
      } catch (error) { report(error, ticket); return; }
      if (!cid) {
        const created = await newConversation({preserveDraft: true});
        if (created && valid(created.ticket)) return send(event);
        return;
      }
      s.sending = true; s.pendingRequestId = body.client_request_id; s.pendingText = body.text; s.controller = new AbortController(); renderFiles(); updateControls(); status('已提交一次请求；正在等待持久化接受与回答，不会自动重发。');
      let streamTerminal = false, streamRunId = null, notice = '';
      try {
        const result = await api.start(cid, body, {ticket, controller: s.controller, maxFrameBytes: (s.config.limits.event_bytes || 1048576) + 1024, onEvent: event => {
          scope.assert(ticket);
          if (event.type === 'run.accepted') {
            if (event.data.client_request_id !== body.client_request_id || !C.isUUID(event.run_id) || streamRunId && streamRunId !== event.run_id) throw new Error('运行流与当前发送请求不一致。');
            streamRunId = event.run_id;
          }
          if (event.run_id !== null && event.run_id !== streamRunId) throw new Error('已忽略其他运行的迟到事件。');
          ingest([event]);
        }});
        if (!valid(ticket)) return;
        streamTerminal = result.terminal;
        if (result.replayed) { s.pendingRequestId = null; s.pendingText = null; s.attachments = []; $('chat-text').value = ''; $('chat-file-status').textContent = ''; notice = '命中原有运行，正在 GET 恢复；没有再次派发。'; }
        else if (!result.terminal) { s.uncertain = true; notice = '连接结束但未收到终态，结果待确认；只通过 GET 查询，未自动重发。'; }
      } catch (error) {
        if (!valid(ticket)) return;
        s.uncertain = true;
        notice = s.stopping || error.name === 'AbortError' ? '已关闭浏览器流，正在只读确认停止结果；这不代表立即停止或不计费。' : `${error.message} 结果待确认，未自动重发；正在 GET 恢复。`;
      } finally {
        if (valid(ticket)) {
          s.sending = false; s.controller = null; status(notice || (streamTerminal ? '运行已结束，正在只读核对历史。' : '正在只读恢复…'), !!notice);
          await recover(ticket);
          if (valid(ticket)) {
            if (notice) status(notice + (currentRun() ? ' 本轮仍可能运行或计费。' : ' 已读取当前已提交状态。'), true);
            updateControls(); renderCurrent();
          }
        }
      }
    }
    async function selectCandidate(rid, index) {
      if (scope.blocked || busy() || s.uncertain || !historyReady() || s.meta?.storage_state === 'corrupt' || !C.canSelectCandidate(projection().runs, rid, index)) return;
      const ticket = scope.ticket(), cid = scope.cid;
      if (typeof api.selectCandidate !== 'function') { status('候选选择接口尚未可用；请勿重发生成。', true); return; }
      s.selecting = true; renderCurrent();
      let notice = '';
      try {
        const value = await api.selectCandidate(cid, rid, s.meta.revision, index, {ticket}); if (!valid(ticket)) return;
        if (value?.conversation?.id !== cid || value?.run?.id !== rid || value.run.status !== 'completed' || value.run.selected_candidate !== index) throw new Error('候选选择响应与本轮不一致。');
        s.meta = value.conversation;
        notice = `候选 ${index + 1} 已保存；正在只读核对完整历史，没有调用模型。`;
      } catch (error) {
        if (!valid(ticket)) return;
        s.uncertain = true; notice = `候选选择未确认：${error.message} 未自动重试，只读刷新后核对所选分支。`;
      } finally {
        if (valid(ticket)) {
          // Keep send/selection locked until the post-selection history catch-up finishes.
          await recover(ticket);
          if (valid(ticket)) { s.selecting = false; renderCurrent(); status(notice, !notice.startsWith('候选 ') || s.historyStatus !== 'ready'); }
        }
      }
    }
    async function undoMemory(rid) {
      if (scope.blocked || busy() || s.uncertain || !historyReady() || s.meta?.storage_state === 'corrupt') return;
      const run = projection().runs.find(r => r.id === rid), audit = run && C.ownerMemoryAudit(run, ownerUserId());
      if (!audit?.changed || run.memory_reverted || !C.TERMINAL.has(run.status)) return;
      if (typeof api.undoMemory !== 'function') { status('记忆撤销接口尚未可用；没有修改 MEMORY。', true); return; }
      const ticket = scope.ticket(), cid = scope.cid;
      s.undoing = true; renderCurrent(); let notice = '', failed = false;
      try {
        // Never use the frozen run context or stale settings revision for undo.
        const context = await api.context({ticket}); if (!valid(ticket)) return;
        const memory = context.profiles?.find(p => p.name === 'MEMORY.md');
        if (!Number.isSafeInteger(context.context_revision) || !memory || memory.revision !== audit.after.memory_revision || memory.sha256 !== audit.after.sha256) throw new Error('个人 MEMORY 已被后续修改，拒绝覆盖；请在个人设置比较当前内容。');
        s.context = context; updateControls();
        const metadata = await api.conversation(cid, {ticket}); if (!valid(ticket)) return;
        if (metadata?.conversation?.id !== cid || !Number.isSafeInteger(metadata.conversation.revision) || metadata.conversation.active_run_id || metadata.conversation.storage_state === 'corrupt') throw new Error('聊天已变化或不可写，请只读刷新后再检查。');
        const value = await api.undoMemory(cid, rid, context.context_revision, metadata.conversation.revision, {ticket}); if (!valid(ticket)) return;
        if (value?.conversation?.id !== cid || !Number.isSafeInteger(value.context?.context_revision) || !Array.isArray(value.context.profiles) || !Array.isArray(value.context.skills)) throw new Error('撤销响应无效，需要只读核对。');
        s.meta = value.conversation; s.context = value.context; restoreSkills(s.meta);
        notice = '已撤销本轮自动记忆更新；已只读核对个人上下文和完整历史，没有调用模型。';
      } catch (error) {
        if (!valid(ticket)) return;
        failed = true; notice = `记忆撤销未确认：${error.message} 未自动重试，将只读核对最新上下文与历史。`;
      } finally {
        if (valid(ticket)) {
          try {
            const context = await api.context({ticket}); if (!valid(ticket)) return;
            s.context = context; updateControls();
          } catch (error) { if (valid(ticket)) { s.context = null; updateControls(); failed = true; notice += ` 最新个人设置未读取：${error.message}`; } }
          if (valid(ticket)) {
            await recover(ticket);
            if (valid(ticket)) {
              s.undoing = false; renderCurrent();
              if (failed && s.historyStatus === 'ready' && projection().runs.find(r => r.id === rid)?.memory_reverted) notice = '撤销响应未确认，但只读历史已确认自动记忆已撤销；未自动重试或重复提交。';
              status(notice, failed || s.historyStatus !== 'ready');
            }
          }
        }
      }
    }
    async function stop() {
      const ticket = scope.ticket(), cid = scope.cid, rid = currentRun(), controller = s.controller;
      if (!rid || s.stopping || scope.blocked) return;
      s.stopping = true; updateControls();
      try {
        const result = await api.cancel(cid, rid, {ticket}); if (!valid(ticket)) return;
        status(result.status && C.TERMINAL.has(result.status) ? '运行已终止，正在只读核对。' : '停止请求已持久化（不等于已停止或不计费），正在只读核对。');
      } catch (error) { report(error, ticket, '停止未确认；仍将关闭浏览器流并只读查询。'); }
      finally {
        controller?.abort();
        if (valid(ticket)) { s.stopping = false; if (!s.sending) await recover(ticket); updateControls(); }
      }
    }
    async function uploadFiles(files) {
      let ticket = scope.ticket(), cid = scope.cid;
      if (!files.length || busy() || creating || scope.blocked || s.meta?.storage_state === 'corrupt') return;
      try {
        for (const file of files) C.checkUpload(file, s.config);
        if (files.length + s.attachments.length > s.config.limits.attachments_per_turn) throw new Error(`本轮最多引用 ${s.config.limits.attachments_per_turn} 个附件，请减少选择或先移除附件。`);
      } catch (error) { report(error, ticket); return; }
      if (!cid) {
        const created = await newConversation({preserveDraft: true});
        if (!created) return;
        ({ticket, cid} = created);
      }
      if (!valid(ticket) || scope.cid !== cid || !s.meta || s.meta.storage_state === 'corrupt' || busy()) return;
      s.uploading = true; s.pendingUploads = [...files]; renderCurrent(); $('chat-file-status').textContent = '正在上传附件…';
      let uploaded = 0;
      try {
        for (const file of files) {
          if (!valid(ticket)) return;
          const fid = C.uuid(win.crypto), result = await api.upload(cid, file, fid, {ticket}); if (!valid(ticket)) return;
          if (result?.file?.id !== fid || result.file.kind !== 'upload') throw new Error('上传响应无效，请刷新核对文件。');
          s.files = [...s.files.filter(item => item.id !== fid), result.file]; s.attachments = [...s.attachments, fid];
          s.pendingUploads.shift(); uploaded++; renderCurrent();
        }
        $('chat-file-status').textContent = '';
      } catch (error) { if (valid(ticket)) $('chat-file-status').textContent = `${uploaded ? `已添加 ${uploaded} 个附件。` : ''}${error.message} 未自动重传，请核对聊天中的附件。`; }
      finally {
        if (valid(ticket)) { s.uploading = false; s.pendingUploads = []; await recover(ticket); renderCurrent(); }
      }
    }
    function upload() { const files = Array.from($('chat-upload').files || []); $('chat-upload').value = ''; return uploadFiles(files); }
    async function rename() {
      if (!s.meta || busy()) return;
      const ticket = scope.ticket(), title = win.prompt('新标题', s.meta.title);
      if (title === null) return;
      if (!title.trim() || [...title.trim()].length > (s.config?.limits.title_chars || 80) || /[\u0000-\u001f\u007f]/.test(title)) { status('标题为空、过长或含控制字符。', true); return; }
      try { const value = await api.rename(scope.cid, title.trim(), s.meta.revision, {ticket}); if (!valid(ticket)) return; s.meta = value.conversation; await recover(ticket); } catch (error) { report(error, ticket, '改名未确认，未自动重试；请只读刷新。'); }
    }
    async function remove() {
      if (!s.meta || busy() || !win.confirm('确定删除该聊天全部记录、上传原件、输出版本和上下文快照？不会删除个人设置，也不承诺擦除备份或供应商日志。')) return;
      const ticket = scope.ticket(), cid = scope.cid;
      try {
        await api.remove(cid, s.meta.revision, {ticket}); if (!valid(ticket)) return;
        scope.switch(null); clearView(); updateControls(); renderModels(); updateControls();
        win.history.replaceState(null, '', win.location.pathname); await loadList(); status('聊天已删除。');
      } catch (error) { report(error, ticket, '删除未确认，未自动重试；请只读刷新列表。'); }
    }
    $('chat-list-refresh').addEventListener('click', () => loadList()); $('chat-more').addEventListener('click', () => loadList(false));
    $('chat-new').addEventListener('click', newConversation); $('chat-model-load').addEventListener('click', loadModels);
    $('chat-token').addEventListener('change', () => {
      if (scope.blocked || busy()) return;
      const token = s.tokens.find(t => t.id === Number($('chat-token').value) && t.usable);
      selection = {...selection, token_id: token?.id || null, model: ''}; rememberSelection();
      modelsVersion++; Object.assign(s, {models: [], modelsTokenId: null, modelsLoading: false}); renderModels();
    });
    $('chat-model').addEventListener('change', () => {
      if (scope.blocked || busy()) return;
      const model = s.modelsTokenId === Number($('chat-token').value) && s.models.find(m => m.id === $('chat-model').value && m.chat_enabled);
      if (model) { selection.model = model.id; rememberSelection(); renderModels(); } else updateControls();
    });
    $('chat-text').addEventListener('input', updateControls); $('chat-compose').addEventListener('submit', send);
    $('chat-stop').addEventListener('click', stop);
    $('chat-attach').addEventListener('click', () => { if (!$('chat-attach').disabled) $('chat-upload').click(); });
    $('chat-compose').addEventListener('dragover', event => { if (Array.from(event.dataTransfer?.types || []).includes('Files')) event.preventDefault(); });
    $('chat-compose').addEventListener('drop', event => { if (event.dataTransfer?.files?.length) { event.preventDefault(); uploadFiles(Array.from(event.dataTransfer.files)); } });
    $('chat-upload').addEventListener('change', upload); $('chat-rename').addEventListener('click', rename); $('chat-delete').addEventListener('click', remove);
    win.addEventListener('cockpit-auth-changed', clearAll); win.addEventListener('pagehide', clearAll);
    win.addEventListener('pageshow', event => { if (event.persisted) win.location.reload(); });
    win.addEventListener('popstate', () => { const cid = new URL(win.location.href).searchParams.get('conversation'); if (C.isUUID(cid)) openConversation(cid, false); else { scope.switch(null); clearView(); renderModels(); updateControls(); renderList(); } });
    async function init() {
      const ticket = scope.ticket(true); updateControls();
      const config = api.config({ticket}).then(value => {
        if (!valid(ticket)) return; s.config = value; $('chat-upload').accept = value.allowed_file_extensions.join(',');
        updateControls();
      }).catch(error => report(error, ticket, '限制配置未载入，发送/上传禁用：'));
      const tokens = api.tokens({ticket}).then(async value => { if (valid(ticket)) { s.tokens = value.tokens; if (renderTokens()) await loadModels(); } }).catch(error => report(error, ticket, 'KEY 摘要未读取；历史仍可查看：'));
      const cid = new URL(win.location.href).searchParams.get('conversation');
      // Capability discovery must finish before choosing the recovery protocol.
      // Other startup requests remain parallel; a config failure still permits
      // legacy read-only history rather than making another discovery request.
      const history = config.then(() => C.isUUID(cid) && valid(ticket) ? openConversation(cid, false) : undefined);
      await Promise.all([config, tokens, loadContext({silent: true}), loadList(), history]);
      if (valid(ticket)) updateControls();
    }
    const ready = init();
    return {ready, state: s, scope, openConversation, loadList, recover, send, stop, selectCandidate, undoMemory, clearAll};
  }
  return {createChatApp};
});
