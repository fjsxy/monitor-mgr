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
import contextlib
import subprocess
from unittest.mock import patch


class BusinessFailureBoundary(unittest.TestCase):
    def test_declared_types_are_canonical_without_filename_or_directory_rules(self):
        """C6 T01同原bytes类型别名；Host夹具不建立正式passing。"""
        with tempfile.TemporaryDirectory() as directory:
            root=pathlib.Path(directory)
            definitions=[('anywhere/a.json','image-config',{'architecture':'amd64'}),
                         ('other/b.json','image-manifest',{'schemaVersion':2}),
                         ('events/arbitrary.json','browser-report',{'suites':[]}),
                         ('legacy/results.json','browser-report',{'suites':[]})]
            declared=[]
            for name,kind,value in definitions:
                path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value))
                declared.append(business.reference(path,root,kind))
            # 未typed的同原件引用不得夺走显式类型；顺序无关且不改原声明。
            untyped={k:v for k,v in declared[0].items() if k in ['path','sha256']}
            original=json.loads(json.dumps(declared))
            refs=business._refs(root,declarations={'nested':[untyped,*declared,declared[0]]})
            self.assertEqual(refs,sorted(declared,key=lambda r:r['path']))
            self.assertEqual(original,declared)
            gate.Evidence(root).closure({'typed':declared,'collected':refs})
            normalized=business._canonical_refs({'nested':[untyped,*declared]},refs)
            gate.Evidence(root).closure({'component':normalized,'collected':refs})
            self.assertEqual(normalized['nested'][0],declared[0])

    def test_collection_rejects_conflicting_or_unsafe_declarations(self):
        with tempfile.TemporaryDirectory() as directory:
            root=pathlib.Path(directory);path=root/'metadata.json';path.write_text('{"schemaVersion":2}')
            declared=business.reference(path,root,'image-manifest')
            private=root/'.private';private.mkdir();(private/'secret.json').write_text('{"privateKey":"PRIVATE_SECRET"}')
            outside=root.parent/(root.name+'-outside.json');outside.write_text('{}')
            (root/'alias.json').symlink_to(outside)
            try:
                for mode in ['kind','hash','path','visibility','unknown-kind','wrong-content-kind','private','symlink','missing']:
                    bad=dict(declared)
                    if mode=='kind':bad['kind']='image-config'
                    elif mode=='hash':bad['sha256']='f'*64
                    elif mode=='path':bad['path']='../'+outside.name
                    elif mode=='visibility':bad['visibility']='private'
                    elif mode=='unknown-kind':bad['kind']='PRIVATE_SECRET'
                    elif mode=='wrong-content-kind':bad['kind']='screenshot'
                    elif mode=='private':bad.update(path='.private/secret.json',sha256=hashlib.sha256((private/'secret.json').read_bytes()).hexdigest())
                    elif mode=='symlink':bad.update(path='alias.json',sha256=hashlib.sha256(outside.read_bytes()).hexdigest())
                    else:bad['path']='missing.json'
                    with self.subTest(mode=mode),self.assertRaises(gate.GateError):
                        business._refs(root,declarations=[declared,bad])
                path.write_text('{"privateKey":"PRIVATE_SECRET"}')
                sensitive=business.reference(path,root,'image-manifest')
                with self.assertRaises(gate.GateError):business._refs(root,declarations=[sensitive])
                path.write_text('{"value":"PRIVATE_SECRET"}')
                sensitive=business.reference(path,root,'safe-json')
                with self.assertRaises(gate.GateError):business._refs(root,declarations=[sensitive],canaries=['PRIVATE_SECRET'])
            finally:outside.unlink()

    def test_untyped_defaults_and_guards_precede_private_or_symlink_reads(self):
        with tempfile.TemporaryDirectory() as directory:
            root=pathlib.Path(directory)
            for name,data in [('normal.json',b'{}'),('events.jsonl',b'{}\n'),('summary.txt',b'controlled'),
                              ('picture.png',b'\x89PNG\r\n\x1a\n')]:
                (root/name).write_bytes(data)
            untyped=[{k:v for k,v in business.reference(path,root).items() if k in ['path','sha256']}
                     for path in sorted(root.iterdir())]
            refs=business._refs(root,declarations=untyped)
            normalized=business._canonical_refs(untyped,refs)
            gate.Evidence(root).closure(normalized)
            self.assertEqual({r['path']:r['kind'] for r in refs},{'events.jsonl':'go-events','normal.json':'safe-json',
                             'picture.png':'screenshot','summary.txt':'safe-summary'})
            for ref in [dict(path='.private/PRIVATE_SECRET.json',sha256='a'*64,kind='safe-json',visibility='public'),
                        dict(path='normal.json',sha256='a'*64,kind='safe-json',visibility='private')]:
                with (self.subTest(reference=ref['visibility']),
                      patch.object(gate.Evidence,'read',side_effect=AssertionError('must not read')),
                      self.assertRaises(gate.GateError)):
                    business._refs(root,declarations=ref)
            link=root/'000link.json';link.symlink_to(root/'normal.json')
            with patch.object(business.os,'fdopen',side_effect=AssertionError('must not read symlink')),self.assertRaises(gate.GateError):
                business._refs(root)

    def test_rejected_collection_preserves_shutdown_cleanup_and_child_root_image(self):
        """受控Suite不执行API；证据拒绝不能跳过原finally。"""
        with tempfile.TemporaryDirectory() as directory:
            root=pathlib.Path(directory);out=root/'business';out.mkdir();private=root/'.private';private.mkdir()
            image=root/'image.json';image.write_text('{}');image_ref=business.reference(image,root,'image-config')
            source=out/'source.json';source.write_text('{}');ref=business.reference(source,out,'image-config')
            for rejected in [False,True]:
                evidence={'metadata':ref,'evidenceRefs':[{**ref,'kind':'safe-json'}] if rejected else [ref]}
                suite=types.SimpleNamespace(out=out,target_names=['control'],inventory=lambda *a:{},
                    deploy=lambda *a:{'node':'fixture-node'},run_case=lambda *a:evidence,
                    cleanup=lambda:{'success':True})
                session=types.SimpleNamespace(out=root,private=private,ledger=types.SimpleNamespace(),canaries=set(),
                    environment_descriptor={'platform':'linux/amd64','targets':{'control':{'clusterUID':'fixture-uid'}}},
                    header=lambda entry:{},image_metadata=lambda image:image_ref,register_secrets=lambda *v:None,
                    cleanup_phase=contextlib.nullcontext,safe=lambda value:None,shutdown=unittest.mock.Mock(return_value=[]))
                module=types.SimpleNamespace(Suite=lambda args:suite)
                with patch.object(business,'plan',return_value={'module':'clusters'}),\
                     patch.object(business,'arguments',return_value=types.SimpleNamespace(image='fixture-image')),\
                     patch.object(business.importlib,'import_module',return_value=module):
                    code=business.run_case(session,{'layer':'K','variantId':'fixture','runner':'clusters'})
                record=json.loads((root/'receipt.json').read_text())
                self.assertEqual(record['productImage'],image_ref)
                session.shutdown.assert_called_once();self.assertTrue(record['cleanup']['success'])
                self.assertEqual(code,1 if rejected else 0)
                if rejected:
                    self.assertEqual(record['outcome'],'failed');self.assertTrue(record['evidenceCollectionFailed'])
                    self.assertNotIn('evidence',record)
                else:gate.Evidence(root).closure(record)

    def test_c5_browser_report_alias_keeps_one_semantic_kind_and_original_events(self):
        """C5 I01的同path/hash不同kind结构；可移植受控报告不冒充原CI passing。"""
        with tempfile.TemporaryDirectory() as directory:
            root=pathlib.Path(directory);report=root/'browser/I01.primary/playwright.json';report.parent.mkdir(parents=True)
            data=json.dumps({'suites':[{'specs':[{'title':'I01.primary 受控原事件','tests':[{'status':'expected','results':[{'status':'passed'}]}]}]}]}).encode()
            report.write_bytes(data);suite=types.SimpleNamespace(out=root)
            event=business._browser_report(suite,{'testSelectors':['I01.primary']})
            refs=business._refs(root,declarations=event)
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
                refs=business._refs(root,declarations=[event,produced])
                reader=gate.Evidence(root);reader.closure({'browserEventRef':event,'evidenceRefs':refs,'nested':[report_ref]})
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
