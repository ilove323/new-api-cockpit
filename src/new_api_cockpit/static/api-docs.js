/* Guided documentation. Selecting a step only fills the editor; never sends. */
(() => {
  'use strict';
  const byId = id => document.getElementById(id);
  if (!byId('api-docs')) return;
  const client = window.CockpitDocsClient;
  const form = byId('docs-request-form'), fields = byId('docs-request-fields');
  const select = byId('docs-operation'), body = byId('docs-body');
  const mode = byId('docs-auth-mode'), pat = byId('docs-pat');
  let spec, list = [], parameterInputs = [], transport, working = false;
  let authEpoch = 0, quotaPreview = null, keyPreview = null, fileURL = '', referenceReady = false;
  const steps = {
    scopes: ['GET', '/cockpit/api/statistics/scopes'], balance: ['GET', '/cockpit/api/statistics/balance'], alert: ['GET', '/cockpit/api/statistics/alert'],
    users: ['GET', '/cockpit/api/users'], 'quota-preview': ['POST', '/cockpit/api/users/quota/preview'], 'quota-apply': ['POST', '/cockpit/api/users/quota/apply'],
    keys: ['POST', '/cockpit/api/keys/query/grouped'], 'keys-preview': ['POST', '/cockpit/api/keys/groups/preview'], 'keys-apply': ['POST', '/cockpit/api/keys/operations/{operation_id}/apply'],
  };
  const notices = {read: '查询不会发起这项业务修改。', preview: '只做预览；核对响应后再选择执行接口。', write: '会调用真实管理接口。改 KEY 配置前请暂停并发调用；超时先核对，不重复提交。', notify: '会实际检查并可能发送通知，不是只读查询；重复调用可能重复发送。', pat: '读取详情可能补建缺失的 PAT，并记录补建操作。', session: '会话接口仅供正常登录流程使用，文档中不能执行。'};
  const current = () => list[Number(select.value)];
  const status = message => { byId('docs-request-status').textContent = message; };
  const request = () => client.buildRequest(current(), Object.fromEntries(parameterInputs.map(([name, input]) => [name, input.value])), body.value, location.origin);
  function updateExample() {
    try { byId('docs-code').textContent = client.example(request(), byId('docs-code-language').value); }
    catch (error) { byId('docs-code').textContent = error.message; }
  }
  function beijingDates() {
    const parts = Object.fromEntries(new Intl.DateTimeFormat('en-CA', {timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit'}).formatToParts(new Date()).map(part => [part.type, part.value]));
    return {start: parts.year + '-' + parts.month + '-01', end: parts.year + '-' + parts.month + '-' + parts.day};
  }
  function renderOperation() {
    const operation = current();
    byId('docs-operation-description').textContent = operation.description;
    byId('docs-effect').textContent = notices[operation['x-cockpit-effect']] || notices.write;
    byId('docs-send').disabled = operation['x-cockpit-testable'] !== true;
    const parameters = byId('docs-parameters');
    parameters.replaceChildren(); parameterInputs = [];
    for (const param of operation.parameters || []) {
      if (!['query', 'path'].includes(param.in)) continue;
      const label = document.createElement('label'), title = document.createElement('span'), hint = document.createElement('small');
      title.textContent = param.name + (param.required ? ' *' : '') + ' · ' + (param.in === 'path' ? '路径' : '查询');
      let input;
      if (param.schema.enum) {
        input = document.createElement('select');
        for (const value of ['', ...param.schema.enum]) { const option = document.createElement('option'); option.value = value; option.textContent = value || '不填写'; input.append(option); }
      } else {
        input = document.createElement('input'); input.type = 'text';
        input.placeholder = param.schema.type === 'array' ? 'JSON 数组，例如 [10,11] 或 ["","default"]' : String(param.example ?? '');
      }
      input.required = Boolean(param.required); input.name = param.in + ':' + param.name;
      if (param.name === 'start' || param.name === 'end') input.value = beijingDates()[param.name];
      hint.textContent = param.description || '从查询结果取得实际 ID，不要直接使用示例 ID。';
      label.append(title, input, hint); parameters.append(label); parameterInputs.push([input.name, input]);
    }
    const media = operation.requestBody?.content?.['application/json'];
    byId('docs-body-field').hidden = !media;
    body.value = media ? JSON.stringify(media.example || {}, null, 2) : '';
    byId('docs-headers').textContent = Object.entries(operation['x-cockpit-confirm-headers'] || {}).map(([name, value]) => name + ': ' + value).join('；');
    updateExample();
  }
  function setOperation(method, path) {
    const index = list.findIndex(item => item.method === method && item.path === path);
    if (index < 0) return;
    select.value = index; renderOperation();
  }
  function confirm(operation, target) {
    const dialog = byId('docs-confirm');
    byId('docs-confirm-description').textContent = operation.summary + '。' + (notices[operation['x-cockpit-effect']] || notices.write)
      + ' 调试身份：' + (mode.value === 'pat' ? '输入的 PAT 对应管理员' : '当前登录管理员') + '。';
    byId('docs-confirm-target').textContent = target;
    if (operation.path === steps['keys-apply'][1] && keyPreview) byId('docs-confirm-target').textContent += '\n冻结名单共 ' + keyPreview.count + ' 个 KEY；请核对下方预览响应，当前只执行下一组（最多 5 个）。';
    dialog.returnValue = 'cancel';
    return new Promise(resolve => { dialog.addEventListener('close', () => resolve(dialog.returnValue === 'confirm'), {once: true}); dialog.showModal(); });
  }
  function checkPreview(url, init, consume = false) {
    const path = new URL(url).pathname;
    if (path === steps['quota-apply'][1]) {
      if (!quotaPreview || quotaPreview.epoch !== authEpoch || quotaPreview.body !== init.body) throw new Error('请先在本页预览完全一致的用户、操作和金额，再执行。');
      if (consume) quotaPreview = null;
    }
    if (/^\/cockpit\/api\/keys\/operations\/[^/]+\/apply$/.test(path)) {
      if (!keyPreview || keyPreview.epoch !== authEpoch || path !== '/cockpit/api/keys/operations/' + keyPreview.id + '/apply') throw new Error('请先在本页冻结改组名单；只允许继续尚未结束且全部成功的操作。');
      if (consume) keyPreview = null;
    }
  }
  function authChanged() {
    authEpoch++; quotaPreview = null; keyPreview = null;
    byId('docs-pat-field').hidden = mode.value !== 'pat';
    byId('docs-clear-pat').hidden = mode.value !== 'pat';
    if (mode.value !== 'pat') pat.value = '';
  }
  mode.addEventListener('change', authChanged); pat.addEventListener('input', authChanged);
  byId('docs-clear-pat').addEventListener('click', () => { if (working) return; pat.value = ''; authChanged(); status('PAT 已清除。'); });
  select.addEventListener('change', renderOperation);
  fields.addEventListener('input', updateExample); fields.addEventListener('change', updateExample);
  byId('docs-code-language').addEventListener('change', updateExample);
  byId('docs-copy').addEventListener('click', async () => {
    try { const sample = client.example(request(), byId('docs-code-language').value); await navigator.clipboard.writeText(sample); status('示例已复制，PAT 使用环境变量占位。'); }
    catch { status('未能复制，请检查参数后手动选择示例文本。'); }
  });
  for (const button of document.querySelectorAll('[data-docs-step]')) button.addEventListener('click', () => {
    if (working || !spec) return;
    const step = button.dataset.docsStep;
    if (step === 'quota-apply' && (!quotaPreview || quotaPreview.epoch !== authEpoch)) { status('先发送额度预览，核对实际用户与金额。'); return; }
    if (step === 'keys-apply' && (!keyPreview || keyPreview.epoch !== authEpoch)) { status('先发送改组预览并核对名单；失败或结果不明确时先查操作记录。'); return; }
    if (step.endsWith('-apply')) {
      const previous = step === 'quota-apply' ? quotaPreview : keyPreview;
      const previewPath = step === 'quota-apply' ? steps['quota-preview'][1] : steps['keys-preview'][1];
      try {
        if (current()?.path === previewPath && request().init.body !== previous.body) { status('预览参数已修改，请重新发送预览后再执行。'); return; }
      } catch (error) { status(error.message); return; }
    }
    const savedBody = step === 'quota-preview' && current()?.path === steps['quota-apply'][1] ? body.value : null;
    setOperation(...steps[step]);
    if (step === 'quota-apply') body.value = JSON.stringify(JSON.parse(quotaPreview.body), null, 2);
    if (savedBody) body.value = savedBody;
    if (step === 'keys-apply') parameterInputs.find(([name]) => name === 'path:operation_id')[1].value = keyPreview.id;
    updateExample(); status('已填写这一步，请核对参数后点击“发送请求”。');
    form.scrollIntoView({block: 'nearest', behavior: 'smooth'});
  });
  function releaseFile() {
    if (fileURL) URL.revokeObjectURL(fileURL);
    fileURL = ''; byId('docs-file').hidden = true; byId('docs-file').removeAttribute('href');
  }
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (working || !spec || !form.reportValidity()) return;
    let outgoing, previousKeyPreview;
    try { outgoing = request(); checkPreview(outgoing.url, outgoing.init); }
    catch (error) { status(error.message); return; }
    previousKeyPreview = keyPreview;
    working = true; fields.disabled = true; mode.disabled = true; pat.disabled = true;
    status('正在准备请求…');
    try {
      const response = await transport(outgoing);
      if (!response) { status('已取消，没有发送请求。'); return; }
      releaseFile(); byId('docs-response').textContent = '';
      if (response.ok && response.headers.get('content-type')?.includes('spreadsheetml')) {
        fileURL = URL.createObjectURL(await response.blob()); byId('docs-file').href = fileURL; byId('docs-file').hidden = false;
        status('HTTP ' + response.status + ' · Excel 已返回，点击链接下载。'); return;
      }
      const raw = await response.text();
      let data; try { data = JSON.parse(raw); } catch { /* Display a non-JSON error as text, never HTML. */ }
      byId('docs-response').textContent = data ? JSON.stringify(data, null, 2) : raw;
      const partial = Array.isArray(data?.results) && data.results.some(item => item.ok === false || (item.state && item.state !== 'success'));
      status('HTTP ' + response.status + (partial ? ' · 存在失败或待核对项，停止后续操作。' : response.ok ? ' · 响应已返回，请核对内容。' : ' · 请求失败；未自动重发。'));
      const path = new URL(outgoing.url).pathname;
      if (response.ok && path === steps['quota-preview'][1] && Array.isArray(data?.users)) quotaPreview = {body: outgoing.init.body, epoch: authEpoch};
      if (response.ok && path === steps['keys-preview'][1] && typeof data?.operation_id === 'string') keyPreview = {id: data.operation_id, count: data.count, body: outgoing.init.body, epoch: authEpoch};
      if (response.ok && previousKeyPreview && path.endsWith('/' + previousKeyPreview.id + '/apply') && !partial && data?.done === false && Array.isArray(data.results) && data.results.length > 0 && data.results.every(item => item.state === 'success')) keyPreview = previousKeyPreview;
    } catch (error) {
      status('请求未完成：' + error.message + '。如已确认发送，先核对实际数据和操作记录，不自动重发。');
    } finally { working = false; fields.disabled = false; mode.disabled = false; pat.disabled = false; }
  });
  async function showReference() {
    if (referenceReady || !spec) return;
    referenceReady = true;
    try {
      if (!window.Scalar) await new Promise((resolve, reject) => {
        const script = document.createElement('script'); script.src = '/cockpit/static/scalar-1.73.1.js'; script.onload = resolve; script.onerror = reject; document.head.append(script);
      });
      window.Scalar.createApiReference('#scalar-reference', {content: spec, theme: 'none', showSidebar: false, hideTestRequestButton: true, hideClientButton: true, hideDarkModeToggle: true, showDeveloperTools: 'never', withDefaultFonts: false, persistAuth: false, telemetry: false, documentDownloadType: 'none', servers: [{url: location.origin}], defaultHttpClient: {targetKey: 'shell', clientKey: 'curl'}, customFetch: async () => { throw new Error('请使用本页统一在线调试入口。'); }});
    } catch { referenceReady = false; byId('docs-load-status').textContent = '接口参考加载失败，可先使用在线调试或下载 OpenAPI。'; }
  }
  for (const name of ['guide', 'reference']) byId('docs-' + name + '-tab').addEventListener('click', () => {
    for (const panel of ['guide', 'reference']) { byId('docs-' + panel).hidden = panel !== name; byId('docs-' + panel + '-tab').setAttribute('aria-selected', String(panel === name)); }
    if (name === 'reference') showReference();
  });
  window.addEventListener('pagehide', () => { pat.value = ''; body.value = ''; byId('docs-response').textContent = ''; byId('docs-code').textContent = ''; quotaPreview = null; keyPreview = null; releaseFile(); });
  byId('docs-origin').textContent = location.origin;
  (async () => {
    try {
      const response = await window.CockpitAuth.fetchOnce('/cockpit/api/openapi.json');
      if (!response.ok) throw new Error('HTTP ' + response.status);
      spec = await response.json(); list = client.operations(spec);
      const groups = new Map();
      list.forEach((operation, index) => {
        const tag = operation.tags[0];
        if (!groups.has(tag)) { const group = document.createElement('optgroup'); group.label = tag; groups.set(tag, group); select.append(group); }
        const option = document.createElement('option'); option.value = index; option.textContent = operation.method + ' ' + operation.path + ' · ' + operation.summary; groups.get(tag).append(option);
      });
      transport = client.createTransport({spec, origin: location.origin,
        authenticate: () => ({mode: mode.value, token: pat.value}), confirm,
        fetchOnce: (url, init) => {
          checkPreview(url, init, true);
          const path = new URL(url).pathname;
          if (path === steps['quota-preview'][1]) quotaPreview = null;
          if (path === steps['keys-preview'][1]) keyPreview = null;
          status('正在发送一次真实请求…'); return window.CockpitAuth.fetchOnce(url, init);
        },
      });
      setOperation('GET', '/cockpit/api/statistics/scopes'); fields.disabled = false;
      byId('docs-load-status').textContent = list.filter(item => item['x-cockpit-testable']).length + ' 项接口可调试；不会在打开页面时调用业务接口。';
      if (!byId('docs-reference').hidden) showReference();
    } catch (error) { byId('docs-load-status').textContent = '接口描述读取失败（' + error.message + '），请重新登录或刷新。'; }
  })();
})();
