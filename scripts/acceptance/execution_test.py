#!/usr/bin/env python3
"""实际执行接缝的Host边界检查，不产生产品K/B通过。"""
import json
import hashlib
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest

from execution import ExecutionSession, go_events


class ExecutionBoundary(unittest.TestCase):
    def test_browser_client_yaml_conversion_and_formal_create_keep_distinct_identity_boundaries(self):
        """真实loopback/子进程保留Node的YAML字节；正式create仍先有intent才确认原UID。"""
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            raw=b'apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: yaml-only\n'
            script=root/'client.py'
            script.write_text("import json,sys,pathlib\nraw=sys.stdin.buffer.read()\nif '--dry-run=client' in sys.argv:\n assert sys.argv[-7:]==['create','--dry-run=client','--validate=false','-f','-','-o','json'];assert raw=="+repr(raw)+"\n print(json.dumps({'kind':'ConfigMap','metadata':{'name':'yaml-only'}}))\nelse:\n ledger=json.loads(pathlib.Path(sys.argv[1]).read_text());assert len(ledger['entries'])==1;assert next(iter(ledger['entries'].values()))['stage']=='intent'\n obj=json.loads(raw);obj['metadata'].update(uid='original-response',resourceVersion='1');print(json.dumps(obj))\n")
            session=ExecutionSession(root/'run',expected=[],run_id='host-ipc',profile='validator-test',candidate='fixture',protocol_fixture=True)
            try:
                session.configure({'targets':{'control':{'clusterUID':'fixture-cluster','nodeTopology':[],'kubeconfig':'fixture-only','context':'explicit'}},'kubectl':[sys.executable,str(script),str(root/'run'/'ledger.json')]})
                prefix=[session.browser_kubectl,'--kubeconfig','fixture-only','--context','explicit']
                converted=subprocess.run([*prefix,'create','--dry-run=client','--validate=false','-f','-','-o','json'],input=raw,capture_output=True,timeout=5)
                self.assertEqual(converted.returncode,0,converted.stderr)
                self.assertNotIn('uid',json.loads(converted.stdout)['metadata'])
                self.assertEqual(session.ledger.entries,{})
                rejected=subprocess.run([*prefix,'create','--dry-run=server','-f','-','-o','json'],input=raw,capture_output=True,timeout=5)
                self.assertNotEqual(rejected.returncode,0)
                self.assertEqual(session.ledger.entries,{})
                created=subprocess.run([*prefix,'create','-f','-','-o','json'],input=json.dumps({'apiVersion':'v1','kind':'Namespace','metadata':{'name':'owned'}}).encode(),capture_output=True,timeout=5)
                self.assertEqual(created.returncode,0,created.stderr)
                self.assertEqual(next(iter(session.ledger.entries.values()))['uid'],'original-response')
            finally:
                session.shutdown();session.close()

    def test_original_file_default_base_preserves_nested_immutable_reference(self):
        """公开封存API默认按原件父目录解析引用，不修改原收据字节。"""
        with tempfile.TemporaryDirectory() as tmp:
            entry={'variantId':'fixture','layer':'B'}
            session=ExecutionSession(Path(tmp)/'run',expected=[entry],run_id='host-original',profile='validator-test',candidate='fixture',protocol_fixture=True)
            try:
                folder=session.out/'cases'/'component';folder.mkdir(parents=True)
                evidence=folder/'evidence.json';evidence.write_text('{}\n')
                original=folder/'receipt.json'
                original.write_text(json.dumps({'outcome':'passed','evidenceRefs':[{'path':'evidence.json','sha256':hashlib.sha256(evidence.read_bytes()).hexdigest()}]}))
                before=original.read_bytes()
                wrapper=session.store_original_file(entry,original)
                self.assertEqual(wrapper['originalEvidenceBase'],'cases/component')
                self.assertEqual(original.read_bytes(),before)
                self.assertEqual(wrapper['evidenceRefs'][0]['path'],'cases/component/evidence.json')
            finally:
                session.shutdown();session.close()

    def test_browser_collection_without_actual_execution_is_rejected(self):
        """--list的相同selector不是浏览器运行，缺results不能生成通过报告引用。"""
        from business import _browser_report
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            report={'suites':[{'specs':[{'title':'U06.primary 原用例','tests':[{'status':'expected','results':[]}]}]}]}
            (root/'collection.json').write_text(json.dumps(report))
            with self.assertRaises(ValueError):
                _browser_report(SimpleNamespace(out=root),{'testSelectors':['U06.primary']})
            report['suites'][0]['specs'][0]['tests'][0]['results']=[{'status':'passed'}]
            (root/'playwright.json').write_text(json.dumps(report))
            self.assertEqual(_browser_report(SimpleNamespace(out=root),{'testSelectors':['U06.primary']})['path'],'playwright.json')

    def test_go_skip_beneath_passing_root_cannot_pass(self):
        """父测试pass但真实leaf skip仍拒绝，Output原文本不会被公开。"""
        entry={'variantId':'fixture','testSelectors':['TestFixture']}
        raw=b'\n'.join(json.dumps(v).encode() for v in [
            {'Action':'output','Output':'private-auth-canary'},
            {'Action':'skip','Test':'TestFixture/required'}, {'Action':'pass','Test':'TestFixture'}])
        events,outcomes=go_events(raw,[entry],0)
        self.assertFalse(outcomes['fixture'])
        self.assertNotIn('private-auth-canary',json.dumps(events))

    def test_product_confirmation_uses_actual_instance_version_without_invented_resource_version(self):
        """平台响应只有uid/version，账本不能伪造Kubernetes resourceVersion。"""
        with tempfile.TemporaryDirectory() as tmp:
            session=ExecutionSession(Path(tmp)/'run',expected=[],run_id='host-fixture',profile='validator-test',candidate='fixture',protocol_fixture=True)
            try:
                session.configure({'targets':{'control':{'clusterUID':'fixture-cluster','nodeTopology':[], 'kubeconfig':'fixture-only','context':'explicit'}},'kubectl':'unused'})
                session.http_response(None,'GET','/clusters/id',None,{'id':'cluster','uid':'fixture-cluster','credentialRevision':1},status=200,headers={})
                instance={'id':'instance','clusterId':'cluster','component':'kube-state-metrics','name':'fixture','targetNamespace':'owned','uid':'original-response-uid','currentVersionId':'version-1'}
                session.http_response(None,'GET','/instances/instance',None,instance,status=200,headers={})
                entry=next(iter(session.ledger.entries.values()))
                self.assertEqual(entry['uid'],'original-response-uid')
                self.assertEqual(entry['confirmedVersionId'],'version-1')
                self.assertNotIn('resourceVersion',entry)
            finally:
                session.shutdown();session.close()


if __name__=='__main__':
    unittest.main()
