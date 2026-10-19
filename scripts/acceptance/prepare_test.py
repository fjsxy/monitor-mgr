#!/usr/bin/env python3
"""环境准备公开协议自测；受控执行器不连接任何真实 Docker/Kubernetes。"""
import json
import hashlib
import io
import tarfile
import pathlib
import subprocess
import tempfile
import unittest

from prepare import PrepareError, prepare_environment, cleanup_environment, verify_environment, operator_bundle_objects, _Preparation
from transport import ProcessFailure


class PrepareProtocolTest(unittest.TestCase):
    def volume_preparation(self,directory,failure=None,foreground_gc=False,runtime=None):
        """完成Job的真实响应形状；执行器只模拟API边界，验证原UID条件删除。"""
        events=[];calls=[];objects={};deleted=[];removed=set();rawproof=b'{"uid":10001,"journalMode":"wal","synchronous":2,"lockBlocked":true,"reopenValue":21,"keyMode":"0600"}\n'
        image='fixture:source';source='b'*40;name='mm21-volume-preflight';runtime={} if runtime is None else runtime
        config=json.dumps({'os':'linux','architecture':'amd64','config':{'Labels':{'org.opencontainers.image.revision':source}}}).encode()
        digest='sha256:'+hashlib.sha256(config).hexdigest()
        manifest=json.dumps({'schemaVersion':2,'mediaType':'application/vnd.docker.distribution.manifest.v2+json',
                             'config':{'digest':digest,'size':len(config)},'layers':[]}).encode()
        manifest_digest='sha256:'+hashlib.sha256(manifest).hexdigest()
        runtime.setdefault('content',{digest:config,manifest_digest:manifest})
        runtime.setdefault('imageID','fixture@'+digest)
        container_id='c'*64
        pod={'apiVersion':'v1','kind':'Pod','metadata':{'name':name+'-pod','namespace':'mm-phase1-operators','uid':'original-pod','resourceVersion':'8',
             'ownerReferences':[{'uid':'original-job','kind':'Job','name':name,'controller':True}]},
             'spec':{'nodeName':'original-node','containers':[{'name':'probe','image':image}]},'status':{'phase':'Succeeded','containerStatuses':[
             {'name':'probe','imageID':runtime['imageID'],'containerID':'containerd://'+container_id,'state':{'terminated':{'exitCode':0}}}]}}
        runtime.setdefault('actual',{'status':{'id':container_id,'metadata':{'name':'probe'},'state':'CONTAINER_EXITED','exitCode':0,
                    'imageRef':runtime['imageID'],'labels':{'io.kubernetes.pod.uid':'original-pod'}},
                    'info':{'config':{'image':{'image':digest}}}})
        runtime.update(pod=pod,configDigest=digest,manifestDigest=manifest_digest)
        def execute(argv,**kwargs):
            calls.append(argv);output=b''
            if argv[:2]==['docker','exec']:
                self.assertEqual(argv[2],'d'*64)
                if argv[3:5]==['crictl','inspect']:
                    self.assertEqual(argv[5],container_id)
                    output=json.dumps(runtime['actual']).encode()
                else:
                    self.assertEqual(argv[3:8],['ctr','--namespace','k8s.io','content','get'])
                    output=runtime['content'].get(argv[8])
                    if output is None:return subprocess.CompletedProcess(argv,1,b'',b'PRIVATE missing content')
                return subprocess.CompletedProcess(argv,0,output,b'')
            args=argv[5:]
            if args[0]=='apply':
                obj=json.loads(kwargs['input']);kind=obj['kind'];obj['metadata'].update(uid='original-job' if kind=='Job' else 'original-pvc',resourceVersion='7')
                objects['jobs' if kind=='Job' else 'persistentvolumeclaims']=obj;output=json.dumps(obj).encode()
            elif args[:3]==['-n','mm-phase1-operators','logs']:output=rawproof
            elif args[:3]==['-n','mm-phase1-operators','get']:
                resource=args[3]
                if failure:
                    result=failure(resource,deleted,pod)
                    if result is not None:return result
                if resource=='pods' and '-l' in args:output=json.dumps({'items':[pod]}).encode()
                else:output=json.dumps(pod if resource=='pods' else objects[resource]).encode() if resource not in removed else b''
            elif args[0]=='delete':
                self.assertEqual(events[-1]['event'],'prepare_probe_cleanup_intent')
                self.assertTrue((pathlib.Path(directory)/'volume-runtime/control-result.json').is_file())
                resource='pods' if '/pods/' in args[1] else 'jobs';options=json.loads(kwargs['input'])
                self.assertEqual(options['propagationPolicy'],'Foreground')
                self.assertEqual(options['preconditions'],{'uid':'original-pod' if resource=='pods' else 'original-job','resourceVersion':'8' if resource=='pods' else '7'})
                deleted.append(resource)
                removed.add(resource)
                if foreground_gc and resource=='jobs':removed.add('pods')
            else:self.assertEqual(args[2],'wait')
            return subprocess.CompletedProcess(argv,0,output,b'')
        request={'runId':'protocol-volume','source':source,'kubectl':'kubectl','privateRoot':directory,
                 'platform':'linux/amd64','fixtureImages':{'environment':image},
                 'imageProofs':{image:{'configDigest':digest,'manifestDigest':None,'indexDigest':None,'source':source,
                                      'platform':'linux/amd64','originalContentBytesVerified':True}},
                 'targets':{'control':{'context':'kind-mm21-control','kubeconfig':directory+'/control.kubeconfig',
                                       'containers':[{'name':'original-node','id':'d'*64}]}}}
        return _Preparation(request,execute,events.append),calls,events,deleted,rawproof

    def test_volume_legacy_archive_accepts_import_manifest_only_after_actual_config_and_content_binding(self):
        """legacy原config与导入manifest不同；通过实际容器和node原bytes证明同一个config。"""
        with tempfile.TemporaryDirectory() as directory:
            runtime={}
            preparation,calls,events,deleted,_=self.volume_preparation(directory,runtime=runtime)
            image=preparation.d['fixtureImages']['environment']
            runtime['pod']['status']['containerStatuses'][0]['imageID']='fixture@'+runtime['manifestDigest']
            runtime['actual']['status']['imageRef']='fixture@'+runtime['manifestDigest']
            preparation.volume('control')
            expected=preparation.d['imageProofs'][image]
            self.assertIsNone(expected['manifestDigest'])
            self.assertIsNone(expected['indexDigest'])
            self.assertNotEqual(runtime['manifestDigest'],expected['configDigest'])
            binding=preparation.d['targets']['control']['volumeProofEvidence']['runtimeBinding']
            self.assertTrue(binding['actualRuntimeConfigVerified'])
            self.assertTrue(binding['originalContentBytesVerified'])
            self.assertEqual(binding['configDigest'],expected['configDigest'])
            self.assertEqual(binding['manifestDigest'],runtime['manifestDigest'])
            self.assertEqual(deleted,['jobs','pods'])

    def test_volume_runtime_reads_only_recorded_node_container_uid(self):
        """node同名替代不能借旧身份；docker exec须绑定本轮已记录container UID。"""
        with tempfile.TemporaryDirectory() as directory:
            preparation,calls,_,_,_=self.volume_preparation(directory)
            preparation.volume('control')
            runtime_reads=[a for a in calls if a[:2]==['docker','exec']]
            self.assertTrue(runtime_reads)
            self.assertTrue(all(a[2]=='d'*64 for a in runtime_reads))

    def test_volume_runtime_index_selects_one_actual_platform_child_without_rewriting_host_proof(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime={};preparation,_,_,_,_=self.volume_preparation(directory,runtime=runtime)
            child=runtime['manifestDigest']
            index=json.dumps({'schemaVersion':2,'manifests':[{'digest':child,'size':len(runtime['content'][child]),
                              'platform':{'os':'linux','architecture':'amd64'}}]}).encode()
            digest='sha256:'+hashlib.sha256(index).hexdigest();runtime['content'][digest]=index
            runtime['pod']['status']['containerStatuses'][0]['imageID']='fixture@'+digest
            runtime['actual']['status']['imageRef']='fixture@'+digest
            preparation.volume('control')
            binding=preparation.d['targets']['control']['volumeProofEvidence']['runtimeBinding']
            self.assertEqual((binding['indexDigest'],binding['manifestDigest']),(digest,child))
            expected=preparation.d['imageProofs'][preparation.d['fixtureImages']['environment']]
            self.assertIsNone(expected['indexDigest'])
            self.assertIsNone(expected['manifestDigest'])

    def test_volume_runtime_rejects_unbound_container_or_content_before_cleanup(self):
        modes=['container-uid','pod-uid','probe-name','running','exit-code','actual-config','image-ref',
               'unknown-digest','config-bytes','manifest-bytes','manifest-config','index-platform','index-ambiguous',
               'index-child-size','index-invalid-shape','actual-invalid-shape','config-platform','config-source']
        for mode in modes:
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as directory:
                runtime={};preparation,_,events,deleted,_=self.volume_preparation(directory,runtime=runtime)
                pod_status=runtime['pod']['status']['containerStatuses'][0];actual=runtime['actual']
                def image_ref(digest):
                    pod_status['imageID']='fixture@'+digest;actual['status']['imageRef']='fixture@'+digest
                if mode=='container-uid':actual['status']['id']='e'*64
                elif mode=='pod-uid':actual['status']['labels']['io.kubernetes.pod.uid']='replacement-pod'
                elif mode=='probe-name':actual['status']['metadata']['name']='another-container'
                elif mode=='running':actual['status']['state']='CONTAINER_RUNNING'
                elif mode=='exit-code':actual['status']['exitCode']=1
                elif mode=='actual-config':actual['info']['config']['image']['image']='sha256:'+'e'*64
                elif mode=='image-ref':actual['status']['imageRef']='fixture@sha256:'+'e'*64
                elif mode=='unknown-digest':image_ref('sha256:'+'e'*64)
                elif mode=='config-bytes':runtime['content'][runtime['configDigest']]+=b' '
                elif mode=='manifest-bytes':
                    image_ref(runtime['manifestDigest']);runtime['content'][runtime['manifestDigest']]+=b' '
                elif mode=='manifest-config':
                    obj=json.loads(runtime['content'][runtime['manifestDigest']]);obj['config']['digest']='sha256:'+'e'*64
                    raw=json.dumps(obj).encode();digest='sha256:'+hashlib.sha256(raw).hexdigest()
                    runtime['content'][digest]=raw;image_ref(digest)
                elif mode.startswith('index-'):
                    child={'digest':runtime['manifestDigest'],'size':len(runtime['content'][runtime['manifestDigest']]),
                           'platform':{'os':'linux','architecture':'amd64'}}
                    if mode=='index-platform':child['platform']['architecture']='arm64'
                    if mode=='index-child-size':child['size']+=1
                    children=[child,child] if mode=='index-ambiguous' else [child]
                    if mode=='index-invalid-shape':children={'untrusted':'PRIVATE'}
                    raw=json.dumps({'schemaVersion':2,'manifests':children}).encode();digest='sha256:'+hashlib.sha256(raw).hexdigest()
                    runtime['content'][digest]=raw;image_ref(digest)
                elif mode=='actual-invalid-shape':actual['status']['labels']=None
                elif mode.startswith('config-'):
                    obj=json.loads(runtime['content'][runtime['configDigest']])
                    if mode=='config-platform':obj['architecture']='arm64'
                    else:obj['config']['Labels']['org.opencontainers.image.revision']='e'*40
                    raw=json.dumps(obj).encode();digest='sha256:'+hashlib.sha256(raw).hexdigest()
                    runtime['content'][digest]=raw;image_ref(digest)
                    actual['info']['config']['image']['image']=digest
                    preparation.d['imageProofs'][preparation.d['fixtureImages']['environment']]['configDigest']=digest
                with self.assertRaises(PrepareError):preparation.volume('control')
                self.assertEqual(deleted,[])
                self.assertNotIn('volumeProofEvidence',preparation.d['targets']['control'])
                self.assertFalse(any(e['event']=='prepare_volume_runtime_checkpoint' for e in events))

    def test_volume_original_job_pod_capability_and_success_assertions_remain_required(self):
        for mode in ['phase','exit-code','probe-image','container-id','job-owner','capability']:
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as directory:
                runtime={};preparation,calls,_,deleted,_=self.volume_preparation(directory,runtime=runtime)
                pod=runtime['pod']
                if mode=='phase':pod['status']['phase']='Failed'
                elif mode=='exit-code':pod['status']['containerStatuses'][0]['state']['terminated']['exitCode']=1
                elif mode=='probe-image':pod['spec']['containers'][0]['image']='another:source'
                elif mode=='container-id':pod['status']['containerStatuses'][0].pop('containerID')
                elif mode=='job-owner':pod['metadata']['ownerReferences'][0]['uid']='another-job'
                else:
                    execute=preparation.execute
                    def invalid_logs(argv,**kwargs):
                        result=execute(argv,**kwargs)
                        return subprocess.CompletedProcess(argv,0,b'{"uid":0}',b'') if 'logs' in argv else result
                    preparation.execute=invalid_logs
                with self.assertRaises(PrepareError):preparation.volume('control')
                self.assertEqual(deleted,[])
                self.assertFalse(any(a[:2]==['docker','exec'] for a in calls))

    def test_volume_runtime_read_failure_or_timeout_never_deletes_or_checkpoints(self):
        for mode in ['forbidden','timeout']:
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as directory:
                preparation,_,events,deleted,_=self.volume_preparation(directory)
                execute=preparation.execute
                def failed_read(argv,**kwargs):
                    if argv[:2]==['docker','exec']:
                        if mode=='timeout':raise ProcessFailure('PRIVATE timeout')
                        return subprocess.CompletedProcess(argv,1,b'',b'PRIVATE forbidden')
                    return execute(argv,**kwargs)
                preparation.execute=failed_read
                with self.assertRaises((PrepareError,ProcessFailure)):preparation.volume('control')
                self.assertEqual(deleted,[])
                self.assertNotIn('volumeProofEvidence',preparation.d['targets']['control'])
                self.assertFalse(any(e['event']=='prepare_volume_runtime_checkpoint' for e in events))

    def test_volume_probe_preserves_original_proof_then_normally_deletes_original_job_and_pod(self):
        for gc in [False,True]:
            with self.subTest(foregroundGC=gc),tempfile.TemporaryDirectory() as directory:
                preparation,calls,events,deleted,rawproof=self.volume_preparation(directory,foreground_gc=gc)
                preparation.volume('control')
                self.assertEqual(deleted,['jobs'] if gc else ['jobs','pods'])
                target=preparation.d['targets']['control']
                self.assertEqual((pathlib.Path(directory)/'volume-runtime/control-result.json').read_bytes(),rawproof)
                self.assertTrue(target['volumeProofEvidence']['cleanup']['success'])
                self.assertEqual(target['volumeProofEvidence']['jobUID'],'original-job')
                self.assertEqual(target['volumeProofEvidence']['pods'][0]['uid'],'original-pod')
                self.assertFalse(any('persistentvolumeclaims' in a and 'delete' in a for a in calls))

    def test_volume_probe_replacement_or_read_failure_is_not_deleted_or_marked_clean(self):
        for mode in ['replacement','forbidden']:
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as directory:
                def failure(resource,deleted,pod):
                    if resource!='pods' or 'jobs' not in deleted:return None
                    if mode=='forbidden':return subprocess.CompletedProcess([],1,b'',b'Forbidden PRIVATE')
                    replacement=json.loads(json.dumps(pod));replacement['metadata']['uid']='replacement-pod'
                    return subprocess.CompletedProcess([],0,json.dumps(replacement).encode(),b'')
                preparation,calls,events,deleted,rawproof=self.volume_preparation(directory,failure)
                with self.assertRaises(PrepareError):preparation.volume('control')
                self.assertEqual(deleted,['jobs'])
                self.assertFalse(preparation.d['targets']['control']['volumeProofEvidence']['cleanup']['success'])

    def data_node(self, directory, *, taints=(), unschedulable=False, ready='True', failure=None):
        """受控API只模拟真实Node读写；保留命令、intent与原响应身份供断言。"""
        name='mm21-data-control-plane';calls=[];events=[]
        node={'apiVersion':'v1','kind':'Node','metadata':{'name':name,'uid':'original-node','resourceVersion':'7'},
              'spec':{'taints':list(taints),'unschedulable':unschedulable},
              'status':{'conditions':[{'type':'Ready','status':ready}]}}
        def execute(argv, **kwargs):
            calls.append(argv);args=argv[5:]
            self.assertEqual(argv[:5],['kubectl','--kubeconfig',str(pathlib.Path(directory)/'data.kubeconfig'),'--context','kind-mm21-data'])
            if failure:
                result=failure(args, node)
                if result is not None:return result
            if args[:2]==['patch','node']:
                self.assertEqual(events[-1]['event'],'prepare_intent')
                self.assertEqual(events[-1]['identity']['nodeUID'],'original-node')
                patch=json.loads(args[args.index('-p')+1])
                self.assertEqual(patch[:2],[{'op':'test','path':'/metadata/uid','value':'original-node'},
                                           {'op':'test','path':'/metadata/resourceVersion','value':'7'}])
                for action in patch[2:]:
                    self.assertEqual(action['op'],'remove')
                    node['spec']['taints'].pop(int(action['path'].rsplit('/',1)[1]))
                node['metadata']['resourceVersion']='8'
            else:self.assertEqual(args,['get','node',name,'-o','json'])
            return subprocess.CompletedProcess(argv,0,json.dumps(node).encode(),b'')
        request={'runId':'protocol-data','kubectl':'kubectl','privateRoot':directory,'targets':{'data':{
            'context':'kind-mm21-data','kubeconfig':str(pathlib.Path(directory)/'data.kubeconfig'),'clusterUID':'original-cluster'}}}
        return _Preparation(request,execute,events.append),name,calls,events,node

    def test_data_node_without_taint_is_valid_without_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            preparation,name,calls,events,node=self.data_node(directory)
            preparation.make_data_node_schedulable(name)
            self.assertFalse(any('patch' in argv for argv in calls))
            self.assertFalse(any(v['event']=='prepare_intent' for v in events))
            self.assertEqual(events[-1]['event'],'prepare_data_node_schedulable')
            self.assertEqual(events[-1]['nodeUID'],'original-node')

    def test_data_node_existing_taint_is_conditionally_removed_before_readback(self):
        with tempfile.TemporaryDirectory() as directory:
            soft={'key':'fixture-soft','effect':'PreferNoSchedule'}
            preparation,name,calls,events,node=self.data_node(directory,taints=[soft,{'key':'node-role.kubernetes.io/control-plane','effect':'NoSchedule'}])
            preparation.make_data_node_schedulable(name)
            self.assertEqual(node['spec']['taints'],[soft])
            self.assertEqual([a[5] for a in calls],['get','patch','get'])
            self.assertEqual(events[1]['event'],'prepare_data_node_taints_removed')
            self.assertEqual(events[1]['resourceVersion'],'8')

    def test_data_node_postconditions_reject_unready_unschedulable_or_blocking_taint(self):
        for changes in [{'ready':'False'},{'unschedulable':True},{'taints':[{'key':'other-owner','effect':'NoSchedule'}]},
                        {'taints':[{'key':'other-owner','effect':'NoExecute'}]}]:
            with self.subTest(changes=changes),tempfile.TemporaryDirectory() as directory:
                preparation,name,calls,events,node=self.data_node(directory,**changes)
                with self.assertRaisesRegex(PrepareError,'PREPARE_DATA_NODE_NOT_SCHEDULABLE'):
                    preparation.make_data_node_schedulable(name)
                self.assertFalse(any(v['event']=='prepare_data_node_schedulable' for v in events))

    def test_data_node_read_and_patch_errors_never_become_missing_taint_success(self):
        for stage in ['get','patch']:
            for mode in ['forbidden','unknown','timeout']:
                with self.subTest(stage=stage,mode=mode),tempfile.TemporaryDirectory() as directory:
                    def failure(args,node):
                        if args[0]!=stage:return None
                        if mode=='timeout':raise ProcessFailure('PRIVATE-TIMEOUT')
                        return subprocess.CompletedProcess(args,1,b'',b'Forbidden PRIVATE' if mode=='forbidden' else b'PRIVATE unknown')
                    preparation,name,calls,events,node=self.data_node(directory,taints=[{'key':'node-role.kubernetes.io/control-plane','effect':'NoSchedule'}],failure=failure)
                    with self.assertRaises((PrepareError,ProcessFailure)):
                        preparation.make_data_node_schedulable(name)
                    self.assertFalse(any(v['event']=='prepare_data_node_schedulable' for v in events))
                    if stage=='get':self.assertFalse(any('patch' in a for a in calls))

    def test_data_node_replacement_or_malformed_readback_is_rejected(self):
        for mode in ['replacement','invalid']:
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as directory:
                count=0
                def failure(args,node):
                    nonlocal count
                    if args[0]!='get':return None
                    count+=1
                    if mode=='replacement' and count==2:node['metadata']['uid']='replacement-node'
                    if mode=='invalid':return subprocess.CompletedProcess(args,0,b'{"kind":"Node","metadata":{}}',b'')
                preparation,name,calls,events,node=self.data_node(directory,failure=failure)
                with self.assertRaisesRegex(PrepareError,'PREPARE_DATA_NODE_(IDENTITY_CHANGED|RESPONSE_INVALID)'):
                    preparation.make_data_node_schedulable(name)
                self.assertFalse(any(v['event']=='prepare_data_node_schedulable' for v in events))

    def test_public_operator_bundle_preserves_literal_equal_enum_and_scalar_types(self):
        """官方0.83 bundle合法裸'='必须保留；不能为绕过解析把bool/int全改string。"""
        objects=operator_bundle_objects("apiVersion: v1\nkind: ConfigMap\nspec:\n  enabled: true\n  replicas: 2\n  matchType:\n    enum:\n    - '!='\n    - =\n    - =~\n    - '!~'\n")
        self.assertEqual(objects[0]['spec']['matchType']['enum'],['!=','=','=~','!~'])
        self.assertIs(objects[0]['spec']['enabled'],True)
        self.assertIs(type(objects[0]['spec']['replicas']),int)

    def test_public_operator_bundle_cannot_enable_python_tags_or_mutate_global_loader(self):
        """局部构造器仅兼容YAML标准value标量，未知/Python标签仍拒绝。"""
        import yaml
        with self.assertRaises(yaml.constructor.ConstructorError):
            operator_bundle_objects('!!python/name:builtins.eval')
        with self.assertRaises(yaml.constructor.ConstructorError):
            yaml.safe_load('value: =')

    def test_fast_has_no_cluster_and_never_invokes_environment_commands(self):
        with tempfile.TemporaryDirectory() as directory:
            request = {'profile': 'CI_FAST', 'runId': 'protocol-fast', 'candidate': 'a' * 40,
                       'privateRoot': directory, 'targets': {}}
            calls = []
            def execute(argv, **kwargs):
                calls.append(argv)
                raise AssertionError('FAST 不得准备集群')
            result = prepare_environment(request, execute=execute, ledger=lambda event: None)
            self.assertEqual(result['targets'], {})
            self.assertEqual(result['source'], 'a' * 40)
            self.assertEqual(calls, [])

    def test_existing_cluster_is_rejected_before_any_intent_or_write(self):
        with tempfile.TemporaryDirectory() as directory:
            request = {'profile': 'CI_SMOKE', 'runId': 'protocol-smoke', 'candidate': 'a' * 40,
                       'privateRoot': directory, 'platform': 'linux/amd64', 'targets': {'control': {
                           'name': 'mm21-protocol-control',
                           'kubeconfig': str(pathlib.Path(directory) / 'control.kubeconfig'),
                           'context': 'kind-mm21-protocol-control'}},
                       'monitorOpSource': directory}
            calls, events = [], []
            def execute(argv, **kwargs):
                calls.append(argv)
                return subprocess.CompletedProcess(argv, 0, b'mm21-protocol-control\n', b'')
            with self.assertRaisesRegex(PrepareError, 'PREPARE_CLUSTER_EXISTS'):
                prepare_environment(request, execute=execute, ledger=events.append)
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0][-2:], ['get', 'clusters'])
            self.assertEqual(events, [])

    def test_partial_create_failure_carries_observed_identity_to_session(self):
        with tempfile.TemporaryDirectory() as directory:
            request = {'profile': 'CI_SMOKE', 'runId': 'protocol-partial', 'candidate': 'a' * 40,
                       'privateRoot': directory, 'platform': 'linux/amd64', 'buildImages': False,
                       'targets': {'control': {'name': 'mm21-partial-control',
                           'kubeconfig': str(pathlib.Path(directory) / 'control.kubeconfig'),
                           'context': 'kind-mm21-partial-control'}}, 'monitorOpSource': directory}
            events = []
            def execute(argv, **kwargs):
                output, code = b'', 0
                if argv[-1] == 'clusters': output = b''
                elif argv[-1] == 'version': output = b'kind v0.33.0'
                elif argv[:2] == ['kubectl', 'version']: output = b'{"clientVersion":{"gitVersion":"v1.28.15"}}'
                elif argv[:1] == ['uname']: output = b'Linux x86_64'
                elif 'rev-parse' in argv: output = b'2b2a8b79a52070fb89a08f5afae2b7698901c999'
                elif argv[:3] == ['docker', 'image', 'inspect']:
                    source = '2b2a8b79a52070fb89a08f5afae2b7698901c999' if 'monitor-op' in argv[-1] else 'a' * 40
                    config = json.dumps({'os': 'linux', 'architecture': 'amd64', 'config': {'Labels': {'org.opencontainers.image.revision': source}}}).encode()
                    output = json.dumps([{'Id': 'sha256:' + hashlib.sha256(config).hexdigest(), 'Os': 'linux', 'Architecture': 'amd64'}]).encode()
                elif argv[:2] == ['docker', 'save']:
                    source = '2b2a8b79a52070fb89a08f5afae2b7698901c999' if 'monitor-op' in argv[-1] else 'a' * 40
                    config = json.dumps({'os': 'linux', 'architecture': 'amd64', 'config': {'Labels': {'org.opencontainers.image.revision': source}}}).encode()
                    config_name = hashlib.sha256(config).hexdigest() + '.json'
                    with tarfile.open(argv[3], 'w') as archive:
                        for name, data in [('manifest.json', json.dumps([{'Config': config_name, 'RepoTags': [argv[-1]], 'Layers': []}]).encode()), (config_name, config)]:
                            member = tarfile.TarInfo(name)
                            member.size = len(data)
                            archive.addfile(member, io.BytesIO(data))
                elif argv[:3] == ['kind', 'create', 'cluster']: code = 9
                elif argv[:2] == ['docker', 'ps']:
                    output = json.dumps({'ID': 'd' * 64, 'Names': 'mm21-partial-control-control-plane'}).encode()
                return subprocess.CompletedProcess(argv, code, output, b'')
            with self.assertRaises(PrepareError) as caught:
                prepare_environment(request, execute=execute, ledger=events.append)
            self.assertEqual(caught.exception.descriptor['targets']['control']['containers'][0]['id'], 'd' * 64)
            self.assertNotIn('clusterUID', caught.exception.descriptor['targets']['control'])
            self.assertEqual(events[-1]['event'], 'prepare_failed')

    def test_smoke_recovery_refuses_unplanned_data_before_commands(self):
        calls = []
        def execute(argv, **kwargs):
            calls.append(argv)
            raise AssertionError('非法 data 不得接触实际环境')
        with self.assertRaisesRegex(PrepareError, 'RECOVER_TARGETS_INVALID'):
            cleanup_environment({'profile': 'CI_SMOKE', 'runId': 'protocol-invalid', 'targets': {'data': {}}},
                                execute=execute, ledger=lambda event: None)
        self.assertEqual(calls, [])

    def recovery_descriptor(self, directory):
        return {'profile': 'CI_SMOKE', 'runId': 'protocol-recover', 'kubectl': 'kubectl', 'kind': 'kind',
                'targets': {'control': {'name': 'mm21-recover-control', 'context': 'kind-mm21-recover-control',
                    'kubeconfig': str(pathlib.Path(directory) / 'control.kubeconfig'), 'clusterUID': 'original-cluster',
                    'containers': [{'id': 'd' * 64, 'name': 'mm21-recover-control-control-plane'}]}}}

    def test_recovery_read_403_is_failure_and_never_delete(self):
        with tempfile.TemporaryDirectory() as directory:
            calls = []
            def execute(argv, **kwargs):
                calls.append(argv)
                if argv[:2] == ['docker', 'ps']:
                    return subprocess.CompletedProcess(argv, 0, json.dumps({'ID': 'd' * 64, 'Names': 'mm21-recover-control-control-plane'}).encode(), b'')
                return subprocess.CompletedProcess(argv, 1, b'', b'Forbidden SYNTHETIC-AUTH')
            result = cleanup_environment(self.recovery_descriptor(directory), execute=execute, ledger=lambda event: None)
            self.assertFalse(result['success'])
            self.assertEqual(result['targets']['control']['error'], 'RECOVER_CLUSTER_READ_FAILED')
            self.assertFalse(any('delete' in argv for argv in calls))
            self.assertNotIn('SYNTHETIC-AUTH', json.dumps(result))

    def test_recovery_uid_replacement_is_not_deleted(self):
        with tempfile.TemporaryDirectory() as directory:
            calls = []
            def execute(argv, **kwargs):
                calls.append(argv)
                output = json.dumps({'ID': 'd' * 64, 'Names': 'mm21-recover-control-control-plane'}).encode() if argv[0] == 'docker' else b'{"metadata":{"uid":"replacement-cluster"}}'
                return subprocess.CompletedProcess(argv, 0, output, b'')
            result = cleanup_environment(self.recovery_descriptor(directory), execute=execute, ledger=lambda event: None)
            self.assertFalse(result['success'])
            self.assertEqual(result['targets']['control']['error'], 'RECOVER_CLUSTER_UID_CHANGED')
            self.assertFalse(any('delete' in argv for argv in calls))

    def test_recovery_uses_explicit_config_and_confirms_container_absence(self):
        with tempfile.TemporaryDirectory() as directory:
            calls, events = [], []
            state = {'removed': False}
            def execute(argv, **kwargs):
                calls.append(argv)
                output = b''
                if argv[0] == 'docker' and not state['removed']:
                    output = json.dumps({'ID': 'd' * 64, 'Names': 'mm21-recover-control-control-plane'}).encode()
                elif argv[0] == 'kubectl': output = b'{"metadata":{"uid":"original-cluster"}}'
                elif argv[:3] == ['kind', 'delete', 'cluster']: state['removed'] = True
                return subprocess.CompletedProcess(argv, 0, output, b'')
            result = cleanup_environment(self.recovery_descriptor(directory), execute=execute, ledger=events.append)
            self.assertTrue(result['success'])
            deletion = next(argv for argv in calls if argv[0] == 'kind')
            self.assertEqual(deletion[-2:], ['--kubeconfig', str(pathlib.Path(directory) / 'control.kubeconfig')])
            self.assertEqual(events[0]['event'], 'environment_cleanup_intent')
            self.assertEqual(events[-1]['event'], 'environment_cleanup_result')

    def test_ledger_refusal_never_issues_prepare_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            request = {'profile': 'CI_SMOKE', 'runId': 'protocol-ledger', 'candidate': 'a' * 40,
                       'privateRoot': directory, 'platform': 'linux/amd64', 'targets': {'control': {
                           'name': 'mm21-ledger-control', 'kubeconfig': str(pathlib.Path(directory) / 'control.kubeconfig'),
                           'context': 'kind-mm21-ledger-control'}}, 'monitorOpSource': directory}
            calls = []
            def execute(argv, **kwargs):
                calls.append(argv)
                output = b'kind v0.33.0' if argv[-1] == 'version' else b'{"clientVersion":{"gitVersion":"v1.28.15"}}' if argv[0] == 'kubectl' else b'Linux x86_64' if argv[0] == 'uname' else b''
                return subprocess.CompletedProcess(argv, 0, output, b'')
            def refuse(event): raise OSError('synthetic ledger unavailable')
            with self.assertRaises(OSError):
                prepare_environment(request, execute=execute, ledger=refuse)
            self.assertFalse(any('create' in argv or 'build' in argv or 'apply' in argv for argv in calls))

    def test_recovery_execution_failure_continues_other_confirmed_target(self):
        for failure in [RuntimeError('PRIVATE-TIMEOUT'), ProcessFailure('PRIVATE-TIMEOUT'), OSError('PRIVATE-EXECUTOR'),
                        PrepareError('PRIVATE-UNEXPECTED-PREPARE'),
                        subprocess.TimeoutExpired(['PRIVATE-ARGV'], 1)]:
            with self.subTest(kind=type(failure).__name__), tempfile.TemporaryDirectory() as directory:
                descriptor = self.recovery_descriptor(directory)
                descriptor['profile'] = 'LOCAL_FULL'
                descriptor['targets']['data'] = {**descriptor['targets']['control'], 'name': 'mm21-recover-data'}
                calls = []
                def execute(argv, **kwargs):
                    calls.append(argv)
                    if 'label=io.x-k8s.kind.cluster=mm21-recover-data' in argv: raise failure
                    return subprocess.CompletedProcess(argv, 0, b'', b'')
                result = cleanup_environment(descriptor, execute=execute, ledger=lambda event: None)
                self.assertFalse(result['success'])
                self.assertTrue(result['targets']['control']['success'])
                self.assertEqual(result['targets']['data']['error'], 'RECOVER_EXECUTION_FAILED')
                self.assertNotIn('PRIVATE', json.dumps(result))
                self.assertEqual(len(calls), 2)

    def test_recovery_ledger_failure_is_fatal_before_later_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            descriptor = self.recovery_descriptor(directory)
            descriptor['profile'] = 'LOCAL_FULL'
            descriptor['targets']['data'] = {**descriptor['targets']['control'], 'name': 'mm21-recover-data'}
            calls = []
            def execute(argv, **kwargs):
                calls.append(argv)
                output = json.dumps({'ID': 'd' * 64, 'Names': 'mm21-recover-data-control-plane'}).encode() if argv[0] == 'docker' else b'{"metadata":{"uid":"original-cluster"}}'
                return subprocess.CompletedProcess(argv, 0, output, b'')
            def refuse(event): raise OSError('PRIVATE-FSYNC')
            with self.assertRaisesRegex(PrepareError, 'RECOVER_LEDGER_FAILED'):
                cleanup_environment(descriptor, execute=execute, ledger=refuse)
            self.assertFalse(any('delete' in argv for argv in calls))
            self.assertFalse(any('label=io.x-k8s.kind.cluster=mm21-recover-control' in argv for argv in calls))

    def test_recovery_result_ledger_failure_stops_after_recorded_delete(self):
        with tempfile.TemporaryDirectory() as directory:
            descriptor = self.recovery_descriptor(directory);descriptor['profile'] = 'LOCAL_FULL'
            descriptor['targets']['data'] = {**descriptor['targets']['control'], 'name': 'mm21-recover-data'}
            calls, events = [], []
            removed = False
            def execute(argv, **kwargs):
                nonlocal removed
                calls.append(argv);output = b''
                if argv[0] == 'docker' and not removed:output = json.dumps({'ID': 'd' * 64}).encode()
                elif argv[0] == 'kubectl':output = b'{"metadata":{"uid":"original-cluster"}}'
                elif argv[0] == 'kind':removed = True
                return subprocess.CompletedProcess(argv, 0, output, b'')
            def ledger(event):
                events.append(event)
                if event['event'] == 'environment_cleanup_result':raise OSError('PRIVATE-RESULT-FSYNC')
            with self.assertRaisesRegex(PrepareError, 'RECOVER_LEDGER_FAILED') as caught:
                cleanup_environment(descriptor, execute=execute, ledger=ledger)
            self.assertFalse(caught.exception.report['success'])
            self.assertEqual(caught.exception.report['targets']['data']['error'], 'RECOVER_LEDGER_FAILED')
            self.assertEqual(sum(argv[0] == 'kind' for argv in calls), 1)
            self.assertFalse(any('label=io.x-k8s.kind.cluster=mm21-recover-control' in argv for argv in calls))


if __name__ == '__main__':
    unittest.main()
