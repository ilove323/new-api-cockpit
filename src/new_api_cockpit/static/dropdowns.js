(function(){
  'use strict';
  // Only transient picker menus dismiss; explanatory <details> stay expanded.
  const selector='details.filter-picker, details.column-picker, details.user-picker, details.management-picker, details.key-group-picker, details.user-group-picker';
  function install(document){
    const opened=()=>Array.from(document.querySelectorAll(selector)).filter(picker=>picker.open);
    function placeColumns(picker){
      const view=document.defaultView;
      if(!picker.matches('.column-picker')||!view?.matchMedia)return;
      const menu=picker.querySelector('.column-menu');
      if(!menu)return;
      if(!view.matchMedia('(max-width: 700px)').matches){
        for(const name of ['top','bottom','maxHeight'])menu.style[name]='';
        return;
      }
      const anchor=picker.querySelector('summary').getBoundingClientRect();
      const header=document.querySelector('.app-header')?.getBoundingClientRect().bottom||8;
      if(anchor.bottom<header||anchor.top>view.innerHeight){picker.open=false;return;}
      const below=view.innerHeight-anchor.bottom-14,above=anchor.top-header-6;
      const height=Math.max(0,Math.min(320,Math.max(below,above)));
      menu.style.maxHeight=height+'px';menu.style.bottom='auto';
      menu.style.top=(below>=height?anchor.bottom+6:anchor.top-height-6)+'px';
    }
    const outside=event=>{
      for(const picker of opened())if(!picker.contains(event.target))picker.open=false;
    };
    document.addEventListener('click',outside,true);
    document.addEventListener('focusin',outside);
    document.addEventListener('toggle',event=>{
      const picker=event.target;
      if(!picker.matches?.(selector)||!picker.open)return;
      for(const other of opened())if(other!==picker)other.open=false;
      placeColumns(picker);
    },true);
    document.defaultView?.addEventListener('resize',()=>opened().forEach(placeColumns));
    document.addEventListener('scroll',event=>{
      for(const picker of opened())if(!picker.contains(event.target))placeColumns(picker);
    },true);
    document.addEventListener('keydown',event=>{
      if(event.key!=='Escape')return;
      const pickers=opened();
      if(!pickers.length)return;
      const active=pickers.find(picker=>picker.contains(event.target))||pickers[0];
      for(const picker of pickers)picker.open=false;
      event.preventDefault();
      event.stopPropagation();
      active.querySelector('summary')?.focus();
    },true);
  }
  if(typeof module!=='undefined'&&module.exports)module.exports={install};
  if(typeof document!=='undefined')install(document);
})();
