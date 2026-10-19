#!/usr/bin/env python3
"""实际执行接缝的Host边界检查，不产生产品K/B通过。"""
import json
import hashlib
import io
import http.server
from pathlib import Path
import subprocess
import sys
import tempfile
import tarfile
import threading
import time
from types import SimpleNamespace
import unittest
import urllib.request

from execution import ExecutionSession, go_events


class ExecutionBoundary(unittest.TestCase):
    def test_legacy_docker_archive_through_node_prepare_supplies_real_manifest_to_session(self):
        """真实tar原config→受控node原manifest→Session闭包；不运行Docker或Kubernetes。"""
        from image_provenance import read_docker_archive
        from prepare import _Preparation
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source='a'*40;image='example:fixture';node_id='d'*64
            config=json.dumps({'os':'linux','architecture':'amd64','config':{'Labels':{'org.opencontainers.image.revision':source}}}).encode()
            digest='sha256:'+hashlib.sha256(config).hexdigest()
            manifest=json.dumps({'schemaVersion':2,'config':{'digest':digest,'size':len(config)},'layers':[]}).encode()
            manifest_digest='sha256:'+hashlib.sha256(manifest).hexdigest()
            files={'manifest.json':json.dumps([{'Config':digest[7:]+'.json','RepoTags':[image],'Layers':[]}]).encode(),digest[7:]+'.json':config}
            archive=root/'original.tar'
            with tarfile.open(archive,'w') as stream:
                for name,data in files.items():
                    info=tarfile.TarInfo(name);info.size=len(data);stream.addfile(info,io.BytesIO(data))
            metadata=read_docker_archive(archive,image=image,expected_source=source,platform='linux/amd64',
                                         inspect={'Id':digest},destination=root/'host-proof')
            self.assertIsNone(metadata['manifestDigest']);self.assertIsNone(metadata['indexDigest'])
            class OfficialBoundary(Exception):pass
            calls=[]
            def execute(argv,**kwargs):
                calls.append(argv)
                if argv[:2]==['kind','load']:return subprocess.CompletedProcess(argv,0,b'',b'')
                if 'pull' in argv:raise OfficialBoundary()
                if 'inspecti' in argv:raw=json.dumps({'status':{'id':digest,'repoDigests':['example@'+manifest_digest]}}).encode()
                else:
                    self.assertEqual(argv[:8],['docker','exec',node_id,'ctr','--namespace','k8s.io','content','get'])
                    raw={digest:config,manifest_digest:manifest}[argv[-1]]
                return subprocess.CompletedProcess(argv,0,raw,b'')
            request={'source':source,'platform':'linux/amd64','runId':'host-legacy','privateRoot':str(root/'prepare'),
                'image':image,'fixtureImages':{},'monitorOpImage':'private:operator','kind':'kind',
                'imageProofs':{image:metadata,'private:operator':metadata.copy()},
                'targets':{'control':{'name':'owned','clusterUID':'original-cluster','containers':[{'name':'original-node','id':node_id}]}}}
            preparation=_Preparation(request,execute,lambda event:None)
            with self.assertRaises(OfficialBoundary):preparation.load_images('control')
            session=ExecutionSession(root/'run',expected=[],run_id='host-legacy',profile='validator-test',candidate=source,protocol_fixture=True)
            try:
                session.environment_descriptor=preparation.d
                proof=session.image_metadata(image)
                raw=(session.out/proof['manifestRef']['path']).read_bytes()
                self.assertEqual(raw,manifest)
                self.assertEqual(proof['manifestDigest'],manifest_digest)
                self.assertEqual(proof['configDigest'],digest)
                self.assertTrue(all(a[2]==node_id for a in calls if 'inspecti' in a or 'content' in a))
                from gate import _image
                _image({'productImage':proof,'platform':dict(podUID='original-pod',nodeName='original-node',
                        imageID='example@'+manifest_digest,clusterUID='original-cluster',serviceName='monitor-mgr')},
                       SimpleNamespace(read=lambda ref:(session.out/ref['path']).read_bytes()),source,'amd64',{'original-cluster'})
            finally:session.shutdown();session.close()

    def test_prepared_config_only_has_fixed_error_and_private_operator_is_not_public(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source='a'*40;path=root/'config.raw.json';path.write_text('{}')
            metadata={'source':source,'evidenceRefs':[dict(path=str(path),sha256=hashlib.sha256(path.read_bytes()).hexdigest())]}
            session=ExecutionSession(root/'run',expected=[],run_id='host-unresolved',profile='validator-test',candidate=source,protocol_fixture=True)
            try:
                session.environment_descriptor={'image':'public:fixture','imageProofs':{'public:fixture':metadata,'private:operator':metadata}}
                with self.assertRaisesRegex(ValueError,'prepared-image-oci-manifest-required'):session.image_metadata('public:fixture')
                with self.assertRaisesRegex(ValueError,'unapproved-public-image-proof'):session.image_metadata('private:operator')
            finally:session.shutdown();session.close()

    def test_original_docker_oci_index_prepared_proof_keeps_child_bytes_and_gate_digest_checks(self):
        from image_provenance import read_docker_archive
        from gate import _image
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source='a'*40;image='example:fixture'
            config=json.dumps({'os':'linux','architecture':'amd64','config':{'Labels':{'org.opencontainers.image.revision':source}}}).encode()
            digest='sha256:'+hashlib.sha256(config).hexdigest()
            manifest=json.dumps({'schemaVersion':2,'config':{'digest':digest,'size':len(config)},'layers':[]}).encode()
            child='sha256:'+hashlib.sha256(manifest).hexdigest()
            index=json.dumps({'schemaVersion':2,'manifests':[{'digest':child,'size':len(manifest),'platform':{'os':'linux','architecture':'amd64'}}]}).encode()
            parent='sha256:'+hashlib.sha256(index).hexdigest()
            files={'manifest.json':json.dumps([{'Config':'blobs/sha256/'+digest[7:],'RepoTags':[image],'Layers':[]}]).encode(),
                   'blobs/sha256/'+digest[7:]:config,'blobs/sha256/'+child[7:]:manifest,'blobs/sha256/'+parent[7:]:index}
            with tarfile.open(root/'original.tar','w') as stream:
                for name,data in files.items():
                    info=tarfile.TarInfo(name);info.size=len(data);stream.addfile(info,io.BytesIO(data))
            metadata=read_docker_archive(root/'original.tar',image=image,expected_source=source,platform='linux/amd64',
                inspect={'Id':parent,'Descriptor':{'digest':parent}},destination=root/'host-proof')
            session=ExecutionSession(root/'run',expected=[],run_id='host-index',profile='validator-test',candidate=source,protocol_fixture=True)
            try:
                session.environment_descriptor={'image':image,'platform':'linux/amd64','imageProofs':{image:metadata}}
                proof=session.image_metadata(image)
                self.assertEqual((proof['indexDigest'],proof['manifestDigest']),(parent,child))
                self.assertEqual((session.out/proof['manifestRef']['path']).read_bytes(),manifest)
                self.assertEqual((session.out/proof['configRef']['path']).read_bytes(),config)
                record={'productImage':proof,'platform':dict(podUID='original-pod',nodeName='original-node',imageID='example@'+child,
                        clusterUID='original-cluster',serviceName='monitor-mgr')}
                _image(record,SimpleNamespace(read=lambda ref:(session.out/ref['path']).read_bytes()),source,'amd64',{'original-cluster'})
                session.images.clear();metadata['manifestDigest']='sha256:'+'f'*64
                with self.assertRaisesRegex(ValueError,'prepared-image-source-or-oci-binding-mismatch'):session.image_metadata(image)
            finally:session.shutdown();session.close()

    def test_undrained_handler_keeps_failed_lease_until_process_exit(self):
        """有界未排空不能解锁；Host夹具确认原handler结束后才手工撤回自己的保留fd。"""
        import fcntl
        import session as session_module
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);release=threading.Event();entered=threading.Event()
            value=ExecutionSession(root/'run',expected=[],run_id='host-undrained',profile='validator-test',candidate='fixture',
                protocol_fixture=True,lane=str(root/'lane'))
            value.handler_drain_timeout=.01;value.handler_abort_timeout=.01
            class Handler(http.server.BaseHTTPRequestHandler):
                def log_message(self,*_):pass
                def do_GET(self):
                    entered.set();release.wait(5);self.send_response(200);self.end_headers()
            address=value._server(Handler)
            def request():
                with urllib.request.urlopen(address,timeout=6) as response:response.read()
            client=threading.Thread(target=request)
            prior=len(session_module._LEASES_UNTIL_PROCESS_EXIT)
            try:
                value.start();client.start();self.assertTrue(entered.wait(5))
                failures=value.shutdown()
                self.assertIn('handler-still-running-lease-retained',[f['errorCode'] for f in failures])
                value.close(cleanup={'success':False,'failures':failures})
                self.assertEqual(value.index['state'],'failed')
                self.assertTrue(value.index['cleanup']['laneRetainedUntilProcessExit'])
                with open(root/'lane','a') as other:
                    with self.assertRaises(BlockingIOError):fcntl.flock(other,fcntl.LOCK_EX|fcntl.LOCK_NB)
            finally:
                release.set();client.join(timeout=6);value.shutdown();value.close()
                # 只恢复本Host夹具已确认停止的原handler持有fd，不作为业务恢复能力或passing键。
                for descriptor in session_module._LEASES_UNTIL_PROCESS_EXIT[prior:]:descriptor.close()
                del session_module._LEASES_UNTIL_PROCESS_EXIT[prior:]

    def test_shutdown_drains_accepted_http_handler_and_ordinary_process_before_return(self):
        """已接受的handler及同步run须在锁内结束，不能shutdown后才继续写原响应。"""
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);started=root/'started';marker=root/'marker';finished=threading.Event()
            session=ExecutionSession(root/'run',expected=[],run_id='host-drain',profile='validator-test',candidate='fixture',protocol_fixture=True)
            class Handler(http.server.BaseHTTPRequestHandler):
                def log_message(self,*_):pass
                def do_GET(self):
                    session.processes.run([sys.executable,'-c',"import pathlib,time;pathlib.Path("+repr(str(started))+").write_text('started');time.sleep(1.2);pathlib.Path("+repr(str(marker))+").write_text('completed')"],label='host-handler-ordinary')
                    self.send_response(200);self.end_headers();self.wfile.write(b'{}');finished.set()
            address=session._server(Handler)
            errors=[]
            def request():
                try:
                    with urllib.request.urlopen(address,timeout=5) as response:response.read()
                except Exception as error:errors.append(type(error).__name__)
            client=threading.Thread(target=request);client.start()
            end=time.monotonic()+5
            while not started.exists() and time.monotonic()<end:time.sleep(.01)
            try:
                self.assertTrue(started.exists())
                self.assertEqual(session.shutdown(),[])
                self.assertTrue(finished.is_set(),'handler仍未结束却已返回清理成功')
                self.assertTrue(marker.exists(),'普通run仍可能在释放lane后写入')
                self.assertEqual(errors,[])
            finally:
                client.join(timeout=5);session.shutdown();session.close()

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
