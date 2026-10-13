/* GPT Image Playground: Copyright (c) 2026 CookSleep, MIT.
 * InvokeAI compositor: Copyright 2022-2026 InvokeAI Contributors, Apache-2.0.
 * Full licenses and notice: GPT-IMAGE-PLAYGROUND-LICENSE, INVOKEAI-LICENSE, INVOKEAI-NOTICE.
 * Built from pinned source. Edit frontend/studio, not this file.
 * Revisions and modifications: docs/image-studio-sources.md. */
"use strict";
var CockpitStudioUpstream = (() => {
  var __defProp = Object.defineProperty;
  var __getOwnPropDesc = Object.getOwnPropertyDescriptor;
  var __getOwnPropNames = Object.getOwnPropertyNames;
  var __hasOwnProp = Object.prototype.hasOwnProperty;
  var __export = (target, all) => {
    for (var name in all)
      __defProp(target, name, { get: all[name], enumerable: true });
  };
  var __copyProps = (to, from, except, desc) => {
    if (from && typeof from === "object" || typeof from === "function") {
      for (let key of __getOwnPropNames(from))
        if (!__hasOwnProp.call(to, key) && key !== except)
          __defProp(to, key, { get: () => from[key], enumerable: !(desc = __getOwnPropDesc(from, key)) || desc.enumerable });
    }
    return to;
  };
  var __toCommonJS = (mod) => __copyProps(__defProp({}, "__esModule", { value: true }), mod);

  // src/index.ts
  var index_exports = {};
  __export(index_exports, {
    compositeCanvas: () => compositeCanvas,
    drawElements: () => drawElements,
    eraseElements: () => eraseElements,
    sampleEraseStroke: () => sampleEraseStroke,
    sketch: () => sketch_exports,
    viewport: () => viewportTransform_exports
  });

  // vendor/gpt-image-playground/lib/sketch.ts
  var sketch_exports = {};
  __export(sketch_exports, {
    SKETCH_FONT_FAMILY: () => SKETCH_FONT_FAMILY,
    SKETCH_HANDLES: () => SKETCH_HANDLES,
    SKETCH_LINE_HEIGHT: () => SKETCH_LINE_HEIGHT,
    SKETCH_LONG_EDGE: () => SKETCH_LONG_EDGE,
    SKETCH_MAX_STROKE: () => SKETCH_MAX_STROKE,
    SKETCH_MIN_STROKE: () => SKETCH_MIN_STROKE,
    SKETCH_SHAPES: () => SKETCH_SHAPES,
    createSketchId: () => createSketchId,
    drawEraseStroke: () => drawEraseStroke,
    drawSketchElement: () => drawSketchElement,
    drawSketchElementWithErase: () => drawSketchElementWithErase,
    findTopElementAt: () => findTopElementAt,
    getElementBounds: () => getElementBounds,
    getHandlePoint: () => getHandlePoint,
    getShapePolylines: () => getShapePolylines,
    getSketchCanvasSize: () => getSketchCanvasSize,
    getSketchFontSize: () => getSketchFontSize,
    getSketchStrokeWidthFromFontSize: () => getSketchStrokeWidthFromFontSize,
    getTransformBounds: () => getTransformBounds,
    hitTestElement: () => hitTestElement,
    isLineShape: () => isLineShape,
    isSketchElementVisible: () => isSketchElementVisible,
    measureTextElement: () => measureTextElement,
    moveElement: () => moveElement,
    resizeBounds: () => resizeBounds,
    snapToAngle: () => snapToAngle,
    transformElement: () => transformElement
  });
  function createSketchId() {
    return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
  }
  var SKETCH_SHAPES = ["line", "arrow", "rect", "ellipse", "triangle", "diamond", "star", "heart"];
  var SKETCH_HANDLES = ["nw", "n", "ne", "e", "se", "s", "sw", "w"];
  var SKETCH_FONT_FAMILY = 'system-ui, -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif';
  var SKETCH_LINE_HEIGHT = 1.25;
  var SKETCH_MIN_STROKE = 2;
  var SKETCH_MAX_STROKE = 160;
  function getSketchFontSize(strokeWidth) {
    return Math.round(16 + strokeWidth * 4);
  }
  function getSketchStrokeWidthFromFontSize(fontSize) {
    return Math.round((fontSize - 16) / 4);
  }
  function getRawBounds(points) {
    const xs = points.map((p) => p.x);
    const ys = points.map((p) => p.y);
    const x = Math.min(...xs);
    const y = Math.min(...ys);
    return { x, y, width: Math.max(...xs) - x, height: Math.max(...ys) - y };
  }
  function getShapePolylines(el) {
    const { start, end } = el;
    if (el.shape === "line") return [[start, end]];
    if (el.shape === "arrow") {
      const angle = Math.atan2(end.y - start.y, end.x - start.x);
      const length = Math.hypot(end.x - start.x, end.y - start.y);
      const head = Math.min(length * 0.4, Math.max(16, el.width * 3.5));
      const wing = (angle2) => ({ x: end.x - head * Math.cos(angle2), y: end.y - head * Math.sin(angle2) });
      return [[start, end], [wing(angle - Math.PI / 6), end, wing(angle + Math.PI / 6)]];
    }
    const b = getRawBounds([start, end]);
    const cx = b.x + b.width / 2;
    const cy = b.y + b.height / 2;
    const at = (u, v) => ({ x: b.x + b.width * u, y: b.y + b.height * v });
    switch (el.shape) {
      case "rect":
        return [[at(0, 0), at(1, 0), at(1, 1), at(0, 1), at(0, 0)]];
      case "triangle":
        return [[at(0.5, 0), at(1, 1), at(0, 1), at(0.5, 0)]];
      case "diamond":
        return [[at(0.5, 0), at(1, 0.5), at(0.5, 1), at(0, 0.5), at(0.5, 0)]];
      case "ellipse":
        return [Array.from({ length: 73 }, (_, i) => {
          const t = i / 72 * Math.PI * 2;
          return { x: cx + b.width / 2 * Math.cos(t), y: cy + b.height / 2 * Math.sin(t) };
        })];
      case "star":
        return [Array.from({ length: 11 }, (_, i) => {
          const t = -Math.PI / 2 + i * Math.PI / 5;
          const r = i % 2 === 0 ? 1 : 0.382;
          return at((r * Math.cos(t) + 0.951) / 1.902, (r * Math.sin(t) + 1) / 1.809);
        })];
      case "heart":
        return [Array.from({ length: 97 }, (_, i) => {
          const t = i / 96 * Math.PI * 2;
          const hx = 16 * Math.sin(t) ** 3;
          const hy = -(13 * Math.cos(t) - 5 * Math.cos(2 * t) - 2 * Math.cos(3 * t) - Math.cos(4 * t));
          return { x: cx + hx / 32 * b.width, y: b.y + (hy + 12) / 29 * b.height };
        })];
    }
  }
  function measureTextElement(ctx, el) {
    ctx.save();
    ctx.font = `${el.fontSize}px ${SKETCH_FONT_FAMILY}`;
    const lines = el.text.split("\n");
    const width = Math.max(el.fontSize * 0.5, ...lines.map((line) => ctx.measureText(line).width));
    ctx.restore();
    return { width, height: lines.length * el.fontSize * SKETCH_LINE_HEIGHT };
  }
  function getElementBounds(el, ctx) {
    if (el.type === "text") {
      const size = measureTextElement(ctx, el);
      return { x: el.x, y: el.y, ...size };
    }
    const b = el.type === "path" ? getRawBounds(el.points) : getRawBounds(getShapePolylines(el).flat());
    const pad = el.width / 2;
    return { x: b.x - pad, y: b.y - pad, width: b.width + pad * 2, height: b.height + pad * 2 };
  }
  function distanceToSegment(p, a, b) {
    const dx = b.x - a.x;
    const dy = b.y - a.y;
    const lengthSq = dx * dx + dy * dy;
    const t = lengthSq === 0 ? 0 : Math.max(0, Math.min(1, ((p.x - a.x) * dx + (p.y - a.y) * dy) / lengthSq));
    return Math.hypot(p.x - (a.x + t * dx), p.y - (a.y + t * dy));
  }
  function distanceToPolyline(p, points) {
    if (points.length === 1) return Math.hypot(p.x - points[0].x, p.y - points[0].y);
    let min = Infinity;
    for (let i = 1; i < points.length; i++) min = Math.min(min, distanceToSegment(p, points[i - 1], points[i]));
    return min;
  }
  function boundsContain(b, p, pad = 0) {
    return p.x >= b.x - pad && p.x <= b.x + b.width + pad && p.y >= b.y - pad && p.y <= b.y + b.height + pad;
  }
  function hitTestElement(el, point, tolerance, ctx, mode) {
    if (el.type === "text") return boundsContain(getElementBounds(el, ctx), point, tolerance);
    const reach = el.width / 2 + tolerance;
    if (el.type === "path") return distanceToPolyline(point, el.points) <= reach;
    const polylines = getShapePolylines(el);
    if (polylines.some((line) => distanceToPolyline(point, line) <= reach)) return true;
    return mode === "select" && el.shape !== "line" && el.shape !== "arrow" && boundsContain(getElementBounds(el, ctx), point);
  }
  function findTopElementAt(elements, point, tolerance, ctx) {
    for (let i = elements.length - 1; i >= 0; i--) {
      if (hitTestElement(elements[i], point, tolerance, ctx, "select")) return elements[i];
    }
    return null;
  }
  function getHandlePoint(bounds, handle) {
    const u = handle.includes("w") ? 0 : handle.includes("e") ? 1 : 0.5;
    const v = handle.includes("n") ? 0 : handle.includes("s") ? 1 : 0.5;
    return { x: bounds.x + bounds.width * u, y: bounds.y + bounds.height * v };
  }
  function resizeBounds(bounds, handle, point, minSize = 8, aspect) {
    let left = bounds.x;
    let top = bounds.y;
    let right = bounds.x + bounds.width;
    let bottom = bounds.y + bounds.height;
    if (handle.includes("w")) left = Math.min(point.x, right - minSize);
    if (handle.includes("e")) right = Math.max(point.x, left + minSize);
    if (handle.includes("n")) top = Math.min(point.y, bottom - minSize);
    if (handle.includes("s")) bottom = Math.max(point.y, top + minSize);
    if (!aspect || !Number.isFinite(aspect)) return { x: left, y: top, width: right - left, height: bottom - top };
    const isCorner = handle.length === 2;
    const horizontal = handle === "e" || handle === "w";
    let width = right - left;
    let height = bottom - top;
    if (isCorner ? width / height > aspect : horizontal) height = width / aspect;
    else width = height * aspect;
    return {
      x: handle.includes("w") ? right - width : left,
      y: handle.includes("n") ? bottom - height : top,
      width,
      height
    };
  }
  function snapToAngle(origin, point) {
    const step = Math.PI / 4;
    const angle = Math.round(Math.atan2(point.y - origin.y, point.x - origin.x) / step) * step;
    const length = Math.hypot(point.x - origin.x, point.y - origin.y);
    return { x: origin.x + length * Math.cos(angle), y: origin.y + length * Math.sin(angle) };
  }
  function isLineShape(el) {
    return el.type === "shape" && (el.shape === "line" || el.shape === "arrow");
  }
  function mapErase(el, map, widthScale = 1) {
    return el.erase?.map((stroke) => ({ width: stroke.width * widthScale, points: stroke.points.map(map) }));
  }
  function moveElement(el, dx, dy) {
    const map = (p) => ({ x: p.x + dx, y: p.y + dy });
    const erase = mapErase(el, map);
    if (el.type === "text") return { ...el, erase, x: el.x + dx, y: el.y + dy };
    if (el.type === "path") return { ...el, erase, points: el.points.map(map) };
    return { ...el, erase, start: map(el.start), end: map(el.end) };
  }
  function transformElement(el, from, to, handle) {
    if (el.type === "text") {
      const horizontal = handle === "e" || handle === "w";
      const raw = horizontal ? to.width / Math.max(1, from.width) : to.height / Math.max(1, from.height);
      const minScale = getSketchFontSize(SKETCH_MIN_STROKE) / el.fontSize;
      const maxScale = getSketchFontSize(SKETCH_MAX_STROKE) / el.fontSize;
      const scale = Math.min(maxScale, Math.max(minScale, raw));
      const x = handle.includes("w") ? from.x + from.width * (1 - scale) : from.x;
      const y = handle.includes("n") ? from.y + from.height * (1 - scale) : from.y;
      const mapText = (p) => ({ x: x + (p.x - from.x) * scale, y: y + (p.y - from.y) * scale });
      return { ...el, erase: mapErase(el, mapText, scale), x, y, fontSize: el.fontSize * scale };
    }
    const map = (p) => ({
      x: from.width < 1 ? p.x + to.x - from.x : to.x + (p.x - from.x) / from.width * to.width,
      y: from.height < 1 ? p.y + to.y - from.y : to.y + (p.y - from.y) / from.height * to.height
    });
    const erase = mapErase(el, map);
    if (el.type === "path") return { ...el, erase, points: el.points.map(map) };
    return { ...el, erase, start: map(el.start), end: map(el.end) };
  }
  function getTransformBounds(el, ctx) {
    const b = getElementBounds(el, ctx);
    if (el.type === "text") return b;
    const pad = el.width / 2;
    return { x: b.x + pad, y: b.y + pad, width: b.width - pad * 2, height: b.height - pad * 2 };
  }
  function drawSketchElement(ctx, el) {
    ctx.save();
    ctx.strokeStyle = el.color;
    ctx.fillStyle = el.color;
    ctx.lineWidth = el.width;
    ctx.lineCap = "round";
    ctx.lineJoin = "round";
    if (el.type === "text") {
      ctx.font = `${el.fontSize}px ${SKETCH_FONT_FAMILY}`;
      const lineHeight = el.fontSize * SKETCH_LINE_HEIGHT;
      const metrics = ctx.measureText(el.text);
      const ascent = metrics.fontBoundingBoxAscent;
      const baseline = (lineHeight - ascent - metrics.fontBoundingBoxDescent) / 2 + ascent;
      el.text.split("\n").forEach((line, idx) => ctx.fillText(line, el.x, el.y + baseline + idx * lineHeight));
      ctx.restore();
      return;
    }
    if (el.type === "path") {
      const points = el.points;
      if (points.length === 1) {
        ctx.beginPath();
        ctx.arc(points[0].x, points[0].y, el.width / 2, 0, Math.PI * 2);
        ctx.fill();
        ctx.restore();
        return;
      }
      ctx.beginPath();
      ctx.moveTo(points[0].x, points[0].y);
      for (let i = 1; i < points.length - 1; i++) {
        const mid = { x: (points[i].x + points[i + 1].x) / 2, y: (points[i].y + points[i + 1].y) / 2 };
        ctx.quadraticCurveTo(points[i].x, points[i].y, mid.x, mid.y);
      }
      const last = points[points.length - 1];
      ctx.lineTo(last.x, last.y);
      ctx.stroke();
      ctx.restore();
      return;
    }
    for (const line of getShapePolylines(el)) {
      ctx.beginPath();
      ctx.moveTo(line[0].x, line[0].y);
      for (const p of line.slice(1)) ctx.lineTo(p.x, p.y);
      ctx.stroke();
    }
    ctx.restore();
  }
  var SKETCH_LONG_EDGE = 1536;
  function getSketchCanvasSize(size, longEdge = SKETCH_LONG_EDGE) {
    const match = /^\s*(\d+)\s*[xX×]\s*(\d+)\s*$/.exec(size);
    const width = match ? Number(match[1]) : 1;
    const height = match ? Number(match[2]) : 1;
    if (!width || !height) return { width: longEdge, height: longEdge };
    return width >= height ? { width: longEdge, height: Math.round(longEdge * height / width) } : { width: Math.round(longEdge * width / height), height: longEdge };
  }
  function drawEraseStroke(ctx, stroke) {
    ctx.save();
    ctx.globalCompositeOperation = "destination-out";
    drawSketchElement(ctx, { id: "", type: "path", color: "#000", width: stroke.width, points: stroke.points });
    ctx.restore();
  }
  function renderErasedElement(el, scratch) {
    const sctx = scratch.getContext("2d");
    const b = getElementBounds(el, sctx);
    const x = Math.floor(b.x) - 2;
    const y = Math.floor(b.y) - 2;
    scratch.width = Math.max(1, Math.ceil(b.width) + 4);
    scratch.height = Math.max(1, Math.ceil(b.height) + 4);
    sctx.setTransform(1, 0, 0, 1, -x, -y);
    drawSketchElement(sctx, el);
    for (const stroke of el.erase ?? []) drawEraseStroke(sctx, stroke);
    sctx.setTransform(1, 0, 0, 1, 0, 0);
    return { x, y };
  }
  function drawSketchElementWithErase(ctx, el, scratch) {
    if (!el.erase?.length) {
      drawSketchElement(ctx, el);
      return;
    }
    const offset = renderErasedElement(el, scratch);
    ctx.drawImage(scratch, offset.x, offset.y);
  }
  function isSketchElementVisible(el, probe) {
    const pctx = probe.getContext("2d", { willReadFrequently: true });
    renderErasedElement(el, probe);
    const data = pctx.getImageData(0, 0, probe.width, probe.height).data;
    for (let i = 3; i < data.length; i += 4) {
      if (data[i] > 8) return true;
    }
    return false;
  }

  // vendor/gpt-image-playground/lib/viewportTransform.ts
  var viewportTransform_exports = {};
  __export(viewportTransform_exports, {
    clampViewTransform: () => clampViewTransform,
    clientPointToCanvasPoint: () => clientPointToCanvasPoint,
    getComfortableInitialTransform: () => getComfortableInitialTransform,
    getPinchTransform: () => getPinchTransform,
    zoomAtPoint: () => zoomAtPoint
  });
  var MIN_SCALE = 1;
  var MAX_SCALE = 8;
  function clamp(value, min, max) {
    return Math.min(max, Math.max(min, value));
  }
  function clampViewTransform(transform, viewportSize, bounds) {
    const scale = clamp(transform.scale, MIN_SCALE, MAX_SCALE);
    if (scale === MIN_SCALE) {
      return { scale, x: 0, y: 0 };
    }
    const b = bounds ?? { x: 0, y: 0, ...viewportSize };
    const clampAxis = (value, size, start, visible) => {
      const scaled = size * scale;
      return scaled > visible ? clamp(value, start + visible - scaled, start) : clamp(value, start, start + visible - scaled);
    };
    return {
      scale,
      x: clampAxis(transform.x, viewportSize.width, b.x, b.width),
      y: clampAxis(transform.y, viewportSize.height, b.y, b.height)
    };
  }
  function zoomAtPoint(transform, point, nextScale, viewportSize, bounds) {
    const localPoint = {
      x: (point.x - transform.x) / transform.scale,
      y: (point.y - transform.y) / transform.scale
    };
    const scale = clamp(nextScale, MIN_SCALE, MAX_SCALE);
    return clampViewTransform({
      scale,
      x: point.x - localPoint.x * scale,
      y: point.y - localPoint.y * scale
    }, viewportSize, bounds);
  }
  function getPinchTransform(input) {
    const localPoint = {
      x: (input.startCentroid.x - input.startTransform.x) / input.startTransform.scale,
      y: (input.startCentroid.y - input.startTransform.y) / input.startTransform.scale
    };
    const distanceRatio = input.startDistance > 0 ? input.nextDistance / input.startDistance : 1;
    const scale = clamp(input.startTransform.scale * distanceRatio, MIN_SCALE, MAX_SCALE);
    return clampViewTransform({
      scale,
      x: input.nextCentroid.x - localPoint.x * scale,
      y: input.nextCentroid.y - localPoint.y * scale
    }, input.viewportSize, input.bounds);
  }
  function clientPointToCanvasPoint(rect, point, canvasSize) {
    return {
      x: (point.x - rect.left) / rect.width * canvasSize.width,
      y: (point.y - rect.top) / rect.height * canvasSize.height
    };
  }
  function getComfortableInitialTransform(imageViewportSize, editorViewportSize, isCompactLayout) {
    if (!isCompactLayout || imageViewportSize.width <= 0 || imageViewportSize.height <= 0) {
      return { scale: MIN_SCALE, x: 0, y: 0 };
    }
    const targetHeight = editorViewportSize.height * 0.42;
    const scale = clamp(targetHeight / imageViewportSize.height, MIN_SCALE, 3);
    return zoomAtPoint(
      { scale: MIN_SCALE, x: 0, y: 0 },
      { x: imageViewportSize.width / 2, y: imageViewportSize.height / 2 },
      scale,
      imageViewportSize
    );
  }

  // src/sketch-adapter.ts
  function isolated(el, ctx, size, allocate, release) {
    const b = getElementBounds(el, ctx);
    const x = Math.max(0, Math.floor(b.x) - 2), y = Math.max(0, Math.floor(b.y) - 2);
    const right = Math.min(size.width, Math.ceil(b.x + b.width) + 2);
    const bottom = Math.min(size.height, Math.ceil(b.y + b.height) + 2);
    if (right <= x || bottom <= y) return null;
    const canvas = allocate(right - x, bottom - y);
    try {
      const scratch = canvas.getContext("2d", { willReadFrequently: true });
      if (!scratch) throw new Error("Canvas 2D context is null");
      scratch.setTransform(1, 0, 0, 1, -x, -y);
      drawSketchElement(scratch, el);
      for (const stroke of el.erase ?? []) drawEraseStroke(scratch, stroke);
      scratch.setTransform(1, 0, 0, 1, 0, 0);
      return { canvas, ctx: scratch, x, y };
    } catch (error) {
      release(canvas);
      throw error;
    }
  }
  function drawElements(ctx, elements, size, allocate, release) {
    for (const el of elements) {
      if (!el.erase?.length) {
        drawSketchElement(ctx, el);
        continue;
      }
      const scratch = isolated(el, ctx, size, allocate, release);
      if (!scratch) continue;
      try {
        ctx.drawImage(scratch.canvas, scratch.x, scratch.y);
      } finally {
        release(scratch.canvas);
      }
    }
  }
  function eraseElements(elements, stroke, ctx, size, allocate, release) {
    let changed = false;
    const next = [];
    const reach = stroke.width / 2;
    const xs = stroke.points.map((p) => p.x), ys = stroke.points.map((p) => p.y);
    const left = Math.min(...xs) - reach, right = Math.max(...xs) + reach;
    const top = Math.min(...ys) - reach, bottom = Math.max(...ys) + reach;
    for (const el of elements) {
      const bounds = getElementBounds(el, ctx);
      if (bounds.x > right || bounds.x + bounds.width < left || bounds.y > bottom || bounds.y + bounds.height < top) {
        next.push(el);
        continue;
      }
      if (!stroke.points.some((p) => hitTestElement(el, p, stroke.width / 2, ctx, "erase"))) {
        next.push(el);
        continue;
      }
      const erased = { ...el, erase: [...el.erase ?? [], stroke] };
      const scratch = isolated(erased, ctx, size, allocate, release);
      if (!scratch) {
        next.push(el);
        continue;
      }
      try {
        const before = isolated(el, ctx, size, allocate, release);
        try {
          const data = scratch.ctx.getImageData(0, 0, scratch.canvas.width, scratch.canvas.height).data;
          const original = before?.ctx.getImageData(0, 0, before.canvas.width, before.canvas.height).data;
          const differs = original && data.some((v, i) => v !== original[i]);
          if (!differs) {
            next.push(el);
            continue;
          }
          changed = true;
          if (data.some((v, i) => i % 4 === 3 && v > 8)) next.push(erased);
        } finally {
          if (before) release(before.canvas);
        }
      } finally {
        release(scratch.canvas);
      }
    }
    return changed ? next : elements;
  }
  function sampleEraseStroke(stroke, maxPoints = 16e3) {
    if (!stroke.points.length) return stroke;
    const points = [stroke.points[0]], step = Math.max(1, stroke.width / 3);
    for (let i = 1; i < stroke.points.length; i++) {
      const a = stroke.points[i - 1], b = stroke.points[i];
      const count = Math.max(1, Math.ceil(Math.hypot(b.x - a.x, b.y - a.y) / step));
      if (points.length + count > maxPoints) throw Object.assign(new Error("Erase stroke is too long"), { code: "COMMAND_LIMIT" });
      for (let j = 1; j <= count; j++) points.push({ x: a.x + (b.x - a.x) * j / count, y: a.y + (b.y - a.y) * j / count });
    }
    return { ...stroke, points };
  }

  // src/invoke-compositor.ts
  function compositeCanvas(adapters, rect, allocate, compositingOptions) {
    const canvas = allocate(rect.width, rect.height);
    const ctx = canvas.getContext("2d");
    if (!ctx) throw new Error("Canvas 2D context is null");
    ctx.imageSmoothingEnabled = false;
    for (const adapter of adapters) {
      const layerCompositeOp = adapter.state.globalCompositeOperation;
      ctx.globalCompositeOperation = layerCompositeOp || compositingOptions?.globalCompositeOperation || "source-over";
      const adapterCanvas = adapter.getCanvas(rect);
      ctx.drawImage(adapterCanvas, 0, 0);
    }
    return canvas;
  }
  return __toCommonJS(index_exports);
})();
if (typeof module === 'object' && module.exports) module.exports = CockpitStudioUpstream;
