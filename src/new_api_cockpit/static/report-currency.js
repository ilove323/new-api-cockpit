/* Report presentation only. Source prices, charges, tokens and ratios stay intact. */
(() => {
  let selected='CNY',rate=null,source='';
  const money=new Intl.NumberFormat('zh-CN',{minimumFractionDigits:2,maximumFractionDigits:2});
  const numeric=value=>{
    if(value===null||value===undefined||value==='')return null;
    const result=Number(value);return Number.isFinite(result)?result:null;
  };
  window.CockpitReportCurrency={
    configure(settings){
      const value=numeric(settings?.usd_cny_rate);
      rate=value!==null&&value>0&&value<=1000000?value:null;
      source=settings?.rate_source||'';
    },
    select(currency){
      if(!['USD','CNY'].includes(currency)||(currency==='CNY'&&rate===null))return false;
      selected=currency;return true;
    },
    convert(value,currency=selected){
      const amount=numeric(value);
      if(amount===null||(currency==='CNY'&&rate===null))return null;
      const result=amount*(currency==='CNY'?rate:1);
      return Number.isFinite(result)?result:null;
    },
    formatRMB(value){
      const converted=this.convert(value,'CNY');return converted===null?'—':'¥ '+money.format(converted);
    },
    get currency(){return selected;},
    get unit(){return selected==='CNY'?'RMB':'USD';},
    get symbol(){return selected==='CNY'?'¥':'$';},
    get rate(){return rate;},
    get source(){return source;},
  };
})();
