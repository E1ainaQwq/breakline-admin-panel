"""Instance operations for the MCSM-style workspace; fixed roots, no browser shell."""
import base64
import hashlib
import json
import logging
import socket
import os
import re
import shutil
import stat
import subprocess
import threading
import time
import uuid
import zipfile
from pathlib import Path, PurePosixPath
from control import ControlError

MAX_FILE = 8 * 1024 * 1024
MAX_UPLOAD = 512 * 1024 * 1024
BINARY_FILES = {'.jar','.zip','.gz','.png','.jpg','.jpeg','.webp','.mca','.nbt','.dat','.ogg','.mp3','.mp4'}
SECRET = re.compile(r'(?im)(?:password|passwd|secret|api[-_]?key|(?:access|auth|service|admin)?[-_]?token|authorization|rcon\.password)\s*["\']?\s*[:=]\s*["\']?\S+')
CONFIG_KEYS = {'motd','max-players','view-distance','simulation-distance','difficulty','pvp','white-list','spawn-protection','allow-flight'}

def fail(message, code='INVALID_INSTANCE_REQUEST', status=400):
    raise ControlError(code, message, status)

def filehash(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        while chunk := stream.read(1024 * 1024): h.update(chunk)
    return h.hexdigest()

def owner_request(request,timeout=10):
    endpoint=Path('/run/breakline-panel-file-owner.sock')
    if endpoint.exists():
        with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as client:
            client.settimeout(timeout);client.connect(str(endpoint));client.sendall(json.dumps(request).encode());client.shutdown(socket.SHUT_WR)
            raw=b''
            while len(raw)<=4096:
                chunk=client.recv(4096)
                if not chunk:break
                raw+=chunk
            value=json.loads(raw)
            if not value.get('ok'):raise OSError('Restricted ownership service rejected request')
            return
    # Compatibility for installations without systemd socket activation.
    result=subprocess.run(['/usr/bin/sudo','-n','/usr/local/libexec/breakline-panel-file-owner'],input=json.dumps(request),capture_output=True,text=True,timeout=timeout,check=False)
    if result.returncode:
        logging.error('FILE_OWNER_HELPER_FAILURE code=%s stdout=%r stderr=%r',result.returncode,result.stdout[:400],result.stderr[:400])
        raise OSError('Restricted ownership helper unavailable')

def preserve_owner(path,reference,mode=None):
    info=reference.stat()
    if hasattr(os,'chown'):
        try:os.chown(path,info.st_uid,info.st_gid)
        except PermissionError:
            helper=Path('/usr/local/libexec/breakline-panel-file-owner')
            if helper.is_file():
                request={'path':str(path.absolute()),'reference':str(reference.absolute()),'mode':stat.S_IMODE(info.st_mode)&0o777 if mode is None else mode}
                try:owner_request(request);return
                except (OSError,ValueError):pass
            fail('面板服务无法保留游戏文件属主，原文件未修改；请核对服务运行身份','FILE_OWNER_UNAVAILABLE',503)
    os.chmod(path,stat.S_IMODE(info.st_mode)&0o777 if mode is None else mode)

def preserve_world_owners(world,reference,preserve_modes=False):
    items=[world,*world.rglob('*')];info=reference.stat();helper=Path('/usr/local/libexec/breakline-panel-file-owner')
    if hasattr(os,'geteuid') and os.geteuid()!=info.st_uid and helper.is_file():
        for offset in range(0,len(items),256):
            batch=[{'path':str(item.absolute()),'reference':str(reference.absolute()),'mode':stat.S_IMODE(info.st_mode)&0o777 if item.is_dir() and not preserve_modes else stat.S_IMODE(item.stat().st_mode)&0o777} for item in items[offset:offset+256]]
            try:owner_request(batch,30)
            except (OSError,ValueError):fail('世界恢复的文件属主验证失败，原世界未替换','FILE_OWNER_UNAVAILABLE',503)
    else:
        for item in items:preserve_owner(item,reference,stat.S_IMODE(info.st_mode)&0o777 if item.is_dir() and not preserve_modes else stat.S_IMODE(item.stat().st_mode)&0o777)

class Management:
    def __init__(self, panel, options=None):
        self.panel = panel
        self.options = options or {}
        self.storage = Path(self.options.get('storage', str(Path(panel.db_path).parent/'instance-data'))).resolve()
        self.lock = threading.RLock()
        self.stop = threading.Event()
        with panel.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS instance_tasks(id TEXT PRIMARY KEY,actor TEXT NOT NULL,server TEXT NOT NULL,name TEXT NOT NULL,action TEXT NOT NULL,content TEXT NOT NULL,interval INTEGER NOT NULL,next_run INTEGER NOT NULL,enabled INTEGER NOT NULL,status TEXT NOT NULL,result TEXT NOT NULL DEFAULT "")')
            db.execute("UPDATE instance_tasks SET enabled=0,status='UNCERTAIN',result='服务重启时上次执行尚无确定回执，已停用，请核对后重新创建' WHERE status='RUNNING'")

    def root(self, server):
        row = self.panel.servers.entry(server)
        if not row.get('directory') and re.fullmatch(r'breakline-(hub|dam|space|babel)',row.get('unit','')):
            try:
                result=subprocess.run(['systemctl','show',row['unit'],'--property=WorkingDirectory','--value'],capture_output=True,text=True,timeout=3,check=False)
                candidate=Path(result.stdout.strip())
                if result.returncode==0 and candidate.is_absolute() and len(candidate.parts)>=3 and candidate.is_dir():row['directory']=str(candidate)
            except (OSError,subprocess.TimeoutExpired):pass
        if not row.get('directory'): fail('此实例尚未配置文件目录', 'INSTANCE_NOT_CONFIGURED', 409)
        root = Path(row['directory'])
        if not root.is_absolute() or root.is_symlink() or not root.is_dir(): fail('实例目录不可用', 'INSTANCE_NOT_CONFIGURED', 409)
        if root.resolve()==Path(root.anchor).resolve():fail('实例目录不能是磁盘根目录','INSTANCE_NOT_CONFIGURED',409)
        return root.resolve()

    def path(self, server, raw='', allow_root=False):
        root = self.root(server)
        if not isinstance(raw,str) or len(raw)>512 or '\\' in raw or any(ord(c)<32 for c in raw): fail('文件路径无效')
        p = PurePosixPath(raw)
        if p.is_absolute() or any(x in ('..','.') for x in raw.split('/')) or ':' in raw: fail('文件路径超出实例目录')
        if any(x.startswith('.') or x.lower() in ('server.properties','ops.json','usercache.json') or x.lower().endswith(('.db','.sqlite','.sqlite3','.key','.pem','.pfx','.env')) for x in p.parts): fail('此文件包含管理凭据或受保护数据', 'PROTECTED_FILE', 403)
        target = root
        for part in p.parts:
            target = target / part
            if target.is_symlink() or getattr(target,'is_junction',lambda:False)(): fail('不允许访问目录链接', 'PATH_LINK', 403)
        if not target.resolve().is_relative_to(root): fail('文件路径超出实例目录', 'PATH_ESCAPE', 403)
        if target == root and not allow_root: fail('不能修改实例根目录')
        return target

    def list_files(self, server, path=''):
        folder = self.path(server,path,True)
        if not folder.is_dir(): fail('目录不存在',status=404)
        rows=[]
        for item in folder.iterdir():
            try:
                rel=item.relative_to(self.root(server)).as_posix()
                self.path(server,rel)
                s=item.stat()
                rows.append({'name':item.name,'path':rel,'directory':item.is_dir(),'size':s.st_size if item.is_file() else None,'modified':int(s.st_mtime)})
            except (OSError,ControlError): continue
            if len(rows)>5000: fail('目录文件过多，请缩小范围',status=413)
        return {'path':path,'entries':sorted(rows,key=lambda x:(not x['directory'],x['name'].lower()))}

    def read_file(self, server, path, binary=False):
        f=self.path(server,path)
        if not f.is_file(): fail('文件不存在',status=404)
        if f.stat().st_size>MAX_FILE: fail('单文件上限为 8 MiB',status=413)
        raw=f.read_bytes()
        if len(raw)>MAX_FILE: fail('文件超出读取上限',status=413)
        decoded=raw.decode('utf-8',errors='replace')
        if SECRET.search(decoded): fail('检测到凭据字段，此文件不通过网页读取', 'PROTECTED_FILE',403)
        if not binary:
            try: decoded=raw.decode('utf-8')
            except UnicodeDecodeError: fail('二进制文件请使用下载')
            if '\x00' in decoded: fail('二进制文件请使用下载')
        return {'path':path,'hash':hashlib.sha256(raw).hexdigest(),'size':len(raw),**({'base64':base64.b64encode(raw).decode()} if binary else {'text':decoded})}

    def fingerprint(self,server,path):
        f=self.path(server,path)
        if f.is_file():return filehash(f)
        records=[]
        for folder,dirs,files in os.walk(f,followlinks=False):
            for name in dirs+files:
                p=Path(folder)/name;self.path(server,p.relative_to(self.root(server)).as_posix());s=p.stat()
                records.append((p.relative_to(f).as_posix(),s.st_size,s.st_mtime_ns))
                if len(records)>100000:fail('目录超过网页操作上限',status=413)
        return hashlib.sha256(json.dumps(sorted(records)).encode()).hexdigest()

    def download(self,user,server,path):
        self.panel.require(user,'instance.read')
        f=self.path(server,path)
        if not f.is_file():fail('文件不存在',status=404)
        if f.stat().st_size<=MAX_FILE:self.read_file(server,path,True)
        elif f.suffix.lower() not in BINARY_FILES:fail('大文本文件请在服务器端处理；网页不直接下载可能包含凭据的文本',status=413)
        return f

    def receive_upload(self,user,server,path,stream,length):
        self.panel.require(user,'instance.file.upload-staged')
        f=self.path(server,path)
        if not f.parent.is_dir():fail('父目录不存在')
        if not 0<=length<=MAX_UPLOAD:fail('上传文件上限为 512 MiB',status=413)
        if length>MAX_FILE and f.suffix.lower() not in BINARY_FILES:fail('大文件上传仅支持模组、压缩包、媒体和世界数据；文本编辑上限为 8 MiB',status=413)
        if f.exists():self.download(user,server,path)
        original=filehash(f) if f.exists() else None
        folder=self.storage/'uploads';folder.mkdir(parents=True,exist_ok=True,mode=0o700)
        if len(list(folder.glob('*.part')))>=8:fail('同时暂存的上传过多，请完成或取消其他上传',status=429)
        if shutil.disk_usage(folder).free<length+256*1024**2:fail('上传暂存空间不足',status=409)
        identity=str(uuid.uuid4());stage=folder/(identity+'.part');meta=folder/(identity+'.json');digest=hashlib.sha256()
        try:
            with stage.open('xb') as out:
                remaining=length
                while remaining:
                    chunk=stream.read(min(1024*1024,remaining))
                    if not chunk:fail('上传中断，未修改目标文件',status=400)
                    out.write(chunk);digest.update(chunk);remaining-=len(chunk)
                out.flush();os.fsync(out.fileno())
            if length<=MAX_FILE and SECRET.search(stage.read_bytes().decode('utf-8',errors='replace')):fail('检测到凭据字段，未写入实例','PROTECTED_FILE',403)
            value={'actor':user['id'],'server':server,'path':path,'hash':original,'size':length,'sha256':digest.hexdigest(),'expires':int(time.time())+900}
            meta.write_text(json.dumps(value,ensure_ascii=False),encoding='utf-8')
            return self.panel.prepare(user,'instance.file.upload-staged',{'server':server,'stage':identity})
        except Exception:
            stage.unlink(missing_ok=True);meta.unlink(missing_ok=True);raise

    def staged(self,user,identity):
        if not re.fullmatch(r'[0-9a-f-]{36}',str(identity)):fail('上传编号无效')
        folder=self.storage/'uploads';meta=folder/(identity+'.json');stage=folder/(identity+'.part')
        if not meta.is_file() or not stage.is_file():fail('上传暂存已失效，请重新上传',status=409)
        value=json.loads(meta.read_text(encoding='utf-8'))
        if value['actor']!=user['id']:fail('上传属于其他管理员',status=403)
        if value['expires']<int(time.time()):fail('上传暂存已过期，请重新上传',status=409)
        return value,stage

    def discard_upload(self,user,identity):
        self.staged(user,identity)
        for ext in ('.part','.json'):(self.storage/'uploads'/(identity+ext)).unlink(missing_ok=True)

    def clean_uploads(self):
        folder=self.storage/'uploads'
        if not folder.is_dir():return
        for stage in folder.glob('*.part'):
            if time.time()-stage.stat().st_mtime>3600 and re.fullmatch(r'[0-9a-f-]{36}',stage.stem):
                stage.unlink();stage.with_suffix('.json').unlink(missing_ok=True)

    def trash(self,server):
        self.panel.servers.entry(server);folder=self.storage/'trash'/server
        rows=[]
        if folder.is_dir():
            for meta in folder.glob('*.json'):
                value=json.loads(meta.read_text(encoding='utf-8'))
                if (folder/meta.stem).exists():rows.append({'id':meta.stem,**value})
        return {'entries':sorted(rows,key=lambda r:r['deletedAt'],reverse=True)}

    def properties(self,server):
        p=self.root(server)/'server.properties'
        if p.is_symlink(): fail('配置文件不能是链接')
        if not p.exists(): return {'values':{},'hash':None}
        raw=p.read_bytes()
        if len(raw)>1024*1024: fail('配置文件过大')
        values={}
        for line in raw.decode('utf-8').splitlines():
            if '=' in line and not line.lstrip().startswith('#'):
                k,v=line.split('=',1)
                if k.strip() in CONFIG_KEYS: values[k.strip()]=v
        return {'values':values,'hash':hashlib.sha256(raw).hexdigest()}

    def logs(self,server,cursor=''):
        root=self.root(server)
        log=self.path(server,'logs/latest.log')
        if not log.exists(): return {'text':'','cursor':'','reset':True,'available':False,'message':'日志文件尚未生成'}
        with log.open('rb') as stream:
            s=os.fstat(stream.fileno());identity=f'{s.st_dev}:{s.st_ino}'
            offset=max(0,s.st_size-65536);reset=True
            if cursor:
                try:
                    raw_identity,raw_offset=cursor.rsplit('@',1);candidate=int(raw_offset)
                    if raw_identity==identity and 0<=candidate<=s.st_size:offset=candidate;reset=False
                except (ValueError,TypeError): fail('日志游标无效')
            stream.seek(offset);data=stream.read(65536)
            end=stream.tell()
            # Complete UTF-8 characters can span polling chunks.
            if end<s.st_size:
                for i in range(1,4):
                    try: data.decode('utf-8');break
                    except UnicodeDecodeError as ex:
                        if ex.reason=='unexpected end of data':data=data[:-1];end-=1
                        else:break
        return {'text':SECRET.sub('[已隐藏凭据字段]',data.decode('utf-8',errors='replace')),'cursor':identity+'@'+str(end),'reset':reset,'available':True}

    def process(self,server):
        row=self.panel.servers.entry(server);unit=row.get('unit','')
        if not re.fullmatch(r'breakline-(hub|dam|space|babel)',unit): return {'state':'unknown','pid':None}
        try:
            r=subprocess.run(['systemctl','show',unit,'--property=ActiveState,MainPID,MemoryCurrent,CPUUsageNSec','--no-pager'],capture_output=True,timeout=3,check=False,text=True)
            if r.returncode:return {'state':'unknown','pid':None}
            d=dict(line.split('=',1) for line in r.stdout.splitlines() if '=' in line)
            return {'state':d.get('ActiveState','unknown'),'pid':int(d.get('MainPID',0)) or None,'memory':int(d.get('MemoryCurrent',0)) if d.get('MemoryCurrent','').isdigit() else None,'cpuSeconds':int(d.get('CPUUsageNSec',0))/1e9 if d.get('CPUUsageNSec','').isdigit() else None}
        except (OSError,ValueError,subprocess.TimeoutExpired):return {'state':'unknown','pid':None}

    def overview(self):
        disk=shutil.disk_usage(Path(self.panel.db_path).parent)
        memory=None
        try:
            values=dict((line.split(':',1)[0],int(line.split()[1])*1024) for line in Path('/proc/meminfo').read_text().splitlines())
            memory={'total':values['MemTotal'],'used':values['MemTotal']-values['MemAvailable']}
        except (OSError,ValueError,KeyError):pass
        entries=[]
        for r in self.panel.servers.status():
            row=self.panel.servers.entry(r['id'])
            process=self.process(r['id'])
            if r['status']=='unknown' and process['state']=='inactive':r={**r,'status':'stopped','online':0}
            entries.append({**r,'process':process,'filesConfigured':bool(row.get('directory')),'type':'Minecraft Java / Forge'})
        return {'servers':entries,'host':{'cpuCount':os.cpu_count(),'load':list(os.getloadavg()) if hasattr(os,'getloadavg') else None,'memory':memory,'disk':{'total':disk.total,'used':disk.used,'free':disk.free}},'time':int(time.time())}

    def backup_folder(self,server):
        self.panel.servers.entry(server)
        folder=self.storage/'backups'/server
        folder.mkdir(parents=True,exist_ok=True,mode=0o700)
        return folder

    def backups(self,server):
        return {'backups':[{'id':p.stem,'size':p.stat().st_size,'modified':int(p.stat().st_mtime)} for p in sorted(self.backup_folder(server).glob('*.zip'),reverse=True)]}

    def offline(self,server):
        if self.process(server)['state']!='inactive': fail('请先正常停止实例，再执行世界备份或恢复；当前状态未确认停止', 'INSTANCE_NOT_STOPPED',409)

    def validate(self,user,kind,data):
        self.panel.require(user,kind)
        server=data.get('server');self.panel.servers.entry(server)
        payload={'server':server}
        if kind in ('instance.file.write','instance.file.upload','instance.file.delete','instance.file.mkdir','instance.file.rename'):
            path=data.get('path','');f=self.path(server,path)
            payload['path']=path
            if kind in ('instance.file.write','instance.file.upload'):
                if not f.parent.is_dir():fail('父目录不存在')
                if f.exists():
                    self.read_file(server,path,True)
                    if data.get('hash')!=filehash(f):fail('文件已被修改，请刷新后重新编辑','FILE_CONFLICT',409)
                elif data.get('hash') is not None:fail('文件状态已改变','FILE_CONFLICT',409)
                try:raw=data['text'].encode('utf-8') if kind.endswith('write') else base64.b64decode(data['base64'],validate=True)
                except (KeyError,ValueError,TypeError,AttributeError):fail('文件内容无效')
                if len(raw)>MAX_FILE:fail('单文件上限为 8 MiB',status=413)
                if SECRET.search(raw.decode('utf-8',errors='replace')):fail('凭据配置不通过网页写入','PROTECTED_FILE',403)
                payload.update(base64=base64.b64encode(raw).decode(),hash=data.get('hash'))
            if kind.endswith('delete'):
                if not f.exists():fail('文件不存在',status=404)
                if f.is_dir():self.offline(server)
                owner=f.parent.stat()
                # Cross-mount copies cannot preserve arbitrary owners as a non-root service.
                # Bound restore to a same-owner reference in this instance before recycling.
                for item in [f,*(f.rglob('*') if f.is_dir() else [])]:
                    info=item.stat()
                    if (info.st_uid,info.st_gid)!=(owner.st_uid,owner.st_gid):fail('此项目包含不同属主，无法安全回收；请由主机管理员处理','FILE_OWNER_UNAVAILABLE',409)
                payload['hash']=self.fingerprint(server,path)
            if kind.endswith('rename'):
                target=data.get('target','');dest=self.path(server,target)
                if not f.exists() or dest.exists() or not dest.parent.is_dir():fail('源文件不存在或目标名称已被使用')
                payload['target']=target
        elif kind=='instance.file.upload-staged':
            value,stage=self.staged(user,data.get('stage'))
            if value['server']!=server:fail('上传实例不匹配',status=403)
            f=self.path(server,value['path'])
            if (filehash(f) if f.exists() else None)!=value['hash']:fail('目标文件在上传期间已改变，未覆盖','FILE_CONFLICT',409)
            if stage.stat().st_size!=value['size'] or filehash(stage)!=value['sha256']:fail('上传暂存校验失败',status=409)
            payload.update(path=value['path'],stage=data['stage'])
        elif kind=='instance.file.restore':
            identity=data.get('trash','')
            if not re.fullmatch(r'[0-9a-f-]{36}',identity):fail('回收编号无效')
            folder=self.storage/'trash'/server;meta=folder/(identity+'.json')
            if not meta.is_file() or not (folder/identity).exists():fail('回收项目不存在',status=404)
            value=json.loads(meta.read_text(encoding='utf-8'));f=self.path(server,value['path'])
            if f.exists() or not f.parent.is_dir():fail('原路径已被占用或父目录不存在，请先处理原路径',status=409)
            owner=f.parent.stat()
            if 'uid' in value and (value['uid'],value['gid'])!=(owner.st_uid,owner.st_gid):fail('原目录属主已改变，回收项目保持原状','FILE_OWNER_UNAVAILABLE',409)
            if value.get('directory'):self.offline(server)
            payload.update(trash=identity,path=value['path'])
        elif kind=='instance.config':
            props=self.properties(server)
            if data.get('hash')!=props['hash']:fail('配置已更改，请刷新','FILE_CONFLICT',409)
            values=data.get('values')
            if not isinstance(values,dict) or set(values)-CONFIG_KEYS:fail('包含不支持的配置项')
            for k,v in values.items():
                if not isinstance(v,str) or len(v)>200 or '\n' in v or '\r' in v:fail('配置值无效')
                if k in ('pvp','white-list','allow-flight') and v not in ('true','false'):fail('布尔配置只允许 true/false')
                if k in ('view-distance','simulation-distance','max-players','spawn-protection') and (not v.isdigit() or not 0<=int(v)<=1000):fail('数字配置超出范围')
                if k=='difficulty' and v not in ('peaceful','easy','normal','hard'):fail('游戏难度无效')
            payload.update(values=values,hash=data.get('hash'))
        elif kind in ('instance.backup.create','instance.backup.restore','instance.backup.delete'):
            self.root(server)
            if not kind.endswith('delete'):self.offline(server)
            if not kind.endswith('create'):
                identity=data.get('backup','')
                if not re.fullmatch(r'[0-9a-f-]{36}',identity):fail('备份编号无效')
                if not (self.backup_folder(server)/(identity+'.zip')).is_file():fail('备份不存在',status=404)
                payload['backup']=identity
        elif kind=='instance.task.create':
            name=data.get('name');action=data.get('action');interval=data.get('interval',0);due=data.get('nextRun')
            if not isinstance(name,str) or not 1<=len(name.strip())<=80:fail('请输入任务名称')
            if action not in ('save','announce','start','stop','restart','backup'):fail('不支持此任务动作')
            if type(interval) is not int or interval!=0 and not 60<=interval<=31536000:fail('重复周期至少一分钟')
            if type(due) is not int or not int(time.time())<=due<=int(time.time())+31536000:fail('请选择未来一年内的执行时间')
            content=data.get('content','')
            if not isinstance(content,str) or len(content)>200 or any(ord(c)<32 for c in content):fail('任务内容无效')
            if action=='announce' and not content.strip():fail('请输入公告内容')
            payload.update(name=name.strip(),action=action,interval=interval,nextRun=due,content=content)
        elif kind=='instance.task.delete':
            task=data.get('task')
            with self.panel.db() as db:
                if not db.execute('SELECT 1 FROM instance_tasks WHERE id=? AND server=?',(task,server)).fetchone():fail('任务不存在',status=404)
            payload['task']=task
        else:fail('未知实例操作')
        labels={'instance.file.write':'保存文件','instance.file.upload':'上传文件','instance.file.upload-staged':'提交上传（同名文件将被替换）','instance.file.delete':'移至回收站','instance.file.restore':'恢复回收项目','instance.file.mkdir':'创建目录','instance.file.rename':'重命名','instance.config':'保存服务器设置（重启后生效）','instance.backup.create':'创建世界备份','instance.backup.restore':'恢复世界备份（替换当前世界）','instance.backup.delete':'删除备份','instance.task.create':'创建计划任务','instance.task.delete':'删除计划任务'}
        return payload,labels[kind]+' · '+self.panel.servers.entry(server).get('name',server)+' '+str(payload.get('path',payload.get('name','')))

    def execute(self,user,kind,payload,operation):
        with self.lock:
            # Revalidate boundaries and conflicts at confirmation, not only at preview.
            data=dict(payload)
            if kind=='instance.file.write':data['text']=base64.b64decode(data['base64']).decode('utf-8')
            self.validate(user,kind,data)
            server=payload['server']
            if kind.startswith('instance.file.'):
                f=self.path(server,payload['path'])
                if kind.endswith('upload-staged'):
                    value,stage=self.staged(user,payload['stage']);temp=f.parent/('.panel-'+operation+'.tmp')
                    try:
                        shutil.copyfile(stage,temp)
                        if filehash(temp)!=value['sha256']:fail('上传落盘校验失败，目标文件未修改',status=503)
                        preserve_owner(temp,f if f.exists() else f.parent,None if f.exists() else 0o644)
                        os.replace(temp,f)
                        try:
                            stage.unlink(missing_ok=True);stage.with_suffix('.json').unlink(missing_ok=True)
                        except OSError:self.panel.audit(user['id'],'UPLOAD_CLEANUP_PENDING',{'operationId':operation},'SERVICE_FAULT')
                    finally:temp.unlink(missing_ok=True)
                elif kind.endswith('restore'):
                    trash=self.storage/'trash'/server;source=trash/payload['trash']
                    if source.is_symlink():fail('回收项目类型无效')
                    stage=f.parent/('.panel-restore-'+operation+'.tmp')
                    try:
                        shutil.move(str(source),str(stage))
                        if stage.is_dir():preserve_world_owners(stage,f.parent,True)
                        else:preserve_owner(stage,f.parent,stat.S_IMODE(stage.stat().st_mode)&0o777)
                        os.replace(stage,f);(trash/(payload['trash']+'.json')).unlink()
                    except Exception:
                        if stage.exists() and not source.exists():shutil.move(str(stage),str(source))
                        raise
                elif kind.endswith(('write','upload')):
                    raw=base64.b64decode(payload['base64']);temp=f.parent/('.panel-'+operation+'.tmp')
                    try:
                        with temp.open('xb') as out:out.write(raw);out.flush();os.fsync(out.fileno())
                        preserve_owner(temp,f if f.exists() else f.parent,None if f.exists() else 0o644)
                        os.replace(temp,f)
                    finally:temp.unlink(missing_ok=True)
                elif kind.endswith('mkdir'):
                    f.mkdir()
                    try:preserve_owner(f,f.parent,0o755)
                    except Exception:f.rmdir();raise
                elif kind.endswith('rename'):os.rename(f,self.path(server,payload['target']))
                elif kind.endswith('delete'):
                    if self.fingerprint(server,payload['path'])!=payload['hash']:fail('文件已改变，未删除','FILE_CONFLICT',409)
                    trash=self.storage/'trash'/server;trash.mkdir(parents=True,exist_ok=True,mode=0o700)
                    info=f.stat();meta=trash/(operation+'.json');meta.write_text(json.dumps({'path':payload['path'],'directory':f.is_dir(),'uid':info.st_uid,'gid':info.st_gid,'deletedAt':int(time.time())},ensure_ascii=False),encoding='utf-8')
                    shutil.move(str(f),str(trash/operation))
                return {'message':'文件操作已完成','operationId':operation}
            if kind=='instance.config':
                f=self.root(server)/'server.properties';lines=f.read_text(encoding='utf-8').splitlines() if f.exists() else []
                keys=payload['values'];lines=[l for l in lines if l.split('=',1)[0].strip() not in keys or l.lstrip().startswith('#')]
                lines += [k+'='+v for k,v in keys.items()]
                temp=f.parent/('.panel-'+operation+'.tmp')
                try:
                    temp.write_text('\n'.join(lines)+'\n',encoding='utf-8');preserve_owner(temp,f if f.exists() else f.parent,None if f.exists() else 0o644);os.replace(temp,f)
                finally:temp.unlink(missing_ok=True)
                return {'message':'配置已保存，重启实例后生效'}
            if kind=='instance.backup.create':
                identity=operation;dest=self.backup_folder(server)/(identity+'.zip');temp=dest.with_suffix('.part')
                world=self.path(server,'world')
                if not world.is_dir():fail('未找到默认 world 世界目录',status=409)
                files=[];size=0
                for p in world.rglob('*'):
                    self.path(server,p.relative_to(self.root(server)).as_posix())
                    if p.is_file():
                        size+=p.stat().st_size;files.append(p)
                        if len(files)>100000 or size>4*1024**3:fail('世界超过网页备份上限（4 GiB / 10万文件）',status=413)
                if shutil.disk_usage(dest.parent).free<size+256*1024**2:fail('备份空间不足',status=409)
                try:
                    with zipfile.ZipFile(temp,'x',zipfile.ZIP_DEFLATED) as z:
                        for f in files:z.write(f,f.relative_to(self.root(server)).as_posix())
                    self.offline(server)
                    os.replace(temp,dest)
                finally:temp.unlink(missing_ok=True)
                return {'message':'世界备份已创建','backup':identity,'size':dest.stat().st_size}
            if kind=='instance.backup.restore':
                source=self.backup_folder(server)/(payload['backup']+'.zip');root=self.root(server)
                stage=root/('.restore-'+operation);old=self.storage/'restore-rollback'/server/operation
                if stage.exists():fail('存在同编号恢复暂存，请人工核对',status=409)
                stage.mkdir();total=0
                try:
                    with zipfile.ZipFile(source) as z:
                        if len(z.infolist())>100000:fail('备份文件数超出上限')
                        for info in z.infolist():
                            rel=PurePosixPath(info.filename);total+=info.file_size
                            if not rel.parts or rel.parts[0]!='world' or rel.is_absolute() or '..' in rel.parts or '\\' in info.filename or ':' in info.filename or stat.S_ISLNK(info.external_attr>>16) or total>4*1024**3:fail('备份路径或容量无效')
                            target=stage.joinpath(*rel.parts)
                            if info.is_dir():target.mkdir(parents=True,exist_ok=True)
                            else:
                                target.parent.mkdir(parents=True,exist_ok=True)
                                with z.open(info) as inp,target.open('xb') as out:shutil.copyfileobj(inp,out)
                                os.chmod(target,(info.external_attr>>16)&0o777 or 0o600)
                    if not (stage/'world').is_dir():fail('备份中没有世界目录')
                    self.offline(server);world=self.path(server,'world')
                    reference=world if world.exists() else root
                    preserve_world_owners(stage/'world',reference)
                    old.parent.mkdir(parents=True,exist_ok=True)
                    if world.exists():shutil.move(str(world),str(old))
                    try:os.replace(stage/'world',world)
                    except Exception:
                        if old.exists():shutil.move(str(old),str(world))
                        raise
                finally:shutil.rmtree(stage,ignore_errors=True)
                return {'message':'世界已恢复，恢复前世界已留存回滚副本'}
            if kind=='instance.backup.delete':
                (self.backup_folder(server)/(payload['backup']+'.zip')).unlink();return {'message':'备份已删除'}
            if kind=='instance.task.create':
                with self.panel.db() as db:db.execute('INSERT INTO instance_tasks(id,actor,server,name,action,content,interval,next_run,enabled,status) VALUES(?,?,?,?,?,?,?,?,1,?)',(operation,user['id'],server,payload['name'],payload['action'],payload['content'],payload['interval'],payload['nextRun'],'WAITING'))
                return {'message':'计划任务已创建','task':operation}
            if kind=='instance.task.delete':
                with self.panel.db() as db:db.execute('DELETE FROM instance_tasks WHERE id=? AND server=?',(payload['task'],server))
                return {'message':'计划任务已删除'}

    def tasks(self,server):
        self.panel.servers.entry(server)
        with self.panel.db() as db:rows=db.execute('SELECT id,server,name,action,content,interval,next_run,enabled,status,result FROM instance_tasks WHERE server=? ORDER BY next_run',(server,)).fetchall()
        return {'tasks':[dict(r) for r in rows]}

    def poll_tasks(self):
        with self.lock:
            now=int(time.time())
            with self.panel.db() as db:
                db.execute('BEGIN IMMEDIATE')
                tasks=db.execute("SELECT * FROM instance_tasks WHERE enabled=1 AND next_run<=? AND status!='RUNNING' LIMIT 10",(now,)).fetchall()
                for t in tasks:db.execute("UPDATE instance_tasks SET status='RUNNING' WHERE id=?",(t['id'],))
            for task in tasks:
                server=task['server']
                try:
                    with self.panel.db() as db:user=db.execute('SELECT * FROM users WHERE id=? AND disabled=0',(task['actor'],)).fetchone()
                    if user is None:fail('创建者已停用，任务不再执行',status=403)
                    self.panel.require(user,'instance.task.create')
                    action=task['action'];server=task['server']
                    if action in ('start','stop','restart'):kind='server.lifecycle';data={'server':server,'action':action}
                    elif action=='backup':kind='instance.backup.create';data={'server':server}
                    else:kind='console';data={'server':server,'command':'save' if action=='save' else 'announce '+task['content']}
                    op=self.panel.prepare(user,kind,{**data,'reason':'计划任务：'+task['name']})
                    result=self.panel.confirm(user,op['operationId']);status='SUCCESS';enabled=bool(task['interval'])
                except Exception as ex:
                    result={'message':str(ex) if isinstance(ex,ControlError) else '任务结果待核对，已停用'};status='FAILED' if isinstance(ex,ControlError) else 'UNCERTAIN';enabled=False
                with self.panel.db() as db:db.execute('UPDATE instance_tasks SET status=?,result=?,enabled=?,next_run=? WHERE id=?',(status,json.dumps(result,ensure_ascii=False),int(enabled),now+task['interval'] if enabled else task['next_run'],task['id']))
                self.panel.audit(task['actor'],'INSTANCE_TASK',{'task':task['id'],'server':server},status)

    def start(self):
        def run():
            while not self.stop.wait(5):
                try:self.poll_tasks();self.clean_uploads()
                except Exception:
                    self.panel.audit('@system','SCHEDULER_ERROR',{},'SERVICE_FAULT')
        threading.Thread(target=run,daemon=True,name='instance-scheduler').start()
