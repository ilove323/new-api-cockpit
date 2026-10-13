/* Image Studio canvas adapter. Load local Konva 10.7.1 and studio-upstream first.
 * Annotation gestures/text ported from GPT Image Playground SketchBoardModal.tsx.
 * Copyright (c) 2026 CookSleep, MIT. Geometry/drawing/resize use its bundled source;
 * guide layering uses the adapted InvokeAI compositor, Apache-2.0.
 * Revisions and local changes: docs/image-studio-sources.md.
 * Vanilla JS; no fetch, persistence, HTML injection, eval, fonts or model calls.
 * Document pixels are EXIF-normalized exactly once by the image decoder. The
 * immutable base, editable coverage, vector annotations and view UI stay apart.
 * API mask alpha = 255 - coverage. History holds commands, never RGBA per stroke.
 * snapshot() owns its source/coverage/objects in a private WeakMap: compose()
 * never consults the current document, even after load()/undo(). clear() starts
 * an empty document and invalidates private snapshots and pending work by default.
 * clear({preserveSnapshots:true}) keeps submitted/in-flight snapshots composable
 * for late candidates after New; identity resets must use clear() or destroy().
 * snapshot({local:true, guideSelection:true}) exports a cyan region guide for
 * reference-only edits, retaining the mask/coverage for strict local composition.
 * setSelectionVisible()/setAnnotationsVisible() only affect observation, never
 * exports or document revision. setMaskVisible() is the selection visibility alias.
 * Offline tests: node --test tests/test_studio_canvas.cjs
 */
(function (root, factory) {
  'use strict';
  const api = factory(root);
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.CockpitImageCanvas = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function (root) {
  'use strict';

  const LIMITS = Object.freeze({sourcePixels: 16000000, workingPixels: 8500000,
    sourceBytes: 8 * 1024 * 1024, resultBytes: 36 * 1024 * 1024,
    maskBytes: 4000000, axis: 16384, history: 100, operations: 1000,
    annotations: 500, points: 250000, strokePoints: 16000});
  const TOOLS = Object.freeze(['pan', 'select', 'brush', 'eraser', 'rectangle',
    'lasso', 'pen', 'arrow', 'box', 'ellipse', 'text', 'guide-eraser']);
  const SELECTION_COLOR = '#00d8ff';
  let documentSequence = 0;
  const clamp = (n, lo, hi) => Math.max(lo, Math.min(hi, n));
  const fail = (message, code) => Object.assign(new Error(message), {code});
  function finite(n, name) {
    if (typeof n !== 'number' || !Number.isFinite(n)) throw fail('Invalid ' + name, 'INVALID_ARGUMENT');
    return n;
  }
  function dimensions(width, height, maxPixels = LIMITS.sourcePixels) {
    if (!Number.isInteger(width) || !Number.isInteger(height) || width < 1 || height < 1 ||
        width > LIMITS.axis || height > LIMITS.axis || width * height > maxPixels) {
      throw fail('Image dimensions exceed the canvas resource limit', 'IMAGE_DIMENSIONS');
    }
    return {width, height};
  }
  function letterbox(width, height, workingWidth, workingHeight) {
    dimensions(width, height);
    dimensions(workingWidth, workingHeight, LIMITS.workingPixels);
    const scale = Math.min(workingWidth / width, workingHeight / height);
    return Object.freeze({width: workingWidth, height: workingHeight, scale,
      offsetX: (workingWidth - width * scale) / 2,
      offsetY: (workingHeight - height * scale) / 2});
  }
  function workingSize(size, width, height) {
    if (size === 'auto' || size === undefined) return letterbox(width, height, width, height);
    if (typeof size !== 'string' || !/^[1-9]\d{0,4}x[1-9]\d{0,4}$/.test(size)) {
      throw fail('Choose auto or an explicit WIDTHxHEIGHT working size', 'INVALID_SIZE');
    }
    const [w, h] = size.split('x').map(Number);
    return letterbox(width, height, w, h);
  }
  function validateWorking(working, width, height) {
    if (!working || typeof working !== 'object') throw fail('Missing output mapping', 'UNKNOWN_MAPPING');
    const expected = letterbox(width, height, working.width, working.height);
    for (const key of ['scale', 'offsetX', 'offsetY']) {
      if (!Number.isFinite(working[key]) || Math.abs(expected[key] - working[key]) > 1e-9) {
        throw fail('Unknown or inconsistent output mapping', 'UNKNOWN_MAPPING');
      }
    }
    return expected;
  }
  function inversePoint(matrix, point) {
    if (!matrix || matrix.length !== 6 || !Array.from(matrix).every(Number.isFinite)) {
      throw fail('Invalid affine transform', 'INVALID_TRANSFORM');
    }
    const [a, b, c, d, e, f] = matrix, det = a * d - b * c;
    if (Math.abs(det) < 1e-15) throw fail('Singular affine transform', 'INVALID_TRANSFORM');
    const x = finite(point.x, 'pointer x') - e, y = finite(point.y, 'pointer y') - f;
    return {x: (d * x - c * y) / det, y: (a * y - b * x) / det};
  }
  function clientToStage(point, rect, stageSize) {
    if (!(rect.width > 0 && rect.height > 0 && stageSize.width > 0 && stageSize.height > 0)) {
      throw fail('Canvas has no visible size', 'INVALID_TRANSFORM');
    }
    return {x: (finite(point.clientX, 'client x') - rect.left) * stageSize.width / rect.width,
      y: (finite(point.clientY, 'client y') - rect.top) * stageSize.height / rect.height};
  }
  function clientToDocument(point, rect, stageSize, absoluteMatrix) {
    return inversePoint(absoluteMatrix, clientToStage(point, rect, stageSize));
  }
  function zoomAt(view, anchor, factor, min = 1 / 512, max = 32) {
    finite(factor, 'zoom factor');
    if (!(factor > 0 && view.scale > 0)) throw fail('Zoom factor must be positive', 'INVALID_ARGUMENT');
    const scale = clamp(view.scale * factor, min, max);
    return {scale, x: anchor.x - (anchor.x - view.x) * scale / view.scale,
      y: anchor.y - (anchor.y - view.y) * scale / view.scale};
  }
  function normalizedRect(a, b) {
    return {x: Math.min(a.x, b.x), y: Math.min(a.y, b.y),
      width: Math.abs(b.x - a.x), height: Math.abs(b.y - a.y)};
  }
  function arrayLength(array, size, name) {
    if (!array || array.length !== size) throw fail('Invalid ' + name + ' length', 'INVALID_PIXELS');
  }
  // Exact squared Euclidean distance to protected pixel centers (including the
  // exterior of the document). One Float32 field, bounded row/column scratch;
  // feathering is calculated once from the frozen coverage, never from a result.
  function inwardFeather(coverage, width, height, radius = 0) {
    dimensions(width, height);
    arrayLength(coverage, width * height, 'coverage');
    finite(radius, 'feather');
    if (radius < 0 || radius > 256) throw fail('Feather must be between 0 and 256 document pixels', 'INVALID_ARGUMENT');
    if (!radius) return Uint8Array.from(coverage);
    const distances = new Float32Array(width * height);
    for (let y = 0; y < height; y++) {
      let distance = 0;
      for (let x = 0; x < width; x++) {
        const i = y * width + x;
        distance = coverage[i] ? distance + 1 : 0;
        distances[i] = distance * distance;
      }
      distance = 0;
      for (let x = width - 1; x >= 0; x--) {
        const i = y * width + x;
        distance = coverage[i] ? distance + 1 : 0;
        distances[i] = Math.min(distances[i], distance * distance);
      }
    }
    const sites = new Int32Array(height), bounds = new Float64Array(height + 1);
    const column = new Float64Array(height), weights = new Uint8Array(width * height);
    for (let x = 0; x < width; x++) {
      for (let y = 0; y < height; y++) column[y] = distances[y * width + x];
      let k = 0;
      sites[0] = 0; bounds[0] = -Infinity; bounds[1] = Infinity;
      for (let q = 1; q < height; q++) {
        let s;
        do {
          const v = sites[k];
          s = (column[q] + q * q - column[v] - v * v) / (2 * (q - v));
          if (s <= bounds[k]) k--;
          else break;
        } while (k >= 0);
        k++; sites[k] = q; bounds[k] = s; bounds[k + 1] = Infinity;
      }
      k = 0;
      for (let y = 0; y < height; y++) {
        while (bounds[k + 1] < y) k++;
        const i = y * width + x;
        if (!coverage[i]) continue;
        const dy = y - sites[k];
        const distance = Math.sqrt(Math.min(dy * dy + column[sites[k]], (y + 1) ** 2, (height - y) ** 2));
        weights[i] = Math.round(coverage[i] * clamp((distance - 0.5) / radius, 0, 1));
      }
    }
    return weights;
  }
  // Bilinear sampling in premultiplied space prevents transparent RGB halos.
  function samplePremultiplied(rgba, width, height, x, y, out = [0, 0, 0, 0]) {
    x = clamp(x, 0, width - 1); y = clamp(y, 0, height - 1);
    const x0 = Math.floor(x), y0 = Math.floor(y), x1 = Math.min(x0 + 1, width - 1), y1 = Math.min(y0 + 1, height - 1);
    const dx = x - x0, dy = y - y0;
    const p00 = (y0 * width + x0) * 4, p10 = (y0 * width + x1) * 4;
    const p01 = (y1 * width + x0) * 4, p11 = (y1 * width + x1) * 4;
    const a00 = rgba[p00 + 3] / 255 * (1 - dx) * (1 - dy), a10 = rgba[p10 + 3] / 255 * dx * (1 - dy);
    const a01 = rgba[p01 + 3] / 255 * (1 - dx) * dy, a11 = rgba[p11 + 3] / 255 * dx * dy;
    for (let c = 0; c < 3; c++) out[c] = rgba[p00 + c] * a00 + rgba[p10 + c] * a10 + rgba[p01 + c] * a01 + rgba[p11 + c] * a11;
    out[3] = a00 + a10 + a01 + a11;
    return out;
  }
  function maskRGBA(coverage, width, height, working = letterbox(width, height, width, height)) {
    dimensions(width, height); validateWorking(working, width, height);
    arrayLength(coverage, width * height, 'coverage');
    const out = new Uint8ClampedArray(working.width * working.height * 4);
    for (let y = 0; y < working.height; y++) for (let x = 0; x < working.width; x++) {
      const sx = (x + 0.5 - working.offsetX) / working.scale;
      const sy = (y + 0.5 - working.offsetY) / working.scale;
      // Nearest pixel coverage, no selection leaking into letterbox padding.
      const selected = sx >= 0 && sy >= 0 && sx < width && sy < height ? coverage[Math.floor(sy) * width + Math.floor(sx)] : 0;
      out[(y * working.width + x) * 4 + 3] = 255 - selected;
    }
    return out;
  }
  /**
   * Visible cyan guidance, NOT an API alpha mask. Use the exact mask sampling
   * transform so holes, partial coverage and letterbox padding stay aligned.
   * A one-working-pixel opaque inner boundary surrounds a translucent interior;
   * feather weights are deliberately separate from the selected-region extent.
   * @param {ArrayLike<number>} coverage
   * @param {number} width
   * @param {number} height
   * @param {{width:number, height:number, scale:number, offsetX:number, offsetY:number}} [working]
   * @returns {Uint8ClampedArray}
   */
  function selectionGuideRGBA(coverage, width, height, working = letterbox(width, height, width, height)) {
    const out = maskRGBA(coverage, width, height, working), w = working.width, h = working.height;
    for (let i = 3; i < out.length; i += 4) out[i] = 255 - out[i];
    // Recolouring keeps selected alpha nonzero, so neighbour membership is stable.
    for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
      const i = (y * w + x) * 4;
      if (!out[i + 3]) continue;
      const boundary = !x || !y || x === w - 1 || y === h - 1 ||
        !out[i - 4 + 3] || !out[i + 4 + 3] || !out[i - w * 4 + 3] || !out[i + w * 4 + 3];
      out[i] = 0; out[i + 1] = 216; out[i + 2] = 255; out[i + 3] = boundary ? 255 : 80;
    }
    return out;
  }
  function composeRGBA(original, result, coverage, width, height, working) {
    dimensions(width, height); validateWorking(working, width, height);
    arrayLength(original, width * height * 4, 'original');
    arrayLength(result, working.width * working.height * 4, 'result');
    arrayLength(coverage, width * height, 'coverage');
    const out = new Uint8ClampedArray(original.length), sample = new Float64Array(4);
    for (let y = 0; y < height; y++) for (let x = 0; x < width; x++) {
      const p = y * width + x, i = p * 4, s = coverage[p] / 255;
      if (!s) { out[i] = original[i]; out[i + 1] = original[i + 1]; out[i + 2] = original[i + 2]; out[i + 3] = original[i + 3]; continue; }
      samplePremultiplied(result, working.width, working.height,
        working.offsetX + (x + 0.5) * working.scale - 0.5,
        working.offsetY + (y + 0.5) * working.scale - 0.5, sample);
      const oa = original[i + 3] / 255, a = (1 - s) * oa + s * sample[3];
      out[i + 3] = Math.round(a * 255);
      for (let c = 0; c < 3; c++) out[i + c] = a > 0 ? Math.round(((1 - s) * original[i + c] * oa + s * sample[c]) / a) : 0;
    }
    return out;
  }
  // Upstream SketchElement includes point objects and per-element erase paths.
  // Freeze them recursively: submitted guidance must survive future edits.
  function freezeRecord(record) {
    if (!record || typeof record !== 'object') return record;
    if (Object.isFrozen(record)) return record;
    if (Array.isArray(record)) return Object.freeze(record.map(freezeRecord));
    return Object.freeze(Object.fromEntries(Object.entries(record).map(([key, value]) => [key, freezeRecord(value)])));
  }
  const model = (masks = [], annotations = [], feather = 0) => Object.freeze({
    masks: Object.freeze(masks), annotations: Object.freeze(annotations), feather});
  class History {
    constructor(limit = LIMITS.history) {
      if (!Number.isInteger(limit) || limit < 1 || limit > LIMITS.history) throw fail('Invalid history limit', 'INVALID_ARGUMENT');
      this.limit = limit; this.past = []; this.future = [];
    }
    push(before, after) {
      if (before === after) return false;
      this.past.push(Object.freeze({before, after}));
      if (this.past.length > this.limit) this.past.shift();
      this.future.length = 0; return true;
    }
    undo() { const entry = this.past.pop(); if (!entry) return null; this.future.push(entry); return entry.before; }
    redo() { const entry = this.future.pop(); if (!entry) return null; this.past.push(entry); return entry.after; }
    clear() { this.past.length = 0; this.future.length = 0; }
  }
  function checkBudget(next) {
    if (next.masks.length > LIMITS.operations || next.annotations.length > LIMITS.annotations) {
      throw fail('Canvas command limit reached; clear unused selections or annotations', 'COMMAND_LIMIT');
    }
    let points = 0;
    for (const object of next.masks) points += (object.points || []).length / 2;
    for (const object of next.annotations) {
      points += (object.points || []).length;
      for (const stroke of object.erase || []) points += stroke.points.length;
    }
    if (points > LIMITS.points) throw fail('Canvas point limit reached', 'COMMAND_LIMIT');
  }
  function editable(target) {
    return !!target && (target.isContentEditable || /^(INPUT|TEXTAREA|SELECT|BUTTON)$/.test(target.tagName || ''));
  }
  // Read bounded raster headers before asking the browser to allocate a bitmap.
  // The native decoder still validates the complete compressed image afterwards.
  function inspectRaster(bytes, maxPixels = LIMITS.sourcePixels) {
    const data = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
    const tag = (start, length) => String.fromCharCode(...bytes.subarray(start, start + length));
    let width, height, kind;
    if ([137, 80, 78, 71, 13, 10, 26, 10].every((b, i) => bytes[i] === b)) {
      kind = 'png';
      if (bytes.length < 33 || data.getUint32(8) !== 13 || tag(12, 4) !== 'IHDR') throw fail('Invalid PNG header', 'IMAGE_DECODE');
      width = data.getUint32(16); height = data.getUint32(20);
      for (let offset = 8; offset + 12 <= bytes.length;) {
        const n = data.getUint32(offset), chunk = tag(offset + 4, 4);
        if (chunk === 'acTL') throw fail('Animated images are not accepted', 'IMAGE_ANIMATION');
        if (n > bytes.length - offset - 12) throw fail('Truncated PNG', 'IMAGE_DECODE');
        offset += n + 12;
      }
    } else if (bytes[0] === 255 && bytes[1] === 216 && bytes[2] === 255) {
      kind = 'jpeg';
      for (let offset = 2; offset + 3 < bytes.length;) {
        if (bytes[offset++] !== 255) throw fail('Invalid JPEG header', 'IMAGE_DECODE');
        while (bytes[offset] === 255) offset++;
        const marker = bytes[offset++];
        if (marker === 217 || marker === 218) break;
        if (marker === 1 || (marker >= 208 && marker <= 215)) continue;
        if (offset + 2 > bytes.length) break;
        const n = data.getUint16(offset);
        if (n < 2 || offset + n > bytes.length) throw fail('Truncated JPEG', 'IMAGE_DECODE');
        if (marker >= 192 && marker <= 207 && ![196, 200, 204].includes(marker)) {
          if (n < 8) throw fail('Invalid JPEG dimensions', 'IMAGE_DECODE');
          height = data.getUint16(offset + 3); width = data.getUint16(offset + 5); break;
        }
        offset += n;
      }
    } else if (tag(0, 4) === 'RIFF' && tag(8, 4) === 'WEBP') {
      kind = 'webp';
      const uint24 = offset => bytes[offset] + bytes[offset + 1] * 256 + bytes[offset + 2] * 65536;
      for (let offset = 12; offset + 8 <= bytes.length;) {
        const chunk = tag(offset, 4), n = data.getUint32(offset + 4, true), start = offset + 8;
        if (n > bytes.length - start) throw fail('Truncated WebP', 'IMAGE_DECODE');
        if (chunk === 'ANIM' || chunk === 'ANMF' || (chunk === 'VP8X' && (bytes[start] & 2))) throw fail('Animated images are not accepted', 'IMAGE_ANIMATION');
        if (chunk === 'VP8X' && n >= 10) { width = uint24(start + 4) + 1; height = uint24(start + 7) + 1; }
        else if (chunk === 'VP8 ' && n >= 10 && !width && tag(start + 3, 3) === '\u009d\u0001\u002a') {
          width = data.getUint16(start + 6, true) & 16383; height = data.getUint16(start + 8, true) & 16383;
        } else if (chunk === 'VP8L' && n >= 5 && !width && bytes[start] === 47) {
          const packed = data.getUint32(start + 1, true); width = (packed & 16383) + 1; height = ((packed >>> 14) & 16383) + 1;
        }
        offset += 8 + n + (n % 2);
      }
    } else throw fail('Only raster PNG, JPEG and WebP images are accepted', 'IMAGE_TYPE');
    dimensions(width, height, maxPixels);
    return {kind, width, height};
  }
  function paintMask(ctx, command) {
    ctx.save(); ctx.globalCompositeOperation = command.type === 'eraser' ? 'destination-out' : 'source-over';
    ctx.fillStyle = SELECTION_COLOR; ctx.strokeStyle = SELECTION_COLOR; ctx.lineCap = 'round'; ctx.lineJoin = 'round';
    ctx.beginPath();
    if (command.type === 'rectangle') ctx.fillRect(command.x, command.y, command.width, command.height);
    else if (command.type === 'lasso') {
      const p = command.points;
      ctx.moveTo(p[0], p[1]); for (let i = 2; i < p.length; i += 2) ctx.lineTo(p[i], p[i + 1]);
      ctx.closePath(); ctx.fill();
    } else {
      const p = command.points; ctx.lineWidth = command.size;
      if (p.length === 2) { ctx.arc(p[0], p[1], command.size / 2, 0, Math.PI * 2); ctx.fill(); }
      else { ctx.moveTo(p[0], p[1]); for (let i = 2; i < p.length; i += 2) ctx.lineTo(p[i], p[i + 1]); ctx.stroke(); }
    }
    ctx.restore();
  }
  function create(host, options = {}) {
    const K = root.Konva, doc = host && host.ownerDocument, upstream = root.CockpitStudioUpstream;
    if (!upstream?.sketch || !upstream.drawElements) throw fail('Pinned studio source bundle is required', 'CANVAS_UNAVAILABLE');
    const S = upstream.sketch;
    if (!host || !doc || !K || K.version !== '10.7.1') throw fail('A host and local Konva 10.7.1 are required', 'CANVAS_UNAVAILABLE');
    const notify = typeof options.onChange === 'function' ? options.onChange : () => {};
    let disposed = false, loadSequence = 0, snapshotEpoch = 0, source = null, current = model(), revision = 0;
    let tool = 'pan', brushSize = 40, color = '#ef4444', strokeWidth = 4, text = '标注';
    let selected = null, nextObject = 0, editing = null, gesture = null, space = false, pinch = null, pinchBlocked = false;
    let hasMask = false, view = {x: 0, y: 0, scale: 1}, fitted = true;
    let selectionVisible = true, annotationsVisible = true;
    let snapshots = new WeakMap();
    const history = new History(), pointers = new Map(), listeners = [], canvases = new Set();
    const oldTab = host.getAttribute('tabindex'), oldRole = host.getAttribute('role');
    const oldLabel = host.getAttribute('aria-label'), oldTouch = host.style.touchAction, oldCursor = host.style.cursor;
    if (oldTab === null) host.setAttribute('tabindex', '0');
    if (oldRole === null) host.setAttribute('role', 'application');
    if (oldLabel === null) host.setAttribute('aria-label', '图片编辑画布；空格拖动，滚轮缩放，选择标注后 Delete 删除');
    host.style.touchAction = 'none';
    const stage = new K.Stage({container: host, width: Math.max(1, host.clientWidth || 640), height: Math.max(1, host.clientHeight || 480)});
    const baseLayer = new K.Layer({listening: false}), maskLayer = new K.Layer({listening: false});
    const annotationLayer = new K.Layer(), uiLayer = new K.Layer({listening: false});
    const baseGroup = new K.Group(), maskGroup = new K.Group(), annotations = new K.Group(), ui = new K.Group();
    baseLayer.add(baseGroup); maskLayer.add(maskGroup); annotationLayer.add(annotations); uiLayer.add(ui);
    stage.add(baseLayer, maskLayer, annotationLayer, uiLayer);
    const baseNode = new K.Image({listening: false}), maskNode = new K.Image({listening: false, opacity: 0.35});
    baseGroup.add(baseNode); maskGroup.add(maskNode);
    let maskCanvas = null, maskContext = null, annotationCanvas = null, annotationContext = null, draftNode = null;
    const inkNode = new K.Image({listening: false}); annotations.add(inkNode);
    const selectionBox = new K.Rect({stroke: '#2563eb', strokeWidth: 1, dash: [5, 4], listening: false, visible: false});
    ui.add(selectionBox);
    const handles = S.SKETCH_HANDLES.map(name => {
      const node = new K.Rect({fill: '#fff', stroke: '#2563eb', listening: false, visible: false});
      ui.add(node); return {name, node};
    });

    function alive() { if (disposed) throw fail('Canvas editor was destroyed', 'DESTROYED'); }
    function loaded() { alive(); if (!source) throw fail('Load a primary image first', 'NO_DOCUMENT'); }
    function state() {
      const noteCount = editing ? current.annotations.filter(a => a.id !== editing.record.id).length +
        (editing.input.value.trim() ? 1 : 0) : current.annotations.length;
      return {width: source ? source.width : 0, height: source ? source.height : 0,
        documentId: source ? source.documentId : null, revision, hasMask,
        hasAnnotations: noteCount > 0, annotationCount: noteCount,
        canUndo: history.past.length > 0, canRedo: history.future.length > 0,
        tool, zoom: view.scale, feather: current.feather, selectedId: selected,
        selectionVisible, annotationsVisible,
        selected: selected ? current.annotations.find(a => a.id === selected) || null : null, editingText: !!editing};
    }
    function changed(error) { notify({...state(), ...(error ? {error: error.message, errorCode: error.code || 'CANVAS_ERROR'} : {})}); }
    function listen(name, fn, opts) { host.addEventListener(name, fn, opts); listeners.push([name, fn, opts]); }
    function canvas(width, height) {
      const c = doc.createElement('canvas'); c.width = width; c.height = height;
      canvases.add(c);
      try {
        const ctx = c.getContext('2d', {willReadFrequently: true});
        if (!ctx) throw fail('Canvas 2D is unavailable', 'CANVAS_UNAVAILABLE');
        return {canvas: c, ctx};
      } catch (error) { releaseCanvas(c); throw error; }
    }
    function releaseCanvas(c) {
      if (!c) return;
      c.width = 0; c.height = 0; canvases.delete(c);
    }
    function putRGBA(ctx, rgba, width, height) {
      const pixels = ctx.createImageData(width, height); pixels.data.set(rgba); ctx.putImageData(pixels, 0, 0);
    }
    function png(c) {
      return new Promise((resolve, reject) => {
        try { c.toBlob(blob => blob ? resolve(blob) : reject(fail('PNG export failed', 'EXPORT_FAILED')), 'image/png'); }
        catch (_) { reject(fail('PNG export failed', 'EXPORT_FAILED')); }
      });
    }
    async function decode(blob, result = false) {
      if (!blob || typeof blob.slice !== 'function' || typeof blob.arrayBuffer !== 'function' ||
          !blob.size || blob.size > (result ? LIMITS.resultBytes : LIMITS.sourceBytes)) {
        throw fail('Image file exceeds the resource limit or is empty', 'IMAGE_BYTES');
      }
      const full = new Uint8Array(await blob.arrayBuffer());
      inspectRaster(full, result ? LIMITS.workingPixels : LIMITS.sourcePixels);
      let image, url, target;
      try {
        if (typeof root.createImageBitmap === 'function') image = await root.createImageBitmap(blob, {imageOrientation: 'from-image'});
        else {
          image = new root.Image(); url = root.URL.createObjectURL(blob);
          await new Promise((resolve, reject) => { image.onload = resolve; image.onerror = reject; image.src = url; });
        }
        const width = image.naturalWidth || image.width, height = image.naturalHeight || image.height;
        dimensions(width, height, result ? LIMITS.workingPixels : LIMITS.sourcePixels);
        target = canvas(width, height); target.ctx.drawImage(image, 0, 0, width, height);
        return {...target, width, height, rgba: target.ctx.getImageData(0, 0, width, height).data};
      } catch (error) {
        if (target) releaseCanvas(target.canvas);
        if (error && error.code) throw error;
        throw fail('The image could not be decoded', 'IMAGE_DECODE');
      } finally {
        if (image && typeof image.close === 'function') image.close();
        if (url) root.URL.revokeObjectURL(url);
      }
    }
    function cursor() { host.style.cursor = tool === 'pan' || space ? 'grab' : tool === 'select' ? 'default' : 'crosshair'; }
    function applyView() {
      for (const group of [baseGroup, maskGroup, annotations, ui]) group.setAttrs({x: view.x, y: view.y, scaleX: view.scale, scaleY: view.scale});
      selectionBox.strokeWidth(1 / view.scale); selectionBox.dash([5 / view.scale, 4 / view.scale]);
      selection(); positionText(); stage.batchDraw();
    }
    function selection(items = current.annotations) {
      const record = items.find(a => a.id === selected);
      const visible = annotationsVisible && !!record && !editing;
      selectionBox.visible(visible);
      const bounds = record && annotationContext ? S.getElementBounds(record, annotationContext) : null;
      if (bounds) selectionBox.setAttrs(bounds);
      for (const {name, node} of handles) {
        node.visible(visible);
        if (bounds) {
          const point = S.getHandlePoint(bounds, name), size = 8 / view.scale;
          node.setAttrs({x: point.x - size / 2, y: point.y - size / 2, width: size, height: size, strokeWidth: 1 / view.scale});
        }
      }
      uiLayer.batchDraw();
    }
    const allocate = (width, height) => canvas(width, height).canvas;
    function renderAnnotations(items = current.annotations) {
      if (annotationContext && source) {
        annotationContext.clearRect(0, 0, source.width, source.height);
        upstream.drawElements(annotationContext, editing ? items.filter(a => a.id !== editing.record.id) : items,
          source, allocate, releaseCanvas);
      }
      selection(items); annotationLayer.batchDraw();
    }
    // Text editing ported from SketchBoardModal: a textarea uses exactly the same
    // font/line box as the upstream renderer. Never inject generated HTML.
    function positionText() {
      if (!editing || !source) return;
      const {record, input} = editing, size = S.measureTextElement(annotationContext, {...record, text: input.value || ' '});
      Object.assign(input.style, {left: (view.x + record.x * view.scale) + 'px', top: (view.y + record.y * view.scale) + 'px',
        width: Math.max(80, (size.width + record.fontSize) * view.scale) + 'px',
        height: Math.max(30, size.height * view.scale) + 'px', fontSize: record.fontSize * view.scale + 'px',
        fontFamily: S.SKETCH_FONT_FAMILY, lineHeight: String(S.SKETCH_LINE_HEIGHT), color: record.color});
    }
    function finishText(cancel = false) {
      if (!editing) return;
      const {record, input} = editing; editing = null;
      const value = input.value.slice(0, 2000); input.remove();
      if (!cancel) {
        const existed = current.annotations.some(a => a.id === record.id);
        const items = current.annotations.filter(a => a.id !== record.id);
        if (value.trim()) {
          const updated = freezeRecord({...record, text: value});
          // Keep z-order when editing an existing text object.
          const next = existed ? current.annotations.map(a => a.id === record.id ? updated : a) : [...items, updated];
          selected = record.id; commit(model(current.masks, next, current.feather));
        } else if (existed) {selected = null; commit(model(current.masks, items, current.feather));}
      }
      renderAnnotations(); changed();
    }
    function startText(record) {
      finishText(); cancelGesture();
      if (current.annotations.length >= LIMITS.annotations && !current.annotations.some(a => a.id === record.id))
        throw fail('Canvas annotation limit reached', 'COMMAND_LIMIT');
      const input = doc.createElement('textarea'); input.className = 'studio-inline-text';
      input.maxLength = 2000; input.value = record.text; input.setAttribute('aria-label', '编辑指导标注文字');
      input.addEventListener('input', () => {positionText(); changed();});
      input.addEventListener('keydown', event => {
        if (event.key === 'Escape') {event.preventDefault(); event.stopPropagation(); finishText(true); host.focus();}
        else if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
          event.preventDefault(); event.stopPropagation(); finishText(); host.focus();
        }
      });
      input.addEventListener('blur', () => finishText());
      editing = {record, input}; selected = record.id; host.append(input);
      positionText(); renderAnnotations(); input.focus({preventScroll: true}); changed();
    }
    function coverage() {
      const rgba = maskContext.getImageData(0, 0, source.width, source.height).data;
      const alpha = new Uint8Array(source.width * source.height);
      for (let i = 0; i < alpha.length; i++) alpha[i] = rgba[i * 4 + 3];
      return alpha;
    }
    function renderMask(check = true) {
      if (!source) return;
      maskContext.clearRect(0, 0, source.width, source.height);
      for (const command of current.masks) paintMask(maskContext, command);
      if (check) hasMask = coverage().some(value => value > 0);
      maskLayer.batchDraw();
    }
    function commit(next) {
      checkBudget(next);
      const masksChanged = current.masks !== next.masks;
      if (!history.push(current, next)) return;
      current = next; revision++;
      if (masksChanged) renderMask();
      renderAnnotations(); changed();
    }
    function cancelGesture() {
      const wasMask = gesture && ['brush', 'eraser'].includes(gesture.type);
      gesture = null;
      if (draftNode) { draftNode.destroy(); draftNode = null; }
      if (wasMask) renderMask(false);
      if (source) renderAnnotations();
      uiLayer.batchDraw();
    }
    function eventPoint(event) {
      return clientToStage(event, stage.content.getBoundingClientRect(), {width: stage.width(), height: stage.height()});
    }
    function documentPoint(point) { return baseGroup.getAbsoluteTransform().copy().invert().point(point); }
    function inDocument(p) { return p.x >= 0 && p.y >= 0 && p.x < source.width && p.y < source.height; }
    function limitedPoint(p) { return {x: clamp(p.x, -LIMITS.axis * 4, LIMITS.axis * 4), y: clamp(p.y, -LIMITS.axis * 4, LIMITS.axis * 4)}; }
    function capture(event) { try { host.setPointerCapture(event.pointerId); } catch (_) { /* detached/synthetic pointer */ } }
    function release(event) { try { if (host.hasPointerCapture(event.pointerId)) host.releasePointerCapture(event.pointerId); } catch (_) { /* already released */ } }
    function releaseAll() { const ids = [...pointers.keys()]; pointers.clear(); for (const pointerId of ids) release({pointerId}); }
    function pinchStart() {
      cancelGesture();
      const [a, b] = [...pointers.values()], center = {x: (a.x + b.x) / 2, y: (a.y + b.y) / 2};
      pinch = {distance: Math.max(1, Math.hypot(a.x - b.x, a.y - b.y)), scale: view.scale, anchor: documentPoint(center)};
      pinchBlocked = true;
    }
    function draftRecord(g) {
      const rect = normalizedRect(g.start, g.end);
      if (g.type === 'rectangle') return {...g.attrs, type: g.type, ...rect};
      if (['box', 'ellipse', 'arrow'].includes(g.type)) return {id: g.id, type: 'shape',
        shape: g.type === 'box' ? 'rect' : g.type, color: g.attrs.color, width: g.attrs.strokeWidth, start: g.start, end: g.end};
      if (g.type === 'pen') return {id: g.id, type: 'path', color: g.attrs.color, width: g.attrs.strokeWidth,
        points: g.points.reduce((items, x, i) => {if (i % 2 === 0) items.push({x, y: g.points[i + 1]}); return items;}, [])};
      return {...g.attrs, type: g.type, points: g.points};
    }
    function drawDraft() {
      if (draftNode) {draftNode.destroy(); draftNode = null;}
      const record = draftRecord(gesture);
      if (gesture.type === 'rectangle') draftNode = new K.Rect({...record, fill: SELECTION_COLOR, opacity: 0.35, listening: false});
      else if (gesture.type === 'lasso') draftNode = new K.Line({points: record.points, closed: true,
        fill: SELECTION_COLOR, opacity: 0.35, stroke: SELECTION_COLOR, strokeWidth: 1 / view.scale, listening: false});
      else if (gesture.type === 'guide-eraser') {
        const stroke = upstream.sampleEraseStroke({width: gesture.attrs.size, points: draftRecord({...gesture, type: 'pen'}).points});
        // Upstream drawScene applies live erase only to the ink canvas. Commit
        // attaches it to touched objects, so moving/resizing keeps erased holes.
        renderAnnotations(); S.drawEraseStroke(annotationContext, stroke);
        annotationLayer.batchDraw(); return;
      } else {renderAnnotations([...current.annotations, record]); return;}
      draftNode.visible(selectionVisible); ui.add(draftNode); uiLayer.batchDraw();
    }
    function pointerDown(event) {
      if (disposed || !source || editable(event.target) || (event.button !== undefined && event.button !== 0 && event.button !== 1)) return;
      if (editing) {finishText(); return;}
      event.preventDefault(); host.focus({preventScroll: true}); capture(event);
      const point = eventPoint(event); pointers.set(event.pointerId, point);
      if (pointers.size > 1) { if (pointers.size === 2) pinchStart(); return; }
      if (pinchBlocked) return;
      const p = limitedPoint(documentPoint(point));
      if (tool === 'pan' || space || event.button === 1) {
        gesture = {type: 'pan', pointerId: event.pointerId, start: point, view: {...view}}; return;
      }
      if (tool === 'select') {
        const record = annotationsVisible ? current.annotations.find(a => a.id === selected) : null;
        const bounds = record ? S.getElementBounds(record, annotationContext) : null;
        const handle = bounds && S.SKETCH_HANDLES.find(name => {
          const q = S.getHandlePoint(bounds, name); return Math.hypot(p.x - q.x, p.y - q.y) <= 8 / view.scale;
        });
        if (handle) {
          const from = S.getTransformBounds(record, annotationContext);
          const boxPoint = S.getHandlePoint(bounds, handle), fromPoint = S.getHandlePoint(from, handle);
          gesture = {type: 'resize', pointerId: event.pointerId, start: p, end: p, record, handle, from,
            offset: {x: boxPoint.x - fromPoint.x, y: boxPoint.y - fromPoint.y}}; return;
        }
        const hit = annotationsVisible ? S.findTopElementAt(current.annotations, p, 6 / view.scale, annotationContext) : null;
        selected = hit ? hit.id : null; selection(); changed();
        if (hit) gesture = {type: 'move', pointerId: event.pointerId, start: p, end: p, record: hit};
        return;
      }
      selected = null; selection();
      if (!inDocument(p)) return;
      if (tool === 'text') {
        const hit = annotationsVisible ? S.findTopElementAt(current.annotations.filter(a => a.type === 'text'), p, 6 / view.scale, annotationContext) : null;
        // Existing sidebar text remains an optional insertion shortcut; blank
        // inserts an in-place editor instead of silently doing nothing.
        const record = hit || {id: 'annotation-' + (++nextObject), type: 'text', x: p.x, y: p.y,
          text, fontSize: S.getSketchFontSize(strokeWidth), color, width: strokeWidth};
        releaseAll();
        if (!hit && text.trim()) commit(model(current.masks, [...current.annotations, freezeRecord(record)], current.feather));
        else startText(record);
        return;
      }
      gesture = {type: tool, id: 'annotation-' + (++nextObject), pointerId: event.pointerId, start: p, end: p, points: [p.x, p.y],
        attrs: {x: 0, y: 0, size: brushSize, color, strokeWidth}};
      if (tool === 'brush' || tool === 'eraser') paintMask(maskContext, draftRecord(gesture));
      else drawDraft();
      maskLayer.batchDraw();
    }
    function pointerMove(event) {
      if (disposed || !source || !pointers.has(event.pointerId)) return;
      event.preventDefault(); const point = eventPoint(event); pointers.set(event.pointerId, point);
      if (pinch && pointers.size >= 2) {
        const [a, b] = [...pointers.values()], center = {x: (a.x + b.x) / 2, y: (a.y + b.y) / 2};
        const scale = clamp(pinch.scale * Math.hypot(a.x - b.x, a.y - b.y) / pinch.distance, 1 / 512, 32);
        view = {scale, x: center.x - pinch.anchor.x * scale, y: center.y - pinch.anchor.y * scale};
        fitted = false; applyView(); changed(); return;
      }
      if (!gesture || gesture.pointerId !== event.pointerId) return;
      if (gesture.type === 'pan') {
        view = {...gesture.view, x: gesture.view.x + point.x - gesture.start.x, y: gesture.view.y + point.y - gesture.start.y};
        fitted = false; applyView(); changed(); return;
      }
      const g = gesture, p = limitedPoint(documentPoint(point)); g.end = p;
      if (g.type === 'move' || g.type === 'resize') {
        if (g.type === 'move') g.preview = S.moveElement(g.record, p.x - g.start.x, p.y - g.start.y);
        else {
          const aspect = event.shiftKey && g.record.type !== 'text' ?
            (g.record.type === 'shape' ? 1 : g.from.width / Math.max(1, g.from.height)) : undefined;
          const to = S.resizeBounds(g.from, g.handle, {x: p.x - g.offset.x, y: p.y - g.offset.y}, 8, aspect);
          g.preview = S.transformElement(g.record, g.from, to, g.handle);
        }
        renderAnnotations(current.annotations.map(a => a.id === g.record.id ? g.preview : a)); return;
      }
      if (g.type === 'arrow' && event.shiftKey) g.end = S.snapToAngle(g.start, p);
      if (['brush', 'eraser', 'pen', 'lasso', 'guide-eraser'].includes(g.type)) {
        const samples = typeof event.getCoalescedEvents === 'function' ? event.getCoalescedEvents() : [];
        for (const sample of samples.length ? [...samples, event] : [event]) {
          const q = limitedPoint(documentPoint(eventPoint(sample))), points = g.points;
          const last = {x: points[points.length - 2], y: points[points.length - 1]};
          if (Math.hypot(q.x - last.x, q.y - last.y) < 0.25) continue;
          if (points.length / 2 >= LIMITS.strokePoints) {
            cancelGesture(); changed(fail('Stroke is too long; use shorter strokes', 'COMMAND_LIMIT')); return;
          }
          points.push(q.x, q.y);
          if (g.type === 'brush' || g.type === 'eraser') paintMask(maskContext,
            {...g.attrs, type: g.type, points: [last.x, last.y, q.x, q.y]});
        }
      }
      if (g.type === 'brush' || g.type === 'eraser') maskLayer.batchDraw();
      else drawDraft();
    }
    function pointerEnd(event, cancelled = false) {
      if (disposed) return;
      // Include a final endpoint even if a device omits the last pointermove.
      if (!cancelled && gesture && gesture.pointerId === event.pointerId) pointerMove(event);
      pointers.delete(event.pointerId);
      if (pinchBlocked) {
        pinch = null; if (!pointers.size) pinchBlocked = false; release(event); return;
      }
      const g = gesture;
      if (!g || g.pointerId !== event.pointerId) { release(event); return; }
      if (cancelled) { cancelGesture(); release(event); return; }
      gesture = null; if (draftNode) { draftNode.destroy(); draftNode = null; }
      try {
        if (g.type === 'move' || g.type === 'resize') {
          if (g.preview && (g.end.x !== g.start.x || g.end.y !== g.start.y)) commit(model(current.masks,
            current.annotations.map(a => a.id === g.record.id ? freezeRecord(g.preview) : a), current.feather));
          else renderAnnotations();
        } else if (g.type === 'guide-eraser') {
          const stroke = upstream.sampleEraseStroke({width: g.attrs.size, points: draftRecord({...g, type: 'pen'}).points});
          const items = upstream.eraseElements(current.annotations, stroke, annotationContext, source, allocate, releaseCanvas);
          if (items !== current.annotations) commit(model(current.masks, items.map(freezeRecord), current.feather));
          else renderAnnotations();
        } else if (g.type !== 'pan') {
          const record = draftRecord(g);
          if (['rectangle', 'box', 'ellipse'].includes(g.type) && (g.start.x === g.end.x || g.start.y === g.end.y)) {renderAnnotations(); return;}
          if (g.type === 'arrow' && Math.hypot(g.end.x - g.start.x, g.end.y - g.start.y) < 0.25) return;
          if (g.type === 'lasso') {
            let area = 0; const p = record.points;
            for (let i = 0; i < p.length; i += 2) { const j = (i + 2) % p.length; area += p[i] * p[j + 1] - p[j] * p[i + 1]; }
            if (p.length < 6 || Math.abs(area) < 0.5) return;
          }
          if (['brush', 'eraser', 'rectangle', 'lasso'].includes(g.type)) {
            commit(model([...current.masks, freezeRecord(record)], current.annotations, current.feather));
          } else commit(model(current.masks, [...current.annotations,
            freezeRecord(record)], current.feather));
        }
      } catch (error) { renderMask(); renderAnnotations(); changed(error); }
      finally { release(event); renderAnnotations(); uiLayer.batchDraw(); }
    }
    function fit() {
      alive(); if (!source) return;
      const scale = Math.min(Math.max(1, stage.width() - 32) / source.width, Math.max(1, stage.height() - 32) / source.height);
      view = {scale, x: (stage.width() - source.width * scale) / 2, y: (stage.height() - source.height * scale) / 2};
      fitted = true; applyView(); changed();
    }
    function zoomBy(factor) {
      loaded(); cancelGesture(); view = zoomAt(view, {x: stage.width() / 2, y: stage.height() / 2}, factor);
      fitted = false; applyView(); changed();
    }
    function undoRedo(redo) {
      alive(); finishText(); cancelGesture(); const next = redo ? history.redo() : history.undo();
      if (!next) return false;
      current = next; revision++; selected = null; renderMask(); renderAnnotations(); changed(); return true;
    }
    function deleteSelected() {
      alive(); finishText(); if (!annotationsVisible || !selected) return false; cancelGesture();
      const records = current.annotations.filter(a => a.id !== selected); selected = null;
      commit(model(current.masks, records, current.feather)); return true;
    }
    function keyDown(event) {
      if (disposed || !source || editable(event.target) || !host.contains(doc.activeElement)) return;
      const key = event.key.toLowerCase(), modifier = event.ctrlKey || event.metaKey;
      if (modifier && key === 'z') { event.preventDefault(); undoRedo(!!event.shiftKey); }
      else if (modifier && key === 'y') { event.preventDefault(); undoRedo(true); }
      else if (key === ' ' && !modifier) { event.preventDefault(); space = true; cursor(); }
      else if (key === 'escape') { event.preventDefault(); cancelGesture(); selected = null; selection(); changed(); }
      else if ((key === 'delete' || key === 'backspace') && selected) { event.preventDefault(); deleteSelected(); }
      else if ((key === '+' || key === '=') && !modifier) { event.preventDefault(); zoomBy(1.2); }
      else if (key === '-' && !modifier) { event.preventDefault(); zoomBy(1 / 1.2); }
      else if (key === '0' && !modifier) { event.preventDefault(); cancelGesture(); fit(); }
      else if (/^arrow(left|right|up|down)$/.test(key) && selected && annotationsVisible && !modifier) {
        event.preventDefault(); cancelGesture(); const distance = event.shiftKey ? 10 : 1;
        const dx = key === 'arrowleft' ? -distance : key === 'arrowright' ? distance : 0;
        const dy = key === 'arrowup' ? -distance : key === 'arrowdown' ? distance : 0;
        commit(model(current.masks, current.annotations.map(a => a.id === selected ?
          freezeRecord(S.moveElement(a, dx, dy)) : a), current.feather));
      }
    }
    listen('dblclick', event => {
      if (tool !== 'select' || !source || !annotationsVisible || editable(event.target)) return;
      const hit = S.findTopElementAt(current.annotations, documentPoint(eventPoint(event)), 6 / view.scale, annotationContext);
      if (hit && hit.type === 'text') {event.preventDefault(); startText(hit);}
    });
    listen('pointerdown', event => { try { pointerDown(event); } catch (error) { cancelGesture(); changed(error); } });
    listen('pointermove', event => { try { pointerMove(event); } catch (error) { cancelGesture(); changed(error); } });
    listen('pointerup', event => pointerEnd(event));
    listen('pointercancel', event => pointerEnd(event, true));
    listen('lostpointercapture', event => { if (pointers.has(event.pointerId)) pointerEnd(event, true); });
    listen('wheel', event => {
      if (!source || disposed || editable(event.target)) return;
      event.preventDefault(); cancelGesture(); const delta = event.deltaY * (event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? stage.height() : 1);
      view = zoomAt(view, eventPoint(event), Math.exp(clamp(-delta * 0.002, -2, 2)));
      fitted = false; applyView(); changed();
    }, {passive: false});
    listen('keydown', keyDown);
    listen('keyup', event => { if (event.key === ' ') { space = false; cursor(); } });
    listen('blur', () => { space = false; releaseAll(); pinch = null; pinchBlocked = false; cancelGesture(); cursor(); });
    const resize = () => {
      if (disposed) return;
      const center = source ? documentPoint({x: stage.width() / 2, y: stage.height() / 2}) : null;
      stage.size({width: Math.max(1, host.clientWidth || 1), height: Math.max(1, host.clientHeight || 1)});
      if (source && fitted) fit();
      else if (center) { view = {...view, x: stage.width() / 2 - center.x * view.scale, y: stage.height() / 2 - center.y * view.scale}; applyView(); }
    };
    const observer = typeof root.ResizeObserver === 'function' ? new root.ResizeObserver(resize) : null;
    if (observer) observer.observe(host);
    else if (root.addEventListener) root.addEventListener('resize', resize);
    cursor();

    async function load(blob) {
      alive(); finishText(true); const ticket = ++loadSequence, decoded = await decode(blob);
      if (disposed || ticket !== loadSequence) { releaseCanvas(decoded.canvas); throw fail('Image load was superseded', 'LOAD_SUPERSEDED'); }
      // Allocate first: a failed replacement must leave the existing source/history intact.
      let mask, ink;
      try { mask = canvas(decoded.width, decoded.height); ink = canvas(decoded.width, decoded.height); }
      catch (error) { releaseCanvas(decoded.canvas); if (mask) releaseCanvas(mask.canvas); throw error; }
      cancelGesture(); releaseAll(); pinch = null; pinchBlocked = false;
      const documentId = root.crypto && typeof root.crypto.randomUUID === 'function' ? root.crypto.randomUUID() :
        'studio-' + Date.now().toString(36) + '-' + (++documentSequence);
      if (source) releaseCanvas(source.canvas);
      releaseCanvas(maskCanvas); releaseCanvas(annotationCanvas);
      annotationCanvas = ink.canvas; annotationContext = ink.ctx;
      inkNode.setAttrs({image: annotationCanvas, width: decoded.width, height: decoded.height});
      source = {...decoded, documentId}; current = model(); revision = 0; hasMask = false; selected = null; history.clear();
      maskCanvas = mask.canvas; maskContext = mask.ctx;
      baseNode.setAttrs({image: source.canvas, width: source.width, height: source.height});
      maskNode.setAttrs({image: maskCanvas, width: source.width, height: source.height});
      for (const group of [baseGroup, maskGroup, annotations, ui]) group.setAttrs({clipX: 0, clipY: 0, clipWidth: source.width, clipHeight: source.height});
      renderAnnotations(); fit(); return state();
    }
    async function exportPNG() { loaded(); return png(source.canvas); }
    /**
     * Capture committed document inputs before any asynchronous encoding.
     * guideSelection is effective only for local edits; hasAnnotations continues
     * to mean real vector annotations, not merely the existence of a guide.
     * The public record/working map are frozen; RGBA and coverage stay private.
     * @param {{size?:string, local?:boolean, guideSelection?:boolean}} [options]
     */
    async function snapshot({size = 'auto', local = false, guideSelection = false} = {}) {
      loaded(); finishText(); cancelGesture();
      if (typeof local !== 'boolean') throw fail('Local must be a boolean', 'INVALID_ARGUMENT');
      if (typeof guideSelection !== 'boolean') throw fail('Guide selection must be a boolean', 'INVALID_ARGUMENT');
      guideSelection = local && guideSelection;
      // All state is captured synchronously BEFORE the first asynchronous encode.
      // Retain only the immutable decoded RGBA, never the old canvas/context.
      const original = {width: source.width, height: source.height, documentId: source.documentId, rgba: source.rgba};
      const frozen = current, documentRevision = revision, epoch = snapshotEpoch;
      const working = workingSize(size, original.width, original.height);
      const rawCoverage = local ? coverage() : null;
      if (local && !rawCoverage.some(value => value > 0)) throw fail('Draw a modification region first', 'EMPTY_MASK');
      const weights = local ? (frozen.feather ? inwardFeather(rawCoverage, original.width, original.height, frozen.feather) : rawCoverage) : null;
      if (local && !weights.some(value => value > 0)) throw fail('Feathering removed the modification region; adjust it first', 'EMPTY_MASK');
      const main = canvas(working.width, working.height), temporary = [main.canvas];
      let mask = null, guide = null;
      try {
        main.ctx.drawImage(source.canvas, working.offsetX, working.offsetY, original.width * working.scale, original.height * working.scale);
        if (weights) {
          mask = canvas(working.width, working.height); temporary.push(mask.canvas);
          const rendered = maskRGBA(weights, original.width, original.height, working);
          let effective = false;
          for (let i = 3; i < rendered.length; i += 4) if (rendered[i] < 255) { effective = true; break; }
          if (!effective) throw fail('The region disappears at this working size; enlarge it or choose a larger working size', 'EMPTY_WORKING_MASK');
          putRGBA(mask.ctx, rendered, working.width, working.height);
        }
        const layers = [{state: {}, getCanvas: () => main.canvas}];
        if (guideSelection) {
          const overlay = canvas(working.width, working.height); temporary.push(overlay.canvas);
          putRGBA(overlay.ctx, selectionGuideRGBA(rawCoverage, original.width, original.height, working), working.width, working.height);
          layers.push({state: {}, getCanvas: () => overlay.canvas});
        }
        // Actual InvokeAI compositor source combines base, selection indication and
        // annotations in a stable order. No InvokeAI job/store/login is imported.
        if (frozen.annotations.length) {
          const ink = canvas(original.width, original.height); temporary.push(ink.canvas);
          upstream.drawElements(ink.ctx, frozen.annotations, original, allocate, releaseCanvas);
          const vector = canvas(working.width, working.height); temporary.push(vector.canvas);
          vector.ctx.drawImage(ink.canvas, working.offsetX, working.offsetY, original.width * working.scale, original.height * working.scale);
          layers.push({state: {}, getCanvas: () => vector.canvas});
        }
        if (layers.length > 1) {
          const composite = upstream.compositeCanvas(layers, working, (width, height) => {
            const c = allocate(width, height); temporary.push(c); return c;
          });
          guide = {canvas: composite};
        }
        const [imageBlob, maskBlob, guideBlob] = await Promise.all([png(main.canvas), mask ? png(mask.canvas) : null, guide ? png(guide.canvas) : null]);
        if (disposed || epoch !== snapshotEpoch) throw fail('Snapshot was cleared', 'SNAPSHOT_SUPERSEDED');
        if (imageBlob.size > LIMITS.sourceBytes || (guideBlob && guideBlob.size > LIMITS.sourceBytes)) {
          throw fail('Working image exceeds 8 MiB; choose a smaller working size', 'IMAGE_BYTES');
        }
        if (maskBlob && maskBlob.size >= LIMITS.maskBytes) throw fail('Mask PNG must be smaller than 4 MB', 'MASK_BYTES');
        const result = Object.freeze({documentId: original.documentId, revision: documentRevision,
          width: original.width, height: original.height, image: imageBlob, mask: maskBlob, guide: guideBlob,
          working, local, guideSelection, hasMask: !!maskBlob, hasAnnotations: frozen.annotations.length > 0, feather: frozen.feather});
        snapshots.set(result, {original, weights, masks: frozen.masks, annotations: frozen.annotations, working});
        return result;
      } finally {
        for (const c of temporary) releaseCanvas(c);
      }
    }
    async function compose(resultBlob, submitted) {
      alive(); const frozen = submitted && snapshots.get(submitted);
      if (!frozen) return {blob: null, reason: 'unknown_snapshot'};
      if (!submitted.local || !frozen.weights) return {blob: null, reason: 'no_local_mask'};
      const epoch = snapshotEpoch;
      const valid = () => !disposed && epoch === snapshotEpoch && snapshots.get(submitted) === frozen;
      let decoded;
      try {
        decoded = await decode(resultBlob, true);
        if (!valid()) return {blob: null, reason: 'unknown_snapshot'};
        if (decoded.width !== frozen.working.width || decoded.height !== frozen.working.height) return {blob: null, reason: 'working_size_mismatch'};
        const original = frozen.original;
        const rgba = composeRGBA(original.rgba, decoded.rgba, frozen.weights, original.width, original.height, frozen.working);
        const target = canvas(original.width, original.height);
        try {
          putRGBA(target.ctx, rgba, original.width, original.height);
          const blob = await png(target.canvas);
          return valid() ? {blob} : {blob: null, reason: 'unknown_snapshot'};
        } finally { releaseCanvas(target.canvas); }
      } catch (error) { return {blob: null, reason: error.code || 'composition_failed'}; }
      finally { if (decoded) releaseCanvas(decoded.canvas); }
    }
    function selectedStyle(attrs) {
      if (!annotationsVisible || !['select', 'text'].includes(tool) || !selected) return;
      cancelGesture(); commit(model(current.masks, current.annotations.map(a => a.id === selected ? freezeRecord({...a, ...attrs}) : a), current.feather));
    }
    /** @param {boolean} value Observation only; no history/revision/snapshot changes. */
    function setSelectionVisible(value) {
      alive(); cancelGesture(); selectionVisible = !!value;
      maskLayer.visible(selectionVisible); maskLayer.batchDraw(); changed();
    }
    /** @param {boolean} value Observation only, including annotation selection UI. */
    function setAnnotationsVisible(value) {
      alive(); cancelGesture(); annotationsVisible = !!value;
      annotationLayer.visible(annotationsVisible); selection(); annotationLayer.batchDraw(); changed();
    }
    function resetDocument(preserveSnapshots = false) {
      // A new document cancels source loads, not already submitted paid work.
      // Identity/session resets must also invalidate all private snapshots first.
      finishText(true); loadSequence++;
      if (!preserveSnapshots) { snapshotEpoch++; snapshots = new WeakMap(); }
      cancelGesture(); releaseAll(); pinch = null; pinchBlocked = false; space = false;
      if (source) releaseCanvas(source.canvas);
      releaseCanvas(maskCanvas); releaseCanvas(annotationCanvas);
      annotationCanvas = null; annotationContext = null; inkNode.setAttrs({image: null, width: 0, height: 0});
      history.clear(); source = null; current = model(); revision = 0; hasMask = false; selected = null; nextObject = 0;
      baseNode.setAttrs({image: null, width: 0, height: 0}); maskNode.setAttrs({image: null, width: 0, height: 0});
      maskCanvas = null; maskContext = null;
      // When preserving, transient snapshot/composition canvases belong to their
      // pending operation and are released in its finally, never retained by a
      // WeakMap entry. Hard resets release everything immediately.
      if (!preserveSnapshots) for (const c of canvases) releaseCanvas(c);
      // Visibility, like the active tool/brush, is an observation preference.
      // Preserve it across new/load so external visibility controls stay in sync.
      maskLayer.visible(selectionVisible); annotationLayer.visible(annotationsVisible);
      view = {x: 0, y: 0, scale: 1}; fitted = true;
      for (const group of [baseGroup, maskGroup, annotations, ui]) group.setAttrs({clipX: 0, clipY: 0, clipWidth: 0, clipHeight: 0});
      renderAnnotations(); applyView(); cursor();
    }
    /**
     * New empty document; releases document buffers/history, retains editor/host.
     * Set preserveSnapshots:true only for New while waiting for late candidates:
     * old source/coverage and pending snapshot/compose operations remain valid.
     * Default false is the identity/session reset: pending snapshot() rejects
     * with SNAPSHOT_SUPERSEDED; compose() returns unknown_snapshot. Callers must
     * also drop their public Blob/URL references when resetting the session.
     * @param {{preserveSnapshots?:boolean}} [options]
     */
    function clear({preserveSnapshots = false} = {}) {
      alive();
      if (typeof preserveSnapshots !== 'boolean') throw fail('Preserve snapshots must be a boolean', 'INVALID_ARGUMENT');
      resetDocument(preserveSnapshots); changed(); return state();
    }
    function destroy() {
      if (disposed) return;
      resetDocument(); disposed = true;
      if (observer) observer.disconnect(); else if (root.removeEventListener) root.removeEventListener('resize', resize);
      for (const [name, fn, opts] of listeners) host.removeEventListener(name, fn, opts);
      stage.destroy();
      for (const [name, value] of [['tabindex', oldTab], ['role', oldRole], ['aria-label', oldLabel]]) {
        if (value === null) host.removeAttribute(name); else host.setAttribute(name, value);
      }
      host.style.touchAction = oldTouch; host.style.cursor = oldCursor;
    }
    return Object.freeze({load, state, finishText, exportPNG, snapshot, compose, fit, zoomBy, clear, destroy,
      setSelectionVisible, setAnnotationsVisible, setMaskVisible: setSelectionVisible,
      setTool(name) { alive(); if (!TOOLS.includes(name)) throw fail('Unknown canvas tool', 'INVALID_TOOL');
        finishText(); cancelGesture(); tool = name; if (name !== 'select') selected = null; selection(); cursor(); changed(); },
      setBrushSize(value) { alive(); finite(value, 'brush size'); brushSize = clamp(value, 1, 512); },
      setColor(value) { alive(); if (typeof value !== 'string' || !/^#[\da-f]{6}$/i.test(value)) throw fail('Use a six-digit hex color', 'INVALID_COLOR');
        color = value; selectedStyle({color}); },
      setStrokeWidth(value) { alive(); finite(value, 'stroke width'); strokeWidth = clamp(value, 1, 128); const record = current.annotations.find(a => a.id === selected);
        selectedStyle({width: strokeWidth, ...(record?.type === 'text' ? {fontSize: S.getSketchFontSize(strokeWidth)} : {})}); },
      setText(value) { alive(); if (typeof value !== 'string' || value.length > 2000) throw fail('Text must be at most 2000 characters', 'INVALID_TEXT');
        text = value; const record = current.annotations.find(a => a.id === selected);
        if (record && record.type === 'text') selectedStyle({text}); },
      setFeather(value) { loaded(); finite(value, 'feather'); if (value < 0 || value > 256) throw fail('Feather must be between 0 and 256 pixels', 'INVALID_ARGUMENT');
        cancelGesture(); if (value !== current.feather) commit(model(current.masks, current.annotations, value)); },
      deleteSelected,
      undo: () => undoRedo(false), redo: () => undoRedo(true),
      clearMask() { alive(); cancelGesture(); if (current.masks.length) commit(model([], current.annotations, current.feather)); },
      clearAnnotations() { alive(); finishText(true); cancelGesture(); selected = null;
        if (current.annotations.length) commit(model(current.masks, [], current.feather)); }});
  }
  return Object.freeze({create, helpers: Object.freeze({LIMITS, TOOLS, dimensions, letterbox, workingSize,
    validateWorking, inversePoint, clientToStage, clientToDocument, zoomAt, normalizedRect,
    inwardFeather, maskRGBA, selectionGuideRGBA, composeRGBA, samplePremultiplied, History, freezeRecord, checkBudget, paintMask, inspectRaster})});
});
