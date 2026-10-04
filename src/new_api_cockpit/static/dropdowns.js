(function(){
  'use strict';
  // Only transient picker menus dismiss; explanatory <details> stay expanded.
  const selector='details.filter-picker, details.column-picker, details.user-picker, details.management-picker, details.key-group-picker';
  function install(document){
    const opened=()=>Array.from(document.querySelectorAll(selector)).filter(picker=>picker.open);
    const outside=event=>{
      for(const picker of opened())if(!picker.contains(event.target))picker.open=false;
    };
    document.addEventListener('click',outside,true);
    document.addEventListener('focusin',outside);
    document.addEventListener('toggle',event=>{
      const picker=event.target;
      if(!picker.matches?.(selector)||!picker.open)return;
      for(const other of opened())if(other!==picker)other.open=false;
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
