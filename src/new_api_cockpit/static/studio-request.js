/* Single-attempt Studio transport and immutable request serialization.
 * The injected fetch MUST be the existing auth.js wrapper, never a raw transport.
 * No automatic retry, field fallback or authentication POST replay.
 */
// @ts-check
  /** @typedef {import('./studio-domain.js').Intent} Intent */
  /** @typedef {import('./studio-domain.js').Model} Model */
  /** @typedef {{blob:Blob,width:number,height:number,role:import('./studio-domain.js').ReferenceRole}} Reference */
  /** @typedef {{image:Blob,mask:Blob|null,guide:Blob|null,working:{width:number,height:number},documentId:string,revision:number}} Snapshot */
  /** @typedef {{tokenId:number,modelId:string,contractRevision:string,requestId:string,prompt:string,intent:Readonly<Intent>,localStrategy:string,parameters:Readonly<Record<string,string|number>>,snapshot:Snapshot|null,references:readonly Reference[],source:Blob|null,versionName:string}} Capture */
(function(root, factory) {
  const api = factory(typeof module === 'object' && module.exports ? require('./studio-domain.js') : root.CockpitStudioDomain);
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.CockpitStudioRequest = api;
})(globalThis, function(/** @type {typeof import('./studio-domain.js')} */ D) {
  'use strict';
  class Cancelled extends Error {}
  class RequestFailure extends Error {
    /** @param {string} message @param {boolean} [uncertain] @param {Record<string,string>} [details] */
    constructor(message, uncertain = false, details = {}) { super(message); this.uncertain = uncertain; this.details = details; }
  }
  /** @param {any} value @returns {Record<string,string>} */
  function safeDetails(value) {
    return Object.fromEntries(['category','http_status','provider_code','param','upstream_request_id','request_id','receipt_state']
      .filter(key => ['string','number'].includes(typeof value?.[key]))
      .map(key => [key,D.clean(value[key])]));
  }
  /** @param {Capture} capture */
  function makeBody(capture) {
    const common = {token_id:capture.tokenId,model:capture.modelId,prompt:capture.prompt,
      parameters:capture.parameters,request_id:capture.requestId,contract_revision:capture.contractRevision,intent:capture.intent};
    if (capture.intent.operation === 'generate') return common;
    if (!capture.snapshot) throw new Error('提交快照不可用。');
    const body = new FormData();
    for (const [key,value] of Object.entries(common)) body.append(key, typeof value === 'object' ? JSON.stringify(value) : String(value));
    body.append('image[]',capture.snapshot.image,'primary.png');
    if (capture.snapshot.guide) body.append('image[]',capture.snapshot.guide,'guide.png');
    capture.references.forEach((ref,i) => body.append('image[]',ref.blob,
      `reference-${i+1}.${ref.blob.type === 'image/jpeg' ? 'jpg' : ref.blob.type === 'image/webp' ? 'webp' : 'png'}`));
    if (capture.intent.operation === 'local' && capture.localStrategy === 'native-mask' && capture.snapshot.mask) {
      body.append('mask',capture.snapshot.mask,'mask.png');
    }
    return body;
  }
  /** @param {Snapshot} snapshot @param {readonly Reference[]} references @param {Model} model @param {Intent} intent */
  function validateSnapshot(snapshot, references, model, intent) {
    const local = intent.operation === 'local', guide = D.needsGuide(model,intent.operation,intent.has_annotations);
    if (!snapshot?.image || snapshot.image.type !== 'image/png') throw new Error('画布主图导出失败。');
    if (local && (!snapshot.mask || snapshot.mask.type !== 'image/png')) throw new Error('请先画出需要修改的选区。');
    if (!local && snapshot.mask) throw new Error('整图快照不应包含修改遮罩。');
    if (guide !== !!snapshot.guide || (snapshot.guide && snapshot.guide.type !== 'image/png')) throw new Error('指导图导出不完整，请重新提交。');
    if (1 + Number(guide) + references.length > (model.edit?.max_images || 0)) throw new Error('输入图片过多，主图和指导图也占用名额。');
    const files = [snapshot.image, ...(snapshot.guide ? [snapshot.guide] : []), ...references.map(x => x.blob)];
    const sentMask = local && model.edit?.local_strategy === 'native-mask' ? snapshot.mask : null;
    if (files.some(x => x.size > D.MAX_INPUT)) throw new Error('导出的单张输入图超过 8 MiB，请调整工作尺寸。');
    if (sentMask && sentMask.size >= 4000000) throw new Error('选区遮罩需小于 4 MB。');
    if (files.reduce((n,x) => n+x.size,sentMask?.size || 0) + 65536 > D.MAX_UPLOAD) throw new Error('本次上传超过 24 MiB。');
    const {width:w,height:h} = snapshot.working;
    if (!(w > 0 && h > 0) || w*h > D.MAX_MAIN_PIXELS || w*h*(guide ? 2 : 1)
        + references.reduce((n,x) => n+x.width*x.height,0) > D.MAX_TOTAL_PIXELS) throw new Error('输入图片总像素超过上限。');
    const limits=model.edit?.inputs;
    if (!limits) throw new Error('图片输入协议不完整，请刷新模型。');
    for (const blob of [snapshot.image,...(snapshot.guide ? [snapshot.guide] : [])]) {
      D.validateInput({blob,width:w,height:h},limits);
    }
    for (const ref of references) D.validateInput(ref,limits);
    if (w*h*(guide ? 2 : 1)+references.reduce((n,x)=>n+x.width*x.height,0)>limits.max_total_pixels) {
      throw new Error('输入图片总像素超过当前模型限制，请减少参考图或工作尺寸。');
    }
  }
  /** @param {{fetch:typeof fetch,assertAuth:(capture:any)=>void,isActive:()=>boolean,onUnauthorized:(reason:string)=>void}} deps */
  function createClient(deps) {
    const controllers = new Set();
    /** @param {any} capture @param {number} timeout @param {{sent:boolean}|null} [request] */
    function operation(capture, timeout, request = null) {
      const controller = new AbortController(); controllers.add(controller);
      let expired = false;
      /** @type {(reason:Error)=>void} */ let rejectAbort = () => {};
      /** @type {Promise<never>} */
      const aborted = new Promise((resolve,reject) => {rejectAbort=reject;}); aborted.catch(() => {});
      const abortError = () => !deps.isActive() ? new Cancelled() : new RequestFailure(
        request?.sent ? (expired ? '等待已超过 360 秒，结果未知。请先核对用量。' : '已结束本页等待，原请求可能仍在执行或计费。请先核对用量。')
          : expired ? '读取超时，请手动刷新。' : '操作已结束。',!!request?.sent);
      const onAbort = () => rejectAbort(abortError());
      controller.signal.addEventListener('abort',onAbort,{once:true});
      const timer = setTimeout(() => {expired=true;controller.abort();},timeout);
      return {controller,signal:controller.signal,
        assert() {deps.assertAuth(capture);if (controller.signal.aborted) throw abortError();},
        /** @template T @param {Promise<T>} promise @returns {Promise<T>} */
        async wait(promise) {const result=await Promise.race([promise,aborted]);this.assert();return result;},
        dispose() {clearTimeout(timer);controller.signal.removeEventListener('abort',onAbort);controllers.delete(controller);}};
    }
    /** @param {string} path @param {any} body @param {ReturnType<typeof operation>} op */
    async function json(path, body, op) {
      op.assert();const multipart=body instanceof FormData;
      const response=await op.wait(deps.fetch(`/cockpit/api/image-studio/${path}`,{
        method:body === undefined ? 'GET' : 'POST',credentials:'same-origin',cache:'no-store',redirect:'error',signal:op.signal,
        ...(body === undefined ? {} : {headers:{'X-Cockpit-Studio':'1',...(multipart ? {} : {'Content-Type':'application/json'})},
          body:multipart ? body : JSON.stringify(body)})}));
      if (response.status === 401) {deps.onUnauthorized('登录已失效，请刷新页面。');throw new Cancelled();}
      let data;
      try {data=await op.wait(response.json());}
      catch(error) {if (error instanceof Cancelled || op.signal.aborted) throw error;throw new Error('后台响应无法读取。');}
      if (data?.code === 'AUTH_SESSION_CHANGED') {deps.onUnauthorized('登录已变化，请刷新页面。');throw new Cancelled();}
      if (!response.ok || data?.error) throw new RequestFailure(D.clean(data?.error || `请求失败（${response.status}）。`),
        data?.uncertain === true || (data?.details?.category === 'duplicate' && ['dispatched','unknown'].includes(data?.details?.receipt_state)) || (response.status >= 500 && data?.uncertain !== false),
        safeDetails({...data?.details,http_status:response.status,request_id:data?.request_id}));
      return data;
    }
    return {operation,json,close() {for (const controller of controllers) controller.abort();}};
  }
  return Object.freeze({Cancelled,RequestFailure,safeDetails,makeBody,validateSnapshot,createClient});
});
