#!/usr/bin/env python3
"""真实子进程验证公开 console 出口，不使用真实认证材料。"""
import json
import os
import pathlib
import signal
import time
import subprocess
import sys
import tempfile
import unittest
import hashlib
import importlib.util

import capture as protected
from prepare import PrepareError


class CaptureProtocolTest(unittest.TestCase):
    def frozen_fixture(self, root):
        source=root/'source';source.mkdir()
        (source/'test_fixture.py').write_text('import unittest\nclass Fixture(unittest.TestCase):\n def test_bad(self):\n  raise ValueError("HOST-PRIVATE-MESSAGE")\n')
        (source/'fixture.py').write_text('def fail(error):\n raise error\n')
        (source/'fixture_test.go').write_text('// 中文来源注释\npackage fixture\nimport "testing"\nfunc TestRoot(t *testing.T) {}\n')
        self.commit_fixture(source)
        return source

    def commit_fixture(self, source):
        for args in [['init','-q'],['add','.'],['-c','user.name=HostFixture','-c','user.email=fixture@example.invalid',
                     '-c','commit.gpgsign=false','-c','core.hooksPath=/dev/null','commit','-qm','test: 静态来源夹具']]:
            subprocess.run(['git',*args],cwd=source,capture_output=True,timeout=5,check=True)
        return subprocess.check_output(['git','rev-parse','HEAD'],cwd=source,text=True,timeout=5).strip()

    def test_captured_failure_ids_and_frames_are_verified_against_frozen_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root=pathlib.Path(directory).resolve();source=self.frozen_fixture(root)
            candidate=subprocess.check_output(['git','rev-parse','HEAD'],cwd=source,text=True,timeout=5).strip()
            result=protected.capture([sys.executable,'-m','unittest','-v','test_fixture.Fixture.test_bad'],
                private_root=str(root/'original'),label='fixture-unit',cwd=source,publish=False)
            self.assertEqual(result.returncode,1)
            result.stdout+=b'--- FAIL: TestRoot (0.01s)\n--- FAIL: TestRoot/HOST-PRIVATE-SUBCASE (0.01s)\n'
            result.stderr+=b'FAIL: test_bad (test_fixture.Fixture.test_bad) (value="HOST-PRIVATE-SUBCASE")\n'
            value=protected.project_output(result,label='quality',candidate=candidate,private_root=root/'source-raw',root=source)
            self.assertTrue(value['unknown'], '被忽略的带参数子例不能被已识别根ID掩盖')
            self.assertEqual({v['testId'] for v in value['testFailures']},{'test_fixture.py::Fixture.test_bad','fixture_test.go::TestRoot'})
            go=next(v for v in value['testFailures'] if v['function']=='TestRoot');self.assertEqual(go['line'],4)
            frame=next(v for v in value['repoFrames'] if v['file']=='test_fixture.py')
            self.assertEqual(frame['function'],'test_bad');self.assertEqual(frame['line'],4)
            self.assertEqual(frame['sourceBlobSHA256'],hashlib.sha256((source/'test_fixture.py').read_bytes()).hexdigest())
            public=json.dumps(value);self.assertNotIn('HOST-PRIVATE',public);self.assertNotIn(str(root),public)
            self.assertTrue(all(set(v)=={'file','line','function','sourceBlobSHA256'} for v in value['repoFrames']))

    def test_projection_rejects_modified_untracked_external_symlink_ambiguous_and_subtest_inputs(self):
        for mode in ['modified','untracked','external','symlink','line','ambiguous','subtest','wrong-candidate']:
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as directory:
                root=pathlib.Path(directory).resolve();source=self.frozen_fixture(root)
                candidate=subprocess.check_output(['git','rev-parse','HEAD'],cwd=source,text=True,timeout=5).strip()
                filename=source/'test_fixture.py';identity='test_fixture.Fixture.test_bad';line=4
                if mode=='modified':filename.write_text(filename.read_text()+'# changed\n')
                if mode=='untracked':filename=source/'test_other.py';filename.write_text('HOST-PRIVATE');identity='test_other.Fixture.test_bad'
                if mode=='external':filename=root/'HOST-PRIVATE.py';filename.write_text('private');identity='external.Fixture.test_bad'
                if mode=='symlink':
                    target=root/'HOST-PRIVATE.py';target.write_text('private');filename.unlink();filename.symlink_to(target)
                if mode=='line':line=99999
                if mode=='ambiguous':
                    (source/'other').mkdir();(source/'other/test_fixture.py').write_bytes(filename.read_bytes());candidate=self.commit_fixture(source)
                if mode=='wrong-candidate':candidate='0'*40
                text=('FAIL: test_bad ('+identity+')\n') if mode!='line' else ''
                if mode=='subtest':text='FAIL: test_bad (test_fixture.Fixture.test_bad) (secret="HOST-PRIVATE")\n--- FAIL: TestRoot/HOST-PRIVATE (0.01s)\n'
                if mode not in ['ambiguous','subtest']:text+='  File "'+str(filename)+'", line '+str(line)+', in HOST_PRIVATE_DYNAMIC_FUNCTION\n'
                result=subprocess.CompletedProcess([],1,b'',text.encode())
                value=protected.project_output(result,label='quality',candidate=candidate,private_root=root/'raw',root=source)
                self.assertTrue(value['unknown']);self.assertEqual(value['testFailures'],[]);self.assertEqual(value['repoFrames'],[])
                self.assertNotIn('HOST-PRIVATE',json.dumps(value));self.assertNotIn(str(root),json.dumps(value))

    def test_exception_projection_uses_static_frames_and_exact_prepare_code_membership(self):
        with tempfile.TemporaryDirectory() as directory:
            root=pathlib.Path(directory).resolve();source=self.frozen_fixture(root)
            candidate=subprocess.check_output(['git','rev-parse','HEAD'],cwd=source,text=True,timeout=5).strip()
            spec=importlib.util.spec_from_file_location('host_frame_fixture',source/'fixture.py')
            module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
            for error,code in [(PrepareError('PREPARE_RESPONSE_INVALID'),'PREPARE_RESPONSE_INVALID'),
                               (PrepareError('PREPARE_HOST_PRIVATE_CANARY'),'unknown'),
                               (type('HOST_PRIVATE_DYNAMIC_CLASS',(Exception,),{})('HOST_PRIVATE_MESSAGE'),'unknown')]:
                try:module.fail(error)
                except BaseException as caught:
                    value=protected.project_exception(caught,candidate=candidate,private_root=root/'raw',root=source,prepare_type=PrepareError)
                self.assertEqual(value['errorCode'],code)
                self.assertEqual([v['function'] for v in value['repoFrames']],['fail'])
                self.assertNotIn('HOST_PRIVATE',json.dumps(value));self.assertNotIn(str(root),json.dumps(value))

    def test_public_report_write_failure_never_overwrites_original_failed_exit(self):
        for code in [0,7]:
            with self.subTest(code=code),tempfile.TemporaryDirectory() as directory:
                root=pathlib.Path(directory);blocked=root/'blocked';blocked.write_text('not-directory')
                result=subprocess.run([sys.executable,str(pathlib.Path(__file__).with_name('capture.py')),
                    '--private-root',str(root/'raw'),'--label','quality','--public-report',str(blocked/'report.json'),
                    '--candidate','0'*40,'--',sys.executable,'-c','import sys;sys.exit('+str(code)+')'],capture_output=True)
                self.assertEqual(result.returncode,code or 125)
                self.assertNotIn(str(root).encode(),result.stdout+result.stderr)
                self.assertIn(b'diagnostic-write-failed',result.stdout)

    def test_optional_public_report_keeps_actual_exit_private_bytes_and_single_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            root=pathlib.Path(directory);marker=root/'once';report=root/'public.json';canary='HOST-PRIVATE-OUTPUT-CANARY'
            script='import pathlib,sys;p=pathlib.Path('+repr(str(marker))+');p.write_text(p.read_text()+"x" if p.exists() else "x");print('+repr(canary)+');sys.exit(7)'
            command=[sys.executable,str(pathlib.Path(__file__).with_name('capture.py')),'--private-root',str(root/'raw'),
                     '--label','quality','--public-report',str(report),'--candidate','0'*40,'--',sys.executable,'-c',script]
            result=subprocess.run(command,capture_output=True)
            self.assertEqual(result.returncode,7)
            value=json.loads(report.read_bytes());self.assertIs(value['productGate'],False)
            self.assertEqual(value['exit'],7);self.assertEqual(value['state'],'failed');self.assertTrue(value['unknown'])
            self.assertEqual(marker.read_text(),'x');self.assertEqual(value['testFailures'],[]);self.assertEqual(value['repoFrames'],[])
            self.assertNotIn(canary,report.read_text());self.assertNotIn(str(root),report.read_text())
            self.assertEqual(value['raw']['stdout'],dict(bytes=len((canary+'\n').encode()),sha256=hashlib.sha256((canary+'\n').encode()).hexdigest()))
            self.assertTrue(all(p.stat().st_mode&0o777==0o600 for p in (root/'raw').glob('*.raw')))
            self.assertEqual(report.stat().st_mode&0o777,0o600)

    def test_nonzero_child_keeps_private_bytes_and_safe_console(self):
        canary = 'SYNTHETIC-AUTH-CANARY-21'
        with tempfile.TemporaryDirectory() as root:
            command = [sys.executable, str(pathlib.Path(__file__).with_name('capture.py')),
                       '--private-root', root, '--label', 'protocol-canary', '--', sys.executable,
                       '-c', "import sys;print('SYNTHETIC-AUTH-CANARY-21');sys.stderr.write('SYNTHETIC-AUTH-CANARY-21');sys.exit(7)"]
            process = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            self.assertEqual(process.returncode, 7)
            self.assertNotIn(canary.encode(), process.stdout + process.stderr)
            summary = json.loads(process.stdout)
            self.assertEqual(summary['exit'], 7)
            self.assertEqual(summary['id'], 'protocol-canary')
            logs = list(pathlib.Path(root).glob('*.raw'))
            self.assertEqual(len(logs), 2)
            for log in logs:
                self.assertIn(canary, log.read_text())
                self.assertEqual(log.stat().st_mode & 0o777, 0o600)

    def test_repeated_signals_do_not_interrupt_child_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            child = root / 'child.py'
            child.write_text("import signal,time,pathlib,sys\np=pathlib.Path(sys.argv[1])\ndef stop(n,f):\n with (p/'signals').open('a') as s:s.write(str(n)+'\\n')\n (p/'cleaning').touch()\nsignal.signal(signal.SIGTERM,stop)\nsignal.signal(signal.SIGINT,stop)\n(p/'ready').touch()\nwhile not (p/'finish').exists():time.sleep(.01)\nsys.exit(3)\n")
            command = [sys.executable, str(pathlib.Path(__file__).with_name('capture.py')),
                       '--private-root', str(root / 'raw'), '--label', 'protocol-signal', '--', sys.executable, str(child), str(root)]
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                end = time.monotonic() + 5
                while not (root / 'ready').exists() and time.monotonic() < end: time.sleep(.01)
                self.assertTrue((root / 'ready').exists())
                process.send_signal(signal.SIGTERM)
                while not (root / 'cleaning').exists() and time.monotonic() < end: time.sleep(.01)
                self.assertTrue((root / 'cleaning').exists())
                process.send_signal(signal.SIGINT)
                time.sleep(.1)
                process.send_signal(signal.SIGTERM)
                time.sleep(.1)
                (root / 'finish').touch()
                output, errors = process.communicate(timeout=5)
                self.assertEqual(process.returncode, 3)
                self.assertEqual(len((root / 'signals').read_text().splitlines()), 1)
                receipt = json.loads(next((root / 'raw').glob('*.json')).read_text())
                self.assertEqual(len(receipt['signals']), 3)
            finally:
                (root / 'finish').touch()
                if process.poll() is None:
                    process.terminate()
                    process.communicate(timeout=5)


if __name__ == '__main__':
    unittest.main()
