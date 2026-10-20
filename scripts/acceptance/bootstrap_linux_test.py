#!/usr/bin/env python3
"""来源验证 seam：实际磁盘字节、archive 越界与失败时禁止安装。"""
import hashlib
import io
import contextlib
import copy
import json
import os
import pathlib
import subprocess
import sys
import tarfile
import tempfile
import unittest
import urllib.error
from unittest.mock import patch

import bootstrap_linux as bootstrap
from bootstrap_linux import BootstrapError, extract_tar, verify_file, apt_policy_summary


class BootstrapProtocolTest(unittest.TestCase):
    def test_main_early_failure_writes_public_diagnostic_before_any_download(self):
        with tempfile.TemporaryDirectory() as root:
            root = pathlib.Path(root);report = root / 'public/bootstrap.json';output = io.StringIO()
            with patch.object(bootstrap.platform, 'system', return_value='HostProtocol'), contextlib.redirect_stdout(output):
                code = bootstrap.main(['--prefix', str(root / 'tools'), '--record', str(root / 'private.json'),
                                       '--public-report', str(report)])
            value = json.loads(report.read_bytes())
            self.assertEqual(code, 1);self.assertEqual(value['state'], 'failed')
            self.assertEqual(value['stage'], 'host-check')
            self.assertEqual(value['errorCode'], 'BOOTSTRAP_REQUIRES_LINUX_AMD64')
            self.assertEqual(value['exceptionClass'], 'BootstrapError');self.assertIs(value['productGate'], False)
            self.assertIsNone(value['httpStatus']);self.assertIsNone(value['childExit'])
            self.assertFalse((root / 'tools').exists());self.assertEqual(report.stat().st_mode & 0o777, 0o600)
            self.assertNotIn(str(root), report.read_text() + output.getvalue())

    def test_main_projects_fixed_failure_fields_without_message_body_path_or_dynamic_class(self):
        canary = 'HOST-SYNTHETIC-PRIVATE-MESSAGE-BODY-CLASS'
        dynamic_error = type(canary, (RuntimeError,), {})
        cases = [('download-python', urllib.error.HTTPError('https://user:'+canary+'@invalid/?token='+canary,
                    403, canary, {'X-Fixture':canary}, io.BytesIO(canary.encode())), 'BOOTSTRAP_HTTP_ERROR', 'HTTPError', 403),
                 ('download-go', urllib.error.URLError(canary), 'BOOTSTRAP_DOWNLOAD_ERROR', 'URLError', None),
                 ('extract-python', bootstrap.BootstrapError('BOOTSTRAP_ARCHIVE_UNSAFE'), 'BOOTSTRAP_ARCHIVE_UNSAFE', 'BootstrapError', None),
                 ('prefix-init', OSError(canary), 'BOOTSTRAP_OS_ERROR', 'OSError', None),
                 ('python-runtime-metadata', ValueError(canary), 'BOOTSTRAP_VALUE_ERROR', 'ValueError', None),
                 ('download-pip', dynamic_error(canary), 'BOOTSTRAP_UNEXPECTED', 'Exception', None),
                 ('download-pip', bootstrap.BootstrapError(canary), 'BOOTSTRAP_UNEXPECTED', 'BootstrapError', None)]
        for stage, error, expected, kind, status in cases:
            with self.subTest(stage=stage,kind=kind), tempfile.TemporaryDirectory() as root:
                root=pathlib.Path(root);report=root/'public.json';output=io.StringIO()
                def controlled_install(*args, diagnostic, **kwargs):
                    self.assertEqual(json.loads(report.read_bytes())['state'],'pending')
                    diagnostic.enter(stage)
                    raise error
                with patch.object(bootstrap,'install',controlled_install),contextlib.redirect_stdout(output):
                    code=bootstrap.main(['--prefix',str(root/'tools'),'--record',str(root/'private'),
                                         '--public-report',str(report)])
                value=json.loads(report.read_bytes())
                self.assertEqual(set(value),{'schemaVersion','kind','productGate','state','stage','errorCode',
                    'exceptionClass','httpStatus','childExit','browsers','sourceLockSHA256'})
                self.assertEqual(code,1);self.assertEqual(value['state'],'failed');self.assertIs(value['productGate'],False)
                self.assertEqual(value['stage'],stage);self.assertEqual(value['errorCode'],expected)
                self.assertEqual(value['exceptionClass'],kind);self.assertEqual(value['httpStatus'],status)
                self.assertNotIn(canary,report.read_text()+output.getvalue());self.assertNotIn(str(root),report.read_text())
                if isinstance(error,urllib.error.HTTPError):error.close()

    def test_main_records_real_child_exit_and_keeps_original_output_private(self):
        with tempfile.TemporaryDirectory() as root:
            root=pathlib.Path(root);report=root/'public.json';output=io.StringIO();canary='HOST-PRIVATE-CHILD-STDERR'
            def controlled_install(*args, diagnostic, **kwargs):
                diagnostic.enter('python-official-setup')
                child=bootstrap.capture([sys.executable,'-c','import sys;sys.stderr.write('+repr(canary)+');sys.exit(7)'],
                    private_root=str(root/'raw'),label='host-child',publish=False)
                raise bootstrap.BootstrapError('BOOTSTRAP_STEP_FAILED_PYTHON_OFFICIAL_SETUP',child_exit=child.returncode)
            with patch.object(bootstrap,'install',controlled_install),contextlib.redirect_stdout(output):
                code=bootstrap.main(['--prefix',str(root/'tools'),'--record',str(root/'private'),'--public-report',str(report)])
            self.assertEqual(code,1);value=json.loads(report.read_bytes());self.assertEqual(value['childExit'],7)
            self.assertEqual(value['errorCode'],'BOOTSTRAP_STEP_FAILED_PYTHON_OFFICIAL_SETUP')
            self.assertNotIn(canary,report.read_text()+output.getvalue())
            raw=list((root/'raw').glob('*.raw'));self.assertTrue(any(canary.encode() in p.read_bytes() for p in raw))
            self.assertTrue(all(p.stat().st_mode&0o777==0o600 for p in raw))

    def test_main_success_and_final_report_failure_never_turn_into_false_success(self):
        for mode in ['success','write-failed-after-success','write-failed-after-http']:
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as root:
                root=pathlib.Path(root);report=root/'public.json';output=io.StringIO();atomic=bootstrap.atomic_json;writes=[]
                http_error=urllib.error.HTTPError('https://invalid',502,'HOST-SECRET',{},None)
                def writer(path,value):
                    writes.append(copy.deepcopy(value))
                    if len(writes)>1 and mode!='success':raise OSError('HOST-SECRET-FSYNC-PATH')
                    atomic(path,value)
                def controlled_install(*args, diagnostic, **kwargs):
                    self.assertEqual(json.loads(report.read_bytes())['state'],'pending')
                    if mode=='write-failed-after-http':raise http_error
                with patch.object(bootstrap,'install',controlled_install),patch.object(bootstrap,'atomic_json',writer),contextlib.redirect_stdout(output):
                    code=bootstrap.main(['--prefix',str(root/'tools'),'--record',str(root/'private'),'--public-report',str(report)])
                value=json.loads(report.read_bytes());console=json.loads(output.getvalue())
                self.assertEqual(code,0 if mode=='success' else 1);self.assertIs(value['productGate'],False)
                if mode=='success':self.assertEqual(value['state'],'completed')
                else:
                    self.assertEqual(value['state'],'pending');self.assertEqual(console['reportErrorCode'],'BOOTSTRAP_REPORT_WRITE_FAILED')
                    if mode.endswith('http'):self.assertEqual(console['errorCode'],'BOOTSTRAP_HTTP_ERROR');self.assertEqual(console['httpStatus'],502)
                self.assertNotIn('HOST-SECRET',report.read_text()+output.getvalue())
                http_error.close()

    def test_python_location_rejects_default_cache_and_changed_loaded_library_bytes(self):
        with tempfile.TemporaryDirectory() as root:
            root=pathlib.Path(root).resolve();digest=hashlib.sha256(b'official-fixture-lib').hexdigest()
            valid=dict(prefix=str(root),basePrefix=str(root),stdlib=str(root/'lib/python3.14'),
                encodings=str(root/'lib/python3.14/encodings/__init__.py'),
                loadedLibpython=[dict(path=str(root/'lib/libpython3.14.so.1.0'),sha256=digest)],LIBDIR='/opt/hostedtoolcache/compile-only')
            bootstrap.verify_python_location(valid,root,digest)
            for field in ['prefix','basePrefix','stdlib','encodings','loadedLibpython','loaded-hash','missing-loaded']:
                with self.subTest(field=field):
                    value=copy.deepcopy(valid)
                    if field=='loadedLibpython':value[field][0]['path']='/opt/hostedtoolcache/Python/3.14.7/x64/lib/libpython3.14.so.1.0'
                    elif field=='loaded-hash':value['loadedLibpython'][0]['sha256']='f'*64
                    elif field=='missing-loaded':value['loadedLibpython']=[]
                    else:value[field]='/opt/hostedtoolcache/wrong-source'
                    with self.assertRaisesRegex(bootstrap.BootstrapError,'BOOTSTRAP_PYTHON_LOCATION_MISMATCH'):
                        bootstrap.verify_python_location(value,root,digest)

    def test_setup_first_process_and_github_env_use_only_new_loader_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root=pathlib.Path(directory).resolve();prefix=root/'tools';python_root=prefix/'toolcache/Python/3.14.7/x64'
            expected=str(python_root/'lib');seen=[];read_text=pathlib.Path.read_text
            def os_release(path,*args,**kwargs):
                return 'ID=ubuntu\nVERSION_ID="24.04"\n' if str(path)=='/etc/os-release' else read_text(path,*args,**kwargs)
            def download(url,path,digest=None):
                pathlib.Path(path).write_bytes(b'Host protocol only')
                return dict(authority='host-protocol-fixture',sha256=hashlib.sha256(b'Host protocol only').hexdigest(),bytes=18)
            def extract(path,destination):
                destination=pathlib.Path(destination)
                if 'python-' in pathlib.Path(path).name:
                    (destination/'lib').mkdir(parents=True);(destination/'setup.sh').write_text('set -e\n')
                    (destination/'lib/libpython3.14.so.1.0').write_bytes(b'Host library fixture')
            def capture(argv,*,env,label,**kwargs):
                seen.append(label)
                self.assertEqual(env['LD_LIBRARY_PATH'],expected)
                self.assertNotIn('HOST-FOREIGN-LIBRARY',env['LD_LIBRARY_PATH'])
                outputs={'verify-go':b'go1.27.1 linux/amd64','verify-node-js':b'v26.4.0','verify-kind':b'v0.33.0',
                         'verify-kubectl':b'{"clientVersion":{"gitVersion":"v1.28.15"}}'}
                if label=='python-runtime-metadata':
                    self.assertIn('-I',argv);self.assertIn('/proc/self/maps',argv[-1])
                    outputs[label]=json.dumps(dict(version='3.14.7',gilDisabled=0,pip='26.2.1',PyYAML='6.0.3',
                        prefix=str(python_root),basePrefix=str(python_root),stdlib=str(python_root/'lib/python3.14'),
                        encodings=str(python_root/'lib/python3.14/encodings/__init__.py'),
                        loadedLibpython=[dict(path=str(python_root/'lib/libpython3.14.so.1.0'),
                            sha256=hashlib.sha256(b'Host library fixture').hexdigest())])).encode()
                return subprocess.CompletedProcess(argv,0,outputs.get(label,b''),b'')
            with patch.object(bootstrap.platform,'system',return_value='Linux'),patch.object(bootstrap.platform,'machine',return_value='x86_64'),\
                 patch.object(pathlib.Path,'read_text',os_release),patch.object(bootstrap,'download',download),\
                 patch.object(bootstrap,'extract_tar',extract),patch.object(bootstrap,'capture',capture),\
                 patch.dict(os.environ,LD_LIBRARY_PATH='HOST-FOREIGN-LIBRARY',GITHUB_ENV=str(root/'github-env'),GITHUB_PATH=str(root/'github-path')):
                bootstrap.install(prefix,root/'private/bootstrap.json')
            self.assertEqual(seen[0],'python-official-setup')
            self.assertIn('LD_LIBRARY_PATH='+expected+'\n',(root/'github-env').read_text())
            self.assertNotIn('HOST-FOREIGN-LIBRARY',(root/'github-env').read_text())

    def test_download_mismatch_rejects_actual_bytes(self):
        with tempfile.TemporaryDirectory() as root:
            path = pathlib.Path(root) / 'asset'
            path.write_bytes(b'changed bytes')
            with self.assertRaisesRegex(BootstrapError, 'BOOTSTRAP_DIGEST_MISMATCH'):
                verify_file(path, 'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad')
            path.write_bytes(b'abc')
            self.assertEqual(verify_file(path, 'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad'), 3)

    def test_unsafe_archive_cannot_escape_install_prefix(self):
        with tempfile.TemporaryDirectory() as root:
            root = pathlib.Path(root)
            archive = root / 'unsafe.tar'
            with tarfile.open(archive, 'w') as stream:
                member = tarfile.TarInfo('../outside')
                member.size = 1
                stream.addfile(member, io.BytesIO(b'x'))
            with self.assertRaisesRegex(BootstrapError, 'BOOTSTRAP_ARCHIVE_UNSAFE'):
                extract_tar(archive, root / 'install')
            self.assertFalse((root / 'outside').exists())

    def test_apt_policy_keeps_suite_candidate_and_excludes_url_credentials(self):
        summary = apt_policy_summary('libnss3:\n  Installed: 1:2.3\n  Candidate: 1:2.4\n  Version table:\n     1:2.4 500\n        500 https://user:SYNTHETIC@archive.ubuntu.com/ubuntu noble-updates/main amd64 Packages\n')
        self.assertEqual(summary[0]['package'], 'libnss3')
        self.assertEqual(summary[0]['candidate'], '1:2.4')
        self.assertEqual(summary[0]['origins'], [{'host': 'archive.ubuntu.com', 'suite': 'noble-updates', 'component': 'main'}])
        self.assertNotIn('SYNTHETIC', str(summary))


if __name__ == '__main__':
    unittest.main()
