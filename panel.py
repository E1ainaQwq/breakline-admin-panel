"""Breakline remote administration. Standard library only, private bridge credentials."""
import argparse
import contextlib
import getpass
import hashlib
import hmac
import ipaddress
import json
import os
import re
import secrets
import shlex
import sqlite3
import threading
import time
import uuid
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, parse_qs, quote
from decimal import Decimal
from evidence import Evidence
from control import ControlError, DataCenter, Servers
import player_check
import voice_moderation
from management import Management
import mimetypes

ROLES = {
    'admin': {'servers.read', 'players.read', 'players.assets', 'catalog.read',
              'grant.money', 'grant.items', 'grant.entitlements', 'console.announce', 'console.save',
              'console.map', 'console.warn', 'console.ban', 'console.voice', 'audit.read', 'evidence.read', 'players.check', 'players.orders'},
    'owner': {'*'},
}
LABELS = {'admin': '管理员', 'owner': '服主'}
COOKIE = 'breakline_admin'


def reject(code, message, status=400):
    raise ControlError(code, message, status)


def text(value, max_length=200):
    if not isinstance(value, str) or not value.strip() or len(value) > max_length or any(ord(c) < 32 for c in value):
        reject('INVALID_TEXT', '文本为空、过长或含控制字符')
    return value.strip()


def amount(value):
    if isinstance(value, bool):
        reject('INVALID_AMOUNT', '金额必须是正整数')
    raw = str(value).strip().lower()
    if raw == '1e':
        raw = '100000000'
    elif re.fullmatch(r'\d+(?:\.\d+)?亿', raw):
        raw = str(Decimal(raw[:-1]) * 100000000)
    if not re.fullmatch(r'\d+(?:\.0+)?', raw):
        reject('INVALID_AMOUNT', '金额必须为整数，可以填写 1亿 或 1e')
    result = int(Decimal(raw))
    if not 1 <= result <= 1_000_000_000:
        reject('INVALID_AMOUNT', '每次金额范围为 1～10亿')
    return result


def public_user(row):
    roles = json.loads(row['roles'])
    permissions = sorted(set().union(*(ROLES[role] for role in roles)))
    return {'uuid': row['id'], 'username': row['username'], 'roles': roles, 'permissions': permissions, 'disabled': bool(row['disabled'])}


def valid_roles(roles):
    return isinstance(roles, list) and len(roles) == 1 and isinstance(roles[0], str) and roles[0] in ROLES


def password_hash(password, salt):
    return hashlib.pbkdf2_hmac('sha256', password.encode(), salt, 310000).hex()


class Panel:
    def __init__(self, db_path, dc, servers, origin, evidence=None, management=None):
        self.db_path, self.dc, self.servers, self.origin = str(db_path), dc, servers, origin.rstrip('/')
        self.evidence = evidence or Evidence()
        self.secure = self.origin.startswith('https://')
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        with self.db() as conn:
            conn.executescript('''
              PRAGMA journal_mode=WAL;
              CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                salt TEXT NOT NULL, password TEXT NOT NULL, roles TEXT NOT NULL, disabled INTEGER NOT NULL DEFAULT 0);
              CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY, user_id TEXT NOT NULL, csrf TEXT NOT NULL, expires INTEGER NOT NULL);
              CREATE TABLE IF NOT EXISTS attempts(source TEXT NOT NULL, time INTEGER NOT NULL);
              CREATE INDEX IF NOT EXISTS attempts_source ON attempts(source,time);
              CREATE TABLE IF NOT EXISTS operations(id TEXT PRIMARY KEY, actor TEXT NOT NULL, permission TEXT NOT NULL,
                kind TEXT NOT NULL, payload TEXT NOT NULL, summary TEXT NOT NULL, status TEXT NOT NULL, expires INTEGER NOT NULL,
                receipt TEXT, lease INTEGER NOT NULL DEFAULT 0);
              CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY, time INTEGER NOT NULL, actor TEXT NOT NULL,
                action TEXT NOT NULL, details TEXT NOT NULL, result TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS player_checks(id TEXT PRIMARY KEY,actor TEXT NOT NULL,player TEXT NOT NULL,
                server TEXT NOT NULL,created INTEGER NOT NULL,report TEXT NOT NULL,nonce TEXT,capture_server TEXT);
            ''')
            if 'client' not in {row[1] for row in conn.execute('PRAGMA table_info(sessions)')}:
                conn.execute("ALTER TABLE sessions ADD COLUMN client TEXT NOT NULL DEFAULT ''")
                conn.execute('DELETE FROM sessions')
                conn.commit()
            # An old narrow role must not silently acquire the broader admin role.
            # Keep its account/password, revoke sessions and require owner review.
            conn.execute('BEGIN IMMEDIATE')
            for row in conn.execute('SELECT id,roles,disabled FROM users').fetchall():
                roles = json.loads(row['roles'])
                if valid_roles(roles):
                    continue
                updated = ['owner'] if 'owner' in roles else ['admin']
                disabled = int(row['disabled']) if 'owner' in roles else 1
                conn.execute('UPDATE users SET roles=?,disabled=? WHERE id=?', (json.dumps(updated), disabled, row['id']))
                conn.execute('DELETE FROM sessions WHERE user_id=?', (row['id'],))
                conn.execute('INSERT INTO audit(time,actor,action,details,result) VALUES(?,?,?,?,?)',
                    (int(time.time()), '@system', 'ROLE_SIMPLIFY',
                     json.dumps({'uuid': row['id'], 'previousRoles': roles, 'roles': updated, 'disabled': bool(disabled)}),
                     'OWNER_REVIEW_REQUIRED' if disabled else 'SUCCESS'))
        try:
            os.chmod(db_path, 0o600)
        except OSError:
            pass
        self.management = Management(self, management)
        self.workspace_default = False

    @contextlib.contextmanager
    def db(self):
        conn = sqlite3.connect(self.db_path, timeout=5)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def audit(self, actor, action, details, result):
        with self.db() as conn:
            conn.execute('INSERT INTO audit(time,actor,action,details,result) VALUES(?,?,?,?,?)',
                (int(time.time()), actor, action, json.dumps(details, ensure_ascii=False), result))

    def create_user(self, identity, username, password, roles, bootstrap=False):
        try:
            identity = str(uuid.UUID(identity))
        except (ValueError, TypeError, AttributeError):
            reject('INVALID_UUID', '必须绑定准确的管理员 UUID')
        if not re.fullmatch(r'[A-Za-z0-9_.-]{3,32}', username or ''):
            reject('INVALID_USERNAME', '账号使用 3～32 位英文、数字、点或下划线')
        if not isinstance(password, str) or not 12 <= len(password) <= 128:
            reject('WEAK_PASSWORD', '密码需为 12～128 位')
        if not valid_roles(roles):
            reject('INVALID_ROLES', '请选择管理员或服主，仅选择一种身份')
        if bootstrap and roles != ['owner']:
            reject('INVALID_ROLES', '首次初始化必须创建服主')
        salt = secrets.token_bytes(16)
        hashed = password_hash(password, salt)
        with self.db() as conn:
            conn.execute('BEGIN IMMEDIATE')
            if bootstrap and conn.execute("SELECT 1 FROM users WHERE disabled=0 AND roles LIKE '%\"owner\"%'").fetchone():
                reject('BOOTSTRAP_CLOSED', '已有服主；请由服主登录后添加管理员', 409)
            try:
                conn.execute('INSERT INTO users(id,username,salt,password,roles) VALUES(?,?,?,?,?)',
                    (identity, username, salt.hex(), hashed, json.dumps(sorted(set(roles)))))
            except sqlite3.IntegrityError:
                reject('USER_EXISTS', '账号或 UUID 已存在', 409)
        return {'uuid': identity, 'username': username, 'roles': roles}

    def login(self, body, source, agent=""):
        name, password = body.get('username'), body.get('password')
        if not isinstance(name, str) or not isinstance(password, str) or len(name) > 32 or len(password) > 128:
            reject('LOGIN_FAILED', '账号或密码错误', 401)
        now = int(time.time())
        with self.db() as conn:
            conn.execute('BEGIN IMMEDIATE')
            conn.execute('DELETE FROM attempts WHERE time<?', (now-900,))
            if conn.execute('SELECT COUNT(*) FROM attempts WHERE source=? AND time>?', (source, now-300)).fetchone()[0] >= 8:
                reject('LOGIN_RATE_LIMIT', '尝试次数过多，请 5 分钟后重试', 429)
            conn.execute('INSERT INTO attempts VALUES(?,?)', (source, now))
            row = conn.execute('SELECT * FROM users WHERE username=? COLLATE NOCASE', (name,)).fetchone()
        expected = row['password'] if row else '0'*64
        salt = bytes.fromhex(row['salt']) if row else b'\0'*16
        if not hmac.compare_digest(password_hash(password, salt), expected) or row['disabled']:
            self.audit('@login', 'LOGIN', {'username': name}, 'DENIED')
            reject('LOGIN_FAILED', '账号或密码错误', 401)
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        with self.db() as conn:
            conn.execute('DELETE FROM sessions WHERE expires<?', (now,))
            conn.execute('INSERT INTO sessions(token,user_id,csrf,expires,client) VALUES(?,?,?,?,?)', (hashlib.sha256(token.encode()).hexdigest(), row['id'], csrf, now+28800, hashlib.sha256(agent.encode()).hexdigest()))
        self.audit(row['id'], 'LOGIN', {}, 'SUCCESS')
        return token, {'user': public_user(row), 'csrf': csrf}

    def session(self, token, agent=None):
        if not isinstance(token, str) or len(token) > 128:
            reject('LOGIN_REQUIRED', '请登录管理员账号', 401)
        with self.db() as conn:
            row = conn.execute('SELECT u.*,s.csrf,s.client FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token=? AND s.expires>? AND u.disabled=0',
                (hashlib.sha256(token.encode()).hexdigest(), int(time.time()))).fetchone()
        if row and agent is not None and not hmac.compare_digest(row['client'], hashlib.sha256(agent.encode()).hexdigest()):
            reject('LOGIN_REQUIRED', '会话设备不匹配，请重新登录', 401)
        if not row:
            reject('LOGIN_REQUIRED', '登录已过期或权限被撤销', 401)
        return row

    def require(self, user, permission):
        permissions = public_user(user)['permissions']
        if '*' not in permissions and permission not in permissions:
            reject('PERMISSION_DENIED', '没有此操作权限：' + permission, 403)

    def prepare(self, user, kind, data):
        payload = {}
        if kind == 'players.orders':
            self.require(user,kind)
            target=self.dc.player(str(uuid.UUID(text(data.get('accountId'),64))))
            server=self.servers.entry(text(data.get('server'),32))['id']
            order=str(uuid.UUID(text(data.get('orderId'),64)))
            rows=[row for row in self.servers.problem_orders(target['accountId'])['orders'] if row['server']==server and row['orderId']==order]
            if len(rows)!=1 or rows[0].get('clearable') is not True:reject('ORDER_NOT_CLEARABLE','该订单不是可直接清理的待交付订单，请刷新后核对',409)
            payload={'accountId':target['accountId'],'server':server,'orderId':order,'reason':text(data.get('reason'))}
            summary=f"清理 {target['displayName']} 的问题订单 {order}（{rows[0]['price']:,} 哈弗币）：删除待交付物品，不退款，已领取物品保留"
        elif isinstance(kind,str) and kind.startswith('instance.'):
            payload,summary=self.management.validate(user,kind,data)
        elif kind in ('grant.items', 'grant.money', 'grant.entitlements'):
            self.require(user, kind)
            target = self.dc.player(text(data.get('accountId'), 64))
            reason = text(data.get('reason'))
            payload = {'accountId': target['accountId'], 'reason': 'panel:' + user['id'] + ' ' + reason,
                       'subject': text(data.get('subject', '管理员补偿'), 80)}
            if data.get('sender'):
                payload['sender'] = text(data['sender'], 40)
            if data.get('description'):
                payload['description'] = text(data['description'], 400)
            if kind == 'grant.money':
                payload.update(currency=amount(data.get('amount')))
                summary = f"向 {target['displayName']} 发送 {payload['currency']:,} 哈弗币邮件"
            elif kind == 'grant.entitlements':
                choice=text(data.get('sku'),256)
                catalog=self.dc.entitlements();rows=[row for row in catalog['items'] if row['id']==choice]
                if len(rows)!=1:reject('UNKNOWN_ENTITLEMENT','请选择真实刀具、安全箱或钥匙包')
                payload['entitlements']=[choice]
                duration=data.get('durationSeconds',0)
                if type(duration) is not int or duration!=0 and not 60<=duration<=31536000:reject('INVALID_DURATION','权限时长为永久或 1 分钟～365 天')
                if duration and catalog.get('supportsTimed') is not True:reject('UPSTREAM_NOT_READY','数据中心尚未支持限时权限，未发放邮件',503)
                payload['durationSeconds']=duration
                summary=f"向 {target['displayName']} 发送 {rows[0]['name']} 的使用权限邮件（领取后 {str(duration//60)+' 分钟' if duration else '永久'}解锁）"
            else:
                product = self.dc.product(text(data.get('sku'), 256))
                count = data.get('count')
                if type(count) is not int or not 1 <= count <= 10000:
                    reject('INVALID_COUNT', '数量范围为 1～10000，必须为整数')
                stack = int(product['maxStack'])
                if stack < 1 or (count+stack-1)//stack > 32:
                    reject('TOO_MANY_STACKS', '一次最多发放 32 个堆叠')
                payload['items'] = [{'sku': product['sku'], 'count': min(stack, count-i)} for i in range(0,count,stack)]
                summary = f"向 {target['displayName']} 发送 {product['name']} × {count} 的附件邮件"
        elif kind == 'moderation.voice':
            self.require(user,'console.voice')
            data={**data,'reason':text(data.get('reason'))}
            payload,summary=voice_moderation.prepare(self,data)
            kind='console.voice'
        elif kind == 'server.lifecycle':
            self.require(user, kind)
            server = self.servers.entry(text(data.get('server'), 32))
            action = data.get('action')
            if action not in ('start', 'stop', 'restart', 'force_restart', 'force_stop'):
                reject('INVALID_ACTION', '未知服务操作')
            payload = {'server': server['id'], 'action': action, 'reason': text(data.get('reason'))}
            summary = {'start': '启动', 'stop': '停止', 'restart': '重启', 'force_restart': '强制重启', 'force_stop': '强制关闭'}[action] + ' ' + server.get('name', server['id'])
            if action.startswith('force_'):
                summary += '（立即执行，在线玩家将断开，当前战局会中断）'
        elif kind == 'console':
            command = text(data.get('command'), 512).lstrip('/')
            target_server=text(data.get('server'), 32)
            if target_server!='all':self.servers.entry(target_server)
            tokens = shlex.split(command)
            if not tokens:
                reject('INVALID_COMMAND', '请输入命令')
            first = tokens[0].lower()
            if target_server=='all' and first not in ('公告','announce','封禁','ban','解封','pardon','警告','warn'):
                reject('COMMAND_NOT_ALLOWED','该操作不支持全部服务器',403)
            if first in ('公告', 'announce'):
                permission = 'console.announce'
                message = text(command[len(tokens[0]):].strip(), 140)
                # Use JSON components to keep quotes/control characters from becoming command fragments.
                commands = ['title @a times 10 100 20', 'title @a title ' + json.dumps({'text': message, 'color': 'aqua'},ensure_ascii=False)]
            elif first in ('封禁', 'ban', '解封', 'pardon', '警告', 'warn'):
                parts = command.split(None, 2)
                if len(parts) < 3 or not re.fullmatch(r'[A-Za-z0-9_\u3400-\u4dbf\u4e00-\u9fff]{1,16}', parts[1]):
                    reject('INVALID_TARGET', '请输入有效角色名与操作原因')
                target, message = parts[1], text(parts[2], 140)
                if first in ('警告', 'warn'):
                    permission = 'console.warn'
                    component = json.dumps({'text': '管理员警告：' + message, 'color': 'yellow'}, ensure_ascii=False)
                    commands = ['title ' + target + ' times 10 100 20', 'title ' + target + ' title ' + component, 'tellraw ' + target + ' ' + component]
                else:
                    permission = 'console.ban'
                    commands = [('pardon ' if first in ('解封', 'pardon') else 'ban ') + target + (' ' + message if first in ('封禁', 'ban') else '')]
            elif first in ('保存', 'save'):
                permission, commands = 'console.save', ['save-all flush']
            elif first in ('地图检查', 'map-inspect'):
                permission, commands = 'console.map', ['deltazipline list', 'deltaspawn info']
            else:
                permission, commands = 'console.raw', [command]
                # Keep process lifecycle and privilege edits in the audited dedicated routes.
                if first.split(':')[-1] in ('stop', 'restart', 'op', 'deop', 'reload', 'execute'):
                    reject('USE_STRUCTURED_ACTION', '此命令需通过对应管理功能执行', 403)
                roots = {row['en'].split()[0] for row in json.loads((Path(__file__).parent/'commands.json').read_text(encoding='utf-8'))}
                if first not in roots:
                    reject('COMMAND_NOT_ALLOWED','该指令未加入服务端白名单；请通过专用入口执行',403)
            self.require(user, permission)
            kind = permission
            payload = {'server': data['server'], 'commands': commands, 'reason': text(data.get('reason'))}
            if target_server=='all':payload['servers']=list(self.servers.entries)
            summary = f"在 {'全部服务器' if target_server=='all' else target_server} 执行：{command}"
        else:
            reject('UNKNOWN_OPERATION', '未知操作')
        permission = kind
        operation_id = str(uuid.uuid4())
        if kind.startswith('grant.'):
            payload['requestId'] = operation_id
        with self.db() as conn:
            conn.execute('INSERT INTO operations(id,actor,permission,kind,payload,summary,status,expires) VALUES(?,?,?,?,?,?,?,?)',
                (operation_id,user['id'],permission,kind,json.dumps(payload,ensure_ascii=False),summary,'PREVIEW',int(time.time())+60))
        self.audit(user['id'], 'PREPARE', {'operationId': operation_id, 'summary': summary}, 'PREVIEW')
        return {'operationId': operation_id, 'summary': summary, 'expiresIn': 60, 'delivery': 'MAIL' if kind.startswith('grant.') else None}

    def confirm(self, user, operation_id):
        now = int(time.time())
        with self.db() as conn:
            conn.execute('BEGIN IMMEDIATE')
            op = conn.execute('SELECT * FROM operations WHERE id=? AND actor=?', (operation_id, user['id'])).fetchone()
            if not op:
                reject('OPERATION_NOT_FOUND', '操作不存在或由其他管理员创建', 404)
            current_user = conn.execute('SELECT * FROM users WHERE id=? AND disabled=0', (user['id'],)).fetchone()
            if not current_user:reject('LOGIN_REQUIRED','管理员权限已撤销',401)
            self.require(current_user, op['permission'])
            if op['status'] == 'SUCCESS':
                return json.loads(op['receipt'])
            if op['status'] in ('CANCELLED', 'FAILED'):
                reject('OPERATION_CLOSED', '操作已关闭，请重新预览', 409)
            if op['status'] == 'PREVIEW' and op['expires'] < now:
                reject('PREVIEW_EXPIRED', '预览已过期，请重新确认目标', 409)
            if op['lease'] > now:
                reject('OPERATION_BUSY', '原操作正在处理中，请稍后刷新', 409)
            if op['status'] in ('UNCERTAIN','RUNNING') and not op['kind'].startswith('grant.') and op['kind'] not in ('console.voice','players.orders'):
                reject('RESULT_UNCERTAIN', '指令结果待核对，不允许重复执行；请查看服务器记录', 409)
            conn.execute('UPDATE operations SET status=?,lease=? WHERE id=?', ('RUNNING',now+60,operation_id))
        payload = json.loads(op['payload'])
        try:
            if op['kind'].startswith('grant.'):
                receipt = self.dc.grant(payload)
                mail = receipt.get('mail', {})
                if not mail.get('mailId') or mail.get('currency',0) != payload.get('currency',0) or mail.get('source') != 'admin-grant:' + operation_id:
                    reject('UPSTREAM_UNCERTAIN', '邮件回执不匹配，需要核对原操作', 503)
                if any(mail.get(field) != payload[field] for field in ('sender','description') if field in payload):
                    reject('UPSTREAM_UNCERTAIN', '邮件文字回执不匹配，数据中心可能尚未更新，请核对原操作', 503)
                expected = sorted((line['sku'],line['count']) for line in payload.get('items',[]))
                returned = sorted((line.get('sku'),line.get('count')) for line in mail.get('attachments',[]))
                if expected != returned:
                    reject('UPSTREAM_UNCERTAIN','附件回执不匹配，请核对原操作',503)
                if payload.get('entitlements') and any(mail.get('entitlementDurations',{}).get(right,0)!=payload.get('durationSeconds',0) for right in payload['entitlements']):reject('UPSTREAM_UNCERTAIN','邮件权限时长回执不匹配，需要核对原操作',503)
                if sorted(mail.get('entitlements',[]))!=sorted(payload.get('entitlements',[])):
                    reject('UPSTREAM_UNCERTAIN','权益回执不匹配，请核对原操作',503)
                receipt = {'mailId': mail['mailId'], 'message': '邮件已发出，玩家可在邮箱领取', 'operationId': operation_id}
            elif op['kind'] == 'players.orders':
                receipt=self.servers.clear_order(payload['server'],payload['accountId'],payload['orderId'],operation_id)
            elif op['kind'] == 'console.voice':
                def save_voice(value):
                    with self.db() as conn:
                        conn.execute('UPDATE operations SET receipt=? WHERE id=?',(json.dumps(value,ensure_ascii=False),operation_id))
                receipt=voice_moderation.execute(self,payload,json.loads(op['receipt']) if op['receipt'] else None,save_voice)
            elif op['kind'].startswith('instance.'):
                receipt=self.management.execute(user,op['kind'],payload,operation_id)
            elif op['kind'] == 'server.lifecycle':
                with self.management.lock:
                    receipt = self.servers.lifecycle(payload['server'],payload['action'])
            else:
                targets=payload.get('servers',[payload['server']])
                receipt = {'output': '\n'.join('['+self.servers.entry(server).get('name',server)+'] '+self.servers.command(server, command) for server in targets for command in payload['commands'])}
        except ControlError as error:
            status = 'UNCERTAIN' if error.status >= 500 else 'FAILED'
            with self.db() as conn:
                conn.execute('UPDATE operations SET status=?,lease=0 WHERE id=?', (status, operation_id))
            self.audit(user['id'], 'EXECUTE', {'operationId': operation_id}, status + ':' + error.code)
            raise
        except Exception:
            with self.db() as conn:
                conn.execute("UPDATE operations SET status='UNCERTAIN',lease=0 WHERE id=?", (operation_id,))
            self.audit(user['id'],'EXECUTE',{'operationId':operation_id},'UNCERTAIN')
            reject('RESULT_UNCERTAIN','执行结果待核对，请保留原操作号',503)
        with self.db() as conn:
            conn.execute('UPDATE operations SET status=?,receipt=?,lease=0 WHERE id=?', ('SUCCESS',json.dumps(receipt,ensure_ascii=False),operation_id))
        self.audit(user['id'], 'EXECUTE', {'operationId': operation_id, 'summary': op['summary'], 'reason': payload.get('reason','')}, 'SUCCESS')
        return receipt

    def route(self, user, path, data):
        if path.startswith('/api/panel/instance/'):
            self.require(user,'instance.read')
            action=path.rsplit('/',1)[1];server=data.get('server')
            if action=='overview':return self.management.overview()
            if action=='files':return self.management.list_files(server,data.get('path',''))
            if action=='file':return self.management.read_file(server,data.get('path',''),data.get('download')=='1')
            if action=='logs':return self.management.logs(server,data.get('cursor',''))
            if action=='config':return self.management.properties(server)
            if action=='backups':return self.management.backups(server)
            if action=='tasks':return self.management.tasks(server)
            if action=='trash':return self.management.trash(server)
            reject('NOT_FOUND','接口不存在',404)
        if path == '/api/panel/problem-orders':
            self.require(user,'players.orders')
            account=str(uuid.UUID(text(data.get('accountId'),64)))
            self.dc.player(account)
            return self.servers.problem_orders(account)
        if path in ('/api/panel/player-check','/api/panel/player-check-result'):
            self.require(user,'players.check');self.require(user,'evidence.read')
            return player_check.start(self,user,data) if path.endswith('/player-check') else player_check.result(self,user,text(data.get('checkId'),64))
        if path in ('/api/panel/evidence', '/api/panel/evidence-detail'):
            self.require(user, 'evidence.read')
            if path.endswith('-detail') and not data.get('nonce'):reject('INVALID_SCREENSHOT','请选择截图')
            result = self.evidence.fetch(data)
            self.audit(user['id'], 'READ_EVIDENCE', {'nonce': data.get('nonce'), 'player': data.get('player','')}, 'SUCCESS')
            return result
        if path == '/api/panel/servers':
            self.require(user,'servers.read');return {'servers': self.servers.status()}
        if path == '/api/panel/online-players':
            self.require(user,'servers.read');self.require(user,'players.read');return self.servers.online_players()
        if path == '/api/panel/players':
            self.require(user,'players.read');return self.dc.players(text(data['query'],64) if data.get('query') else '',int(data.get('offset',0)))
        if path == '/api/panel/player':
            self.require(user,'players.assets');result=self.dc.player(text(data.get('accountId'),64))
            self.audit(user['id'],'READ_ASSETS',{'accountId':result['accountId']},'SUCCESS');return result
        if path == '/api/panel/catalog':
            self.require(user,'catalog.read');return self.dc.catalog(data.get('query',''),int(data.get('offset',0)))
        if path == '/api/panel/entitlements':
            self.require(user,'grant.entitlements');return self.dc.entitlements()
        if path == '/api/panel/prepare':
            return self.prepare(user,data.get('kind'),data)
        if path == '/api/panel/confirm':
            return self.confirm(user,text(data.get('operationId'),64))
        if path == '/api/panel/cancel':
            with self.db() as conn:
                original=conn.execute('SELECT kind,payload FROM operations WHERE id=? AND actor=?',(data.get('operationId'),user['id'])).fetchone()
                changed=conn.execute("UPDATE operations SET status='CANCELLED' WHERE id=? AND actor=? AND status='PREVIEW'",(data.get('operationId'),user['id'])).rowcount
            if not changed:reject('OPERATION_CLOSED','操作无法取消',409)
            if original and original['kind']=='instance.file.upload-staged':
                self.management.discard_upload(user,json.loads(original['payload'])['stage'])
            self.audit(user['id'],'CANCEL',{'operationId':data['operationId']},'SUCCESS');return {'cancelled':True}
        if path == '/api/panel/operations':
            with self.db() as conn:
                rows=conn.execute('SELECT id,summary,status,receipt FROM operations WHERE actor=? ORDER BY rowid DESC LIMIT 30',(user['id'],)).fetchall()
            return {'operations':[dict(row) for row in rows]}
        if path == '/api/panel/console-help':
            commands = json.loads((Path(__file__).parent/'commands.json').read_text(encoding='utf-8'))
            allowed = public_user(user)['permissions']
            return {'commands': [row for row in commands if '*' in allowed or row['permission'] in allowed], 'notice':'选择指令填写参数；Tab 补齐。写操作必须预览确认。'}
        if path == '/api/panel/console-guide':
            self.require(user,'servers.read')
            page = int(data.get('page',1))
            if not 1 <= page <= 100:reject('INVALID_PAGE','帮助页码必须为 1～100')
            server = text(data.get('server'),32)
            output = self.servers.command(server,'help '+str(page))
            self.audit(user['id'],'READ_COMMAND_GUIDE',{'server':server,'page':page},'SUCCESS')
            return {'output':output,'page':page}
        if path == '/api/panel/console-read':
            self.require(user,'servers.read')
            if data.get('command') not in ('在线人数','list'):reject('NOT_READONLY','此命令需预览确认')
            return {'output':self.servers.command(data.get('server'),'list')}
        if path == '/api/panel/users':
            self.require(user,'groups.manage')
            with self.db() as conn:rows=conn.execute('SELECT * FROM users ORDER BY username').fetchall()
            return {'users':[public_user(row) for row in rows],'roles':LABELS}
        if path == '/api/panel/user-create':
            self.require(user,'groups.manage')
            result=self.create_user(data.get('uuid'),data.get('username'),data.get('password'),data.get('roles'))
            self.audit(user['id'],'USER_CREATE',result,'SUCCESS');return result
        if path == '/api/panel/user-update':
            self.require(user,'groups.manage')
            roles=data.get('roles');disabled=data.get('disabled')
            if not valid_roles(roles) or type(disabled) is not bool:reject('INVALID_ROLES','请选择一种有效身份和账号状态')
            with self.db() as conn:
                conn.execute('BEGIN IMMEDIATE')
                target=conn.execute('SELECT * FROM users WHERE id=?',(data.get('uuid'),)).fetchone()
                if not target:reject('USER_NOT_FOUND','管理员不存在',404)
                owners=conn.execute("SELECT COUNT(*) FROM users WHERE disabled=0 AND roles LIKE '%\"owner\"%'").fetchone()[0]
                if 'owner' in json.loads(target['roles']) and not target['disabled'] and (disabled or 'owner' not in roles) and owners<=1:reject('LAST_OWNER','不能撤销最后一个服主',409)
                conn.execute('UPDATE users SET roles=?,disabled=? WHERE id=?',(json.dumps(sorted(set(roles))),int(disabled),target['id']))
                conn.execute('DELETE FROM sessions WHERE user_id=?',(target['id'],))
            self.audit(user['id'],'USER_UPDATE',{'uuid':target['id'],'roles':roles,'disabled':disabled},'SUCCESS');return {'updated':True}
        if path == '/api/panel/audit':
            self.require(user,'audit.read')
            with self.db() as conn:rows=conn.execute('SELECT * FROM audit ORDER BY id DESC LIMIT 100').fetchall()
            return {'events':[dict(row) for row in rows]}
        reject('NOT_FOUND','接口不存在',404)


class Handler(BaseHTTPRequestHandler):
    server_version='BreaklineAdmin'
    def log_message(self,*args):
        pass  # Avoid logging credentials, tokens or command text.

    def send(self,status,value,cookie=None,content_type='application/json; charset=utf-8'):
        output=json.dumps(value,ensure_ascii=False).encode() if isinstance(value,dict) else value
        self.send_response(status)
        for name,val in {'Content-Type':content_type,'Cache-Control':'no-store','X-Content-Type-Options':'nosniff',
            'Content-Security-Policy':("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'" if getattr(self,'workspace_page',False) else "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"),
            'Referrer-Policy':'no-referrer','X-Frame-Options':'DENY', 'Strict-Transport-Security':'max-age=31536000'}.items():self.send_header(name,val)
        if cookie:self.send_header('Set-Cookie',cookie)
        self.send_header('Content-Length',str(len(output)))
        try:self.end_headers();self.wfile.write(output)
        except (ConnectionError,TimeoutError):self.close_connection=True

    def token(self):
        try:
            jar=cookies.SimpleCookie(self.headers.get('Cookie',''))
            return jar[COOKIE].value if COOKIE in jar else ''
        except cookies.CookieError:return ''

    def cookie(self,token):
        return f'{COOKIE}={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age=' + ('28800' if token else '0') + ('; Secure' if self.server.panel.secure else '')

    def do_GET(self):self.handle_route(False)
    def do_POST(self):self.handle_route(True)

    def handle_route(self,post):
        try:
            panel=self.server.panel
            if panel.secure and self.headers.get('X-Forwarded-Proto') != 'https':reject('HTTPS_REQUIRED','管理操作仅允许加密连接',403)
            if self.headers.get('Host')!=urlsplit(panel.origin).netloc:reject('INVALID_HOST','访问域名不匹配',403)
            route=urlsplit(self.path).path
            self.workspace_page=False
            if not post and (route=='/workspace' or route=='/' and panel.workspace_default):
                file=Path(__file__).parent/'web/mcsm/index.html'
                if not file.is_file():reject('WORKSPACE_NOT_BUILT','新版管理界面尚未构建',503)
                self.workspace_page=True
                return self.send(200,file.read_bytes(),content_type='text/html; charset=utf-8')
            if not post and route.startswith('/mcsm/'):
                root=(Path(__file__).parent/'web/mcsm').resolve();file=(root/route[len('/mcsm/'):]).resolve()
                if not file.is_relative_to(root) or not file.is_file():reject('NOT_FOUND','资源不存在',404)
                return self.send(200,file.read_bytes(),content_type=mimetypes.guess_type(file.name)[0] or 'application/octet-stream')
            if not post and route in ('/','/panel.css','/panel.js'):
                name={'/':'index.html','/panel.css':'panel.css','/panel.js':'panel.js'}[route]
                mime={'/':'text/html; charset=utf-8','/panel.css':'text/css; charset=utf-8','/panel.js':'text/javascript; charset=utf-8'}[route]
                return self.send(200,(Path(__file__).parent/'web'/name).read_bytes(),content_type=mime)
            if not route.startswith('/api/panel/') and not (route=='/icons/index.json' or re.fullmatch(r'/icons/[a-f0-9]{64}\.png',route)):reject('NOT_FOUND','页面不存在',404)
            data={}
            if not post and route=='/api/panel/instance/download':
                user=panel.session(self.token(),self.headers.get('User-Agent','')[:512]);args={key:values[0] for key,values in parse_qs(urlsplit(self.path).query).items()}
                f=panel.management.download(user,args.get('server'),args.get('path',''))
                with f.open('rb') as stream:
                    remaining=os.fstat(stream.fileno()).st_size
                    self.send_response(200)
                    for name,value in {'Content-Type':'application/octet-stream','Content-Length':str(remaining),'Content-Disposition':"attachment; filename*=UTF-8''"+quote(f.name,safe=''),'Cache-Control':'no-store','X-Content-Type-Options':'nosniff','X-Frame-Options':'DENY','Referrer-Policy':'no-referrer'}.items():self.send_header(name,value)
                    self.end_headers()
                    while remaining:
                        chunk=stream.read(min(1024*1024,remaining))
                        if not chunk:break
                        self.wfile.write(chunk);remaining-=len(chunk)
                panel.audit(user['id'],'INSTANCE_DOWNLOAD',{'server':args.get('server'),'path':args.get('path')},'SUCCESS')
                return
            if post:
                if self.headers.get('Origin')!=panel.origin:reject('BAD_ORIGIN','请求来源不匹配',403)
                if route=='/api/panel/instance/upload':
                    user=panel.session(self.token(),self.headers.get('User-Agent','')[:512])
                    if not hmac.compare_digest(self.headers.get('X-CSRF-Token',''),user['csrf']):reject('BAD_CSRF','请刷新后重试',403)
                    if self.headers.get('Content-Type')!='application/octet-stream':reject('BINARY_REQUIRED','请使用文件上传入口')
                    args={key:values[0] for key,values in parse_qs(urlsplit(self.path).query).items()}
                    result=panel.management.receive_upload(user,args.get('server'),args.get('path',''),self.rfile,int(self.headers.get('Content-Length','-1')))
                    return self.send(200,{'ok':True,'data':result})
                if self.headers.get('Content-Type','').split(';')[0]!='application/json':reject('JSON_REQUIRED','请发送 JSON')
                size=int(self.headers.get('Content-Length','0'))
                limit=12*1024*1024 if route=='/api/panel/prepare' else 16384
                if not 0<size<=limit:reject('BODY_TOO_LARGE','请求内容过大',413)
                if size>16384:
                    large_user=panel.session(self.token(),self.headers.get('User-Agent','')[:512]);panel.require(large_user,'instance.file.write')
                    if not hmac.compare_digest(self.headers.get('X-CSRF-Token',''),large_user['csrf']):reject('BAD_CSRF','请刷新后重试',403)
                data=json.loads(self.rfile.read(size))
                if not isinstance(data,dict):reject('INVALID_JSON','需要对象参数')
            else:
                data={key:values[0] for key,values in parse_qs(urlsplit(self.path).query).items()}
            if route=='/api/panel/login':
                if not post:reject('METHOD','请用登录表单',405)
                source=self.client_address[0]
                # Only the loopback tunnel may provide the visitor IP. Other clients cannot choose a rate-limit bucket.
                if panel.secure and ipaddress.ip_address(source).is_loopback:
                    forwarded=self.headers.get('CF-Connecting-IP','')
                    try:source=str(ipaddress.ip_address(forwarded))
                    except ValueError:pass
                token,result=panel.login(data,source,self.headers.get('User-Agent','')[:512]);return self.send(200,{'ok':True,**result},self.cookie(token))
            user=panel.session(self.token(),self.headers.get('User-Agent','')[:512])
            if not post and (route=='/icons/index.json' or re.fullmatch(r'/icons/[a-f0-9]{64}\.png',route)):
                panel.require(user,'catalog.read')
                file=Path(__file__).parent/'web'/route.lstrip('/')
                if not file.is_file():reject('ICON_NOT_FOUND','物品图标尚未导出',404)
                return self.send(200,file.read_bytes(),content_type='application/json; charset=utf-8' if route.endswith('.json') else 'image/png')
            if post and not hmac.compare_digest(self.headers.get('X-CSRF-Token',''),user['csrf']):reject('BAD_CSRF','请刷新后重试',403)
            if route=='/api/panel/session':return self.send(200,{'ok':True,'user':public_user(user),'csrf':user['csrf']})
            if route=='/api/panel/logout':
                if not post:reject('METHOD','请使用退出按钮',405)
                with panel.db() as conn:conn.execute('DELETE FROM sessions WHERE token=?',(hashlib.sha256(self.token().encode()).hexdigest(),))
                return self.send(200,{'ok':True},self.cookie(''))
            writes={'/api/panel/prepare','/api/panel/confirm','/api/panel/cancel','/api/panel/user-create','/api/panel/user-update','/api/panel/console-read','/api/panel/console-guide','/api/panel/player-check'}
            if (route in writes)!=post:reject('METHOD','请求方法不匹配',405)
            return self.send(200,{'ok':True,'data':panel.route(user,route,data)})
        except ControlError as error:
            self.send(error.status,{'ok':False,'error':error.code,'message':str(error)})
        except (ValueError,TypeError,KeyError):
            self.send(400,{'ok':False,'error':'INVALID_REQUEST','message':'请求参数无效'})
        except Exception:
            self.send(503,{'ok':False,'error':'PANEL_UNAVAILABLE','message':'管理服务暂不可用，请保留原操作号并稍后重试'})


class Server(ThreadingHTTPServer):
    daemon_threads=True
    def __init__(self,address,panel):
        self.panel=panel;self.slots=threading.BoundedSemaphore(32)
        super().__init__(address,Handler)
    def get_request(self):
        request,address=super().get_request();request.settimeout(15);return request,address
    def process_request(self,request,address):
        if not self.slots.acquire(blocking=False):
            request.close();return
        try:super().process_request(request,address)
        except Exception:self.slots.release();raise
    def process_request_thread(self,request,address):
        try:super().process_request_thread(request,address)
        finally:self.slots.release()


def main():
    os.umask(0o077)
    parser=argparse.ArgumentParser()
    parser.add_argument('--config',default='config.json')
    parser.add_argument('--bootstrap-owner',action='store_true')
    parser.add_argument('--username')
    parser.add_argument('--uuid')
    args=parser.parse_args()
    config=json.loads(Path(args.config).read_text(encoding='utf-8-sig'))
    origin=config['origin']
    if not origin.startswith('https://') and urlsplit(origin).hostname not in ('127.0.0.1','localhost'):
        parser.error('Remote panel requires HTTPS')
    panel=Panel(config['database'],DataCenter(config['dataCenter'],os.environ.get('DELTA_SERVICE_TOKEN',''),os.environ.get('DELTA_ADMIN_TOKEN','')),
        Servers(config['servers']),origin,Evidence(config.get('evidence')),config.get('management'))
    panel.workspace_default=bool(config.get('workspaceDefault',True))
    if args.bootstrap_owner:
        secret=getpass.getpass('New owner password (12+ characters): ')
        if secret!=getpass.getpass('Confirm password: '):parser.error('Passwords differ')
        result=panel.create_user(args.uuid,args.username,secret,['owner'],bootstrap=True)
        panel.audit(result['uuid'],'BOOTSTRAP_OWNER',{'username':result['username']},'SUCCESS')
        print('Owner created. No password was written to a log.');return
    if config.get('bind','127.0.0.1') not in ('127.0.0.1','::1'):
        parser.error('Panel must bind loopback behind HTTPS reverse proxy')
    server=Server((config.get('bind','127.0.0.1'),int(config.get('port',17850))),panel)
    panel.management.start()
    print('Breakline administrator panel ready at '+origin,flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:panel.management.stop.set();server.server_close()


if __name__=='__main__':main()
