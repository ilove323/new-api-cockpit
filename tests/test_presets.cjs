/* Calendar shortcut boundaries only; no browser, network or database. */
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const {spawnSync}=require('node:child_process');
const root=path.join(__dirname,'../src/new_api_cockpit');
const source=fs.readFileSync(path.join(root,'static/presets.js'),'utf8');
const context=vm.createContext({});
vm.runInContext(source,context);
const range=(preset,now)=>JSON.parse(vm.runInContext(`JSON.stringify(presetRange(${JSON.stringify(preset)},${Date.parse(now)}))`,context));
const cases=[
  ['2026-10-26T18:20:54.987Z','2026-10-26','2026-08-01','2026-10-27T02:20:54'],
  ['2026-10-26T15:59:59.999Z','2026-10-25','2026-08-01','2026-10-26T23:59:59'],
  ['2026-10-26T16:00:00.000Z','2026-10-26','2026-08-01','2026-10-27T00:00:00'],
  ['2025-12-31T16:00:00.000Z','2025-12-31','2025-11-01','2026-01-01T00:00:00'],
  ['2024-02-29T16:00:00.000Z','2024-02-29','2024-01-01','2024-03-01T00:00:00'],
  ['2026-02-28T16:00:00.000Z','2026-02-28','2026-01-01','2026-03-01T00:00:00'],
  ['2026-05-31T04:05:06.000Z','2026-05-30','2026-03-01','2026-05-31T12:05:06'],
  ['2026-10-31T15:59:59.999Z','2026-10-30','2026-08-01','2026-10-31T23:59:59'],
  ['2026-10-31T16:00:00.000Z','2026-10-31','2026-09-01','2026-11-01T00:00:00'],
  ['2026-11-15T04:05:06.000Z','2026-11-14','2026-09-01','2026-11-15T12:05:06'],
];

test('shortcut order puts three months before last month and yesterday between seven and one day',()=>{
  const html=fs.readFileSync(path.join(root,'templates/index.html'),'utf8');
  assert.deepEqual([...html.matchAll(/data-preset="([^"]+)">([^<]+)</g)].map(m=>[m[1],m[2]]),[
    ['three-months','三个月'],['month','上个月'],['current-month','本月'],
    ['30','近30天'],['7','近7天'],['yesterday','昨天'],['1','近1天'],
  ]);
});

test('yesterday is the previous Beijing calendar day, including midnight, year and leap boundaries',()=>{
  for(const [now,day] of cases)assert.deepEqual(range('yesterday',now),{
    start:day+'T00:00:00',end:day+'T23:59:59',
  },now);
});

test('three months includes this month and starts two calendar months earlier, ending now',()=>{
  for(const [now,,start,end] of cases)assert.deepEqual(range('three-months',now),{
    start:start+'T00:00:00',end,
  },now);
});

test('existing month and rolling shortcuts retain their original ranges and second precision',()=>{
  const now='2026-10-26T18:20:54.987Z',end='2026-10-27T02:20:54';
  assert.deepEqual(range('month',now),{start:'2026-09-01T00:00:00',end:'2026-09-30T23:59:59'});
  assert.deepEqual(range('current-month',now),{start:'2026-10-01T00:00:00',end});
  for(const [days,start] of [['30','2026-09-27T02:20:54'],['7','2026-10-20T02:20:54'],['1','2026-10-26T02:20:54']]){
    assert.deepEqual(range(days,now),{start,end});
  }
});

test('calendar shortcuts are independent of the browser timezone and daylight saving time',()=>{
  const expected=cases.map(([now])=>[range('yesterday',now),range('three-months',now)]);
  const script=source+`\nprocess.stdout.write(JSON.stringify(${JSON.stringify(cases)}.map(([now])=>['yesterday','three-months'].map(p=>presetRange(p,Date.parse(now))))));`;
  for(const TZ of ['UTC','Asia/Shanghai','America/Los_Angeles','Pacific/Auckland']){
    const result=spawnSync(process.execPath,['-e',script],{encoding:'utf8',env:{...process.env,TZ},timeout:10000});
    assert.equal(result.status,0,result.stderr);assert.deepEqual(JSON.parse(result.stdout),expected,TZ);
  }
});
