#!/usr/bin/env python3
"""#15 独占真实 Pod/Kubernetes 验收；敏感 kubeconfig 只在内存及 0600 临时文件中。"""
import argparse,copy,hashlib,http.cookiejar,json,os,pathlib,platform,secrets,tarfile,tempfile,time,urllib.error,urllib.request,uuid
from transport import process as subprocess
import yaml
ROOT=pathlib.Path(__file__).resolve().parents[2]
CASES=['T04.loopback','T04.wrong-ca','T04.wrong-name','C01.client-cert','C01.token','C02.primary','C06.bad-candidate','C08.primary','T06.primary']
class EndpointPending(AssertionError):
 """原Pod身份已确认，但EndpointSlice控制器尚未发布Ready endpoint。"""

class Suite:
 def __init__(self,args):
  self.a=args;self.session=getattr(args,'session',None);self.out=pathlib.Path(args.output);self.out.mkdir(parents=True,exist_ok=True);os.chmod(self.out,0o700)
  self.descriptor=self.session.environment_descriptor if self.session else None
  self.target_names=list(self.descriptor['targets']) if self.descriptor else ['control']+(['data'] if getattr(args,'data_pod_config',None) else [])
  expected={'control'} if getattr(args,'profile',None)=='CI_SMOKE' else {'control','data'}
  if self.descriptor and set(self.target_names)!=expected:raise ValueError('SUITE_TARGETS_INVALID')
  self.runtime_arch=self.descriptor['platform'].split('/')[1] if self.descriptor else ('arm64' if platform.machine() in ('arm64','aarch64') else 'amd64')
  for target in self.target_names:
   setattr(self,target,json.loads(pathlib.Path(getattr(args,target+'_pod_config')).read_text()))
  self.password=secrets.token_urlsafe(24);self.results=[]
  if self.session:self.session.register_secrets(self.password)
 def k(self,*args,data=None,target='control',check=True):
  if target not in self.target_names:raise ValueError('SUITE_TARGET_UNAVAILABLE')
  if self.session:return self.session.kube.command(target,list(args),data=data,check=check)
  cfg=self.a.control_config if target=='control' else self.a.data_config
  context=getattr(self.a,target+'_context',None)
  if not context:raise ValueError('SUITE_CONTEXT_REQUIRED')
  r=subprocess.run([self.a.kubectl,'--kubeconfig',cfg,'--context',context,*args],input=None if data is None else json.dumps(data).encode(),stdout=subprocess.PIPE,stderr=subprocess.PIPE)
  if check and r.returncode:raise RuntimeError('kubectl '+str(args[:2])+' 失败（敏感参数已隐藏）')
  return r
 def config(self,target='control'):
  if target not in self.target_names:raise ValueError('SUITE_TARGET_UNAVAILABLE')
  c=copy.deepcopy(getattr(self,target));c['contexts'][0]['name']='explicit';c['current-context']='explicit';return c
 def image_proof(self,image):
  """保持原 config 字节证据；Session 复用本 run 已验证的同来源 OCI 证明。"""
  if self.session:proof=copy.deepcopy(self.session.image_metadata(image))
  else:
   inspected=json.loads(subprocess.check_output(['docker','image','inspect',image],text=True))[0]
   with tempfile.TemporaryFile() as saved:
    subprocess.run(['docker','save',image],stdout=saved,check=True);saved.seek(0)
    with tarfile.open(fileobj=saved) as archive:
     entry=json.load(archive.extractfile('manifest.json'))[0];raw=archive.extractfile(entry['Config']).read();config=json.loads(raw)
   proof={'dockerInspectId':inspected['Id'],'manifestDigest':inspected.get('Descriptor',{}).get('digest'),'configDigest':'sha256:'+hashlib.sha256(raw).hexdigest(),'configDigestMethod':'docker-save manifest.json Config bytes SHA256','os':config['os'],'architecture':config['architecture'],'source':config.get('config',{}).get('Labels',{}).get('org.opencontainers.image.revision')}
  assert proof['source']==self.a.source and proof['os']=='linux' and proof['architecture']==self.runtime_arch,'镜像来源/架构不符'
  assert proof.get('configDigest') and proof['configDigest'].startswith('sha256:'),'镜像缺少原config摘要'
  proof.setdefault('configID',proof['configDigest']);proof.setdefault('dockerInspectId',proof['configDigest']);proof.setdefault('manifest',proof.get('manifestDigest'))
  return proof
 def target_environment(self):
  """按实际 descriptor 逐目标重读节点与集群身份，不凭声明替代运行事实。"""
  environment={}
  for target in self.target_names:
   nodes=json.loads(self.k('get','nodes','-o','json',target=target).stdout)['items'];uid=json.loads(self.k('get','namespace','kube-system','-o','json',target=target).stdout)['metadata']['uid']
   assert len(nodes)==(3 if target=='control' else 1) and all(n['status']['nodeInfo']['kubeletVersion']=='v1.28.15' and n['status']['nodeInfo']['architecture']==self.runtime_arch and n['status']['nodeInfo']['operatingSystem']=='linux' for n in nodes),'实际目标拓扑/版本/架构不符'
   if self.descriptor:
    expected=self.descriptor['targets'][target];assert uid==expected['clusterUID'],'实际目标集群UID变化'
    actual={(n['metadata']['name'],n['metadata']['uid'],'control-plane' if 'node-role.kubernetes.io/control-plane' in n['metadata'].get('labels',{}) else 'worker') for n in nodes}
    assert actual=={(n['name'],n['uid'],n['role']) for n in expected['nodeTopology']},'实际目标节点UID/角色变化'
   environment[target]={'uid':uid,'nodes':[{'name':n['metadata']['name'],'uid':n['metadata'].get('uid'),'nodeInfo':n['status']['nodeInfo'],'taints':n.get('spec',{}).get('taints',[])} for n in nodes]}
  return environment
 def http_observed(self,method,path,body,response,value):
  if self.session:
   values=[cookie.value for cookie in getattr(self,'cookies',[])]
   if values:self.session.register_secrets(*values)
   self.session.http_response(self,method,path,body,value,status=response.code,headers=dict(response.headers))
 def call(self,method,path,body=None,key=None,status=200):
  if self.session:self.session.http_intent(self,method,path,body)
  req=urllib.request.Request(self.base+'/api/v1'+path,data=None if body is None else json.dumps(body).encode(),method=method,headers={'Origin':self.base,'Content-Type':'application/json','Idempotency-Key':key or str(uuid.uuid4())})
  try:r=self.http.open(req,timeout=18)
  except urllib.error.HTTPError as e:r=e
  data=json.load(r);self.http_observed(method,path,body,r,data);r.close()
  assert r.code==status,(path,r.code,data.get('code'),data.get('message'))
  return data
 def operation(self,op):
  end=time.monotonic()+20
  while time.monotonic()<end:
   o=self.call('GET','/operations/'+op['operationId'])
   if o['state'] in ['SUCCEEDED','FAILED','WAITING_CONFIRMATION']:return o
   time.sleep(.1)
  raise AssertionError('操作未在预算内结束')
 def accept(self,config,owner=None,path='/clusters',name='runtime-cluster',key=None):
  b={'context':'explicit','kubeconfig':json.dumps(config)}
  if path=='/clusters':b['name']=name
  if owner:b['ownerId']=owner
  return self.call('POST',path,b,key,status=202)
 def registered(self,config=None):
  op=self.accept(config or self.config(),self.owner);done=self.operation(op);assert done['state']=='SUCCEEDED',done
  cl=self.call('GET','/clusters/'+done['clusterId']);return cl,done
 def inventory(self,target):
  raw=self.k('get','kubestatemetrics,prometheusagents','-A','-o','json',target=target)
  return sorted([{'kind':i['kind'],'namespace':i['metadata'].get('namespace',''),'name':i['metadata']['name'],'uid':i['metadata']['uid']} for i in json.loads(raw.stdout)['items']],key=lambda x:(x['kind'],x['namespace'],x['name']))
 def deploy(self,case,index):
  if not getattr(self.a,'initialize',True) and case!='I01.primary':raise ValueError('SUITE_INITIALIZATION_SKIP_INVALID')
  self.ns=self.a.prefix+'-'+str(index);self.port=self.a.port+index
  self.k('create','namespace',self.ns)
  objs=[]
  for o in yaml.safe_load_all(open(ROOT/'deploy/platform.yaml')):
   if o is None:continue
   o['metadata']['namespace']=self.ns;o['metadata'].setdefault('labels',{})['monitor-mgr-run']=self.a.prefix
   if o['kind']=='Deployment':
    o['spec']['template']['metadata'].setdefault('annotations',{})['monitor-mgr-source']=self.a.source
    c=o['spec']['template']['spec']['containers'][0];c['image']=self.a.image;c['imagePullPolicy']='Never'
   objs.append(o)
  self.k('apply','-f','-',data={'apiVersion':'v1','kind':'List','items':objs})
  self.k('-n',self.ns,'rollout','status','deployment/monitor-mgr','--timeout=120s')
  pod=json.loads(self.k('-n',self.ns,'get','pods','-l','app=monitor-mgr','-o','json').stdout)['items'][0]
  self.pod=pod['metadata']['name'];self.platform_pod_uid=pod['metadata']['uid'];self.initial_uid=pod['metadata']['uid'];self.connect()
  if getattr(self.a,'initialize',True):
   self.call('POST','/initialization',{'username':'admin','password':self.password},status=201)
   self.call('POST','/sessions',{'username':'admin','password':self.password},status=201)
   self.owner=self.call('GET','/session')['account']['id']
  return {'podUID':pod['metadata']['uid'],'node':pod['spec']['nodeName'],'imageID':pod['status']['containerStatuses'][0]['imageID'],'source':self.a.source}
 def service_identity(self):
  """关键：Service 的唯一 Ready endpoint 必须仍是本例原 Pod；替代 UID 不能借绿。"""
  def read(*args):return json.loads(self.k('-n',self.ns,'get',*args,'-o','json').stdout)
  pod=read('pod',self.pod);assert pod['metadata']['uid']==self.platform_pod_uid,'平台原Pod UID变化'
  service=read('service','monitor-mgr');selector=service['spec']['selector']
  pods=read('pods','-l',','.join(k+'='+v for k,v in sorted(selector.items())))['items']
  live=[p for p in pods if not p['metadata'].get('deletionTimestamp')]
  assert len(live)==1 and live[0]['metadata']['uid']==pod['metadata']['uid'] and not pod['metadata'].get('deletionTimestamp'),'Service选择到其他平台Pod'
  assert any(c['type']=='Ready' and c['status']=='True' for c in pod['status'].get('conditions',[])),'平台原Pod未Ready'
  slices=read('endpointslices','-l','kubernetes.io/service-name=monitor-mgr')['items'];ready=[]
  for item in slices:
   assert any(o.get('uid')==service['metadata']['uid'] and o.get('kind')=='Service' for o in item['metadata'].get('ownerReferences',[])),'EndpointSlice原Service UID不符'
   for endpoint in item.get('endpoints',[]):
    if endpoint.get('conditions',{}).get('ready') is True and not endpoint.get('conditions',{}).get('terminating'):
     ready.append((item,endpoint))
  if not ready:raise EndpointPending('平台原Pod的Service endpoint尚未Ready')
  assert len(ready)==1,'平台Service必须唯一Ready endpoint'
  item,endpoint=ready[0];ref=endpoint['targetRef']
  assert ref.get('kind')=='Pod' and ref.get('namespace')==self.ns and ref.get('name')==self.pod and ref.get('uid')==pod['metadata']['uid'] and pod['status']['podIP'] in endpoint['addresses'],'EndpointSlice不属于本例原Pod'
  return {'namespace':self.ns,'service':'monitor-mgr','serviceUID':service['metadata']['uid'],'pod':self.pod,'podUID':pod['metadata']['uid'],'endpointSliceUID':item['metadata']['uid'],'context':self.a.control_context}
 def wait_service_identity(self,timeout=20):
  """Pod Ready不等于EndpointSlice Ready；只等待原身份的异步就绪，不重试身份冲突。"""
  deadline=time.monotonic()+timeout
  while True:
   try:return self.service_identity()
   except EndpointPending:
    if time.monotonic()>=deadline:raise AssertionError('平台原Pod的Service endpoint等待超时') from None
    time.sleep(.2)
 def connect(self):
  # 关键：唯一原Pod/Service/EndpointSlice的UID保护保持严格，只有零Ready endpoint可以等待。
  identity=self.wait_service_identity();self.base='http://127.0.0.1:'+str(self.port)
  self.forward=subprocess.Popen([self.a.kubectl,'--kubeconfig',self.a.control_config,'--context',self.a.control_context,'-n',self.ns,'port-forward','service/monitor-mgr',str(self.port)+':8080'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
  end=time.monotonic()+20
  while time.monotonic()<end:
   try:
    if urllib.request.urlopen(self.base+'/readyz',timeout=1).status==200:break
   except Exception:time.sleep(.2)
  else:raise RuntimeError('真实 Service port-forward 不可用')
  assert self.service_identity()==identity,'Service ready后原Pod或endpoint身份变化'
  self.actualEndpoint=identity;self.service_endpoint=identity
  if self.session:self.base=self.session.platform_access(self,self.base)
  if not hasattr(self,'cookies'):self.cookies=http.cookiejar.CookieJar()
  self.http=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.cookies))
 def cleanup(self):
  # 关键：统一运行只按 Session 原 UID ledger 清理，不能先执行旧诊断恢复或前缀认领。
  if self.session:return self.session.cleanup_case(self)
  failures=[]
  if hasattr(self,'forward'):self.forward.terminate();self.forward.wait(timeout=5)
  if hasattr(self,'ns'):
   logs=self.k('-n',self.ns,'logs','deployment/monitor-mgr',check=False).stdout;(self.out/(self.case+'.platform.log')).write_bytes(logs)
   if self.k('delete','namespace',self.ns,'--wait=true','--timeout=90s',check=False).returncode:failures.append('平台namespace清理失败')
  if getattr(self,'token_ns',None):
   target=self.token_target
   for args in [('clusterrolebinding',self.token_ns),('namespace',self.token_ns)]:
    if self.k('delete',*args,'--wait=true',target=target,check=False).returncode:failures.append('Token fixture清理失败')
  return {'success':not failures,'failures':failures}
 def proof(self,name):
  # Probe is a fixture, never a platform volume-management feature. No key bytes are emitted.
  script="""import hashlib,json,os,sqlite3,stat
p='/data/master.key';s=os.stat(p);c=sqlite3.connect('file:/data/platform.db?mode=ro',uri=True)
print(json.dumps({'keySHA256':hashlib.sha256(open(p,'rb').read()).hexdigest(),'keyUID':s.st_uid,'keyMode':oct(stat.S_IMODE(s.st_mode)),'integrity':c.execute('pragma integrity_check').fetchone()[0],'credentialRows':c.execute('select count(*) from clusters where credential is not null').fetchone()[0]}))"""
  return self.job(name,script,True)
 def job(self,name,script,readonly=False):
  o={'apiVersion':'batch/v1','kind':'Job','metadata':{'name':name,'namespace':self.ns},'spec':{'backoffLimit':0,'activeDeadlineSeconds':60,'template':{'spec':{'restartPolicy':'Never','automountServiceAccountToken':False,'securityContext':{'runAsUser':10001,'runAsGroup':10001,'fsGroup':10001},'containers':[{'name':'probe','image':'python:3.13.2-alpine','imagePullPolicy':'Never','command':['python','-c',script],'volumeMounts':[{'name':'data','mountPath':'/data','readOnly':readonly}]}],'volumes':[{'name':'data','persistentVolumeClaim':{'claimName':'monitor-mgr-data'}}]}}}}
  self.k('apply','-f','-',data=o);self.k('-n',self.ns,'wait','--for=condition=complete','job/'+name,'--timeout=65s');raw=self.k('-n',self.ns,'logs','job/'+name).stdout.decode().strip();return json.loads(raw) if raw else {}
 def run_case(self,case):
  if case.startswith('T04.'):
   cfg=self.config();cl=cfg['clusters'][0]['cluster']
   if case.endswith('loopback'):cl['server']='https://127.0.0.1:1'
   if case.endswith('wrong-ca'):cl['certificate-authority-data']=self.config('data')['clusters'][0]['cluster']['certificate-authority-data']
   if case.endswith('wrong-name'):cl['tls-server-name']='wrong.example.invalid'
   o=self.operation(self.accept(cfg,self.owner));assert o['state']=='FAILED';assert self.call('GET','/clusters')['clusters']==[];return {'operation':o}
  if case=='C01.token':
   target='data' if 'data' in self.target_names else 'control';self.token_target=target
   ns=self.a.prefix+'-token';self.k('create','namespace',ns,target=target);self.token_ns=ns
   self.k('-n',ns,'create','serviceaccount','verifier',target=target);self.k('create','clusterrolebinding',ns,'--clusterrole=cluster-admin','--serviceaccount='+ns+':verifier',target=target)
   token=self.k('-n',ns,'create','token','verifier','--duration=1h',target=target).stdout.decode().strip();cfg=self.config(target);cfg['users']=[{'name':'token-user','user':{'token':token}}];cfg['contexts'][0]['context']['user']='token-user';c,o=self.registered(cfg);assert c['identity']=='system:serviceaccount:'+ns+':verifier';return {'cluster':c,'operation':o}
  c,o=self.registered()
  if case=='C01.client-cert':assert c['identity']!='system:anonymous';return {'cluster':c,'operation':o,'namespaces':self.call('GET','/clusters/'+c['id']+'/namespaces')['namespaces']}
  if case=='C02.primary':
   cfg=self.config();cfg['contexts'][0]['name']='another-context';cfg['contexts'].append({'name':'explicit','context':copy.deepcopy(cfg['contexts'][0]['context'])});duplicate=self.operation(self.accept(cfg,self.owner,name='duplicate'));assert duplicate['state']=='FAILED';after=self.call('GET','/clusters')['clusters'];assert len(after)==1 and after[0]['id']==c['id'];return {'original':c,'duplicate':duplicate}
  if case=='C06.bad-candidate':
   bad=self.config();bad['users'][0]['user']={'token':'invalid-candidate-fixture-token'};failed=self.operation(self.accept(bad,path='/clusters/'+c['id']+'/credentials'));assert failed['state']=='FAILED';after=self.call('GET','/clusters/'+c['id']);assert after['credentialRevision']==c['credentialRevision'];self.call('GET','/clusters/'+c['id']+'/namespaces');good=self.operation(self.accept(self.config(),path='/clusters/'+c['id']+'/credentials'));assert good['state']=='SUCCEEDED';return {'clusterId':c['id'],'beforeRevision':c['credentialRevision'],'failed':failed,'successfulSameUID':good}
  if case=='C08.primary':
   failed=self.operation(self.accept(self.config('data'),path='/clusters/'+c['id']+'/credentials'));assert failed['state']=='FAILED';after=self.call('GET','/clusters/'+c['id']);assert after['uid']==c['uid'] and after['credentialRevision']==1;self.call('GET','/clusters/'+c['id']+'/namespaces');return {'original':c,'candidate':failed}
  if case=='T06.primary':
   before=self.proof('key-before');session=self.call('GET','/session');self.forward.terminate();self.forward.wait(timeout=5);self.k('-n',self.ns,'scale','deployment/monitor-mgr','--replicas=0');self.k('-n',self.ns,'wait','--for=delete','pods','-l','app=monitor-mgr','--timeout=60s')
   self.job('hide-original-key',"import os,json;os.rename('/data/master.key','/data/master.key.original');print(json.dumps({'moved':True}))")
   self.k('-n',self.ns,'scale','deployment/monitor-mgr','--replicas=1');end=time.monotonic()+40;logs='';badpod=None
   while time.monotonic()<end:
    items=json.loads(self.k('-n',self.ns,'get','pods','-l','app=monitor-mgr','-o','json').stdout)['items']
    if items:
     badpod=items[0];r=self.k('-n',self.ns,'logs',badpod['metadata']['name'],check=False);logs=r.stdout.decode()
     if '已有数据库缺少原密钥' in logs:break
    time.sleep(.5)
   assert '已有数据库缺少原密钥' in logs and not any(s.get('ready') for s in badpod['status'].get('containerStatuses',[]))
   self.k('-n',self.ns,'scale','deployment/monitor-mgr','--replicas=0');self.k('-n',self.ns,'wait','--for=delete','pods','-l','app=monitor-mgr','--timeout=60s')
   restored=self.job('restore-original-key',"import os,json;assert not os.path.exists('/data/master.key');os.rename('/data/master.key.original','/data/master.key');print(json.dumps({'restoredOriginal':True,'replacementGenerated':False}))")
   self.k('-n',self.ns,'scale','deployment/monitor-mgr','--replicas=1');self.k('-n',self.ns,'rollout','status','deployment/monitor-mgr','--timeout=100s');new=json.loads(self.k('-n',self.ns,'get','pods','-l','app=monitor-mgr','-o','json').stdout)['items'][0];self.pod=new['metadata']['name'];self.platform_pod_uid=new['metadata']['uid'];self.connect();self.call('GET','/clusters/'+c['id']+'/namespaces');assert self.call('GET','/session')['expiresAt']==session['expiresAt'];after=self.proof('key-after');assert before==after and before['keyUID']==10001 and before['keyMode']=='0o600' and before['integrity']=='ok';assert new['metadata']['uid']!=self.initial_uid;return {'before':before,'after':after,'missingKeyFailed':True,**restored,'rebuildPodUID':new['metadata']['uid'],'sessionExpiryPreserved':True}
  raise AssertionError(case)
 def run(self):
  for index,case in enumerate(self.a.cases or CASES):
   self.case=case;self.cookies=http.cookiejar.CookieJar();self.token_ns=None;record={'variantId':case,'layer':'K','source':self.a.source,'status':'failed'};before={t:self.inventory(t) for t in self.target_names}
   try:
    record['runtime']=self.deploy(case,index);record['evidence']=self.run_case(case);after={t:self.inventory(t) for t in self.target_names};assert before==after,'注册/凭据操作创建或修改了观测 CR';record['observabilityInventoryUnchanged']=True;record['status']='passed'
   except Exception as e:record['error']=str(e);raise
   finally:
    record['cleanup']=self.cleanup()
    if not record['cleanup']['success']:record['status']='failed'
    (self.out/(case+'.json')).write_text(json.dumps(record,ensure_ascii=False,indent=2));self.results.append(record);(self.out/'results.json').write_text(json.dumps(self.results,ensure_ascii=False,indent=2));print(case,record['status'],flush=True)
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--kubectl',required=True);p.add_argument('--control-config',required=True);p.add_argument('--control-context',required=True);p.add_argument('--data-config',required=True);p.add_argument('--data-context',required=True);p.add_argument('--control-pod-config',required=True);p.add_argument('--data-pod-config',required=True);p.add_argument('--image',required=True);p.add_argument('--source',required=True);p.add_argument('--output',required=True);p.add_argument('--prefix',default='mm-phase1-clusters-k');p.add_argument('--port',type=int,default=18400);p.add_argument('--cases',nargs='*',choices=CASES);Suite(p.parse_args()).run()
