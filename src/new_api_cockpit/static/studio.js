/* Studio DOM adapter. Domain owns intent/parameters, request owns single-attempt
 * HTTP, results owns candidates/versions/Blob lifetime, canvas owns document pixels.
 * Controls are projections of state; only input events write state. async requests
 * use an immutable capture, never current controls or a candidate preview.
 * Offline: node --test tests/test_studio_domain.cjs tests/test_studio_frontend.cjs
 */
(() => {
  'use strict';
  const D = window.CockpitStudioDomain, R = window.CockpitStudioRequest, Results = window.CockpitStudioResults;
  const {MAX_INPUT,MAX_MAIN_PIXELS,MAX_TOTAL_PIXELS,MAX_CANDIDATES,REQUEST_TIMEOUT,MIME,clean,
    modelProfile,parametersFor,referenceLimit,submissionMode,intentFor,validatePrompt,operationProfile,defaultsFor} = D;
  const {Cancelled,RequestFailure,validateSnapshot} = R;
  const MODES = ['create','edit'];
  const maskTools = ['brush', 'eraser', 'rectangle', 'lasso'];
  const toolNames = {pan: '移动', select: '选择', brush: '选区笔刷', eraser: '擦除选区', rectangle: '矩形选区',
    lasso: '圈选', pen: '标注画笔', arrow: '箭头', box: '方框', ellipse: '椭圆', text: '文字', 'guide-eraser': '擦除标注'};
  const labels = {auto: '自动', low: '低', medium: '标准', high: '高', opaque: '不透明',
    transparent: '透明', png: 'PNG', jpeg: 'JPEG', webp: 'WebP', xhigh: '超高', max: '最高'};
  const canvasErrors = {EMPTY_MASK: '请画出修改选区，或减小向内羽化。',
    EMPTY_WORKING_MASK: '选区在当前输出尺寸下太小，请扩大选区或提高输出尺寸。',
    COMMAND_LIMIT: '画布操作已达上限，请清理选区或标注。',
    IMAGE_DIMENSIONS: '图片尺寸超过画布上限，请选择较小的图片。',
    IMAGE_TOO_LARGE: '图片超过画布输入上限，请选择较小的图片。',
    HISTORY_LIMIT: '画布操作已达上限，请清理选区或标注。',
    INVALID_IMAGE: '图片无法读取，请选择 PNG、JPEG 或 WebP。'};
  const $ = id => document.getElementById(`studio-${id}`);
  if (!$('page')) return;
  const tools = [...document.querySelectorAll('[data-tool]')];
  const modes = [...document.querySelectorAll('[data-mode]')];
  const results = Results.createStore();
  const {urlFor,revoke,inspect} = results;
  const state = {active: true, epoch: 0, tokenEpoch: 0, catalogBusy: false, tokens: [], models: [],
    tokenId: '', modelId: '', mode: 'create', tool: 'pan', source: null, info: null,
    references: [], versions: results.versions, versionId: '', loadingImage: false, preparing: false,
    pending: null, uncertain: null, candidates: results.candidates, previewId: '', comparing: false, editor: null,
    prompt: '', inputs: {size:'',quality:'',format:'',background:'',fidelity:'',compression:'90',width:'1024',height:'1024'},
    visibility: {mask:true,annotations:true}, documentEpoch: 0};
  const session = () => document.querySelector('meta[name="cockpit-session"]')?.content || '';
  const initialSession = session();
  let previewTrigger = null, previewBackdropPressed = false;
  const token = () => state.tokens.find(x => String(x.id) === state.tokenId);
  const model = () => state.models.find(x => x.id === state.modelId);
  const currentVersion = () => state.versions.find(x => x.id === state.versionId);
  const node = (tag, text, className) => {
    const result = document.createElement(tag);
    if (text !== undefined) result.textContent = text;
    if (className) result.className = className;
    return result;
  };
  const option = (value, text) => { const result = node('option', text); result.value = value; return result; };
  const message = (id, text, error = false) => {
    $(id).textContent = clean(text); $(id).hidden = !text; $(id).classList.toggle('error', error);
  };
  function bind(element, event, fn) {
    element.addEventListener(event, async e => {
      try { await fn(e); }
      catch (error) { if (state.active && !(error instanceof Cancelled)) message('request-status', canvasErrors[error.code] || error.message || '操作失败。', true); }
    });
  }
  const authCapture = () => ({epoch: state.epoch, session: session()});
  function assertAuth(capture) {
    if (state.active && capture.session !== session()) shutdown('登录已变化，请刷新页面重新进入。');
    if (!state.active || capture.epoch !== state.epoch) throw new Cancelled();
  }
  const requestClient = R.createClient({fetch:(...args) => window.fetch(...args), assertAuth,
    isActive:() => state.active, onUnauthorized:shutdown});
  const {operation,json} = requestClient;
  const inputs = () => state.inputs;
  const currentOperation = () => submissionMode(state.mode,state.info);
  const refLimit = () => referenceLimit(model(),currentOperation(),!!state.info?.hasAnnotations);
  function readiness() {
    if (!state.active) return '登录已变化，请刷新页面。';
    if (!state.editor) return '画布组件未加载，请刷新页面。';
    if (state.previewId) return '应用候选，或返回画布继续编辑。';
    if (state.catalogBusy) return '正在读取 KEY 与模型…';
    if (!token()?.usable) return '请选择可用的本人 KEY。';
    const m = model(); if (!m) return '请选择可用模型。';
    const mode = submissionMode(state.mode, state.info);
    const profile = operationProfile(m,mode);
    if (!profile) return m.unavailable_reason || '此模型未开放当前操作。';
    if (mode !== 'generate') {
      if (!state.source) return '请先导入主图。';
      if (!m.edit.inputs.mime_types.includes('image/png')) return '当前模型不接受画布导出的 PNG，请选择其他模型。';
      if (mode === 'local' && m.edit.local_strategy === 'unavailable') return '此模型未开放局部修改，请清空选区或选择其他模型。';
      if (state.references.length > refLimit()) return '输入图片过多，请移除参考图。';
      if (D.needsGuide(m,mode,!!state.info?.hasAnnotations) && m.edit.max_images < 2) return '此模型不能接收指导图，请切换模型。';
      try {for (const ref of state.references) D.validateInput(ref,m.edit.inputs);}
      catch (error) {return error.message;}
    }
    try { parametersFor(m, mode, inputs(), mode === 'generate' ? null : state.info); }
    catch (error) { return error.message; }
    if (state.candidates.length >= MAX_CANDIDATES) return '候选区已满，请先移除一个候选。';
    if (state.uncertain) return '上一笔结果未知，请先核对用量。';
    try { validatePrompt(state.prompt,mode,!!state.info?.hasAnnotations); }
    catch (error) { return error.message; }
    return '';
  }
  function controls() {
    const image = state.active && !!state.source && !!state.editor;
    const editable = image && state.mode === 'edit' && !state.previewId;
    const mutating = !state.active || state.loadingImage || state.preparing;
    const mode = submissionMode(state.mode, state.info);
    const reason = readiness();
    $('submit').disabled = !!reason || !!state.pending || state.loadingImage;
    $('submit-label').textContent = state.preparing ? '准备图片…' : state.pending ? '正在生成…'
      : mode === 'generate' ? '生成图片' : mode === 'local' ? '生成局部修改'
        : state.info?.hasAnnotations ? '按标注修改' : '整图编辑';
    message('readiness', reason);
    $('select-region').hidden = !editable || model()?.edit?.local_strategy === 'unavailable' || !!state.info?.hasMask;
    $('select-region').disabled = mutating || !!state.pending;
    $('token').disabled = !state.active || state.catalogBusy || !state.tokens.length;
    $('model').disabled = !state.active || state.catalogBusy || !state.models.length;
    $('refresh').disabled = !state.active || state.catalogBusy;
    $('prompt').disabled = !state.active;
    $('prompt').required = mode === 'generate' || !state.info?.hasAnnotations;
    $('prompt-help').textContent = mode === 'generate' ? '描述要生成的画面'
      : state.info?.hasAnnotations ? '可留空；与画布标注一起发给模型' : '描述要修改的内容';
    $('prompt').placeholder = mode === 'generate' ? '主体、场景、光线、风格，以及需要改变的细节…'
      : state.info?.hasAnnotations ? '可留空，按指导标注修改；也可填写额外要求…' : '描述需要修改的内容…';
    for (const el of modes) { el.disabled = !state.active; el.setAttribute('aria-pressed', String(el.dataset.mode === state.mode)); }
    for (const el of tools) {
      el.disabled = mutating || !image || !!state.previewId || (el.dataset.drawing !== 'view' && !editable);
      el.setAttribute('aria-pressed', String(el.dataset.tool === state.tool));
    }
    for (const id of ['undo', 'redo', 'fit', 'zoom-in', 'zoom-out']) $(id).disabled = mutating || !image || !!state.previewId;
    if (state.info?.canUndo === false) $('undo').disabled = true;
    if (state.info?.canRedo === false) $('redo').disabled = true;
    for (const id of ['upload', 'empty-upload', 'image-input']) $(id).disabled = mutating || !state.editor;
    $('version').disabled = mutating || !state.versions.length;
    $('drawing-controls').hidden = !editable;
    $('mask-controls').hidden = !maskTools.includes(state.tool) && state.tool !== 'guide-eraser';
    $('guide-controls').hidden = !['pen','arrow','box','ellipse','text','select'].includes(state.tool);
    $('text-help').hidden = state.tool !== 'text' && state.info?.selected?.type !== 'text';
    $('canvas-help').textContent = editable && state.tool === 'text'
      ? '点击画布输入文字 · 双击修改 · Ctrl / ⌘ + Enter 完成 · Esc 取消'
      : '选区控制修改范围，标注表达修改方向';
    $('delete-guide').disabled = !state.info?.selectedId || mutating || !editable;
    $('fidelity-control').hidden = state.mode === 'create' || !operationProfile(model(),mode)?.parameters.allowed_fields.includes('input_fidelity');
    $('references-section').hidden = state.mode === 'create';
    const limit = refLimit();
    $('add-reference').disabled = mutating || !model() || state.references.length >= limit;
    $('reference-input').disabled = $('add-reference').disabled;
    message('reference-help', limit ? `可添加 ${limit} 张参考图${D.needsGuide(model(),mode,!!state.info?.hasAnnotations) ? '，含指导图' : ''}`
      : D.needsGuide(model(),mode,!!state.info?.hasAnnotations) ? '指导图已占用输入名额' : '当前模型不接收额外参考图');
    $('custom-size').hidden = state.inputs.size !== 'custom';
    $('compression-control').hidden = !['jpeg','webp'].includes(state.inputs.format) || !operationProfile(model(),mode)?.parameters.allowed_fields.includes('output_compression');
    for (const id of ['brush-size', 'feather', 'color', 'stroke-width', 'clear-mask', 'clear-guides']) {
      $(id).disabled = mutating || !editable;
    }
    for (const id of ['size', 'quality', 'format', 'background', 'fidelity', 'width', 'height', 'compression']) {
      $(id).disabled = !state.active || !model();
    }
    $('stop-wait').disabled = !state.pending?.sent;
    $('waiting').hidden = !state.pending?.sent;
    $('dropzone').classList.toggle('is-preparing', state.preparing);
    // The native dialog overlays the page; the canvas stays visible and keeps
    // its exact viewport, history and zoom when the lightbox opens or closes.
    $('preview').hidden = !state.previewId; $('preview').inert = !state.previewId;
    $('preview-result').hidden = !state.previewId;
    $('preview-source').hidden = !state.previewId || !state.comparing;
    $('canvas').inert = state.preparing || !state.active || !!state.previewId;
    $('new').disabled = mutating || !state.editor?.clear;
    $('download').disabled = !image || mutating;
    for (const [id,method] of [['mask','setSelectionVisible'],['annotations','setAnnotationsVisible']]) {
      $(`show-${id}`).disabled = !editable || mutating || typeof state.editor?.[method] !== 'function';
      $(`show-${id}`).checked = state.visibility[id];
    }
    $('empty').hidden = !!state.source || !!state.previewId;
    $('mode-help').textContent = mode === 'generate' ? (state.source ? '新建图片 · 不使用当前主图' : '描述你希望生成的画面')
      : mode === 'local' ? '局部修改 · 选区外保留原图'
        : state.info?.hasAnnotations ? '按指导标注修改主图' : '整图编辑 · 选区可限制修改范围';
    $('document-name').textContent = currentVersion()?.name || '主画布';
    $('document-info').textContent = state.info ? `${state.info.width} × ${state.info.height} · ${state.info.hasMask ? '有选区' : '无选区'}${state.info.hasAnnotations ? ' · 有标注' : ''}` : '';
    try {
      const p = model() && parametersFor(model(), mode, inputs(), mode === 'generate' ? null : state.info);
      $('working-size').textContent = state.info && mode !== 'generate' && p
        ? `工作画幅 ${D.snapshotSizeFor(p,state.info)} · 原图不变` : '';
    } catch { $('working-size').textContent = ''; }
  }
  function populateProfiles() {
    const p = operationProfile(model(),currentOperation())?.parameters;
    state.inputs = p ? defaultsFor(p) : {size:'',quality:'',format:'',background:'',fidelity:'',compression:'90',width:'1024',height:'1024'};
    const groups = {size: p?.sizes, quality: p?.qualities, format: p?.formats,
      background: p?.backgrounds, fidelity: p?.input_fidelity};
    for (const [id, list] of Object.entries(groups)) {
      const items = [...(list || [])]; if (id === 'size' && p?.custom_size) items.push('custom');
      $(id).replaceChildren(...(items.length ? items.map(x => option(x, labels[x] || (x === 'custom' ? '自定义' : x.replace('x', ' × '))))
        : [option('', '—')]));
      $(id).value = state.inputs[id];
      const field={size:'size',quality:'quality',format:'output_format',background:'background',fidelity:'input_fidelity'}[id];
      $(`${id}-control`).hidden=!p?.allowed_fields.includes(field);
    }
    for (const id of ['width','height','compression']) $(id).value = state.inputs[id];
    $('compression-value').textContent = state.inputs.compression;
    $('profile-label').textContent = p ? (p.custom_size ? '灵活尺寸' : '标准尺寸') : '';
    controls();
  }
  async function loadTokens() {
    if (!state.active || state.catalogBusy) return;
    state.catalogBusy = true; state.tokenId = ''; state.modelId = ''; state.tokens = []; state.models = [];
    $('token').replaceChildren(option('', '正在读取 KEY…')); $('model').replaceChildren(option('', '请先选择 KEY'));
    populateProfiles(); message('catalog-status', '正在读取本人 KEY…');
    const capture = authCapture(), ticket = ++state.tokenEpoch, op = operation(capture, 15000);
    try {
      const data = await json('tokens', undefined, op); if (ticket !== state.tokenEpoch) return;
      state.tokens = Array.isArray(data.tokens) ? data.tokens.filter(x => Number.isSafeInteger(x.id) && x.id > 0)
        .map(x => ({id: x.id, name: clean(x.name), group: clean(x.group), usable: x.usable === true,
          masked: typeof x.key_masked === 'string' && /[*•]/.test(x.key_masked) ? x.key_masked.slice(0, 80) : '',
          reason: clean(x.unavailable_reason)})) : [];
      $('token').replaceChildren(option('', state.tokens.some(x => x.usable) ? '请选择本人 KEY' : '暂无可用 KEY'),
        ...state.tokens.map(x => { const el = option(String(x.id), `${x.name}${x.group ? ` · ${x.group}` : ''}${x.masked ? ` · ${x.masked}` : ''}${x.usable ? '' : ` · ${x.reason || '不可用'}`}`); el.disabled = !x.usable; return el; }));
      // Never accept a server-supplied arbitrary URL or return raw token fields.
      $('token-manager').href = '/keys';
      message('catalog-status', state.tokens.some(x => x.usable) ? '' : '请到「管理我的 KEY」启用本人 KEY，再刷新。');
    } catch (error) {
      if (error instanceof Cancelled || !state.active) return;
      $('token').replaceChildren(option('', '读取失败，请刷新')); message('catalog-status', error.message, true);
    } finally { op.dispose(); if (ticket === state.tokenEpoch) { state.catalogBusy = false; controls(); } }
  }
  async function loadModels() {
    state.modelId = ''; state.models = [];
    $('model').replaceChildren(option('', '正在读取模型…')); populateProfiles();
    if (!token()?.usable) { $('model').replaceChildren(option('', '请先选择可用 KEY')); controls(); return; }
    const capture = authCapture(), ticket = ++state.tokenEpoch, tokenId = state.tokenId, op = operation(capture, 15000);
    state.catalogBusy = true; controls(); message('catalog-status', '正在读取此 KEY 的可用模型…');
    try {
      const data = await json('models', {token_id: Number(tokenId)}, op);
      if (ticket !== state.tokenEpoch || state.tokenId !== tokenId) return;
      state.models = Array.isArray(data.models) ? data.models.map(modelProfile).filter(Boolean) : [];
      $('model').replaceChildren(option('', state.models.length ? '请选择可用模型' : '此 KEY 暂无可用图像模型'),
        ...state.models.map(x => option(x.id, x.id)));
      message('catalog-status', state.models.length ? '' : '此 KEY 暂无已配置图片协议的可用模型，请检查模型授权或选择其他 KEY。');
    } catch (error) {
      if (error instanceof Cancelled || !state.active) return;
      $('model').replaceChildren(option('', '读取失败，请刷新')); message('catalog-status', error.message, true);
    } finally { op.dispose(); if (ticket === state.tokenEpoch) { state.catalogBusy = false; controls(); } }
  }
  function setTool(name) {
    if (!state.active || !state.editor || !Object.hasOwn(toolNames, name)) return;
    state.tool = name; state.editor.setTool(name); $('active-tool').textContent = toolNames[name]; controls();
  }
  function setMode(mode) {
    if (!state.active || !MODES.includes(mode)) return;
    state.mode = mode;
    if (mode === 'create') setTool('pan');
    closePreview(); populateProfiles();
  }
  function renderVersions() {
    $('version').replaceChildren(...(state.versions.length ? state.versions.map(x => option(x.id, x.name))
      : [option('', '尚未导入主图')])); $('version').value = state.versionId;
  }
  async function loadSource(blob, name, existingVersion) {
    if (!state.active || state.loadingImage || state.preparing || !state.editor) return;
    const capture = authCapture(); state.loadingImage = true; controls();
    try {
      const info = await state.editor.load(blob); assertAuth(capture);
      state.source = blob; state.info = info; state.documentEpoch++; closePreview();
      if (existingVersion) { state.versionId = existingVersion.id; existingVersion.documentId = info.documentId; }
      else {
        const version = results.addVersion(blob,name,info.documentId); state.versionId = version.id;
      }
      if (state.mode === 'create') setMode('edit');
      setTool('pan'); renderVersions(); renderCandidates();
    } finally { if (state.active) { state.loadingImage = false; controls(); } }
  }
  async function importPrimary(files) {
    if (!state.active || state.loadingImage || state.preparing || !files.length) return;
    const file = files[0], capture = {...authCapture(),documentEpoch:state.documentEpoch};
    if (file.size > MAX_INPUT) throw new Error('主图超过 8 MiB，请选择较小的图片。');
    await inspect(file, MAX_MAIN_PIXELS); assertAuth(capture);
    if (capture.documentEpoch !== state.documentEpoch) throw new Error('画布已变化，请重新导入图片。');
    await loadSource(file, file.name || '粘贴的图片'); assertAuth(capture);
    if (files.length > 1) await addReferences(files.slice(1));
  }
  function renderReferences() {
    $('references').replaceChildren(...state.references.map((ref, i) => {
      const figure = node('figure', undefined, 'studio-reference'), img = node('img');
      img.src = ref.url; img.alt = `参考图 ${i + 1}：${ref.name}`;
      const remove = node('button', '×'); remove.type = 'button'; remove.setAttribute('aria-label', `移除参考图 ${i + 1}`);
      remove.disabled = !state.active || state.preparing;
      bind(remove, 'click', () => { if (!state.active || state.preparing) return; revoke(ref.url); state.references = state.references.filter(x => x !== ref); renderReferences(); controls(); });
      const role = node('select'); role.setAttribute('aria-label',`参考图 ${i+1} 用途`);
      role.replaceChildren(...Object.entries({reference:'通用参考',subject:'主体',style:'风格',composition:'构图'}).map(([value,label]) => option(value,label)));
      role.value = ref.role; role.disabled = !state.active || state.preparing;
      bind(role,'change',() => {if (state.active && !state.preparing && D.ROLES.includes(role.value)) ref.role=role.value;});
      figure.append(img, remove, node('figcaption', ref.name),role); return figure;
    })); $('reference-count').textContent = String(state.references.length);
  }
  async function addReferences(files) {
    if (!state.active || state.preparing || !files.length) return;
    const capture = {...authCapture(),documentEpoch:state.documentEpoch}, limit = refLimit();
    if (state.references.length + files.length > limit) throw new Error(`当前最多可添加 ${limit} 张参考图，指导图也占用名额。`);
    const incoming = [];
    for (const file of files) {
      if (file.size > MAX_INPUT) throw new Error('参考图超过 8 MiB，请选择较小的图片。');
      const info = await inspect(file, MAX_MAIN_PIXELS); assertAuth(capture);
      if (model()?.edit) D.validateInput({blob:file,...info},model().edit.inputs);
      incoming.push({role:'reference',blob: file, name: clean(file.name || '参考图'), ...info});
    }
    if (capture.documentEpoch !== state.documentEpoch) throw new Error('主图已变化，请重新添加参考图。');
    // Recheck after async decode: a new selection, guide or import can reduce capacity.
    if (state.references.length + incoming.length > refLimit()) {
      throw new Error('参考图名额已变化，请减少图片后再添加。');
    }
    if ((state.info?.width || 0) * (state.info?.height || 0)
        + [...state.references, ...incoming].reduce((n, x) => n + x.width * x.height, 0) > MAX_TOTAL_PIXELS) {
      throw new Error('输入图片总像素超过 3200 万，请减少参考图。');
    }
    for (const ref of incoming) { ref.url = urlFor(ref.blob); state.references.push(ref); }
    renderReferences(); controls();
  }
  function startElapsed(request) {
    request.started = Date.now();
    const tick = () => {
      if (!state.active || state.pending !== request) return;
      const seconds = Math.floor((Date.now() - request.started) / 1000);
      $('elapsed').textContent = `正在生成 · 已等待 ${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;
    };
    tick(); request.interval = setInterval(tick, 1000);
  }
  async function submit(event) {
    event.preventDefault();
    if (!state.active || state.pending || state.loadingImage) return;
    state.editor?.finishText?.();
    const reason = readiness(); if (reason) { message('request-status', reason, true); return; }
    const request = {sent: false, capture: null, interval: null, op: null};
    state.pending = request; state.preparing = true; controls(); renderReferences();
    message('request-status', ''); showErrorDetails(null);
    let capture = null, op = null;
    try {
      const auth = authCapture(), p = model(), originalInfo = state.info && {...state.info};
      const mode = submissionMode(state.mode, originalInfo);
      capture = {auth, requestId: crypto.randomUUID(), tokenId: Number(state.tokenId), modelId: p.id,
        contractRevision:p.revision, localStrategy:p.edit?.local_strategy || 'unavailable',
        intent:intentFor(mode,!!originalInfo?.hasAnnotations,state.references.map(x=>x.role)), prompt: state.prompt, parameters: parametersFor(p, mode, inputs(), mode === 'generate' ? null : originalInfo),
        source: mode === 'generate' ? null : state.source, versionName: mode === 'generate' ? '文生图' : currentVersion()?.name || '主图',
        references: Object.freeze(mode === 'generate' ? [] : state.references.map(x => Object.freeze({blob: x.blob, width: x.width, height: x.height, role:x.role}))), snapshot: null};
      op = operation(auth, REQUEST_TIMEOUT, request); request.op = op;
      if (mode !== 'generate') {
        capture.snapshot = await op.wait(state.editor.snapshot({size: D.snapshotSizeFor(capture.parameters,originalInfo), local: mode === 'local',
          guideSelection:mode === 'local' && capture.localStrategy === 'reference-only'}));
        if (capture.snapshot.documentId !== originalInfo.documentId || capture.snapshot.revision !== originalInfo.revision) {
          throw new Error('主图或选区已变化，请确认当前画布后重新提交。');
        }
        if (mode === 'whole' && capture.snapshot.mask) throw new Error('整图编辑快照不应包含修改遮罩。');
        validateSnapshot(capture.snapshot, capture.references, p, capture.intent);
      }
      request.capture = Object.freeze(capture);
      const body = R.makeBody(capture);
      op.assert(); request.sent = true; state.preparing = false; controls(); renderReferences(); startElapsed(request);
      let data = await json(mode === 'generate' ? 'generations' : 'edits', body, op);
      await results.finish(capture,data,state.editor,op);
      data = null; renderCandidates();
      message('request-status', '结果已加入候选区。');
    } catch (error) {
      if (!state.active || error instanceof Cancelled) return;
      showErrorDetails(error,capture?.requestId);
      const unknown = request.sent && (!(error instanceof RequestFailure) || error.uncertain);
      if (unknown) {
        state.uncertain = {id: capture?.requestId, message: clean(error.message || '连接中断，结果未知。')};
        $('uncertain').hidden = false; $('uncertain-message').textContent = `${state.uncertain.message}${/核对用量/.test(state.uncertain.message) ? '' : ' 请先核对用量，不要重复提交。'}`;
        $('uncertain-id').textContent = capture.requestId; message('request-status', '');
      } else message('request-status', canvasErrors[error.code] || error.message, true);
    } finally {
      op?.dispose(); if (request.interval !== null) clearInterval(request.interval);
      request.capture = null; request.op = null;
      if (state.pending === request) { state.pending = null; state.preparing = false; controls(); renderReferences(); }
    }
  }
  const {selectedBlob,selectedURL} = Results;
  const selectedLabel = c => c.selected === 'composite' && c.composite ? '局部合成结果' : '模型原始结果';
  const applyingWhole = c => c.capture.intent.operation === 'local' && (c.selected !== 'composite' || !c.composite);
  function download(candidate) {
    if (!state.active) return;
    const anchor = node('a'); anchor.href = selectedURL(candidate);
    const type = selectedBlob(candidate).type;
    anchor.download = `studio-${candidate.id.slice(0, 8)}-${candidate.selected}.${type === 'image/jpeg' ? 'jpg' : type === 'image/webp' ? 'webp' : 'png'}`;
    document.body.append(anchor); anchor.click(); anchor.remove();
  }
  async function adopt(candidate, continuing) {
    if (!state.active || state.preparing || state.loadingImage) return;
    await loadSource(selectedBlob(candidate), `候选 · ${selectedLabel(candidate)}`);
    if (!state.active) return;
    closePreview(); setMode('edit');
    message('request-status', continuing ? '已作为主图，写下下一步改法。' : '已采用为主图。');
    if (continuing) $('prompt').focus();
  }
  function discard(candidate) {
    if (state.previewId === candidate.id) closePreview();
    results.discard(candidate); renderCandidates(); controls();
  }
  function actionButton(text, fn) {
    const button = node('button', text); button.type = 'button'; button.disabled = !state.active;
    bind(button, 'click', fn); return button;
  }
  function renderCandidates() {
    $('candidates').replaceChildren(...state.candidates.map((c, index) => {
      const article = node('article', undefined, 'studio-candidate');
      const preview = actionButton('', e => openPreview(c, e.currentTarget)); preview.className = 'studio-candidate-preview';
      preview.setAttribute('aria-label', `放大预览候选 ${index + 1}`);
      const img = node('img'); img.src = selectedURL(c); img.alt = `候选 ${index + 1}：${selectedLabel(c)}`; preview.append(img);
      const heading = node('div', undefined, 'studio-candidate-heading');
      const remove = actionButton('×', () => discard(c)); remove.className = 'icon-button button-small button-ghost'; remove.setAttribute('aria-label', `移除候选 ${index + 1}`);
      heading.append(node('strong', `候选 ${index + 1}`), remove);
      const changed = c.capture.source && c.capture.snapshot.documentId !== state.info?.documentId;
      const summary = node('p', `${selectedLabel(c)} · ${c.capture.versionName}${changed ? ' · 非当前主图' : ''}\n${c.capture.modelId}${c.duration === null ? '' : ` · ${Math.round(c.duration)} 秒`}`);
      const actions = node('div', undefined, 'studio-candidate-actions');
      actions.append(actionButton('预览', e => openPreview(c, e.currentTarget)), actionButton(applyingWhole(c) ? '整图应用' : '应用', () => adopt(c, false)),
        actionButton(applyingWhole(c) ? '整图应用并编辑' : '继续编辑', () => adopt(c, true)), actionButton('下载', () => download(c)));
      article.append(preview, heading, summary, actions); return article;
    }));
    $('result-count').textContent = `${state.candidates.length} / ${MAX_CANDIDATES}`;
    $('results-empty').hidden = !!state.candidates.length;
  }
  let lightbox = null;
  function navigatePreview(direction) {
    const index = state.candidates.findIndex(c => c.id === state.previewId), next = state.candidates[index + direction];
    if (!next || !state.active) return;
    state.previewId = next.id; state.comparing = false; $('compare').checked = false;
    lightbox?.reset(); updatePreview(); controls();
  }
  const previewCandidate = () => state.candidates.find(x => x.id === state.previewId);
  function updatePreview() {
    const c = previewCandidate(); if (!c) return;
    const url = selectedURL(c);
    if ($('preview-result').src !== url) lightbox?.reset();
    $('preview-previous').disabled = state.candidates[0]?.id === c.id;
    $('preview-next').disabled = state.candidates.at(-1)?.id === c.id;
    $('preview-result').src = url; $('preview-result').alt = selectedLabel(c);
    $('preview-result-label').textContent = selectedLabel(c);
    $('preview-adopt').textContent = applyingWhole(c) ? '整图应用为新版本' : '应用为新版本';
    $('preview-continue').textContent = applyingWhole(c) ? '整图应用并编辑' : '应用并继续编辑';
    $('show-original').setAttribute('aria-pressed', String(c.selected === 'original'));
    $('show-composite').setAttribute('aria-pressed', String(c.selected === 'composite')); $('show-composite').disabled = !c.composite;
    $('compare').disabled = !c.capture.source;
    const comparing = !!c.capture.source && state.comparing;
    $('preview-source').hidden=!comparing;
    $('preview-source-panel').hidden = !comparing; $('preview-images').classList.toggle('is-comparing', comparing);
    if (comparing) $('preview-source').src = c.sourceURL; else $('preview-source').removeAttribute('src');
    message('composite-reason', c.compositeReason);
    $('preview-info').textContent = `${c.capture.versionName} · ${c.capture.modelId} · 请求 ${c.id}`;
  }
  function openPreview(candidate, trigger) {
    if (!state.active) return;
    if (typeof $('preview').showModal !== 'function') throw new Error('浏览器不支持图片预览弹窗，请更新浏览器。');
    previewTrigger = trigger || document.activeElement; previewBackdropPressed = false;
    closeSettings(false);
    state.previewId = candidate.id; state.comparing = false; $('compare').checked = false;
    $('preview').hidden = false; updatePreview(); controls();
    try { if (!$('preview').open) $('preview').showModal(); }
    catch (error) { closePreview(false); throw error; }
    lightbox?.setActive(true); $('preview-close').focus();
  }
  function closePreview(returnFocus = true) {
    lightbox?.setActive(false);
    const id = state.previewId, trigger = previewTrigger;
    state.previewId = ''; state.comparing = false; previewTrigger = null; previewBackdropPressed = false;
    if ($('preview').open) $('preview').close();
    $('preview').hidden = true; $('compare').checked = false;
    $('preview-source').removeAttribute('src'); $('preview-result').removeAttribute('src');
    $('preview-info').textContent = ''; $('composite-reason').textContent = ''; controls();
    if (returnFocus && state.active && id) {
      const index = state.candidates.findIndex(c => c.id === id);
      const target = trigger?.isConnected && !trigger.disabled ? trigger : $('candidates').children[index]?.children[0];
      (target || $('dropzone')).focus();
    }
  }
  function outsidePreview(event) {
    const rect = $('preview').getBoundingClientRect();
    return event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom;
  }
  const narrow = window.matchMedia('(max-width: 1050px)');
  function openSettings() {
    if (!state.active || !narrow.matches) return;
    $('inspector').classList.toggle('is-open', true); $('inspector').inert = false;
    $('inspector').setAttribute('aria-modal', 'true'); $('inspector').setAttribute('role', 'dialog');
    $('open-settings').setAttribute('aria-expanded', 'true'); $('settings-backdrop').hidden = false; $('close-settings').focus();
  }
  function closeSettings(returnFocus = true) {
    $('inspector').classList.toggle('is-open', false); $('inspector').inert = narrow.matches;
    $('inspector').removeAttribute('aria-modal'); $('inspector').removeAttribute('role');
    $('open-settings').setAttribute('aria-expanded', 'false'); $('settings-backdrop').hidden = true;
    if (returnFocus && narrow.matches) $('open-settings').focus();
  }
  function shutdown(reason) {
    if (!state.active) return;
    state.active = false; state.epoch++; state.tokenEpoch++;
    requestClient.close();
    if (state.pending?.interval !== null && state.pending?.interval !== undefined) clearInterval(state.pending.interval);
    if (state.pending) { state.pending.capture = null; state.pending = null; }
    closePreview(false); closeSettings(false);
    results.clear();
    lightbox?.destroy(); state.editor?.destroy(); state.editor = null; $('canvas').replaceChildren();
    state.source = null; state.info = null; state.references = [];
    state.tokens = []; state.models = []; state.tokenId = ''; state.modelId = ''; state.versionId = '';
    state.prompt=''; showErrorDetails(null);
    state.preparing = false; state.loadingImage = false; state.catalogBusy = false; state.uncertain = null;
    for (const id of ['prompt', 'image-input', 'reference-input']) $(id).value = '';
    $('token').replaceChildren(option('', '请刷新页面')); $('model').replaceChildren(option('', '请刷新页面'));
    $('uncertain').hidden = true; $('uncertain-id').textContent = ''; $('uncertain-message').textContent = ''; $('elapsed').textContent = '';
    renderVersions(); renderReferences(); renderCandidates(); populateProfiles();
    message('request-status', ''); message('catalog-status', reason);
    sessionObserver?.disconnect(); composerObserver?.disconnect();
  }
  function showErrorDetails(error,requestId) {
    const details=R.safeDetails({...error?.details,request_id:requestId});
    $('error-details').hidden=!error || !Object.keys(details).length;
    $('error-details').open=false;
    $('error-details-text').textContent=error ? Object.entries(details).map(([key,value])=>`${key}: ${value}`).join('\n') : '';
  }
  function newDocument() {
    if (!state.active || state.preparing || state.loadingImage || !state.editor?.clear) return;
    state.editor.clear({preserveSnapshots:!!state.pending?.sent}); state.source=null; state.info=null; state.versionId=''; state.documentEpoch++;
    for (const ref of state.references) revoke(ref.url);
    state.references=[];state.prompt='';$('prompt').value='';setMode('create');
    renderReferences();renderVersions();renderCandidates();controls();$('prompt').focus();
  }
  bind($('new'),'click',newDocument);
  bind($('download'),'click',() => {
    if (!state.active || !state.source) return;
    const url=urlFor(state.source),anchor=node('a');anchor.href=url;
    anchor.download=`studio-current.${state.source.type==='image/jpeg' ? 'jpg' : state.source.type==='image/webp' ? 'webp' : 'png'}`;
    document.body.append(anchor);anchor.click();anchor.remove();setTimeout(()=>revoke(url),1000);
  });
  for (const [id,method] of [['mask','setSelectionVisible'],['annotations','setAnnotationsVisible']]) {
    bind($(`show-${id}`),'change',() => {
      if (!state.active || state.preparing || !state.editor?.[method]) return;
      state.visibility[id]=$(`show-${id}`).checked;state.editor[method](state.visibility[id]);controls();
    });
  }
  let sessionObserver = null;
  // Geometry only: the document/request remain independent from layout. The
  // drawer follows the composer's actual wrapped height, not a fixed estimate.
  const composerObserver = typeof ResizeObserver === 'function' ? new ResizeObserver(() => {
    const height=Math.ceil($('form').getBoundingClientRect().height);
    $('page').style.setProperty('--studio-composer-height',`${height}px`);
  }) : null;
  composerObserver?.observe($('form'));
  bind($('token'), 'change', () => {state.tokenId=$('token').value;return loadModels();});
  bind($('model'), 'change', () => { if (!state.active) return; state.modelId = $('model').value; populateProfiles(); });
  bind($('refresh'), 'click', loadTokens);
  for (const el of modes) bind(el, 'click', () => setMode(el.dataset.mode));
  for (const el of tools) bind(el, 'click', () => { if (!el.disabled) setTool(el.dataset.tool); });
  bind($('select-region'), 'click', () => {
    if ($('select-region').hidden || $('select-region').disabled || !state.active) return;
    setTool('brush'); closeSettings(false); $('dropzone').focus();
  });
  for (const id of ['prompt', 'size', 'quality', 'format', 'background', 'fidelity', 'width', 'height', 'compression']) {
    bind($(id), id === 'prompt' || id === 'width' || id === 'height' || id === 'compression' ? 'input' : 'change', () => {if(id==='prompt') state.prompt=$(id).value; else state.inputs={...state.inputs,[id]:$(id).value};controls();});
  }
  bind($('compression'), 'input', () => { $('compression-value').textContent = $('compression').value; });
  const engineControls = {undo: () => state.editor.undo(), redo: () => state.editor.redo(), fit: () => state.editor.fit(),
    'zoom-in': () => state.editor.zoomBy(1.25), 'zoom-out': () => state.editor.zoomBy(.8),
    'delete-guide': () => state.editor.deleteSelected(), 'clear-mask': () => state.editor.clearMask(), 'clear-guides': () => state.editor.clearAnnotations()};
  for (const [id, fn] of Object.entries(engineControls)) bind($(id), 'click', () => { if (!$(id).disabled && state.editor) fn(); });
  bind($('brush-size'), 'input', () => { if (state.active && !state.preparing) { state.editor?.setBrushSize(Number($('brush-size').value)); $('brush-value').textContent = `${$('brush-size').value} px`; } });
  bind($('feather'), 'input', () => { if (state.active && !state.preparing) { state.editor?.setFeather?.(Number($('feather').value)); $('feather-value').textContent = `${$('feather').value} px`; } });
  bind($('color'), 'input', () => { if (state.active && !state.preparing) state.editor?.setColor($('color').value); });
  bind($('stroke-width'), 'input', () => { const width = Number($('stroke-width').value); if (state.active && !state.preparing && width >= 1 && width <= 64) state.editor?.setStrokeWidth(width); });
  for (const id of ['upload', 'empty-upload']) bind($(id), 'click', () => { if (!$(id).disabled) $('image-input').click(); });
  bind($('image-input'), 'change', async () => { const files = [...($('image-input').files || [])]; $('image-input').value = ''; await importPrimary(files); });
  bind($('add-reference'), 'click', () => { if (!$('add-reference').disabled) $('reference-input').click(); });
  bind($('reference-input'), 'change', async () => { const files = [...($('reference-input').files || [])]; $('reference-input').value = ''; await addReferences(files); });
  bind($('version'), 'change', async () => { const version = state.versions.find(x => x.id === $('version').value); if (version) await loadSource(version.blob, version.name, version); });
  bind($('dropzone'), 'dragover', e => { e.preventDefault(); if (state.active && !state.preparing) $('dropzone').classList.toggle('is-dragging', true); });
  bind($('dropzone'), 'dragleave', () => $('dropzone').classList.toggle('is-dragging', false));
  bind($('dropzone'), 'drop', async e => { e.preventDefault(); $('dropzone').classList.toggle('is-dragging', false); await importPrimary([...(e.dataTransfer?.files || [])]); });
  document.addEventListener('paste', e => {
    if (!state.active || state.previewId || state.preparing || state.loadingImage || e.target?.isContentEditable
        || ['INPUT', 'TEXTAREA', 'SELECT'].includes(e.target?.tagName)) return;
    const files = [...(e.clipboardData?.items || [])].filter(x => x.kind === 'file' && MIME.has(x.type)).map(x => x.getAsFile()).filter(Boolean);
    if (!files.length) return; e.preventDefault();
    importPrimary(files).catch(error => { if (state.active && !(error instanceof Cancelled)) message('request-status', canvasErrors[error.code] || error.message, true); });
  });
  bind($('dropzone'), 'keydown', e => {
    if (e.defaultPrevented || !state.active || state.preparing || state.previewId || !state.source || e.target?.isContentEditable
        || ['INPUT', 'TEXTAREA', 'SELECT', 'BUTTON'].includes(e.target?.tagName)) return;
    let action;
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'z') action = e.shiftKey ? engineControls.redo : engineControls.undo;
    else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'y') action = engineControls.redo;
    else if (e.key === '+' || e.key === '=') action = engineControls['zoom-in'];
    else if (e.key === '-') action = engineControls['zoom-out']; else if (e.key === '0') action = engineControls.fit;
    if (action) { e.preventDefault(); action(); }
  });
  bind($('form'), 'submit', submit);
  bind($('stop-wait'), 'click', () => state.pending?.op?.controller.abort());
  bind($('acknowledge'), 'click', () => { if (!state.active || state.pending) return; state.uncertain = null; $('uncertain').hidden = true; $('uncertain-message').textContent = ''; $('uncertain-id').textContent = ''; controls(); });
  if (window.CockpitStudioLightbox?.create) lightbox = window.CockpitStudioLightbox.create([
    {host: $('preview-result-viewport'), image: $('preview-result')},
    {host: $('preview-source-viewport'), image: $('preview-source')},
  ], {onZoom(scale) { $('preview-zoom').textContent = Math.round(scale * 100) + '%'; }, onNavigate: navigatePreview});
  bind($('preview-previous'), 'click', () => navigatePreview(-1));
  bind($('preview-next'), 'click', () => navigatePreview(1));
  bind($('preview-zoom-in'), 'click', () => lightbox?.zoomBy(1.15));
  bind($('preview-zoom-out'), 'click', () => lightbox?.zoomBy(1 / 1.15));
  bind($('preview-fit'), 'click', () => lightbox?.reset());
  bind($('preview'), 'keydown', e => lightbox?.keyDown(e));
  bind($('preview-close'), 'click', () => closePreview());
  bind($('preview'), 'cancel', e => { e.preventDefault(); closePreview(); });
  bind($('preview'), 'close', () => { if (!$('preview').open && state.previewId) closePreview(); });
  bind($('preview'), 'pointerdown', e => { previewBackdropPressed = e.target === $('preview') && outsidePreview(e); });
  bind($('preview'), 'pointercancel', () => { previewBackdropPressed = false; });
  bind($('preview'), 'click', e => {
    const dismiss = previewBackdropPressed && e.target === $('preview') && outsidePreview(e);
    previewBackdropPressed = false; if (dismiss) closePreview();
  });
  for (const kind of ['original', 'composite']) bind($(`show-${kind}`), 'click', () => {
    const c = previewCandidate(); if (!c || (kind === 'composite' && !c.composite)) return; c.selected = kind; updatePreview(); renderCandidates();
  });
  bind($('compare'), 'change', () => {state.comparing=$('compare').checked; updatePreview();});
  bind($('preview-download'), 'click', () => { const c = previewCandidate(); if (c) download(c); });
  bind($('preview-adopt'), 'click', async () => { const c = previewCandidate(); if (c) await adopt(c, false); });
  bind($('preview-continue'), 'click', async () => { const c = previewCandidate(); if (c) await adopt(c, true); });
  bind($('open-settings'), 'click', openSettings);
  bind($('close-settings'), 'click', () => closeSettings());
  bind($('settings-backdrop'), 'click', () => closeSettings());
  bind($('inspector'), 'keydown', e => {
    if (!narrow.matches || !$('inspector').classList.contains('is-open')) return;
    if (e.key === 'Escape') { e.preventDefault(); closeSettings(); }
    if (e.key !== 'Tab') return;
    const items = [...$('inspector').querySelectorAll('button:not(:disabled),input:not(:disabled),select:not(:disabled),textarea:not(:disabled),a[href]')]
      .filter(x => x.getClientRects().length && x.tabIndex !== -1);
    const first = items[0], last = items.at(-1); if (!first) return;
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  });
  narrow.addEventListener('change', () => closeSettings(false)); closeSettings(false);
  window.addEventListener('cockpit-auth-changed', () => shutdown('登录已变化，画布与候选已清空。请刷新页面。'));
  window.addEventListener('pagehide', () => shutdown('请刷新页面重新进入工作站。'));
  window.addEventListener('pageshow', e => { if (e.persisted) message('catalog-status', '请刷新页面重新进入工作站。'); });
  const meta = document.querySelector('meta[name="cockpit-session"]');
  if (meta && typeof MutationObserver === 'function') {
    sessionObserver = new MutationObserver(() => { if (session() !== initialSession) shutdown('登录已变化，画布与候选已清空。请刷新页面。'); });
    sessionObserver.observe(meta, {attributes: true, attributeFilter: ['content']});
  }
  try {
    if (!window.CockpitImageCanvas?.create) throw new Error('画布组件未加载，请刷新页面。');
    state.editor = window.CockpitImageCanvas.create($('canvas'), {onChange(info) {
      if (!state.active) return; state.info = info;
      if (info.selected) {
        $('color').value = info.selected.color; $('stroke-width').value = info.selected.width;
      }
      if (typeof info.selectionVisible === 'boolean') state.visibility.mask=info.selectionVisible;
      if (typeof info.annotationsVisible === 'boolean') state.visibility.annotations=info.annotationsVisible;
      if (info.error) message('request-status', canvasErrors[info.errorCode] || '画布操作失败，请调整选区或标注。', true);
      controls();
    }});
    state.editor.setTool('pan'); state.editor.setBrushSize(40); state.editor.setColor('#e5484d'); state.editor.setStrokeWidth(4);
    $('feather-control').hidden = typeof state.editor.setFeather !== 'function';
  } catch (error) { message('request-status', error.message, true); }
  controls(); loadTokens();
})();
