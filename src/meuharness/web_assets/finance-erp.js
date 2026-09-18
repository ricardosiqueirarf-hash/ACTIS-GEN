const $ = (s) => document.querySelector(s);
const content = $("#content");
const title = $("#pageTitle");
const subtitle = $("#pageSubtitle");
const banner = $("#sourceBanner");
const refreshBtn = $("#refreshBtn");
const refreshERP = $("#refreshReceivablesBtn");
const accountDialog = $("#accountDialog");
const accountForm = $("#accountForm");
let ACCOUNT_CREATE_TARGET = "";
let DATA = null;
let VIEW = new URLSearchParams(location.search).get("view") || "overview";

const money = (v) => new Intl.NumberFormat("pt-BR", {style:"currency", currency:"BRL"}).format(Number(v || 0));
const num = (v) => new Intl.NumberFormat("pt-BR").format(Number(v || 0));
const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (m) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#039;"}[m]));
const pill = (text, kind) => '<span class="pill '+(kind || "")+'">'+esc(text)+'</span>';
const statusKind = (s) => (s === "confirmed" || s === "paid" || s === "ok") ? "good" : ((s === "unreconciled" || s === "proposed" || s === "open") ? "warn" : "");

function monthStart(){ const d=new Date(); return d.getFullYear()+"-"+String(d.getMonth()+1).padStart(2,"0")+"-01"; }
function monthEnd(){ const d=new Date(); return new Date(d.getFullYear(),d.getMonth()+1,0).toISOString().slice(0,10); }
$("#dateFrom").value = monthStart();
$("#dateTo").value = monthEnd();

const viewMeta = {
  overview:["Visão Geral","Posição financeira consolidada da ColorGlass."],
  bank:["Banco","Saldo e movimentações reais sincronizadas da Unicred."],
  reconciliation:["Conciliação","Cobertura da conciliação bancária e itens pendentes."],
  receivables:["A Receber","Cobranças oficiais do ERP/Asaas."],
  payables:["A Pagar","Obrigações registradas no financeiro da ColorGlass."],
  cashflow:["Fluxo de Caixa","Realizado bancário e projeção de entradas e saídas."],
  dre:["DRE","Resultado gerencial por competência."],
  chart:["Plano de Contas","Estrutura gerencial usada na classificação e na DRE."]
};

function sourceBanner(){
  const r=(DATA && DATA.receivables) || {};
  if(r.ok){ banner.classList.add("hidden"); return; }
  banner.classList.remove("hidden");
  banner.innerHTML='<b>Recebíveis ERP não carregados.</b> Fonte atual: <code>'+esc(r.status || "indisponível")+'</code>. Fluxo de caixa e posição continuam mostrando banco e contas locais, mas ficam parciais.';
}
function kpi(label, value, meta, cls){
  return '<div class="card kpi"><span class="label">'+esc(label)+'</span><div class="value '+(cls||"")+'">'+value+'</div><span class="meta">'+esc(meta||"")+'</span></div>';
}
function table(headers, rows){
  if(!rows.length) return '<div class="empty">Nenhum registro neste período.</div>';
  return '<div class="table-wrap"><table><thead><tr>'+headers.map((h)=>'<th>'+h+'</th>').join("")+'</tr></thead><tbody>'+rows.join("")+'</tbody></table></div>';
}
function renderOverview(){
  const b=DATA.bank||{}, p=DATA.position||{}, r=DATA.reconciliation||{}, c=DATA.cashflow||{}, d=DATA.dre||{};
  const rec=p.receivables||{}, counts=r.counts||{}, flow=c.forecast||{};
  const openRec=(rec.adjusted_open_total != null) ? rec.adjusted_open_total : ((rec.official_summary||{}).total_aberto || 0);
  const coverage=r.total_transactions ? Math.round((counts.confirmed||0)/r.total_transactions*100) : 0;
  let html='<div class="grid kpis">';
  html+=kpi("Saldo bancário",money(b.balance),b.as_of ? "posição em "+b.as_of : "sem saldo observado","good");
  html+=kpi("A receber",money(openRec),rec.source_ok ? "ERP/Asaas atualizado" : "fonte ERP parcial","");
  const pay=p.payables||{};
  html+=kpi("A pagar",money(pay.expected_outflows_total||pay.open_total||0),(pay.open_count||0)+" classificadas · "+(pay.dda_unclassified_count||0)+" DDA pendentes","");
  html+=kpi("Conciliação",coverage+"%",(counts.confirmed||0)+" confirmados · "+(counts.unreconciled||0)+" pendentes",coverage===100?"good":"warn");
  html+='</div>';
  html+='<div class="two-col section"><div class="card"><div class="section-head"><h2>Fluxo projetado</h2><span>próximos '+DATA.period.forecast_days+' dias</span></div><div class="summary-list">';
  html+='<div class="summary-line"><span>Entradas previstas</span><b>'+money(flow.receivables)+'</b></div>';
  html+='<div class="summary-line"><span>Saídas previstas</span><b>'+money(flow.payables)+'</b></div>';
  html+='<div class="summary-line"><span>Fluxo líquido previsto</span><b>'+money(flow.net)+'</b></div>';
  html+='<div class="summary-line"><span>Saldo bancário projetado</span><b>'+(flow.projected_bank_balance==null?"—":money(flow.projected_bank_balance))+'</b></div></div></div>';
  html+='<div class="card"><div class="section-head"><h2>DRE do período</h2><span>'+pill(d.complete?"completa":"parcial",d.complete?"good":"warn")+'</span></div><div class="summary-list">';
  html+='<div class="summary-line"><span>Receita</span><b>'+money(d.revenue)+'</b></div>';
  html+='<div class="summary-line"><span>Lucro bruto</span><b>'+money(d.gross_profit)+'</b></div>';
  html+='<div class="summary-line"><span>Resultado operacional</span><b>'+money(d.operating_result)+'</b></div>';
  html+='<div class="summary-line"><span>Resultado líquido</span><b>'+money(d.net_result)+'</b></div></div>';
  if(d.warning) html+='<p class="note">'+esc(d.warning)+'</p>';
  html+='</div></div>';
  html+='<div class="card source-card section"><div class="source-state"><i class="ok"></i><div><b>Unicred Bank Bridge</b><div class="note">último sync: '+esc((b.last_sync||{}).finished_at||"—")+'</div></div></div>';
  html+='<div class="source-state"><i class="'+(DATA.receivables&&DATA.receivables.ok?"ok":"bad")+'"></i><div><b>ERP / Asaas</b><div class="note">'+(DATA.receivables&&DATA.receivables.ok?"conectado":esc((DATA.receivables||{}).status||"não carregado"))+'</div></div></div></div>';
  content.innerHTML=html;
}
function renderBank(){
  const tx=DATA.transactions||[];
  const rows=tx.map((t)=>'<tr><td>'+esc(t.date)+'</td><td>'+pill(t.direction==="credit"?"Crédito":"Débito",t.direction==="credit"?"good":"bad")+'</td><td>'+esc(t.description)+'</td><td class="money">'+money(Math.abs(t.amount||0))+'</td><td class="money">'+(t.balance_after==null?"—":money(t.balance_after))+'</td><td>'+pill(t.reconciliation_status,statusKind(t.reconciliation_status))+'</td></tr>');
  let html='<div class="grid kpis">';
  html+=kpi("Saldo atual",money((DATA.bank||{}).balance),(DATA.bank||{}).as_of||"","good");
  html+=kpi("Lançamentos",num(tx.length),"no período selecionado","");
  html+=kpi("Créditos",money(tx.filter((x)=>x.direction==="credit").reduce((s,x)=>s+Math.abs(x.amount||0),0)),"movimentação observada","");
  html+=kpi("Débitos",money(tx.filter((x)=>x.direction==="debit").reduce((s,x)=>s+Math.abs(x.amount||0),0)),"movimentação observada","");
  html+='</div><div class="section"><div class="section-head"><h2>Extrato estruturado</h2><span>Unicred · read-only</span></div>';
  html+=table(["Data","Tipo","Descrição","Valor","Saldo após","Conciliação"],rows)+'</div>';
  content.innerHTML=html;
}
function renderReconciliation(){
  const r=DATA.reconciliation||{}, c=r.counts||{}, a=r.amounts||{};
  const accounts=(DATA.chart_of_accounts||[]).filter((x)=>x.active!==false);
  const opts='<option value="">Escolher conta...</option><option value="__create__">＋ Criar nova conta...</option>'+accounts.map((x)=>'<option value="'+esc(x.code)+'">'+esc(x.code+" · "+x.name)+'</option>').join("");
  const rows=(DATA.transactions||[]).filter((t)=>t.reconciliation_status!=="confirmed").map((t)=>'<tr data-recon-row><td>'+esc(t.date)+'</td><td>'+esc(t.description)+'</td><td>'+pill(t.direction==="credit"?"Crédito":"Débito",t.direction==="credit"?"good":"bad")+'</td><td class="money">'+money(Math.abs(t.amount||0))+'</td><td><select class="recon-account">'+opts+'</select></td><td><input class="recon-note" placeholder="O que foi isso?"></td><td><button class="recon-confirm" data-key="'+esc(t.bank_transaction_key)+'">MARCAR</button></td></tr>');
  const coverage=r.total_transactions?Math.round((c.confirmed||0)/r.total_transactions*100):0;
  let html='<div class="grid kpis">'+kpi("Confirmados",num(c.confirmed||0),money(((a.confirmed||{}).credits||0)+((a.confirmed||{}).debits||0)),"good")+kpi("Propostos",num(c.proposed||0),"aguardando confirmação","warn")+kpi("Não conciliados",num(c.unreconciled||0),"precisam de classificação",c.unreconciled?"warn":"good")+kpi("Cobertura",coverage+"%",r.complete?"período fechado":"período em aberto",r.complete?"good":"warn")+'</div>';
  html+='<div class="section"><div class="section-head"><h2>Pendências da conciliação</h2><span>defina a conta e confirme</span></div>'+table(["Data","Descrição","Tipo","Valor","Conta","Observação",""],rows)+'</div>';
  content.innerHTML=html;
}
function renderReceivables(){
  const r=DATA.receivables||{}, items=r.itens||[], s=r.resumo||{};
  const rows=items.map((x)=>'<tr><td>'+esc(x.numero_pedido||"—")+'</td><td>'+esc(x.cliente_nome||"—")+'</td><td>'+esc(x.parcela||"—")+'</td><td class="money">'+money(x.valor)+'</td><td>'+esc(x.vencimento||"—")+'</td><td>'+pill(x.status_label||x.status_categoria||x.status_raw||"—",x.status_categoria==="recebido"?"good":(x.status_categoria==="vencido"?"bad":"warn"))+'</td></tr>');
  let html='<div class="grid kpis">'+kpi("Em aberto",r.ok?money(s.total_aberto):"—",r.ok?(s.quantidade_abertos||0)+" cobrança(s)":"ERP não conectado","")+kpi("Vencidos",r.ok?money(s.vencidos):"—",r.ok?"cobranças atrasadas":"","")+kpi("Vence hoje",r.ok?money(s.vence_hoje):"—",r.ok?"vencimento no dia":"","")+kpi("Recebido no mês",r.ok?money(s.recebido_mes):"—",r.ok?(s.quantidade_recebidos||0)+" recebimento(s)":"","")+'</div>';
  html+='<div class="section"><div class="section-head"><h2>Recebíveis oficiais</h2><span>'+(r.ok?"ERP / Asaas":"clique em Atualizar ERP")+'</span></div>';
  html+=r.ok?table(["Pedido","Cliente","Parcela","Valor","Vencimento","Status"],rows):'<div class="card empty">Fonte ERP indisponível: <b>'+esc(r.status||"not_loaded")+'</b>.</div>';
  content.innerHTML=html+'</div>';
}
function renderPayables(){
  const items=DATA.payables||[], open=items.filter((x)=>x.status==="open"), dda=DATA.dda_bills||[];
  const pos=((DATA.position||{}).payables)||{};
  const rows=items.map((x)=>'<tr><td>'+esc(x.due_date||"—")+'</td><td>'+esc(x.counterparty||"—")+'</td><td>'+esc(x.description||"—")+'</td><td>'+esc(x.account_code||"—")+'</td><td class="money">'+money(x.amount)+'</td><td>'+pill(x.status,statusKind(x.status))+'</td></tr>');
  const ddaRows=dda.map((x)=>'<tr><td>'+esc(x.due_date||"—")+'</td><td>'+esc(x.counterparty||"—")+'</td><td>'+esc(x.document_ref||"—")+'</td><td class="money">'+money(x.amount)+'</td><td>'+pill(x.dda_status||"DDA","warn")+'</td><td>'+pill("não classificado","warn")+'</td></tr>');
  let html='<div class="grid kpis">';
  html+=kpi("Saída esperada",money(pos.expected_outflows_total||0),"ledger + DDA sem duplicar","");
  html+=kpi("DDA detectados",num(DATA.dda_bills_all_count||0),money(DATA.dda_bills_all_total||0)+" no banco","warn");
  html+=kpi("Classificados",money(open.reduce((s,x)=>s+Number(x.amount||0),0)),open.length+" conta(s) ACTIS","");
  html+=kpi("DDA não classificados",money(pos.dda_unclassified_total||0),(pos.dda_unclassified_count||0)+" obrigação(ões)","warn");
  html+='</div>';
  html+='<div class="section"><div class="section-head"><h2>Boletos detectados no DDA</h2><span>'+dda.length+' no período · '+(DATA.dda_bills_all_count||0)+' armazenados</span></div>'+table(["Vencimento","Beneficiário","Documento","Valor","DDA","Classificação"],ddaRows)+'</div>';
  html+='<div class="section"><div class="section-head"><h2>Contas a pagar classificadas</h2><span>ledger ACTIS</span></div>'+table(["Vencimento","Fornecedor","Descrição","Conta","Valor","Status"],rows)+'</div>';
  content.innerHTML=html;
}
function renderCashflow(){
  const c=DATA.cashflow||{}, r=c.realized_month||{}, f=c.forecast||{};
  const max=Math.max(r.inflows||0,r.outflows||0,f.receivables||0,f.payables||0,1);
  const bar=(label,v,out)=>'<div class="chart-row"><span>'+esc(label)+'</span><div class="bar '+(out?"out":"")+'"><i style="width:'+Math.max(2,Math.round(Number(v||0)/max*100))+'%"></i></div><b>'+money(v)+'</b></div>';
  let html='<div class="grid kpis">'+kpi("Entradas realizadas",money(r.inflows),"mês atual","good")+kpi("Saídas realizadas",money(r.outflows),"mês atual","bad")+kpi("Líquido realizado",money(r.net),"mês atual",r.net>=0?"good":"bad")+kpi("Saldo projetado",f.projected_bank_balance==null?"—":money(f.projected_bank_balance),"até "+(c.forecast_to||"—"),f.projected_bank_balance>=0?"good":"bad")+'</div>';
  html+='<div class="two-col section"><div class="card"><div class="section-head"><h2>Realizado x projetado</h2><span>movimento de caixa</span></div><div class="chart-list">'+bar("Entradas realizadas",r.inflows,false)+bar("Saídas realizadas",r.outflows,true)+bar("Recebimentos previstos",f.receivables,false)+bar("Pagamentos previstos",f.payables,true)+'</div></div>';
  html+='<div class="card"><div class="section-head"><h2>Cobertura</h2></div><div class="summary-list"><div class="summary-line"><span>Banco</span><b>'+money(c.bank_balance)+'</b></div><div class="summary-line"><span>Recebíveis considerados</span><b>'+num((c.coverage||{}).receivables_count||0)+'</b></div><div class="summary-line"><span>Contas a pagar consideradas</span><b>'+num((c.coverage||{}).payables_count||0)+'</b></div><div class="summary-line"><span>ERP recebíveis</span><b>'+((c.coverage||{}).receivables_source_ok?"OK":"PARCIAL")+'</b></div></div></div></div>';
  content.innerHTML=html;
}
function renderDRE(){
  const d=DATA.dre||{};
  let html='<div class="grid kpis">'+kpi("Receita",money(d.revenue),"competência","")+kpi("Lucro bruto",money(d.gross_profit),"receita - CMV",d.gross_profit>=0?"good":"bad")+kpi("Resultado operacional",money(d.operating_result),"após despesas operacionais",d.operating_result>=0?"good":"bad")+kpi("Resultado líquido",money(d.net_result),d.complete?"DRE completa":"DRE parcial",d.complete?(d.net_result>=0?"good":"bad"):"warn")+'</div>';
  html+='<div class="section"><div class="section-head"><h2>DRE gerencial</h2><span>'+pill(d.complete?"completa":"parcial",d.complete?"good":"warn")+'</span></div><div class="dre-stack">';
  html+='<div class="dre-line"><span>Receita</span><strong>'+money(d.revenue)+'</strong></div><div class="dre-line"><span>(-) Custo dos produtos vendidos</span><strong>'+money(d.cogs)+'</strong></div><div class="dre-line total"><span>= Lucro bruto</span><strong>'+money(d.gross_profit)+'</strong></div><div class="dre-line"><span>(-) Despesas operacionais</span><strong>'+money(d.operating_expenses)+'</strong></div><div class="dre-line total"><span>= Resultado operacional</span><strong>'+money(d.operating_result)+'</strong></div><div class="dre-line"><span>(-) Despesas financeiras</span><strong>'+money(d.financial_expenses)+'</strong></div><div class="dre-line"><span>(-) Impostos</span><strong>'+money(d.taxes)+'</strong></div><div class="dre-line total result"><span>= Resultado líquido</span><strong>'+money(d.net_result)+'</strong></div></div>';
  if(d.warning) html+='<p class="note">'+esc(d.warning)+' Classificados: '+(d.classified_entries||0)+'; fora/sem classificação: '+(d.excluded_or_unclassified_entries||0)+'.</p>';
  content.innerHTML=html+'</div>';
}
function renderChart(){
  const a=DATA.chart_of_accounts||[];
  content.innerHTML='<div class="section-head"><h2>Plano de contas gerencial</h2><div><span>'+a.length+' contas ativas</span> <button id="createAccountBtn" class="button primary">+ Nova conta</button></div></div><div class="account-grid">'+a.map((x)=>'<div class="account"><code>'+esc(x.code)+'</code><b>'+esc(x.name)+'</b><small>DRE: '+esc(x.dre_group)+' · Caixa: '+esc(x.cashflow_group)+'</small></div>').join("")+'</div>';
}
function openAccountDialog(targetKey=""){
  ACCOUNT_CREATE_TARGET=targetKey||"";
  accountForm.reset();
  $("#accountNature").value="asset";
  $("#accountDre").value="non_dre";
  $("#accountCashflow").value="operating";
  $("#accountError").classList.add("hidden");
  accountDialog.showModal();
  setTimeout(()=>$("#accountCode").focus(),0);
}
function closeAccountDialog(){ ACCOUNT_CREATE_TARGET=""; accountDialog.close(); }
async function createAccount(){
  const payload={
    code:$("#accountCode").value.trim(), name:$("#accountName").value.trim(),
    nature:$("#accountNature").value, dre_group:$("#accountDre").value,
    cashflow_group:$("#accountCashflow").value
  };
  const err=$("#accountError"), save=$("#accountSave");
  if(!payload.code||!payload.name){err.textContent="Informe código e nome.";err.classList.remove("hidden");return;}
  save.disabled=true; save.textContent="Criando...";
  try{
    const r=await fetch("/api/chart/account",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)});
    const data=await r.json(); if(!r.ok||!data.ok) throw new Error(data.error||"Falha ao criar conta");
    const target=ACCOUNT_CREATE_TARGET; accountDialog.close(); ACCOUNT_CREATE_TARGET="";
    await load();
    if(target){
      const row=[...document.querySelectorAll("[data-recon-row]")].find((x)=>x.querySelector(".recon-confirm")?.dataset.key===target);
      if(row) row.querySelector(".recon-account").value=data.account.code;
    }
  }catch(e){err.textContent=e.message;err.classList.remove("hidden");}
  finally{save.disabled=false;save.textContent="Criar conta";}
}
function render(){
  if(!viewMeta[VIEW]) VIEW="overview";
  document.querySelectorAll(".nav-item").forEach((x)=>x.classList.toggle("active",x.dataset.view===VIEW));
  const meta=viewMeta[VIEW];
  title.textContent=meta[0]; subtitle.textContent=meta[1]; sourceBanner();
  const map={overview:renderOverview,bank:renderBank,reconciliation:renderReconciliation,receivables:renderReceivables,payables:renderPayables,cashflow:renderCashflow,dre:renderDRE,chart:renderChart};
  (map[VIEW]||renderOverview)();
}
async function load(){
  refreshBtn.disabled=true;
  content.innerHTML='<div class="loading">Atualizando dados financeiros...</div>';
  try{
    const q=new URLSearchParams({from:$("#dateFrom").value,to:$("#dateTo").value,forecast_days:"30"});
    const r=await fetch("/api/state?"+q.toString(),{cache:"no-store"});
    DATA=await r.json();
    if(!r.ok||!DATA.ok) throw new Error(DATA.error||"Falha ao carregar");
    render();
  }catch(e){
    content.innerHTML='<div class="card empty">Erro ao carregar ERP: '+esc(e.message)+'</div>';
  }finally{refreshBtn.disabled=false;}
}
$("#nav").addEventListener("click",(e)=>{
  const b=e.target.closest("[data-view]"); if(!b)return;
  document.querySelectorAll(".nav-item").forEach((x)=>x.classList.remove("active"));
  b.classList.add("active"); VIEW=b.dataset.view; if(DATA)render();
});
content.addEventListener("change",(e)=>{
  const select=e.target.closest(".recon-account");
  if(!select||select.value!=="__create__")return;
  const row=select.closest("[data-recon-row]");
  const key=row?.querySelector(".recon-confirm")?.dataset.key||"";
  select.value="";
  openAccountDialog(key);
});
content.addEventListener("click",async(e)=>{
  const createBtn=e.target.closest("#createAccountBtn");
  if(createBtn){openAccountDialog("");return;}
  const b=e.target.closest(".recon-confirm"); if(!b)return;
  const row=b.closest("[data-recon-row]");
  const account=row.querySelector(".recon-account").value;
  const notes=row.querySelector(".recon-note").value.trim();
  if(!account){row.querySelector(".recon-account").focus();return;}
  b.disabled=true; b.classList.add("saving"); b.textContent="SALVANDO";
  try{
    const r=await fetch("/api/reconciliation/confirm",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({bank_transaction_key:b.dataset.key,account_code:account,notes})});
    const payload=await r.json(); if(!r.ok||!payload.ok) throw new Error(payload.error||"Falha ao classificar");
    b.classList.remove("saving"); b.classList.add("completed"); b.textContent="CONCLUÍDO";
    await new Promise((resolve)=>setTimeout(resolve,380));
    await load();
  }catch(err){b.disabled=false;b.classList.remove("saving");b.classList.add("error");b.textContent="ERRO";alert(err.message);}
});
accountForm.addEventListener("submit",(e)=>{e.preventDefault();createAccount();});
$("#accountCancel").addEventListener("click",closeAccountDialog);
$("#accountCancelX").addEventListener("click",closeAccountDialog);
accountDialog.addEventListener("cancel",(e)=>{e.preventDefault();closeAccountDialog();});
refreshBtn.addEventListener("click",load);
$("#dateFrom").addEventListener("change",load);
$("#dateTo").addEventListener("change",load);
refreshERP.addEventListener("click",async()=>{
  refreshERP.disabled=true; refreshERP.textContent="Atualizando...";
  try{await fetch("/api/receivables/refresh",{method:"POST"});}catch(e){}
  refreshERP.disabled=false; refreshERP.textContent="Atualizar ERP"; await load();
});
load();
