/* Full-page size regression, offline by default.
 * NODE_PATH=<playwright modules> STUDIO_BROWSER_EXECUTABLE=<chromium> node tests/browser/studio-size-regression.cjs
 * A paid deployment relay is opt-in, external to this repository, and never receives browser credentials.
 */
'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const http = require('node:http');
const {spawnSync} = require('node:child_process');
const {chromium} = require('playwright');

const root = path.resolve(__dirname, '../..');
const temporary = fs.mkdtempSync(path.join(os.tmpdir(), 'cockpit-studio-size-'));
const paidRelay = process.env.STUDIO_PAID_RELAY;
if (paidRelay && process.env.STUDIO_ALLOW_PAID !== '1') throw new Error('Paid relay requires explicit STUDIO_ALLOW_PAID=1');
const relay = paidRelay ? require(path.resolve(paidRelay)) : null;
const parameters = {sizes:['auto','1024x1024','1536x1024','1024x1536'],custom_size:false,
  qualities:['auto','low','medium','high'],formats:['png'],backgrounds:[],input_fidelity:[],
  allowed_fields:['size','quality','output_format'],max_images:4};
const fixtureModel = {id:'gpt-image-2.5-flare',revision:'a'.repeat(64),
  generation:{parameters:{...parameters,max_images:0}},
  edit:{parameters,local_strategy:'reference-only',max_images:4}};

async function main() {
  const rendered = spawnSync(process.env.STUDIO_PYTHON || path.join(root,'.venv/bin/python'), ['-c',
    'from flask import render_template,g\nfrom new_api_cockpit.app import app\n'
    + 'with app.test_request_context("/cockpit/image-studio"):\n'
    + ' g.current_user={"id":1,"username":"fixture","role":1,"session_id":"fixture-session","expires_at":4102444800}\n'
    + ' print(render_template("image_studio/index.html",site_name="Studio regression"))'],
    {cwd:root,env:{...process.env,PYTHONPATH:path.join(root,'src')},encoding:'utf8'});
  assert.equal(rendered.status,0,'Fixture template render failed');
  const catalog = relay ? await relay({kind:'catalog'}) : {
    token_id:1,tokens:[{id:1,name:'Fixture GPT KEY',group:'GPT',key_masked:'sk-****fixture',usable:true}],models:[fixtureModel]};
  const model = catalog.models.find(item => item.id === fixtureModel.id);
  assert(model?.edit && model.edit.local_strategy === 'reference-only');
  assert.equal(model.edit.parameters.custom_size,false);
  const server = http.createServer((req,res) => {
    if (req.url === '/cockpit/image-studio/') {res.setHeader('Content-Type','text/html');res.end(rendered.stdout);return;}
    const match = /^\/cockpit\/static\/([a-zA-Z0-9_.-]+)(?:\?.*)?$/.exec(req.url);
    if (!match) {res.writeHead(404);res.end();return;}
    const file = path.join(root,'src/new_api_cockpit/static',match[1]);
    if (!fs.existsSync(file)) {res.writeHead(404);res.end();return;}
    res.setHeader('Content-Type',file.endsWith('.js')?'text/javascript':file.endsWith('.css')?'text/css':'image/svg+xml');
    res.end(fs.readFileSync(file));
  });
  await new Promise(resolve => server.listen(0,'127.0.0.1',resolve));
  const browser = await chromium.launch({headless:true,
    ...(process.env.STUDIO_BROWSER_EXECUTABLE ? {executablePath:process.env.STUDIO_BROWSER_EXECUTABLE} : {})});
  try {
    const results = [];
    const artifactsDir=process.env.STUDIO_ARTIFACT_DIR;
    if(artifactsDir)fs.mkdirSync(artifactsDir,{recursive:true});
    for (const testCase of [{name:'portrait',width:837,height:1280,size:'1024x1536'},
      {name:'landscape',width:1280,height:837,size:'1536x1024'}]) {
      const page = await browser.newPage({viewport:{width:1440,height:960}});
      await page.addInitScript(() => {
        Object.defineProperty(window,'CockpitImageCanvas',{configurable:true,set(api) {
          Object.defineProperty(window,'CockpitImageCanvas',{configurable:true,writable:true,value:{...api,create(host,options) {
            const editor=api.create(host,options);window.__studioEditor=editor;return editor;
          }}});
        }});
      });
      const errors = [];page.on('pageerror',error => errors.push(error.message));
      const artifacts = await page.evaluate(({width,height,size}) => {
        const canvas=document.createElement('canvas');canvas.width=width;canvas.height=height;
        const ctx=canvas.getContext('2d');ctx.fillStyle='#ffffff';ctx.fillRect(0,0,width,height);
        ctx.fillStyle='#2475ef';ctx.fillRect(width*.4,height*.4,width*.2,height*.2);
        const source=canvas.toDataURL('image/png').split(',')[1];
        const [w,h]=size.split('x').map(Number);canvas.width=w;canvas.height=h;
        ctx.fillStyle='#22c55e';ctx.fillRect(0,0,w,h);
        return {source,output:canvas.toDataURL('image/png').split(',')[1]};
      },testCase);
      const sourceFile=path.join(temporary,testCase.name+'.png');fs.writeFileSync(sourceFile,Buffer.from(artifacts.source,'base64'));
      let capture = null, reply = null;
      await page.route('**/cockpit/api/image-studio/**',async route => {
        const request=route.request(),endpoint=new URL(request.url()).pathname;
        if (endpoint.endsWith('/tokens')) {
          await route.fulfill({json:{tokens:catalog.tokens,token_manager_path:'/keys'}});return;
        }
        if (endpoint.endsWith('/models')) {await route.fulfill({json:{models:catalog.models}});return;}
        assert.equal(capture,null,'Only one paid dispatch per case');
        assert(endpoint.endsWith('/edits'));
        const raw=request.postDataBuffer(),contentType=request.headers()['content-type'];
        const form=await new Request('http://fixture/',{method:'POST',headers:{'Content-Type':contentType},body:raw}).formData();
        const parameters=JSON.parse(form.get('parameters')),intent=JSON.parse(form.get('intent'));
        assert.equal(parameters.size,testCase.size);assert.equal(form.get('prompt'),'');
        assert.equal(intent.operation,'local');assert.equal(intent.has_annotations,true);assert.equal(form.has('mask'),false);
        const files=form.getAll('image[]');assert.equal(files.length,2);
        const dimensions=[];
        for (const file of files) {
          const bytes=Buffer.from(await file.arrayBuffer());
          assert.equal(bytes.subarray(1,4).toString(),'PNG');
          dimensions.push([bytes.readUInt32BE(16),bytes.readUInt32BE(20)]);
        }
        const expected=testCase.size.split('x').map(Number);
        assert.deepEqual(dimensions,[expected,expected]);assert.equal(files[1].name,'guide.png');
        capture={size:parameters.size,request_id:form.get('request_id'),intent,dimensions};
        if (relay) reply=await relay({kind:'request',endpoint,content_type:contentType,body_b64:raw.toString('base64')});
        else reply={status:200,body:{model:model.id,request_id:form.get('request_id'),duration_seconds:.1,
          images:[{b64_json:artifacts.output,mime:'image/png',width:expected[0],height:expected[1]}]}};
        assert.equal(reply.status,200,JSON.stringify({status:reply.status,error:reply.body?.error,code:reply.body?.code}));
        const result=reply.body.images[0];assert.deepEqual([result.width,result.height],expected);
        await route.fulfill({status:reply.status,json:reply.body});
      });
      await page.goto(`http://127.0.0.1:${server.address().port}/cockpit/image-studio/`);
      await page.selectOption('#studio-token',String(catalog.token_id));
      await page.selectOption('#studio-model',model.id);
      await page.selectOption('#studio-quality','low');
      await page.setInputFiles('#studio-image-input',sourceFile);
      await page.waitForFunction(() => document.getElementById('studio-document-info').textContent.includes('有主图')
        || document.getElementById('studio-document-info').textContent.includes('无选区'));
      assert.equal(await page.inputValue('#studio-size'),'auto');
      const board=await page.locator('#studio-canvas').boundingBox();
      const cx=board.x+board.width/2,cy=board.y+board.height/2;
      const scale=Math.min((board.width-32)/testCase.width,(board.height-32)/testCase.height);
      if (testCase.name === 'portrait') {
        // Actual upstream drawing and DOM text editing; no model calls.
        await page.click('[data-tool="text"]');
        await page.mouse.click(cx-35,cy-35);
        const inline=page.locator('.studio-inline-text');await inline.waitFor();
        await inline.fill('把这里改为绿色\n保留其他部分');
        assert.equal(await page.locator('#studio-submit').isDisabled(),false,'Typing a guide should enable submission before blur');
        await inline.press('Control+Enter');await inline.waitFor({state:'detached'});
        assert.equal(await page.evaluate(()=>window.__studioEditor.state().annotationCount),1);
        await page.click('[data-tool="select"]');await page.mouse.dblclick(cx-30,cy-30);
        await inline.waitFor();await inline.fill('原位编辑');await inline.press('Control+Enter');
        assert.equal(await page.evaluate(()=>window.__studioEditor.state().selected.text),'原位编辑');
        await page.click('#studio-delete-guide');
        assert.equal(await page.evaluate(()=>window.__studioEditor.state().hasAnnotations),false);
        await page.click('[data-tool="box"]');
        await page.mouse.move(cx-30,cy-30);await page.mouse.down();await page.mouse.move(cx+30,cy+30);await page.mouse.up();
        await page.click('[data-tool="select"]');await page.mouse.click(cx,cy);
        const resize=await page.evaluate(()=>{
          const stage=Konva.stages[0],group=stage.children[0].children[0],record=window.__studioEditor.state().selected;
          const ctx=document.createElement('canvas').getContext('2d'),bounds=CockpitStudioUpstream.sketch.getElementBounds(record,ctx);
          const p=group.getAbsoluteTransform().point(CockpitStudioUpstream.sketch.getHandlePoint(bounds,'se'));
          const rect=stage.content.getBoundingClientRect();return {x:rect.left+p.x,y:rect.top+p.y,end:record.end};
        });
        await page.mouse.move(resize.x,resize.y);await page.mouse.down();await page.mouse.move(resize.x+25,resize.y+20);await page.mouse.up();
        const resized=await page.evaluate(()=>window.__studioEditor.state().selected.end);
        assert(resized.x>resize.end.x && resized.y>resize.end.y);
        await page.click('#studio-undo');await page.click('#studio-clear-guides');
        assert.equal(await page.evaluate(()=>window.__studioEditor.state().annotationCount),0);
        await page.click('[data-tool="pen"]');await page.mouse.move(cx-20,cy);await page.mouse.down();
        await page.mouse.move(cx+20,cy,{steps:8});await page.mouse.up();
        await page.click('[data-tool="guide-eraser"]');await page.fill('#studio-brush-size','512');
        await page.locator('#studio-brush-size').dispatchEvent('input');await page.mouse.click(cx,cy);
        assert.equal(await page.evaluate(()=>window.__studioEditor.state().hasAnnotations),false);
        assert.equal(await page.evaluate(()=>window.__studioEditor.state().hasMask),false);
      }
      const selection={left:testCase.width/2-45/scale,right:testCase.width/2+45/scale,
        top:testCase.height/2-45/scale,bottom:testCase.height/2+45/scale};
      await page.click('[data-tool="rectangle"]');
      await page.mouse.move(cx-45,cy-45);await page.mouse.down();
      await page.mouse.move(cx+45,cy+45,{steps:5});await page.mouse.up();
      assert.equal(await page.locator('#studio-submit').isDisabled(),true,'Selection alone needs an instruction');
      await page.click('[data-tool="text"]');
      assert.equal(await page.locator('[data-tool="text"]').getAttribute('aria-pressed'),'true');
      await page.fill('#studio-annotation-text','Change this square to green');
      await page.mouse.click(cx-40,cy-30);
      await page.waitForFunction(() => !document.getElementById('studio-submit').disabled);
      assert.equal(await page.inputValue('#studio-prompt'),'');
      assert.match(await page.locator('#studio-submit-label').innerText(),/局部/);
      assert.equal(await page.locator('[data-tool="text"]').getAttribute('aria-pressed'),'true');
      assert.equal(await page.evaluate(()=>window.__studioEditor.state().tool),'text');
      if(artifactsDir)await page.screenshot({path:path.join(artifactsDir,testCase.name+'-editor.png')});
      await page.click('#studio-submit');
      await page.locator('#studio-candidates .studio-candidate-preview img').waitFor({timeout:350000});
      const pixels=await page.locator('#studio-candidates .studio-candidate-preview img').evaluate(async (img,{source,selection}) => {
        async function decode(blob) {const bitmap=await createImageBitmap(blob),c=document.createElement('canvas');
          c.width=bitmap.width;c.height=bitmap.height;const ctx=c.getContext('2d');ctx.drawImage(bitmap,0,0);
          bitmap.close();return {width:c.width,height:c.height,rgba:ctx.getImageData(0,0,c.width,c.height).data};}
        const original=await decode(await (await fetch('data:image/png;base64,'+source)).blob());
        const composite=await decode(await (await fetch(img.src)).blob());
        if(original.width!==composite.width || original.height!==composite.height) return {width:composite.width,height:composite.height,sizeMismatch:true};
        let unchangedOutside=0,changedOutside=0,changedInside=0;
        for(let y=0;y<original.height;y++)for(let x=0;x<original.width;x++) {
          const i=(y*original.width+x)*4;
          const same=[0,1,2,3].every(k=>original.rgba[i+k]===composite.rgba[i+k]);
          // Exclude a two-pixel rasterization boundary, check every other exterior pixel.
          const outside=x<Math.floor(selection.left)-2 || x>Math.ceil(selection.right)+2
            || y<Math.floor(selection.top)-2 || y>Math.ceil(selection.bottom)+2;
          if(outside) {if(same) unchangedOutside++;else changedOutside++;}else if(!same) changedInside++;
        }
        return {width:composite.width,height:composite.height,unchangedOutside,changedOutside,changedInside};
      },{...artifacts,selection});
      assert.deepEqual([pixels.width,pixels.height],[testCase.width,testCase.height]);
      assert.equal(pixels.changedOutside,0,'Outside selection must remain pixel-identical');
      assert(pixels.unchangedOutside>0);assert(pixels.changedInside>0,'Selection should receive returned pixels');
      // Exercise the actual native lightbox, without another model request.
      const infoBefore=await page.locator('#studio-document-info').innerText();
      const versionBefore=await page.inputValue('#studio-version');
      const previewBoard=await page.locator('#studio-canvas').boundingBox();
      const candidateImage=await page.locator('#studio-candidates .studio-candidate-preview img').getAttribute('src');
      const preview=page.locator('#studio-preview'),thumbnail=page.locator('#studio-candidates .studio-candidate-preview');
      await thumbnail.click();
      assert.equal(await preview.evaluate(dialog=>dialog.open && dialog.matches(':modal')),true);
      assert.equal(await preview.evaluate(dialog=>!!dialog.closest('main')),false);
      const expanded=await preview.boundingBox();assert(expanded.width>previewBoard.width);assert(expanded.height>previewBoard.height);
      assert.equal(await page.locator('#studio-preview-result').getAttribute('src'),candidateImage);
      assert.equal(await page.isChecked('#studio-compare'),false);
      assert.equal(await page.locator('#studio-preview-source-panel').isVisible(),false);
      const resultViewport=page.locator('#studio-preview-result-viewport');
      const target=await resultViewport.boundingBox();await page.mouse.move(target.x+target.width/2,target.y+target.height/2);
      await page.mouse.wheel(0,-100);await page.waitForFunction(()=>document.getElementById('studio-preview-zoom').textContent==='115%');
      assert.match(await page.locator('#studio-preview-result').evaluate(img=>img.style.transform),/scale\(1.15\)/);
      await page.click('#studio-preview-fit');assert.equal(await page.locator('#studio-preview-zoom').innerText(),'100%');
      const enlarged=await page.locator('#studio-preview-result').boundingBox();assert(enlarged.width>300 && enlarged.height>300);
      assert.deepEqual(await page.locator('#studio-canvas').boundingBox(),previewBoard,'Modal must not resize the canvas');
      if(artifactsDir)await page.screenshot({path:path.join(artifactsDir,testCase.name+'-preview.png')});
      await page.evaluate(()=>document.getElementById('studio-prompt').focus());
      assert.equal(await preview.evaluate(dialog=>dialog.contains(document.activeElement)),true,'Modal keeps background controls inert');
      for(let i=0;i<12;i++) {
        await page.keyboard.press('Tab');
        assert.equal(await page.evaluate(()=>document.getElementById('studio-page').contains(document.activeElement)),false);
      }
      await page.keyboard.press('Escape');await preview.waitFor({state:'hidden'});
      assert.equal(await thumbnail.evaluate(element=>document.activeElement===element),true);
      assert.deepEqual(await page.locator('#studio-canvas').boundingBox(),previewBoard);
      const previewButton=page.locator('#studio-candidates .studio-candidate-actions').getByRole('button',{name:'预览',exact:true});
      await previewButton.click();await page.locator('#studio-preview-result').click();
      assert.equal(await preview.evaluate(dialog=>dialog.open),true,'Image clicks do not dismiss');
      await page.mouse.move(expanded.x+expanded.width/2,expanded.y+expanded.height/2);await page.mouse.down();
      await page.mouse.move(2,2,{steps:3});await page.mouse.up();
      assert.equal(await preview.evaluate(dialog=>dialog.open),true,'Dragging out of an image does not dismiss');
      await page.mouse.click(2,2);await preview.waitFor({state:'hidden'});
      assert.equal(await previewButton.evaluate(element=>document.activeElement===element),true);
      await thumbnail.click();await page.check('#studio-compare');
      assert.equal(await page.locator('#studio-preview-source-panel').isVisible(),true);
      await page.click('#studio-show-original');await page.click('#studio-preview-close');
      await preview.waitFor({state:'hidden'});
      assert.equal(await page.locator('#studio-candidates .studio-candidate-preview').evaluate(element=>document.activeElement===element),true);
      assert.equal(await page.locator('#studio-document-info').innerText(),infoBefore);
      assert.equal(await page.inputValue('#studio-version'),versionBefore);
      assert.deepEqual(await page.locator('#studio-canvas').boundingBox(),previewBoard);
      if(testCase.name==='landscape') {
        await page.setViewportSize({width:390,height:844});
        await page.locator('#studio-candidates .studio-candidate-preview').click();
        const mobile=await preview.boundingBox();assert(mobile.x>=0 && mobile.y>=0 && mobile.width<=390 && mobile.height<=844);
        for(const id of ['studio-preview-close','studio-preview-download','studio-preview-adopt','studio-preview-continue']) {
          const rect=await page.locator('#'+id).boundingBox();assert(rect && rect.x>=0 && rect.y>=0 && rect.x+rect.width<=390 && rect.y+rect.height<=844,id);
        }
        if(artifactsDir)await page.screenshot({path:path.join(artifactsDir,'mobile-preview.png')});
        const imageBox=await page.locator('#studio-preview-result').boundingBox();assert(imageBox.width>200 && imageBox.height>200);
        await page.check('#studio-compare');await page.locator('#studio-preview-source').waitFor();
        assert.equal(await preview.evaluate(dialog=>dialog.open),true);
        await page.keyboard.press('Escape');await preview.waitFor({state:'hidden'});
      }
      assert.deepEqual(errors,[]);assert(capture);
      results.push({case:testCase.name,paid:!!relay,source:[testCase.width,testCase.height],...capture,composite:pixels,modal_preview:true,source_editor:testCase.name==='portrait',zoom_preview:true});
      console.log(JSON.stringify(results.at(-1)));await page.close();
    }
    if(process.env.STUDIO_REGRESSION_OUTPUT) fs.writeFileSync(process.env.STUDIO_REGRESSION_OUTPUT,JSON.stringify(results,null,2));
  } finally {await browser.close();await new Promise(resolve=>server.close(resolve));}
}
main().catch(error => {console.error(error);process.exitCode=1;}).finally(()=>fs.rmSync(temporary,{recursive:true,force:true}));
