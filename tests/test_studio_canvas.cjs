'use strict';
/* Offline only: node --test tests/test_studio_canvas.cjs
 * Pure pixel/transform tests + a small deterministic DOM/2D/Konva API fixture.
 * The fixture is NOT browser/codec/antialiasing/performance acceptance. Official
 * vendor primitives are also checked separately, with dynamic code disabled.
 */
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const crypto = require('node:crypto');
const zlib = require('node:zlib');
const directory = path.join(__dirname, '../src/new_api_cockpit/static');
const engineSource = fs.readFileSync(path.join(directory, 'studio-canvas.js'), 'utf8');
const vendorSource = fs.readFileSync(path.join(directory, 'konva-10.7.1.min.js'), 'utf8');
const engine = require(path.join(directory, 'studio-canvas.js'));
const K = require(path.join(directory, 'konva-10.7.1.min.js'));
const H = engine.helpers;
const rgba = (...values) => new Uint8ClampedArray(values.flat());
const bytes = (...values) => new Uint8Array(values.flat());
const plain = value => JSON.parse(JSON.stringify(value));
const tick = async () => { for (let n = 0; n < 5; n++) await new Promise(resolve => setImmediate(resolve)); };
const deferred = () => { let resolve, reject; const promise = new Promise((a, b) => {resolve = a; reject = b;}); return {promise, resolve, reject}; };

test('vendor is the verified official 10.7.1 UMD package with MIT license', () => {
  assert.equal(K.version, '10.7.1');
  assert.equal(crypto.createHash('sha256').update(vendorSource).digest('hex'), '46bea57e1c6009ac237745df395498cd6dbc0a4d164584c1fcbff6471335be5c');
  const license = fs.readFileSync(path.join(directory, 'KONVA-LICENSE'), 'utf8');
  assert.match(license, /MIT License/); assert.match(license, /Permission is hereby granted/);
  for (const name of ['Stage', 'Layer', 'Group', 'Image', 'Line', 'Arrow', 'Rect', 'Ellipse', 'Text', 'Transform']) assert.equal(typeof K[name], 'function');
});
test('browser globals and vendor initialize with string/wasm code generation disabled', () => {
  const context = vm.createContext({}, {codeGeneration: {strings: false, wasm: false}});
  vm.runInContext(vendorSource, context); vm.runInContext(engineSource, context);
  assert.equal(context.Konva.version, '10.7.1'); assert.equal(typeof context.CockpitImageCanvas.create, 'function');
  assert.throws(() => vm.runInContext('eval("1")', context), /Code generation/);
  assert.doesNotMatch(engineSource, /\beval\s*\(|new\s+Function\s*\(|\bfetch\s*\(|localStorage|sessionStorage|innerHTML|https?:\/\//);
  assert.doesNotMatch(vendorSource, /\beval\s*\(|new\s+Function\s*\(/);
});
test('letterbox uses a known aspect-preserving transform, including fractional offsets', () => {
  assert.deepEqual(H.letterbox(8, 4, 8, 8), {width: 8, height: 8, scale: 1, offsetX: 0, offsetY: 2});
  assert.deepEqual(H.letterbox(3, 7, 10, 10), {width: 10, height: 10, scale: 10 / 7, offsetX: 20 / 7, offsetY: 0});
  assert.deepEqual(H.workingSize('auto', 7, 5), {width: 7, height: 5, scale: 1, offsetX: 0, offsetY: 0});
  assert.deepEqual(H.workingSize('1024x1536', 2000, 1000), {width: 1024, height: 1536, scale: 0.512, offsetX: 0, offsetY: 512});
  assert.ok(Object.isFrozen(H.letterbox(3, 7, 10, 10)));
});
test('invalid or resource-exceeding sizes are explicit errors, not silent resize', () => {
  for (const size of ['fill', '0x1', '1.5x2', '1X2', '1x2x3', ' 4x5', {}, null]) assert.throws(() => H.workingSize(size, 4, 5));
  for (const dim of [[0, 1], [-1, 2], [1.2, 2], [Infinity, 2], [16385, 1], [4001, 4001]]) assert.throws(() => H.dimensions(...dim));
  assert.throws(() => H.workingSize('auto', 4000, 4000), {code: 'IMAGE_DIMENSIONS'});
  assert.throws(() => H.validateWorking({...H.letterbox(4, 2, 4, 4), offsetY: 0}, 4, 2), {code: 'UNKNOWN_MAPPING'});
  assert.throws(() => H.validateWorking(null, 4, 2), {code: 'UNKNOWN_MAPPING'});
});
test('CSS dimensions map once; inverse pointer mapping includes nested rotate/scale/translate', () => {
  const parent = new K.Group({x: 23, y: 35, scaleX: 1.4, scaleY: 0.8, rotation: 30});
  const child = new K.Group({x: -7, y: 20, scaleX: 3, scaleY: 2, rotation: -15}); parent.add(child);
  const expected = {x: 17.25, y: 21.75}, transform = child.getAbsoluteTransform();
  const p = transform.point(expected), rect = {left: 50, top: 70, width: 1200, height: 400};
  const actual = H.clientToDocument({clientX: rect.left + p.x * 2, clientY: rect.top + p.y * 0.5}, rect,
    {width: 600, height: 800}, transform.getMatrix());
  assert.ok(Math.abs(actual.x - expected.x) < 1e-10); assert.ok(Math.abs(actual.y - expected.y) < 1e-10);
  assert.throws(() => H.inversePoint([0, 0, 0, 0, 1, 2], expected), {code: 'INVALID_TRANSFORM'});
  assert.throws(() => H.clientToStage({clientX: 1, clientY: 2}, {...rect, width: 0}, {width: 1, height: 1}));
  parent.destroy();
});
test('zoom stays anchored and clamps without moving the anchor document point', () => {
  const view = {x: -20, y: 13, scale: 0.5}, anchor = {x: 43, y: 89};
  for (const factor of [2, 0.01, 10000]) {
    const next = H.zoomAt(view, anchor, factor);
    assert.ok(Math.abs((anchor.x - next.x) / next.scale - (anchor.x - view.x) / view.scale) < 1e-9);
    assert.ok(next.scale >= 1 / 512 && next.scale <= 32);
  }
  assert.throws(() => H.zoomAt(view, anchor, 0)); assert.throws(() => H.zoomAt(view, anchor, NaN));
  assert.deepEqual(H.normalizedRect({x: 8, y: 9}, {x: 2, y: 3}), {x: 2, y: 3, width: 6, height: 6});
});
test('API mask is inverse coverage, including partial alpha; padding always protected', () => {
  const mask = H.maskRGBA(bytes(0, 255, 128, 64), 2, 2);
  assert.deepEqual([...mask], [0, 0, 0, 255, 0, 0, 0, 0, 0, 0, 0, 127, 0, 0, 0, 191]);
  const boxed = H.maskRGBA(bytes(255, 255, 255, 255), 4, 1, H.letterbox(4, 1, 4, 3));
  assert.deepEqual([...boxed.filter((_, i) => i % 4 === 3)], [255, 255, 255, 255, 0, 0, 0, 0, 255, 255, 255, 255]);
});
test('fractional letterbox/downscale mask never writes edit alpha in padding', () => {
  for (const [w, h, ww, wh] of [[3, 7, 4, 4], [11, 3, 7, 6], [1, 1, 5, 4]]) {
    const t = H.letterbox(w, h, ww, wh), mask = H.maskRGBA(new Uint8Array(w * h).fill(255), w, h, t);
    for (let y = 0; y < wh; y++) for (let x = 0; x < ww; x++) {
      const sx = (x + 0.5 - t.offsetX) / t.scale, sy = (y + 0.5 - t.offsetY) / t.scale;
      if (sx < 0 || sx >= w || sy < 0 || sy >= h) assert.equal(mask[(y * ww + x) * 4 + 3], 255);
    }
  }
});
test('S=0 copies every original byte, including hidden transparent RGB', () => {
  const original = rgba([23, 45, 67, 0], [255, 1, 6, 4], [2, 3, 4, 255]);
  const result = rgba([0, 255, 0, 255], [9, 8, 7, 255], [0, 0, 0, 0]);
  assert.deepEqual(H.composeRGBA(original, result, bytes(0, 0, 0), 3, 1, H.letterbox(3, 1, 3, 1)), original);
});
test('premultiplied blending handles transparent source/result instead of interpolating hidden RGB', () => {
  const original = rgba([200, 10, 20, 255], [255, 0, 0, 0]);
  const result = rgba([1, 250, 255, 0], [0, 80, 160, 128]);
  assert.deepEqual([...H.composeRGBA(original, result, bytes(128, 128), 2, 1, H.letterbox(2, 1, 2, 1))],
    [200, 10, 20, 127, 0, 80, 160, 64]);
});
test('both semi-transparent inputs mix alpha and premultiplied channels deterministically', () => {
  const actual = H.composeRGBA(rgba(200, 0, 0, 128), rgba(0, 0, 200, 64), bytes(128), 1, 1, H.letterbox(1, 1, 1, 1));
  assert.deepEqual([...actual], [133, 0, 67, 96]);
  assert.deepEqual(H.composeRGBA(rgba(200, 0, 0, 128), rgba(0, 0, 200, 64), bytes(128), 1, 1, H.letterbox(1, 1, 1, 1)), actual);
});
test('known inverse letterbox maps only result, preserves source dimensions and unselected bytes', () => {
  const original = rgba([1, 2, 3, 0], [4, 5, 6, 255], [7, 8, 9, 33], [10, 11, 12, 255]);
  const result = rgba([255, 0, 0, 255], [255, 0, 0, 255], [0, 80, 0, 255], [0, 90, 0, 255],
    [0, 100, 0, 255], [0, 110, 0, 255], [255, 0, 0, 255], [255, 0, 0, 255]);
  assert.deepEqual([...H.composeRGBA(original, result, bytes(0, 255, 0, 255), 2, 2, H.letterbox(2, 2, 2, 4))],
    [1, 2, 3, 0, 0, 90, 0, 255, 7, 8, 9, 33, 0, 110, 0, 255]);
});
test('bilinear sampling is premultiplied, never leaking hidden transparent colors', () => {
  assert.deepEqual(H.samplePremultiplied(rgba([255, 0, 0, 0], [0, 100, 200, 255]), 2, 1, 0.5, 0), [0, 50, 100, 0.5]);
  const result = H.composeRGBA(rgba(9, 9, 9, 255), rgba([255, 0, 0, 0], [0, 100, 200, 255]), bytes(255), 1, 1, H.letterbox(1, 1, 2, 1));
  // The square source occupies a known centered 1x1 content area in the 2x1 result.
  assert.deepEqual([...result], [0, 100, 200, 128]);
});
test('compositor and mask reject mismatched source/result/coverage arrays and unknown transforms', () => {
  const t = H.letterbox(1, 1, 1, 1);
  assert.throws(() => H.composeRGBA(rgba(0, 0, 0, 0), rgba(0), bytes(0), 1, 1, t), {code: 'INVALID_PIXELS'});
  assert.throws(() => H.composeRGBA(rgba(0), rgba(0, 0, 0, 0), bytes(0), 1, 1, t));
  assert.throws(() => H.maskRGBA(bytes(), 1, 1));
  assert.throws(() => H.composeRGBA(rgba(0, 0, 0, 0), rgba(0, 0, 0, 0), bytes(0), 1, 1, {...t, scale: 2}), {code: 'UNKNOWN_MAPPING'});
});
test('default feather is a copy and inward feather cannot modify anything outside frozen D', () => {
  const coverage = new Uint8Array(49); for (let y = 1; y <= 5; y++) for (let x = 1; x <= 5; x++) coverage[y * 7 + x] = 255;
  const copy = H.inwardFeather(coverage, 7, 7); assert.deepEqual(copy, coverage); assert.notEqual(copy, coverage);
  const weights = H.inwardFeather(coverage, 7, 7, 2);
  assert.equal(weights[3 * 7 + 3], 255); assert.equal(weights[1 * 7 + 1], 64);
  for (let i = 0; i < 49; i++) if (!coverage[i]) assert.equal(weights[i], 0);
  assert.equal(coverage[1 * 7 + 1], 255); assert.throws(() => H.inwardFeather(coverage, 7, 7, 257));
});
test('Euclidean inward weights agree with a brute-force distance reference for holes, borders and thin regions', () => {
  let seed = 109;
  for (let n = 0; n < 20; n++) {
    const w = 7, h = 6, c = new Uint8Array(w * h);
    for (let i = 0; i < c.length; i++) { seed = (seed * 1664525 + 1013904223) >>> 0; c[i] = seed % 5 ? (seed % 255) + 1 : 0; }
    const actual = H.inwardFeather(c, w, h, 3);
    for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
      let d2 = Math.min((x + 1) ** 2, (w - x) ** 2, (y + 1) ** 2, (h - y) ** 2);
      for (let sy = 0; sy < h; sy++) for (let sx = 0; sx < w; sx++) if (!c[sy * w + sx]) d2 = Math.min(d2, (x - sx) ** 2 + (y - sy) ** 2);
      const expected = Math.round(c[y * w + x] * Math.min(1, Math.max(0, (Math.sqrt(d2) - 0.5) / 3)));
      assert.equal(actual[y * w + x], expected);
    }
  }
});
test('history stores shared immutable commands, has bounded undo and invalidates redo on branching', () => {
  const history = new H.History(2), a = Object.freeze({}), b = Object.freeze({}), c = Object.freeze({}), d = Object.freeze({});
  assert.equal(history.push(a, a), false); history.push(a, b); history.push(b, c); history.push(c, d);
  assert.equal(history.past.length, 2); assert.equal(history.past[0].before, b);
  assert.equal(history.undo(), c); assert.equal(history.undo(), b); assert.equal(history.undo(), null);
  assert.equal(history.redo(), c); history.push(c, a); assert.equal(history.redo(), null);
  history.clear(); assert.equal(history.undo(), null);
  const record = H.freezeRecord({type: 'brush', points: [1, 2], size: 3});
  assert.ok(Object.isFrozen(record.points)); assert.throws(() => {record.points[0] = 5;}, TypeError);
  assert.throws(() => H.checkBudget({masks: Array(1001).fill(record), annotations: []}), {code: 'COMMAND_LIMIT'});
  assert.throws(() => new H.History(0));
});

function crc32(data) {
  let crc = 0xffffffff;
  for (const byte of data) { crc ^= byte; for (let j = 0; j < 8; j++) crc = (crc >>> 1) ^ ((crc & 1) ? 0xedb88320 : 0); }
  return (crc ^ 0xffffffff) >>> 0;
}
function pngChunk(tag, data) {
  const t = Buffer.from(tag), size = Buffer.alloc(4), crc = Buffer.alloc(4); size.writeUInt32BE(data.length);
  crc.writeUInt32BE(crc32(Buffer.concat([t, data]))); return Buffer.concat([size, t, data, crc]);
}
function pngBytes(w, h, data = new Uint8ClampedArray(w * h * 4), extra = []) {
  const ihdr = Buffer.alloc(13); ihdr.writeUInt32BE(w); ihdr.writeUInt32BE(h, 4); ihdr[8] = 8; ihdr[9] = 6;
  const rows = Buffer.alloc(h * (w * 4 + 1));
  for (let y = 0; y < h; y++) rows.set(data.subarray(y * w * 4, (y + 1) * w * 4), y * (w * 4 + 1) + 1);
  return Buffer.concat([Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]), pngChunk('IHDR', ihdr), ...extra,
    pngChunk('IDAT', zlib.deflateSync(rows)), pngChunk('IEND', Buffer.alloc(0))]);
}
test('raster header limits and animations reject before bitmap allocation', () => {
  assert.deepEqual(H.inspectRaster(pngBytes(2, 3)), {kind: 'png', width: 2, height: 3});
  const huge = pngBytes(1, 1); huge.writeUInt32BE(10000, 16); huge.writeUInt32BE(10000, 20);
  assert.throws(() => H.inspectRaster(huge), {code: 'IMAGE_DIMENSIONS'});
  assert.throws(() => H.inspectRaster(pngBytes(2, 3, undefined, [pngChunk('acTL', Buffer.alloc(8))])), {code: 'IMAGE_ANIMATION'});
  assert.throws(() => H.inspectRaster(Buffer.from('<svg/>')), {code: 'IMAGE_TYPE'});
  const truncated = pngBytes(2, 3); truncated.writeUInt32BE(5000, 8);
  assert.throws(() => H.inspectRaster(truncated), {code: 'IMAGE_DECODE'});
});
function webpHeader(w, h, {kind = 'VP8X', animated = false} = {}) {
  let data;
  if (kind === 'VP8L') { data = Buffer.alloc(5); data[0] = 47; data.writeUInt32LE((w - 1) + ((h - 1) << 14), 1); }
  else { data = Buffer.alloc(10); if (kind === 'VP8X') {
    data[0] = animated ? 2 : 0; data.writeUIntLE(w - 1, 4, 3); data.writeUIntLE(h - 1, 7, 3);
  } else { data.set([157, 1, 42], 3); data.writeUInt16LE(w, 6); data.writeUInt16LE(h, 8); } }
  const header = Buffer.from('RIFF\0\0\0\0WEBP'), size = Buffer.alloc(4); size.writeUInt32LE(data.length);
  const out = Buffer.concat([header, Buffer.from(kind), size, data, ...(data.length % 2 ? [Buffer.alloc(1)] : [])]); out.writeUInt32LE(out.length - 8, 4); return out;
}
test('JPEG SOF and all WebP dimension header variants are handled without EXIF re-rotation', () => {
  const jpeg = Buffer.from([255, 216, 255, 225, 0, 4, 0, 0, 255, 192, 0, 11, 8, 0, 3, 0, 2, 1, 1, 17, 0, 255, 217]);
  assert.deepEqual(H.inspectRaster(jpeg), {kind: 'jpeg', width: 2, height: 3});
  for (const kind of ['VP8X', 'VP8 ', 'VP8L']) assert.deepEqual(H.inspectRaster(webpHeader(7, 5, {kind})), {kind: 'webp', width: 7, height: 5});
  assert.throws(() => H.inspectRaster(webpHeader(7, 5, {animated: true})), {code: 'IMAGE_ANIMATION'});
  assert.throws(() => H.inspectRaster(webpHeader(10000, 10000)), {code: 'IMAGE_DIMENSIONS'});
});

// Minimal raster fixture: hard pixel-center geometry, no browser antialiasing.
class FixtureCanvas {
  constructor(harness) { this.h = harness; this.style = {}; this._width = 1; this._height = 1; this.data = new Uint8ClampedArray(4); this.context = new FixtureContext(this); }
  get width() {return this._width;} set width(n) {this._width = n; this.data = new Uint8ClampedArray(n * this._height * 4);}
  get height() {return this._height;} set height(n) {this._height = n; this.data = new Uint8ClampedArray(n * this._width * 4);}
  getContext() {return this.context;}
  toBlob(callback) {
    const blob = this.h.image(this.width, this.height, this.data.slice());
    if (this.h.holdPNG) this.h.pendingPNG.push(() => callback(blob)); else queueMicrotask(() => callback(blob));
  }
}
class FixtureContext {
  constructor(canvas) {this.canvas = canvas; this.matrix = [1, 0, 0, 1, 0, 0]; this.globalCompositeOperation = 'source-over'; this.fillStyle = '#000000'; this.strokeStyle = '#000000'; this.stack = []; this.lineWidth = 1;}
  save() {this.stack.push({matrix: [...this.matrix], globalCompositeOperation: this.globalCompositeOperation, fillStyle: this.fillStyle, strokeStyle: this.strokeStyle, lineWidth: this.lineWidth});}
  restore() {Object.assign(this, this.stack.pop());}
  setTransform(a, b, c, d, e, f) {this.matrix = [a, b, c, d, e, f];}
  transform(a, b, c, d, e, f) {const t = new K.Transform(this.matrix); t.multiply(new K.Transform([a, b, c, d, e, f])); this.matrix = t.getMatrix();}
  translate(x, y) {this.transform(1, 0, 0, 1, x, y);} scale(x, y) {this.transform(x, 0, 0, y, 0, 0);}
  rotate(angle) {this.transform(Math.cos(angle), Math.sin(angle), -Math.sin(angle), Math.cos(angle), 0, 0);}
  resetTransform() {this.matrix = [1, 0, 0, 1, 0, 0];}
  setLineDash(_) {} getLineDash() {return [];}
  rect(x, y, w, h) {this.points.push([x, y], [x + w, y], [x + w, y + h], [x, y + h], [x, y]);}
  clip() {} // Clip fidelity is a browser acceptance item, not this fixture.
  measureText(text) {return {width: text.length * 8, actualBoundingBoxAscent: 12, actualBoundingBoxDescent: 4};}
  fillText(text, x, y) {this.fillRect(x, y, text.length * 8, 16);} strokeText(text, x, y) {this.fillText(text, x, y);}
  createImageData(w, h) {return {data: new Uint8ClampedArray(w * h * 4), width: w, height: h};}
  getImageData() {return {data: this.canvas.data.slice()};}
  putImageData(pixels) {this.canvas.data.set(pixels.data);}
  clearRect() {this.canvas.data.fill(0);}
  color(text) {return /^#[\da-f]{6}$/i.test(text) ? [parseInt(text.slice(1, 3), 16), parseInt(text.slice(3, 5), 16), parseInt(text.slice(5, 7), 16), 255] : [0, 0, 0, 255];}
  blend(index, value) {
    const d = this.canvas.data;
    if (this.globalCompositeOperation === 'destination-out') {d.fill(0, index, index + 4); return;}
    const a = value[3] / 255, oa = d[index + 3] / 255, alpha = a + oa * (1 - a);
    for (let c = 0; c < 3; c++) d[index + c] = alpha ? Math.round((value[c] * a + d[index + c] * oa * (1 - a)) / alpha) : 0;
    d[index + 3] = Math.round(alpha * 255);
  }
  eachPixel(predicate, color) {
    for (let y = 0; y < this.canvas.height; y++) for (let x = 0; x < this.canvas.width; x++) {
      const p = H.inversePoint(this.matrix, {x: x + 0.5, y: y + 0.5});
      if (predicate(p.x, p.y)) this.blend((y * this.canvas.width + x) * 4, color);
    }
  }
  fillRect(x, y, width, height) {this.eachPixel((px, py) => px >= x && px < x + width && py >= y && py < y + height, this.color(this.fillStyle));}
  beginPath() {this.points = []; this.circle = null;}
  moveTo(x, y) {this.points.push([x, y]);} lineTo(x, y) {this.points.push([x, y]);}
  closePath() {} arc(x, y, radius) {this.circle = {x, y, radius};}
  fill() {
    if (this.circle) {const {x, y, radius} = this.circle; this.eachPixel((px, py) => Math.hypot(px - x, py - y) <= radius, this.color(this.fillStyle)); return;}
    const p = this.points;
    this.eachPixel((x, y) => {let inside = false; for (let i = 0, j = p.length - 1; i < p.length; j = i++) {
      if ((p[i][1] > y) !== (p[j][1] > y) && x < (p[j][0] - p[i][0]) * (y - p[i][1]) / (p[j][1] - p[i][1]) + p[i][0]) inside = !inside;
    } return inside;}, this.color(this.fillStyle));
  }
  stroke() {
    const p = this.points;
    this.eachPixel((x, y) => p.some(([ax, ay], i) => {
      if (!i) return false; const [bx, by] = p[i - 1], dx = bx - ax, dy = by - ay;
      const t = Math.max(0, Math.min(1, ((x - ax) * dx + (y - ay) * dy) / (dx * dx + dy * dy || 1)));
      return Math.hypot(x - ax - t * dx, y - ay - t * dy) <= this.lineWidth / 2;
    }), this.color(this.strokeStyle));
  }
  drawImage(image, ...args) {
    let [dx = 0, dy = 0, dw = image.width, dh = image.height] = args;
    const pixels = image.data, w = image.width, h = image.height;
    for (let y = 0; y < this.canvas.height; y++) for (let x = 0; x < this.canvas.width; x++) {
      const p = H.inversePoint(this.matrix, {x: x + 0.5, y: y + 0.5});
      const sx = (p.x - dx) * w / dw, sy = (p.y - dy) * h / dh;
      if (sx >= 0 && sx < w && sy >= 0 && sy < h) this.blend((y * this.canvas.width + x) * 4, pixels.subarray((Math.floor(sy) * w + Math.floor(sx)) * 4, (Math.floor(sy) * w + Math.floor(sx)) * 4 + 4));
    }
  }
}
function fixtureKonva(h) {
  class Node {
    constructor(attrs = {}) {this.attrs = {...attrs}; this.children = []; this.kind = this.constructor.name;}
    add(...nodes) {for (const n of nodes) {n.parent = this; this.children.push(n);} return this;}
    getChildren() {return this.children;} id() {return this.attrs.id;}
    setAttrs(attrs) {Object.assign(this.attrs, attrs);}
    x() {return this.attrs.x || 0;} y() {return this.attrs.y || 0;}
    width() {return this.attrs.width || 0;} height() {return this.attrs.height || 0;}
    visible(value) {if (value !== undefined) this.attrs.visible = value; return this.attrs.visible !== false;}
    listening(value) {this.attrs.listening = value;} strokeWidth(n) {this.attrs.strokeWidth = n;} dash(n) {this.attrs.dash = n;}
    getAbsoluteTransform() {
      const t = this.parent ? this.parent.getAbsoluteTransform().copy() : new K.Transform();
      t.translate(this.x(), this.y()); t.scale(this.attrs.scaleX || 1, this.attrs.scaleY || 1); return t;
    }
    getClientRect() {
      const a = this.attrs;
      if (a.points) {const xs = a.points.filter((_, i) => !(i % 2)), ys = a.points.filter((_, i) => i % 2);
        return {x: this.x() + Math.min(...xs), y: this.y() + Math.min(...ys), width: Math.max(...xs) - Math.min(...xs), height: Math.max(...ys) - Math.min(...ys)};}
      if (this.kind === 'Ellipse') return {x: this.x() - a.radiusX, y: this.y() - a.radiusY, width: a.radiusX * 2, height: a.radiusY * 2};
      if (this.kind === 'Text') return {x: this.x(), y: this.y(), width: a.text.length * a.fontSize / 2, height: a.fontSize};
      return {x: this.x(), y: this.y(), width: this.width(), height: this.height()};
    }
    destroyChildren() {for (const n of [...this.children]) n.destroy();}
    destroy() {this.destroyChildren(); if (this.parent) this.parent.children = this.parent.children.filter(n => n !== this); this.destroyed = true;}
    batchDraw() {}
    toCanvas({width, height}) {
      const c = new FixtureCanvas(h); h.allocations.push(c); c.width = width; c.height = height; const ctx = c.context;
      const walk = node => {
        ctx.save(); const matrix = node.getAbsoluteTransform().getMatrix(); ctx.setTransform(...matrix);
        const a = node.attrs; ctx.strokeStyle = a.stroke; ctx.fillStyle = a.fill || a.stroke; ctx.lineWidth = a.strokeWidth || 1;
        if (node.kind === 'Rect' && a.stroke) {ctx.beginPath(); ctx.moveTo(0, 0); ctx.lineTo(a.width, 0); ctx.lineTo(a.width, a.height); ctx.lineTo(0, a.height); ctx.lineTo(0, 0); ctx.stroke();}
        else if (['Line', 'Arrow'].includes(node.kind)) {ctx.beginPath(); ctx.moveTo(a.points[0], a.points[1]); for (let i = 2; i < a.points.length; i += 2) ctx.lineTo(a.points[i], a.points[i + 1]); ctx.stroke();}
        else if (node.kind === 'Text') ctx.fillRect(0, 0, a.text.length * a.fontSize / 2, a.fontSize);
        else if (node.kind === 'Ellipse') {ctx.beginPath(); ctx.arc(0, 0, Math.min(a.radiusX, a.radiusY)); ctx.fill();}
        ctx.restore(); for (const child of node.children) walk(child);
      };
      walk(this); return c;
    }
  }
  class Stage extends Node {
    constructor(attrs) {super(attrs); h.stage = this; this.content = {getBoundingClientRect: () => h.host.rect};}
    size(attrs) {this.setAttrs(attrs);}
    getIntersection(p) {
      const group = this.children[2].children[0], d = group.getAbsoluteTransform().copy().invert().point(p);
      return [...group.children].reverse().find(n => {const r = n.getClientRect(); return d.x >= r.x - 6 && d.x <= r.x + r.width + 6 && d.y >= r.y - 6 && d.y <= r.y + r.height + 6;}) || null;
    }
  }
  return {version: '10.7.1', Stage, Layer: class Layer extends Node {}, Group: class Group extends Node {},
    Image: class Image extends Node {}, Rect: class Rect extends Node {}, Ellipse: class Ellipse extends Node {},
    Arrow: class Arrow extends Node {}, Line: class Line extends Node {}, Text: class Text extends Node {}};
}
function harness({fallback = false} = {}) {
  const h = {images: new WeakMap(), changes: [], allocations: [], captures: new Set(), bitmapCalls: [], closed: 0,
    holdPNG: false, pendingPNG: [], decoder: null, urls: new Map(), revoked: [], observers: []};
  h.image = (w, height, data = new Uint8ClampedArray(w * height * 4).fill(255), fileBytes) => {
    const blob = new Blob([fileBytes || pngBytes(w, height, data)], {type: 'image/png'});
    h.images.set(blob, {width: w, height, data: data.slice()}); return blob;
  };
  const attrs = new Map(), events = new Map(), document = {activeElement: null, createElement(tag) {
    assert.equal(tag, 'canvas'); const c = new FixtureCanvas(h); h.allocations.push(c); return c;
  }};
  h.host = {ownerDocument: document, clientWidth: 200, clientHeight: 160,
    rect: {left: 17, top: 30, width: 400, height: 80}, style: {cursor: 'pointer', touchAction: 'auto'},
    getAttribute: name => attrs.get(name) ?? null, setAttribute: (name, value) => attrs.set(name, value), removeAttribute: name => attrs.delete(name),
    addEventListener(name, fn) {const a = events.get(name) || []; a.push(fn); events.set(name, a);},
    removeEventListener(name, fn) {events.set(name, events.get(name).filter(f => f !== fn));},
    focus() {document.activeElement = h.host;}, contains(target) {return target === h.host;},
    setPointerCapture(id) {h.captures.add(id);}, hasPointerCapture: id => h.captures.has(id), releasePointerCapture(id) {h.captures.delete(id);}};
  h.decode = async blob => {if (h.decoder) await h.decoder(blob); const frame = h.images.get(blob); assert.ok(frame, 'fixture decoder only accepts known fixture images'); return {...frame, close() {h.closed++;}};};
  const context = {Blob, Date, crypto: {randomUUID: () => 'document-' + (++h.documentID || (h.documentID = 1))},
    Konva: fixtureKonva(h), createImageBitmap: async (blob, options) => {h.bitmapCalls.push({blob, options}); return h.decode(blob);},
    ResizeObserver: class {constructor(fn) {this.fn = fn; h.observers.push(this);} observe() {} disconnect() {this.disconnected = true;}},
    URL: {createObjectURL(blob) {const url = 'blob:fixture-' + h.urls.size; h.urls.set(url, blob); return url;}, revokeObjectURL(url) {h.revoked.push(url); h.urls.delete(url);}},
    Image: class {set src(url) {h.decode(h.urls.get(url)).then(frame => {this.naturalWidth = frame.width; this.naturalHeight = frame.height; this.data = frame.data; this.width = frame.width; this.height = frame.height; this.onload();}, error => this.onerror(error));}}};
  if (fallback) delete context.createImageBitmap;
  vm.createContext(context, {codeGeneration: {strings: false, wasm: false}}); vm.runInContext(engineSource, context);
  h.editor = context.CockpitImageCanvas.create(h.host, {onChange: info => h.changes.push(info)}); h.document = document; h.events = events;
  h.point = (x, y) => {const p = h.stage.children[0].children[0].getAbsoluteTransform().point({x, y}), rect = h.host.rect;
    return {clientX: rect.left + p.x * rect.width / h.stage.width(), clientY: rect.top + p.y * rect.height / h.stage.height()};};
  h.fire = (name, detail = {}) => {
    const event = {target: h.host, pointerId: 1, button: 0, preventDefault() {this.prevented = true;}, ...detail};
    for (const fn of events.get(name) || []) fn(event); return event;
  };
  h.drag = (tool, points) => {h.editor.setTool(tool); h.fire('pointerdown', h.point(...points[0]));
    for (const p of points.slice(1)) h.fire('pointermove', h.point(...p)); h.fire('pointerup', h.point(...points.at(-1)));};
  h.nodes = () => h.stage.children[2].children[0].children;
  h.pixels = blob => h.images.get(blob);
  return h;
}

test('real vendored Konva exports a detached document group at explicit pixelRatio=1 without a browser', () => {
  const h = harness(), context = {document: h.document};
  vm.createContext(context, {codeGeneration: {strings: false, wasm: false}}); vm.runInContext(vendorSource, context);
  const Real = context.Konva;
  const vectors = new Real.Group({x: 1, y: 2, scaleX: 0.5, scaleY: 0.5,
    clipX: 0, clipY: 0, clipWidth: 10, clipHeight: 6, listening: false});
  vectors.add(new Real.Arrow({points: [1, 1, 8, 4], stroke: '#ff0000', fill: '#ff0000', strokeWidth: 1, pointerWidth: 2, pointerLength: 2}),
    new Real.Rect({x: 1, y: 1, width: 5, height: 3, stroke: '#00ff00', strokeWidth: 1}),
    new Real.Text({x: 1, y: 1, text: 'Guide', fill: '#0000ff', fontSize: 12, fontFamily: 'Arial'}));
  const output = vectors.toCanvas({x: 0, y: 0, width: 8, height: 8, pixelRatio: 1});
  assert.equal(output.width, 8); assert.equal(output.height, 8); assert.ok(output.data.some((value, i) => i % 4 === 3 && value));
  assert.equal(vectors.getChildren().length, 3); vectors.destroy(); h.editor.destroy();
});

test('editor exposes agreed methods, creates four separate layers and cleans up idempotently', async () => {
  const h = harness(), e = h.editor;
  for (const key of ['load', 'state', 'setTool', 'setBrushSize', 'setColor', 'setText', 'setStrokeWidth', 'undo', 'redo', 'clearMask',
    'clearAnnotations', 'fit', 'zoomBy', 'destroy', 'snapshot', 'compose', 'exportPNG', 'setFeather', 'clear',
    'setMaskVisible', 'setSelectionVisible', 'setAnnotationsVisible']) assert.equal(typeof e[key], 'function');
  assert.equal(h.stage.children.length, 4); assert.equal(e.state().documentId, null);
  await assert.rejects(e.snapshot(), {code: 'NO_DOCUMENT'});
  await e.load(h.image(8, 6)); assert.equal(e.state().revision, 0);
  assert.equal(e.state().width, 8); assert.equal(e.state().height, 6); assert.equal(h.closed, 1);
  e.destroy(); e.destroy(); assert.ok(h.stage.destroyed); assert.ok(h.observers[0].disconnected);
  assert.equal([...h.events.values()].flat().length, 0); assert.equal(h.host.style.touchAction, 'auto'); assert.equal(h.host.getAttribute('tabindex'), null);
  await assert.rejects(e.load(h.image(1, 1)), {code: 'DESTROYED'});
});
test('all mask tools, erasure, visibility, undo/redo and clears keep the base PNG unchanged', async () => {
  const h = harness(), base = h.image(8, 6); await h.editor.load(base); h.editor.setBrushSize(2);
  h.drag('brush', [[2, 2], [4, 2]]); assert.ok(h.editor.state().hasMask);
  h.drag('eraser', [[2, 2], [4, 2]]); assert.equal(h.editor.state().hasMask, false);
  h.editor.undo(); assert.ok(h.editor.state().hasMask); h.editor.redo(); assert.equal(h.editor.state().hasMask, false);
  h.drag('rectangle', [[6, 5], [2, 1]]); assert.ok(h.editor.state().hasMask);
  h.editor.setMaskVisible(false); const hidden = await h.editor.snapshot({local: true}); assert.ok(hidden.mask);
  const mask = h.pixels(hidden.mask); assert.equal(mask.width, 8); assert.equal(mask.height, 6);
  assert.equal(mask.data[(2 * 8 + 3) * 4 + 3], 0); assert.equal(mask.data[3], 255);
  h.editor.clearMask(); assert.equal(h.editor.state().hasMask, false); h.editor.undo(); assert.equal(h.editor.state().hasMask, true);
  h.editor.clearMask(); h.drag('lasso', [[1, 1], [6, 1], [6, 5], [1, 5]]); assert.ok(h.editor.state().hasMask);
  const exported = await h.editor.exportPNG(); assert.deepEqual(h.pixels(exported).data, h.pixels(base).data);
});
test('all vector tools stay selectable/editable objects; a guide is base plus vectors only', async () => {
  const h = harness(), base = h.image(12, 10, new Uint8ClampedArray(480)); await h.editor.load(base); h.editor.setStrokeWidth(1);
  for (const tool of ['pen', 'arrow', 'box', 'ellipse']) h.drag(tool, [[2, 2], [7, 6]]);
  h.editor.setTool('text'); h.editor.setText('<script>literal text</script>'); h.fire('pointerdown', h.point(2, 2)); h.fire('pointerup', h.point(2, 2));
  assert.equal(h.editor.state().annotationCount, 5); assert.equal(h.editor.state().hasMask, false);
  assert.deepEqual(h.nodes().map(n => n.kind), ['Line', 'Arrow', 'Rect', 'Ellipse', 'Text']);
  h.editor.setTool('select'); h.fire('pointerdown', h.point(3, 3)); h.fire('pointerup', h.point(3, 3)); assert.ok(h.editor.state().selectedId);
  const submitted = await h.editor.snapshot({local: false}); assert.equal(submitted.mask, null); assert.ok(submitted.guide);
  assert.deepEqual(h.pixels(submitted.image).data, h.pixels(base).data);
  assert.notDeepEqual(h.pixels(submitted.guide).data, h.pixels(base).data); assert.equal(h.stage.children[3].children[0].children.length, 1);
  h.editor.clearAnnotations(); assert.equal(h.editor.state().hasAnnotations, false); h.editor.undo(); assert.equal(h.editor.state().annotationCount, 5);
});
test('annotation move, style/text edit and delete are single undoable transactions', async () => {
  const h = harness(); await h.editor.load(h.image(10, 8)); h.editor.setStrokeWidth(1);
  h.drag('box', [[1, 1], [3, 3]]); h.editor.setTool('select'); const before = h.editor.state().revision;
  h.fire('pointerdown', h.point(2, 2)); h.fire('pointermove', h.point(4, 3)); h.fire('pointermove', h.point(5, 4)); h.fire('pointerup', h.point(5, 4));
  assert.equal(h.editor.state().revision, before + 1); assert.equal(h.nodes()[0].x(), 4); assert.equal(h.nodes()[0].y(), 3);
  h.editor.setColor('#00ff00'); assert.equal(h.nodes()[0].attrs.stroke, '#00ff00');
  h.editor.undo(); assert.equal(h.nodes()[0].attrs.stroke, '#ef4444'); h.editor.undo(); assert.equal(h.nodes()[0].x(), 1);
  h.editor.redo(); h.fire('pointerdown', h.point(5, 4)); h.fire('pointerup', h.point(5, 4));
  h.fire('keydown', {key: 'Delete'}); assert.equal(h.editor.state().annotationCount, 0); h.editor.undo(); assert.equal(h.editor.state().annotationCount, 1);
  h.editor.setTool('text'); h.editor.setText('old'); h.fire('pointerdown', h.point(1, 1)); h.fire('pointerup', h.point(1, 1));
  h.editor.setTool('select'); h.fire('pointerdown', h.point(1, 1)); h.fire('pointerup', h.point(1, 1)); h.editor.setText('new');
  assert.equal(h.nodes().at(-1).attrs.text, 'new'); h.editor.undo(); assert.equal(h.nodes().at(-1).attrs.text, 'old');
});
test('wheel pointer zoom, hand pan, keyboard focus and arrow nudges do not alter document pixels', async () => {
  const h = harness(); await h.editor.load(h.image(8, 6)); const initial = h.editor.state().revision;
  h.editor.setTool('pan'); const anchor = h.point(3, 3), old = h.stage.children[0].children[0].getAbsoluteTransform().copy().invert().point(H.clientToStage(anchor, h.host.rect, {width: 200, height: 160}));
  assert.equal(h.fire('wheel', {...anchor, deltaY: -100, deltaMode: 0}).prevented, true);
  const now = h.stage.children[0].children[0].getAbsoluteTransform().copy().invert().point(H.clientToStage(anchor, h.host.rect, {width: 200, height: 160}));
  assert.ok(Math.abs(now.x - old.x) < 1e-10); assert.equal(h.editor.state().revision, initial);
  h.drag('pan', [[1, 1], [2, 2]]); assert.equal(h.editor.state().revision, initial);
  h.editor.setStrokeWidth(1); h.drag('box', [[1, 1], [3, 3]]); h.editor.setTool('select');
  h.fire('pointerdown', h.point(2, 2)); h.fire('pointerup', h.point(2, 2)); h.fire('keydown', {key: 'ArrowRight', shiftKey: true}); assert.equal(h.nodes()[0].x(), 11);
  h.fire('keydown', {key: 'z', ctrlKey: true}); assert.ok(Math.abs(h.nodes()[0].x() - 1) < 1e-9);
  h.document.activeElement = {}; const r = h.editor.state().revision; h.fire('keydown', {key: 'z', ctrlKey: true}); assert.equal(h.editor.state().revision, r);
  h.host.focus(); h.fire('keydown', {key: 'z', ctrlKey: true, target: {tagName: 'INPUT'}}); assert.equal(h.editor.state().revision, r);
});
test('cancelled strokes/moves and pinch interruptions never enter history or leave draft pixels', async () => {
  const h = harness(); await h.editor.load(h.image(8, 6)); h.editor.setBrushSize(2); h.editor.setTool('brush');
  h.fire('pointerdown', h.point(2, 2)); h.fire('pointermove', h.point(4, 2)); h.fire('pointercancel');
  assert.equal(h.editor.state().revision, 0); assert.equal(h.editor.state().hasMask, false);
  h.fire('pointerdown', h.point(2, 2)); h.fire('pointerdown', {...h.point(6, 2), pointerId: 2});
  h.fire('pointermove', {...h.point(7, 2), pointerId: 2}); h.fire('pointerup', {...h.point(7, 2), pointerId: 2}); h.fire('pointerup', h.point(2, 2));
  assert.equal(h.editor.state().revision, 0); assert.equal(h.editor.state().hasMask, false); assert.equal(h.captures.size, 0);
  h.drag('box', [[1, 1], [3, 3]]); h.editor.setTool('select'); h.fire('pointerdown', h.point(2, 2)); h.fire('pointermove', h.point(4, 3)); h.fire('lostpointercapture');
  assert.equal(h.nodes()[0].x(), 1); assert.equal(h.editor.state().revision, 1);
});
test('snapshot freezes source, region, guide, metadata and letterbox before asynchronous export', async () => {
  const h = harness(), original = h.image(8, 4); await h.editor.load(original);
  h.drag('rectangle', [[1, 1], [5, 3]]); h.editor.setStrokeWidth(1); h.drag('arrow', [[1, 1], [3, 2]]);
  const identity = h.editor.state().documentId, revision = h.editor.state().revision;
  h.holdPNG = true; const promise = h.editor.snapshot({size: '8x8', local: true});
  h.editor.clearMask(); h.editor.clearAnnotations(); await h.editor.load(h.image(2, 2, rgba(...Array(4).fill([255, 0, 0, 255]))));
  for (const release of h.pendingPNG) release(); h.holdPNG = false;
  const frozen = await promise;
  assert.ok(Object.isFrozen(frozen)); assert.ok(Object.isFrozen(frozen.working));
  assert.equal(frozen.documentId, identity); assert.equal(frozen.revision, revision); assert.notEqual(h.editor.state().documentId, identity);
  assert.deepEqual(plain(frozen.working), {width: 8, height: 8, scale: 1, offsetX: 0, offsetY: 2});
  assert.ok(frozen.guide); assert.equal(h.pixels(frozen.image).width, 8); assert.equal(h.pixels(frozen.mask).data[(3 * 8 + 2) * 4 + 3], 0);
  assert.throws(() => {frozen.revision = 9;}, TypeError); assert.throws(() => {frozen.working.offsetY = 0;}, TypeError);
  const result = h.image(8, 8, rgba(...Array(64).fill([0, 80, 0, 255]))), currentID = h.editor.state().documentId;
  const composed = await h.editor.compose(result, frozen); assert.ok(composed.blob); assert.equal(h.pixels(composed.blob).width, 8); assert.equal(h.pixels(composed.blob).height, 4);
  const pixels = h.pixels(composed.blob).data; assert.deepEqual([...pixels.subarray((1 * 8 + 2) * 4, (1 * 8 + 2) * 4 + 4)], [0, 80, 0, 255]);
  assert.deepEqual([...pixels.subarray(0, 4)], [255, 255, 255, 255]); assert.equal(h.editor.state().documentId, currentID);
});
test('empty local mask is rejected; whole edit omits mask; mismatched/foreign snapshots never force-composite', async () => {
  const h = harness(); await h.editor.load(h.image(8, 6)); await assert.rejects(h.editor.snapshot({local: true}), {code: 'EMPTY_MASK'});
  h.drag('rectangle', [[1, 1], [4, 4]]);
  const local = await h.editor.snapshot({local: true}), whole = await h.editor.snapshot({local: false}); assert.equal(whole.mask, null);
  const result = h.image(8, 6); assert.equal((await h.editor.compose(result, whole)).reason, 'no_local_mask');
  assert.equal((await h.editor.compose(h.image(4, 3), local)).reason, 'working_size_mismatch');
  assert.equal((await h.editor.compose(result, {...local})).reason, 'unknown_snapshot');
  const other = harness(); assert.equal((await other.editor.compose(result, local)).reason, 'unknown_snapshot');
  assert.equal((await h.editor.compose(new Blob(['<html>']), local)).reason, 'IMAGE_TYPE');
});
test('feather is undoable; original exterior remains exact after local composition', async () => {
  const h = harness(), original = h.image(8, 6, rgba(...Array(48).fill([20, 50, 100, 128]))); await h.editor.load(original);
  h.drag('rectangle', [[2, 1], [6, 5]]); h.editor.setFeather(2); assert.equal(h.editor.state().feather, 2);
  const s = await h.editor.snapshot({local: true}); h.editor.setFeather(0); h.editor.undo(); assert.equal(h.editor.state().feather, 2);
  const result = h.image(8, 6, rgba(...Array(48).fill([255, 0, 0, 0]))), composed = await h.editor.compose(result, s);
  const actual = h.pixels(composed.blob).data; assert.deepEqual([...actual.subarray(0, 4)], [20, 50, 100, 128]);
  assert.equal(actual[(2 * 8 + 3) * 4], 20); assert.ok(actual[(2 * 8 + 3) * 4 + 3] < 128);
});
test('latest load wins even for equal image bytes; losing/failed loads never replace the current document', async () => {
  const h = harness(), a = h.image(2, 2), b = h.image(3, 2), gate = deferred(); h.decoder = blob => blob === a ? gate.promise : Promise.resolve();
  const pending = h.editor.load(a); await tick(); await h.editor.load(b); const identity = h.editor.state().documentId;
  gate.resolve(); await assert.rejects(pending, {code: 'LOAD_SUPERSEDED'}); assert.equal(h.editor.state().width, 3); assert.equal(h.editor.state().documentId, identity);
  await assert.rejects(h.editor.load(new Blob(['bad'])), {code: 'IMAGE_TYPE'}); assert.equal(h.editor.state().documentId, identity);
  await h.editor.load(b); assert.notEqual(h.editor.state().documentId, identity); assert.equal(h.editor.state().revision, 0);
});
test('bitmap decoder normalizes EXIF once; fallback Blob URLs are revoked on success and failure', async () => {
  const h = harness(); await h.editor.load(h.image(2, 3)); assert.deepEqual(plain(h.bitmapCalls[0].options), {imageOrientation: 'from-image'});
  const fallback = harness({fallback: true}); await fallback.editor.load(fallback.image(2, 3)); assert.equal(fallback.urls.size, 0); assert.equal(fallback.revoked.length, 1);
  fallback.decoder = () => Promise.reject(new Error('fixture decode failure'));
  await assert.rejects(fallback.editor.load(fallback.image(2, 3)), {code: 'IMAGE_DECODE'}); assert.equal(fallback.urls.size, 0); assert.equal(fallback.revoked.length, 2);
});
test('invalid configuration and oversized/animated source headers fail safely before decoder invocation', async () => {
  const h = harness(); for (const tool of ['paint', null, 'crop']) assert.throws(() => h.editor.setTool(tool), {code: 'INVALID_TOOL'});
  assert.throws(() => h.editor.setColor('red')); assert.throws(() => h.editor.setColor('#ff0000; background:url(x)'));
  assert.throws(() => h.editor.setBrushSize(NaN)); assert.throws(() => h.editor.setStrokeWidth(Infinity)); assert.throws(() => h.editor.setText('x'.repeat(2001)));
  await assert.rejects(h.editor.load(new Blob([new Uint8Array(H.LIMITS.sourceBytes + 1)])), {code: 'IMAGE_BYTES'});
  const header = pngBytes(1, 1); header.writeUInt32BE(10000, 16); header.writeUInt32BE(10000, 20);
  await assert.rejects(h.editor.load(new Blob([header])), {code: 'IMAGE_DIMENSIONS'});
  await assert.rejects(h.editor.load(new Blob([pngBytes(1, 1, undefined, [pngChunk('acTL', Buffer.alloc(8))])])), {code: 'IMAGE_ANIMATION'});
  assert.equal(h.bitmapCalls.length, 0);
});

const pixelAt = (frame, x, y) => [...frame.data.subarray((y * frame.width + x) * 4, (y * frame.width + x) * 4 + 4)];
const assertReleased = canvases => { for (const c of canvases) { assert.equal(c.width, 0); assert.equal(c.height, 0); } };

test('cyan guide footprint uses mask coordinates including fractional offsets, holes and partial coverage', () => {
  for (const [w, h, ww, wh] of [[7, 5, 7, 9], [3, 7, 4, 4], [11, 3, 7, 6], [5, 5, 10, 10]]) {
    const coverage = Uint8Array.from({length: w * h}, (_, i) => i % 4 ? (i % 255) + 1 : 0);
    const working = H.letterbox(w, h, ww, wh), mask = H.maskRGBA(coverage, w, h, working);
    const overlay = H.selectionGuideRGBA(coverage, w, h, working);
    for (let i = 0; i < ww * wh; i++) {
      if (mask[i * 4 + 3] === 255) assert.deepEqual([...overlay.subarray(i * 4, i * 4 + 4)], [0, 0, 0, 0]);
      else { assert.deepEqual([...overlay.subarray(i * 4, i * 4 + 3)], [0, 216, 255]); assert.ok(overlay[i * 4 + 3] > 0); }
    }
  }
  const field = new Uint8Array(49); for (let y = 1; y < 6; y++) for (let x = 1; x < 6; x++) field[y * 7 + x] = 255;
  const frame = {width: 7, data: H.selectionGuideRGBA(field, 7, 7)};
  assert.equal(pixelAt(frame, 1, 1)[3], 255); assert.equal(pixelAt(frame, 3, 3)[3], 80);
  assert.throws(() => H.selectionGuideRGBA(bytes(), 7, 7), {code: 'INVALID_PIXELS'});
});

test('reference-only selection with empty annotations exports source plus cyan guide, never a renamed alpha mask', async () => {
  const h = harness(), base = h.image(10, 6); await h.editor.load(base);
  h.drag('rectangle', [[2, 1], [8, 5]]);
  const native = await h.editor.snapshot({size: '10x10', local: true});
  h.editor.zoomBy(1.7); h.drag('pan', [[1, 1], [2, 2]]);
  const snapshot = await h.editor.snapshot({size: '10x10', local: true, guideSelection: true});
  assert.equal(snapshot.guideSelection, true); assert.equal(snapshot.hasAnnotations, false); assert.equal(snapshot.hasMask, true);
  assert.equal(native.guide, null); assert.equal(native.guideSelection, false);
  assert.deepEqual(h.pixels(snapshot.image), h.pixels(native.image)); assert.deepEqual(h.pixels(snapshot.mask), h.pixels(native.mask));
  const main = h.pixels(snapshot.image), guide = h.pixels(snapshot.guide), mask = h.pixels(snapshot.mask);
  assert.deepEqual(pixelAt(main, 2, 3), [255, 255, 255, 255]); assert.deepEqual(pixelAt(guide, 2, 3), [0, 216, 255, 255]);
  assert.ok(pixelAt(guide, 4, 4)[0] > 0 && pixelAt(guide, 4, 4)[0] < 255, 'interior is translucent cyan over the source');
  for (let y = 0; y < 10; y++) for (let x = 0; x < 10; x++) {
    if (pixelAt(mask, x, y)[3] === 255) assert.deepEqual(pixelAt(guide, x, y), pixelAt(main, x, y));
  }
  assert.notDeepEqual(guide.data, mask.data); assert.deepEqual(h.pixels(await h.editor.exportPNG()), h.pixels(base));
  assert.deepEqual(Object.keys(snapshot).filter(k => /rgba|coverage|weights|annotations$|masks$/.test(k)), []);
  h.editor.destroy(); assertReleased(h.allocations);
});

test('guide annotations render above cyan indication; both visibility controls are observation-only', async () => {
  const h = harness(); await h.editor.load(h.image(10, 6)); h.drag('rectangle', [[2, 1], [8, 5]]);
  h.editor.setStrokeWidth(1); h.editor.setColor('#ff0000'); h.drag('pen', [[2, 1.5], [7, 1.5]]);
  const before = h.editor.state(), options = {local: true, guideSelection: true}, shown = await h.editor.snapshot(options);
  assert.deepEqual(pixelAt(h.pixels(shown.guide), 3, 1), [255, 0, 0, 255]);
  h.editor.setTool('select'); h.fire('pointerdown', h.point(3, 1)); h.fire('pointerup', h.point(3, 1));
  const selected = h.editor.state().selectedId; assert.ok(selected);
  h.editor.setSelectionVisible(false); h.editor.setAnnotationsVisible(false);
  assert.equal(h.editor.state().selectionVisible, false); assert.equal(h.editor.state().annotationsVisible, false);
  assert.equal(h.stage.children[1].visible(), false); assert.equal(h.stage.children[2].visible(), false);
  assert.equal(h.stage.children[3].children[0].children[0].visible(), false);
  const hidden = await h.editor.snapshot(options);
  for (const key of ['image', 'mask', 'guide']) assert.deepEqual(h.pixels(hidden[key]), h.pixels(shown[key]));
  assert.equal(hidden.hasAnnotations, true); assert.equal(hidden.guideSelection, true);
  assert.equal(h.editor.state().revision, before.revision); assert.equal(h.editor.state().selectedId, selected);
  assert.equal(h.editor.state().canUndo, before.canUndo); assert.equal(h.editor.state().canRedo, before.canRedo);
  h.fire('keydown', {key: 'Delete'}); h.fire('keydown', {key: 'ArrowRight'}); h.editor.setColor('#00ff00');
  assert.equal(h.editor.state().revision, before.revision, 'hidden annotations must not be edited accidentally');
  h.editor.setMaskVisible(true); h.editor.setAnnotationsVisible(true);
  assert.equal(h.editor.state().selectionVisible, true); assert.equal(h.editor.state().annotationsVisible, true);
  assert.equal(h.editor.state().revision, before.revision); assert.equal(h.stage.children[3].children[0].children[0].visible(), true);
  h.editor.undo(); assert.equal(h.editor.state().annotationCount, 0); h.editor.redo(); assert.equal(h.editor.state().annotationCount, 1);
});

test('guideSelection is boolean, local-only, independent of actual annotations and visibility', async () => {
  const h = harness(); await h.editor.load(h.image(8, 6));
  for (const guideSelection of [null, 1, 'true', {}]) await assert.rejects(h.editor.snapshot({guideSelection}), {code: 'INVALID_ARGUMENT'});
  await assert.rejects(h.editor.snapshot({local: true, guideSelection: true}), {code: 'EMPTY_MASK'});
  let whole = await h.editor.snapshot({guideSelection: true});
  assert.equal(whole.guide, null); assert.equal(whole.mask, null); assert.equal(whole.guideSelection, false); assert.equal(whole.hasAnnotations, false);
  h.drag('rectangle', [[1, 1], [5, 4]]); h.editor.setStrokeWidth(1); h.drag('arrow', [[1, 1], [3, 2]]);
  whole = await h.editor.snapshot({guideSelection: true});
  const native = await h.editor.snapshot({local: true});
  assert.equal(whole.guideSelection, false); assert.equal(whole.hasAnnotations, true); assert.equal(native.guideSelection, false);
  assert.deepEqual(h.pixels(whole.guide), h.pixels(native.guide));
});

test('reference guide keeps private inward feather and every exterior decoded RGBA byte after source replacement', async () => {
  const h = harness(), data = Uint8ClampedArray.from({length: 8 * 6 * 4}, (_, i) => (i * 71) % 256);
  await h.editor.load(h.image(8, 6, data)); const original = h.pixels(await h.editor.exportPNG());
  const oldCanvases = [...h.allocations]; h.drag('rectangle', [[2, 1], [6, 5]]); h.editor.setFeather(2);
  const s = await h.editor.snapshot({local: true, guideSelection: true, size: '8x8'});
  const native = await h.editor.snapshot({local: true, size: '8x8'}); assert.deepEqual(h.pixels(s.mask), h.pixels(native.mask));
  h.editor.clearMask(); h.editor.undo(); await h.editor.load(h.image(2, 2)); assertReleased(oldCanvases);
  const result = h.image(8, 8, rgba(...Array(64).fill([0, 100, 200, 96]))), id = h.editor.state().documentId;
  const actual = h.pixels((await h.editor.compose(result, s)).blob);
  const coverage = new Uint8Array(48); for (let y = 1; y < 5; y++) for (let x = 2; x < 6; x++) coverage[y * 8 + x] = 255;
  const expected = H.composeRGBA(original.data, h.pixels(result).data, H.inwardFeather(coverage, 8, 6, 2), 8, 6, H.letterbox(8, 6, 8, 8));
  assert.deepEqual(actual.data, expected);
  for (let i = 0; i < 48; i++) if (!coverage[i]) assert.deepEqual([...actual.data.subarray(i * 4, i * 4 + 4)], [...original.data.subarray(i * 4, i * 4 + 4)]);
  assert.equal(h.editor.state().documentId, id); assert.equal(h.editor.state().revision, 0);
  assert.equal(h.editor.undo(), false); h.editor.destroy(); assertReleased(h.allocations);
});

test('clear releases canvases and private snapshots, resets history and leaves a reusable empty editor', async () => {
  const h = harness(); await h.editor.load(h.image(8, 6)); h.drag('rectangle', [[1, 1], [5, 5]]); h.drag('pen', [[1, 1], [2, 2]]);
  const submitted = await h.editor.snapshot({local: true, guideSelection: true}), id = submitted.documentId;
  h.editor.setSelectionVisible(false); h.editor.setAnnotationsVisible(false); h.editor.setTool('brush'); h.fire('pointerdown', h.point(3, 3));
  h.editor.clear(); h.editor.clear(); assertReleased(h.allocations);
  assert.equal(h.captures.size, 0); assert.equal(h.editor.state().documentId, null); assert.equal(h.editor.state().revision, 0);
  assert.equal(h.editor.state().width, 0); assert.equal(h.editor.state().height, 0); assert.equal(h.editor.state().hasMask, false);
  assert.equal(h.editor.state().annotationCount, 0); assert.equal(h.editor.state().feather, 0);
  assert.equal(h.editor.state().selectionVisible, false); assert.equal(h.editor.state().annotationsVisible, false);
  assert.equal(h.editor.undo(), false); assert.equal(h.editor.redo(), false);
  assert.equal(h.stage.children[0].children[0].children[0].attrs.image, null);
  assert.equal(h.stage.children[1].children[0].children[0].attrs.image, null);
  await assert.rejects(h.editor.snapshot(), {code: 'NO_DOCUMENT'}); await assert.rejects(h.editor.exportPNG(), {code: 'NO_DOCUMENT'});
  assert.throws(() => h.editor.setFeather(1), {code: 'NO_DOCUMENT'});
  assert.equal((await h.editor.compose(h.image(8, 6), submitted)).reason, 'unknown_snapshot');
  await h.editor.load(h.image(3, 2)); assert.notEqual(h.editor.state().documentId, id); assert.equal(h.editor.state().width, 3);
  assert.equal((await h.editor.compose(h.image(8, 6), submitted)).reason, 'unknown_snapshot');
  assert.equal(h.stage.destroyed, undefined); h.editor.destroy(); assertReleased(h.allocations);
  assert.throws(() => h.editor.clear(), {code: 'DESTROYED'});
});

test('clear or destroy during snapshot export cannot resurrect private resources', async () => {
  for (const action of ['clear', 'destroy']) {
    const h = harness(); await h.editor.load(h.image(8, 6)); h.drag('rectangle', [[1, 1], [5, 5]]); h.drag('pen', [[1, 1], [2, 2]]);
    h.holdPNG = true; const pending = h.editor.snapshot({local: true, guideSelection: true});
    const rejected = assert.rejects(pending, {code: 'SNAPSHOT_SUPERSEDED'});
    h.editor[action](); assertReleased(h.allocations);
    if (action === 'clear') await h.editor.load(h.image(2, 2));
    for (const release of h.pendingPNG) release(); h.holdPNG = false; await rejected;
    if (action === 'clear') { assert.equal(h.editor.state().width, 2); h.editor.destroy(); }
    assertReleased(h.allocations);
  }
});

test('clear supersedes pending decodes and composition without reviving a source or old candidate', async () => {
  const h = harness(), first = h.image(4, 3), gate = deferred(); h.decoder = () => gate.promise;
  const loading = h.editor.load(first); await tick(); h.editor.clear(); gate.resolve();
  await assert.rejects(loading, {code: 'LOAD_SUPERSEDED'}); assert.equal(h.editor.state().documentId, null); assertReleased(h.allocations);
  h.decoder = null; await h.editor.load(first); h.drag('rectangle', [[1, 1], [3, 2]]);
  const submitted = await h.editor.snapshot({local: true}), result = h.image(4, 3), composing = deferred();
  h.decoder = () => composing.promise; const pending = h.editor.compose(result, submitted); await tick();
  h.editor.clear(); composing.resolve(); assert.equal((await pending).reason, 'unknown_snapshot'); assertReleased(h.allocations);
  h.decoder = null; await h.editor.load(first); h.drag('rectangle', [[1, 1], [3, 2]]);
  const next = await h.editor.snapshot({local: true}); h.holdPNG = true;
  const exporting = h.editor.compose(result, next); await tick(); h.editor.clear(); assertReleased(h.allocations);
  for (const release of h.pendingPNG) release(); assert.equal((await exporting).reason, 'unknown_snapshot'); assertReleased(h.allocations);
});

test('reference-only guide freezes before encode and survives view/history/source changes, not clear', async () => {
  const h = harness(); await h.editor.load(h.image(8, 6)); h.drag('rectangle', [[1, 1], [6, 5]]);
  const expected = await h.editor.snapshot({local: true, guideSelection: true, size: '8x8'}), sourceCanvases = h.allocations.slice(0, 2);
  h.holdPNG = true; const pending = h.editor.snapshot({local: true, guideSelection: true, size: '8x8'});
  h.editor.setSelectionVisible(false); h.editor.setAnnotationsVisible(false); h.editor.clearMask(); h.drag('pen', [[1, 1], [2, 2]]);
  await h.editor.load(h.image(2, 2)); assertReleased(sourceCanvases);
  h.holdPNG = false; for (const release of h.pendingPNG) release(); const actual = await pending;
  for (const field of ['documentId', 'revision', 'guideSelection', 'hasAnnotations']) assert.equal(actual[field], expected[field]);
  for (const field of ['image', 'mask', 'guide']) assert.deepEqual(h.pixels(actual[field]), h.pixels(expected[field]));
  assert.equal((await h.editor.compose(h.image(4, 4), actual)).reason, 'working_size_mismatch');
  assert.ok((await h.editor.compose(h.image(8, 8), actual)).blob);
  h.editor.clear(); assertReleased(h.allocations);
});

test('failed source decode or mask allocation releases only new buffers, preserving source/history/snapshots', async () => {
  for (const failure of ['readback', 'mask']) {
    const h = harness(); await h.editor.load(h.image(8, 6)); h.drag('rectangle', [[1, 1], [6, 5]]);
    const submitted = await h.editor.snapshot({local: true}), before = h.editor.state(), count = h.allocations.length;
    const create = h.document.createElement; let allocations = 0;
    h.document.createElement = tag => {
      const c = create(tag); allocations++;
      if (failure === 'readback') c.context.getImageData = () => { throw new Error('readback failed'); };
      else if (allocations === 2) c.getContext = () => null;
      return c;
    };
    await assert.rejects(h.editor.load(h.image(2, 2)), {code: failure === 'readback' ? 'IMAGE_DECODE' : 'CANVAS_UNAVAILABLE'});
    h.document.createElement = create;
    assertReleased(h.allocations.slice(count)); assert.deepEqual(h.editor.state(), before); assert.equal(h.closed, 2);
    assert.ok((await h.editor.compose(h.image(8, 6), submitted)).blob);
    h.editor.undo(); assert.equal(h.editor.state().hasMask, false); h.editor.redo(); assert.equal(h.editor.state().hasMask, true);
    h.editor.destroy(); assertReleased(h.allocations);
  }
});

test('failed PNG export and vanished downscaled regions release temporary canvases without touching the document', async () => {
  const h = harness(); await h.editor.load(h.image(8, 6)); h.drag('rectangle', [[0, 0], [1, 1]]);
  const count = h.allocations.length, before = h.editor.state();
  await assert.rejects(h.editor.snapshot({local: true, guideSelection: true, size: '1x1'}), {code: 'EMPTY_WORKING_MASK'});
  assertReleased(h.allocations.slice(count)); assert.deepEqual(h.editor.state(), before);
  h.drag('pen', [[1, 1], [2, 2]]); const create = h.document.createElement;
  h.document.createElement = tag => { const c = create(tag); c.toBlob = callback => queueMicrotask(() => callback(null)); return c; };
  await assert.rejects(h.editor.snapshot({local: true, guideSelection: true}), {code: 'EXPORT_FAILED'});
  h.document.createElement = create; assertReleased(h.allocations.slice(count));
  assert.equal(h.editor.state().documentId, before.documentId); assert.equal(h.editor.state().hasMask, true);
  assert.ok((await h.editor.snapshot({local: true, guideSelection: true})).guide);
  h.editor.destroy(); assertReleased(h.allocations);
});

test('view-only hiding also hides temporary drafts without adding or removing committed commands', async () => {
  const h = harness(); await h.editor.load(h.image(8, 6));
  for (const tool of ['rectangle', 'lasso', 'pen', 'arrow', 'box', 'ellipse']) {
    h.editor.setSelectionVisible(false); h.editor.setAnnotationsVisible(false); h.editor.setTool(tool);
    h.fire('pointerdown', h.point(1, 1)); h.fire('pointermove', h.point(4, 4));
    assert.equal(h.stage.children[3].children[0].children.at(-1).visible(), false);
    h.fire('pointercancel'); assert.equal(h.editor.state().revision, 0); assert.equal(h.editor.state().canUndo, false);
    h.editor.setSelectionVisible(true); h.editor.setAnnotationsVisible(true);
  }
  h.editor.destroy(); assertReleased(h.allocations);
});

test('new-document clear preserves submitted local snapshots for late results; default clear still invalidates', async () => {
  const h = harness(); await h.editor.load(h.image(8, 6, rgba(...Array(48).fill([20, 50, 100, 128]))));
  h.drag('rectangle', [[2, 1], [6, 5]]); h.editor.setFeather(2);
  const submitted = await h.editor.snapshot({local: true, guideSelection: true, size: '8x8'});
  const result = h.image(8, 8, rgba(...Array(64).fill([0, 100, 200, 96])));
  const expected = h.pixels((await h.editor.compose(result, submitted)).blob), owned = [...h.allocations];
  const empty = h.editor.clear({preserveSnapshots: true}); assertReleased(owned);
  assert.equal(empty.documentId, null); assert.equal(empty.width, 0); assert.equal(empty.revision, 0);
  assert.equal(empty.hasMask, false); assert.equal(empty.hasAnnotations, false);
  assert.equal(h.editor.undo(), false); assert.equal(h.editor.redo(), false);
  const late = await h.editor.compose(result, submitted); assert.deepEqual(h.pixels(late.blob), expected);
  assert.deepEqual(pixelAt(h.pixels(late.blob), 0, 0), [20, 50, 100, 128]);
  assert.equal(h.editor.state().documentId, null, 'late candidate must not apply itself to the empty document');
  await h.editor.load(h.image(2, 2)); const id = h.editor.state().documentId;
  assert.deepEqual(h.pixels((await h.editor.compose(result, submitted)).blob), expected);
  assert.equal(h.editor.state().documentId, id); assert.equal(h.editor.state().revision, 0);
  h.editor.clear(); assert.equal((await h.editor.compose(result, submitted)).reason, 'unknown_snapshot');
  h.editor.clear({preserveSnapshots: true});
  assert.equal((await h.editor.compose(result, submitted)).reason, 'unknown_snapshot', 'preserving must not revive invalidated snapshots');
  h.editor.destroy(); assertReleased(h.allocations);
});

test('preserving clear keeps pending snapshot/composition buffers alive, but destroy always invalidates them', async () => {
  const h = harness(); await h.editor.load(h.image(8, 6)); h.drag('rectangle', [[1, 1], [5, 5]]);
  const before = h.editor.state();
  for (const preserveSnapshots of [null, 1, 'true']) {
    assert.throws(() => h.editor.clear({preserveSnapshots}), {code: 'INVALID_ARGUMENT'});
    assert.deepEqual(h.editor.state(), before);
  }
  const documentCanvases = h.allocations.slice(0, 2);
  h.holdPNG = true; const preparing = h.editor.snapshot({local: true, guideSelection: true});
  h.editor.clear({preserveSnapshots: true}); assertReleased(documentCanvases);
  for (const release of h.pendingPNG.splice(0)) release(); h.holdPNG = false;
  const submitted = await preparing, result = h.image(8, 6);
  assert.equal(submitted.documentId, before.documentId); assertReleased(h.allocations);
  for (const phase of ['decode', 'encode']) {
    const gate = deferred(); h.decoder = phase === 'decode' ? () => gate.promise : null;
    h.holdPNG = phase === 'encode'; const pending = h.editor.compose(result, submitted); await tick();
    h.editor.clear({preserveSnapshots: true});
    gate.resolve(); for (const release of h.pendingPNG.splice(0)) release(); h.holdPNG = false;
    assert.ok((await pending).blob); assert.equal(h.editor.state().documentId, null); assertReleased(h.allocations);
  }
  h.decoder = null; h.holdPNG = true; const pending = h.editor.compose(result, submitted); await tick();
  h.editor.destroy(); assertReleased(h.allocations);
  for (const release of h.pendingPNG.splice(0)) release();
  assert.equal((await pending).reason, 'unknown_snapshot'); assertReleased(h.allocations);
});
