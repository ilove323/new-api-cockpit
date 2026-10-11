/* Candidate/version and Blob lifetime ownership, independent of DOM rendering.
 * Preview/download/discard never apply. The UI explicitly loads an applied version.
 */
// @ts-check
/** @typedef {import('./studio-request.js').Capture} Capture */
/** @typedef {{blob:Blob,width:number,height:number}} Result */
/** @typedef {{id:string,name:string,blob:Blob,documentId:string}} Version */
/** @typedef {{id:string,capture:Capture,result:Result,composite:Blob|null,compositeReason:string,duration:number|null,selected:string,originalURL:string,compositeURL:string,sourceURL:string}} Candidate */
/** @typedef {ReturnType<ReturnType<typeof import('./studio-request.js').createClient>['operation']>} Operation */
(function(root, factory) {
  const api = factory(typeof module === 'object' && module.exports ? require('./studio-domain.js') : root.CockpitStudioDomain);
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.CockpitStudioResults = api;
})(globalThis, function(/** @type {typeof import('./studio-domain.js')} */ D) {
  'use strict';
  const {MIME,MAX_OUTPUT_PIXELS,MAX_CANDIDATES,MAX_VERSIONS,clean} = D;
  function createStore() {
    /** @type {Candidate[]} */ const candidates=[];
    /** @type {Version[]} */ const versions=[];
    const urls=new Set();let serial=0;
    const pool={
      /** @param {Blob} blob */
      urlFor(blob) {const url=URL.createObjectURL(blob);urls.add(url);return url;},
      /** @param {string} url */
      revoke(url) {if (url && urls.delete(url)) URL.revokeObjectURL(url);}
    };
    /** @param {Blob} blob @param {number} maxPixels */
  async function inspect(blob, maxPixels) {
    if (!MIME.has(blob?.type) || !blob.size) throw new Error('请选择 PNG、JPEG 或 WebP 图片。');
    /** @type {any} */ let decoded;
    if (typeof createImageBitmap === 'function') {
      decoded = await createImageBitmap(blob);
      const info = {width: decoded.width, height: decoded.height}; decoded.close();
      if (!info.width || !info.height || info.width * info.height > maxPixels) throw new Error('图片像素超过上限，请选择较小的图片。');
      return info;
    }
    const url = pool.urlFor(blob);
    try {
      decoded = new Image(); decoded.src = url; await decoded.decode();
      const info = {width: decoded.naturalWidth, height: decoded.naturalHeight};
      if (!info.width || !info.height || info.width * info.height > maxPixels) throw new Error('图片像素超过上限，请选择较小的图片。');
      return info;
    } finally { if (decoded) decoded.removeAttribute('src'); pool.revoke(url); }
  }
  /** @param {any} record @param {Operation} op @returns {Promise<Result>} */
  async function decodeResult(record, op) {
    if (!record || !MIME.has(record.mime) || typeof record.b64_json !== 'string'
        || !record.b64_json.length || record.b64_json.length > 48 * 1024 * 1024
        || !/^[A-Za-z0-9+/]+={0,2}$/.test(record.b64_json) || record.b64_json.length % 4) {
      throw new Error('后台未返回可读取的图片。');
    }
    const raw = atob(record.b64_json), bytes = new Uint8Array(raw.length);
    for (let i = 0; i < raw.length; i++) bytes[i] = raw.charCodeAt(i);
    const blob = new Blob([bytes], {type: record.mime}); record.b64_json = '';
    const info = await op.wait(inspect(blob, MAX_OUTPUT_PIXELS));
    if (Number(record.width) !== info.width || Number(record.height) !== info.height) throw new Error('结果尺寸与后台记录不一致。');
    return {blob, ...info};
  }
    const store = {candidates,versions,...pool,inspect,decodeResult,
      /** @param {Capture} capture @param {any} data @param {{compose:(blob:Blob,snapshot:import('./studio-request.js').Snapshot)=>Promise<{blob:Blob|null,reason?:string}>}} editor @param {Operation} op */
      async finish(capture,data,editor,op) {
        if(data.request_id!==capture.requestId || data.model!==capture.modelId || !Array.isArray(data.images) || data.images.length!==1) {
          throw new Error('后台结果与本次请求不一致。');
        }
        const result=await decodeResult(data.images[0],op);
        let composite=null,compositeReason='';
        if(capture.intent.operation==='local' && capture.snapshot) {
          try {
            const composed=await op.wait(editor.compose(result.blob,capture.snapshot));
            composite=composed?.blob || null;
            compositeReason=composite ? '' : composed?.reason==='working_size_mismatch'
              ? '结果尺寸与提交画幅不同，已保留模型原图。' : '无法合成到提交原图，已保留模型原图。';
            if(composite && composite.type!=='image/png') {composite=null;compositeReason='合成结果非无损 PNG，已保留模型原图。';}
          } catch(error) {
            if(op.signal.aborted) throw error;
            compositeReason='局部合成失败，已保留模型原图。';
          }
        }
        op.assert();
        return store.addCandidate(capture,result,composite,compositeReason,Number.isFinite(data.duration_seconds) ? data.duration_seconds : null);
      },
      /** @param {Blob} blob @param {string} name @param {string} documentId */
      addVersion(blob,name,documentId) {
        const version={id:`v${++serial}`,name:`${serial} · ${clean(name || '主图')}`,blob,documentId};
        versions.push(version);if (versions.length>MAX_VERSIONS) versions.shift();return version;
      },
      /** @param {Capture} capture @param {Result} result @param {Blob|null} composite @param {string} compositeReason @param {number|null} duration */
      addCandidate(capture,result,composite,compositeReason,duration) {
        if(candidates.length>=MAX_CANDIDATES) throw new Error('候选区已满。');
        const candidate={id:capture.requestId,capture,result,composite,compositeReason,duration,
          selected:composite ? 'composite' : 'original',originalURL:pool.urlFor(result.blob),
          compositeURL:composite ? pool.urlFor(composite) : '',sourceURL:capture.source ? pool.urlFor(capture.source) : ''};
        candidates.push(candidate);return candidate;
      },
      /** @param {Candidate} candidate */
      discard(candidate) {
        const index=candidates.indexOf(candidate);if(index<0)return;
        for(const url of [candidate.originalURL,candidate.compositeURL,candidate.sourceURL])pool.revoke(url);
        candidates.splice(index,1);
      },
      clear() {for(const url of [...urls]) pool.revoke(url);candidates.length=0;versions.length=0;}
    };
    return store;
  }
  /** @param {Candidate} candidate @returns {Blob} */
  const selectedBlob=candidate=>candidate.selected==='composite'&&candidate.composite ? candidate.composite : candidate.result.blob;
  /** @param {Candidate} candidate @returns {string} */
  const selectedURL=candidate=>candidate.selected==='composite'&&candidate.composite ? candidate.compositeURL : candidate.originalURL;
  return Object.freeze({createStore,selectedBlob,selectedURL});
});
