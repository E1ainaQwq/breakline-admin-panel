"""Fixed-path read-only SSH bridge; install behind a forced SSH command."""
import base64,hashlib,json,re,sqlite3,sys
from pathlib import Path

def read(database, query):
    connection = sqlite3.connect(Path(database).resolve().as_uri() + '?mode=ro', uri=True, timeout=5)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute('PRAGMA query_only=ON')
        columns = 'nonce,player,reason,server,received_at,sha256,width,height'
        if query.get('nonce'):
            row = connection.execute('SELECT '+columns+',png FROM screenshots WHERE nonce=?', (query['nonce'],)).fetchone()
            if not row:
                return {'missing': True}
            value = dict(row); png = value.pop('png')
            if len(png) > 2*1024*1024 or not png.startswith(b'\x89PNG\r\n\x1a\n') or hashlib.sha256(png).hexdigest() != value['sha256']:
                raise ValueError('Evidence integrity failure')
            value['image'] = base64.b64encode(png).decode()
            return value
        where = 'WHERE player LIKE ? ESCAPE \'!\' AND received_at>=? AND received_at<=?'
        name = query['player'].replace('!', '!!').replace('%', '!%').replace('_', '!_')
        args = ('%'+name+'%', query['from'], query['to'])
        total = connection.execute('SELECT COUNT(*) FROM screenshots '+where, args).fetchone()[0]
        rows = connection.execute('SELECT '+columns+' FROM screenshots '+where+' ORDER BY received_at DESC,nonce LIMIT 30 OFFSET ?', (*args, query['offset'])).fetchall()
        return {'screenshots': [dict(row) for row in rows], 'total': total, 'offset': query['offset']}
    finally:
        connection.close()

if __name__ == '__main__':
    try:
        raw=sys.stdin.buffer.read(4097)
        if len(raw)>4096:raise ValueError('Request too large')
        query=json.loads(raw)
        if not isinstance(query,dict) or set(query)-{'nonce','player','from','to','offset'}:raise ValueError('Invalid query')
        if not isinstance(query.get('player'),str) or len(query['player'])>16:raise ValueError('Invalid player')
        if any(type(query.get(k)) is not int for k in ('from','to','offset')):raise ValueError('Invalid numbers')
        if not 0<=query['from']<=query['to']<=4102444800 or not 0<=query['offset']<=100000:raise ValueError('Invalid range')
        if 'nonce' in query and (not isinstance(query['nonce'],str) or not re.fullmatch('[0-9a-f]{32}',query['nonce'])):raise ValueError('Invalid nonce')
        print(json.dumps(read('/opt/breakline/evidence/screenshots.db',query)))
    except Exception:
        print('Evidence request unavailable',file=sys.stderr)
        sys.exit(1)
