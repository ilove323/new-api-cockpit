/* Personal-token manual. No generation, POST replay, secret preview or storage.
 * Usage: defer manual-guides.js before this file; auth.js owns the global fetch bridge.
 * A KEY exists only inside one explicit copy operation and the requested clipboard item.
 */
(() => {
  'use strict';
  const $ = id => document.getElementById(`manual-${id}`);
  if (!$('page')) return;
  const protocolNames = {
    chat: 'OpenAI Chat Completions（/v1/chat/completions）',
    responses: 'OpenAI Responses（/v1/responses）',
    anthropic: 'Anthropic Messages（/v1/messages）',
  };
  const module = window.CockpitManualGuides;
  const guides = Array.isArray(module?.guides) ? module.guides.filter(guide =>
    typeof guide?.id === 'string' && typeof guide.title === 'string') : [];
  const state = {
    active: true, generation: 0, tokens: [], models: [], tokenId: '', modelId: '',
    guideId: guides[0]?.id || '', protocol: '', platform: 'posix', target: 'claude-code', snippet: 0,
    previews: [], loadingTokens: false, loadingModels: false, copying: false,
  };
  const controllers = new Set();
  // One placeholder preview and copy action per configuration step. No KEY is
  // retained here; actual credentials exist only inside an explicit copy.
  let examples = [];
  const text = value => typeof value === 'string' ? value : value == null ? '' : String(value);
  const element = (tag, value, className) => {
    const node = document.createElement(tag);
    if (value !== undefined) node.textContent = text(value);
    if (className) node.className = className;
    return node;
  };
  const option = (value, label) => {
    const node = element('option', label);
    node.value = value;
    return node;
  };
  const message = (id, value, error = false) => {
    const node = $(id);
    node.textContent = value;
    node.hidden = !value;
    node.classList.toggle('error', error);
  };
  const token = () => state.tokens.find(item => String(item.id) === state.tokenId);
  const model = () => state.models.find(item => item.id === state.modelId);
  const guide = () => guides.find(item => item.id === state.guideId);
  const session = () => document.querySelector('meta[name="cockpit-session"]')?.content || '';
  const declared = value => Array.isArray(value) ? [...new Set(value.filter(item => Object.hasOwn(protocolNames, item)))] : [];
  const ready = () => state.active && token()?.usable === true && !!model() && !!guide()
    && !!state.protocol && !!state.previews[state.snippet];
  const canClipboard = () => !!navigator.clipboard
    && (typeof navigator.clipboard.writeText === 'function'
      || (typeof navigator.clipboard.write === 'function' && typeof window.ClipboardItem === 'function'));

  class Cancelled extends Error {}
  function copyMessage(value, error = false) {
    const node = examples[state.snippet]?.status;
    if (!node) return;
    node.textContent = value;
    node.hidden = !value;
    node.classList.toggle('error', error);
  }
  function capture() {
    return {generation: state.generation, session: session(), tokenId: state.tokenId,
      modelId: state.modelId, guideId: state.guideId, protocol: state.protocol,
      platform: state.platform, target: state.target, snippet: state.snippet, origin: location.origin};
  }
  function current(snapshot) {
    return state.active && snapshot.generation === state.generation && snapshot.session === session()
      && snapshot.tokenId === state.tokenId && snapshot.modelId === state.modelId
      && snapshot.guideId === state.guideId && snapshot.protocol === state.protocol
      && snapshot.platform === state.platform && snapshot.snippet === state.snippet
      && snapshot.target === state.target && $('target').value === state.target
      && $('token').value === state.tokenId && $('model').value === state.modelId
      && $('protocol').value === state.protocol && $('platform').value === state.platform;
  }
  function invalidate() {
    const interrupted = state.loadingTokens || state.loadingModels;
    if (state.loadingTokens) {
      $('token').replaceChildren(option('', '读取已取消，请刷新令牌')); $('token').value = '';
    }
    if (state.loadingModels) {
      $('model').replaceChildren(option('', '读取已取消，请手动读取模型')); $('model').value = '';
    }
    state.generation++;
    for (const controller of controllers) controller.abort();
    state.loadingTokens = false;
    state.loadingModels = false;
    state.copying = false;
    for (const example of examples.filter(Boolean)) {
      example.status.textContent = ''; example.status.hidden = true;
    }
    if (interrupted) message('load-status', '选择已变化，读取已取消。请手动刷新令牌或读取模型。');
  }
  // One deadline covers fetch, JSON decoding, and (for copies) clipboard completion.
  // Racing the abort also handles a bridge/fixture that has not settled its fetch yet.
  function operation(snapshot) {
    const controller = new AbortController();
    controllers.add(controller);
    let timedOut = false;
    const timer = setTimeout(() => { timedOut = true; controller.abort(); }, 15000);
    const failure = () => timedOut ? new Error('操作超时，未自动重发。请确认后手动重试。') : new Cancelled();
    let rejectAbort;
    const aborted = new Promise((resolve, reject) => { rejectAbort = reject; });
    // Cancellation may precede the first wait (clipboard constructors can throw).
    aborted.catch(() => {});
    const onAbort = () => rejectAbort(failure());
    controller.signal.addEventListener('abort', onAbort, {once: true});
    const assertCurrent = () => {
      if (!current(snapshot)) throw new Cancelled();
      if (controller.signal.aborted) throw failure();
    };
    return {
      signal: controller.signal, assertCurrent,
      async wait(promise) {
        const result = await Promise.race([promise, aborted]);
        assertCurrent();
        return result;
      },
      cancel() { controller.abort(); },
      dispose() {
        clearTimeout(timer);
        controller.signal.removeEventListener('abort', onAbort);
        controllers.delete(controller);
      },
    };
  }
  function backendError(data, fallback) {
    const value = typeof data?.error === 'string' ? data.error
      : typeof data?.error?.message === 'string' ? data.error.message
      : typeof data?.message === 'string' ? data.message : fallback;
    // Even an error response must not accidentally echo a model KEY into the page.
    return value.replace(/sk-[^\s"'<>`,;:()\[\]{}\\]+/gi, '[KEY 已隐藏]').slice(0, 500);
  }
  async function json(path, body, op) {
    op.assertCurrent();
    // Do not capture rawFetch or use fetchOnce: the shared auth bridge adds the session.
    const response = await op.wait(fetch(path, {
      method: body === undefined ? 'GET' : 'POST', credentials: 'same-origin',
      cache: 'no-store', redirect: 'error', signal: op.signal,
      ...(body === undefined ? {} : {
        headers: {'X-Cockpit-Manual': '1', 'Content-Type': 'application/json'},
        body: JSON.stringify(body),
      }),
    }));
    let data;
    try { data = await op.wait(response.json()); }
    catch (error) {
      if (error instanceof Cancelled || op.signal.aborted) throw error;
      throw new Error('后台返回格式无法读取，请检查服务入口；未自动重发。');
    }
    const failed = !response.ok || data?.success === false || !!data?.error;
    if (failed) {
      const reason = backendError(data, `请求失败（${response.status || '未知状态'}），未自动重发。`);
      if (response.status === 401 || (response.status === 409 && data?.code === 'AUTH_SESSION_CHANGED')) {
        shutdown(reason);
        throw new Cancelled();
      }
      throw new Error(reason);
    }
    return data;
  }
  function controls() {
    $('token').disabled = !state.active || state.loadingTokens || state.tokens.length === 0;
    $('model').disabled = !state.active || token()?.usable !== true || state.loadingModels || state.models.length === 0;
    $('refresh-tokens').disabled = !state.active || state.loadingTokens;
    $('refresh-models').disabled = !state.active || token()?.usable !== true || state.loadingModels || state.loadingTokens;
    $('platform').disabled = !state.active;
    $('target').disabled = !state.active;
    $('protocol').disabled = !state.active || !model() || !state.protocol;
    for (const example of examples.filter(Boolean)) {
      const block = state.previews[example.index];
      example.button.disabled = !ready() || !block || state.copying || !canClipboard();
      example.button.textContent = state.copying && state.snippet === example.index ? '正在复制…'
        : block?.containsKey === true ? '复制配置（含 KEY）' : '复制配置';
      if (state.active && !canClipboard() && !example.status.textContent) {
        example.status.textContent = '安全剪贴板不可用，请使用 HTTPS 或本机安全页面并允许剪贴板权限。';
        example.status.hidden = false; example.status.classList.toggle('error', true);
      }
    }
    for (const card of $('tools').children) card.disabled = !state.active;
  }
  function emptyModels(label = '请先选择可用令牌') {
    state.models = [];
    state.modelId = '';
    state.protocol = '';
    state.snippet = 0;
    $('model').replaceChildren(option('', label));
    $('model').value = '';
  }
  function tokenDetails() {
    const item = token();
    $('token-details').replaceChildren();
    $('token-details').hidden = !item;
    if (!item) return;
    const statuses = {1: '启用', 2: '禁用', 3: '已过期', 4: '额度用尽'};
    const expiry = Number(item.expired_time);
    const pairs = [
      ['名称', item.name || '未命名令牌'], ['脱敏 KEY', item.key_masked],
      ['分组', item.group || '默认分组'], ['状态', item.usable ? '可用' : item.unavailable_reason || statuses[item.status] || '不可用'],
      ['有效期', expiry > 0 && Number.isFinite(expiry) ? new Date(expiry * 1000).toLocaleString('zh-CN') : '不过期'],
      ['剩余额度', item.unlimited_quota ? '不限额' : `${text(item.remain_quota) || '—'}（系统额度单位，非金额）`],
    ];
    for (const [label, value] of pairs) {
      const row = element('div'); row.append(element('dt', label), element('dd', value));
      $('token-details').append(row);
    }
  }
  function masked(value) {
    return typeof value === 'string' && /\*|•|…|\.{3}/.test(value) ? value.slice(0, 160) : '已隐藏';
  }
  async function loadTokens() {
    if (!state.active || state.loadingTokens) return;
    invalidate();
    state.tokens = []; state.tokenId = '';
    $('token').replaceChildren(option('', '正在读取本人令牌…')); $('token').value = '';
    emptyModels(); tokenDetails(); renderGuide();
    state.loadingTokens = true; controls(); $('empty').hidden = true;
    message('load-status', '正在读取本人令牌…');
    const snapshot = capture(), op = operation(snapshot);
    try {
      const data = await json('/cockpit/api/manual/tokens', undefined, op);
      if (!Array.isArray(data?.tokens)) throw new Error('后台未返回本人令牌列表。');
      state.tokens = data.tokens.filter(item => Number.isSafeInteger(Number(item?.id)) && Number(item.id) > 0).map(item => ({
        id: Number(item.id), name: text(item.name), key_masked: masked(item.key_masked),
        group: text(item.group), status: item.status, expired_time: item.expired_time,
        remain_quota: item.remain_quota, unlimited_quota: item.unlimited_quota === true,
        usable: item.usable === true, unavailable_reason: backendError({error: text(item.unavailable_reason)}, ''),
      }));
      // This link is deliberately New API's personal token page, never cockpit/keys.
      $('token-manager').href = '/keys';
      $('token').replaceChildren(option('', state.tokens.length ? '请选择本人令牌' : '暂无本人令牌'));
      for (const item of state.tokens) {
        $('token').append(option(String(item.id), `${item.name || '未命名令牌'} · ${item.key_masked} · ${item.group || '默认分组'} · ${item.usable ? '可用' : '不可用'}`));
      }
      $('token').value = '';
      $('empty').hidden = state.tokens.some(item => item.usable);
      message('load-status', state.tokens.length ? '请选择本人令牌，只有可用令牌才能读取模型。' : '当前账号还没有令牌。');
    } catch (error) {
      if (current(snapshot) && !(error instanceof Cancelled)) {
        $('token').replaceChildren(option('', '令牌读取失败')); $('token').value = '';
        message('load-status', backendError({error: error.message}, '令牌读取失败，未自动重发。'), true);
      }
    } finally {
      op.dispose();
      if (current(snapshot)) { state.loadingTokens = false; controls(); }
    }
  }
  async function loadModels() {
    if (!state.active || token()?.usable !== true || state.loadingModels) return;
    invalidate(); emptyModels('正在检查所选令牌的模型权限…'); renderGuide();
    state.loadingModels = true; controls();
    message('load-status', '正在读取所选令牌允许使用的模型，不发送付费模型请求…');
    const snapshot = capture(), op = operation(snapshot);
    try {
      const data = await json('/cockpit/api/manual/models', {token_id: Number(snapshot.tokenId)}, op);
      if (!Array.isArray(data?.models)) throw new Error('后台未返回模型列表；不会回退读取全站模型。');
      state.models = data.models.filter(item => typeof item?.id === 'string' && item.id.length > 0)
        .map(item => ({id: item.id}));
      $('model').replaceChildren(option('', state.models.length ? '请选择模型' : '该令牌没有可用模型'));
      for (const item of state.models) $('model').append(option(item.id, item.id));
      $('model').value = '';
      message('load-status', state.models.length ? '' : '该令牌没有可用模型，请检查令牌模型权限和分组。');
    } catch (error) {
      if (current(snapshot) && !(error instanceof Cancelled)) {
        emptyModels('模型读取失败，请查看后台错误');
        message('load-status', backendError({error: error.message}, '模型读取失败，未自动重发。'), true);
      }
    } finally {
      op.dispose();
      if (current(snapshot)) { state.loadingModels = false; renderGuide(); }
    }
  }
  function renderTools() {
    $('tools').replaceChildren();
    guides.forEach((item, index) => {
      const card = element('button', undefined, 'manual-tool'); card.type = 'button';
      card.setAttribute('aria-pressed', String(state.guideId === item.id));
      card.setAttribute('aria-controls', 'manual-guide-title');
      card.append(element('span', String(index + 1).padStart(2, '0'), 'manual-tool-mark'),
        element('span', item.title, 'manual-tool-title'), element('span', item.description, 'manual-tool-description'));
      card.addEventListener('click', () => {
        if (!state.active || state.guideId === item.id) return;
        invalidate(); state.guideId = item.id; state.snippet = 0;
        renderTools(); renderGuide();
      });
      $('tools').append(card);
    });
    if (!guides.length || typeof module?.build !== 'function') message('guides-status', '教程内容暂未加载，暂时不能生成配置。请稍后重新加载页面。', true);
  }
  // HTTPS documentation sources only. GitHub remains repository-restricted.
  const sourceHosts = new Set([
    'learn.chatgpt.com', 'platform.openai.com', 'developers.openai.com',
    'code.claude.com', 'platform.claude.com', 'cursor.com', 'forum.cursor.com',
    'docs.trae.cn', 'docs.qoder.com', 'docs.openclaw.ai',
    'workbuddy.cn', 'developers.cloudflare.com',
  ]);
  const githubSources = /^\/(?:farion1231\/cc-switch|chatboxai\/chatbox|openai\/codex|anthropics\/claude-code|NousResearch\/hermes-agent)(?:\/|$)/i;
  function sourceURL(value) {
    if (typeof value !== 'string') return '';
    try {
      const url = new URL(value);
      if (url.protocol !== 'https:' || url.username || url.password || (url.port && url.port !== '443')) return '';
      const host = url.hostname.replace(/^www\./, '');
      return sourceHosts.has(host) || (host === 'github.com' && githubSources.test(url.pathname)) ? url.href : '';
    } catch { return ''; }
  }
  function blocks(snapshot, key) {
    if (typeof module?.build !== 'function') throw new Error('配置生成器暂不可用。');
    const result = module.build(snapshot.guideId, {origin: snapshot.origin, model: snapshot.modelId,
      key, platform: snapshot.platform, target: snapshot.target, protocol: snapshot.protocol});
    if (!Array.isArray(result) || result.some(item => typeof item?.content !== 'string')) throw new Error('配置内容格式不完整。');
    return result.map(item => ({title: text(item.title) || '配置片段', language: text(item.language), content: item.content, containsKey: item.containsKey !== false}));
  }
  function exampleView(index, help, error) {
    const block = state.previews[index];
    const container = element('div', undefined, 'manual-step-example');
    container.id = `manual-example-${index}`;
    const hint = element('p', block ? '' : help, 'hint'); hint.hidden = !!block;
    hint.classList.toggle('error', error);
    const preview = element('div', undefined, 'manual-preview'); preview.hidden = !block;
    const heading = element('div', undefined, 'manual-code-heading');
    const title = element('span', block?.title), language = element('span', block?.language, 'hint');
    const pre = element('pre'), code = element('code', block?.content);
    pre.tabIndex = 0; pre.setAttribute('aria-label', block?.title || '仅含占位符的配置示例');
    heading.append(title, language); pre.append(code); preview.append(heading, pre);
    const actions = element('div', undefined, 'manual-copy-actions');
    const button = element('button', '复制配置（含 KEY）', 'button-primary'); button.type = 'button';
    const status = element('p', '', 'manual-copy-status');
    status.setAttribute('role', 'status'); status.setAttribute('aria-live', 'polite'); status.hidden = true;
    actions.append(button); container.append(hint, preview, actions, status);
    const view = {index, container, button, status}; examples[index] = view;
    button.addEventListener('click', () => {
      // Detached buttons from an earlier render cannot start a new KEY read.
      if (examples[index] === view) return copy(index);
    });
    return container;
  }
  function renderGuide() {
    const item = guide(), selectedModel = model();
    $('guide-title').textContent = item?.title || '配置教程';
    $('guide-description').textContent = text(item?.description);
    $('target-control').hidden = item?.id !== 'cc-switch';
    $('target').value = state.target;
    $('protocol-label').textContent = item?.id === 'cc-switch' ? 'CC Switch 上游协议'
      : item ? `${item.title} 使用的协议` : '客户端协议';
    // Model-list metadata is not a client protocol whitelist. Keep all three
    // formats visible, enabling only the selected tool's native configurations.
    const allowed = declared(item?.protocols);
    if (!selectedModel) {
      state.protocol = '';
    } else if (!allowed.includes(state.protocol)) {
      state.protocol = allowed.includes(item?.defaultProtocol) ? item.defaultProtocol : allowed[0] || '';
    }
    $('protocol').replaceChildren();
    for (const [value, label] of Object.entries(protocolNames)) {
      const node = option(value, label);
      node.disabled = !allowed.includes(value);
      $('protocol').append(node);
    }
    $('protocol-help').textContent = item && !allowed.length ? '工具协议配置缺失，请重新加载页面。' : '';
    $('protocol-help').hidden = !$('protocol-help').textContent;
    $('protocol').value = state.protocol;
    state.previews = [];
    let help = '请选择上方的令牌和模型。', buildError = false;
    if (state.active && token()?.usable === true && selectedModel && item && state.protocol) {
      try {
        const built = blocks(capture(), 'API_KEY');
        const slots = (item.steps || []).flatMap(step => step.snippets || []);
        if (slots.length !== built.length || new Set(slots).size !== built.length
          || slots.some(index => !Number.isSafeInteger(index) || index < 0 || index >= built.length)) {
          throw new Error('Configuration steps must contain each block exactly once');
        }
        state.previews = built;
      } catch { help = '配置示例暂时无法生成；未读取真实 KEY。'; buildError = true; }
    }
    if (state.snippet >= state.previews.length) state.snippet = 0;
    examples = [];
    $('steps').replaceChildren();
    for (const [index, step] of (Array.isArray(item?.steps) ? item.steps : []).entries()) {
      const row = element('li', undefined, 'manual-step'), body = element('div');
      body.append(element('h3', step.title), element('p', step.body));
      for (const snippet of Array.isArray(step.snippets) ? step.snippets : []) {
        if (Number.isSafeInteger(snippet) && snippet >= 0 && !examples[snippet]) {
          body.append(exampleView(snippet, help, buildError));
        }
      }
      const picture = step.image, href = sourceURL(picture?.source);
      // Official screenshots are bundled static assets. No remote image load,
      // HTML injection, arbitrary path, or account data in a third-party request.
      if (picture && /^\/cockpit\/static\/manual-images\/[a-z0-9-]+\.(?:webp|png|jpg)$/.test(picture.src) && href) {
        const figure = element('figure', undefined, 'manual-screenshot');
        const link = element('a'), img = element('img');
        link.href = picture.src; link.target = '_blank'; link.rel = 'noopener noreferrer';
        img.src = picture.src; img.alt = text(picture.alt); img.loading = 'lazy'; img.decoding = 'async';
        if (Number.isSafeInteger(picture.width) && picture.width > 0) img.width = picture.width;
        if (Number.isSafeInteger(picture.height) && picture.height > 0) img.height = picture.height;
        link.append(img);
        const caption = element('figcaption'), attribution = element('a', '官方截图');
        attribution.href = href; attribution.target = '_blank'; attribution.rel = 'noopener noreferrer';
        caption.append(element('span', picture.caption), attribution); figure.append(link, caption); body.append(figure);
      }
      row.append(element('span', index + 1, 'manual-step-number'), body); $('steps').append(row);
    }
    $('notes').replaceChildren();
    for (const note of Array.isArray(item?.notes) ? item.notes : []) $('notes').append(element('li', note));
    $('sources').replaceChildren();
    for (const source of Array.isArray(item?.sources) ? item.sources : []) {
      const href = sourceURL(source.url);
      if (!href) continue;
      const row = element('li'), link = element('a', source.title || '官方说明');
      link.href = href; link.target = '_blank'; link.rel = 'noopener noreferrer';
      row.append(link); $('sources').append(row);
    }
    if (!$('sources').children.length) $('sources').append(element('li', '暂无官方资料链接。'));
    controls();
  }
  async function writeClipboard(content, op) {
    op.assertCurrent();
    if (typeof navigator.clipboard?.write === 'function' && typeof window.ClipboardItem === 'function') {
      // Invoke write synchronously within the click, before the KEY fetch settles.
      const payload = Promise.resolve(content).then(value => {
        op.assertCurrent();
        return new Blob([value], {type: 'text/plain'});
      });
      payload.catch(() => {});
      await op.wait(navigator.clipboard.write([new window.ClipboardItem({'text/plain': payload})]));
    } else {
      const value = await op.wait(Promise.resolve(content));
      op.assertCurrent();
      await op.wait(navigator.clipboard.writeText(value));
    }
    op.assertCurrent();
  }
  async function copy(index) {
    if (state.copying || !state.previews[index]) return;
    state.snippet = index;
    if (!ready()) return;
    const withKey = state.previews[state.snippet]?.containsKey !== false;
    if (!canClipboard()) { copyMessage('安全剪贴板不可用；本页不会展示真实 KEY 作为后备方案。', true); return; }
    state.copying = true; controls();
    copyMessage('正在复制…');
    const snapshot = capture(), op = operation(snapshot);
    let keyError = '', copied = false;
    // Keep the key, response, build result and content out of state and the DOM.
    const content = withKey ? (async () => {
      let key = '', built = [], value = '', data;
      try {
        try { data = await json('/cockpit/api/manual/key', {token_id: Number(snapshot.tokenId)}, op); }
        catch (error) {
          if (!(error instanceof Cancelled)) keyError = backendError({error: error.message}, '读取 KEY 失败，未自动重发。');
          throw error;
        }
        op.assertCurrent();
        if (typeof data?.key !== 'string' || !data.key || /[\r\n\x00]/.test(data.key)) {
          keyError = '后台未返回有效 KEY，未复制。'; throw new Error();
        }
        key = data.key; data = undefined;
        built = blocks(snapshot, key);
        if (!built[snapshot.snippet]) throw new Error();
        value = built[snapshot.snippet].content;
        op.assertCurrent();
        return value;
      } finally { key = ''; data = undefined; built = []; value = ''; }
    })() : state.previews[snapshot.snippet].content;
    // Some clipboard implementations reject before consuming the promised payload.
    if (withKey) content.catch(() => {});
    try {
      await writeClipboard(content, op);
      copied = true;
      copyMessage('已复制。');
    } catch (error) {
      op.cancel();
      if (current(snapshot) && !(error instanceof Cancelled)) {
        copyMessage(keyError || '复制失败，请检查浏览器剪贴板权限；未展示 KEY、未自动重发。', true);
      }
    } finally {
      // If clipboard refused early, the aborted KEY request can never build/copy later.
      if (!copied) op.cancel();
      op.dispose();
      if (current(snapshot)) { state.copying = false; controls(); }
    }
  }
  function shutdown(reason = '登录状态已变化，请重新加载页面后重新选择。') {
    invalidate(); state.active = false;
    state.tokens = []; state.tokenId = ''; state.guideId = '';
    state.platform = 'posix'; $('platform').value = 'posix';
    $('token').replaceChildren(option('', '请重新加载页面')); $('token').value = '';
    emptyModels('请重新加载页面'); tokenDetails(); renderTools(); renderGuide();
    $('empty').hidden = true;
    message('load-status', reason, true);
    channel?.close(); observer?.disconnect();
  }
  $('token').addEventListener('change', () => {
    if (!state.active) return;
    invalidate(); state.tokenId = $('token').value; emptyModels(); tokenDetails(); renderGuide();
    if (token()?.usable === true) return loadModels();
    message('load-status', token() ? token().unavailable_reason || '此令牌不可用，请检查状态、有效期和额度。' : '请先选择本人可用令牌。', !!token());
  });
  $('model').addEventListener('change', () => {
    if (!state.active) return;
    invalidate(); state.modelId = $('model').value; state.protocol = ''; state.snippet = 0; renderGuide();
  });
  $('protocol').addEventListener('change', () => {
    if (!state.active) return;
    invalidate(); state.protocol = $('protocol').value; state.snippet = 0; renderGuide();
  });
  $('platform').addEventListener('change', () => {
    if (!state.active) return;
    invalidate(); state.platform = $('platform').value === 'powershell' ? 'powershell' : 'posix'; renderGuide();
  });
  $('target').addEventListener('change', () => {
    if (!state.active) return;
    invalidate(); state.target = $('target').value === 'codex' ? 'codex' : 'claude-code';
    state.snippet = 0; renderGuide();
  });
  $('refresh-tokens').addEventListener('click', loadTokens);
  $('refresh-models').addEventListener('click', loadModels);
  window.addEventListener('cockpit-auth-changed', () => shutdown());
  window.addEventListener('pagehide', () => shutdown('页面已离开，选择与预览已清空。返回后请重新加载页面。'));
  document.getElementById('logout')?.addEventListener('click', () => shutdown('正在退出登录，已清空选择与预览。'), {capture: true});
  // Mirror the existing bridge's cross-tab invalidation immediately, even while its
  // session DELETE is pending. No access to browser token storage is needed.
  function authEvent(event) {
    if (!event || typeof event.sid !== 'string' || !event.sid || typeof event.timestamp !== 'number'
      || Math.abs(Date.now() - event.timestamp) > 60000) return;
    if ((event.kind === 'signed_out' && event.sid === session())
      || (event.kind === 'authenticated' && session() && event.sid !== session())) shutdown();
  }
  const channel = typeof BroadcastChannel === 'function' ? new BroadcastChannel('new-api:auth-session') : null;
  channel?.addEventListener('message', event => authEvent(event.data));
  window.addEventListener('storage', event => {
    if (event.key !== 'new-api:auth-session:event' || !event.newValue) return;
    try { authEvent(JSON.parse(event.newValue)); } catch { /* Untrusted synchronization data. */ }
  });
  const initialSession = session();
  const observer = typeof MutationObserver === 'function' ? new MutationObserver(() => {
    if (session() !== initialSession) shutdown();
  }) : null;
  const sessionMeta = document.querySelector('meta[name="cockpit-session"]');
  if (sessionMeta) observer?.observe(sessionMeta, {attributes: true, attributeFilter: ['content']});
  // Account-MCP examples are independent of the model-token selection. They
  // contain placeholders only: never call /manual/key for a user PAT.
  function mcpPreview() {
    if (typeof module?.buildMcp !== 'function' || !$('mcp-code')) return;
    try {
      const snippet = module.buildMcp($('mcp-client').value || 'codex', location.origin);
      $('mcp-url').textContent = new URL('/cockpit/mcp', location.origin).href;
      $('mcp-code-title').textContent = snippet.title;
      $('mcp-code').textContent = snippet.content;
      $('mcp-copy-status').textContent = '';
    } catch { $('mcp-code').textContent = '无法生成 MCP 示例，请核对站点地址。'; }
  }
  $('mcp-client')?.addEventListener('change', mcpPreview);
  $('mcp-copy')?.addEventListener('click', async () => {
    try {
      const snippet = module.buildMcp($('mcp-client').value || 'codex', location.origin);
      await navigator.clipboard.writeText(snippet.content);
      $('mcp-copy-status').textContent = '已复制。请在客户端单独配置用户 API PAT；本页不会读取或填入它。';
    } catch { $('mcp-copy-status').textContent = '复制被阻止，请手动复制占位符配置。'; }
  });
  mcpPreview();
  renderTools(); renderGuide();
  loadTokens();
})();
