(function (root, factory) {
  'use strict';
  const value = factory();
  if (typeof module === 'object' && module.exports) module.exports = value;
  else Object.assign(root.CockpitChat ||= {}, value);
})(typeof window === 'object' ? window : globalThis, function () {
  'use strict';
  // Browser frames carry committed events (EVENT_BYTES), not raw upstream frames.
  function createSSEParser(onEvent, maxBytes = 1049600) {
    let buffer = '', data = [], id = '', type = '', frameBytes = 0;
    const length = s => new TextEncoder().encode(s).length;
    function line(value) {
      frameBytes += length(value) + 1;
      if (frameBytes > maxBytes) throw new Error('事件帧超过限制，已停止读取。');
      if (!value) {
        if (data.length) {
          const event = JSON.parse(data.join('\n'));
          if (String(event.seq) !== id || event.type !== type) throw new Error('事件流序号或类型不一致。');
          onEvent(event);
        }
        data = []; id = ''; type = ''; frameBytes = 0; return;
      }
      if (value.startsWith(':')) return;
      const split = value.indexOf(':'); const field = split < 0 ? value : value.slice(0, split);
      let content = split < 0 ? '' : value.slice(split + 1); if (content.startsWith(' ')) content = content.slice(1);
      if (field === 'data') data.push(content);
      if (field === 'id') id = content;
      if (field === 'event') type = content;
    }
    return {
      push(chunk, final = false) {
        buffer += chunk;
        let start = 0;
        for (let i = 0; i < buffer.length; i++) {
          if (buffer[i] !== '\r' && buffer[i] !== '\n') continue;
          if (buffer[i] === '\r' && i + 1 === buffer.length && !final) break;
          line(buffer.slice(start, i));
          if (buffer[i] === '\r' && buffer[i + 1] === '\n') i++;
          start = i + 1;
        }
        buffer = buffer.slice(start);
        if (length(buffer) + frameBytes > maxBytes) throw new Error('事件帧超过限制，已停止读取。');
        if (final && (buffer || data.length || id || type)) throw new Error('事件流结束于不完整帧，状态待确认。');
      },
    };
  }
  async function consumeRun(response, {onEvent, assertCurrent = () => {}, signal, maxFrameBytes = 1049600}) {
    const contentType = (response.headers.get('Content-Type') || '').split(';')[0].trim().toLowerCase();
    if (contentType === 'application/json') {
      const value = await response.json(); assertCurrent();
      if (value.replayed !== true || typeof value.run_id !== 'string') throw new Error('幂等响应格式无效，需只读恢复。');
      return {...value, terminal: false};
    }
    if (contentType !== 'text/event-stream' || !response.body?.getReader) throw new Error('运行响应不是事件流，需只读恢复。');
    const reader = response.body.getReader(), decoder = new TextDecoder('utf-8', {fatal: true}); let terminal = false, complete = false;
    const parser = createSSEParser(event => {
      assertCurrent(); if (signal?.aborted) throw new DOMException('已停止读取', 'AbortError');
      onEvent(event); if (event.type === 'run.terminal') terminal = true;
    }, maxFrameBytes);
    const abort = () => { reader.cancel().catch(() => {}); };
    signal?.addEventListener('abort', abort, {once: true});
    try {
      while (true) {
        assertCurrent(); if (signal?.aborted) throw new DOMException('已停止读取', 'AbortError');
        const part = await reader.read(); assertCurrent();
        if (signal?.aborted) throw new DOMException('已停止读取', 'AbortError');
        if (part.done) break;
        parser.push(decoder.decode(part.value, {stream: true}));
      }
      parser.push(decoder.decode(), true); complete = true;
      return {replayed: false, terminal};
    } finally {
      signal?.removeEventListener('abort', abort);
      if (!complete) await reader.cancel().catch(() => {});
      reader.releaseLock();
    }
  }
  return {createSSEParser, consumeRun};
});
