"""Private, bounded evidence reads. The browser supplies filters, never paths or SSH options."""
import base64
import hashlib
import json
import re
import sqlite3
import subprocess
from pathlib import Path
from control import ControlError


from evidence_reader import read


class Evidence:
    def __init__(self, config=None):
        self.config = config or {}

    def fetch(self, data):
        query = {'player': data.get('player', ''), 'offset': int(data.get('offset', 0)),
                 'from': int(data.get('from', 0)), 'to': int(data.get('to', 4102444800))}
        if not isinstance(query['player'], str) or len(query['player']) > 16 or not 0 <= query['offset'] <= 100000 or not 0 <= query['from'] <= query['to'] <= 4102444800:
            raise ControlError('INVALID_FILTER', '截图筛选参数无效')
        if 'nonce' in data:
            if not isinstance(data['nonce'], str) or not re.fullmatch('[0-9a-f]{32}', data['nonce']):
                raise ControlError('INVALID_SCREENSHOT', '截图编号无效')
            query['nonce'] = data['nonce']
        if not self.config.get('database'):
            raise ControlError('EVIDENCE_NOT_CONFIGURED', '截图证据库尚未接线', 503)
        try:
            if self.config.get('sshHost'):
                host = self.config['sshHost']
                if not re.fullmatch('[A-Za-z0-9_.-]+@[A-Za-z0-9.-]+', host):
                    raise ValueError('Invalid fixed SSH host')
                # A dedicated restricted key invokes only the installed fixed-path reader.
                process = subprocess.run(['ssh', '-T', '-i', self.config['sshKey'], '-o', 'BatchMode=yes',
                    '-o', 'StrictHostKeyChecking=yes', '-o', 'ConnectTimeout=5', host, 'evidence-read'],
                    input=json.dumps(query).encode(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15)
                if process.returncode or len(process.stdout) > 3*1024*1024:
                    raise ValueError('Private evidence bridge unavailable')
                result = json.loads(process.stdout)
            else:
                result = read(self.config['database'], query)
        except (OSError, ValueError, sqlite3.Error, subprocess.TimeoutExpired):
            raise ControlError('EVIDENCE_UNAVAILABLE', '截图读取失败；不会更改或删除证据', 503) from None
        if result.get('missing'):
            raise ControlError('SCREENSHOT_NOT_FOUND', '截图已过保留期或不存在', 404)
        return result
