/* Focused pure domain + serialization fixtures; no network, browser or model. */
const {test}=require('node:test');
const assert=require('node:assert/strict');
const D=require('../src/new_api_cockpit/static/studio-domain.js');
const R=require('../src/new_api_cockpit/static/studio-request.js');
const params=overrides=>({sizes:['auto','1024x1024','1536x1024','1024x1536'],custom_size:false,
  qualities:['auto','low','high'],formats:['png','jpeg','webp'],backgrounds:['auto','opaque','transparent'],input_fidelity:['low','high'],
  allowed_fields:['size','quality','output_format','background','output_compression','input_fidelity'],...overrides});
const record=overrides=>({id:'gpt-image-fixture',revision:'a'.repeat(64),generation:{parameters:params({input_fidelity:[],allowed_fields:['size','quality','output_format','background','output_compression']})},
  edit:{parameters:params(),local_strategy:'native-mask',max_images:4},...overrides});
const blob=(type='image/png')=>new Blob(['fixture'],{type});
const reference=()=>({blob:blob(),width:10,height:10,role:'style'});
const snapshot=()=>({image:blob(),guide:blob(),mask:blob(),working:{width:1024,height:1024},documentId:'doc-fixture',revision:1});
const capture=(intent,strategy='native-mask')=>({tokenId:1,modelId:'gpt-image-fixture',contractRevision:'a'.repeat(64),requestId:'fixture-request-1234',
  prompt:'  原始说明\n ',intent,localStrategy:strategy,parameters:{},snapshot:snapshot(),references:[reference()],source:blob(),versionName:'主图'});

test('canonical catalog rejects legacy guesses and keeps generation/edit separate',()=>{
  assert.equal(D.modelProfile({id:'gpt-image-fixture',capabilities:{edit:true},parameters:params()}),null);
  assert.equal(D.modelProfile({...record(),revision:'stale'}),null);
  const p=D.modelProfile(record({generation:null}));assert.equal(p.generation,null);assert.equal(p.edit.local_strategy,'native-mask');
  const unavailable=D.modelProfile(record({generation:null,edit:null,unavailable_reason:'没有共同能力'}));
  assert.equal(unavailable.unavailable_reason,'没有共同能力');assert.equal(unavailable.edit,null);
});
test('input intent matrix is explicit create/edit; selection alone never supplies instructions',()=>{
  for (const [mode,marks,prompt,expected,valid] of [
    ['create',{},'新图片','generate',true],['create',{hasAnnotations:true},'','generate',false],
    ['edit',{},'改图','whole',true],['edit',{hasAnnotations:true},'','whole',true],
    ['edit',{hasMask:true},'修改这里','local',true],['edit',{hasMask:true,hasAnnotations:true},'','local',true],
    ['edit',{hasMask:true},'','local',false],['edit',{},'','whole',false]]) {
    const operation=D.submissionMode(mode,marks);assert.equal(operation,expected);
    if(valid) assert.equal(D.validatePrompt(prompt,operation,!!marks.hasAnnotations),prompt);
    else assert.throws(()=>D.validatePrompt(prompt,operation,!!marks.hasAnnotations));
  }
  assert.equal(D.validatePrompt('  原样\n ', 'whole',false),'  原样\n ');
  assert.equal(D.validatePrompt(' '.repeat(10),'whole',true),' '.repeat(10));
  assert.equal(D.validatePrompt('字'.repeat(32000),'local',true).length,32000);
  assert.throws(()=>D.validatePrompt('字'.repeat(32001),'whole',true));
});
test('public per-route input limits are bounded and checked against exported files and references',()=>{
  const base=record(),limits=D.inputLimits(undefined);
  const configured=changes=>D.modelProfile(record({edit:{...base.edit,inputs:{...limits,...changes}}}));
  assert.equal(configured({max_pixels:16000001}).edit,null);
  assert.equal(configured({max_bytes:true}).edit,null);
  assert.equal(configured({mime_types:['image/svg+xml']}).edit,null);
  const s=snapshot(),intent=D.intentFor('whole',true,[]);
  for(const [changes,pattern] of [
    [{mime_types:['image/jpeg']},/格式/], [{max_bytes:1},/大小/],
    [{max_pixels:100},/像素/], [{max_dimension:500},/尺寸/],
    [{max_total_pixels:1048576},/总像素/]]) {
    assert.throws(()=>R.validateSnapshot({...s,mask:null},[],configured(changes),intent),pattern);
  }
  assert.throws(()=>R.validateSnapshot({...s,mask:null,working:{width:1024,height:512}},[],
    configured({max_aspect_ratio:1}),intent),/比例/);
  const pngOnly=configured({mime_types:['image/png']});
  assert.throws(()=>R.validateSnapshot({...s,mask:null},[{...reference(),blob:blob('image/jpeg')}],pngOnly,
    D.intentFor('whole',true,['style'])),/格式/);
  assert.equal(pngOnly.edit.inputs.max_pixels,16000000);
});
test('unsupported fields are neither defaulted nor sent, with independent safe export geometry',()=>{
  const empty=params({allowed_fields:[],sizes:[],qualities:[],formats:[],backgrounds:[],input_fidelity:[]});
  const p=D.modelProfile(record({generation:{parameters:empty},edit:{parameters:empty,local_strategy:'reference-only',max_images:2}}));
  assert.ok(p.generation);assert.ok(p.edit);
  for(const operation of ['generate','whole','local']) {
    const values=D.parametersFor(p,operation,D.defaultsFor(empty),{width:4000,height:4000});
    assert.deepEqual(values,{});
    const [w,h]=D.snapshotSizeFor(values,{width:4000,height:4000}).split('x').map(Number);
    assert.ok(w*h<=8500000);assert.equal(w,h);assert.ok(!Object.hasOwn(values,'size'));
  }
  const onlyAuto=D.modelProfile(record({edit:{parameters:params({sizes:['auto']}),local_strategy:'native-mask',max_images:1}}));
  assert.equal(D.parametersFor(onlyAuto,'local',D.defaultsFor(onlyAuto.edit.parameters),{width:4000,height:4000}).size,'auto');
});
test('operation parameters follow their own allowed fields and legal custom bounds',()=>{
  const p=D.modelProfile(record({generation:{parameters:params({allowed_fields:['quality'],qualities:['low'],sizes:[],formats:[],backgrounds:[],input_fidelity:[]})}}));
  assert.deepEqual(D.parametersFor(p,'generate',D.defaultsFor(p.generation.parameters)),{quality:'low'});
  let inputs=D.defaultsFor(p.edit.parameters);
  assert.equal(D.parametersFor(p,'whole',inputs,{width:1800,height:1200}).size,'1536x1024');
  assert.throws(()=>D.parametersFor(p,'whole',{...inputs,format:'jpeg',background:'transparent'}),/透明/);
  const custom=D.modelProfile(record({edit:{parameters:params({sizes:['auto'],custom_size:true}),local_strategy:'native-mask',max_images:4}}));
  inputs=D.defaultsFor(custom.edit.parameters);
  assert.equal(D.parametersFor(custom,'whole',{...inputs,size:'custom'}).size,'1024x1024');
  for (const [width,height] of [['3856','1024'],['1025','1024'],['3200','512'],['256','256'],['3840','3840']]) {
    assert.throws(()=>D.parametersFor(custom,'whole',{...inputs,size:'custom',width,height}),/尺寸/);
  }
  for(const source of [{width:100,height:100},{width:16000,height:1},{width:500,height:1200}]) {
    assert.equal(D.workingSize(custom.edit.parameters,source,'auto'),'auto');
  }
});
test('edit auto chooses a declared output tier, never rounds the uploaded image into a custom size',()=>{
  for(const custom_size of [false,true]) {
    const p=D.modelProfile(record({edit:{parameters:params({custom_size}),local_strategy:'reference-only',max_images:4}}));
    for(const [source,expected] of [
      [{width:837,height:1280},'1024x1536'], [{width:1280,height:837},'1536x1024'],
      [{width:1024,height:1024},'1024x1024'], [{width:100,height:100},'1024x1024'],
      [{width:16000,height:1000},'1536x1024'], [{width:1000,height:16000},'1024x1536']]) {
      for(const operation of ['whole','local']) {
        const parameters=D.parametersFor(p,operation,D.defaultsFor(p.edit.parameters),source);
        assert.equal(parameters.size,expected);
        assert.ok(p.edit.parameters.sizes.includes(parameters.size));
        assert.equal(D.snapshotSizeFor(parameters,source),expected);
        const body=R.makeBody({...capture(D.intentFor(operation,true,[]),'reference-only'),parameters,
          snapshot:{...snapshot(),mask:operation==='local'?blob():null}});
        assert.equal(JSON.parse(body.get('parameters')).size,expected);
        assert.equal(body.has('mask'),false);
      }
    }
    assert.equal(D.parametersFor(p,'generate',D.defaultsFor(p.generation.parameters)).size,'auto');
  }
});
test('fixed-tier routes reject explicit arbitrary sizes, while verified custom remains opt-in',()=>{
  const p=D.modelProfile(record());const inputs=D.defaultsFor(p.edit.parameters);
  assert.throws(()=>D.parametersFor(p,'local',{...inputs,size:'848x1296'}),/允许的尺寸/);
  assert.throws(()=>D.parametersFor(p,'local',{...inputs,size:'custom',width:'848',height:'1296'}),/自定义尺寸/);
  const custom=D.modelProfile(record({edit:{parameters:params({custom_size:true}),local_strategy:'reference-only',max_images:4}}));
  assert.equal(D.parametersFor(custom,'local',{...inputs,size:'custom',width:'848',height:'1296'}).size,'848x1296');
});
test('guide budget reserves clean main, reference-only selection and annotations exactly once',()=>{
  const native=D.modelProfile(record()),reference=D.modelProfile(record({edit:{parameters:params(),local_strategy:'reference-only',max_images:4}}));
  assert.equal(D.referenceLimit(native,'whole',false),3);assert.equal(D.referenceLimit(native,'local',false),3);
  assert.equal(D.referenceLimit(native,'local',true),2);assert.equal(D.referenceLimit(reference,'local',false),2);
  assert.equal(D.referenceLimit(reference,'local',true),2);assert.equal(D.referenceLimit(reference,'generate',true),0);
});
test('intent roles freeze at capture and serialization keeps raw prompt and canonical endpoint semantics',()=>{
  const roles=['style'],intent=D.intentFor('local',true,roles);roles[0]='composition';assert.equal(intent.reference_roles[0],'style');
  assert.ok(Object.isFrozen(intent));assert.ok(Object.isFrozen(intent.reference_roles));
  const body=R.makeBody(capture(intent));
  for(const key of ['token_id','model','prompt','parameters','request_id','contract_revision','intent','mask']) assert.equal(body.getAll(key).length,1);
  assert.equal(body.get('prompt'),'  原始说明\n ');assert.deepEqual(JSON.parse(body.get('intent')),intent);
  assert.deepEqual(body.getAll('image[]').map(x=>x.name),['primary.png','guide.png','reference-1.png']);
  assert.equal(R.makeBody(capture(intent,'reference-only')).has('mask'),false);
  assert.equal(R.makeBody(capture(D.intentFor('whole',true,['reference']))).has('mask'),false);
  const json=R.makeBody(capture(D.intentFor('generate',false,[])));
  assert.equal(typeof json.intent,'object');assert.equal(json.intent.operation,'generate');assert.equal(json.prompt,'  原始说明\n ');
});
test('snapshot validation rejects missing guide, wrong mask strategy and resource overflow',()=>{
  const native=D.modelProfile(record()),s=snapshot(),intent=D.intentFor('local',true,['style']);
  R.validateSnapshot(s,[reference()],native,intent);
  assert.throws(()=>R.validateSnapshot({...s,guide:null},[reference()],native,intent),/指导图/);
  assert.throws(()=>R.validateSnapshot({...s,mask:null},[reference()],native,intent),/选区/);
  assert.throws(()=>R.validateSnapshot(s,[reference(),reference(),reference()],native,intent),/过多/);
  assert.throws(()=>R.validateSnapshot({...s,image:{type:'image/png',size:9*1024*1024}},[],native,intent),/8 MiB/);
  assert.throws(()=>R.validateSnapshot({...s,mask:{type:'image/png',size:4000000}},[],native,intent),/4 MB/);
  const ref=D.modelProfile(record({edit:{parameters:params(),local_strategy:'reference-only',max_images:4}}));
  R.validateSnapshot({...s,mask:{type:'image/png',size:4000000}},[],ref,D.intentFor('local',false,[]));
  assert.throws(()=>R.validateSnapshot({...s,working:{width:4096,height:4096}},[],ref,intent),/像素/);
});
