/* Runs only in the preview's opaque sandbox. Posts dimensions, never HTML. */
(function(){
  'use strict';
  let pending=false,last='';
  function measure(){
    pending=false;
    const body=document.body,root=document.documentElement;if(!body)return;
    let top=0,left=0,bottom=0,right=0;
    for(const node of document.querySelectorAll('body > *, h1, h2, svg, canvas')){
      if(node.tagName==='SCRIPT'||node.tagName==='STYLE')continue;
      const rect=node.getBoundingClientRect();
      top=Math.min(top,rect.top);left=Math.min(left,rect.left);
      bottom=Math.max(bottom,rect.bottom);right=Math.max(right,rect.right);
    }
    const width=Math.ceil(Math.max(root.scrollWidth,body.scrollWidth,right-left));
    const height=Math.ceil(Math.max(root.scrollHeight,body.scrollHeight,bottom-top));
    if(!Number.isFinite(width)||!Number.isFinite(height)||width<=0||height<=0||width>16384||height>16384)return;
    const next=width+':'+height;if(next===last)return;last=next;
    parent.postMessage({type:'cockpit-intelligence-size',width,height},'*');
  }
  function schedule(){if(!pending){pending=true;requestAnimationFrame(measure);}}
  function start(){
    if(typeof ResizeObserver==='function'){
      const observer=new ResizeObserver(schedule);observer.observe(document.body);observer.observe(document.documentElement);
    }
    window.addEventListener('load',schedule);window.addEventListener('resize',schedule);schedule();
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',start,{once:true});else start();
})();
