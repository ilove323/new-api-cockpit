/* Offline UI/contract fixtures: node --test tests/test_studio_frontend.cjs
 * No account, HTTP server, paid model, clipboard, image persistence or deployment.
 */
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.join(__dirname, '../src/new_api_cockpit');
const source = fs.readFileSync(path.join(root, 'static/studio.js'), 'utf8');
const html = fs.readFileSync(path.join(root, 'templates/studio.html'), 'utf8');
const css = fs.readFileSync(path.join(root, 'static/studio.css'), 'utf8');
const api = require(path.join(root, 'static/studio-domain.js'));
const SECRET = 'sk-fixture-secret-never-render';
const tokens = [{id: 1, name: '工作 KEY', group: 'GPT', key_masked: 'sk-ab****yz', usable: true},
  {id: 2, name: '已停用', group: 'GPT', usable: false, unavailable_reason: '额度不足'},
  {id: 3, name: '不能列出', group: 'default', usable: true},
  {id: 4, name: '备用 KEY', group: 'GPT', key_masked: SECRET, usable: true}];
const parameters = overrides => ({sizes:['auto','1024x1024','1536x1024','1024x1536'],custom_size:false,
  qualities:['auto','low','medium','high'],formats:['png','jpeg','webp'],backgrounds:['auto','opaque','transparent'],
  input_fidelity:['low','high'],allowed_fields:['size','quality','output_format','background','output_compression','input_fidelity'],...overrides});
const profile = (id='gpt-image-fixture',overrides={}) => ({id,revision:'a'.repeat(64),
  generation:{parameters:parameters()},edit:{parameters:parameters(),local_strategy:'native-mask',max_images:4},...overrides});
const models=[profile(),profile('gpt-image-modern',{generation:{parameters:parameters({sizes:['auto'],custom_size:true})},
  edit:{parameters:parameters({sizes:['auto'],custom_size:true,input_fidelity:[],allowed_fields:['size','quality','output_format','background','output_compression']}),local_strategy:'reference-only',max_images:4}})];
const defaults = {size: 'auto', quality: 'auto', format: 'png', background: 'auto', fidelity: 'low', compression: '90', width: '1024', height: '1024'};
const reply = (data, status = 200) => ({ok: status >= 200 && status < 300, status, json: async () => data});
const deferred = () => { let resolve, reject; const promise = new Promise((r, j) => { resolve = r; reject = j; }); return {promise, resolve, reject}; };
const settle = async () => { for (let i = 0; i < 5; i++) await new Promise(resolve => setImmediate(resolve)); };
const image = (name = '主图.png', width = 1024, height = 1024, type = 'image/png') => Object.assign(new Blob(['image'], {type}), {name, width, height});
class Element {
  constructor(tag = 'div') {
    this.style = {}; this.tagName = tag.toUpperCase(); this.children = []; this.attrs = {}; this.events = {};
    this.value = ''; this.textContent = ''; this.hidden = false; this.disabled = false; this.dataset = {};
    this.checked = false; this.inert = false; this.open = false; this.tabIndex = 0; this.classes = new Set();
    this.classList = {contains: x => this.classes.has(x), toggle: (x, force) => {
      const on = force === undefined ? !this.classes.has(x) : force;
      if (on) this.classes.add(x); else this.classes.delete(x);
    }};
  }
  set innerHTML(_) { throw new Error('No innerHTML in Studio'); }
  setAttribute(name, value) { this.attrs[name] = String(value); }
  removeAttribute(name) { delete this.attrs[name]; if (name === 'src') delete this.src; }
  setConnected(value) { this.isConnected = value; for (const child of this.children) child.setConnected(value); }
  append(...items) { this.children.push(...items); for (const child of items) child.setConnected(true); }
  replaceChildren(...items) { for (const child of this.children) child.setConnected(false); this.children = []; this.append(...items); this.textContent = ''; if (this.tagName === 'SELECT') this.value = items[0]?.value || ''; }
  addEventListener(name, fn) { (this.events[name] ??= []).push(fn); }
  removeEventListener(name, fn) {this.events[name] = (this.events[name] || []).filter(f => f !== fn);}
  async fire(name, detail = {}) { for (const fn of this.events[name] || []) await fn({target: this, currentTarget: this, preventDefault() { this.defaultPrevented = true; }, ...detail}); }
  querySelectorAll() { return this.focusable || []; }
  getClientRects() { return this.hidden ? [] : [{}]; }
  getBoundingClientRect() { return {left:100,top:100,right:900,bottom:700,width:800,height:600}; }
  focus() { if (this.doc) this.doc.activeElement = this; }
  click() { this.clicked = true; this.fire('click'); }
  remove() { this.removed = true; this.setConnected(false); }
  showModal() { this.open = true; this.modalCalls = (this.modalCalls || 0) + 1; }
  close() { this.open = false; this.fire('close'); }
}
async function harness({missingEngine = false, narrow = false, catalogModels = models, catalogTokens = tokens} = {}) {
  const elements = new Map([...html.matchAll(/<([a-z][\w-]*)[^>]*\bid="(studio-[^"]+)"([^>]*)>/g)]
    .map(m => { const el = new Element(m[1]); el.hidden = /\bhidden\b/.test(m[3]); el.disabled = /\bdisabled\b/.test(m[3]);
      el.value = /\bvalue="([^"]*)"/.exec(m[3])?.[1] || ''; el.dataset.mode = /data-mode="([^"]+)"/.exec(m[3])?.[1]; return [m[2], el]; }));
  const tools = [...html.matchAll(/tool\('([^']+)', '([^']+)', '([^']+)'\)/g)].map(m => {
    const el = new Element('button'); el.dataset = {tool: m[1], drawing: m[3]}; el.disabled = true; return el;
  });
  const modeElements = [...elements.values()].filter(x => x.dataset.mode);
  const events = {}, documentEvents = {}, timers = new Map(), intervals = new Map(), calls = [], created = [], revoked = [],
    loads = [], engineCalls = [], snapshots = [], compositions = [], forbidden = [], observers = [], downloads = [];
  let serial = 0, timerSerial = 0, urlSerial = 0, docSerial = 0;
  const meta = {content: 'fixture-session'}, body = new Element('body'), doc = {body, activeElement: null,
    getElementById: id => elements.get(id), querySelector: q => q === 'meta[name="cockpit-session"]' ? meta : null,
    querySelectorAll: q => q === '[data-tool]' ? tools : q === '[data-mode]' ? modeElements : [],
    createElement(tag) { const el = new Element(tag); el.doc = doc; if (tag === 'a') downloads.push(el); return el; },
    addEventListener(name, fn) { (documentEvents[name] ??= []).push(fn); }};
  for (const el of [...elements.values(), ...tools]) el.doc = doc;
  elements.get('studio-inspector').focusable = ['studio-close-settings', 'studio-size', 'studio-add-reference'].map(id => elements.get(id));
  const media = {matches: narrow, addEventListener(name, fn) { this.change = fn; }};
  const h = {elements, events, documentEvents, timers, intervals, calls, created, revoked, loads, engineCalls, snapshots,
    compositions, forbidden, observers, downloads, tools, media, meta, doc, resultSize: {width: 1024, height: 1024},
    info: {width: 0, height: 0, documentId: null, revision: 0, hasMask: false, hasAnnotations: false},
    el: id => elements.get(`studio-${id}`),
    async fire(id, event = 'click', value, detail = {}) { if (value !== undefined) this.el(id).value = value; await this.el(id).fire(event, detail); await settle(); },
    async event(name, detail = {}) { for (const fn of events[name] || []) await fn(detail); await settle(); },
    async documentEvent(name, detail = {}) { for (const fn of documentEvents[name] || []) await fn(detail); await settle(); },
    async choose(id = 'gpt-image-fixture') { await this.fire('token', 'change', '1'); await this.fire('model', 'change', id); await this.fire('prompt', 'input', '把背景改成晴朗的海边'); },
    async upload(blob = image()) { this.el('image-input').files = [blob]; await this.fire('image-input', 'change'); },
    change(info) { this.info = {...this.info, ...info}; this.onChange({...this.info}); },
    async timeout(ms) { for (const {fn, ms: delay} of [...timers.values()]) if (delay === ms) fn(); await settle(); },
    dom() { const walk = el => [el.textContent, el.value, el.src, el.href, JSON.stringify(el.attrs), ...el.children.flatMap(walk)]; return [...elements.values()].flatMap(walk).filter(Boolean).join('\n'); },
    paid() { return calls.filter(x => /\/(generations|edits)$/.test(x.url)); },
    result(body) { return reply({images: [{b64_json: 'aW1hZ2U=', mime: 'image/png', ...this.resultSize}],
      request_id: body.request_id, model: body.model, duration_seconds: 2.5}); },
  };
  const tracked = name => (...args) => engineCalls.push({name, args});
  const editor = {async load(blob) {
    loads.push(blob); h.info = {width: blob.width || 1024, height: blob.height || 1024,
      documentId: `doc-${++docSerial}`, revision: 0, hasMask: false, hasAnnotations: false};
    h.onChange({...h.info}); return {...h.info};
  }, state: () => ({...h.info}), async snapshot(options) {
    const size = /^(\d+)x(\d+)$/.exec(options.size);
    const snapshot = Object.freeze({...h.info, image: image('working.png'),
      mask: options.local && h.info.hasMask ? image('mask.png') : null,
      guide: h.info.hasAnnotations || (options.local && options.guideSelection) ? image('guide.png') : null,
      working: {width: size ? Number(size[1]) : 1024, height: size ? Number(size[2]) : 1024, scale: 1, offsetX: 0, offsetY: 0}});
    snapshots.push({options, snapshot}); return h.snapshotHandler ? h.snapshotHandler(snapshot) : snapshot;
  }, async compose(blob, snapshot) { compositions.push({blob, snapshot}); return h.composeHandler ? h.composeHandler(blob, snapshot) : {blob: image('composite.png')}; },
    clear(options) {engineCalls.push({name:'clear',args:[options]});h.change({width:0,height:0,hasMask:false,hasAnnotations:false,documentId:null,revision:0});},
    destroy() { h.destroyed = true; }, exportPNG() { throw new Error('UI must not duplicate the engine source'); }};
  for (const name of ['setSelectionVisible','setAnnotationsVisible','setTool', 'setBrushSize', 'setColor', 'setStrokeWidth', 'setText', 'setFeather', 'undo', 'redo', 'clearMask', 'clearAnnotations', 'fit', 'zoomBy']) editor[name] = tracked(name);
  const ctx = {document: doc, Blob, FormData, AbortController, Uint8Array, atob, Date, Object, Math, Number,
    crypto: {randomUUID: () => `request-fixture-${++serial}`},
    URL: {createObjectURL(blob) { const url = `blob:fixture-${++urlSerial}`; created.push({url, blob}); return url; }, revokeObjectURL(url) { revoked.push(url); }},
    async createImageBitmap(blob) {
      if (h.decodeHandler) return h.decodeHandler(blob);
      return {width: blob.width || h.resultSize.width, height: blob.height || h.resultSize.height, close() {}};
    },
    matchMedia: () => media,
    MutationObserver: class { constructor(fn) { this.fn = fn; observers.push(this); } observe() {} disconnect() { this.disconnected = true; } },
    setTimeout(fn, ms) { const id = ++timerSerial; timers.set(id, {fn, ms}); return id; }, clearTimeout(id) { timers.delete(id); },
    setInterval(fn, ms) { const id = ++timerSerial; intervals.set(id, {fn, ms}); return id; }, clearInterval(id) { intervals.delete(id); },
    addEventListener(name, fn) { (events[name] ??= []).push(fn); },
    localStorage: {getItem() { forbidden.push('storage'); }, setItem() { forbidden.push('storage'); }},
    sessionStorage: {setItem() { forbidden.push('storage'); }}, console: {log() { forbidden.push('log'); }, error() { forbidden.push('log'); }},
    async fetch(url, init) {
      const multipart = init.body instanceof FormData;
      const data = init.body ? (multipart ? Object.fromEntries(init.body) : JSON.parse(init.body)) : undefined;
      const bridged = {...init, headers: {...init.headers, 'X-Cockpit-Session': meta.content}};
      calls.push({url, init, bridged, data});
      if (h.handler) return h.handler(url, init, data);
      if (url.endsWith('/tokens')) return reply({tokens: catalogTokens, token_manager_path: 'https://evil.invalid'});
      if (url.endsWith('/models')) return reply({models: catalogModels});
      return h.result(data);
    },
  };
  ctx.window = ctx; if (!missingEngine) ctx.CockpitImageCanvas = {create(host, options) { h.onChange = options.onChange; return editor; }};
  h.ctx = ctx; h.editor = editor;
  for (const file of ['studio-upstream','studio-lightbox','studio-domain','studio-request','studio-results']) vm.runInNewContext(fs.readFileSync(path.join(root,`static/${file}.js`),'utf8'),ctx,{filename:`${file}.js`});
  vm.runInNewContext(source, ctx, {filename: 'studio.js'}); await settle(); return h;
}

test('template shares the shell, local assets, accessible controls, no inline JS or styles', () => {
  for (const partial of ['auth', 'sidebar']) assert.match(html, new RegExp(`include "partials/${partial}\\.html"`));
  assert.match(html, /app_header\(site_name\)/); assert.match(html, /ui\.css/); assert.match(html, /navigation\.css/);
  assert.ok(html.indexOf('konva-10.7.1.min.js') < html.indexOf('studio-canvas.js'));
  assert.ok(html.indexOf('studio-canvas.js') < html.indexOf('static/studio.js'));
  assert.ok([...html.matchAll(/<script\b([^>]*)>/g)].every(m => /\bsrc=/.test(m[1])));
  assert.doesNotMatch(html, /\son\w+=|\sstyle=|https?:\/\//);
  assert.doesNotMatch(source, /innerHTML|localStorage|sessionStorage|fetchOnce|rawFetch|\/v1\/|response_format|setInterval\([^]*fetch/);
  assert.match(html, /maxlength="32000"/); assert.equal((html.match(/max="3840"/g) || []).length, 2);
  const ids = [...html.matchAll(/\bid="([^"]+)"/g)].map(m => m[1]); assert.equal(ids.length, new Set(ids).size);
  assert.match(html, /aria-live="polite"/); assert.match(html, /aria-pressed="false"/); assert.match(css, /var\(--ui-primary\)/);
  assert.match(css, /max-width: 1050px/); assert.match(css, /max-width: 600px/);
  assert.doesNotMatch(html + source, /文档已核对|有限支持|资料核对不等于|进度百分比/);
});
test('viewport layout reserves composer space instead of fixed overlay and removes legacy form CSS',()=>{
  const page=css.match(/\.app-layout > \.studio-page\s*\{([^}]+)\}/)[1];
  assert.match(page,/grid-template-rows:\s*auto minmax\(0, 1fr\) auto auto/);
  assert.match(page,/height:\s*calc\(100dvh - var\(--app-header-height\)\)/);
  const composer=css.match(/\.studio-composer\s*\{([^}]+)\}/)[1];
  assert.match(composer,/position:\s*relative/);assert.doesNotMatch(composer,/position:\s*fixed/);
  assert.doesNotMatch(css,/\.studio-inspector form/);
  assert.equal((css.match(/\.studio-preview \{/g)||[]).length,2,'one base preview rule and one mobile adjustment');
});
test('canvas geometry is preserved in preview and src-less preview images remain hidden',async()=>{
  const h=await harness();await h.choose();await h.upload();
  assert.equal(h.el('preview').hidden,true);assert.equal(h.el('preview-result').hidden,true);
  assert.equal(h.el('preview-source').hidden,true);assert.equal(h.el('canvas').hidden,false);
  await h.fire('form','submit');await h.el('candidates').children[0].children[0].fire('click');await settle();
  assert.equal(h.el('preview').tagName,'DIALOG');assert.equal(h.el('preview').open,true);
  assert.equal(h.el('preview').modalCalls,1);
  assert.equal(h.el('page').classList.contains('is-previewing'),false);
  assert.equal(h.el('canvas').classList.contains('is-previewing'),false);
  assert.equal(h.el('canvas').hidden,false);assert.equal(h.el('preview').inert,false);
  assert.equal(h.el('preview-result').hidden,false);assert.equal(h.el('preview-source').hidden,true);
  h.el('compare').checked=true;await h.fire('compare','change');assert.equal(h.el('preview-source').hidden,false);
  h.el('compare').checked=false;await h.fire('compare','change');
  assert.equal(h.el('preview-source').hidden,true);
  await h.fire('preview-close');assert.equal(h.el('preview').inert,true);
  assert.equal(h.el('preview').open,false);
  assert.equal(h.el('preview-result').hidden,true);assert.equal(h.el('preview-source').hidden,true);
  assert.equal(h.el('canvas').classList.contains('is-previewing'),false);
  assert.equal(h.el('page').classList.contains('is-previewing'),false);assert.equal(h.el('canvas').hidden,false);
  assert.equal(h.loads.length,1);assert.equal(h.paid().length,1);
});
test('preview is a viewport-sized top-layer dialog outside the workbench, not a canvas replacement',()=>{
  assert.match(html,/<dialog id="studio-preview"[^>]*aria-modal="true"/);
  assert.ok(html.indexOf('<dialog id="studio-preview"')>html.indexOf('</main>'));
  const preview=css.match(/\.studio-preview\s*\{([^}]+)\}/)[1];
  assert.match(preview,/position:\s*fixed/);assert.match(preview,/100vw/);assert.match(preview,/100dvh/);
  assert.match(css,/\.studio-preview::backdrop/);assert.match(css,/\.studio-preview\[open\]/);
  assert.doesNotMatch(css,/\.studio-page\.is-previewing|\.studio-canvas\.is-previewing/);
});
test('both preview entry points open a modal; closing returns focus without applying or dispatching',async()=>{
  const h=await harness();await h.choose();await h.fire('form','submit');
  const card=h.el('candidates').children[0],thumbnail=card.children[0],button=card.children[3].children[0];
  for(const trigger of [thumbnail,button]) {
    await trigger.fire('click');await settle();assert.equal(h.el('preview').open,true);
    assert.equal(h.doc.activeElement,h.el('preview-close'));
    await h.fire('preview-close');assert.equal(h.doc.activeElement,trigger);
    assert.equal(h.el('preview').open,false);assert.equal(h.el('preview-result').src,undefined);
  }
  assert.equal(h.loads.length,0);assert.equal(h.paid().length,1);
});
test('source-backed preview zoom and candidate switching do not apply images or dispatch again',async()=>{
  const h=await harness();await h.choose();await h.fire('form','submit');await h.fire('form','submit');
  const cards=h.el('candidates').children,urls=cards.map(card=>card.children[0].children[0].src);
  await cards[0].children[0].fire('click');
  assert.equal(h.el('preview-result').src,urls[0]);assert.equal(h.el('preview-previous').disabled,true);
  await h.fire('preview-zoom-in');assert.equal(h.el('preview-zoom').textContent,'115%');
  await h.fire('preview-next');assert.equal(h.el('preview-result').src,urls[1]);
  assert.equal(h.el('preview-zoom').textContent,'100%');assert.equal(h.el('preview-next').disabled,true);
  await h.fire('preview','keydown',undefined,{key:'ArrowLeft',target:h.el('preview-result-viewport')});
  assert.equal(h.el('preview-result').src,urls[0]);
  await h.fire('preview-close');assert.equal(h.loads.length,0);assert.equal(h.paid().length,2);
});
test('Escape, native close and backdrop synchronize preview state; an inside-to-outside drag does not dismiss',async()=>{
  const h=await harness();await h.choose();await h.fire('form','submit');
  const thumbnail=h.el('candidates').children[0].children[0];
  const open=async()=>{await thumbnail.fire('click');await settle();assert.equal(h.el('preview').open,true);};
  await open();await h.fire('preview','cancel');assert.equal(h.el('preview').hidden,true);assert.equal(h.doc.activeElement,thumbnail);
  await open();h.el('preview').close();await settle();assert.equal(h.el('preview').hidden,true);
  await open();await h.fire('preview','close');assert.equal(h.el('preview').open,true,'A queued old close event cannot close a reopened modal');
  await h.fire('preview','pointerdown',undefined,{clientX:300,clientY:300});
  await h.fire('preview','click',undefined,{clientX:10,clientY:10});assert.equal(h.el('preview').open,true);
  await h.fire('preview','pointerdown',undefined,{clientX:10,clientY:10});
  await h.fire('preview','pointercancel');await h.fire('preview','click',undefined,{clientX:10,clientY:10});
  assert.equal(h.el('preview').open,true);
  await h.fire('preview','pointerdown',undefined,{clientX:10,clientY:10});
  await h.fire('preview','click',undefined,{clientX:10,clientY:10});assert.equal(h.el('preview').open,false);
  assert.equal(h.doc.activeElement,thumbnail);assert.equal(h.loads.length,0);assert.equal(h.paid().length,1);
});
test('modal switch retains candidate focus fallback and blocks document image paste; logout closes the modal',async()=>{
  const h=await harness();await h.choose();await h.upload();h.change({hasMask:true});await h.fire('form','submit');
  await h.el('candidates').children[0].children[0].fire('click');await settle();await h.fire('show-original');
  const uploads=h.loads.length;
  await h.documentEvent('paste',{target:h.el('preview-close'),clipboardData:{items:[{kind:'file',type:'image/png',getAsFile:()=>image('pasted.png')}]}});
  assert.equal(h.loads.length,uploads);await h.fire('preview-close');
  assert.equal(h.doc.activeElement,h.el('candidates').children[0].children[0]);
  await h.el('candidates').children[0].children[0].fire('click');await settle();
  await h.event('cockpit-auth-changed');assert.equal(h.el('preview').open,false);assert.equal(h.el('preview').hidden,true);
  assert.equal(h.el('preview-result').src,undefined);assert.equal(h.el('preview-source').src,undefined);
});
test('catalog is own GPT only, masked, explicit model selection, zero inference on boot', async () => {
  const h = await harness();
  assert.equal(h.calls.length, 1); assert.equal(h.el('token').children.length, 4);
  assert.ok(!h.dom().includes(SECRET)); assert.ok(!h.dom().includes('不能列出')); assert.equal(h.el('token-manager').href, '/keys');
  assert.equal(h.el('token').children[2].disabled, true);
  await h.choose(); assert.equal(h.paid().length, 0);
  assert.equal(h.el('model').children[0].value, '');
  await h.fire('token', 'change', '4'); assert.equal(h.el('model').value, ''); assert.equal(h.el('submit').disabled, true);
  assert.deepEqual(h.forbidden, []);
});
test('generation only on explicit form submit; canonical seven fields and shared session wrapper', async () => {
  const h = await harness(); await h.choose(); await h.fire('form', 'submit');
  assert.equal(h.paid().length, 1); const call = h.paid()[0];
  assert.equal(call.url, '/cockpit/api/image-studio/generations');
  assert.deepEqual(Object.keys(call.data).sort(), ['contract_revision','intent','model', 'parameters', 'prompt', 'request_id', 'token_id']);
  assert.deepEqual(call.data.parameters, {size: 'auto', quality: 'auto', output_format: 'png'});
  assert.equal(call.init.headers['X-Cockpit-Studio'], '1'); assert.equal(call.bridged.headers['X-Cockpit-Session'], 'fixture-session');
  assert.ok(!Object.hasOwn(call.init.headers, 'X-Cockpit-Session')); assert.ok(!Object.hasOwn(call.init.headers, 'Authorization'));
  for (const call of h.calls) { assert.equal(call.init.redirect, 'error'); assert.equal(call.init.cache, 'no-store'); assert.equal(call.init.credentials, 'same-origin'); assert.ok(call.init.signal); }
  assert.equal(h.loads.length, 0); assert.equal(h.el('candidates').children.length, 1); assert.equal(h.timers.size, 0); assert.equal(h.intervals.size, 0);
});
test('whole edit exports immutable PNG primary, guide next and two refs; fidelity edit only', async () => {
  const h = await harness(); await h.choose(); await h.upload(); h.change({hasAnnotations: true, hasMask: false, revision: 3});
  h.el('reference-input').files = [image('ref1.png', 100, 100), image('ref2.png', 100, 100)]; await h.fire('reference-input', 'change');
  await h.fire('form', 'submit'); const call = h.paid()[0];
  assert.equal(call.url, '/cockpit/api/image-studio/edits'); assert.equal(call.init.headers['Content-Type'], undefined);
  assert.deepEqual(call.init.body.getAll('image[]').map(x => x.name), ['primary.png', 'guide.png', 'reference-1.png', 'reference-2.png']);
  assert.equal(call.init.body.getAll('mask').length, 0); assert.equal(call.init.body.get('prompt'), '把背景改成晴朗的海边');
  assert.equal(JSON.parse(call.init.body.get('parameters')).input_fidelity, 'low');
  assert.equal(h.snapshots[0].options.local, false); assert.equal(h.snapshots[0].options.size, '1024x1024'); assert.equal(h.loads.length, 1);
});
test('edit operation follows selection, not a hidden local tab; selection alone blocks', async () => {
  const h=await harness(); await h.choose(); await h.upload();
  assert.equal(h.el('submit-label').textContent,'整图编辑');
  h.change({hasMask:true}); await h.fire('prompt','input','');
  assert.equal(h.el('submit').disabled,true); assert.match(h.el('readiness').textContent,/选区本身/);
  await h.fire('form','submit'); assert.equal(h.paid().length,0);
  h.change({hasAnnotations:true}); assert.equal(h.el('submit').disabled,false);
  assert.equal(h.el('submit-label').textContent,'生成局部修改');
  await h.fire('form','submit'); const body=h.paid()[0].init.body;
  assert.equal(body.get('prompt'),''); assert.equal(body.getAll('mask').length,1);
  assert.deepEqual(JSON.parse(body.get('intent')),{operation:'local',has_annotations:true,reference_roles:[]});
  assert.equal(body.get('contract_revision'),'a'.repeat(64));
  assert.equal(h.compositions[0].snapshot,h.snapshots[0].snapshot);assert.equal(h.loads.length,1);
});
test('annotations alone allow empty raw prompt and whole edit; missing guide blocks',async()=>{
  const h=await harness();await h.choose();await h.upload();await h.fire('prompt','input','  ');
  h.change({hasAnnotations:true});assert.equal(h.el('submit-label').textContent,'按标注修改');
  assert.equal(h.el('prompt').required,false);await h.fire('form','submit');
  const body=h.paid()[0].init.body;assert.equal(body.get('prompt'),'  ');assert.equal(body.getAll('mask').length,0);
  assert.equal(JSON.parse(body.get('intent')).operation,'whole');assert.equal(h.compositions.length,0);
  h.snapshotHandler=snapshot=>({...snapshot,guide:null});await h.fire('form','submit');
  assert.equal(h.paid().length,1);assert.match(h.el('request-status').textContent,/指导图/);
});
test('reference-only local sends a selection guide and roles, never a mask, still composes',async()=>{
  const h=await harness();await h.choose('gpt-image-modern');await h.upload();h.change({hasMask:true});
  h.el('reference-input').files=[image('ref.png',100,100)];await h.fire('reference-input','change');
  const role=h.el('references').children[0].children.at(-1);role.value='style';await role.fire('change');
  await h.fire('form','submit');const body=h.paid()[0].init.body;
  assert.equal(body.getAll('mask').length,0);assert.deepEqual(body.getAll('image[]').map(x=>x.name),['primary.png','guide.png','reference-1.png']);
  assert.deepEqual(JSON.parse(body.get('intent')),{operation:'local',has_annotations:false,reference_roles:['style']});
  assert.equal(h.snapshots[0].options.guideSelection,true);assert.ok(h.snapshots[0].snapshot.mask);
  assert.equal(h.compositions[0].snapshot,h.snapshots[0].snapshot);
});
test('operation-specific profiles reset fields and DOM tampering is not request state',async()=>{
  const p=profile();p.generation.parameters=parameters({qualities:['low'],formats:['png']});
  p.edit.parameters=parameters({qualities:['high'],formats:['webp'],input_fidelity:[],allowed_fields:['size','quality','output_format']});
  const h=await harness({catalogModels:[p]});await h.choose();assert.equal(h.el('quality').value,'low');
  await h.upload();assert.equal(h.el('quality').value,'high');assert.equal(h.el('format').value,'webp');
  h.el('prompt').value='not an input event';h.el('quality').value='low';await h.fire('form','submit');
  const body=h.paid()[0].init.body;assert.equal(body.get('prompt'),'把背景改成晴朗的海边');
  assert.deepEqual(JSON.parse(body.get('parameters')),{size:'1024x1024',quality:'high',output_format:'webp'});
  await h.fire('mode-create');assert.equal(h.el('quality').value,'low');
  await h.fire('prompt','input','');await h.fire('form','submit');assert.equal(h.paid().length,1);
});
test('new clears document and references, keeps candidates and never dispatches',async()=>{
  const h=await harness();await h.choose();await h.upload();await h.fire('form','submit');
  h.el('reference-input').files=[image('ref.png',100,100)];await h.fire('reference-input','change');
  await h.fire('new');assert.equal(h.info.documentId,null);assert.equal(h.el('prompt').value,'');
  assert.equal(h.el('mode-create').attrs['aria-pressed'],'true');assert.equal(h.el('references').children.length,0);
  assert.equal(h.el('candidates').children.length,1);assert.equal(h.paid().length,1);
});
test('new during a local request preserves snapshot; late candidate never repopulates new canvas',async()=>{
  const h=await harness();await h.choose();await h.upload();h.change({hasMask:true});
  const pending=deferred();h.handler=async()=>pending.promise;
  const submitting=h.el('form').fire('submit');await settle();const paid=h.paid()[0];
  await h.fire('new');assert.equal(h.engineCalls.find(x=>x.name==='clear').args[0].preserveSnapshots,true);
  pending.resolve(h.result(paid.data));await submitting;
  assert.equal(h.info.documentId,null);assert.equal(h.el('mode-create').attrs['aria-pressed'],'true');
  assert.equal(h.compositions[0].snapshot.documentId,'doc-1');assert.equal(h.loads.length,1);
  assert.equal(h.el('candidates').children.length,1);assert.equal(h.el('preview').hidden,true);
});
test('generation preview is not an apply or a mode change',async()=>{
  const h=await harness();await h.choose();await h.fire('form','submit');
  await h.el('candidates').children[0].children[0].fire('click');await settle();
  assert.equal(h.el('mode-create').attrs['aria-pressed'],'true');assert.equal(h.el('preview').hidden,false);
  assert.equal(h.loads.length,0);assert.equal(h.el('submit').disabled,true);
  await h.fire('preview-download');await h.fire('preview-close');assert.equal(h.loads.length,0);
  await h.el('candidates').children[0].children[0].fire('click');await h.fire('preview-adopt');
  assert.equal(h.loads.length,1);assert.equal(h.el('mode-edit').attrs['aria-pressed'],'true');assert.equal(h.paid().length,1);
});
test('no-size contract hides unsupported controls and keeps export size out of HTTP parameters',async()=>{
  const p=profile(),empty=parameters({allowed_fields:[],sizes:[],qualities:[],formats:[],backgrounds:[],input_fidelity:[]});
  p.generation.parameters=empty;p.edit.parameters=empty;
  const h=await harness({catalogModels:[p]});await h.choose();await h.upload(image('large.png',4000,4000));
  for(const id of ['size','quality','format','background','fidelity']) assert.equal(h.el(`${id}-control`).hidden,true);
  await h.fire('form','submit');assert.deepEqual(JSON.parse(h.paid()[0].init.body.get('parameters')),{});
  const [w,height]=h.snapshots[0].options.size.split('x').map(Number);assert.ok(w*height<=8500000);
});
test('observation switches do not remove marks or change submitted intent',async()=>{
  const h=await harness();await h.choose();await h.upload();h.change({hasMask:true,hasAnnotations:true});
  for(const id of ['show-mask','show-annotations']) {h.el(id).checked=false;await h.fire(id,'change');}
  await h.fire('prompt','input','');await h.fire('form','submit');
  assert.equal(JSON.parse(h.paid()[0].init.body.get('intent')).operation,'local');
  assert.equal(h.paid()[0].init.body.getAll('image[]').length,2);
});
test('duplicate unknown receipt requires acknowledgment and a fresh explicit request id',async()=>{
  const h=await harness();await h.choose();h.handler=async()=>reply({error:'请求已受理',uncertain:false,
    details:{category:'duplicate',receipt_state:'unknown',secret:SECRET}},409);
  await h.fire('form','submit');assert.equal(h.el('uncertain').hidden,false);assert.equal(h.el('submit').disabled,true);
  assert.equal(h.el('error-details').open,false);assert.match(h.el('error-details-text').textContent,/receipt_state: unknown/);
  await h.fire('form','submit');assert.equal(h.paid().length,1);await h.fire('acknowledge');assert.equal(h.paid().length,1);
  h.handler=undefined;await h.fire('form','submit');assert.equal(h.paid().length,2);
  assert.notEqual(h.paid()[0].data.request_id,h.paid()[1].data.request_id);assert.ok(!h.dom().includes(SECRET));
});
test('late response after changing main document remains bound to old source, never loads itself', async () => {
  const h = await harness(); await h.choose(); const original = image('原图.png'); await h.upload(original); h.change({hasMask: true});
  const pending = deferred(); h.handler = async () => pending.promise;
  const submitting = h.el('form').fire('submit'); await settle(); const paid = h.paid()[0];
  const next = image('新图.png', 1200, 1200); await h.upload(next); h.change({revision: 9});
  pending.resolve(h.result(paid.data)); await submitting; await settle();
  assert.equal(h.loads.length, 2); assert.equal(h.snapshots[0].snapshot.documentId, 'doc-1');
  assert.equal(h.compositions[0].snapshot.documentId, 'doc-1'); assert.match(h.dom(), /非当前主图/);
  await h.el('candidates').children[0].children[0].fire('click'); await settle();
  h.el('compare').checked = true; await h.fire('compare','change');
  assert.equal(h.created.find(x => x.url === h.el('preview-source').src).blob, original);
  assert.equal(h.el('preview').hidden, false);
});
test('original/composite switch, compare, download, adoption and continue are local actions', async () => {
  const h = await harness(); await h.choose(); await h.upload(); h.change({hasMask: true}); await h.fire('form', 'submit');
  await h.el('candidates').children[0].children[0].fire('click'); await settle();
  assert.equal(h.el('show-composite').attrs['aria-pressed'], 'true');
  await h.fire('show-original'); assert.equal(h.el('show-original').attrs['aria-pressed'], 'true');
  h.el('compare').checked = false; await h.fire('compare', 'change'); assert.equal(h.el('preview-source').src, undefined);
  await h.fire('preview-download'); assert.match(h.downloads.at(-1).download, /original\.png$/);
  await h.fire('show-composite'); await h.fire('preview-download'); assert.match(h.downloads.at(-1).download, /composite\.png$/);
  await h.fire('preview-continue'); assert.equal(h.loads.length, 2); assert.equal(h.loads[1].name, 'composite.png'); assert.equal(h.el('preview').hidden, true);
  assert.equal(h.paid().length, 1); assert.match(h.el('request-status').textContent, /下一步/);
});
test('composition mismatch keeps full result and disables composite choice', async () => {
  const h = await harness(); await h.choose(); await h.upload(); h.change({hasMask: true});
  h.composeHandler = async () => ({blob: null, reason: 'working_size_mismatch'}); await h.fire('form', 'submit');
  await h.el('candidates').children[0].children[0].fire('click'); await settle();
  assert.equal(h.el('show-composite').disabled, true); assert.equal(h.el('show-original').attrs['aria-pressed'], 'true');
  assert.equal(h.loads.length, 1); assert.ok(h.el('composite-reason').textContent);
});
test('snapshot revision change during asynchronous encoding prevents the request', async () => {
  const h = await harness(); await h.choose(); await h.upload();
  h.snapshotHandler = async snapshot => ({...snapshot, revision: 999}); await h.fire('form', 'submit');
  assert.equal(h.paid().length, 0); assert.match(h.el('request-status').textContent, /已变化/); assert.equal(h.el('submit').disabled, false);
});
test('360 second timeout covers unsettled fetch, blocks repeat until manual acknowledgment', async () => {
  const h = await harness(); await h.choose(); const pending = deferred(); h.handler = async () => pending.promise;
  const submitting = h.el('form').fire('submit'); await settle(); assert.equal(h.paid().length, 1);
  assert.equal([...h.timers.values()][0].ms, 360000); assert.equal(h.el('waiting').hidden, false);
  const duplicate = h.el('form').fire('submit'); await duplicate; assert.equal(h.paid().length, 1);
  await h.timeout(360000); await submitting; assert.match(h.el('uncertain-message').textContent, /360 秒.*结果未知/);
  assert.equal(h.el('submit').disabled, true); assert.ok(h.paid()[0].init.signal.aborted);
  await h.fire('form', 'submit'); assert.equal(h.paid().length, 1);
  await h.fire('acknowledge'); assert.equal(h.paid().length, 1); assert.equal(h.el('submit').disabled, false);
  pending.resolve(h.result(h.paid()[0].data)); await settle(); assert.equal(h.el('candidates').children.length, 0);
  assert.equal(h.timers.size, 0); assert.equal(h.intervals.size, 0);
});
test('ending wait is uncertainty, not upstream cancellation, and never replays', async () => {
  const h = await harness(); await h.choose(); const pending = deferred(); h.handler = async () => pending.promise;
  const submitting = h.el('form').fire('submit'); await settle(); await h.fire('stop-wait'); await submitting;
  assert.match(h.el('uncertain-message').textContent, /仍在执行或计费/); assert.equal(h.el('submit').disabled, true);
  assert.equal(h.paid().length, 1); assert.equal(h.loads.length, 0);
});
test('deadline also covers pending JSON body; malformed or mismatched results are uncertain', async () => {
  const h = await harness(); await h.choose(); const pending = deferred();
  h.handler = async () => ({ok: true, status: 200, json: () => pending.promise});
  const submitting = h.el('form').fire('submit'); await settle(); await h.timeout(360000); await submitting;
  assert.equal(h.el('uncertain').hidden, false); assert.equal(h.paid().length, 1);
  const second = await harness(); await second.choose(); second.handler = async () => reply({request_id: 'wrong', model: 'gpt-image-fixture', images: []});
  await second.fire('form', 'submit'); assert.match(second.el('uncertain-message').textContent, /不一致/); assert.equal(second.loads.length, 0);
});
test('known server rejection is not marked unknown and no automatic retry occurs', async () => {
  const h = await harness(); await h.choose(); h.handler = async () => reply({error: '请求池暂不可用', uncertain: false}, 503);
  await h.fire('form', 'submit'); assert.equal(h.paid().length, 1); assert.equal(h.el('uncertain').hidden, true);
  assert.equal(h.el('submit').disabled, false); assert.match(h.el('request-status').textContent, /请求池/);
});
test('auth switch clears images, prompts, references, candidates, URLs and pending responses', async () => {
  const h = await harness(); await h.choose(); await h.upload();
  h.el('reference-input').files = [image('ref.png', 100, 100)]; await h.fire('reference-input', 'change'); await h.fire('form', 'submit');
  const urls = h.created.map(x => x.url), pending = deferred(); h.handler = async () => pending.promise;
  const submitting = h.el('form').fire('submit'); await settle(); const call = h.paid().at(-1);
  await h.event('cockpit-auth-changed'); await submitting;
  assert.equal(h.destroyed, true); assert.equal(h.el('prompt').value, ''); assert.equal(h.el('annotation-text').value, '');
  assert.equal(h.el('references').children.length, 0); assert.equal(h.el('candidates').children.length, 0);
  assert.ok(urls.every(x => h.revoked.includes(x))); assert.equal(h.el('submit').disabled, true);
  pending.resolve(h.result(call.data)); await settle(); assert.equal(h.el('candidates').children.length, 0);
  assert.equal(h.timers.size, 0); assert.equal(h.intervals.size, 0); assert.equal(h.observers[0].disconnected, true);
});
test('session metadata mutation, auth HTTP failure and pagehide all scrub without reloading inference', async () => {
  for (const kind of ['meta', '401', '409', 'pagehide']) {
    const h = await harness(); await h.choose(); await h.upload(); await h.fire('form', 'submit');
    if (kind === 'meta') { h.meta.content = 'other-session'; h.observers[0].fn(); await settle(); }
    else if (kind === 'pagehide') await h.event('pagehide');
    else { h.handler = async () => reply({error: '认证已失效', code: kind === '409' ? 'AUTH_SESSION_CHANGED' : ''}, Number(kind)); await h.fire('form', 'submit'); }
    assert.equal(h.el('candidates').children.length, 0, kind); assert.equal(h.el('prompt').value, '', kind);
    assert.equal(h.destroyed, true, kind); const count = h.calls.length; await h.event('pageshow', {persisted: true}); assert.equal(h.calls.length, count);
  }
});
test('candidate count is bounded, deletion frees URLs and explicitly re-enables submit', async () => {
  const h = await harness(); await h.choose(); for (let i = 0; i < 4; i++) await h.fire('form', 'submit');
  assert.equal(h.el('candidates').children.length, 4); assert.equal(h.el('submit').disabled, true); await h.fire('form', 'submit'); assert.equal(h.paid().length, 4);
  await h.el('candidates').children[0].children[1].children[1].fire('click'); await settle();
  assert.equal(h.el('candidates').children.length, 3); assert.equal(h.el('submit').disabled, false); assert.equal(h.revoked.length, 1);
});
test('version list is bounded and switching is a new document, not a model call', async () => {
  const h = await harness(); await h.choose(); for (let i = 0; i < 5; i++) await h.upload(image(`主图-${i}.png`));
  assert.equal(h.el('version').children.length, 4); await h.fire('version', 'change', h.el('version').children[0].value);
  assert.equal(h.loads.length, 6); assert.equal(h.info.documentId, 'doc-6'); assert.equal(h.paid().length, 0);
});
test('input MIME/size/pixels are gated; references are all-or-nothing on decode failure', async () => {
  const h = await harness(); await h.choose(); await h.upload(image('bad.svg', 1, 1, 'image/svg+xml')); assert.equal(h.loads.length, 0);
  await h.upload(image('huge.png', 5000, 5000)); assert.equal(h.loads.length, 0);await h.fire('mode-edit');
  h.el('reference-input').files = [image('20mp-reference.png', 5000, 4000)]; await h.fire('reference-input', 'change');
  assert.equal(h.el('references').children.length, 0); assert.match(h.el('request-status').textContent, /像素/);
  h.decodeHandler = async blob => { if (blob.name === 'broken.png') throw new Error('bad image'); return {width: 100, height: 100, close() {}}; };
  h.el('reference-input').files = [image('fine.png'), image('broken.png')]; await h.fire('reference-input', 'change');
  assert.equal(h.el('references').children.length, 0); assert.equal(h.created.length, 0); assert.equal(h.paid().length, 0);
});
test('setup exception releases submit state without dispatching or leaking a deadline', async () => {
  const h = await harness(); await h.choose();
  h.ctx.crypto.randomUUID = () => { throw new Error('无法创建请求 ID，请刷新页面。'); };
  await h.fire('form', 'submit');
  assert.equal(h.paid().length, 0); assert.equal(h.timers.size, 0); assert.equal(h.el('submit').disabled, false);
  assert.equal(h.el('waiting').hidden, true); assert.match(h.el('request-status').textContent, /请求 ID/);
});
test('engine controls use the frozen API, scoped shortcuts do not run twice or steal text input', async () => {
  const h = await harness(); await h.choose(); await h.upload();
  for (const tool of h.tools) await tool.fire('click');
  for (const id of ['undo', 'redo', 'fit', 'zoom-in', 'zoom-out', 'clear-mask', 'clear-guides']) await h.fire(id);
  await h.fire('brush-size', 'input', '128'); await h.fire('stroke-width', 'input', '8'); await h.fire('color', 'input', '#112233'); await h.fire('annotation-text', 'input', '这里改为蓝色');
  for (const name of ['setTool', 'setBrushSize', 'setColor', 'setStrokeWidth', 'setText', 'undo', 'redo', 'fit', 'zoomBy', 'clearMask', 'clearAnnotations']) assert.ok(h.engineCalls.some(x => x.name === name), name);
  const count = h.engineCalls.filter(x => x.name === 'undo').length;
  await h.fire('dropzone', 'keydown', undefined, {key: 'z', ctrlKey: true, defaultPrevented: true});
  await h.fire('dropzone', 'keydown', undefined, {key: 'z', ctrlKey: true, target: new Element('textarea')});
  assert.equal(h.engineCalls.filter(x => x.name === 'undo').length, count);
  await h.fire('dropzone', 'keydown', undefined, {key: 'z', ctrlKey: true}); assert.equal(h.engineCalls.filter(x => x.name === 'undo').length, count + 1);
});
test('paste ignores text inputs; drag/drop and image paste import only, without inference', async () => {
  const h = await harness(); await h.choose(); const blob = image();
  const clipboardData = {items: [{kind: 'file', type: 'image/png', getAsFile: () => blob}]};
  await h.documentEvent('paste', {target: new Element('textarea'), clipboardData}); assert.equal(h.loads.length, 0);
  await h.documentEvent('paste', {target: h.el('dropzone'), clipboardData, preventDefault() {}}); assert.equal(h.loads.length, 1);
  await h.fire('dropzone', 'drop', undefined, {dataTransfer: {files: [image('drop.png')]}}); assert.equal(h.loads.length, 2);
  assert.equal(h.paid().length, 0);
});
test('narrow screen settings has focus return, inert closed panel and Escape handling', async () => {
  const h = await harness({narrow: true}); assert.equal(h.el('inspector').inert, true);
  await h.fire('open-settings'); assert.equal(h.el('inspector').inert, false); assert.equal(h.el('inspector').attrs['aria-modal'], 'true');
  assert.equal(h.doc.activeElement, h.el('close-settings'));
  await h.fire('inspector', 'keydown', undefined, {key: 'Escape'});
  assert.equal(h.el('inspector').inert, true); assert.equal(h.doc.activeElement, h.el('open-settings'));
  assert.equal(h.el('settings-backdrop').hidden, true);
});
test('missing canvas module is actionable; backend errors redact secrets and never inject HTML', async () => {
  const missing = await harness({missingEngine: true}); await missing.choose(); assert.equal(missing.el('submit').disabled, true); assert.match(missing.el('request-status').textContent, /画布组件/);
  const h = await harness(); await h.choose(); h.handler = async () => reply({error: `<img onerror=evil> ${SECRET}`, uncertain: false}, 400);
  await h.fire('form', 'submit'); assert.ok(!h.dom().includes(SECRET)); assert.match(h.el('request-status').textContent, /KEY 已隐藏/); assert.deepEqual(h.forbidden, []);
});
test('route input restrictions prevent unsupported PNG and oversized exported files before POST', async () => {
  for(const changes of [{mime_types:['image/jpeg']},{max_bytes:1}]) {
    const p=profile();p.edit.inputs={...api.inputLimits(undefined),...changes};
    const h=await harness({catalogModels:[p]});await h.choose();await h.upload();
    if(changes.mime_types) {assert.equal(h.el('submit').disabled,true);assert.match(h.el('readiness').textContent,/PNG/);}
    await h.fire('form','submit');assert.equal(h.paid().length,0);
    if(changes.max_bytes) assert.match(h.el('request-status').textContent,/大小/);
  }
});
