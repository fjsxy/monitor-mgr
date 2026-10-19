"""原业务异常的私有留存与公开诊断边界；不建立产品通过键。"""
import json
import pathlib
import stat
import tempfile
import types
import unittest

import business
import gate
import hashlib
import base64
import subprocess
from unittest.mock import patch


class BusinessFailureBoundary(unittest.TestCase):
    def test_c5_browser_report_alias_keeps_one_semantic_kind_and_original_events(self):
        """C5 I01的同path/hash不同kind结构；可移植受控报告不冒充原CI passing。"""
        with tempfile.TemporaryDirectory() as directory:
            root=pathlib.Path(directory);report=root/'browser/I01.primary/playwright.json';report.parent.mkdir(parents=True)
            data=json.dumps({'suites':[{'specs':[{'title':'I01.primary 受控原事件','tests':[{'status':'expected','results':[{'status':'passed'}]}]}]}]}).encode()
            report.write_bytes(data);suite=types.SimpleNamespace(out=root)
            event=business._browser_report(suite,{'testSelectors':['I01.primary']})
            refs=business._refs(root)
            gate.Evidence(root).closure({'browserEventRef':event,'evidenceRefs':refs})
            self.assertEqual(next(v for v in refs if v['path']==event['path']),event)
            self.assertEqual(hashlib.sha256(report.read_bytes()).hexdigest(),event['sha256'])
            gate._events({'browserEventRef':event},{'layer':'B','testSelectors':['I01.primary']},gate.Evidence(root))
            for mode in ['hash','kind','path']:
                bad=dict(event)
                if mode=='hash':bad['sha256']='f'*64
                elif mode=='kind':bad['kind']='safe-json'
                else:bad['path']='browser/other/playwright.json'
                with self.subTest(mode=mode),self.assertRaises(gate.GateError):
                    gate.Evidence(root).closure({'browserEventRef':event,'evidenceRefs':[bad]})

    def test_agent_and_workbench_producers_use_same_report_kind_as_collection(self):
        """受控子进程只写Playwright原结构；不启动浏览器或建立B通过键。"""
        import agent_browser,workbench_browser
        for module,case in [(agent_browser,'U02.primary'),(workbench_browser,'U06.primary')]:
            with self.subTest(module=module.__name__),tempfile.TemporaryDirectory() as directory:
                root=pathlib.Path(directory);suite=types.SimpleNamespace(out=root,base='http://127.0.0.1:1',session=None,password='fixture-password',
                    secret_values={},proxies=[],cluster={'id':'fixture-cluster'},targetns='fixture-ns',target='control',initial_uid='original-platform',ksms=[],
                    a=types.SimpleNamespace(control_pod_config='private',control_config='private',control_context='explicit',kubectl='kubectl',prefix='fixture'),
                    get=lambda *a,**kw:{'items':[]})
                def execute(command,**kwargs):
                    attachments=[dict(name=name,body=base64.b64encode(b'{}').decode()) for name in
                        ['browser-console.json','browser-layout.json','account-pattern-login.json','account-pattern-create.json']]
                    report={'suites':[{'specs':[{'title':case+' 受控事件','tests':[{'status':'expected','results':[{'status':'passed','attachments':attachments}]}]}]}]}
                    pathlib.Path(kwargs['env']['PLAYWRIGHT_JSON_OUTPUT_FILE']).write_text(json.dumps(report))
                    return subprocess.CompletedProcess(command,0,b'controlled-private-output',b'')
                with patch.object(module.subprocess,'run',side_effect=execute):
                    produced=module.run_browser(suite,case) if module is agent_browser else module.execute(suite,case,{})
                event=business._browser_report(suite,{'testSelectors':[case]})
                report_ref=next(v for v in produced['evidenceRefs'] if v['path']==event['path'])
                self.assertEqual(report_ref,event)
                reader=gate.Evidence(root);reader.closure({'browserEventRef':event,'evidenceRefs':business._refs(root),'nested':[report_ref]})
                gate._events({'browserEventRef':event},{'layer':'B','testSelectors':[case]},reader)

    def test_original_exception_is_private_and_public_summary_has_no_message_or_private_path(self):
        with tempfile.TemporaryDirectory() as directory:
            session=types.SimpleNamespace(private=pathlib.Path(directory))
            marker='SYNTHETIC-PRIVATE-FAILURE'
            try:
                exec(compile("raise RuntimeError('SYNTHETIC-PRIVATE-FAILURE')",'/private-source/credential.py','exec'))
            except RuntimeError as failure:
                result=business.capture_failure(session,failure)
                safe=result['errorDiagnostics']
            original=session.private/'business-exception.json'
            self.assertEqual(stat.S_IMODE(original.stat().st_mode),0o600)
            value=json.loads(original.read_text())
            self.assertIn(marker,value['traceback'])
            self.assertIn('/private-source/credential.py',value['traceback'])
            self.assertEqual(safe['exceptionType'],'RuntimeError')
            self.assertTrue(safe['repositoryFrames'])
            self.assertNotIn(marker,json.dumps(safe))
            self.assertNotIn('/private-source',json.dumps(safe))
            self.assertTrue(all(not pathlib.Path(v['file']).is_absolute() and '..' not in pathlib.Path(v['file']).parts
                                for v in safe['repositoryFrames']))

    def test_private_capture_oserror_keeps_primary_failure_code_type_and_frames(self):
        with tempfile.TemporaryDirectory() as directory:
            private=pathlib.Path(directory)/'not-a-directory';private.write_text('original-file')
            session=types.SimpleNamespace(private=private)
            class OriginalFailure(RuntimeError):code='ORIGINAL_BUSINESS_CODE'
            try:raise OriginalFailure('SYNTHETIC-PRIVATE-FAILURE')
            except OriginalFailure as failure:result=business.capture_failure(session,failure)
            self.assertEqual(result['errorCode'],'ORIGINAL_BUSINESS_CODE')
            safe=result['errorDiagnostics']
            self.assertIs(safe['privateCaptureSucceeded'],False)
            self.assertEqual(safe['diagnosticErrorCode'],'private-exception-capture-failed')
            self.assertEqual(safe['exceptionType'],'OriginalFailure')
            self.assertTrue(safe['repositoryFrames'])
            self.assertNotIn('SYNTHETIC-PRIVATE-FAILURE',json.dumps(result))
            self.assertNotIn(str(private),json.dumps(result))
            self.assertEqual(private.read_text(),'original-file')


if __name__=='__main__':unittest.main()
