"""真实JSON/OCI原字节结构的Host协议回归；不调用Docker/API，不建立正式passing。"""
import copy
import hashlib
import json
import pathlib
import subprocess
import tempfile
import types
import unittest
from unittest.mock import patch

import image_provenance
import platform_cases
import prepare
import workbench_cases


def graph(source=None, arch='amd64'):
    config=json.dumps({'os':'linux','architecture':arch,'config':{'Labels':{'org.opencontainers.image.revision':source}}}).encode()
    digest='sha256:'+hashlib.sha256(config).hexdigest()
    manifest=json.dumps({'schemaVersion':2,'config':{'digest':digest,'size':len(config)},'layers':[]}).encode()
    child='sha256:'+hashlib.sha256(manifest).hexdigest()
    index=json.dumps({'schemaVersion':2,'manifests':[{'digest':child,'size':len(manifest),'platform':{'os':'linux','architecture':arch}}]}).encode()
    parent='sha256:'+hashlib.sha256(index).hexdigest()
    return {digest:config,child:manifest,parent:index},digest,child,parent


class RuntimeFixture:
    def __init__(self, root):
        self.root=root;self.calls=[];self.source='a'*40;self.content={};self.private=root/'private';self.private.mkdir();self.actual_node_id='d'*64
        self.lock=copy.deepcopy(prepare.LOCK)
        self.nodes=[dict(name='node'+str(n),uid='node-uid-'+str(n),role='control-plane' if n==0 else 'worker') for n in range(3)]
        self.node={'metadata':{'name':'node0','uid':'node-uid-0'},'status':{'nodeInfo':{'architecture':'amd64','operatingSystem':'linux'}}}
        self.d=dict(profile='CI_SMOKE',source=self.source,sourceLock=self.lock,monitorOpCommit=self.lock['monitorOp']['source'],
            privateRoot=str(self.private),platform='linux/amd64',image='platform:fixed',fixtureImages={'environment':'environment:fixed'},
            monitorOpImage='operator:fixed',imageProofs={},officialContent={})
        for name,revision in [('platform:fixed',self.source),('environment:fixed',self.source),('operator:fixed',self.lock['monitorOp']['source'])]:
            values,digest,child,parent=graph(revision);self.content.update(values)
            path=self.private/(name.split(':')[0]+'-config.raw.json');path.write_bytes(values[digest])
            self.d['imageProofs'][name]=dict(configDigest=digest,manifestDigest=None,indexDigest=None,source=revision,
                platform='linux/amd64',originalContentBytesVerified=True,evidenceRefs=[dict(path=str(path),sha256=digest[7:])])
        for name in ['ksm','prometheus','promop','reloader','provisioner']:
            values,digest,child,parent=graph();self.content.update(values)
            locked=self.lock['images'][name];locked.update(indexDigest=parent,amd64Manifest=child,amd64Config=digest)
            locked['image']=locked['image'].split('@')[0]+'@'+parent
            refs=[]
            for label,key in [('index',parent),('manifest',child),('config',digest)]:
                path=self.private/(name+'-'+label+'.json');path.write_bytes(values[key]);refs.append(dict(path=str(path),sha256=key[7:]))
            proof=dict(image=locked['image'],indexDigest=parent,manifestDigest=child,configDigest=digest,platform='linux/amd64',
                originalContentBytesVerified=True,evidenceRefs=refs)
            self.d['officialContent'][name]=proof;self.d['imageProofs'][locked['image']]=proof
        self.target=dict(clusterUID='cluster-original',nodeTopology=self.nodes,
            containers=[dict(name=n['name'],id=hex(13+i)[2:]*64) for i,n in enumerate(self.nodes)],controllers=[],baseline=[])
        self.d['targets']={'control':self.target};self.pods=[];self.deployments={};self.replica_sets={};self.actual={}
        for number,(name,image) in enumerate([('monitor-mgr-monitor-op',self.d['monitorOpImage']),('prometheus-operator',self.lock['images']['promop']['image'])]):
            proof=self.d['imageProofs'][image];values,digest,child,parent=graph(proof.get('source'));self.content.update(values)
            pod=self.pod(name,image,child,'c' if number==0 else 'b');self.pods.append(pod)
            self.target['controllers'].append(dict(name=name,podUID=pod['metadata']['uid'],image=image,imageID=pod['status']['containerStatuses'][0]['imageID']))
            self.target['baseline'].append(dict(kind='Deployment',namespace='mm-phase1-operators',name=name,uid=name+'-deployment'))
        self.ksm=self.pod('ksm',self.lock['images']['ksm']['tag'],self.d['officialContent']['ksm']['manifestDigest'],'a')
        self.cr=dict(apiVersion='monitoring.fjsxy.github.io/v1alpha1',kind='KubeStateMetrics',metadata=dict(uid='original-cr',generation=1),spec={'deploymentMode':'Deployment'})
        self.deployments['ksm']['metadata']['ownerReferences']=[dict(uid='original-cr',kind='KubeStateMetrics',controller=True)]
        self.item=dict(target='control',namespace='fixture-ns',name='fixture-ksm',uid='original-cr')

    def pod(self,name,image,digest,cid):
        self.deployments[name]=dict(metadata=dict(name=name,uid=name+'-deployment'))
        self.replica_sets[name+'-rs']=dict(metadata=dict(name=name+'-rs',uid=name+'-rs-uid',ownerReferences=[dict(uid=name+'-deployment',kind='Deployment',controller=True)]))
        pod=dict(metadata=dict(name=name+'-pod',namespace='fixture-ns',uid=name+'-pod-uid',ownerReferences=[dict(name=name+'-rs',uid=name+'-rs-uid',kind='ReplicaSet',controller=True)]),
            spec=dict(nodeName='node0',containers=[dict(name='main',image=image)]),
            status=dict(conditions=[dict(type='Ready',status='True')],containerStatuses=[dict(name='main',ready=True,containerID='containerd://'+cid*64,imageID='repository@'+digest)]))
        proof=self.d['imageProofs'].get(image) or self.d['officialContent']['ksm']
        self.actual[cid*64]=dict(status=dict(id=cid*64,metadata=dict(name='main'),state='CONTAINER_RUNNING',imageRef='repository@'+digest,labels={'io.kubernetes.pod.uid':name+'-pod-uid'}),info=dict(config=dict(image=dict(image=proof['configDigest']))))
        return pod

    def operator_host_oci(self, kind='manifest'):
        proof=self.d['imageProofs'][self.d['monitorOpImage']]
        values,digest,child,parent=graph(proof['source']);proof.update(manifestDigest=child,indexDigest=parent if kind=='index' else None)
        for name,key in [('operator-manifest',child),('operator-index',parent)] if kind=='index' else [('operator-manifest',child)]:
            path=self.private/(name+'.raw.json');path.write_bytes(values[key])
            proof['evidenceRefs'].append(dict(path=str(path),sha256=key[7:]))
        return proof

    def read(self,argv):
        self.calls.append(argv)
        if argv[:2]==['docker','inspect']:return (self.actual_node_id+'\n').encode()
        if argv[:3]==['docker','image','inspect']:return json.dumps(['kind@'+self.lock['images']['kind_node']['indexDigest']]).encode()
        if argv[:2]==['docker','exec']:
            assert argv[2]=='d'*64
            if argv[3:5]==['crictl','inspect']:return json.dumps(self.actual[argv[5]]).encode()
            return self.content.get(argv[-1],b'')
        raise AssertionError('未知受控读取')

    def get(self,kind,*args,**kwargs):
        if kind=='kubestatemetrics':return self.cr
        if kind=='node':return self.node
        if kind=='pods':return {'items':[self.ksm]}
        if kind=='deployments':return {'items':[self.deployments['ksm']]}
        if kind=='statefulsets':return {'items':[]}
        if kind=='replicaset':return self.replica_sets[args[0]]
        raise AssertionError(kind)


class RuntimeImageProtocol(unittest.TestCase):
    def test_prepare_and_t01_share_actual_runtime_binding_without_rebinding_existing_node_uid(self):
        for replaced in [False,True]:
            with self.subTest(replaced=replaced),tempfile.TemporaryDirectory() as tmp:
                f=RuntimeFixture(pathlib.Path(tmp));f.d['kubectl']='kubectl';f.target.update(kubeconfig='private-config',context='explicit')
                nodes=[dict(metadata=dict(name=n['name'],uid='replacement' if replaced and i==0 else n['uid'],labels={'node-role.kubernetes.io/control-plane':''} if i==0 else {}),status=dict(nodeInfo=dict(architecture='amd64',operatingSystem='linux',kubeletVersion='v1.28.15'),conditions=[dict(type='Ready',status='True')])) for i,n in enumerate(f.nodes)]
                def execute(argv,**kwargs):
                    if argv[0]=='docker':return subprocess.CompletedProcess(argv,0,f.read(argv),b'')
                    args=argv[5:]
                    if args[:2]==['get','namespace']:value={'metadata':{'uid':'cluster-original'}}
                    elif args[:2]==['get','nodes']:value={'items':nodes}
                    elif args[:2]==['get','storageclasses']:value={'items':[dict(metadata=dict(name='standard',uid='storage',annotations={'storageclass.kubernetes.io/is-default-class':'true'}),provisioner='local-path')]}
                    else:value={'items':f.pods}
                    return subprocess.CompletedProcess(argv,0,json.dumps(value).encode(),b'')
                with patch.object(prepare,'LOCK',f.lock):
                    if replaced:
                        with self.assertRaises(prepare.PrepareError):prepare.verify_environment(f.d,execute=execute)
                    else:
                        proof=prepare.verify_environment(f.d,execute=execute)
                        self.assertTrue(all(c['runtimeProof']['actualRuntimeConfigVerified'] for c in proof['targets']['control']['controllers']))

    def test_runtime_rejects_wrong_original_node_pod_container_config_source_arch_and_unlocked_reference(self):
        modes=['node-uid','node-id','node-arch','pod-uid','container-id','container-name','not-running','not-ready','actual-config',
               'image-ref','missing-content','content-bytes','host-bytes','source','arch','unlocked-tag','official-config','official-index']
        for mode in modes:
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as tmp:
                f=RuntimeFixture(pathlib.Path(tmp));pod=f.pods[0] if mode not in ['unlocked-tag','official-config','official-index'] else f.ksm
                container=pod['spec']['containers'][0];status=pod['status']['containerStatuses'][0];actual=f.actual[status['containerID'].split('://')[1]]
                if mode=='node-uid':f.node['metadata']['uid']='replacement-node'
                elif mode=='node-id':f.actual_node_id='e'*64
                elif mode=='node-arch':f.node['status']['nodeInfo']['architecture']='arm64'
                elif mode=='pod-uid':actual['status']['labels']['io.kubernetes.pod.uid']='replacement-pod'
                elif mode=='container-id':actual['status']['id']='e'*64
                elif mode=='container-name':actual['status']['metadata']['name']='another'
                elif mode=='not-running':actual['status']['state']='CONTAINER_EXITED'
                elif mode=='not-ready':status['ready']=False
                elif mode=='actual-config':actual['info']['config']['image']['image']='sha256:'+'f'*64
                elif mode=='image-ref':actual['status']['imageRef']='repository@sha256:'+'f'*64
                elif mode=='missing-content':f.content.pop(status['imageID'].split('@')[1])
                elif mode=='content-bytes':f.content[actual['info']['config']['image']['image']]+=b' '
                elif mode=='host-bytes':pathlib.Path(f.d['imageProofs'][container['image']]['evidenceRefs'][0]['path']).write_bytes(b'PRIVATE-HOST-CONFIG')
                elif mode in ['source','arch']:
                    revision=f.lock['monitorOp']['source'];values,cfg,child,parent=graph('f'*40 if mode=='source' else revision,'arm64' if mode=='arch' else 'amd64');f.content.update(values)
                    status['imageID']='repository@'+child;actual['status']['imageRef']=status['imageID'];actual['info']['config']['image']['image']=cfg
                    proof=f.d['imageProofs'][container['image']];proof['configDigest']=cfg
                    path=f.private/'changed-config.json';path.write_bytes(values[cfg]);proof['evidenceRefs']=[dict(path=str(path),sha256=cfg[7:])]
                elif mode=='unlocked-tag':container['image']=f.lock['images']['ksm']['tag']+'-PRIVATE'
                elif mode=='official-config':f.d['officialContent']['ksm']['configDigest']='sha256:'+'e'*64
                elif mode=='official-index':f.content[f.lock['images']['ksm']['indexDigest']]+=b' '
                with self.assertRaises(image_provenance.ImageProofError):
                    image_provenance.runtime_image_proof(f.d,'control',pod,container,status,f.node,read=f.read,lock=f.lock,output=f.root/'out')
                observations=list((f.root/'out/runtime-image-identities').glob('*.json'));self.assertEqual(len(observations),1)
                text=observations[0].read_text();self.assertFalse(json.loads(text)['verified']);self.assertNotIn('PRIVATE',text)
                self.assertEqual(observations[0].stat().st_mode&0o777,0o600)

    def test_official_index_and_tag_use_same_original_bytes_and_ignore_untrusted_repo_digest_claims(self):
        with tempfile.TemporaryDirectory() as tmp:
            f=RuntimeFixture(pathlib.Path(tmp));pod=f.ksm;container=pod['spec']['containers'][0];status=pod['status']['containerStatuses'][0]
            f.target['nodeImageProofs']=[dict(node='node0',configDigest='sha256:'+'f'*64,repoDigests=['repository@sha256:'+'f'*64])]
            results=[]
            for ref in [f.lock['images']['ksm']['image'],f.lock['images']['ksm']['tag']]:
                container['image']=ref
                results.append(image_provenance.runtime_image_proof(f.d,'control',pod,container,status,f.node,read=f.read,lock=f.lock))
            self.assertEqual(results[0]['configDigest'],results[1]['configDigest'])
            self.assertTrue(all(v['originalContentBytesVerified'] for v in results))
            self.assertTrue(all(argv[2]=='d'*64 for argv in f.calls if argv[:2]==['docker','exec']))

    def test_u01_does_not_adopt_namespace_pod_or_replacement_workload_owner(self):
        for mode in ['cr-owner','rs-uid','pod-owner','unrelated-pod']:
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as tmp:
                f=RuntimeFixture(pathlib.Path(tmp));s=workbench_cases.Suite.__new__(workbench_cases.Suite)
                s.descriptor=f.d;s.runtime_arch='amd64';s.environment={'control':{'nodes':f.nodes}};s.out=f.root/'out';s.out.mkdir();s.get=f.get
                if mode=='cr-owner':f.deployments['ksm']['metadata']['ownerReferences'][0]['uid']='another-cr'
                elif mode=='rs-uid':f.replica_sets['ksm-rs']['metadata']['uid']='replacement-rs'
                elif mode=='pod-owner':f.replica_sets['ksm-rs']['metadata']['ownerReferences'][0]['uid']='another-deployment'
                else:
                    get=s.get
                    def more(kind,*args,**kw):return {'items':[dict(metadata=dict(uid='other-pod')),f.ksm]} if kind=='pods' else get(kind,*args,**kw)
                    s.get=more
                with patch.object(workbench_cases,'LOCK',f.lock),patch.object(workbench_cases.subprocess,'check_output',side_effect=lambda argv,**kw:f.read(argv)):
                    if mode=='unrelated-pod':self.assertEqual(len(s.ksm_proof(f.item)['pods']),1)
                    else:
                        with self.assertRaises(AssertionError):s.ksm_proof(f.item)

    def test_original_u01_accepts_locked_tag_only_with_original_owner_and_actual_official_runtime_chain(self):
        with tempfile.TemporaryDirectory() as tmp:
            f=RuntimeFixture(pathlib.Path(tmp));s=workbench_cases.Suite.__new__(workbench_cases.Suite)
            s.descriptor=f.d;s.runtime_arch='amd64';s.environment={'control':{'nodes':f.nodes}};s.out=f.root/'out';s.out.mkdir();s.get=f.get
            with patch.object(workbench_cases,'LOCK',f.lock,create=True),patch.object(workbench_cases.subprocess,'check_output',side_effect=lambda argv,**kw:f.read(argv)):
                proof=s.ksm_proof(f.item)
            self.assertEqual(proof['crUID'],'original-cr');self.assertEqual(proof['pods'][0]['configDigest'],f.d['officialContent']['ksm']['configDigest'])
            self.assertTrue(proof['pods'][0]['actualRuntimeConfigVerified'])

    def test_original_t01_legacy_operator_manifest_requires_actual_config_not_host_digest_alias(self):
        self.check_t01()

    def test_t01_private_operator_host_oci_manifest_and_index_stay_private_and_bind_actual_runtime(self):
        for kind in ['manifest','index']:
            with self.subTest(kind=kind):self.check_t01(kind)

    def test_private_operator_host_oci_rejects_wrong_digest_bytes_and_config_binding(self):
        for mode in ['digest','bytes','config-binding']:
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as tmp:
                f=RuntimeFixture(pathlib.Path(tmp));proof=f.operator_host_oci()
                path=pathlib.Path(proof['evidenceRefs'][-1]['path'])
                if mode=='digest':proof['manifestDigest']='sha256:'+'f'*64
                elif mode=='bytes':path.write_bytes(path.read_bytes()+b' ')
                else:
                    value=json.loads(path.read_bytes());value['config']['digest']='sha256:'+'f'*64
                    data=json.dumps(value).encode();path.write_bytes(data);proof['manifestDigest']='sha256:'+hashlib.sha256(data).hexdigest();proof['evidenceRefs'][-1]['sha256']=proof['manifestDigest'][7:]
                with self.assertRaises(image_provenance.ImageProofError):image_provenance.source_config_bytes(f.d,f.d['monitorOpImage'])

    def test_runtime_unvalidated_or_non_uuid_identity_is_hashed_without_private_token(self):
        for mode in ['node-replaced','pod-replaced','node-confirmed','pod-confirmed','node-format','pod-format','missing']:
            with self.subTest(mode=mode),tempfile.TemporaryDirectory() as tmp:
                f=RuntimeFixture(pathlib.Path(tmp));pod=f.pods[0];container=pod['spec']['containers'][0];status=pod['status']['containerStatuses'][0]
                actual=f.actual[status['containerID'].split('://')[1]];marker='PRIVATE_SECRET' if not mode.endswith('format') else '../PRIVATE_SECRET'
                if mode.startswith('node'):
                    f.node['metadata']['uid']=marker
                    if mode=='node-confirmed':f.nodes[0]['uid']=marker
                elif mode.startswith('pod'):
                    pod['metadata']['uid']=marker
                    if mode=='pod-confirmed':actual['status']['labels']['io.kubernetes.pod.uid']=marker
                else:pod['metadata'].pop('uid')
                if mode.endswith('confirmed'):
                    value=image_provenance.runtime_image_proof(f.d,'control',pod,container,status,f.node,read=f.read,lock=f.lock,output=f.root/'out')
                    self.assertTrue(value['verified'])
                else:
                    with self.assertRaises(image_provenance.ImageProofError):image_provenance.runtime_image_proof(f.d,'control',pod,container,status,f.node,read=f.read,lock=f.lock,output=f.root/'out')
                path=next((f.root/'out/runtime-image-identities').glob('*.json'));text=path.read_text();value=json.loads(text)
                self.assertNotIn('PRIVATE_SECRET',text)
                if mode=='missing':self.assertTrue(value['podUIDMissing'])
                else:
                    field='nodeUID' if mode.startswith('node') else 'podUID'
                    self.assertNotIn(field,value);self.assertEqual(value[field+'SHA256'],hashlib.sha256(marker.encode()).hexdigest())

    def test_runtime_known_uuid_is_only_public_after_original_identity_confirmation(self):
        import session
        with tempfile.TemporaryDirectory() as tmp:
            f=RuntimeFixture(pathlib.Path(tmp));pod=f.pods[0];container=pod['spec']['containers'][0];status=pod['status']['containerStatuses'][0]
            node_uid='00000000-0000-4000-8000-000000000001';pod_uid='00000000-0000-4000-8000-000000000002'
            f.node['metadata']['uid']=node_uid;f.nodes[0]['uid']=node_uid;pod['metadata']['uid']=pod_uid
            f.actual[status['containerID'].split('://')[1]]['status']['labels']['io.kubernetes.pod.uid']=pod_uid
            snapshots=[];atomic=session.atomic_json
            def record(path,value):snapshots.append((copy.deepcopy(value),copy.deepcopy(f.calls)));atomic(path,value)
            with patch.object(session,'atomic_json',side_effect=record):
                value=image_provenance.runtime_image_proof(f.d,'control',pod,container,status,f.node,read=f.read,lock=f.lock,output=f.root/'out')
            self.assertEqual(value['podUID'],pod_uid);self.assertEqual(value['nodeUID'],node_uid)
            self.assertNotIn('podUID',snapshots[0][0]);self.assertNotIn('nodeUID',snapshots[0][0])
            for observation,calls in snapshots:
                if 'nodeUID' in observation:self.assertTrue(any(c[:2]==['docker','inspect'] for c in calls))
                if 'podUID' in observation:self.assertTrue(any('crictl' in c and 'inspect' in c for c in calls))

    def test_runtime_unknown_image_error_value_is_never_public(self):
        for args in [('PRIVATE_SECRET',),({'PRIVATE_SECRET':'untrusted'},),('IMAGE_RUNTIME_NODE_ID_MISMATCH','PRIVATE_SECRET'),('IMAGE_RUNTIME_READ_FAILED',)]:
            with self.subTest(args=args),tempfile.TemporaryDirectory() as tmp:
                f=RuntimeFixture(pathlib.Path(tmp));pod=f.pods[0];container=pod['spec']['containers'][0];status=pod['status']['containerStatuses'][0]
                def fail(argv):raise image_provenance.ImageProofError(*args)
                with self.assertRaises(image_provenance.ImageProofError):
                    image_provenance.runtime_image_proof(f.d,'control',pod,container,status,f.node,read=fail,lock=f.lock,output=f.root/'out')
                text=next((f.root/'out/runtime-image-identities').glob('*.json')).read_text()
                self.assertNotIn('PRIVATE_SECRET',text);self.assertEqual(json.loads(text)['errorCode'],'IMAGE_RUNTIME_READ_FAILED')

    def check_t01(self, host_oci=None):
        with tempfile.TemporaryDirectory() as tmp:
            f=RuntimeFixture(pathlib.Path(tmp));s=types.SimpleNamespace(out=f.root/'out',a=types.SimpleNamespace(source=f.source,environment_descriptor=f.d));s.out.mkdir()
            if host_oci:f.operator_host_oci(host_oci)
            crd_names=['kubestatemetrics.monitoring.fjsxy.github.io','prometheusagents.monitoring.coreos.com','servicemonitors.monitoring.coreos.com','podmonitors.monitoring.coreos.com','probes.monitoring.coreos.com','scrapeconfigs.monitoring.coreos.com']
            spec={'group':'fixture'};spec_hash=hashlib.sha256(json.dumps(spec,sort_keys=True,separators=(',',':')).encode()).hexdigest()
            f.target['crds']=[dict(name=n,uid=n+'-uid',specSHA256=spec_hash) for n in crd_names]
            f.target['storageClass']=dict(name='standard',uid='storage-original',provisioner='local-path')
            f.target['volumeProof']=dict(uid=10001,journalMode='wal',synchronous=2,lockBlocked=True,reopenValue=21,keyMode='0600')
            p=f.private/'volume.json';p.write_text('{}')
            f.target['volumeProofEvidence']=dict(source=f.source,cleanup={'success':True},jobUID='volume-job',pods=[{'uid':'volume-pod'}],image=f.d['fixtureImages']['environment'],evidenceRefs=[dict(path=str(p),sha256=hashlib.sha256(b'{}').hexdigest())])
            def get(kind,*args,**kwargs):
                if kind=='namespace':return {'metadata':{'uid':'cluster-original'}}
                if kind=='nodes':return {'items':[dict(metadata=dict(name=n['name'],uid=n['uid'],labels={'node-role.kubernetes.io/control-plane':''} if i==0 else {}),status=dict(nodeInfo=dict(architecture='amd64',operatingSystem='linux',kubeletVersion='v1.28.15'),conditions=[dict(type='Ready',status='True')])) for i,n in enumerate(f.nodes)]}
                if kind=='node':return f.node
                if kind=='crd':return dict(metadata=dict(uid=args[0]+'-uid'),spec=spec,status={'conditions':[dict(type='Established',status='True')]})
                if kind=='pods':return {'items':f.pods if 'mm-phase1-operators' in args else [dict(metadata=dict(name='local-path-provisioner-pod',uid='prov-pod'),status=dict(containerStatuses=[dict(ready=True,imageID=f.d['officialContent']['provisioner']['configDigest'])]))]}
                if kind=='deployment':return f.deployments[args[0]] if args[0]!='local-path-provisioner' else dict(metadata=dict(uid='prov'),spec={'template':{'spec':{'containers':[{'image':f.lock['images']['provisioner']['image']}]}}})
                if kind=='replicaset':return f.replica_sets[args[0]]
                if kind=='storageclass':return dict(metadata=dict(uid='storage-original',annotations={'storageclass.kubernetes.io/is-default-class':'true'}),provisioner='local-path')
                raise AssertionError(kind)
            s.get=get;s.k=lambda *a,**kw:types.SimpleNamespace(stdout=b'version="0.83.0" revision="5cf2f5d" prometheus_operator_ready{controller="prometheus-agent"} 1')
            def process(argv,**kwargs):
                if argv[:2]==['docker','inspect'] and argv[-1].startswith('node'):
                    return subprocess.CompletedProcess(argv,0,json.dumps(dict(id=next(n['id'] for n in f.target['containers'] if n['name']==argv[-1]),image=f.lock['images']['kind_node']['image'],runtimeImageID='sha256:'+'1'*64)).encode(),b'')
                return subprocess.CompletedProcess(argv,0,f.read(argv),b'')
            s.run_captured=process
            fake_root=f.root/'repo';(fake_root/'scripts/acceptance').mkdir(parents=True);(fake_root/'scripts/acceptance/source-lock.json').write_text(json.dumps(f.lock))
            with patch.object(platform_cases,'ROOT',fake_root):proof=platform_cases._topology(s,{'profile':'CI_SMOKE'})
            self.assertEqual(len(proof['targets']['control']['controllers']),2)
            self.assertTrue(proof['targets']['control']['controllers'][0]['runtimeProof']['actualRuntimeConfigVerified'])
            operator_config=f.d['imageProofs'][f.d['monitorOpImage']]['configDigest']
            self.assertFalse((s.out/'platform-provenance'/(operator_config[7:]+'.json')).exists(),'私有Operator config只私有回读，不能公开复制')

