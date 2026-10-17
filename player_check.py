"""Actor-bound inspection receipts. Only trusted, fixed RCON commands and exact screenshot nonces."""
import json
import re
import time
import uuid
from control import ControlError

LABELS = {'aim': '瞄准', 'move': '移动', 'integrity': '客户端完整性', 'programs': '异常程序'}


def start(panel, user, data):
    player = panel.dc.player(data.get('accountId'))
    name = player.get('displayName', '')
    if not isinstance(name,str) or not re.fullmatch('[A-Za-z0-9_\u3400-\u4dbf\u4e00-\u9fff]{1,16}', name):
        raise ControlError('INVALID_PLAYER','角色名无效，无法检查')
    server = data.get('server')
    panel.servers.entry(server)
    now = int(time.time())
    check_id = str(uuid.uuid4())
    with panel.db() as conn:
        conn.execute('BEGIN IMMEDIATE')
        conn.execute('DELETE FROM player_checks WHERE created<?',(now-600,))
        recent = conn.execute('SELECT id FROM player_checks WHERE player=? AND created>?',(name,now-30)).fetchone()
        if recent:
            raise ControlError('CHECK_COOLDOWN','该玩家刚检查过，请稍等 30 秒',429)
        if conn.execute('SELECT COUNT(*) FROM player_checks WHERE created>?',(now-30,)).fetchone()[0] >= 12:
            raise ControlError('CHECK_BUSY','检查请求较多，请稍后再试',429)
        conn.execute('INSERT INTO player_checks(id,actor,player,server,created,report) VALUES(?,?,?,?,?,?)',
                     (check_id,user['id'],name,server,now,'{}'))
    report = dict(checkId=check_id,player=name,server=server,createdAt=now,checks=[],conclusion='unavailable',
                  screenshot=dict(status='unavailable',message='尚未请求截图'))
    try:
        output = panel.servers.command(server,'mugenac panel '+name)
        marker = re.search(r'MUGEN_PANEL_V1 (\{[^\r\n]+\})',output)
        if not marker:
            raise ControlError('CHECK_NOT_READY','该服尚未支持面板检查，或玩家不在该服',503)
        parsed = json.loads(marker[1])
        if parsed.get('player','').casefold() != name.casefold():
            raise ControlError('CHECK_MISMATCH','检查回执玩家不匹配',503)
        rows = parsed.get('checks',[])
        if not isinstance(rows,list) or len(rows)!=4 or {r.get('id') for r in rows} != set(LABELS):
            raise ControlError('CHECK_INVALID','检查回执无效',503)
        for row in rows:
            if row.get('status') not in ('available','unavailable') or type(row.get('flagged')) is not bool or not isinstance(row.get('lines'),list) or len(row['lines'])>3:
                raise ControlError('CHECK_INVALID','检查回执无效',503)
            if any(not isinstance(line,str) or len(line)>160 for line in row['lines']):
                raise ControlError('CHECK_INVALID','检查回执无效',503)
            row['label']=LABELS[row['id']]
        rows=[dict(id=r['id'],label=LABELS[r['id']],status=r['status'],flagged=r['flagged'],lines=r['lines']) for r in rows]
        report['checks']=rows
        report['conclusion']='review' if any(r['flagged'] for r in rows) else 'unavailable' if any(r['status']=='unavailable' for r in rows) else 'no_records'
        report['notice']='仅复核现有记录；异常需人工确认，不能据此排除作弊。'
        origin = parsed.get('captureServer','')
        if not isinstance(origin,str) or not re.fullmatch('[A-Za-z0-9_-]{1,64}',origin):
            raise ControlError('CAPTURE_SOURCE_INVALID','截图来源尚未接线',503)
        # The command sends a signed challenge over the existing game-to-launcher bridge.
        output = panel.servers.command(server,'mugenac capture '+name+' manual')
        receipt = re.search(r'MUGEN_CAPTURE_V1 ([0-9a-f]{32})(?![0-9a-f])',output)
        if receipt:
            report['screenshot']=dict(status='pending',message='已请求游戏窗口截图，等待上传')
            with panel.db() as conn:
                conn.execute('UPDATE player_checks SET nonce=?,capture_server=? WHERE id=?',(receipt[1],origin,check_id))
        else:
            report['screenshot']=dict(status='unavailable',message='截图未发出：玩家离线、采集未启用或仍在冷却')
    except ControlError as error:
        report['screenshot']=dict(status='unavailable',message=str(error))
        if not report['checks']:
            report['checks']=[dict(id=k,label=v,status='unavailable',flagged=False,lines=['未获取数据']) for k,v in LABELS.items()]
            report['notice']=str(error)
    except (ValueError,TypeError,KeyError):
        report['screenshot']=dict(status='unavailable',message='检查回执无效，未采用结果')
        report['checks']=[];report['conclusion']='unavailable'
    with panel.db() as conn:
        conn.execute('UPDATE player_checks SET report=? WHERE id=?',(json.dumps(report,ensure_ascii=False),check_id))
    panel.audit(user['id'],'PLAYER_CHECK',dict(checkId=check_id,player=name,server=server),'SUCCESS' if report['checks'] else 'UNAVAILABLE')
    return report


def result(panel,user,check_id):
    with panel.db() as conn:
        row = conn.execute('SELECT * FROM player_checks WHERE id=? AND actor=?',(check_id,user['id'])).fetchone()
    if not row:
        raise ControlError('CHECK_NOT_FOUND','检查记录不存在',404)
    report = json.loads(row['report'])
    shot = report.get('screenshot',dict(status='pending',message='正在检查'))
    if row['nonce']:
        try:
            image=panel.evidence.fetch(dict(nonce=row['nonce']))
            if (image['player'].casefold()!=row['player'].casefold() or image['server']!=row['capture_server']
                    or image['reason']!='manual' or image['received_at']<row['created']-5):
                raise ControlError('CAPTURE_MISMATCH','截图来源不匹配，未展示',503)
            shot=dict(image,status='received',message='游戏窗口截图已收到')
        except ControlError as error:
            if error.code=='SCREENSHOT_NOT_FOUND':
                shot=dict(status='timeout' if time.time()-row['created']>90 else 'pending',
                          message='截图等待超时，可稍后重新检查' if time.time()-row['created']>90 else '等待截图上传')
            else:
                shot=dict(status='unavailable',message=str(error))
        except (KeyError,TypeError):
            shot=dict(status='unavailable',message='截图回执无效，未展示')
    return dict(checkId=check_id,screenshot=shot)
