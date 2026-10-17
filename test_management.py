"""Isolated HTTP and filesystem acceptance; never connects to live servers."""
import base64
import hashlib
import json
import os
import tempfile
import threading
import time
import unittest
import urllib.request
import urllib.error
import uuid
import zipfile
import io
from pathlib import Path
from panel import Panel,Server,ControlError
from management import Management

class ServersFixture:
    def __init__(self,root):self.entries={'hub':{'id':'hub','name':'隔离实例','directory':str(root),'unit':'breakline-hub'}};self.commands=[];self.state='inactive'
    def entry(self,key):
        if key not in self.entries:raise ControlError('UNKNOWN_SERVER','未知实例',404)
        return self.entries[key]
    def status(self):return [{'id':'hub','name':'隔离实例','status':'online','online':0}]
    def command(self,server,command):self.entry(server);self.commands.append(command);return 'isolated command receipt'
    def lifecycle(self,server,action):self.entry(server);self.commands.append(action);return {'message':'isolated lifecycle receipt'}

class ManagementAcceptance(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.instance=self.root/'instance';self.instance.mkdir();(self.instance/'world').mkdir();(self.instance/'world/level.dat').write_bytes(b'world-before');(self.instance/'logs').mkdir();(self.instance/'logs/latest.log').write_text('服务器启动\n',encoding='utf-8');(self.instance/'notes.txt').write_text('original',encoding='utf-8');(self.instance/'server.properties').write_text('rcon.password=NEVER_RETURN\nonline-mode=true\nmax-players=40\nmotd=Breakline\n',encoding='utf-8');self.servers=ServersFixture(self.instance)
        self.panel=Panel(self.root/'admin.db',None,self.servers,'http://127.0.0.1:1');self.panel.management.process=lambda _: {'state':self.servers.state,'pid':None,'memory':None,'cpuSeconds':None}
        self.owner=str(uuid.uuid4());self.admin=str(uuid.uuid4());self.panel.create_user(self.owner,'owner-test','test-only-password-12345',['owner'],True);self.panel.create_user(self.admin,'admin-test','test-only-password-12345',['admin'])
        self.http=Server(('127.0.0.1',0),self.panel);self.panel.origin='http://127.0.0.1:'+str(self.http.server_port);self.base=self.panel.origin
        self.thread=threading.Thread(target=self.http.serve_forever,daemon=True);self.thread.start()
        token,value=self.panel.login({'username':'owner-test','password':'test-only-password-12345'},'test','acceptance');self.cookie='breakline_admin='+token;self.csrf=value['csrf']
    def tearDown(self):self.http.shutdown();self.http.server_close();self.tmp.cleanup()
    def user(self,admin=False):
        with self.panel.db() as db:return db.execute('SELECT * FROM users WHERE id=?',(self.admin if admin else self.owner,)).fetchone()
    def http_call(self,path,body=None,cookie=True,csrf=True):
        headers={'User-Agent':'acceptance','Origin':self.base}
        if cookie:headers['Cookie']=self.cookie
        if csrf:headers['X-CSRF-Token']=self.csrf
        if body is not None:headers['Content-Type']='application/json'
        req=urllib.request.Request(self.base+'/api/panel/'+path,data=json.dumps(body).encode() if body is not None else None,headers=headers)
        try:
            with urllib.request.urlopen(req) as response:return response.status,json.load(response)
        except urllib.error.HTTPError as ex:return ex.code,json.load(ex)
    def perform(self,kind,**data):
        op=self.panel.prepare(self.user(),kind,{'server':'hub',**data});return self.panel.confirm(self.user(),op['operationId'])
    def test_auth_csrf_and_role_checks(self):
        self.assertEqual(self.http_call('instance/files?server=hub',cookie=False)[0],401)
        self.assertEqual(self.http_call('prepare',{'kind':'instance.file.mkdir','server':'hub','path':'new'},csrf=False)[0],403)
        with self.assertRaises(ControlError):self.panel.route(self.user(True),'/api/panel/instance/files',{'server':'hub'})
        with self.assertRaises(ControlError):self.panel.prepare(self.user(True),'instance.file.mkdir',{'server':'hub','path':'bad'})
        self.assertFalse((self.instance/'bad').exists())
    def test_staff_terminal_is_narrow_and_commands_remain_authorized(self):
        user=self.user(True)
        logs=self.panel.route(user,'/api/panel/instance/logs',{'server':'hub'})
        self.assertIn('服务器启动',logs['text'])
        for action in ['files','file','config','backups','tasks','trash','overview']:
            with self.subTest(action=action),self.assertRaises(ControlError) as error:self.panel.route(user,'/api/panel/instance/'+action,{'server':'hub','path':'notes.txt'})
            self.assertEqual(error.exception.status,403)
        help=self.panel.route(user,'/api/panel/console-help',{})['commands']
        self.assertIn('save',[row['en'] for row in help]);self.assertNotIn('console.raw',[row['permission'] for row in help])
        for command in ['op Player','stop','execute run say x','forge tps','deltaloot clear','minecraft:op Player']:
            with self.subTest(command=command),self.assertRaises(ControlError) as error:self.panel.prepare(user,'console',{'server':'hub','command':command,'reason':'permission regression'})
            self.assertEqual(error.exception.status,403)
        before=list(self.servers.commands)
        op=self.panel.prepare(user,'console',{'server':'hub','command':'announce permission-test','reason':'permission regression'})
        self.assertEqual(before,self.servers.commands)
        self.panel.confirm(user,op['operationId']);self.assertEqual(len(self.servers.commands)-len(before),2)
        with self.assertRaises(ControlError):self.panel.prepare(user,'server.lifecycle',{'server':'hub','action':'stop','reason':'denied'})
    def test_path_escape_links_and_sensitive_files(self):
        for path in ['../outside','/etc/passwd','C:/Windows','logs/../../x','.env','server.properties','store.sqlite']:
            with self.subTest(path=path),self.assertRaises(ControlError):self.panel.management.read_file('hub',path)
        (self.instance/'private.properties').write_text('service-token=SECRET',encoding='utf-8')
        with self.assertRaises(ControlError):self.panel.management.read_file('hub','private.properties',True)
        outside=self.root/'outside';outside.mkdir();(outside/'secret.txt').write_text('outside')
        try:os.symlink(outside,self.instance/'linked',target_is_directory=True)
        except OSError:return
        with self.assertRaises(ControlError):self.panel.management.list_files('hub','linked')
    def test_http_file_listing_and_read(self):
        status,body=self.http_call('instance/files?server=hub');self.assertEqual(status,200);names=[r['name'] for r in body['data']['entries']];self.assertIn('notes.txt',names);self.assertNotIn('server.properties',names)
        status,body=self.http_call('instance/file?server=hub&path=notes.txt');self.assertEqual(body['data']['text'],'original');self.assertEqual(status,200)
    def test_file_conflict_and_confirmation_idempotency(self):
        hashed=hashlib.sha256(b'original').hexdigest();op=self.panel.prepare(self.user(),'instance.file.write',{'server':'hub','path':'notes.txt','text':'new','hash':hashed})
        (self.instance/'notes.txt').write_text('concurrent')
        with self.assertRaises(ControlError):self.panel.confirm(self.user(),op['operationId'])
        self.assertEqual((self.instance/'notes.txt').read_text(),'concurrent')
        op=self.panel.prepare(self.user(),'instance.file.write',{'server':'hub','path':'new.txt','text':'new','hash':None});first=self.panel.confirm(self.user(),op['operationId']);second=self.panel.confirm(self.user(),op['operationId']);self.assertEqual(first,second)
    def test_upload_rename_recycle_and_download(self):
        raw=bytes(range(256));self.perform('instance.file.upload',path='binary.bin',base64=base64.b64encode(raw).decode(),hash=None)
        self.assertEqual(base64.b64decode(self.panel.management.read_file('hub','binary.bin',True)['base64']),raw)
        self.perform('instance.file.mkdir',path='folder');self.perform('instance.file.rename',path='binary.bin',target='folder/moved.bin');self.perform('instance.file.delete',path='folder/moved.bin')
        self.assertFalse((self.instance/'folder/moved.bin').exists());trash=self.panel.management.trash('hub')['entries'];self.assertEqual((self.panel.management.storage/'trash/hub'/trash[0]['id']).read_bytes(),raw)
        self.perform('instance.file.restore',trash=trash[0]['id']);self.assertEqual((self.instance/'folder/moved.bin').read_bytes(),raw)
        self.perform('instance.file.delete',path='folder');trash=self.panel.management.trash('hub')['entries'];self.perform('instance.file.restore',trash=trash[0]['id']);self.assertEqual((self.instance/'folder/moved.bin').read_bytes(),raw)
    def test_log_cursor_rotation_and_split_utf8(self):
        first=self.panel.management.logs('hub');self.assertIn('服务器启动',first['text']);self.assertEqual(self.panel.management.logs('hub',first['cursor'])['text'],'')
        log=self.instance/'logs/latest.log'
        with log.open('a',encoding='utf-8') as f:f.write('新的一行\n')
        self.assertEqual(self.panel.management.logs('hub',first['cursor'])['text'].replace('\r\n','\n'),'新的一行\n')
        log.rename(log.with_suffix('.old'));log.write_text('rotated\n',encoding='utf-8');self.assertTrue(self.panel.management.logs('hub',first['cursor'])['reset'])
        log.write_bytes(b'x'*65535+'中'.encode()+b'\n');cursor=self.panel.management.logs('hub')['cursor'];self.assertTrue(cursor)
    def test_safe_properties_preserve_credentials(self):
        props=self.panel.management.properties('hub');self.assertNotIn('NEVER_RETURN',json.dumps(props));self.perform('instance.config',values={'motd':'New title','max-players':'60'},hash=props['hash']);text=(self.instance/'server.properties').read_text();self.assertIn('rcon.password=NEVER_RETURN',text);self.assertIn('online-mode=true',text);self.assertIn('max-players=60',text)
    def test_offline_world_backup_restore_and_zip_slip(self):
        self.servers.state='active'
        with self.assertRaises(ControlError):self.perform('instance.backup.create')
        self.servers.state='inactive';backup=self.perform('instance.backup.create')['backup'];(self.instance/'world/level.dat').write_bytes(b'changed');self.perform('instance.backup.restore',backup=backup);self.assertEqual((self.instance/'world/level.dat').read_bytes(),b'world-before');self.assertTrue(list((self.panel.management.storage/'restore-rollback/hub').iterdir()))
        identity=str(uuid.uuid4());target=self.panel.management.backup_folder('hub')/(identity+'.zip')
        with zipfile.ZipFile(target,'w') as z:z.writestr('../escape','bad')
        with self.assertRaises(ControlError):self.perform('instance.backup.restore',backup=identity)
        self.assertFalse((self.root/'escape').exists());self.assertEqual((self.instance/'world/level.dat').read_bytes(),b'world-before')
    def test_task_persistence_execution_and_revoked_actor(self):
        self.perform('instance.task.create',name='save-test',action='save',content='',interval=60,nextRun=int(time.time())+60)
        with self.panel.db() as db:db.execute('UPDATE instance_tasks SET next_run=?',(int(time.time())-1,))
        self.panel.management.poll_tasks();self.assertIn('save-all flush',self.servers.commands)
        with self.panel.db() as db:db.execute('UPDATE instance_tasks SET next_run=?',(int(time.time())-1,));db.execute('UPDATE users SET roles=? WHERE id=?',(json.dumps(['admin']),self.owner))
        count=len(self.servers.commands);self.panel.management.poll_tasks();self.assertEqual(len(self.servers.commands),count);self.assertFalse(self.panel.management.tasks('hub')['tasks'][0]['enabled'])
    def test_interrupted_task_is_not_replayed(self):
        self.perform('instance.task.create',name='crash-test',action='restart',content='',interval=0,nextRun=int(time.time())+60)
        with self.panel.db() as db:db.execute("UPDATE instance_tasks SET status='RUNNING'")
        reloaded=Management(self.panel);row=reloaded.tasks('hub')['tasks'][0];self.assertEqual(row['status'],'UNCERTAIN');self.assertFalse(row['enabled']);reloaded.poll_tasks();self.assertEqual(self.servers.commands,[])

    def test_stream_upload_large_file_confirm_and_native_download(self):
        raw=(b'BINARY-ISOLATED-MOD\x00'*600000)
        request=urllib.request.Request(self.base+'/api/panel/instance/upload?server=hub&path=fixture.jar',data=raw,headers={'Cookie':self.cookie,'X-CSRF-Token':self.csrf,'Origin':self.base,'User-Agent':'acceptance','Content-Type':'application/octet-stream'},method='POST')
        with urllib.request.urlopen(request) as response:op=json.load(response)['data']
        self.assertFalse((self.instance/'fixture.jar').exists())
        with self.panel.db() as db:payload=db.execute('SELECT payload FROM operations WHERE id=?',(op['operationId'],)).fetchone()[0]
        self.assertLess(len(payload),1000)
        self.panel.confirm(self.user(),op['operationId'])
        request=urllib.request.Request(self.base+'/api/panel/instance/download?server=hub&path=fixture.jar',headers={'Cookie':self.cookie,'User-Agent':'acceptance'})
        with urllib.request.urlopen(request) as response:
            self.assertIn('attachment',response.headers['Content-Disposition']);self.assertEqual(hashlib.sha256(response.read()).digest(),hashlib.sha256(raw).digest())

    def test_stream_upload_csrf_cancel_and_actor_binding(self):
        request=urllib.request.Request(self.base+'/api/panel/instance/upload?server=hub&path=cancel.txt',data=b'cancelled',headers={'Cookie':self.cookie,'Origin':self.base,'User-Agent':'acceptance','Content-Type':'application/octet-stream'},method='POST')
        with self.assertRaises(urllib.error.HTTPError) as ex:urllib.request.urlopen(request)
        self.assertEqual(ex.exception.code,403)
        op=self.panel.management.receive_upload(self.user(),'hub','cancel.txt',io.BytesIO(b'cancelled'),9)
        with self.panel.db() as db:stage=json.loads(db.execute('SELECT payload FROM operations WHERE id=?',(op['operationId'],)).fetchone()[0])['stage']
        identity=str(uuid.uuid4());self.panel.create_user(identity,'other-owner','test-only-password-12345',['owner'])
        with self.panel.db() as db:other=db.execute('SELECT * FROM users WHERE id=?',(identity,)).fetchone()
        with self.assertRaises(ControlError):self.panel.management.validate(other,'instance.file.upload-staged',{'server':'hub','stage':stage})
        self.panel.route(self.user(),'/api/panel/cancel',{'operationId':op['operationId']})
        self.assertFalse((self.instance/'cancel.txt').exists());self.assertFalse((self.panel.management.storage/'uploads'/(stage+'.part')).exists())

    def test_target_changes_during_stream_upload_are_not_overwritten(self):
        path=self.instance/'notes.txt'
        class ChangingStream(io.BytesIO):
            def read(self,size=-1):
                path.write_text('external change',encoding='utf-8')
                return super().read(size)
        with self.assertRaises(ControlError):self.panel.management.receive_upload(self.user(),'hub','notes.txt',ChangingStream(b'candidate'),9)
        self.assertEqual(path.read_text(),'external change')
        self.assertEqual(list((self.panel.management.storage/'uploads').glob('*.part')),[])

if __name__=='__main__':unittest.main(verbosity=2)
