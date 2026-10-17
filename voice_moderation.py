"""Four-server voice actions: canonical UUID from DC, immutable deadlines, checked receipts."""
import json,re,time,uuid
from control import ControlError
def prepare(panel,data):
 name=data.get('player','')
 if not isinstance(name,str) or not re.fullmatch('[A-Za-z0-9_\u3400-\u4dbf\u4e00-\u9fff]{1,16}',name):raise ControlError('INVALID_TARGET','请输入有效角色名',400)
 result=panel.dc.players(name);matches=[p for p in result['players'] if p['displayName'].casefold()==name.casefold()]
 if len(matches)!=1 or result.get('total',len(result['players']))>len(result['players']):raise ControlError('AMBIGUOUS_PLAYER','无法唯一确认玩家，请检查角色名',400)
 target=panel.dc.player(matches[0]['accountId']);raw=target.get('playerUuid')
 try:
  canonical=str(uuid.UUID(raw))
  if raw!=canonical:raise ValueError()
 except (ValueError,TypeError,AttributeError):raise ControlError('PLAYER_UUID_UNAVAILABLE','该玩家游戏身份尚未接线，未执行禁言',503)
 action=data.get('action','mute');duration=data.get('durationSeconds',0)
 if action not in ('mute','unmute') or type(duration) is not int or duration!=0 and not 60<=duration<=31536000:raise ControlError('INVALID_VOICE_ACTION','请选择解禁、永久禁言或1分钟至365天',400)
 until=(int(time.time()*1000)+duration*1000 if duration else 0) if action=='mute' else None
 payload=dict(uuid=canonical,player=target['displayName'],accountId=target['accountId'],action=action,expiresAt=until,servers=list(panel.servers.entries),reason=data['reason'])
 if len(payload['servers'])!=4:raise ControlError('VOICE_SERVERS_INCOMPLETE','全服禁言需要四个服务器都已接线',503)
 return payload,('解除 '+target['displayName']+' 的全服语音禁言' if action=='unmute' else '全服语音禁言 '+target['displayName']+'（'+(str(duration//60)+'分钟' if duration else '永久')+'）')
def execute(panel,payload,previous,save):
 receipt=previous or dict(servers={},message='');errors=[]
 for server in payload['servers']:
  if receipt['servers'].get(server,{}).get('ok') is True:continue
  try:
   command='deltavoice '+payload['action']+' '+payload['uuid']+(' '+str(payload['expiresAt']) if payload['action']=='mute' else '')
   output=panel.servers.command(server,command)
   match=re.search(r'DELTA_VOICE_V1 (\{[^\r\n]+\})',output);value=json.loads(match[1]) if match else {}
   if value.get('ok') is not True or value.get('uuid')!=payload['uuid'] or value.get('expiresAt')!=payload['expiresAt'] or value.get('muted')!=(payload['action']=='mute'):raise ControlError('VOICE_RECEIPT_MISMATCH','语音回执未确认：服务器可能尚未更新',503)
   receipt['servers'][server]=dict(ok=True,message='已禁言' if payload['action']=='mute' else '已解禁')
  except (ControlError,ValueError,TypeError) as ex:
   receipt['servers'][server]=dict(ok=False,message=str(ex));errors.append(server)
  save(receipt)
 if errors:
  receipt['message']='部分服务器未确认：'+ '、'.join(errors)+'；重试只处理未成功服务器，期限保持不变';save(receipt)
  raise ControlError('VOICE_PARTIAL',receipt['message'],503)
 receipt['message']='四个服务器均已确认'+('语音禁言' if payload['action']=='mute' else '解除语音禁言');return receipt
