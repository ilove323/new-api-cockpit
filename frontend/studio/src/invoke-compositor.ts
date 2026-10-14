/**
 * Adapted from InvokeAI CanvasCompositorModule.getCompositeCanvas(), Apache-2.0.
 * Copyright 2022-2026 InvokeAI Contributors. Upstream notice: vendor/invokeai/NOTICE.
 * Upstream aa46003581e2c482e03e62116f68ef3c3a6f0233; see vendor/sources.json.
 * Local changes: inject canvas allocation and plain raster adapters; remove Redux,
 * image uploads and global cache. Caller owns bounded buffers and their lifetime.
 * Keep upstream ordered adapter rendering and per-layer compositing precedence.
 */
export interface RasterAdapter {
  state: { globalCompositeOperation?: GlobalCompositeOperation };
  getCanvas(rect: {width: number; height: number}): HTMLCanvasElement;
}
export function compositeCanvas(
  adapters: RasterAdapter[], rect: {width: number; height: number},
  allocate: (width: number, height: number) => HTMLCanvasElement,
  compositingOptions?: { globalCompositeOperation?: GlobalCompositeOperation },
): HTMLCanvasElement {
  const canvas = allocate(rect.width, rect.height);
  const ctx = canvas.getContext('2d');
  if (!ctx) throw new Error('Canvas 2D context is null');
  ctx.imageSmoothingEnabled = false;
  for (const adapter of adapters) {
    const layerCompositeOp = adapter.state.globalCompositeOperation;
    ctx.globalCompositeOperation = layerCompositeOp || compositingOptions?.globalCompositeOperation || 'source-over';
    const adapterCanvas = adapter.getCanvas(rect);
    ctx.drawImage(adapterCanvas, 0, 0);
  }
  return canvas;
}
