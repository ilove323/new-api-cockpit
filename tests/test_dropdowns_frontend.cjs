const test=require('node:test');
const assert=require('node:assert/strict');
const {install}=require('../src/new_api_cockpit/static/dropdowns.js');

function harness(){
  const listeners={};
  const picker=name=>{
    const node={name,open:false,contains:target=>target===node||target?.owner===node,
      matches:selector=>selector.includes('details.'+name),
      querySelector:()=>({focus(){node.focused=true;}})};
    return node;
  };
  const menus=['management-picker','user-picker','filter-picker','column-picker','key-group-picker','user-group-picker','help-note'].map(picker);
  install({querySelectorAll:selector=>menus.filter(menu=>menu.matches(selector)),addEventListener(type,fn){listeners[type]=fn;}});
  return {menus,fire(type,target,extra={}){const event={target,...extra,preventDefault(){this.prevented=true;},stopPropagation(){this.stopped=true;}};listeners[type](event);return event;}};
}

test('clicking or focusing outside closes each picker, not help panels or selections',()=>{
  const h=harness();const checkbox={owner:h.menus[0],checked:true};h.menus[0].open=true;h.menus.at(-1).open=true;
  h.fire('click',checkbox);assert.equal(h.menus[0].open,true);assert.equal(checkbox.checked,true);
  h.fire('focusin',{owner:h.menus[0]});assert.equal(h.menus[0].open,true);
  h.fire('click',{});assert.equal(h.menus[0].open,false);assert.equal(h.menus.at(-1).open,true);assert.equal(checkbox.checked,true);
  for(const menu of h.menus.slice(0,-1))menu.open=true;h.fire('focusin',{});assert.ok(h.menus.slice(0,-1).every(menu=>!menu.open));
});
test('opening another menu closes the previous menu without closing the new one',()=>{
  const h=harness();h.menus[0].open=true;h.menus[5].open=true;h.fire('toggle',h.menus[5]);
  assert.equal(h.menus[0].open,false);assert.equal(h.menus[5].open,true);
  h.menus[5].open=false;h.fire('toggle',h.menus[5]);assert.equal(h.menus[5].open,false);
});
test('Escape closes group/column menus and returns focus, but leaves unrelated dialogs alone',()=>{
  for(const index of [4,5]){
    const h=harness();h.menus[index].open=true;h.menus.at(-1).open=true;
    assert.equal(h.fire('keydown',{owner:h.menus[index]},{key:'Enter'}).prevented,undefined);assert.equal(h.menus[index].open,true);
    const event=h.fire('keydown',{owner:h.menus[index]},{key:'Escape'});assert.equal(h.menus[index].open,false);assert.equal(h.menus[index].focused,true);assert.equal(h.menus.at(-1).open,true);assert.equal(event.prevented,true);
    assert.equal(h.fire('keydown',{}, {key:'Escape'}).prevented,undefined);
  }
});
test('mobile column menu stays above or below its trigger, follows scrolling and resets on desktop',()=>{
  const events={},viewEvents={},style={};
  let mobile=true,anchor={top:150,bottom:190};
  const menu={style};
  const picker={open:true,matches:()=>true,contains:target=>target===menu,
    querySelector:selector=>selector==='summary'?{getBoundingClientRect:()=>anchor}:menu};
  const view={innerHeight:900,matchMedia:()=>({matches:mobile}),addEventListener(type,fn){viewEvents[type]=fn;}};
  install({defaultView:view,querySelectorAll:()=>[picker],querySelector:()=>({getBoundingClientRect:()=>({bottom:60})}),
    addEventListener(type,fn){events[type]=fn;}});
  events.toggle({target:picker});
  assert.equal(style.top,'196px');assert.equal(style.maxHeight,'320px');
  anchor={top:750,bottom:790};events.scroll({target:{}});
  assert.equal(style.top,'424px');assert.ok(parseFloat(style.top)+parseFloat(style.maxHeight)<anchor.top);
  mobile=false;viewEvents.resize();
  assert.deepEqual(style,{top:'',bottom:'',maxHeight:''});
  mobile=true;anchor={top:10,bottom:50};events.scroll({target:{}});
  assert.equal(picker.open,false);
});
