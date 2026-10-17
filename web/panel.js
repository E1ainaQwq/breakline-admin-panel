'use strict';
// The Vue workspace hosts these existing flows without changing their permission or receipt logic.
const $ = selector => document.querySelector(selector);
const state = { user:null, csrf:'', page:'servers', servers:[], players:[], playerOffset:0, playerTotal:0, playerQuery:'', catalogOffset:0, catalogTotal:0, catalogQuery:'', selectedPlayer:null, operation:null };
const titles = {
  online:['在线玩家','ONLINE PLAYERS',''],
  evidence:['截图证据','SCREENSHOT EVIDENCE',''],
  moderation:['广播与玩家管理','MODERATION',''],
  servers:['服务器','FLEET CONTROL',''],
  players:['玩家列表','PLAYER LIST',''],
  grants:['物品与补偿','MAIL DELIVERY',''],
  console:['执行台','COMMAND DESK',''],
  admins:['管理员','ACCESS CONTROL',''],
  audit:['操作记录','OPERATION JOURNAL','']
};
const roles = {admin:'管理员',owner:'服主'};
const format = value => new Intl.NumberFormat('zh-CN').format(value || 0);
const date = value => new Date(value * (value < 1e12 ? 1000 : 1)).toLocaleString('zh-CN');
function node(tag,cls,content){const element=document.createElement(tag);if(cls)element.className=cls;if(content!==undefined)element.textContent=content;return element;}
function has(permission){return state.user?.permissions.includes('*') || state.user?.permissions.includes(permission);}
function error(message){$('#global-error').textContent=message;$('#global-error').hidden=!message;}
function status(message){$('#global-status').textContent=message;$('#global-status').hidden=!message;}
async function api(path,data){
  const response=await fetch('/api/panel/'+path,{method:data?'POST':'GET',credentials:'same-origin',headers:data?{'Content-Type':'application/json','X-CSRF-Token':state.csrf}:{},...(data?{body:JSON.stringify(data)}:{}),signal:AbortSignal.timeout(35000)});
  const value=await response.json();
  if(!response.ok||!value.ok){if(response.status===401&&path!=='login')showLogin();throw new Error(value.message||'请求未成功');}
  return value.data??value;
}
async function busy(button,task){if(button.disabled)return;const old=button.textContent;button.disabled=true;button.textContent='处理中…';try{return await task();}catch(e){error(e.message);throw e;}finally{button.disabled=false;button.textContent=old;}}
function safe(task){return (...args)=>{try{return Promise.resolve(task(...args)).catch(e=>error(e.message));}catch(e){error(e.message);}};}
function showLogin(){$('#login').hidden=false;$('#app').hidden=true;state.user=null;state.csrf='';}
async function enter(session){
  state.user=session.user;state.csrf=session.csrf;
  $('#login').hidden=true;$('#app').hidden=true;$('#user-name').textContent=state.user.username;
  $('#user-roles').textContent=state.user.roles.map(role=>roles[role]).join(' / ');
  document.querySelectorAll('[data-permission]').forEach(element=>element.hidden=!has(element.dataset.permission));
  await page('servers');
  $('#app').hidden=false;
}
async function page(name){
  if(!Object.prototype.hasOwnProperty.call(titles,name))return;
  state.page=name;error('');status('');
  document.querySelectorAll('.page').forEach(element=>element.hidden=element.id!=='page-'+name);
  document.querySelectorAll('nav button[data-page]').forEach(button=>button.classList.toggle('active',button.dataset.page===name));
  const [title,tag,description]=titles[name];$('#page-title').textContent=title;$('#page-tag').textContent=tag;$('#page-description').textContent=description;$('#page-description').hidden=!description;$('#breadcrumb').textContent='控制中心 / '+title;
  await refresh();
  window.dispatchEvent(new CustomEvent('breakline:navigate',{detail:name}));
}
async function refresh(){
  if(state.page==='online')await loadOnlinePlayers();
  if(state.page==='servers')await loadServers();
  if(state.page==='players')await loadPlayers();
  if(state.page==='grants'){await loadCatalog();await loadOperations();}
  if(state.page==='moderation'){if(!state.servers.length)await loadServers();setModeration(moderationAction);}
  if(state.page==='console'){if(!state.servers.length)await loadServers();await loadHelp();}
  if(state.page==='admins')await loadAdmins();
  if(state.page==='audit')await loadAudit();
  if(state.page==='evidence')await loadEvidence();
}
function empty(target,message){target.replaceChildren(node('div','empty-state',message));}
async function loadServers(){
  const data=await api('servers');state.servers=data.servers;
  $('#server-updated').textContent='更新于 '+new Date().toLocaleTimeString('zh-CN');
  const target=$('#server-list');target.replaceChildren();
  if(!data.servers.length)empty(target,'尚未配置服务器条目');
  for(const server of data.servers){
    const card=node('article','server');const head=node('div','server-head');head.append(node('h2','',server.name),node('span','badge '+(server.status==='online'?'live':''),server.status==='online'?'可连接':'状态未知'));
    const metric=node('div','server-metric',server.online===null?'—':String(server.online));metric.append(node('small','','在线玩家'));
    card.append(head,metric,node('p','muted',server.players||server.message||''));
    const actions=node('div','server-actions');
    if(has('console.save'))actions.append(action('保存',()=>prepare({kind:'console',server:server.id,command:'保存',reason:'面板手动保存'})));
    if(has('console.announce'))actions.append(action('中央公告',()=>{state.page='console';return page('console').then(()=>{$('#console-server').value=server.id;$('#console-form [name=command]').value='公告 ';$('#console-form [name=command]').focus();});}));
    if(has('server.lifecycle'))for(const [kind,label] of [['start','启动'],['restart','重启'],['stop','停止']])actions.append(action(label,()=>prepare({kind:'server.lifecycle',server:server.id,action:kind,reason:'面板手动'+label})));
    if(state.user?.roles.includes('owner'))for(const [kind,label] of [['force_restart','强制重启'],['force_stop','强制关闭']])actions.append(action(label,()=>prepare({kind:'server.lifecycle',server:server.id,action:kind,reason:'服主面板'+label})));
    card.append(actions);target.append(card);
  }
  const select=$('#console-server');const current=select.value;select.replaceChildren(...state.servers.map(server=>{const option=node('option','',server.name);option.value=server.id;return option;}));select.value=current||state.servers[0]?.id||'';
}
function action(label,handler){const button=node('button','',label);button.type='button';button.addEventListener('click',safe(()=>busy(button,handler)));return button;}
let onlineLoading=false;
async function onlineAccount(name){
  const result=await api('players?query='+encodeURIComponent(name));
  const matches=result.players.filter(player=>player.displayName.toLowerCase()===name.toLowerCase());
  if(matches.length!==1||result.total>result.players.length)throw new Error('无法唯一确认该玩家账号，请到玩家列表核对');
  return matches[0];
}
async function loadOnlinePlayers(){
  if(onlineLoading)return;onlineLoading=true;
  try{
    const result=await api('online-players');if(state.page!=='online')return;
    const target=$('#online-list');target.replaceChildren();
    $('#online-updated').textContent='已确认 '+result.players.length+' 人在线 · '+new Date().toLocaleTimeString('zh-CN')+(result.complete?'':' · 部分服务器状态未知');
    for(const server of result.servers){
      const section=node('section','online-server'),heading=node('div','server-head');
      heading.append(node('h2','',server.name),node('span','badge '+(server.status==='online'?'live':''),server.online===null?'状态未知':server.online+' 人在线'));section.append(heading);
      if(server.status!=='online')section.append(node('p','muted',server.message||'暂时无法读取在线名单'));
      const rows=result.players.filter(player=>player.server===server.id);
      if(server.status==='online'&&!rows.length)section.append(node('p','muted','当前无人在线'));
      for(const player of rows){
        const row=node('article','online-player'),actions=node('div','moderation-actions');row.append(node('strong','',player.name));
        if(has('players.assets'))actions.append(action('查看仓库',async()=>{const account=await onlineAccount(player.name);await page('players');await selectPlayer(account);}));
        if(has('players.orders'))actions.append(action('清理问题订单',async()=>openProblemOrders(await onlineAccount(player.name))));
        if(has('grant.items')||has('grant.money')||has('grant.entitlements'))actions.append(action('发邮件',async()=>{const account=await onlineAccount(player.name);await page('players');await selectPlayer(account);await page('grants');}));
        for(const [kind,label,permission] of [['ban','封禁','console.ban'],['warn','警告','console.warn'],['voice','语音禁言','console.voice']])if(has(permission))actions.append(action(label,async()=>{await page('moderation');setModeration(kind);$('#moderation-form [name=player]').value=player.name;}));
        if(has('players.check')&&has('evidence.read'))actions.append(action('检查该玩家',async()=>{const account=await onlineAccount(player.name);const detail=await api('player?accountId='+encodeURIComponent(account.accountId));await openPlayerCheck({...detail,server:player.server});}));
        row.append(actions);section.append(row);
      }
      target.append(section);
    }
  }finally{onlineLoading=false;}
}
setInterval(()=>{if(state.user&&state.page==='online'&&!document.hidden)loadOnlinePlayers().catch(e=>error(e.message));},15000);
async function loadPlayers(){
  const data=await api('players?query='+encodeURIComponent(state.playerQuery)+'&offset='+state.playerOffset);state.players=data.players;state.playerTotal=data.total;
  const target=$('#player-list');target.replaceChildren();
  if(!data.players.length)empty(target,'没有找到玩家');
  for(const player of data.players){
    const button=node('button','player-row'+(state.selectedPlayer?.accountId===player.accountId?' active':''));button.type='button';button.append(node('strong','',player.displayName),node('small','',format(player.balance)+' 哈弗币 · 待交付 '+player.pendingDeliveries));
    button.addEventListener('click',safe(()=>selectPlayer(player)));target.append(button);
  }
  $('#players-prev').disabled=state.playerOffset===0;$('#players-next').disabled=state.playerOffset+30>=data.total;$('#player-page').textContent=(state.playerOffset+1)+' / '+data.total;
}
async function selectPlayer(player){
  state.selectedPlayer=player;const option=node('option','',player.displayName);option.value=player.accountId;$('#grant-target').replaceChildren(option);
  if(!has('players.assets')){empty($('#player-detail'),'当前权限仅能查看公开资料和待交付数量');return;}
  await loadProductIcons();const detail=await api('player?accountId='+encodeURIComponent(player.accountId));
  if(state.selectedPlayer?.accountId!==detail.accountId)return;
  const target=$('#player-detail');target.replaceChildren();
  const head=node('div','detail-title');head.append(node('h2','',detail.displayName));
  if(has('players.orders'))head.append(action('清理问题订单',()=>openProblemOrders(detail)));
  if(has('grant.items')||has('grant.money'))head.append(action('给该玩家发邮件',()=>page('grants')));
  target.append(head,node('p','uuid',detail.accountId));
  const management=node('div','moderation-actions');for(const [kind,label] of [['ban','封禁'],['warn','警告'],['voice','语音禁言']])management.append(action(label,()=>page('moderation').then(()=>{setModeration(kind);$('#moderation-form [name=player]').value=detail.displayName;})));if(has('players.check')&&has('evidence.read'))management.append(action('检查该玩家',()=>openPlayerCheck(detail)));target.append(management);
  const stats=node('div','metrics');for(const [label,value] of [['哈弗币',detail.balance],['主仓库参考价值',detail.warehouse.referenceValue],['待交付',detail.pendingDeliveries]]){const part=node('div');part.append(node('span','',label),node('strong','',format(value)));stats.append(part);}target.append(stats);
  const tabs=node('div','storage-tabs');const view=node('div','warehouse-wrap');target.append(tabs,view);
  const draw=(storage)=>{
    const columns=Math.max(1,storage.columns),rows=Math.ceil(storage.capacity/columns),grid=node('div','warehouse-grid');
    grid.style.setProperty('--columns',columns);grid.style.gridTemplateColumns='repeat('+columns+',minmax(0,1fr))';grid.style.gridTemplateRows='repeat('+rows+',minmax(0,1fr))';grid.style.aspectRatio=columns+' / '+rows;grid.style.minWidth=(columns*52)+'px';grid.style.backgroundSize=(100/columns)+'% '+(100/rows)+'%';
    for(let index=storage.capacity;index<columns*rows;index++){const blocked=node('div','warehouse-unavailable');blocked.style.gridColumn=(index%columns+1);blocked.style.gridRow=(Math.floor(index/columns)+1);blocked.title='未解锁格子';grid.append(blocked);}
    for(const item of storage.items){
      if(item.x<0||item.y<0)continue;
      const cell=node('div','warehouse-item');cell.dataset.rarity=item.rarity;
      cell.style.gridColumn=(item.x+1)+' / span '+item.width;cell.style.gridRow=(item.y+1)+' / span '+item.height;
      cell.title=item.name+' × '+item.count+'\n'+item.sku+'\nUID: '+item.uid;cell.append(productIcon(item),node('span','',item.name),node('b','',item.count));grid.append(cell);
    }
    view.replaceChildren(grid);
  };
  tabs.append(action('主仓库',()=>draw(detail.warehouse)));for(const box of detail.boxes)tabs.append(action(box.shell.name,()=>draw(box)));draw(detail.warehouse);
  target.append(node('p','muted','格子按服务器实际坐标与尺寸显示。参考价值按目录基础价格计算。'));
  target.append(node('h2','','待交付记录'));if(!detail.deliveries.length)target.append(node('p','muted','没有待交付物品'));for(const delivery of detail.deliveries)target.append(dataRow(delivery.item?.name||delivery.deliveryId,delivery.status,delivery.deliveryId));
  target.append(node('h2','history-title','最近邮件'));for(const mail of detail.mails)target.append(dataRow(mail.subject,format(mail.currency)+' 哈弗币 · '+mail.attachmentCount+' 件附件',mail.status+' · '+date(mail.createdAt)));
}
function dataRow(title,value,subtitle){const row=node('div','data-row');const body=node('div');body.append(node('strong','',title));if(subtitle)body.append(node('small','',subtitle));row.append(body,node('span','',value));return row;}
async function loadCatalog(){
  await loadProductIcons();
  const rights=$('#grant-form [name=kind]').value==='grant.entitlements';let data;
  if(rights){const raw=await api('entitlements');const rows=raw.items.filter(row=>(row.name+' '+row.id).toLowerCase().includes(state.catalogQuery.toLowerCase()));data={total:rows.length,items:rows.slice(state.catalogOffset,state.catalogOffset+30).map(row=>({...row,sku:row.id}))};}
  else data=await api('catalog?query='+encodeURIComponent(state.catalogQuery)+'&offset='+state.catalogOffset);state.catalogTotal=data.total;
  const target=$('#catalog-list');target.replaceChildren();if(!data.items.length)empty(target,'未找到商品');
  for(const product of data.items){const button=node('button','product');button.type='button';button.append(productIcon(product),node('strong','',product.name),node('small','',rights?({melee:'刀具',secure:'安全箱',keycase:'钥匙包'}[product.category]+' · 领取解锁使用'):product.rarity+' · '+format(product.referencePrice)+' 哈弗币'),node('small','',product.sku));button.addEventListener('click',()=>{document.querySelectorAll('.product').forEach(row=>row.classList.remove('selected'));button.classList.add('selected');$('#grant-form [name=sku]').value=product.sku;$('#grant-form [name=productName]').value=product.name;});target.append(button);}
  $('#catalog-prev').disabled=state.catalogOffset===0;$('#catalog-next').disabled=state.catalogOffset+30>=data.total;$('#catalog-page').textContent=(state.catalogOffset+1)+' / '+data.total;
}
async function loadOperations(){
  const data=await api('operations');const target=$('#operations');target.replaceChildren();
  const labels={PREVIEW:'待确认',RUNNING:'处理中',SUCCESS:'成功',FAILED:'失败',UNCERTAIN:'待核对',CANCELLED:'已取消'};
  for(const op of data.operations){const row=dataRow(op.summary,labels[op.status]||op.status,op.id);if(op.status==='UNCERTAIN')row.append(action('核对原操作',()=>execute(op.id)));target.append(row);}
  if(!data.operations.length)empty(target,'还没有发放记录');
}
async function prepare(data){
  const result=await api('prepare',data);state.operation=result;
  $('#confirm-summary').textContent=result.summary;$('#confirm-note').textContent=result.delivery==='MAIL'?'确认后发送邮件；预览有效期 60 秒。':'预览有效期 60 秒；断线后请先核对执行结果。';$('#confirm-error').textContent='';$('#confirm-dialog').showModal();
}
async function openProblemOrders(player){
  if($('#problem-orders-dialog'))return;
  const dialog=node('dialog'),title=node('h2','',player.displayName+' · 清理问题订单');dialog.append(title,node('p','muted','选择订单查看处理状态。清理会删除待交付物品，不退款，已领取物品保留。'));
  dialog.id='problem-orders-dialog';const content=node('div');dialog.append(content);
  async function load(){
    content.replaceChildren(node('p','muted','正在查询主城问题订单…'));
    try{
      const data=await api('problem-orders?accountId='+encodeURIComponent(player.accountId));
      if(!dialog.isConnected)return;
      content.replaceChildren();
      if(!data.orders.length)content.append(node('p','muted','没有待恢复的配装订单'));
      for(const order of data.orders){
        const row=dataRow(order.summary,format(order.price)+' 哈弗币',order.orderId+' · 主城');
        row.classList.add('problem-order-row');
        const detail=node('div','problem-order-detail');detail.hidden=true;
        detail.append(node('p','',order.clearable?'这笔订单可以清理。确认前不会删除任何物品。':(order.blockedReason||'这笔订单受托管保护，暂不能直接清理。需先核对旧装备、装配记录及付款状态。')));
        row.append(action('选择这笔订单',()=>{for(const other of content.querySelectorAll('.problem-order-detail'))other.hidden=true;detail.hidden=false;detail.focus();}));
        detail.tabIndex=-1;
        if(order.clearable){
          const failure=node('small','');failure.hidden=true;failure.setAttribute('role','alert');
          detail.append(action('清理这笔订单',async()=>{try{await prepare({kind:'players.orders',accountId:player.accountId,server:order.server,orderId:order.orderId,reason:'管理员清理玩家问题订单'});dialog.close();dialog.remove();}catch(e){failure.textContent=e.message;failure.hidden=false;throw e;}}),failure);
        }
        else detail.append(node('button','','暂不能直接清理'));
        if(!order.clearable)detail.querySelector('button').disabled=true;
        row.append(detail);
        content.append(row);
      }
    }catch(e){
      if(!dialog.isConnected)return;
      const message=node('p','',e.message);message.setAttribute('role','alert');
      content.replaceChildren(message,action('重试',load));
    }
  }
  dialog.append(action('关闭',()=>{dialog.close();dialog.remove();}));dialog.addEventListener('cancel',()=>dialog.remove());document.body.append(dialog);dialog.showModal();
  await load();
}
async function execute(id){
  const result=await api('confirm',{operationId:id});
  if(result.output!==undefined){$('#console-output').textContent+='\n\n['+new Date().toLocaleTimeString('zh-CN')+']\n'+result.output;}
  status(result.message||'操作已完成');
  if(state.page==='grants')await loadOperations();if(state.page==='servers')await loadServers();return result;
}
let commands=[];
async function loadHelp(){commands=(await api('console-help')).commands;renderHelp();}
function renderHelp(){
 const query=$('#command-search').value.toLowerCase(),target=$('#console-help');target.replaceChildren();
 for(const command of commands.filter(c=>(c.zh+' '+c.en+' '+c.description).toLowerCase().includes(query))){
  const button=action(command.zh+' / '+command.en,()=>selectCommand(command));button.title=command.description;target.append(button);
 }
}
function selectCommand(command){
 const target=$('#command-parameters'),form=node('form','form-panel');form.append(node('h3','',command.zh),node('p','muted',command.description));
 for(const parameter of command.parameters||[]){const label=node('label','',parameter.name),input=node('input');input.name=parameter.name;input.type=parameter.type==='number'?'number':'text';input.value=parameter.default||'';input.required=!parameter.optional;input.maxLength=140;if(parameter.type==='player')input.setAttribute('list','command-player-names');label.append(input);form.append(label);}
 const use=node('button','primary','填入执行台');use.type='submit';form.append(use);
 form.addEventListener('submit',event=>{event.preventDefault();const fields=Object.fromEntries(new FormData(form));$('#console-form [name=command]').value=command.template.replace(/\{([^}]+)\}/g,(_,name)=>fields[name]||'').trim();$('#console-form [name=command]').focus();});target.replaceChildren(form);
 let datalist=$('#command-player-names');if(!datalist){datalist=node('datalist');datalist.id='command-player-names';document.body.append(datalist);}
 datalist.replaceChildren(...playerNames().map(name=>{const option=node('option');option.value=name;return option;}));
}
function playerNames(){
 const names=state.players.map(p=>p.displayName);
 for(const server of state.servers){const list=(server.players||'').split(':').slice(1).join(':');names.push(...list.split(',').map(x=>x.trim()).filter(x=>/^[A-Za-z0-9_\u3400-\u4dbf\u4e00-\u9fff]{1,16}$/.test(x)));}
 return [...new Set(names)];
}
$('#command-search').addEventListener('input',renderHelp);
$('#command-help-live').addEventListener('click',safe(async()=>{const value=await api('console-guide',{server:$('#console-server').value,page:Number($('#command-help-page').value)});$('#command-help-output').textContent=value.output;}));
$('#console-form [name=command]').addEventListener('keydown',event=>{
 if(event.key!=='Tab'||event.shiftKey)return;
 const input=event.target,before=input.value.slice(0,input.selectionStart),parts=before.split(/\s+/),prefix=parts.pop(),options=parts.length?playerNames():commands.flatMap(c=>[c.en.split(' ')[0],c.zh.split(' ')[0]]);
 const matches=[...new Set(options)].filter(x=>x.toLowerCase().startsWith(prefix.toLowerCase()));
 if(!matches.length)return;event.preventDefault();input.setRangeText(matches[0]+(parts.length?'':' '),before.length-prefix.length,input.selectionStart,'end');status(matches.length>1?'可选：'+matches.join(' / '):'已补齐');
});
let evidenceOffset=0,evidenceTotal=0,evidenceFilters={};
async function loadEvidence(){
 const params=new URLSearchParams({...evidenceFilters,offset:evidenceOffset});const data=await api('evidence?'+params);evidenceTotal=data.total;
 const target=$('#evidence-list');target.replaceChildren();
 for(const shot of data.screenshots){const button=action(shot.player+' · '+date(shot.received_at),async()=>{
  const item=await api('evidence-detail?nonce='+encodeURIComponent(shot.nonce));const detail=$('#evidence-detail');detail.replaceChildren(node('h2','',item.player),node('p','',date(item.received_at)+' · '+item.server+' · '+item.reason),node('p','uuid','SHA256: '+item.sha256));
  const image=node('img','evidence-image');image.alt=item.player+' 的游戏截图';image.src='data:image/png;base64,'+item.image;detail.append(image,node('p','muted',item.width+' × '+item.height+' · 证据编号 '+item.nonce));
 });button.classList.add('player-row');target.append(button);}
 if(!data.screenshots.length)empty(target,'没有符合条件的截图');$('#evidence-page').textContent=(evidenceOffset+1)+' / '+evidenceTotal;$('#evidence-prev').disabled=evidenceOffset===0;$('#evidence-next').disabled=evidenceOffset+30>=evidenceTotal;
}
$('#evidence-search').addEventListener('submit',safe(async event=>{event.preventDefault();const values=Object.fromEntries(new FormData(event.target));evidenceFilters={player:values.player};for(const key of ['from','to'])if(values[key])evidenceFilters[key]=Math.floor(new Date(values[key]).getTime()/1000);evidenceOffset=0;await loadEvidence();}));
for(const [id,step] of [['evidence-prev',-30],['evidence-next',30]])$('#'+id).addEventListener('click',safe(async()=>{evidenceOffset=Math.max(0,evidenceOffset+step);await loadEvidence();}));
async function loadAdmins(){
  const data=await api('users');const target=$('#admin-list');target.replaceChildren();
  const identitySelect=(value)=>{const select=node('select');select.name='roles';select.setAttribute('aria-label','管理员身份');for(const [role,label] of Object.entries(roles)){const option=node('option','',label);option.value=role;select.append(option);}select.value=value;return select;};
  for(const admin of data.users){const row=dataRow(admin.username,admin.disabled?'已停用':'可登录',admin.uuid);row.classList.add('admin-row');const select=identitySelect(admin.roles[0]);row.append(select);row.append(action('保存身份',async()=>{await api('user-update',{uuid:admin.uuid,roles:[select.value],disabled:admin.disabled});status('身份已保存，目标账号需重新登录');await loadAdmins();}));row.append(action(admin.disabled?'启用':'停用',async()=>{await api('user-update',{uuid:admin.uuid,roles:[select.value],disabled:!admin.disabled});await loadAdmins();}));target.append(row);}
  const picker=$('#admin-role-picker');picker.replaceChildren(identitySelect('admin'));
}
async function loadAudit(){
  const data=await api('audit');const target=$('#audit-list');target.replaceChildren();
  for(const event of data.events){const details=JSON.parse(event.details);target.append(dataRow(event.action,event.result,date(event.time)+' · '+event.actor+' · '+(details.summary||details.accountId||details.operationId||details.username||'')));}
  if(!data.events.length)empty(target,'暂无操作记录');
}
$('#login-form').addEventListener('submit',async event=>{event.preventDefault();const form=event.target,button=form.querySelector('button');try{const data=Object.fromEntries(new FormData(form));const session=await busy(button,()=>api('login',data));form.password.value='';$('#login-error').textContent='';await enter(session);}catch(e){$('#login-error').textContent=e.message;}});
document.querySelectorAll('nav button[data-page]').forEach(button=>button.addEventListener('click',safe(()=>page(button.dataset.page))));
$('#refresh').addEventListener('click',safe(()=>busy($('#refresh'),refresh)));
$('#logout').addEventListener('click',safe(async()=>{await api('logout',{});showLogin();}));
$('#player-search').addEventListener('submit',safe(async event=>{event.preventDefault();state.playerQuery=new FormData(event.target).get('query');state.playerOffset=0;await loadPlayers();}));
$('#catalog-search').addEventListener('submit',safe(async event=>{event.preventDefault();state.catalogQuery=new FormData(event.target).get('query');state.catalogOffset=0;await loadCatalog();}));
for(const [id,step,kind] of [['players-prev',-30,'player'],['players-next',30,'player'],['catalog-prev',-30,'catalog'],['catalog-next',30,'catalog']])$('#'+id).addEventListener('click',safe(async()=>{state[kind+'Offset']=Math.max(0,state[kind+'Offset']+step);await(kind==='player'?loadPlayers():loadCatalog());}));
$('#grant-form [name=kind]').addEventListener('change',safe(async event=>{const money=event.target.value==='grant.money';$('#amount-label').hidden=!money;$('#selected-product-label').hidden=money;$('#count-label').hidden=money||event.target.value==='grant.entitlements';$('#duration-label').hidden=event.target.value!=='grant.entitlements';$('#grant-form [name=sku]').value='';$('#grant-form [name=productName]').value='';state.catalogOffset=0;state.catalogQuery='';$('#catalog-search [name=query]').value='';await loadCatalog();}));
$('#grant-form').addEventListener('submit',safe(async event=>{event.preventDefault();const data=Object.fromEntries(new FormData(event.target));data.count=Number(data.count);data.durationSeconds=data.kind==='grant.entitlements'&&data.durationMode==='limited'?Number(data.durationMinutes)*60:0;delete data.durationMode;delete data.durationMinutes;delete data.productName;await busy(event.target.querySelector('button[type=submit]'),()=>prepare(data));}));
$('#console-form').addEventListener('submit',safe(async event=>{event.preventDefault();const data=Object.fromEntries(new FormData(event.target));if(['在线人数','list'].includes(data.command.trim())){const result=await api('console-read',data);$('#console-output').textContent+='\n\n'+result.output;}else await prepare({kind:'console',...data});}));
$('#console-clear').addEventListener('click',()=>$('#console-output').textContent='等待执行指令…');
$('#admin-form').addEventListener('submit',safe(async event=>{event.preventDefault();const form=event.target,fields=new FormData(form),data=Object.fromEntries(fields);data.roles=fields.getAll('roles');await busy(form.querySelector('button[type=submit]'),()=>api('user-create',data));form.reset();status('管理员已创建');await loadAdmins();}));
$('#confirm-execute').addEventListener('click',async()=>{const button=$('#confirm-execute');try{await busy(button,()=>execute(state.operation.operationId));$('#confirm-dialog').close('done');state.operation=null;}catch(e){$('#confirm-error').textContent=e.message;}});
$('#confirm-dialog').addEventListener('close',safe(async()=>{if(state.operation){const operation=state.operation;state.operation=null;try{await api('cancel',{operationId:operation.operationId});}catch{/* Already submitted or expired; keep its original receipt. */}}}));
api('session').then(enter).catch(showLogin);
window.breaklineLegacy={enter,page,prepare,pause:()=>{state.page='';}};
$('#nav-search').addEventListener('input', event => {
 const query=event.target.value.trim().toLowerCase();
 document.querySelectorAll('nav button[data-page]').forEach(button=>button.style.display=button.textContent.toLowerCase().includes(query)?'':'none');
});
document.addEventListener('keydown', event => {
 if ((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==='k'&&!$('#app').hidden&&$('#nav-search').getClientRects().length>0) {
  event.preventDefault();$('#nav-search').focus();
 }
});

let moderationAction='announce';
function setModeration(kind){
 moderationAction=kind;
 const all=node('option','','全部服务器');all.value='all';$('#moderation-server').replaceChildren(all);
 if(kind==='announce')for(const server of state.servers){const option=node('option','',server.name);option.value=server.id;$('#moderation-server').append(option);}
 const labels={announce:'屏幕广播',ban:'封禁',pardon:'解封',warn:'警告',voice:'语音禁言'};
 $('#moderation-title').textContent=labels[kind];
 document.querySelectorAll('[data-mod-action]').forEach(button=>button.classList.toggle('primary',button.dataset.modAction===kind));
 $('#moderation-player-label').hidden=kind==='announce';
 $('#moderation-form [name=player]').required=kind!=='announce';
 const permission=kind==='voice'?'console.voice':kind==='announce'?'console.announce':kind==='warn'?'console.warn':'console.ban';
 $('#moderation-submit').disabled=!has(permission);
 $('#voice-action-label').hidden=kind!=='voice';$('#voice-duration-label').hidden=kind!=='voice';
 $('#moderation-note').textContent=kind==='voice'?'应用到全部四个服务器，禁言后仍可听到其他玩家。':!has(permission)?'当前账号没有此操作权限。':kind==='ban'||kind==='pardon'?'同步执行到全部服务器。':kind==='announce'?'可选择全部服务器或指定服务器，显示中央字幕。':'发送至全部服务器，显示中央字幕。';
}
document.querySelectorAll('[data-mod-action]').forEach(button=>button.addEventListener('click',()=>setModeration(button.dataset.modAction)));
$('#moderation-form').addEventListener('submit',safe(async event=>{
 event.preventDefault();
 const data=Object.fromEntries(new FormData(event.target));
 if(moderationAction!=='announce'&&!/^[A-Za-z0-9_\u3400-\u4dbf\u4e00-\u9fff]{1,16}$/.test(data.player))throw new Error('请输入有效角色名');
 if(moderationAction==='voice'){await busy($('#moderation-submit'),()=>prepare({kind:'moderation.voice',player:data.player,action:data.voiceAction,durationSeconds:Number(data.voiceDuration),reason:data.message}));return;}
 const command=moderationAction==='announce'?'公告 '+data.message:moderationAction+' '+data.player+' '+data.message;
 await busy($('#moderation-submit'),()=>prepare({kind:'console',server:data.server,command,reason:data.message}));
}));

let productIcons=null;
async function loadProductIcons(){if(productIcons)return;try{const r=await fetch('/icons/index.json',{credentials:'same-origin',cache:'no-store'});if(!r.ok)throw Error('icons');productIcons=await r.json();}catch{productIcons={};}}
function productIcon(product){
 const frame=node('span','product-icon');const keys=[product.sku,product.id,product.itemId];const icon=keys.map(k=>productIcons?.[k]).find(v=>typeof v==='string'&&/^[a-f0-9]{64}\.png$/.test(v));
 if(icon){const image=node('img');image.src='/icons/'+icon;image.alt='';image.loading='lazy';image.addEventListener('error',()=>{image.remove();frame.textContent='暂无图标';},{once:true});frame.append(image);}else{frame.textContent='暂无图标';frame.classList.add('missing');}return frame;
}
const checkView={generation:0,player:null,timer:null};
function cancelPlayerCheck(){checkView.generation++;clearTimeout(checkView.timer);checkView.timer=null;}
function closePlayerCheck(){cancelPlayerCheck();$('#player-check-dialog').close();}
$('#check-close').addEventListener('click',closePlayerCheck);
$('#player-check-dialog').addEventListener('cancel',cancelPlayerCheck);
$('#player-check-dialog').addEventListener('close',cancelPlayerCheck);
$('#player-check-form').addEventListener('submit',safe(async e=>{e.preventDefault();await runPlayerCheck();}));
async function openPlayerCheck(player){
 cancelPlayerCheck();checkView.player={accountId:player.accountId,displayName:player.displayName};$('#check-player-name').textContent='检查 '+player.displayName;
 if(!state.servers.length)await loadServers();$('#check-server').replaceChildren(...state.servers.map(server=>{const option=node('option','',server.name);option.value=server.id;return option;}));
 if(player.server)$('#check-server').value=player.server;
 else{const roster=await api('online-players');const matches=roster.players.filter(row=>row.name.toLowerCase()===player.displayName.toLowerCase());if(matches.length===1)$('#check-server').value=matches[0].server;}
 $('#player-check-dialog').showModal();await runPlayerCheck();
}
function conciseLine(line){if(typeof line!=='string')return '';const s=line.trim();return /https?:|Bearer |token|secret|password|nonce|sha256|[a-f0-9]{32,}|Exception|Traceback|\bat [\w.]+\(/i.test(s)?'':s.slice(0,120);}
function renderPlayerChecks(result){
 const target=$('#check-projects');target.replaceChildren();const projects={aim:'瞄准',aiming:'瞄准',movement:'移动',move:'移动',integrity:'客户端完整性',client_integrity:'客户端完整性',programs:'异常程序',process:'异常程序',processes:'异常程序',abnormal_process:'异常程序',service:'检查服务',services:'检查服务',service_status:'检查服务'};
 for(const entry of result.checks||[]){const label=projects[entry.id]||conciseLine(entry.label)||'检查项目';const box=node('section','check-result');box.append(node('h3','',label));const lines=(entry.lines||[]).map(conciseLine).filter(Boolean).slice(0,3);box.append(node('p','muted',lines.join('；')||(entry.status==='unavailable'?'未获取足够数据':label==='检查服务'?'检查服务正常':entry.flagged?'有待复核记录':'暂未发现记录（不等于无作弊）')));target.append(box);}
 $('#check-conclusion').textContent=result.conclusion==='review'?'发现待复核记录':result.conclusion==='unavailable'?'检查数据不足':'暂未发现记录（不等于无作弊）';
}
function renderCheckShot(shot){
 $('#check-shot-status').textContent=shot.status==='received'?'已收到 '+(shot.player||checkView.player.displayName)+' 的游戏截图 · '+date((shot.received_at||0)*1000):shot.status==='timeout'?'未收到游戏截图，可稍后重新检查':shot.status==='unavailable'?(conciseLine(shot.message)||'当前无法获取该玩家截图'):shot.message||'正在等待游戏截图';
 const target=$('#check-shot-image');target.replaceChildren();if(shot.status==='received'&&typeof shot.image==='string'&&shot.image.length<3000000&&/^[A-Za-z0-9+/=]+$/.test(shot.image)){const image=node('img','check-image');image.src='data:image/png;base64,'+shot.image;image.alt=(shot.player||checkView.player.displayName)+' 的游戏窗口截图';target.append(image);}
}
async function runPlayerCheck(){
 cancelPlayerCheck();const generation=checkView.generation,player={...checkView.player},server=$('#check-server').value;$('#check-conclusion').textContent='正在检查';$('#check-projects').replaceChildren();$('#check-shot-image').replaceChildren();$('#check-shot-status').textContent='正在请求游戏截图';
 try{const result=await api('player-check',{accountId:player.accountId,server});if(generation!==checkView.generation||!$('#player-check-dialog').open)return;renderPlayerChecks(result);renderCheckShot(result.screenshot||{status:'unavailable'});$('#check-note').textContent='检查对象：'+player.displayName+' · '+(state.servers.find(s=>s.id===server)?.name||server)+' · 需人工复核。';
  const deadline=Date.now()+90000;const poll=async()=>{if(generation!==checkView.generation||!$('#player-check-dialog').open)return;try{const update=await api('player-check-result?checkId='+encodeURIComponent(result.checkId));if(generation!==checkView.generation)return;renderCheckShot(update.screenshot);if(update.screenshot.status==='pending'&&Date.now()<deadline)checkView.timer=setTimeout(poll,3000);else if(update.screenshot.status==='pending')renderCheckShot({status:'timeout'});}catch(e){if(generation===checkView.generation)$('#check-shot-status').textContent=e.message;}};
  if(result.screenshot?.status==='pending')checkView.timer=setTimeout(poll,3000);
 }catch(e){if(generation===checkView.generation){$('#check-conclusion').textContent='检查数据不足';$('#check-note').textContent=e.message;$('#check-shot-status').textContent='未发起截图或截图服务不可用';}}
}
