"""Root-only Linux filesystem proof. All changes stay in a disposable fixture."""
import json
import os
import stat
import tempfile
import unittest
from unittest.mock import patch
import socket
from pathlib import Path
if os.name=='posix':
    import pwd
    import file_owner_helper as helper

@unittest.skipUnless(os.name=='posix' and hasattr(os,'geteuid') and os.geteuid()==0,'Linux root fixture required')
class FileOwnerHelperTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='breakline-owner-proof-');self.root=Path(self.tmp.name);self.root.chmod(0o755);self.user=pwd.getpwnam('nobody')
        self.reference=self.root/'reference';self.reference.write_text('reference');self.reference.chmod(0o600)
    def tearDown(self):self.tmp.cleanup()
    def apply(self,path,mode=0o600,reference=None):return helper.apply({'path':str(path),'reference':str(reference or self.reference),'mode':mode},[self.root],self.user.pw_uid)
    def target(self,name='target'):
        target=self.root/name;target.write_text('temporary');os.chown(target,self.user.pw_uid,self.user.pw_gid);return target
    def test_regular_file_preserves_root_owner_and_is_idempotent(self):
        target=self.target();self.apply(target);self.apply(target);self.assertEqual(target.stat().st_uid,0);self.assertEqual(target.stat().st_mode&0o007,0)
    def test_socket_checks_real_peer_identity(self):
        first,second=socket.socketpair()
        descriptor=os.dup(first.fileno())
        try:
            with patch.object(helper,'SERVICE','nobody'),patch.object(helper.sys,'argv',['helper','--socket']),patch.object(helper.os,'dup',return_value=descriptor):
                with self.assertRaisesRegex(PermissionError,'Untrusted socket peer'):helper.main()
        finally:first.close();second.close()
    def test_outside_instance_denied(self):
        with self.assertRaises((OSError,ValueError)):self.apply('/etc/passwd')
    def test_special_bits_denied(self):
        target=self.target()
        with self.assertRaises(PermissionError):self.apply(target,0o4755)
        self.assertEqual(target.stat().st_uid,self.user.pw_uid)
    def test_symlink_leaf_and_parent_denied(self):
        target=self.target();link=self.root/'link';link.symlink_to(target)
        with self.assertRaises(OSError):self.apply(link)
        folder=self.root/'sub';folder.mkdir();nested=folder/'target';nested.write_text('nested');os.chown(nested,self.user.pw_uid,self.user.pw_gid);alias=self.root/'alias';alias.symlink_to(folder,target_is_directory=True)
        with self.assertRaises(OSError):self.apply(alias/'target')
        self.assertEqual(nested.stat().st_uid,self.user.pw_uid)
    def test_existing_root_file_cannot_be_changed(self):
        root_file=self.root/'root-file';root_file.write_text('root');root_file.chmod(0o600)
        with self.assertRaises(PermissionError):self.apply(root_file,0o644)
        self.assertEqual(stat.S_IMODE(root_file.stat().st_mode),0o600)
    def test_panel_keeps_access_without_granting_other_users_access(self):
        reference=self.root/'private-world';reference.mkdir(mode=0o700);folder=self.root/'restored-world';folder.mkdir();os.chown(folder,self.user.pw_uid,self.user.pw_gid);self.apply(folder,0o700,reference)
        target=folder/'level.dat';target.write_text('world');os.chown(target,self.user.pw_uid,self.user.pw_gid);self.apply(target)
        child=os.fork()
        if child==0:
            try:
                os.setgroups([]);os.setgid(self.user.pw_gid);os.setuid(self.user.pw_uid);assert target.read_text()=='world';target.write_text('updated');(folder/'new').write_text('new');os._exit(0)
            except Exception:os._exit(1)
        _,status=os.waitpid(child,0);self.assertEqual(os.waitstatus_to_exitcode(status),0);self.assertEqual(target.stat().st_uid,0);self.assertEqual(target.stat().st_mode&0o007,0);self.assertEqual(folder.stat().st_mode&0o007,0)

if __name__=='__main__':unittest.main(verbosity=2)
