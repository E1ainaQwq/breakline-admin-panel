"""Fixed server adapters. Secrets and transport paths are never supplied by the browser."""
import json
import os
import re
import socket
import struct
import subprocess
import time
import uuid
import urllib.request
import urllib.error
from urllib.parse import urlsplit
from concurrent.futures import ThreadPoolExecutor


class ControlError(Exception):
    def __init__(self, code, message, status=400):
        super().__init__(message)
        self.code, self.status = code, status


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        raise ControlError('UPSTREAM_REDIRECT_DENIED', '管理凭据不允许跟随重定向', 502)


class DataCenter:
    def __init__(self, url, service_token, admin_token):
        parsed = urlsplit(url)
        if parsed.scheme != 'https' and not (parsed.scheme == 'http' and parsed.hostname in ('127.0.0.1','localhost','::1')):
            raise ControlError('INSECURE_TRANSPORT', '数据中心必须使用 HTTPS 或本机回环连接', 503)
        self.url = url.rstrip('/')
        self.tokens = {'service': service_token, 'admin': admin_token}

    def call(self, route, body=None, kind='admin'):
        if not self.tokens.get(kind):
            raise ControlError('NOT_CONFIGURED', '数据中心凭据未配置', 503)
        request = urllib.request.Request(self.url + route, json.dumps(body or {}).encode(),
            {'Content-Type': 'application/json', 'Authorization': 'Bearer ' + self.tokens[kind]}, method='POST')
        try:
            with urllib.request.build_opener(NoRedirect).open(request, timeout=10) as response:
                raw = response.read(2_097_153)
        except urllib.error.HTTPError as e:
            try:
                reply = json.loads(e.read(16384))
                detail = reply.get('error', {})
                detail = detail if isinstance(detail, dict) else {}
                raise ControlError(detail.get('code', 'UPSTREAM_REJECTED'),
                    detail.get('message', '数据中心拒绝请求'), e.code) from None
            except (ValueError, TypeError):
                raise ControlError('UPSTREAM_REJECTED', '数据中心拒绝请求', 502) from None
        except (OSError, TimeoutError):
            raise ControlError('UPSTREAM_UNCERTAIN', '数据中心暂不可达；写入结果待核对，请重试原操作', 503) from None
        if len(raw) > 2_097_152:
            raise ControlError('UPSTREAM_TOO_LARGE', '响应超出限制，请缩小查询范围', 502)
        value = json.loads(raw)
        if value.get('ok') is not True:
            raise ControlError('UPSTREAM_REJECTED', value.get('message', '请求未成功'), 502)
        return value.get('data', value)

    def players(self, query='', offset=0):
        return self.call('/v1/admin/panel/players', {'query': query, 'offset': offset, 'limit': 30})

    def player(self, account):
        return self.call('/v1/admin/panel/player', {'accountId': account})

    def catalog(self, query='', offset=0):
        value = self.call('/v1/catalog/query', {'search': query, 'offset': offset, 'limit': 30}, 'service')
        # The complete opaque catalog data stays on the server.
        for item in value.get('items', []):
            item.pop('data', None)
        return value

    def product(self, sku):
        value = self.call('/v1/catalog/query', {'itemKey': sku, 'offset': 0, 'limit': 100}, 'service')
        rows = [row for row in value.get('items', []) if row.get('sku') == sku]
        if len(rows) != 1:
            raise ControlError('UNKNOWN_PRODUCT', '商品不存在或不是唯一结果', 404)
        return rows[0]

    def entitlements(self):
        return self.call('/v1/admin/panel/entitlements',{})

    def grant(self, payload):
        return self.call('/v1/admin/inventory/grant', payload)


def _read_exact(sock, count):
    result = bytearray()
    while len(result) < count:
        chunk = sock.recv(count - len(result))
        if not chunk:
            raise OSError('RCON disconnected')
        result.extend(chunk)
    return bytes(result)


def rcon(host, port, password, command):
    """RCON with a response terminator, packet bounds, and a fixed deadline."""
    if host not in ("127.0.0.1","localhost","::1"):
        raise ControlError("INSECURE_RCON", "RCON 只允许本机回环连接", 503)
    def send(sock, request_id, kind, text):
        body = struct.pack('<ii', request_id, kind) + text.encode('utf-8') + b'\x00\x00'
        sock.sendall(struct.pack('<i', len(body)) + body)

    def receive(sock):
        length = struct.unpack('<i', _read_exact(sock, 4))[0]
        if not 10 <= length <= 1_048_576:
            raise OSError('Invalid RCON packet')
        body = _read_exact(sock, length)
        request_id, kind = struct.unpack('<ii', body[:8])
        return request_id, kind, body[8:-2].decode('utf-8', errors='replace')

    if not password:
        raise ControlError('NOT_CONFIGURED', '此服务器尚未配置 RCON', 503)
    try:
        with socket.create_connection((host, port), timeout=4) as sock:
            sock.settimeout(4)
            send(sock, 11, 3, password)
            for _ in range(3):
                rid, kind, text = receive(sock)
                if rid == -1:
                    raise ControlError('RCON_AUTH_FAILED', '服务器控制连接认证失败', 503)
                if kind == 2 and rid == 11:
                    break
            else:
                raise OSError('Missing authentication response')
            send(sock, 12, 2, command)
            send(sock, 13, 2, '')
            chunks = []
            for _ in range(256):
                rid, _, text = receive(sock)
                if rid == 13:
                    return ''.join(chunks)[:65536]
                if rid != 12:
                    raise OSError('Unexpected response ID')
                chunks.append(text)
                if sum(map(len, chunks)) > 65536:
                    raise OSError('Response exceeds limit')
            raise OSError('Missing response terminator')
    except (OSError, TimeoutError):
        raise ControlError('RCON_UNCERTAIN', '服务器控制连接中断；执行结果待核对，不会自动重复命令', 503) from None


def online_count(text):
    clean = re.sub(r'§.', '', text)
    match = re.search(r'There are (\d+) of a max', clean)
    if not match:
        match = re.search(r'当前有\s*(\d+)\s*(?:位|个|名)?玩家', clean)
    if not match:
        raise ControlError('ONLINE_UNKNOWN', '无法确认在线人数，暂不执行服务器启停', 409)
    return int(match[1])


class Servers:
    def order_job(self, server, command, job):
        prefix='DELTA_ORDER_V1 '
        output=self.command(server, command)
        if prefix not in output:
            raise ControlError('ORDER_CONTROL_UNAVAILABLE','该服务器尚未加载问题订单管理补丁',503)
        for _ in range(40):
            output=self.command(server,'deltaorderadmin result '+str(job))
            if prefix not in output:raise ControlError('ORDER_CONTROL_UNAVAILABLE','订单管理回执无法确认',503)
            try:result=json.loads(output.split(prefix,1)[1].strip())
            except (TypeError,ValueError):raise ControlError('ORDER_RESULT_INVALID','订单管理回执无法确认',503) from None
            if result.get('status')=='SUCCESS':return result
            if result.get('status')=='FAILED':raise ControlError('ORDER_CLEAR_FAILED',result.get('message','订单处理失败，请核对原操作'),503)
            if result.get('status')!='RUNNING':raise ControlError('ORDER_RESULT_UNCERTAIN','订单结果待核对，请重试原操作',503)
            time.sleep(0.25)
        raise ControlError('ORDER_RESULT_UNCERTAIN','订单仍在处理中，请重试原操作',503)

    def problem_orders(self, account):
        account=str(uuid.UUID(account))
        # GameAccountBridge owns the order journal only on the hub. Battle servers
        # intentionally do not initialize it, including when the account is online there.
        self.entry('hub')
        job=uuid.uuid4()
        result=self.order_job('hub','deltaorderadmin list '+account+' '+str(job),job)
        return {'orders':[{**row,'server':'hub'} for row in result['orders']]}

    def clear_order(self, server, account, order, operation):
        if server != 'hub':
            raise ControlError('ORDER_CONTROL_UNAVAILABLE','问题订单由主城管理，请刷新后重新选择订单',409)
        account=str(uuid.UUID(account));order=str(uuid.UUID(order));operation=str(uuid.UUID(operation))
        result=self.order_job(server,'deltaorderadmin clear '+account+' '+order+' '+operation,operation)
        if result.get('orderId')!=order or result.get('refund')!=0:raise ControlError('ORDER_RESULT_INVALID','清理回执不匹配，请核对原操作',503)
        return {**result,'message':'已清理所选订单的待交付物品并解除订单锁，未退款'}

    def __init__(self, entries):
        self.entries = {row['id']: row for row in entries}

    def entry(self, server):
        if server not in self.entries:
            raise ControlError('UNKNOWN_SERVER', '未知服务器', 404)
        return self.entries[server]

    def command(self, server, command):
        row = self.entry(server)
        if row.get('host','127.0.0.1') not in ('127.0.0.1','localhost','::1'):
            raise ControlError('INSECURE_RCON', 'RCON 仅允许回环地址；跨主机请使用 SSH 本地隧道', 503)
        return rcon(row.get('host', '127.0.0.1'), int(row['port']),
            os.environ.get(row['passwordEnv'], ''), command)

    def status_one(self, row):
        result = {'id': row['id'], 'name': row.get('name', row['id']), 'status': 'unknown', 'online': None}
        try:
            listing = self.command(row['id'], 'list')
            result.update(status='online', online=online_count(listing), players=listing)
        except ControlError as error:
            result['message'] = str(error)
        return result

    def status(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            return list(pool.map(self.status_one, self.entries.values()))

    def online_players(self):
        servers = self.status()
        players = []
        for server in servers:
            if server['status'] != 'online':
                continue
            listing = re.sub(r'§.', '', server['players'])
            names = listing.split(':', 1)[1].strip() if ':' in listing else ''
            names = [name.strip() for name in names.split(',') if name.strip()]
            if len(names) != server['online'] or any(not re.fullmatch(r'[A-Za-z0-9_\u3400-\u4dbf\u4e00-\u9fff]{1,16}', name) for name in names):
                server.update(status='unknown', online=None, message='在线名单无法确认，请刷新后重试')
                continue
            for name in names:
                players.append({'name': name, 'server': server['id'], 'serverName': server['name']})
        return {'servers': servers, 'players': players, 'complete': all(row['status'] == 'online' for row in servers)}

    def lifecycle(self, server, action):
        row = self.entry(server)
        if action not in ('start', 'stop', 'restart', 'force_restart', 'force_stop'):
            raise ControlError('INVALID_ACTION', '未知服务操作')
        unit = row.get('unit', '')
        if not re.fullmatch(r'breakline-(hub|dam|space|babel)', unit):
            raise ControlError('LIFECYCLE_DISABLED', '此条目不允许启停', 403)
        # Unknown is never equivalent to empty. Stop/restart cannot kick an active match.
        if action in ('stop', 'restart'):
            for other in self.entries:
                if online_count(self.command(other, 'list')) != 0:
                    raise ControlError('PLAYERS_ONLINE', '仍有玩家在线，请先发送中央维护公告并等待离服', 409)
        command_action = {'force_restart': 'restart', 'force_stop': 'stop'}.get(action, action)
        options = ['--no-block'] if action.startswith('force_') else []
        process = subprocess.run(['sudo', '-n', '/usr/bin/systemctl', *options, command_action, unit],
            capture_output=True, timeout=25, check=False)
        if process.returncode:
            raise ControlError('SERVICE_ACTION_FAILED', '服务操作未成功，请检查服务器控制授权', 503)
        return {'accepted': True, 'message': '操作已提交；请刷新确认服务器状态', 'server': server, 'action': action}
