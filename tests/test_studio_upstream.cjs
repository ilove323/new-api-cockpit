'use strict';
/* Source-backed offline tests; no network, account, browser or paid model. */
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const vm = require('node:vm');
const root = path.resolve(__dirname, '..'), directory = path.join(root, 'src/new_api_cockpit/static');
const source = fs.readFileSync(path.join(directory, 'studio-upstream.js'), 'utf8');
const U = require(path.join(directory, 'studio-upstream.js'));
const S = U.sketch;
const ctx = {save() {}, restore() {}, measureText: text => ({width: text.length * 10})};
const shape = {id:'rect',type:'shape',shape:'rect',color:'#ff0000',width:4,start:{x:10,y:20},end:{x:50,y:70},
  erase:[{width:6,points:[{x:11,y:22}]}]};
test('all original source files and licenses match the fixed repository manifest', () => {
  const vendor = path.join(root, 'frontend/studio/vendor');
  const entries = JSON.parse(fs.readFileSync(path.join(vendor, 'sources.json'), 'utf8'));
  assert.equal(entries.length, 6);
  for (const entry of entries) {
    assert.match(entry.commit, /^[a-f0-9]{40}$/);
    assert.equal(crypto.createHash('sha256').update(fs.readFileSync(path.join(vendor, entry.file))).digest('hex'), entry.sha256);
    assert(['MIT','Apache-2.0'].includes(entry.license));
  }
  assert.equal(fs.readFileSync(path.join(vendor, 'gpt-image-playground/LICENSE'), 'utf8'), fs.readFileSync(path.join(directory, 'GPT-IMAGE-PLAYGROUND-LICENSE'), 'utf8'));
  assert.equal(fs.readFileSync(path.join(vendor, 'invokeai/LICENSE'), 'utf8'), fs.readFileSync(path.join(directory, 'INVOKEAI-LICENSE'), 'utf8'));
  assert.equal(fs.readFileSync(path.join(vendor, 'invokeai/NOTICE'), 'utf8'), fs.readFileSync(path.join(directory, 'INVOKEAI-NOTICE'), 'utf8'));
  const html = fs.readFileSync(path.join(root, 'src/new_api_cockpit/templates/image_studio/index.html'), 'utf8');
  assert(html.indexOf('studio-upstream.js') < html.indexOf('studio-canvas.js'));
  assert(html.indexOf('studio-lightbox.js') < html.indexOf('static/studio.js'));
  assert.match(source, /Copyright \(c\) 2026 CookSleep/);
});
test('original shape geometry, hit tests and handles drive selection and resizing', () => {
  assert.deepEqual(S.getElementBounds(shape,ctx),{x:8,y:18,width:44,height:54});
  assert.equal(S.hitTestElement(shape,{x:30,y:40},0,ctx,'select'),true);
  assert.equal(S.hitTestElement(shape,{x:30,y:40},0,ctx,'erase'),false);
  assert.equal(S.findTopElementAt([shape,{...shape,id:'top'}],{x:10,y:20},0,ctx).id,'top');
  assert.equal(S.SKETCH_HANDLES.length,8);
  const from = S.getTransformBounds(shape,ctx), to = S.resizeBounds(from,'se',{x:90,y:120});
  assert.deepEqual(to,{x:10,y:20,width:80,height:100});
  const result = S.transformElement(shape,from,to,'se');
  assert.deepEqual(result.end,{x:90,y:120});
  assert.deepEqual(result.erase[0].points[0],{x:12,y:24});
  assert.deepEqual(shape.end,{x:50,y:70},'original object is not mutated');
  const moved = S.moveElement(result,3,4);
  assert.deepEqual(moved.erase[0].points[0],{x:15,y:28});
});
test('text uses upstream multiline metrics and bounded proportional resize', () => {
  const text = {id:'text',type:'text',x:10,y:20,text:'ab\nc',fontSize:32,color:'#ff0000',width:4};
  assert.deepEqual(S.measureTextElement(ctx,text),{width:20,height:80});
  const from = S.getTransformBounds(text,ctx);
  const result = S.transformElement(text,from,{...from,width:40,height:160},'se');
  assert.equal(result.fontSize,64); assert.equal(result.x,10); assert.equal(result.y,20);
  assert.equal(S.transformElement(text,from,{...from,height:1},'se').fontSize,24);
});
test('sparse erase samples cover crossed objects and resource limits reject before allocation', () => {
  const stroke = U.sampleEraseStroke({width:3,points:[{x:0,y:20},{x:100,y:20}]});
  assert.equal(stroke.points.length,101);
  assert(stroke.points.some(p => S.hitTestElement(shape,p,1.5,ctx,'erase')));
  assert.throws(() => U.sampleEraseStroke({width:1,points:[{x:0,y:0},{x:20000,y:0}]}), {code:'COMMAND_LIMIT'});
});
test('adapted InvokeAI compositor preserves layer order and per-layer operation precedence', () => {
  const drawn=[],context={drawImage(image){drawn.push([image,this.globalCompositeOperation]);}},canvas={getContext:()=>context};
  const result=U.compositeCanvas([
    {state:{},getCanvas:()=> 'base'},
    {state:{globalCompositeOperation:'source-over'},getCanvas:()=> 'region'},
    {state:{},getCanvas:()=> 'guide'},
  ],{width:10,height:20},(w,h)=>{assert.equal(w,10);assert.equal(h,20);return canvas;},{globalCompositeOperation:'multiply'});
  assert.equal(result,canvas);assert.equal(context.imageSmoothingEnabled,false);
  assert.deepEqual(drawn,[['base','multiply'],['region','source-over'],['guide','multiply']]);
});

function lightboxFixture() {
  class Pane {
    constructor() {this.style={};this.events={};this.captures=new Set();}
    getBoundingClientRect(){return {left:10,top:20,width:400,height:300};}
    addEventListener(name,fn){(this.events[name]??=[]).push(fn);}
    removeEventListener(name,fn){this.events[name]=this.events[name].filter(f=>f!==fn);}
    setPointerCapture(id){this.captures.add(id);} hasPointerCapture(id){return this.captures.has(id);}
    releasePointerCapture(id){this.captures.delete(id);}
    fire(name,detail={}) {const e={clientX:210,clientY:120,pointerId:1,button:0,preventDefault(){this.defaultPrevented=true;},...detail};
      for(const fn of this.events[name]??[])fn(e);return e;}
  }
  const context=vm.createContext({}, {codeGeneration:{strings:false,wasm:false}});
  vm.runInContext(source,context); vm.runInContext(fs.readFileSync(path.join(directory,'studio-lightbox.js'),'utf8'),context);
  const host=new Pane(),image=new Pane(),other=new Pane(),otherImage=new Pane(),zooms=[],navigated=[];
  const api=context.CockpitStudioLightbox.create([{host,image},{host:other,image:otherImage}],{onZoom:z=>zooms.push(z),onNavigate:d=>navigated.push(d)});
  return {api,host,image,other,otherImage,zooms,navigated};
}
test('lightbox wheel anchors, bounded pan, compare isolation, reset and keyboard navigation', () => {
  const f=lightboxFixture();f.api.setActive(true);
  f.host.fire('wheel',{deltaY:-100});assert.equal(f.zooms.at(-1),1.15);
  assert.match(f.image.style.transform,/translate\(-[\d.]+px, -[\d.]+px\) scale\(1.15\)/);
  assert.equal(f.otherImage.style.transform,'translate(0px, 0px) scale(1)');
  f.host.fire('pointerdown');f.host.fire('pointermove',{clientX:10000,clientY:10000});f.host.fire('pointerup');
  assert.equal(f.image.style.transform,'translate(0px, 0px) scale(1.15)');
  f.api.keyDown({key:'ArrowRight',preventDefault(){}});assert.deepEqual(f.navigated,[1]);
  f.api.keyDown({key:'ArrowLeft',target:{tagName:'INPUT'},preventDefault(){}});assert.deepEqual(f.navigated,[1]);
  f.api.reset();assert.equal(f.zooms.at(-1),1);
  f.api.setActive(false);f.host.fire('wheel',{deltaY:-100});assert.equal(f.zooms.at(-1),1);
  f.api.destroy();assert(Object.values(f.host.events).every(v=>v.length===0));
});
test('lightbox pinch cancellation releases capture and never turns a remaining finger into a pan', () => {
  const f=lightboxFixture();f.api.setActive(true);
  f.host.fire('pointerdown',{pointerId:1,clientX:110});f.host.fire('pointerdown',{pointerId:2,clientX:210});
  f.host.fire('pointermove',{pointerId:2,clientX:310});assert.equal(f.zooms.at(-1),2);
  const before=f.image.style.transform;
  f.host.fire('pointercancel',{pointerId:2});f.host.fire('pointermove',{pointerId:1,clientX:200});
  assert.equal(f.image.style.transform,before);
  f.api.setActive(false);assert.equal(f.host.captures.size,0);
});
