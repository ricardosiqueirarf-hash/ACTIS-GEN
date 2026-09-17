let whatsappViewer={agentId:'',contact:'',data:null,messages:[],error:''};
function waViewerAgents(){return agents.filter(a=>agentHasWhatsApp(a));}
function waContactName(row){return String((row&&row.contact_name)||(row&&row.title)||'').trim();}
function waInitials(name=''){
  const parts=String(name).trim().split(/\s+/).filter(Boolean);
  return (parts.length>1?parts[0][0]+parts[1][0]:String(name).slice(0,2)).toUpperCase()||'WA';
}
function waDate(value,full=false){
  if(!value)return'';
  try{return new Date(value).toLocaleString('pt-BR',full?{dateStyle:'short',timeStyle:'short'}:{hour:'2-digit',minute:'2-digit'});}catch(_){return String(value);}
}
function waPreview(row){
  const text=String(row?.last_message_content||row?.summary||row?.intent||'Sem mensagens armazenadas').trim();
  return (row?.last_message_direction==='outgoing'?'Você: ':'')+text;
}
function waSelectedConversation(){
  return (whatsappViewer.data?.inbox||[]).find(row=>waContactName(row)===whatsappViewer.contact)||null;
}
function waConversationHtml(row){
  const name=waContactName(row),active=name===whatsappViewer.contact;
  const when=row.stored_last_message_at||row.last_message_at||row.updated_at;
  const unread=Number(row.unread_count||0);
  return `<button class="wa-view-row ${active?'active':''}" data-wa-contact="${esc(name)}"><span class="wa-view-avatar">${esc(waInitials(name))}</span><span class="wa-view-row-main"><span class="wa-view-row-line"><strong>${esc(name||'Contato')}</strong></span><p>${esc(waPreview(row))}</p></span><span><time>${esc(waDate(when))}</time>${unread?`<span class="wa-view-badge">${unread}</span>`:''}</span></button>`;
}
function waMessageHtml(message){
  const outgoing=String(message.direction||'').toLowerCase()==='outgoing';
  const text=String(message.content||'').trim()||`[${message.message_type||'mensagem sem texto'}]`;
  const attachment=message.attachment_path?`<div class="wa-view-text">📎 ${esc(message.attachment_path)}</div>`:'';
  const sender=!outgoing&&message.sender?`<div class="wa-view-sender">${esc(message.sender)}</div>`:'';
  return `<div class="wa-view-msg ${outgoing?'outgoing':'incoming'}" data-wa-message="${esc(text.toLowerCase())}"><div class="wa-view-bubble">${sender}<div class="wa-view-text">${esc(text)}</div>${attachment}<div class="wa-view-meta"><span class="wa-view-type">${esc(message.message_type||'text')}</span><span>${esc(waDate(message.message_ts||message.created_at))}</span></div></div></div>`;
}
function waInfoHtml(row){
  if(!row)return '<div class="wa-view-empty"><div><b>Nenhuma conversa</b>Selecione um contato.</div></div>';
  const labels=String(row.labels||'').split(',').filter(Boolean);
  return `<div class="wa-view-infohead">Dados do Shadow DB</div><div class="wa-view-info-body"><div class="wa-view-profile"><span class="wa-view-avatar">${esc(waInitials(waContactName(row)))}</span><b>${esc(waContactName(row))}</b><small>${esc(row.contact_external_id||'ID local')}</small></div><div class="wa-view-stat"><span>Mensagens armazenadas</span><b>${Number(row.message_count||0)}</b></div><div class="wa-view-stat"><span>Status</span><b>${esc(row.status||'active')}</b></div><div class="wa-view-stat"><span>Automação</span><b>${esc(row.automation_mode||'—')}</b></div><div class="wa-view-stat"><span>Prioridade</span><b>${esc(row.priority||'normal')}</b></div><div class="wa-view-stat"><span>Última mensagem</span><b>${esc(waDate(row.stored_last_message_at||row.last_message_at,true)||'—')}</b></div><div class="wa-view-stat"><span>Última sincronização</span><b>${esc(waDate(row.last_synced_at,true)||row.sync_status||'—')}</b></div>${labels.length?`<div class="wa-view-stat"><span>Labels</span><div class="wa-view-labels">${labels.map(label=>`<span class="wa-view-label">${esc(label)}</span>`).join('')}</div></div>`:''}</div>`;
}
function waFilterRows(value){
  const query=String(value||'').trim().toLowerCase();
  document.querySelectorAll('[data-wa-contact]').forEach(el=>{el.hidden=Boolean(query&&!String(el.textContent||'').toLowerCase().includes(query));});
}
function waFilterMessages(value){
  const query=String(value||'').trim().toLowerCase();
  document.querySelectorAll('[data-wa-message]').forEach(el=>{el.hidden=Boolean(query&&!String(el.dataset.waMessage||'').includes(query));});
}
function waPaint(){
  title.textContent='WhatsApp';
  const candidates=waViewerAgents();
  if(!candidates.length){content.innerHTML='<div class="wa-view-error">Nenhum agente com a skill WhatsApp habilitada.</div>';return;}
  const data=whatsappViewer.data||{inbox:[],outbox:[]},inbox=data.inbox||[],row=waSelectedConversation();
  const agentOptions=candidates.map(agent=>`<option value="${esc(agent.id)}" ${agent.id===whatsappViewer.agentId?'selected':''}>${esc(agent.name||agent.id)}</option>`).join('');
  const conversations=inbox.length?inbox.map(waConversationHtml).join(''):'<div class="wa-view-empty"><div><b>Banco vazio</b>Nenhuma conversa armazenada.</div></div>';
  const messages=whatsappViewer.messages.length?whatsappViewer.messages.map(waMessageHtml).join(''):'<div class="wa-view-empty"><div><b>Sem mensagens</b>Não há histórico armazenado para este contato.</div></div>';
  const contact=whatsappViewer.contact||'Selecione uma conversa';
  content.innerHTML=`<div class="wa-view-page"><div class="wa-view-topbar"><h1>Shadow DB</h1><span class="wa-view-ro">● somente leitura</span><span class="spacer"></span><select id="waViewAgent">${agentOptions}</select><button id="waViewRefresh">Atualizar</button><button id="waViewSync" class="primary">Sincronizar</button></div><div class="wa-view-shell"><aside class="wa-view-sidebar"><div class="wa-view-sidehead"><span class="wa-view-avatar">WA</span><div><strong>${esc(candidates.find(a=>a.id===whatsappViewer.agentId)?.name||whatsappViewer.agentId)}</strong><small>${inbox.length} conversa(s) · ${(inbox.reduce((n,x)=>n+Number(x.message_count||0),0))} mensagens</small></div></div><div class="wa-view-search"><input id="waViewContactSearch" placeholder="Pesquisar conversa"></div><div class="wa-view-list">${conversations}</div></aside><section class="wa-view-chat"><div class="wa-view-chathead"><span class="wa-view-avatar">${esc(waInitials(contact))}</span><div><strong>${esc(contact)}</strong><small>${row?`${Number(row.message_count||0)} mensagens no banco`:'Shadow DB local'}</small></div>${row?'<input id="waViewMessageSearch" class="wa-view-chatsearch" placeholder="Buscar nesta conversa">':''}</div><div class="wa-view-messages" id="waViewMessages">${messages}</div></section><aside class="wa-view-info">${waInfoHtml(row)}</aside></div></div>`;
  waBind();
}
function waBind(){
  document.querySelector('#waViewAgent')?.addEventListener('change',async event=>{whatsappViewer.agentId=event.target.value;whatsappViewer.contact='';await waRefresh(false);});
  document.querySelector('#waViewRefresh')?.addEventListener('click',()=>waRefresh(true));
  document.querySelector('#waViewSync')?.addEventListener('click',waSyncNow);
  document.querySelector('#waViewContactSearch')?.addEventListener('input',event=>waFilterRows(event.target.value));
  document.querySelector('#waViewMessageSearch')?.addEventListener('input',event=>waFilterMessages(event.target.value));
  document.querySelectorAll('[data-wa-contact]').forEach(button=>button.addEventListener('click',async()=>{whatsappViewer.contact=button.dataset.waContact||'';await waLoadMessages();waPaint();}));
  requestAnimationFrame(()=>{const host=document.querySelector('#waViewMessages');if(host)host.scrollTop=host.scrollHeight;});
}
async function waLoadMessages(){
  whatsappViewer.messages=[];
  if(!whatsappViewer.agentId||!whatsappViewer.contact)return;
  const url='/api/agents/'+encodeURIComponent(whatsappViewer.agentId)+'/whatsapp/messages?contact='+encodeURIComponent(whatsappViewer.contact)+'&limit=200';
  const result=await api(url);
  whatsappViewer.messages=result.messages||[];
}
async function waRefresh(keepContact=true){
  try{
    whatsappViewer.error='';
    const result=await api('/api/agents/'+encodeURIComponent(whatsappViewer.agentId)+'/whatsapp');
    whatsappViewer.data=result;
    const names=(result.inbox||[]).map(waContactName);
    if(!keepContact||!names.includes(whatsappViewer.contact))whatsappViewer.contact=names[0]||'';
    await waLoadMessages();
    waPaint();
  }catch(error){whatsappViewer.error=error.message;content.innerHTML=`<div class="wa-view-error">${esc(error.message||'Falha ao ler Shadow DB.')}</div>`;}
}
async function waSyncNow(){
  const button=document.querySelector('#waViewSync');
  try{
    if(button){button.disabled=true;button.textContent='Sincronizando…';}
    await api('/api/agents/'+encodeURIComponent(whatsappViewer.agentId)+'/whatsapp/sync',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({emit_inbound_events:false})});
    await waRefresh(true);
  }catch(error){whatsappViewer.error=error.message;alert('Falha ao sincronizar: '+error.message);}
  finally{if(button){button.disabled=false;button.textContent='Sincronizar';}}
}
async function renderWhatsApp(){
  stopWorld();currentView='whatsapp';selected=null;title.textContent='WhatsApp';setNavigationPage('whatsapp');
  const candidates=waViewerAgents();
  if(!candidates.length){waPaint();return;}
  if(!candidates.some(agent=>agent.id===whatsappViewer.agentId))whatsappViewer.agentId=candidates[0].id;
  content.innerHTML='<div class="wa-view-empty"><div><b>Lendo Shadow DB…</b>Carregando conversas armazenadas.</div></div>';
  await waRefresh(true);
}
