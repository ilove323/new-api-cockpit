(function (root, factory) {
  'use strict';
  const value = factory(typeof module === 'object' && module.exports
    ? {...require('./state.js'), ...require('./api.js'), ...require('./render.js'), ...require('./files.js')} : root.CockpitChat);
  if (typeof module === 'object' && module.exports) module.exports = value;
  else { Object.assign(root.CockpitChat, value); if (root.document.body.dataset.chatPage === 'settings') value.createSettingsApp({window: root, document: root.document}); }
})(typeof window === 'object' ? window : globalThis, function (C) {
  'use strict';
  function parseSkill(value, limits) {
    const content = C.normalizeText(value), lines = content.split('\n');
    if (C.bytes(content) > limits.skill_file_bytes) throw new Error('SKILL.md 超过单文件字节上限。');
    if (lines[0] !== '---' || lines[3] !== '---') throw new Error('第 1、4 行必须恰为 ---，中间仅 name、description 两行。');
    const name = /^name: ([a-z0-9]+(?:-[a-z0-9]+)*)$/.exec(lines[1] || '')?.[1];
    if (!name || name.length > limits.skill_name_chars) throw new Error('第 2 行 name 仅允许小写字母、数字及单个连字符，且不能超长。');
    const raw = /^description: (".*")$/.exec(lines[2] || '')?.[1]; let description;
    try { description = JSON.parse(raw); } catch { throw new Error('第 3 行 description 必须是单行 JSON 双引号字符串。'); }
    if (typeof description !== 'string' || !description.trim() || [...description].length > limits.skill_description_chars || /[\u0000-\u001f\u007f]/.test(description)) throw new Error('description 为空、超长或含控制字符。');
    C.normalizeText(description);
    const body = lines.slice(4).join('\n'); if (!body.trim()) throw new Error('Skill 正文不能为空。');
    return {name, description, body, content};
  }
  function makeSkill(name, description, body, limits) { return parseSkill(`---\nname: ${name}\ndescription: ${JSON.stringify(description)}\n---\n${body}`, limits).content; }
  function createSettingsApp({window: win, document: doc, api: injectedAPI, scope = new C.Scope()}) {
    const $ = id => doc.getElementById(id), status = (text, error = false) => { $('settings-status').textContent = text; $('settings-status').classList.toggle('error', error); };
    const s = {context: null, kind: 'profile', id: 'MEMORY.md', baseline: null, editContextRevision: 0, draft: '', busy: false, pending: null, skillTab: false};
    const api = injectedAPI || C.createAPI({scope, onAuth: clearAll});
    const dirty = () => !!s.baseline && s.draft !== s.baseline.content;
    function limit() { return s.kind === 'profile' ? s.context?.limits.profile_bytes[s.id] : s.context?.limits.skill_file_bytes; }
    function update() {
      const disabled = scope.blocked || s.busy || !s.context;
      $('settings-content').disabled = disabled || !s.baseline;
      $('settings-save').disabled = disabled || !s.baseline || !dirty() || !!s.pending;
      $('settings-discard').disabled = disabled || !s.baseline;
      $('settings-clear').disabled = disabled || s.kind !== 'profile' || !s.baseline?.content && !s.draft;
      $('settings-delete-skill').hidden = s.kind !== 'skill' || !s.id;
      $('settings-delete-skill').disabled = disabled || !!s.pending;
      $('settings-clear').hidden = s.kind !== 'profile';
      $('settings-refresh').disabled = disabled;
      for (const control of doc.querySelectorAll('[data-profile], #settings-new-skill, #settings-skills-tab, #settings-import-skill, #settings-skill-header, #settings-skill-name, #settings-skill-description')) control.disabled = disabled;
      $('settings-rebase').disabled = disabled || !s.pending || !!s.pending.unavailable;
      $('settings-skill-fields').hidden = s.kind !== 'skill';
      $('settings-document-title').textContent = s.kind === 'profile' ? s.id : s.id ? `SKILL.md · ${s.baseline?.name || ''}` : '新建单文件 SKILL.md';
      $('settings-document-info').textContent = s.baseline ? `文档 v${s.baseline.revision || 0} · 编辑基线：上下文 v${s.editContextRevision} · ${s.baseline.updated_at || '尚未保存'}${dirty() ? ' · 有未保存草稿' : ''}` : '';
      $('settings-byte-count').textContent = s.context ? `${C.bytes(s.draft)} / ${limit() || 0} UTF-8 字节${C.bytes(s.draft) > limit() ? '（超限）' : ''}` : '';
      $('settings-context-info').textContent = s.context ? `个人上下文 v${s.context.context_revision} · 最后保存 ${s.context.updated_at || '尚未保存'}` : '';
      if (!$('settings-preview').hidden) $('settings-preview').replaceChildren(C.markdown(doc, s.draft));
      for (const node of doc.querySelectorAll('[data-profile]')) node.setAttribute('aria-pressed', String(s.kind === 'profile' && node.dataset.profile === s.id));
      $('settings-skills-tab').setAttribute('aria-pressed', String(s.skillTab));
      $('settings-skills-panel').hidden = !s.skillTab;
    }
    function showList() {
      $('settings-skill-list').replaceChildren(...(s.context?.skills || []).map(skill => C.button(doc, `${skill.name} · v${skill.revision}\n${skill.description}\n${skill.updated_at || ''}`, () => selectSkill(skill.id))));
      $('settings-skill-count').textContent = s.context ? `${s.context.skills.length}/${s.context.limits.skills_per_user} 个（完整列表）` : '';
    }
    function syncFields() {
      if (s.kind !== 'skill') { $('settings-skill-name').value = ''; $('settings-skill-description').value = ''; return; }
      try { const parsed = parseSkill(s.draft, s.context.limits); $('settings-skill-name').value = parsed.name; $('settings-skill-description').value = parsed.description; }
      catch { $('settings-skill-name').value = ''; $('settings-skill-description').value = ''; }
    }
    function install(kind, id, document, revision) {
      s.kind = kind; s.id = id; s.baseline = document; s.editContextRevision = revision; s.draft = document.content; s.pending = null;
      $('settings-content').value = s.draft; $('settings-conflict').hidden = true; $('settings-latest-content').textContent = ''; $('settings-preview').replaceChildren(); syncFields(); update();
    }
    function mayLeave() { return !s.busy && (!dirty() || win.confirm('当前草稿尚未保存，确定放弃并切换？草稿不会保存到浏览器。')); }
    function selectProfile(name, ask = true) {
      if (!s.context || ask && !mayLeave()) return;
      scope.switch(null); s.skillTab = false;
      const profile = s.context.profiles.find(p => p.name === name);
      if (!profile) { status('个人设置返回缺少文档，不能退回空内容。', true); return; }
      install('profile', name, profile, s.context.context_revision);
    }
    async function selectSkill(id, ask = true) {
      if (!s.context || ask && !mayLeave()) return;
      const ticket = scope.switch(null), revision = s.context.context_revision; s.skillTab = true; s.busy = true; update();
      try {
        const value = await api.skill(id, revision, {ticket}); if (!scope.current(ticket)) return;
        install('skill', id, value.skill, value.context_revision); status('单文件 Skill 原文已载入；不会执行或自动加载到聊天。');
      } catch (error) { if (scope.current(ticket)) status(`Skill 未载入：${error.message}。请读取最新列表后重试。`, true); }
      finally { if (scope.current(ticket)) { s.busy = false; update(); } }
    }
    function newSkill(ask = true) {
      if (!s.context || ask && !mayLeave()) return;
      scope.switch(null); s.skillTab = true;
      install('skill', null, {revision: 0, content: '', updated_at: null}, s.context.context_revision);
      s.draft = '---\nname: my-skill\ndescription: "描述本模板的用途"\n---\n请在此填写本模板的具体步骤与约束。\n'; $('settings-content').value = s.draft; syncFields(); update();
    }
    async function refresh(ask = true) {
      if (ask && dirty() && !s.busy) {
        const ticket = scope.ticket(); s.busy = true; update();
        try { await compareLatest(ticket, s.draft); if (scope.current(ticket)) status('已只读获取最新版，当前草稿保留；请核对比较后主动采用新版版本号。'); }
        catch (error) { if (scope.current(ticket)) status(`无法读取最新版；草稿仍保留。${error.message}`, true); }
        finally { if (scope.current(ticket)) { s.busy = false; update(); } }
        return;
      }
      if (ask && !mayLeave()) return;
      const ticket = scope.switch(null); s.busy = true; update();
      try {
        const value = await api.context({ticket}); if (!scope.current(ticket)) return;
        s.context = value; showList();
        if (s.kind === 'skill' && s.id && value.skills.some(skill => skill.id === s.id)) {
          const result = await api.skill(s.id, value.context_revision, {ticket}); if (!scope.current(ticket)) return;
          install('skill', s.id, result.skill, result.context_revision);
        } else if (s.kind === 'skill') newSkill(false);
        else selectProfile(s.id, false);
        status('已读取本人最新设置，未写入或调用模型。');
      } catch (error) { if (scope.current(ticket)) status(`无法读取个人设置，不代表文档为空：${error.message}`, true); }
      finally { s.busy = false; if (!scope.blocked) update(); }
    }
    function adoptMutation(value) {
      s.context.context_revision = value.context_revision;
      if (value.profile) {
        s.context.profiles = s.context.profiles.map(p => p.name === value.profile.name ? value.profile : p);
        s.context.updated_at = value.profile.updated_at; install('profile', value.profile.name, value.profile, value.context_revision);
      } else if (value.skill) {
        const {content: _content, ...summary} = value.skill;
        s.context.skills = [...s.context.skills.filter(skill => skill.id !== value.skill.id), summary].sort((a, b) => a.name < b.name ? -1 : a.name > b.name ? 1 : a.id.localeCompare(b.id));
        s.context.updated_at = value.skill.updated_at; install('skill', value.skill.id, value.skill, value.context_revision);
      }
      showList();
    }
    async function compareLatest(ticket, attempted, unknown = false) {
      const value = await api.context({ticket}); if (!scope.current(ticket)) return;
      let latest;
      if (s.kind === 'profile') latest = value.profiles.find(p => p.name === s.id);
      else {
        let sid = s.id;
        if (!sid) { const name = parseSkill(attempted, value.limits).name; sid = value.skills.find(skill => skill.name === name)?.id; }
        if (sid && value.skills.some(skill => skill.id === sid)) { const result = await api.skill(sid, value.context_revision, {ticket}); if (!scope.current(ticket)) return; latest = result.skill; }
      }
      if (unknown && latest?.content === attempted) {
        s.context = value; install(s.kind, s.kind === 'skill' ? latest.id : s.id, latest, value.context_revision); showList(); status('写入响应未知；已通过 GET 确认服务端当前内容与草稿一致，没有重发写请求。'); return;
      }
      s.pending = {context: value, document: latest || null};
      $('settings-latest-content').textContent = `最新上下文 v${value.context_revision}${latest ? ` / 文档 v${latest.revision}` : ' / 原文档已不存在'}\n\n${latest?.content ?? '（没有该 Skill；采用新版后只能主动创建新 Skill）'}`;
      $('settings-conflict').hidden = false; update();
    }
    async function save() {
      if (s.busy || !s.context || !s.baseline || s.pending || !dirty()) return;
      const ticket = scope.ticket(); let content;
      try {
        content = s.kind === 'skill' ? parseSkill(s.draft, s.context.limits).content : C.normalizeText(s.draft);
        if (C.bytes(content) > limit()) throw new Error('文档超过 UTF-8 字节上限。');
      } catch (error) { status(error.message, true); return; }
      s.busy = true; update(); status('正在提交一次保存；不会自动重试。');
      try {
        const body = {context_revision: s.editContextRevision, ...(s.kind === 'profile' || s.id ? {expected_revision: s.baseline.revision} : {}), content};
        const result = s.kind === 'profile' ? await api.profile(s.id, body, {ticket}) : s.id ? await api.updateSkill(s.id, body, {ticket}) : await api.createSkill(body, {ticket});
        if (!scope.current(ticket)) return;
        adoptMutation(result); status(result.changed === false ? '服务端确认内容未变，版本没有递增。' : '已保存，仅影响之后开始的轮次；旧快照不变。');
      } catch (error) {
        if (!scope.current(ticket)) return;
        status(`${error.message} 草稿已保留，未自动重试。`, true);
        if (!error.status || error.status === 409 || error.status >= 500) {
          try { await compareLatest(ticket, content, !error.status || error.status >= 500); }
          catch (readError) { if (scope.current(ticket)) status(`保存结果尚无法核对，草稿保留；请稍后主动读取最新设置，勿重复点击保存。${readError.message}`, true); s.pending = {unavailable: true}; }
        }
      } finally { if (scope.current(ticket)) { s.busy = false; update(); } }
    }
    async function deleteSkill() {
      if (!s.id || s.kind !== 'skill' || s.busy || !win.confirm('从个人 Skill 库删除？以前聊天的上下文副本不受影响。当前草稿会丢失。')) return;
      const ticket = scope.ticket(), id = s.id; s.busy = true; update();
      try {
        const value = await api.deleteSkill(id, {context_revision: s.editContextRevision, expected_revision: s.baseline.revision, confirm: true}, {ticket}); if (!scope.current(ticket)) return;
        s.context.context_revision = value.context_revision; s.context.skills = s.context.skills.filter(skill => skill.id !== id); showList(); newSkill(false); status('Skill 已从当前库删除；历史运行副本仍可查看。');
      } catch (error) {
        if (!scope.current(ticket)) return;
        status(`删除未确认：${error.message}。未自动重试。`, true);
        try {
          const value = await api.context({ticket}); if (!scope.current(ticket)) return;
          if (!value.skills.some(skill => skill.id === id)) { s.context = value; showList(); newSkill(false); status('已通过 GET 确认该 Skill 不在当前库，未重复删除。'); }
          else await compareLatest(ticket, s.draft);
        } catch { if (scope.current(ticket)) s.pending = {unavailable: true}; }
      } finally { if (!scope.blocked) { s.busy = false; update(); } }
    }
    function clearAll() {
      scope.invalidate(); Object.assign(s, {context: null, baseline: null, draft: '', id: 'MEMORY.md', kind: 'profile', editContextRevision: 0, pending: null, busy: false, skillTab: false});
      for (const id of ['settings-content', 'settings-skill-name', 'settings-skill-description', 'settings-import-skill']) $(id).value = '';
      for (const id of ['settings-preview', 'settings-latest-content', 'settings-skill-list', 'settings-context-info', 'settings-document-info', 'settings-byte-count', 'settings-skill-count']) $(id).replaceChildren();
      $('settings-conflict').hidden = true; $('settings-preview').hidden = true; const dialog = $('chat-dialog'); if (dialog.open) dialog.close(); dialog.replaceChildren();
      status('登录状态已变化，旧个人内容和草稿已清空。请重新载入并登录。', true); update();
    }
    for (const node of doc.querySelectorAll('[data-profile]')) node.addEventListener('click', () => selectProfile(node.dataset.profile));
    $('settings-skills-tab').addEventListener('click', () => { if (mayLeave()) { s.skillTab = true; update(); showList(); } });
    $('settings-new-skill').addEventListener('click', () => newSkill());
    $('settings-refresh').addEventListener('click', () => refresh());
    $('settings-save').addEventListener('click', save);
    $('settings-discard').addEventListener('click', () => { if (!s.busy && s.baseline && (!dirty() || win.confirm('放弃当前未保存草稿？'))) { s.draft = s.baseline.content; $('settings-content').value = s.draft; syncFields(); update(); } });
    $('settings-clear').addEventListener('click', () => { if (!s.busy && s.kind === 'profile' && win.confirm('确认将此文档保存为空？以前聊天的上下文副本不受影响。')) { s.draft = ''; $('settings-content').value = ''; update(); save(); } });
    $('settings-delete-skill').addEventListener('click', deleteSkill);
    $('settings-content').addEventListener('input', () => { s.draft = $('settings-content').value; syncFields(); update(); });
    $('settings-preview-toggle').addEventListener('click', () => { $('settings-preview').hidden = !$('settings-preview').hidden; $('settings-preview-toggle').setAttribute('aria-expanded', String(!$('settings-preview').hidden)); update(); });
    $('settings-copy-draft').addEventListener('click', async () => { const ticket = scope.ticket(); try { await win.navigator.clipboard.writeText(s.draft); if (scope.current(ticket)) status('已复制当前草稿。'); } catch { if (scope.current(ticket)) status('复制不可用，请手动选择原文复制。', true); } });
    $('settings-rebase').addEventListener('click', () => {
      if (!s.pending || s.pending.unavailable || s.busy) return;
      const {context, document} = s.pending; s.context = context; s.editContextRevision = context.context_revision;
      s.baseline = document || {revision: 0, content: '', updated_at: null}; if (!document && s.kind === 'skill') s.id = null;
      s.pending = null; $('settings-conflict').hidden = true; $('settings-latest-content').textContent = ''; showList(); update(); status('已主动采用新版版本号，草稿保留；仍需你再次点击保存。');
    });
    $('settings-skill-header').addEventListener('click', () => {
      try {
        let body = s.draft;
        if (body.startsWith('---\n')) { const end = body.indexOf('\n---\n', 4); if (end >= 0) body = body.slice(end + 5); }
        s.draft = makeSkill($('settings-skill-name').value, $('settings-skill-description').value, body, s.context.limits); $('settings-content').value = s.draft; update();
      } catch (error) { status(error.message, true); }
    });
    $('settings-import-skill').addEventListener('change', async () => {
      const ticket = scope.ticket(), draft = s.draft, file = $('settings-import-skill').files[0]; if (!file || !s.context || s.kind !== 'skill' || s.busy) return;
      try {
        const content = await C.readSkillFile(file, s.context.limits.skill_file_bytes); if (!scope.current(ticket)) return;
        if (s.draft !== draft) { status('读取期间草稿已变化，未用导入文件覆盖草稿，请主动重新选择文件。', true); return; }
        s.draft = parseSkill(content, s.context.limits).content; $('settings-content').value = s.draft; syncFields(); update(); status('SKILL.md 仅载入编辑器，尚未保存。');
      } catch (error) { if (scope.current(ticket)) status(`导入失败：${error.message}`, true); }
      finally { if (scope.current(ticket)) $('settings-import-skill').value = ''; }
    });
    win.addEventListener('beforeunload', event => { if (!scope.blocked && dirty()) { event.preventDefault(); event.returnValue = ''; } });
    win.addEventListener('cockpit-auth-changed', clearAll); win.addEventListener('pagehide', clearAll);
    win.addEventListener('pageshow', event => { if (event.persisted) win.location.reload(); });
    const ready = refresh(false);
    return {ready, state: s, scope, save, refresh, selectProfile, selectSkill, newSkill, clearAll};
  }
  return {parseSkill, makeSkill, createSettingsApp};
});
