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
    const control = (id, tag) => { const node = $(id); return node?.tagName === tag ? node : null; };
    const selectionCache = C.createSelectionCache({ownerUserId: Number($('chat-root')?.dataset?.ownerUserId), storage: () => win.localStorage});
    let selection = selectionCache.read();
    $('chat-mode').value = selection.mode;
    const skillsUI = {open: control('chat-skills-open', 'BUTTON'), dialog: control('chat-skills-dialog', 'DIALOG'), close: control('chat-skills-close', 'BUTTON'), status: control('chat-skills-status', 'P')};
    let timer = null, modelsVersion = 0, listVersion = 0, dialogVersion = 0, historyVersion = 0, contextVersion = 0, paint = null, listLoading = false, creating = false, skillWrites = [];
    const s = {config: null, context: null, tokens: [], models: [], modelsTokenId: null, modelsLoading: false, conversations: [], cursor: null, cursors: new Set(), total: 0, meta: null, events: [], files: [], skills: [], attachments: [], runStates: new Map(), historyStatus: 'idle', historyUntilSeq: null, historyError: null, sending: false, stopping: false, uploading: false, selecting: false, undoing: false, savingSkills: false, skillsUncertain: false, contextLoading: false, uncertain: false, controller: null, pendingRequestId: null, pendingText: null};
    function rememberSelection() { if (!scope.blocked) selectionCache.write(selection); }
    function clearDialog() { dialogVersion++; const dialog = $('chat-dialog'); if (dialog.open) dialog.close(); dialog.replaceChildren(); }
    function closeSkills(restoreFocus = true) {
      clearDialog();
      if (skillsUI.dialog?.open) skillsUI.dialog.close();
      if (restoreFocus && !scope.blocked) skillsUI.open?.focus?.();
    }
    function clearView() {
      win.clearTimeout(timer); timer = null; historyVersion++;
      if (paint !== null) win.cancelAnimationFrame(paint); paint = null;
      Object.assign(s, {meta: null, events: [], files: [], skills: [], attachments: [], runStates: new Map(), historyStatus: 'idle', historyUntilSeq: null, historyError: null, sending: false, stopping: false, uploading: false, selecting: false, undoing: false, savingSkills: false, skillsUncertain: false, uncertain: false, controller: null, pendingRequestId: null, pendingText: null});
      $('chat-candidate-status').textContent = '';
      $('chat-title').textContent = '选择或新建一个聊天'; $('chat-messages').replaceChildren(); $('chat-files-list').replaceChildren();
      $('chat-text').value = ''; $('chat-file-status').textContent = ''; $('chat-upload').value = '';
      $('chat-storage-warning').hidden = true; $('chat-storage-warning').textContent = ''; closeSkills(false);
      if (skillsUI.status) skillsUI.status.textContent = '';
    }
    function clearAll() {
      scope.invalidate(); listVersion++; contextVersion++; modelsVersion++; listLoading = false; creating = false; clearView();
      Object.assign(s, {config: null, context: null, contextLoading: false, tokens: [], models: [], modelsTokenId: null, modelsLoading: false, conversations: [], cursor: null, cursors: new Set(), total: 0});
      selection = C.emptySelection(); $('chat-mode').value = 'chat';
      for (const id of ['chat-list', 'chat-skill-choices', 'chat-skill-order', 'chat-token', 'chat-model']) $(id).replaceChildren();
      $('chat-token').value = ''; $('chat-model').value = '';
      for (const id of ['chat-list-info', 'chat-list-status', 'chat-context-info', 'chat-text-count', 'chat-file-limits']) $(id).textContent = '';
      $('chat-more').hidden = true; status('登录状态已变化，旧内容已清空。请重新载入并登录。', true); updateControls();
    }
    const api = injectedAPI || C.createAPI({scope, onAuth: clearAll});
    const valid = ticket => scope.current(ticket);
    const currentHistory = (ticket, version) => valid(ticket) && version === historyVersion;
    function report(error, ticket, prefix = '') { if (valid(ticket) && error.name !== 'StaleResponse') status(prefix + error.message, true); }
    function projection() {
      const p = C.projectEvents(s.events);
      for (const run of p.runs) {
        const state = s.runStates.get(run.id);
        if (state && !C.TERMINAL.has(run.status)) Object.assign(run, state);
      }
      return p;
    }
    function currentRun() { return s.meta?.active_run_id || projection().runs.findLast(r => !C.TERMINAL.has(r.status))?.id || null; }
    function busy() { return s.sending || s.uploading || s.selecting || s.undoing || s.savingSkills || !!currentRun(); }
    const ownerUserId = () => Number($('chat-root')?.dataset?.ownerUserId);
    function runOptions() {
      // UI defaults do not depend on optional form controls. The API retains its
      // explicit options for old clients and existing multi-candidate history.
      const features = s.config?.features, web = features?.web;
      const webAvailable = features?.web_enabled !== false && (!web || ['http_fetch', 'download_url', 'web_search'].some(name => web[name] === true));
      return {candidate_count: 1, web_enabled: $('chat-mode').value === 'files' && webAvailable, auto_memory: features?.auto_memory !== false, context_strategy: 'adaptive'};
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
    function skillsWritable() {
      return !!s.meta && !!s.context && !scope.blocked && !busy() && !s.contextLoading && !s.uncertain && !s.skillsUncertain && historyReady() && s.meta.storage_state !== 'corrupt';
    }
    function skillsStatus(text, error = false) {
      if (skillsUI.status) { skillsUI.status.textContent = text; skillsUI.status.classList.toggle('error', error); }
    }
    function updateControls() {
      const disabled = scope.blocked, active = busy() || creating, writable = !!s.meta && s.meta.storage_state !== 'corrupt' && !disabled;
      $('chat-new').disabled = disabled || creating || s.savingSkills;
      $('chat-list-refresh').disabled = disabled || listLoading; $('chat-more').disabled = disabled || listLoading;
      $('chat-selection').disabled = disabled || active;
      // Viewing the library is allowed without a selected chat or while a run is active.
      $('chat-skills').disabled = disabled;
      if (skillsUI.open) { skillsUI.open.disabled = disabled; skillsUI.open.textContent = s.skills.length ? `Skills · ${s.skills.length}` : 'Skills'; }
      const skillEditable = skillsWritable();
      for (const node of skillWrites) node.disabled = !skillEditable || node.dataset.skillLimit === 'true';
      if (skillsUI.dialog) skillsUI.dialog.setAttribute('aria-busy', String(s.savingSkills));
      $('chat-token').disabled = disabled || active || !s.tokens.length;
      const catalogCurrent = s.modelsTokenId === Number($('chat-token').value);
      $('chat-model-load').disabled = disabled || active || s.modelsLoading || !$('chat-token').value;
      $('chat-model').disabled = disabled || active || s.modelsLoading || !catalogCurrent || !s.models.length;
      $('chat-context-refresh').disabled = disabled || active || s.contextLoading;
      $('chat-text').disabled = !writable || active || s.uncertain;
      const model = catalogCurrent && !s.modelsLoading ? s.models.find(m => m.id === $('chat-model').value) : null, token = s.tokens.find(t => t.id === Number($('chat-token').value));
      updateCandidateStatus();
      if (!disabled && s.config && !adaptiveAvailable()) status('当前服务暂不支持自适应上下文，请联系维护者。', true);
      $('chat-send').disabled = !writable || !historyReady() || active || s.uncertain || s.skillsUncertain || s.contextLoading || unavailableSkills().length > 0 || C.needsCandidateSelection(projection().runs) || !adaptiveAvailable() || !s.config || !s.context || !token?.usable || !model?.chat_enabled || $('chat-mode').value === 'files' && !model.tools_enabled;
      $('chat-stop').disabled = disabled || s.stopping || !currentRun();
      $('chat-recover').disabled = disabled || !scope.cid || s.sending || s.stopping || s.selecting || s.undoing || s.savingSkills;
      $('chat-rename').disabled = !writable || active; $('chat-delete').disabled = !writable || active;
      const uploadHint = disabled ? '请重新登录后上传。' : !s.config ? '正在读取上传配置…' : creating ? '正在新建聊天…' : active ? '当前聊天正在处理，请稍后上传。' : s.meta?.storage_state === 'corrupt' ? '当前聊天存储待修复，暂不能上传。' : scope.cid && !s.meta ? '请先刷新当前聊天状态。' : !scope.cid ? '选择文件后自动新建聊天。' : '';
      $('chat-upload').disabled = disabled || !s.config || active || s.meta?.storage_state === 'corrupt' || !!scope.cid && !s.meta;
      $('chat-upload').title = uploadHint;
      $('chat-upload-hint').textContent = uploadHint; $('chat-upload-hint').hidden = !uploadHint;
      $('chat-skills-clear').disabled = !skillsWritable() || !s.skills.length;
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
      const desired = selection.model, mode = $('chat-mode').value;
      $('chat-model').replaceChildren();
      for (const model of s.models) {
        const enabled = model.chat_enabled && (mode !== 'files' || model.tools_enabled);
        const option = C.el(doc, 'option', `${model.id}${enabled ? '' : `（${model.reason || (mode === 'files' ? '未确认工具能力' : '协议未确认')}）`}`);
        option.value = model.id; option.disabled = !enabled; $('chat-model').append(option);
      }
      const selected = s.models.find(m => (!desired || m.id === desired) && m.chat_enabled && (mode !== 'files' || m.tools_enabled));
      if (!selected) { const option = C.el(doc, 'option', s.modelsLoading ? '正在载入模型…' : s.models.length ? '请选择可用模型' : '选择 KEY 后载入模型目录'); option.value = ''; $('chat-model').append(option); }
      $('chat-model').value = selected?.id || '';
      const noModel = '此模式暂无可用模型，请更换模式或模型。', missingModel = '原选模型已不可用，请重新选择。';
      if (selected && s.modelsTokenId === selection.token_id) { selection.model = selected.id; rememberSelection(); }
      if (s.models.length && !selected) status(s.models.some(m => m.chat_enabled && (mode !== 'files' || m.tools_enabled)) ? missingModel : noModel, true);
      else if ([noModel, missingModel].includes($('chat-status').textContent)) status('');
      updateControls();
    }
    async function loadModels() {
      const tokenId = Number($('chat-token').value);
      if (scope.blocked || s.modelsLoading || !s.tokens.some(t => t.id === tokenId && t.usable)) return;
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
    async function showSkill(id) {
      if (!s.context || scope.blocked || !control('chat-dialog', 'DIALOG')) return;
      const ticket = scope.ticket(), revision = s.context.context_revision, version = ++dialogVersion;
      try {
        const value = await api.skill(id, revision, {ticket}); if (!valid(ticket) || version !== dialogVersion || s.context?.context_revision !== revision) return;
        if (value?.skill?.id !== id || typeof value.skill.content !== 'string') throw new Error('Skill 正文响应无效。');
        C.showDialog(doc, `${value.skill.name} · v${value.skill.revision}`, [C.el(doc, 'p', value.skill.description), C.el(doc, 'pre', value.skill.content)]);
      } catch (error) { if (valid(ticket)) { report(error, ticket); skillsStatus(`无法查看 Skill：${error.message}`, true); } }
    }
    function renderSkills() {
      const context = s.context, limit = Math.min(3, context?.limits?.skills_per_run ?? 3);
      $('chat-context-info').textContent = context ? `个人设置 v${context.context_revision} · 当前聊天已载入 ${s.skills.length}/${limit}` : '个人设置未载入；发送已禁用。';
      $('chat-skill-choices').replaceChildren(); $('chat-skill-order').replaceChildren();
      skillWrites = [];
      if (!s.meta) $('chat-skill-order').append(C.el(doc, 'li', '请先选择或新建聊天，再载入 Skills。', 'hint'));
      else if (!s.skills.length) $('chat-skill-order').append(C.el(doc, 'li', '尚未载入 Skills。', 'hint'));
      if (!context?.skills?.length) $('chat-skill-choices').append(C.el(doc, 'p', context ? '个人库暂无 Skills，可前往管理库添加。' : '请刷新个人设置后查看 Skills 库。', 'hint'));
      for (const skill of context?.skills || []) {
        const row = C.el(doc, 'div', null, 'chat-skill-row'), loaded = s.skills.includes(skill.id);
        const label = C.el(doc, 'div', `${skill.name} · v${skill.revision}${loaded ? ' · 已载入' : ''}`, 'chat-skill-description');
        const toggle = C.button(doc, loaded ? '移除' : '载入', async () => {
          if (!skillsWritable()) { skillsStatus(s.meta ? '请先只读刷新并确认聊天状态，再修改已载入 Skills。' : '请先选择或新建聊天，再载入 Skills。', true); return; }
          if (!loaded && s.skills.length >= limit) { skillsStatus(`当前聊天最多载入 ${limit} 个 Skill，请先移除一个。`, true); return; }
          await saveSkills(loaded ? s.skills.filter(id => id !== skill.id) : [...s.skills, skill.id]);
        }, 'button-small');
        toggle.setAttribute('aria-label', `${loaded ? '移除' : '载入'} ${skill.name}`);
        skillWrites.push(toggle);
        row.append(label, C.button(doc, '查看正文', () => showSkill(skill.id), 'button-small'), toggle); $('chat-skill-choices').append(row);
      }
      for (const [index, id] of s.skills.entries()) {
        const skill = context?.skills?.find(skill => skill.id === id);
        const item = C.el(doc, 'li', `${skill?.name || `${id}（不可用：已从个人库删除或尚未读取）`} `);
        const move = delta => { const ids = [...s.skills]; [ids[index], ids[index + delta]] = [ids[index + delta], ids[index]]; return saveSkills(ids); };
        const up = C.button(doc, '上移', () => move(-1), 'button-small'), down = C.button(doc, '下移', () => move(1), 'button-small');
        up.dataset.skillLimit = String(index === 0); down.dataset.skillLimit = String(index === s.skills.length - 1);
        const remove = C.button(doc, '移除', () => saveSkills(s.skills.filter(value => value !== id)), 'button-small');
        skillWrites.push(up, down, remove);
        item.append(up, down, remove); $('chat-skill-order').append(item);
      }
      if (unavailableSkills().length) $('chat-skill-order').append(C.el(doc, 'li', '存在不可用 Skill，发送已禁用；请手动移除，或刷新个人设置后核对。', 'warning'));
      updateControls();
    }
    function openSkills() {
      if (scope.blocked || !skillsUI.dialog || typeof skillsUI.dialog.showModal !== 'function') return;
      renderSkills();
      if (!s.meta) skillsStatus('可查看个人库。请先选择或新建聊天，再载入 Skills。');
      else if (s.skillsUncertain) skillsStatus('保存结果尚未核对，请先只读刷新聊天状态。', true);
      if (!skillsUI.dialog.open) skillsUI.dialog.showModal();
    }
    async function saveSkills(ids) {
      if (!skillsWritable()) return;
      const ticket = scope.ticket(), cid = scope.cid, expectedRevision = s.meta.revision, contextRevision = s.context.context_revision;
      const limit = Math.min(3, s.context.limits.skills_per_run);
      if (!Array.isArray(ids) || ids.length > limit || new Set(ids).size !== ids.length || ids.some(id => !C.isUUID(id))) { skillsStatus('载入 Skills 参数无效或超限。', true); return; }
      if (ids.length === s.skills.length && ids.every((id, i) => id === s.skills[i])) return;
      if (typeof api.setSkills !== 'function') { skillsStatus('聊天 Skills 保存接口尚未可用，未修改载入状态。', true); return; }
      s.savingSkills = true; s.skillsUncertain = true; updateControls(); skillsStatus('正在保存当前聊天 Skills…');
      let failed = false;
      try {
        const value = await api.setSkills(cid, [...ids], expectedRevision, contextRevision, {ticket}); if (!valid(ticket)) return;
        const meta = value?.conversation, saved = loadedIDs(meta);
        if (meta?.id !== cid || !Number.isSafeInteger(meta.revision) || meta.revision < expectedRevision || saved.length !== ids.length || saved.some((id, i) => id !== ids[i])) throw new Error('保存响应不一致，需要只读核对。');
        s.meta = meta; restoreSkills(meta);
      } catch (error) {
        if (!valid(ticket)) return;
        failed = true; skillsStatus(`保存未确认：${error.message} 未自动重试，正在只读核对服务器状态。`, true);
      } finally {
        if (valid(ticket)) {
          // The PATCH advances the history high-water mark. Catch it up with GET,
          // keeping writes/send locked even when the response was lost or conflicted.
          await recover(ticket);
          if (valid(ticket)) {
            s.savingSkills = false; renderSkills();
            const changedAgain = s.skills.length !== ids.length || s.skills.some((id, i) => id !== ids[i]);
            const text = s.skillsUncertain ? 'Skills 保存结果仍未核对；发送与修改已禁用，请只读刷新。' : failed ? '已只读恢复服务器当前已载入 Skills；未自动重试或覆盖其他页面的设置，请核对。' : changedAgain ? '保存后聊天设置又有变化；已恢复服务器当前已载入 Skills，请核对。' : '已保存到当前聊天，持续生效直到手动移除。';
            skillsStatus(text, failed || s.skillsUncertain || changedAgain); status(text, failed || s.skillsUncertain || changedAgain);
          }
        }
      }
    }
    async function loadContext({silent = false} = {}) {
      const ticket = scope.ticket(silent), version = ++contextVersion;
      s.contextLoading = true; updateControls(); clearDialog();
      try {
        const value = await api.context({ticket}); if (!valid(ticket) || version !== contextVersion) return;
        if (!s.context || value.context_revision >= s.context.context_revision) s.context = value;
        renderSkills();
        if (!silent) { const text = '已刷新个人设置；当前聊天已载入 Skills 保持不变。'; status(text); skillsStatus(text); }
      } catch (error) { if (version === contextVersion) report(error, ticket, '个人设置未载入：'); }
      finally { if (version === contextVersion) { s.contextLoading = false; if (valid(ticket)) renderSkills(); } }
    }
    function allFiles() { return [...new Map([...s.files, ...projection().files].map(file => [file.id, file])).values()]; }
    function renderFiles() {
      $('chat-files-list').replaceChildren();
      for (const file of allFiles()) {
        const item = C.el(doc, 'section', null, 'chat-file'); item.append(C.el(doc, 'strong', file.name), C.el(doc, 'p', `${file.kind === 'upload' ? '上传原件 · 只读' : `输出 · v${file.version}`} · ${file.size_bytes} 字节`, 'hint'));
        const row = C.el(doc, 'div', null, 'chat-skill-row');
        row.append(C.button(doc, '下载', async () => {
          const ticket = scope.ticket(); try { await C.downloadFile({api, scope, cid: scope.cid, file, doc, ticket}); } catch (error) { report(error, ticket, '下载失败：'); }
        }, 'button-small'));
        const input = doc.createElement('input'); input.type = 'checkbox'; input.checked = s.attachments.includes(file.id); input.disabled = !s.config || busy() || $('chat-mode').value !== 'files';
        input.addEventListener('change', () => {
          if (input.checked && s.attachments.length >= s.config.limits.attachments_per_turn) { input.checked = false; status('本轮引用附件数量已达上限。', true); return; }
          s.attachments = input.checked ? [...s.attachments, file.id] : s.attachments.filter(id => id !== file.id);
        });
        const label = C.el(doc, 'label'); label.append(input, doc.createTextNode('本轮引用')); row.append(label); item.append(row); $('chat-files-list').append(item);
      }
      if (!allFiles().length) $('chat-files-list').append(C.el(doc, 'p', '当前聊天还没有文件。', 'hint'));
    }
    async function showSnapshot(rid) {
      const ticket = scope.ticket(), cid = scope.cid, version = ++dialogVersion;
      try {
        const value = await api.snapshot(cid, rid, {ticket}); if (!valid(ticket) || version !== dialogVersion) return;
        const snapshot = value.context_snapshot;
        if (snapshot.conversation_id !== cid || snapshot.run_id !== rid) throw new Error('上下文快照归属不一致。');
        C.showDialog(doc, '历史运行的真实上下文', C.snapshotNodes(doc, snapshot));
      } catch (error) { report(error, ticket, '无法读取当时快照，不会用当前设置替代：'); }
    }
    function renderCurrent() {
      const nearBottom = win.innerHeight + win.scrollY >= doc.documentElement.scrollHeight - 140;
      const disabled = scope.blocked || busy() || !historyReady() || s.uncertain || s.meta?.storage_state === 'corrupt';
      C.renderRuns(doc, $('chat-messages'), projection().runs, showSnapshot, {onSelectCandidate: selectCandidate, selectionDisabled: disabled, ownerUserId: ownerUserId(), onUndoMemory: undoMemory, memoryDisabled: disabled}); renderFiles(); updateControls();
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
            if ($('chat-text').value === s.pendingText) { s.attachments = []; $('chat-text').value = ''; }
            s.pendingRequestId = null; s.pendingText = null; renderSkills();
          }
        }
        if (event.type === 'run.terminal' && s.meta?.active_run_id === event.run_id) { s.meta.active_run_id = null; s.meta.last_run_status = event.data.status; }
      }
      if (skillsChanged) renderSkills();
      if (immediate) renderCurrent();
      else if (paint === null) { const ticket = scope.ticket(); paint = win.requestAnimationFrame(() => { paint = null; if (valid(ticket)) renderCurrent(); }); }
    }
    async function loadEvents(ticket, version) {
      let after = s.events.at(-1)?.seq || 0, until;
      while (true) {
        const page = await api.events(scope.cid, after, until, s.config?.limits.event_page_max || 200, {ticket}); if (!currentHistory(ticket, version)) return;
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
      if (valid(ticket) && currentRun() && !s.sending) timer = win.setTimeout(() => recover(ticket), (s.config?.limits.poll_interval || 2) * 1000);
    }
    async function recover(ticket = scope.ticket()) {
      if (!scope.cid || !valid(ticket)) return;
      // Scope tickets reject old chats/accounts; this generation also rejects
      // a slower refresh of the same chat after a newer refresh has started.
      const version = ++historyVersion, checkingSkills = s.skillsUncertain;
      Object.assign(s, {historyStatus: 'loading', historyUntilSeq: null, historyError: null});
      win.clearTimeout(timer); timer = null; updateControls();
      try {
        const value = await api.conversation(scope.cid, {ticket}); if (!currentHistory(ticket, version)) return;
        if (value?.conversation?.id !== scope.cid) throw new Error('聊天状态响应归属无效。');
        restoreSkills(value.conversation); s.meta = value.conversation; renderSkills();
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
        const files = await api.files(scope.cid, {ticket}); if (!currentHistory(ticket, version)) return;
        s.files = files.files;
        const contextRevision = projection().runs.reduce((revision, run) => Math.max(revision, C.ownerMemoryAudit(run, ownerUserId())?.after.context_revision || 0, Number.isSafeInteger(run.memory_reverted_context_revision) ? run.memory_reverted_context_revision : 0), 0);
        if (s.context && s.context.context_revision < contextRevision) {
          const context = await api.context({ticket}); if (!currentHistory(ticket, version)) return;
          s.context = context; renderSkills();
        }
        s.historyStatus = 'ready';
        if (!historyReady()) throw new Error('历史事件未达到已提交高水位，请只读刷新。');
        s.uncertain = false; s.skillsUncertain = false; renderSkills(); renderCurrent(); updateListItem(s.meta);
        if (checkingSkills) skillsStatus('已只读核对服务器当前已载入 Skills，请核对后继续。');
        if (!s.sending) {
          const last = projection().runs.at(-1);
          status(currentRun() ? '正在只读查询运行状态，不会继续或重发生成。' : last?.uncertain ? '历史已恢复。该轮结果不确定，可能已计费；请核对 New API 用量。' : '已完整恢复已提交历史；没有重新调用模型。');
        }
      } catch (error) {
        if (currentHistory(ticket, version)) {
          s.historyStatus = 'failed'; s.historyError = error.message;
          report(error, ticket, '只读恢复未完成，现有内容保留；发送已禁用：');
        }
      }
      finally { if (currentHistory(ticket, version)) { updateControls(); schedulePoll(ticket); } }
    }
    async function openConversation(cid, push = true) {
      if (!C.isUUID(cid) || scope.blocked) return;
      const ticket = scope.switch(cid); clearView(); status('正在恢复完整历史…'); renderList(); renderModels(); renderSkills();
      if (push) { const url = new URL(win.location.href); url.search = new URLSearchParams({conversation: cid}).toString(); win.history.pushState(null, '', url.pathname + url.search); }
      try { await recover(ticket); }
      finally { if (valid(ticket)) { renderCurrent(); renderSkills(); } }
    }
    async function newConversation() {
      if (creating || scope.blocked || s.savingSkills) return;
      const ticket = scope.ticket(); creating = true; updateControls();
      try {
        const value = await api.create(C.uuid(win.crypto), {ticket}); if (!valid(ticket)) return;
        // A fresh first page includes this chat; old cursor snapshots never hide older rows.
        await openConversation(value.conversation.id);
        const destination = scope.ticket(); await loadList();
        return valid(destination) && scope.cid === value.conversation.id && s.meta?.id === value.conversation.id ? {cid: scope.cid, ticket: destination} : null;
      } catch (error) { report(error, ticket, '创建结果待确认，请重新读取列表；未自动重试。'); }
      finally { creating = false; updateControls(); }
    }
    async function send(event) {
      event.preventDefault(); if (busy() || s.uncertain || scope.blocked || s.contextLoading) return;
      if (s.skillsUncertain) { status('Skills 保存结果尚未核对，请只读刷新后再发送。', true); return; }
      if (unavailableSkills().length) { const text = '当前聊天存在不可用 Skill，请打开 Skills 手动移除，或刷新个人设置后核对。'; status(text, true); skillsStatus(text, true); return; }
      if (C.needsCandidateSelection(projection().runs)) { status('请先选择最新完成轮次的一个候选，再继续发送。', true); return; }
      if (!historyReady()) { status(s.historyStatus === 'loading' ? '历史仍在加载，完整恢复后才能发送。' : '历史尚未完整恢复，请只读刷新后再发送。', true); return; }
      if (s.modelsLoading || s.modelsTokenId !== Number($('chat-token').value)) { status('请先载入当前 KEY 的模型目录。', true); return; }
      const ticket = scope.ticket(), cid = scope.cid; let body;
      try {
        body = C.buildRunRequest({meta: s.meta, token: s.tokens.find(t => t.id === Number($('chat-token').value)), model: s.models.find(m => m.id === $('chat-model').value), mode: $('chat-mode').value, text: $('chat-text').value, attachments: s.attachments, skillIds: s.skills, context: s.context, config: s.config, requestId: C.uuid(win.crypto), options: runOptions()});
      } catch (error) { report(error, ticket); return; }
      s.sending = true; s.pendingRequestId = body.client_request_id; s.pendingText = body.text; s.controller = new AbortController(); renderSkills(); renderFiles(); updateControls(); status('已提交一次请求；正在等待持久化接受与回答，不会自动重发。');
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
        if (result.replayed) { s.pendingRequestId = null; s.pendingText = null; $('chat-text').value = ''; notice = '命中原有运行，正在 GET 恢复；没有再次派发。'; }
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
            renderSkills(); renderCurrent();
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
        s.context = context; renderSkills();
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
            s.context = context; renderSkills();
          } catch (error) { if (valid(ticket)) { s.context = null; renderSkills(); failed = true; notice += ` 最新个人设置未读取：${error.message}`; } }
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
    async function upload() {
      const file = $('chat-upload').files[0];
      let ticket = scope.ticket(), cid = scope.cid;
      if (!file || busy() || creating || scope.blocked || s.meta?.storage_state === 'corrupt') return;
      try { C.checkUpload(file, s.config); } catch (error) { report(error, ticket); $('chat-upload').value = ''; return; }
      if (!cid) {
        const created = await newConversation();
        if (!created) return;
        ({ticket, cid} = created);
      }
      if (!valid(ticket) || scope.cid !== cid || !s.meta || s.meta.storage_state === 'corrupt' || busy()) return;
      s.uploading = true; updateControls(); $('chat-file-status').textContent = '正在上传一个文件…';
      try {
        await api.upload(cid, file, C.uuid(win.crypto), {ticket}); if (!valid(ticket)) return;
        $('chat-file-status').textContent = '文件已保存；发送前可显式引用。';
      } catch (error) { if (valid(ticket)) $('chat-file-status').textContent = `${error.message} 未自动重传，请通过文件列表确认。`; }
      finally { if (valid(ticket)) { s.uploading = false; $('chat-upload').value = ''; await recover(ticket); updateControls(); } }
    }
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
        scope.switch(null); clearView(); renderSkills(); renderModels(); updateControls();
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
      const model = s.modelsTokenId === Number($('chat-token').value) && s.models.find(m => m.id === $('chat-model').value && m.chat_enabled && ($('chat-mode').value !== 'files' || m.tools_enabled));
      if (model) { selection.model = model.id; rememberSelection(); renderModels(); } else updateControls();
    });
    $('chat-mode').addEventListener('change', () => {
      if (scope.blocked || busy() || !['chat', 'files'].includes($('chat-mode').value)) return;
      selection.mode = $('chat-mode').value; rememberSelection(); s.attachments = []; renderModels(); renderFiles();
    });
    $('chat-context-refresh').addEventListener('click', () => loadContext());
    $('chat-skills-clear').addEventListener('click', () => saveSkills([]));
    skillsUI.open?.addEventListener('click', openSkills);
    skillsUI.close?.addEventListener('click', () => closeSkills());
    skillsUI.dialog?.addEventListener('cancel', event => { event.preventDefault(); closeSkills(); });
    skillsUI.dialog?.addEventListener('close', () => { clearDialog(); });
    $('chat-text').addEventListener('input', updateControls); $('chat-compose').addEventListener('submit', send);
    $('chat-stop').addEventListener('click', stop); $('chat-recover').addEventListener('click', () => recover());
    $('chat-upload').addEventListener('change', upload); $('chat-rename').addEventListener('click', rename); $('chat-delete').addEventListener('click', remove);
    $('chat-dialog').addEventListener('cancel', () => { win.setTimeout(clearDialog, 0); });
    $('chat-dialog').addEventListener('close', () => { if (skillsUI.dialog?.open) skillsUI.close?.focus?.(); });
    win.addEventListener('cockpit-auth-changed', clearAll); win.addEventListener('pagehide', clearAll);
    win.addEventListener('pageshow', event => { if (event.persisted) win.location.reload(); });
    win.addEventListener('popstate', () => { const cid = new URL(win.location.href).searchParams.get('conversation'); if (C.isUUID(cid)) openConversation(cid, false); else { scope.switch(null); clearView(); renderModels(); renderSkills(); renderList(); } });
    async function init() {
      const ticket = scope.ticket(true); updateControls();
      const config = api.config({ticket}).then(value => {
        if (!valid(ticket)) return; s.config = value; $('chat-upload').accept = value.allowed_file_extensions.join(',');
        $('chat-file-limits').textContent = `单文件不超过 ${C.fileSizeLabel(value.limits.upload_file_bytes)}；允许 ${value.allowed_file_extensions.join(' ')}`; updateControls();
      }).catch(error => report(error, ticket, '限制配置未载入，发送/上传禁用：'));
      const tokens = api.tokens({ticket}).then(async value => { if (valid(ticket)) { s.tokens = value.tokens; if (renderTokens()) await loadModels(); } }).catch(error => report(error, ticket, 'KEY 摘要未读取；历史仍可查看：'));
      const cid = new URL(win.location.href).searchParams.get('conversation');
      const history = C.isUUID(cid) ? openConversation(cid, false) : Promise.resolve();
      await Promise.all([config, tokens, loadContext({silent: true}), loadList(), history]);
      if (valid(ticket)) updateControls();
    }
    const ready = init();
    return {ready, state: s, scope, openConversation, loadList, recover, send, stop, selectCandidate, undoMemory, clearAll};
  }
  return {createChatApp};
});
