/* Lightbox gestures ported from GPT Image Playground Lightbox.tsx (MIT).
 * Copyright (c) 2026 CookSleep. Pinned source and changes: docs/image-studio-sources.md.
 * Local changes: native dialog/Pointer Events, bounded viewportTransform helpers,
 * independent compare panes, lifecycle cleanup, no React, fetch or stored images.
 */
(function (root, factory) {
  'use strict';
  const api = factory(root);
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.CockpitStudioLightbox = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function (root) {
  'use strict';
  function create(panes, {onZoom = () => {}, onNavigate = () => {}} = {}) {
    const V = root.CockpitStudioUpstream?.viewport;
    if (!V) throw new Error('Pinned lightbox source bundle is required');
    let active = false, disposed = false;
    const listeners = [], states = panes.map(({host, image}) => ({host, image,
      view: {scale: 1, x: 0, y: 0}, points: new Map(), drag: null, pinch: null, pinched: false}));
    function size(s) {const r = s.host.getBoundingClientRect(); return {width: r.width || 1, height: r.height || 1};}
    function point(s, e) {const r = s.host.getBoundingClientRect(); return {x: e.clientX - r.left, y: e.clientY - r.top};}
    function apply(s, view) {
      s.view = V.clampViewTransform(view, size(s));
      s.image.style.transform = `translate(${s.view.x}px, ${s.view.y}px) scale(${s.view.scale})`;
      s.host.style.cursor = s.view.scale > 1 ? 'grab' : 'zoom-in';
      if (s === states[0]) onZoom(s.view.scale);
    }
    function listen(el, name, fn, options) {el.addEventListener(name, fn, options); listeners.push([el, name, fn, options]);}
    function release(s, id) {try {if (s.host.hasPointerCapture(id)) s.host.releasePointerCapture(id);} catch (_) { /* detached pane */ }}
    function stop(s) {
      const ids = [...s.points.keys()]; s.points.clear(); s.drag = null; s.pinch = null; s.pinched = false;
      for (const id of ids) release(s, id);
    }
    function zoom(s, factor, anchor = {x: size(s).width / 2, y: size(s).height / 2}) {
      stop(s); apply(s, V.zoomAtPoint(s.view, anchor, s.view.scale * factor, size(s)));
    }
    for (const s of states) {
      listen(s.host, 'wheel', e => {
        if (!active || disposed) return; e.preventDefault();
        // Upstream wheel zoom is anchored to the pointer, not the image center.
        zoom(s, e.deltaY < 0 ? 1.15 : 1 / 1.15, point(s, e));
      }, {passive: false});
      listen(s.host, 'pointerdown', e => {
        if (!active || disposed || (e.button !== undefined && e.button !== 0)) return;
        e.preventDefault(); const p = point(s, e); s.points.set(e.pointerId, p);
        try {s.host.setPointerCapture(e.pointerId);} catch (_) { /* synthetic event */ }
        if (s.points.size === 2) {
          const [a, b] = [...s.points.values()]; s.drag = null; s.pinched = true;
          s.pinch = {startTransform: {...s.view}, startCentroid: {x: (a.x + b.x) / 2, y: (a.y + b.y) / 2},
            startDistance: Math.max(1, Math.hypot(a.x - b.x, a.y - b.y))};
        } else if (s.points.size === 1 && !s.pinched) s.drag = {start: p, view: {...s.view}};
      });
      listen(s.host, 'pointermove', e => {
        if (!active || !s.points.has(e.pointerId)) return; e.preventDefault();
        const p = point(s, e); s.points.set(e.pointerId, p);
        if (s.pinch && s.points.size === 2) {
          const [a, b] = [...s.points.values()];
          apply(s, V.getPinchTransform({...s.pinch, nextCentroid: {x: (a.x + b.x) / 2, y: (a.y + b.y) / 2},
            nextDistance: Math.hypot(a.x - b.x, a.y - b.y), viewportSize: size(s)}));
        } else if (s.drag && !s.pinched && s.view.scale > 1) {
          apply(s, {...s.view, x: s.drag.view.x + p.x - s.drag.start.x, y: s.drag.view.y + p.y - s.drag.start.y});
        }
      });
      const finish = e => {s.points.delete(e.pointerId); s.drag = null; s.pinch = null;
        if (!s.points.size) s.pinched = false; release(s, e.pointerId);};
      for (const name of ['pointerup', 'pointercancel', 'lostpointercapture']) listen(s.host, name, finish);
      listen(s.host, 'dblclick', e => {if (active) {e.preventDefault(); zoom(s, s.view.scale > 1 ? 1 / s.view.scale : 2, point(s, e));}});
      listen(s.image, 'dragstart', e => e.preventDefault());
    }
    const resize = () => {if (active && !disposed) for (const s of states) {stop(s); apply(s, s.view);}};
    const observer = root.ResizeObserver ? new root.ResizeObserver(resize) : null;
    if (observer) for (const s of states) observer.observe(s.host);
    const reset = () => {for (const s of states) {stop(s); apply(s, {scale: 1, x: 0, y: 0});}};
    return Object.freeze({
      reset, zoomBy(factor) {if (active && states[0]) zoom(states[0], factor);},
      setActive(value) {if (disposed) return; active = !!value; reset();},
      keyDown(e) {
        if (!active || e.defaultPrevented || /^(INPUT|SELECT|TEXTAREA|BUTTON)$/.test(e.target?.tagName || '') || e.target?.isContentEditable) return;
        if (e.key === 'ArrowLeft' || e.key === 'ArrowRight') {e.preventDefault(); onNavigate(e.key === 'ArrowLeft' ? -1 : 1);}
        else if (e.key === '+' || e.key === '=') {e.preventDefault(); this.zoomBy(1.15);}
        else if (e.key === '-') {e.preventDefault(); this.zoomBy(1 / 1.15);}
        else if (e.key === '0') {e.preventDefault(); reset();}
      },
      destroy() {if (disposed) return; active = false; reset(); disposed = true; observer?.disconnect();
        for (const [el, name, fn, options] of listeners) el.removeEventListener(name, fn, options);},
    });
  }
  return Object.freeze({create});
});
