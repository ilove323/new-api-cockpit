/* Pure Studio contracts and edit intent. No DOM, network, image decoding or secrets.
 * Check: tsc -p tests/tsconfig.studio.json; tests: node --test tests/test_studio_domain.cjs
 */
// @ts-check
  /** @typedef {'generate'|'whole'|'local'} Operation */
  /** @typedef {'reference'|'subject'|'style'|'composition'} ReferenceRole */
  /** @typedef {{width:number,height:number}} Dimensions */
  /** @typedef {{mime_types:string[],max_bytes:number,max_pixels:number,max_total_pixels:number,max_dimension:number,max_aspect_ratio:number}} InputLimits */
  /** @typedef {{sizes:string[],custom_size:boolean,qualities:string[],formats:string[],backgrounds:string[],input_fidelity:string[],allowed_fields:string[]}} Parameters */
  /** @typedef {{id:string,revision:string,generation:null|{parameters:Parameters},edit:null|{parameters:Parameters,inputs:InputLimits,local_strategy:'native-mask'|'reference-only'|'unavailable',max_images:number},unavailable_reason:string}} Model */
  /** @typedef {{operation:Operation,has_annotations:boolean,reference_roles:readonly ReferenceRole[]}} Intent */
  /** @typedef {{size:string,quality:string,format:string,background:string,fidelity:string,compression:string,width:string,height:string}} Inputs */
  /** @typedef {{hasMask?:boolean,hasAnnotations?:boolean}} Marks */
(function(root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.CockpitStudioDomain = api;
})(globalThis, function() {
  'use strict';
  const MAX_INPUT = 8 * 1024 * 1024, MAX_UPLOAD = 24 * 1024 * 1024;
  const MAX_MAIN_PIXELS = 16000000, MAX_TOTAL_PIXELS = 32000000, MAX_OUTPUT_PIXELS = 8500000;
  const MAX_CANDIDATES = 4, MAX_VERSIONS = 4, REQUEST_TIMEOUT = 360000;
  const MIME = new Set(['image/png', 'image/jpeg', 'image/webp']);
  const INPUT_LIMITS = Object.freeze({mime_types:Object.freeze([...MIME]),max_bytes:MAX_INPUT,
    max_pixels:MAX_MAIN_PIXELS,max_total_pixels:MAX_TOTAL_PIXELS,max_dimension:16000,max_aspect_ratio:100});
  const ROLES = Object.freeze(['reference', 'subject', 'style', 'composition']);
  /** @param {unknown} value */
  const clean = value => String(value ?? '').replace(/sk-[^\s"'<>`,;:()\[\]{}\\]+/gi, '[KEY 已隐藏]').slice(0, 500);
  /** @param {unknown} list @returns {string[]} */
  const values = list => Array.isArray(list) ? [...new Set(list.filter(x => typeof x === 'string' && x.length < 100))] : [];
  /** @param {string} value @returns {Dimensions|null} */
  function dimension(value) {
    const match = /^(\d+)x(\d+)$/.exec(value || '');
    return match ? {width:Number(match[1]),height:Number(match[2])} : null;
  }
  /** Missing limits use only the hard application bounds, never provider capabilities.
   * @param {any} value @returns {InputLimits|null} */
  function inputLimits(value) {
    if (value === undefined) return {...INPUT_LIMITS,mime_types:[...INPUT_LIMITS.mime_types]};
    if (!value || !Array.isArray(value.mime_types) || !value.mime_types.length
        || value.mime_types.some((/** @type {any} */ mime) => !MIME.has(mime))) return null;
    for (const key of /** @type {const} */ (['max_bytes','max_pixels','max_total_pixels','max_dimension','max_aspect_ratio'])) {
      if (!Number.isInteger(value[key]) || value[key]<1 || value[key]>INPUT_LIMITS[key]) return null;
    }
    return {mime_types:[...new Set(/** @type {string[]} */ (value.mime_types))],max_bytes:value.max_bytes,
      max_pixels:value.max_pixels,max_total_pixels:value.max_total_pixels,max_dimension:value.max_dimension,max_aspect_ratio:value.max_aspect_ratio};
  }
  /** Validate the actual file to be sent, not a visual preview or original before export.
   * @param {{blob:Blob,width:number,height:number}} image @param {InputLimits} limits */
  function validateInput(image,limits) {
    const {blob,width:w,height:h}=image;
    if (!limits.mime_types.includes(blob.type)) throw new Error('当前模型不接受这种输入图片格式。');
    if (!blob.size || blob.size>limits.max_bytes) throw new Error('输入图片超过当前模型的单图大小限制。');
    if (!Number.isInteger(w) || !Number.isInteger(h) || w<1 || h<1 || w*h>limits.max_pixels
        || Math.max(w,h)>limits.max_dimension || Math.max(w,h)>limits.max_aspect_ratio*Math.min(w,h)) {
      throw new Error('输入图片超出当前模型的像素、尺寸或比例限制。');
    }
  }
  /** Public input is untrusted. No legacy capability or optimistic route defaults.
   * @param {any} record @returns {Model|null} */
  function modelProfile(record) {
    if (!record || typeof record.id !== 'string' || !record.id.length || record.id.length > 256 || record.id !== record.id.trim() || /[\x00-\x1f\x7f\uD800-\uDFFF]/u.test(record.id)
        || !/^[a-f0-9]{64}$/.test(record.revision || '')) return null;
    /** @param {any} p @returns {Parameters|null} */
    const parameters = p => {
      if (!p || !Array.isArray(p.allowed_fields)) return null;
      const result = {sizes:values(p.sizes), custom_size:p.custom_size === true, qualities:values(p.qualities),
        formats:values(p.formats).filter(x => ['png','jpeg','webp'].includes(x)), backgrounds:values(p.backgrounds),
        input_fidelity:values(p.input_fidelity), allowed_fields:values(p.allowed_fields)};
      /** @type {Record<string,'sizes'|'qualities'|'formats'|'backgrounds'|'input_fidelity'>} */
      const lists = {size:'sizes',quality:'qualities',output_format:'formats',background:'backgrounds',input_fidelity:'input_fidelity'};
      if (result.allowed_fields.some(field => ![...Object.keys(lists),'output_compression'].includes(field))) return null;
      for (const [field,key] of Object.entries(lists)) {
        if (result.allowed_fields.includes(field)) {if (!result[key].length) return null;}
        else result[key]=[];
      }
      if (!result.allowed_fields.includes('size')) result.custom_size=false;
      return result;
    };
    const generation = parameters(record.generation?.parameters), edit = parameters(record.edit?.parameters), limits=inputLimits(record.edit?.inputs);
    const validEdit = edit && limits && ['native-mask','reference-only','unavailable'].includes(record.edit?.local_strategy)
      && Number.isInteger(record.edit?.max_images) && record.edit.max_images >= 1;
    return {id:record.id, revision:record.revision, generation:generation ? {parameters:generation} : null,
      edit:validEdit ? {parameters:edit,inputs:limits,local_strategy:record.edit.local_strategy,max_images:Math.min(4,record.edit.max_images)} : null,
      unavailable_reason:clean(record.unavailable_reason)};
  }
  /** @param {Model|null|undefined} model @param {Operation} operation */
  const operationProfile = (model, operation) => operation === 'generate' ? model?.generation : model?.edit;
  /** @param {'create'|'edit'} mode @param {Marks|null} info @returns {Operation} */
  const submissionMode = (mode, info) => mode === 'create' ? 'generate' : info?.hasMask ? 'local' : 'whole';
  /** @param {Operation} operation @param {boolean} annotations @param {ReferenceRole[]} roles @returns {Readonly<Intent>} */
  function intentFor(operation, annotations, roles) {
    if (roles.some(role => !ROLES.includes(role))) throw new Error('参考图用途无效。');
    return Object.freeze({operation, has_annotations:operation !== 'generate' && annotations,
      reference_roles:Object.freeze(operation === 'generate' ? [] : [...roles])});
  }
  /** @param {string} prompt @param {Operation} operation @param {boolean} annotations */
  function validatePrompt(prompt, operation, annotations) {
    if (prompt.length > 32000 || prompt.includes('\0')) throw new Error('提示词最多 32000 字，不能含空字符。');
    if (!prompt.trim() && (operation === 'generate' || !annotations)) {
      throw new Error(operation === 'generate' ? '请描述要生成的图片。' : operation === 'local'
        ? '请补充修改说明或指导标注，选区本身不是修改要求。' : '请补充修改说明或指导标注。');
    }
    return prompt; // Preserve whitespace and blank annotations-only prompts; server compiles instructions.
  }
  /** @param {Model|null|undefined} model @param {Operation} operation @param {boolean} annotations */
  const needsGuide = (model, operation, annotations) => operation !== 'generate' &&
    (annotations || (operation === 'local' && model?.edit?.local_strategy === 'reference-only'));
  /** @param {Model|null|undefined} model @param {Operation} operation @param {boolean} annotations */
  const referenceLimit = (model, operation, annotations) => operation === 'generate' ? 0
    : Math.max(0, (model?.edit?.max_images || 0) - 1 - Number(needsGuide(model, operation, annotations)));
  /** @param {Parameters} p @returns {Inputs} */
  function defaultsFor(p) {
    /** @param {string[]} list @param {string} preferred */
    const pick = (list, preferred) => list.includes(preferred) ? preferred : list[0] || '';
    return {size:pick(p.sizes,'auto'),quality:pick(p.qualities,'auto'),format:pick(p.formats,'png'),
      background:pick(p.backgrounds,'auto'),fidelity:pick(p.input_fidelity,'low'),compression:'90',width:'1024',height:'1024'};
  }
  /** @param {Parameters} p @param {Dimensions|null} source @param {string} chosen @returns {string} */
  // Auto chooses only declared output tiers. Custom dimensions are opt-in, not
  // inferred from an upload or from the model family's generic pixel bounds.
  // This describes the engine's letterbox working copy, never resizes the source.
  function workingSize(p, source, chosen) {
    if (chosen !== 'auto' || !source) return chosen;
    const candidates = p.sizes.map(dimension).filter((/** @type {Dimensions|null} */ x) => x !== null).filter(x => x.width > 0 && x.height > 0
      && x.width * x.height <= MAX_OUTPUT_PIXELS);
    if (!candidates.length) return 'auto'; // Explicit auto-only route: never invent an unsupported size.
    /** @param {Dimensions} x */
    const score = x => Math.abs(Math.log((x.width / x.height) / (source.width / source.height))) * 10
      + Math.abs(Math.log((x.width * x.height) / (source.width * source.height)));
    candidates.sort((a, b) => score(a) - score(b));
    return `${candidates[0].width}x${candidates[0].height}`;
  }
  /** Local export geometry is NOT a provider parameter. Do not add this value
   * to the request when the route has no size field (or only supports auto).
   * @param {Readonly<Record<string,string|number>>} parameters @param {Dimensions} source */
  function snapshotSizeFor(parameters,source) {
    if (typeof parameters.size==='string' && parameters.size!=='auto') return parameters.size;
    if (!Number.isInteger(source.width) || !Number.isInteger(source.height) || source.width<1 || source.height<1) {
      throw new Error('主图尺寸无效。');
    }
    const scale=Math.min(1,Math.sqrt(MAX_OUTPUT_PIXELS/(source.width*source.height)),16384/source.width,16384/source.height);
    return `${Math.max(1,Math.floor(source.width*scale))}x${Math.max(1,Math.floor(source.height*scale))}`;
  }
  /** @param {Model|null|undefined} profile @param {Operation} mode @param {Inputs} inputs @param {Dimensions|null} [source] */
  function parametersFor(profile, mode, inputs, source = null) {
    if (!profile) throw new Error('请选择可用模型。');
    const p = operationProfile(profile, mode)?.parameters;
    if (!p) throw new Error(profile.unavailable_reason || '当前操作不可用。');
    const allowed = new Set(p.allowed_fields);
    /** @type {Record<string,string|number>} */
    const result = {};
    if (allowed.has('size')) {
      let size = inputs.size;
      if (size === 'custom') {
        if (!p.custom_size) throw new Error('此模型不支持自定义尺寸。');
        const w=Number(inputs.width),h=Number(inputs.height);
        if (!Number.isInteger(w) || !Number.isInteger(h) || w<16 || h<16 || w>3840 || h>3840
            || w%16 || h%16 || Math.max(w,h)>3*Math.min(w,h) || w*h<655360 || w*h>8294400) {
          throw new Error('尺寸需为 16 的倍数，最长边 3840，比例不超过 3:1，像素为 655360–8294400。');
        }
        size=`${w}x${h}`;
      } else if (!p.sizes.includes(size)) throw new Error('请选择模型允许的尺寸。');
      result.size=mode==='generate' ? size : workingSize(p,source,size);
    }
    for (const [field,value,list] of /** @type {[string,string,string[]][]} */ ([
      ['quality',inputs.quality,p.qualities],['output_format',inputs.format,p.formats],['background',inputs.background,p.backgrounds]])) {
      if (!allowed.has(field)) continue;
      if (!list.includes(value)) throw new Error('请选择模型允许的输出参数。');
      if (field !== 'background' || value !== 'auto') result[field]=value;
    }
    if (result.background==='transparent' && !['png','webp'].includes(String(result.output_format))) {
      throw new Error('透明背景请选择 PNG 或 WebP。');
    }
    if (allowed.has('output_compression') && ['jpeg','webp'].includes(String(result.output_format))) {
      const compression=Number(inputs.compression);
      if (!Number.isInteger(compression) || compression<0 || compression>100) throw new Error('压缩质量需在 0–100 之间。');
      result.output_compression=compression;
    }
    if (mode!=='generate' && allowed.has('input_fidelity')) {
      if (!p.input_fidelity.includes(inputs.fidelity)) throw new Error('请选择模型允许的输入保真度。');
      result.input_fidelity=inputs.fidelity;
    }
    return Object.freeze(result);
  }
  return Object.freeze({MAX_INPUT,MAX_UPLOAD,MAX_MAIN_PIXELS,MAX_TOTAL_PIXELS,MAX_OUTPUT_PIXELS,
    MAX_CANDIDATES,MAX_VERSIONS,REQUEST_TIMEOUT,MIME,ROLES,clean,modelProfile,operationProfile,
    submissionMode,intentFor,validatePrompt,needsGuide,referenceLimit,defaultsFor,workingSize,snapshotSizeFor,parametersFor,
    inputLimits,validateInput});
});
