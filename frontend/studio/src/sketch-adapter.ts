/* Adapted from GPT Image Playground sketch.ts / SketchBoardModal.tsx (MIT).
 * Copyright (c) 2026 CookSleep. Local changes: bounded, clipped scratch allocation,
 * supplied canvas allocator, synchronous immutable inputs, no React or persistence.
 */
import {drawSketchElement, drawEraseStroke, getElementBounds, hitTestElement} from '../vendor/gpt-image-playground/lib/sketch';
import type {SketchElement, SketchEraseStroke} from '../vendor/gpt-image-playground/lib/sketch';

type Size = {width: number; height: number};
type Allocate = (width: number, height: number) => HTMLCanvasElement;
type Release = (canvas: HTMLCanvasElement) => void;

// Upstream isolates erased objects so destination-out never erases other objects.
// Clip that allocation to the document: moving a mark far away cannot allocate
// an unbounded bitmap. The same renderer serves the live view and submitted guide.
function isolated(el: SketchElement, ctx: CanvasRenderingContext2D, size: Size, allocate: Allocate, release: Release) {
  const b = getElementBounds(el, ctx);
  const x = Math.max(0, Math.floor(b.x) - 2), y = Math.max(0, Math.floor(b.y) - 2);
  const right = Math.min(size.width, Math.ceil(b.x + b.width) + 2);
  const bottom = Math.min(size.height, Math.ceil(b.y + b.height) + 2);
  if (right <= x || bottom <= y) return null;
  const canvas = allocate(right - x, bottom - y);
  try {
    const scratch = canvas.getContext('2d', {willReadFrequently: true});
    if (!scratch) throw new Error('Canvas 2D context is null');
    scratch.setTransform(1, 0, 0, 1, -x, -y);
    drawSketchElement(scratch, el);
    for (const stroke of el.erase ?? []) drawEraseStroke(scratch, stroke);
    scratch.setTransform(1, 0, 0, 1, 0, 0);
    return {canvas, ctx: scratch, x, y};
  } catch (error) {release(canvas); throw error;}
}

export function drawElements(ctx: CanvasRenderingContext2D, elements: SketchElement[], size: Size, allocate: Allocate, release: Release) {
  for (const el of elements) {
    if (!el.erase?.length) {drawSketchElement(ctx, el); continue;}
    const scratch = isolated(el, ctx, size, allocate, release);
    if (!scratch) continue;
    try {ctx.drawImage(scratch.canvas, scratch.x, scratch.y);} finally {release(scratch.canvas);}
  }
}

export function eraseElements(elements: SketchElement[], stroke: SketchEraseStroke, ctx: CanvasRenderingContext2D,
  size: Size, allocate: Allocate, release: Release) {
  let changed = false;
  const next: SketchElement[] = [];
  const reach = stroke.width / 2;
  const xs = stroke.points.map(p => p.x), ys = stroke.points.map(p => p.y);
  const left = Math.min(...xs) - reach, right = Math.max(...xs) + reach;
  const top = Math.min(...ys) - reach, bottom = Math.max(...ys) + reach;
  for (const el of elements) {
    const bounds = getElementBounds(el, ctx);
    if (bounds.x > right || bounds.x + bounds.width < left || bounds.y > bottom || bounds.y + bounds.height < top) {next.push(el); continue;}
    if (!stroke.points.some(p => hitTestElement(el, p, stroke.width / 2, ctx, 'erase'))) {next.push(el); continue;}
    const erased = {...el, erase: [...(el.erase ?? []), stroke]};
    const scratch = isolated(erased, ctx, size, allocate, release);
    if (!scratch) {next.push(el); continue;}
    try {
      // Do not add invisible erase operations to history when traversing a hole.
      const before = isolated(el, ctx, size, allocate, release);
      try {
        const data = scratch.ctx.getImageData(0, 0, scratch.canvas.width, scratch.canvas.height).data;
        const original = before?.ctx.getImageData(0, 0, before.canvas.width, before.canvas.height).data;
        const differs = original && data.some((v, i) => v !== original[i]);
        if (!differs) {next.push(el); continue;}
        changed = true;
        if (data.some((v, i) => i % 4 === 3 && v > 8)) next.push(erased);
      } finally {if (before) release(before.canvas);}
    } finally {release(scratch.canvas);}
  }
  return changed ? next : elements;
}

// Sparse pointer samples must not skip objects crossed between two events.
// Resampling is bounded before allocating or writing an erase operation.
export function sampleEraseStroke(stroke: SketchEraseStroke, maxPoints = 16000): SketchEraseStroke {
  if (!stroke.points.length) return stroke;
  const points = [stroke.points[0]], step = Math.max(1, stroke.width / 3);
  for (let i = 1; i < stroke.points.length; i++) {
    const a = stroke.points[i - 1], b = stroke.points[i];
    const count = Math.max(1, Math.ceil(Math.hypot(b.x - a.x, b.y - a.y) / step));
    if (points.length + count > maxPoints) throw Object.assign(new Error('Erase stroke is too long'), {code: 'COMMAND_LIMIT'});
    for (let j = 1; j <= count; j++) points.push({x: a.x + (b.x - a.x) * j / count, y: a.y + (b.y - a.y) * j / count});
  }
  return {...stroke, points};
}
