#!/usr/bin/python3 -I
"""Restricted ownership helper for service-created instance files.

Install root-owned as /usr/local/libexec/breakline-panel-file-owner.
systemd --socket mode authenticates AF_UNIX peer credentials; legacy sudo mode
accepts no arguments. Both receive bounded JSON on stdin.
All filesystem traversal uses openat/O_NOFOLLOW, including parent components.
"""
import json
import os
import pathlib
import pwd
import re
import stat
import subprocess
import sys
import ctypes
import socket
import struct

SERVICE='breakline-admin'
CONFIG='/etc/breakline-admin/config.json'
_ACL=None

def acl_library():
    global _ACL
    if _ACL is None:
        lib=ctypes.CDLL('libacl.so.1',use_errno=True)
        lib.acl_get_fd.argtypes=[ctypes.c_int];lib.acl_get_fd.restype=ctypes.c_void_p
        lib.acl_to_text.argtypes=[ctypes.c_void_p,ctypes.POINTER(ctypes.c_ssize_t)];lib.acl_to_text.restype=ctypes.c_void_p
        lib.acl_from_text.argtypes=[ctypes.c_char_p];lib.acl_from_text.restype=ctypes.c_void_p
        lib.acl_set_fd.argtypes=[ctypes.c_int,ctypes.c_void_p];lib.acl_set_fd.restype=ctypes.c_int
        lib.acl_free.argtypes=[ctypes.c_void_p];lib.acl_free.restype=ctypes.c_int
        lib.acl_cmp.argtypes=[ctypes.c_void_p,ctypes.c_void_p];lib.acl_cmp.restype=ctypes.c_int
        _ACL=lib
    return _ACL

def desired_acl(reference,mode,service_uid,directory):
    lib=acl_library();source=lib.acl_get_fd(reference)
    if not source:raise OSError(ctypes.get_errno(),'Cannot read reference ACL')
    text=None
    try:
        text=lib.acl_to_text(source,None)
        if not text:raise OSError(ctypes.get_errno(),'Cannot read ACL text')
        rows=[line.split(':') for line in ctypes.string_at(text).decode().strip().splitlines()]
    finally:
        if text:lib.acl_free(text)
        lib.acl_free(source)
    bits=lambda value:sum(bit for char,bit in zip(value,(4,2,1)) if char!='-')
    perms=lambda value:''.join(char if value&bit else '-' for char,bit in zip('rwx',(4,2,1)))
    mask=next((bits(value) for tag,qualifier,value in rows if tag=='mask'),7)
    result=[];union=0;service_names={SERVICE,str(service_uid)}
    try:service_names.add(pwd.getpwuid(service_uid).pw_name)
    except KeyError:pass
    for tag,qualifier,value in rows:
        if tag=='mask' or tag=='user' and qualifier in service_names:continue
        if tag=='user' and not qualifier:permission=(mode>>6)&7
        elif tag=='other':permission=mode&7
        else:permission=bits(value)&mask&((mode>>3)&7);union|=permission
        result.append(tag+':'+qualifier+':'+perms(permission))
    service=7 if directory else 6;result+=['user:'+str(service_uid)+':'+perms(service),'mask::'+perms(union|service)]
    acl=lib.acl_from_text(('\n'.join(result)+'\n').encode())
    if not acl:raise OSError(ctypes.get_errno(),'Cannot construct bounded ACL')
    return acl

def roots():
    info=os.stat(CONFIG,follow_symlinks=False)
    if info.st_uid!=0 or info.st_mode&0o022:raise PermissionError('Untrusted configuration ownership')
    config=json.loads(pathlib.Path(CONFIG).read_text())
    result=[]
    for row in config['servers']:
        unit=row.get('unit','')
        if not re.fullmatch(r'breakline-(hub|dam|space|babel)',unit):continue
        value=row.get('directory')
        if not value:
            run=subprocess.run(['/usr/bin/systemctl','show',unit,'--property=WorkingDirectory','--value'],capture_output=True,text=True,timeout=3,check=True)
            value=run.stdout.strip()
        root=pathlib.Path(value)
        if root.is_absolute() and len(root.parts)>=3 and root.resolve()==root:result.append(root)
    return result

def beneath(root,target):
    target=pathlib.Path(target)
    if not target.is_absolute():raise PermissionError('Absolute instance path required')
    rel=target.relative_to(root)
    if '..' in rel.parts or any(any(ord(c)<32 for c in p) for p in rel.parts):raise PermissionError('Invalid path')
    return rel

def open_under(root,relative):
    fd=os.open(str(root),os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        for index,part in enumerate(relative.parts):
            flags=os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK
            if index<len(relative.parts)-1:flags|=os.O_DIRECTORY
            nextfd=os.open(part,flags,dir_fd=fd);os.close(fd);fd=nextfd
        info=os.fstat(fd)
        if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):raise PermissionError('Only regular files and directories')
        return fd
    except Exception:os.close(fd);raise

def apply(request,allowed,service_uid):
    mode=request.get('mode')
    if type(mode) is not int or not 0<=mode<=0o777:raise PermissionError('Special permission bits are prohibited')
    target=request.get('path');reference=request.get('reference')
    if not isinstance(target,str) or not isinstance(reference,str) or len(target)>2048 or len(reference)>2048:raise PermissionError('Invalid request')
    root=next((root for root in allowed if pathlib.Path(target).is_relative_to(root) and pathlib.Path(reference).is_relative_to(root)),None)
    if root is None:raise PermissionError('Outside configured game instances')
    target_rel=beneath(root,target);reference_rel=beneath(root,reference)
    if not target_rel.parts:raise PermissionError('Instance root cannot be changed')
    fd=open_under(root,target_rel);ref=None;acl=None
    try:
        ref=open_under(root,reference_rel);before=os.fstat(fd);desired=os.fstat(ref)
        acl=desired_acl(ref,mode,service_uid,stat.S_ISDIR(before.st_mode));lib=acl_library()
        if before.st_uid!=service_uid:
            current=lib.acl_get_fd(fd)
            try:
                if (before.st_uid,before.st_gid)==(desired.st_uid,desired.st_gid) and current and lib.acl_cmp(current,acl)==0:return
            finally:
                if current:lib.acl_free(current)
            raise PermissionError('Target was not created by the panel service')
        os.fchown(fd,desired.st_uid,desired.st_gid);os.fchmod(fd,mode)
        if lib.acl_set_fd(fd,acl)!=0:raise OSError(ctypes.get_errno(),'Cannot preserve panel ACL')
    finally:
        if acl:acl_library().acl_free(acl)
        os.close(fd)
        if ref is not None:os.close(ref)

def main():
    if os.geteuid()!=0:raise PermissionError('Restricted service invocation required')
    if sys.argv[1:]==['--socket']:
        # systemd Accept=yes passes the connected AF_UNIX socket as stdin.
        peer=socket.socket(fileno=os.dup(0))
        try:_,uid,_=struct.unpack('3i',peer.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12))
        finally:peer.close()
        if uid!=pwd.getpwnam(SERVICE).pw_uid:raise PermissionError('Untrusted socket peer')
    elif len(sys.argv)!=1 or os.environ.get('SUDO_USER')!=SERVICE:
        raise PermissionError('Restricted service invocation required')
    body=sys.stdin.buffer.read(1024*1024+1)
    if len(body)>1024*1024:raise ValueError('Request too large')
    value=json.loads(body);requests=value if isinstance(value,list) else [value]
    if not 1<=len(requests)<=256:raise ValueError('Invalid batch size')
    allowed=roots();service_uid=pwd.getpwnam(SERVICE).pw_uid
    for request in requests:
        if not isinstance(request,dict) or set(request)!={'path','reference','mode'}:raise ValueError('Invalid fields')
        apply(request,allowed,service_uid)
    print(json.dumps({'ok':True,'processed':len(requests)}))

if __name__=='__main__':
    try:main()
    except Exception:
        print(json.dumps({'ok':False,'error':'FILE_OWNER_REQUEST_REJECTED'}));sys.exit(1)
