#!/usr/bin/env python3
import ginkgo_events
"""#18真实Agent门禁：每例独立平台Pod和目标fixture，完整批次持有跨票API互斥锁。"""
import argparse,base64,copy,datetime,fcntl,gzip,hashlib,http.cookiejar,json,os,pathlib,platform,re,secrets,sys,tarfile,tempfile,time,traceback,urllib.error,urllib.request,uuid
from transport import process as subprocess
import yaml
from clusters import Suite as ClusterSuite
from ksm import manifest,source_state,fingerprint
from agent_cases import run_case
from agent_browser import run_browser
from agent_projection import projection_case, projection_failure, projection_artifact_scan

ROOT=pathlib.Path(__file__).resolve().parents[2]
QCASES={'R03.agent-paused':'TestR03AgentPausedReadOnly',**{'F01.'+v:'TestF01Agent/'+v for v in ['absent','empty-object','empty-list','unknown-mode']},**{'F03.'+v:'TestF03Agent' for v in ['selector','url','tls-pair']}}
def stamp():return datetime.datetime.now(datetime.timezone.utc).isoformat()
def source_bridge(product,runner):
 """按明确构建输入blob向量证明测试driver更新未改变真实产品/应用fixture。"""
 def vector(ref):
  lines=subprocess.check_output(['git','ls-tree','-r',ref],cwd=ROOT,text=True).splitlines();out={}
  for line in lines:
   meta,path=line.split('\t',1)
   selected=(path in ['Dockerfile','.dockerignore','go.mod','go.sum','deploy/platform.yaml','scripts/generate-rpc.sh'] or path.startswith(('cmd/','internal/','kitex_gen/','web/src/','web/public/')) and not path.endswith('_test.go') or path in ['web/index.html','web/package.json','web/package-lock.json'] or path.startswith(('web/tsconfig','web/vite.config','scripts/acceptance/Dockerfile.')) or path.startswith('scripts/acceptance/fixtures/'))
   if selected:out[path]=meta.split()[2]
  return out
 before,after=vector(product),vector(runner);assert before==after,'source bridge产品或应用fixture构建输入变化，必须重新构建'
 digest=lambda v:hashlib.sha256(json.dumps(v,sort_keys=True,separators=(',',':')).encode()).hexdigest()
 return {'verified':True,'productCommit':product,'productTree':subprocess.check_output(['git','rev-parse',product+'^{tree}'],cwd=ROOT,text=True).strip(),'runnerCommit':runner,'productInputVector':before,'productInputSHA256':digest(before),'runnerInputSHA256':digest(after),'changedPaths':subprocess.check_output(['git','diff','--name-only',product,runner],cwd=ROOT,text=True).splitlines(),'method':'明确产品/应用fixture输入blob向量逐项相等；真实镜像仍使用productCommit'}
def owned():
 ids=re.findall(r'^\| ([A-Z]\d+\.[^ |]+) \|', (ROOT/'docs/e2e/issue-18.md').read_text(),re.M)
 assert len(ids)==len(set(ids))==69,'固定owned子例必须保持69且无重复'
 return ids
KCASES=[v for v in owned() if manifest()[v]['layer']=='K']
assert len(KCASES)==59,'默认owned K数量不得混入补充用例'
BCASES=['U02.primary','U06.primary']
# 关键：补充K只允许显式选择，默认owned批次始终59项。
PROJECTION_KCASES=['REG18-PROJECTION-013.'+v for v in ['url-parse-error','proxy-parse-error']]
REGCASES={
 'REG18-SHARED-001':['TestSharedSchema4PreviewSurvivesUpgrade'],
 'REG18-SHARED-002':['TestSharedSchema4PreviewSurvivesUpgrade','TestAgentTypedDraftIdentityAndDefaults'],
 'REG18-SHARED-003':['TestSharedTargetSensitiveReadProjection','TestSharedHMACDetectsHiddenConfigAndPreservesMetadata'],
 'REG18-CAP-004':['TestAgentOperatorCapabilityBoundaries/missing-agent-controller'],
 'REG18-CAP-005':['TestAgentOperatorCapabilityBoundaries/missing-scrape-informer'],
 'REG18-CAP-004.single-dash-namespace':['TestAgentOperatorCapabilityBoundaries/single-dash-namespace'],
 'REG18-CAP-004.single-dash-selector':['TestAgentOperatorCapabilityBoundaries/single-dash-selector'],
 'REG18-CAP-004.override':['TestAgentOperatorCapabilityBoundaries/identity-override'],
 'REG18-CAP-004.old-rs':['TestAgentOperatorCapabilityBoundaries/old-rs-mismatch','TestAgentOperatorCapabilityBoundaries/mixed-pod-scope'],
 'REG18-CAP-004.none-short-circuit':['TestAgentOperatorCapabilityBoundaries/none-without-scrape-api'],
 'REG18-VALID-006':['TestAgentDurationRange'],
 'REG18-SOURCE-007':['TestAgentDraftSourceIsolation'],
 'REG18-FIXED-008':['TestAgentFixedCapacityRevalidated','TestAgentFixedCapacityPossiblySent'],
 'REG18-HEADERS-009':['TestAgentHeadersReadProjection'],
 **{'REG18-CAP-010.'+v:['TestAgentOperatorCapabilityBoundaries/'+v] for v in ['positional','terminator','duplicate-scope','boolean-arity','known-scope-space','env-expansion']},
 **{'REG18-SELECTOR-012.'+v:['TestAgentSelectorExpressionProjection/'+v] for v in ['exists-nil','exists-empty','not-exists-nil','not-exists-empty','in','not-in']},
 **{'REG18-PROJECTION-013.'+v:['TestAgentMalformedURLReadProjection/'+v] for v in ['url-parse-error','proxy-parse-error']}}

def run_regressions(a):
 """补充回归单独计数：真实HTTP/Kitex/SQLite加受控目标，不冒充Operator真实启动故障。"""
 source=source_state();assert not source['dirty'],'正式补充回归需要干净来源';assert a.profile=='LOCAL_FULL'
 contracts={}
 for line in (ROOT/'docs/e2e/issue-18.md').read_text().splitlines():
  cells=[c.strip() for c in line.strip('|').split('|')]
  if len(cells)==5 and cells[0] in REGCASES:contracts[cells[0]]={'variantId':cells[0],'purpose':cells[1],'steps':cells[2],'expected':cells[3]}
 assert set(contracts)==set(REGCASES),'补充计划与runner不一致'
 selected=a.cases or list(REGCASES)
 out=pathlib.Path(a.output);out.mkdir(parents=True,exist_ok=True)
 started=stamp();command,r,events=ginkgo_events.run([s for v in selected for s in REGCASES[v]],out/'.private'/'ginkgo.json',subprocess.run,cwd=ROOT,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
 log=out/'go-test.jsonl';raw=('\n'.join(json.dumps(e) for e in events)+'\n').encode();log.write_bytes(raw);passed=ginkgo_events.passed(events)
 records=[]
 for variant in selected:
  selectors=REGCASES[variant];record={**contracts[variant],'kind':'supplementary','layer':'Q','profile':'LOCAL_FULL','source':source,'testedCommit':source['commit'],'testedTree':source['tree'],'runnerCommit':source['commit'],'testSelectors':selectors,'executionBoundary':'实际HTTP/Kitex/SQLite；受控TLS目标API/metrics/派发屏障','runtimeOS':sys.platform,'runtimeArch':platform.machine(),'contractFingerprint':fingerprint(contracts[variant]),'command':command,'commandExitCode':r.returncode,'startedAt':started,'finishedAt':stamp(),'outcome':'passed' if r.returncode==0 and all(s in passed for s in selectors) else 'failed','evidenceRefs':[{'path':log.name,'sha256':hashlib.sha256(raw).hexdigest()}],'cleanup':{'success':True,'scope':'Go t.TempDir及测试listeners/屏障按测试finally关闭'}}
  (out/(variant+'.json')).write_text(json.dumps(record,ensure_ascii=False,indent=2));records.append(record)
 (out/'results.json').write_text(json.dumps(records,ensure_ascii=False,indent=2));print(json.dumps({'supplementary':len(records),'passed':sum(r['outcome']=='passed' for r in records),'ownedCountUnaffected':69}));return 0 if all(r['outcome']=='passed' for r in records) else 1

class Suite(ClusterSuite):
 def __init__(self,a):
  super().__init__(a);self.contract=manifest();self.source=source_state();self.traces=[];self.resources=[];self.namespace_uids={};self.ksms=[];self.restorers=[];self.target='control';self.targetns=None
  for line in (ROOT/'docs/e2e/issue-18.md').read_text().splitlines():
   cells=[c.strip() for c in line.strip('|').split('|')]
   if len(cells)==5 and cells[0] in ['REG18-SOURCE-007',*PROJECTION_KCASES]:self.contract[cells[0]]={'variantId':cells[0],'caseId':cells[0],'layer':'K' if cells[0] in PROJECTION_KCASES else 'B','kind':'supplementary','purpose':cells[1],'steps':cells[2],'expected':cells[3],'profiles':['LOCAL_FULL']}
  assert not self.source['dirty'] or a.allow_dirty_runner,'门禁来源必须干净'
  if self.session:assert a.source==self.source['commit'] and not self.source['dirty'],'正式Session必须同一干净来源'
  self.bridge=None
  if a.source!=self.source['commit']:
   assert a.allow_source_bridge,'运行器与产品来源不同，需要显式source bridge证明'
   self.bridge=source_bridge(a.source,self.source['commit'])
  self.image_metadata=self.image_proof(a.image);self.environment=self.target_environment()
  # 证书仅用于独占fixture；私钥只驻留内存和0600临时文件，生成配置也不输出到证据。
  with tempfile.TemporaryDirectory(prefix='mm18-tls-') as d:
   cert=pathlib.Path(d)/'cert';key=pathlib.Path(d)/'key'
   subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-keyout',str(key),'-out',str(cert),'-days','1','-subj','/CN=receiver.example.invalid'],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
   os.chmod(key,0o600);self.secret_values={'username':secrets.token_urlsafe(12),'password':secrets.token_urlsafe(24),'credentials':secrets.token_urlsafe(32),'ca':cert.read_text(),'cert':cert.read_text(),'key':key.read_text()}
  if self.session:self.session.register_secrets(*self.secret_values.values())

 def k(self,*args,data=None,target='control',check=True):
  return super().k(*args,data=data,target=target,check=check)
 def get(self,*args,target=None):return json.loads(self.k('get',*args,'-o','json',target=target or self.target).stdout)
 def apply(self,obj,target=None):self.k('create','-f','-',data=obj,target=target or self.target)
 def deploy(self,case,index):
  if not getattr(self.a,'initialize',True) and case!='I01.primary':raise ValueError('SUITE_INITIALIZATION_SKIP_INVALID')
  # 平台namespace也由本例显式创建并登记UID，名称冲突时不能误清理既有资源。
  self.ns=self.a.prefix+'-'+str(index);self.port=self.a.port+index;self.namespace(self.ns,'control');objects=[]
  for raw in yaml.safe_load_all(open(ROOT/'deploy/platform.yaml')):
   if raw is None:continue
   raw['metadata']['namespace']=self.ns;raw['metadata'].setdefault('labels',{})['monitor-mgr-run']=self.a.prefix
   if raw['kind']=='Deployment':
    raw['spec']['template']['metadata'].setdefault('annotations',{})['monitor-mgr-source']=self.a.source
    container=raw['spec']['template']['spec']['containers'][0];container['image']=self.a.image;container['imagePullPolicy']='Never'
   objects.append(raw)
  self.k('apply','-f','-',data={'apiVersion':'v1','kind':'List','items':objects});self.k('-n',self.ns,'rollout','status','deployment/monitor-mgr','--timeout=120s')
  pods=self.get('pods','-n',self.ns,'-l','app=monitor-mgr',target='control')['items'];assert len(pods)==1
  pod=pods[0];self.pod=pod['metadata']['name'];self.platform_pod_uid=pod['metadata']['uid'];self.initial_uid=pod['metadata']['uid'];self.connect()
  if getattr(self.a,'initialize',True):
   self.call('POST','/initialization',{'username':'admin','password':self.password},status=201);self.call('POST','/sessions',{'username':'admin','password':self.password},status=201);self.owner=self.call('GET','/session')['account']['id']
  return {'namespaceUID':self.namespace_uids[('control',self.ns)],'podUID':self.initial_uid,'node':pod['spec']['nodeName'],'imageID':pod['status']['containerStatuses'][0]['imageID'],'source':self.a.source}
 def connect(self):
  self.cookies=http.cookiejar.CookieJar();super().connect()
 def call(self,method,path,body=None,key=None,status=200):
  if self.session:self.session.http_intent(self,method,path,body)
  req=urllib.request.Request(self.base+'/api/v1'+path,data=None if body is None else json.dumps(body).encode(),method=method,headers={'Origin':self.base,'Content-Type':'application/json','Idempotency-Key':key or str(uuid.uuid4())})
  try:r=self.http.open(req,timeout=30)
  except urllib.error.HTTPError as e:r=e
  value=json.load(r);self.http_observed(method,path,body,r,value);r.close();self.traces.append({'at':stamp(),'method':method,'path':path,'status':r.code,'requestId':r.headers.get('X-Request-Id',''),'operationId':value.get('operationId','')})
  assert r.code==status,(path,r.code,value.get('code'),value.get('message'));return value
 def cfg(self,name=None,namespace=None):
  rule=lambda:{'object':{'mode':'None'},'namespace':{'mode':'Own'}}
  return {'name':name or self.crname,'namespace':namespace or self.targetns,'desiredState':'Running','shards':1,'resources':{'requestCPU':'200m','requestMemory':'512Mi','limitMemory':'1Gi'},'storage':{'mode':'PVC','capacity':'1Gi','whenScaled':'Retain','whenDeleted':'Retain'},'serviceAccountName':'prometheus-agent','associations':{k:rule() for k in ['serviceMonitor','podMonitor','probe','scrapeConfig']},'remoteWrite':[{'url':'http://receiver.example.invalid/write','auth':{'mode':'None'}}]}
 def create(self,cfg=None,cluster=None):return self.call('POST','/clusters/'+(cluster or self.cluster['id'])+'/instances',{'component':'Prometheus Agent','agent':cfg or self.cfg()},status=201)
 def save(self,i,cfg,status=200):
  rev=self.call('GET','/instances/'+i['id']+'/draft')['revision']
  return self.call('PUT','/instances/'+i['id']+'/draft',{'expectedDraftRevision':rev,'agent':cfg},status=status)
 def preview(self,i,status=200):
  rev=self.call('GET','/instances/'+i['id']+'/draft')['revision']
  return self.call('POST','/instances/'+i['id']+'/previews',{'draftRevision':rev},status=status)
 def publish(self,i):
  p=self.preview(i);o=self.call('POST','/instances/'+i['id']+'/publications',{'draftRevision':p['draftRevision'],'previewId':p['previewId'],'overwriteDrift':False},status=202);done=self.operation(o)
  assert done['state']=='SUCCEEDED',done;return done
 def operation(self,op):
  end=time.monotonic()+45;last={}
  while time.monotonic()<end:
   last=self.call('GET','/operations/'+op['operationId'])
   if last['state'] not in ['QUEUED','RUNNING','CHECKING','DISPATCHED']:return last
   time.sleep(.15)
  raise AssertionError('操作未结束：'+json.dumps(last,ensure_ascii=False))
 def cr(self,name=None,namespace=None,target=None):return self.get('prometheusagent',name or self.crname,'-n',namespace or self.targetns,target=target)
 def sources(self,i):return {s['source']:s for s in self.call('GET','/instances/'+i['id']).get('sources',[])}
 def association_preview(self,i):
  rev=self.call('GET','/instances/'+i['id']+'/draft')['revision']
  value=self.call('POST','/instances/'+i['id']+'/association-previews',{'draftRevision':rev});assert value['draftRevision']==rev
  return {s['source']:s for s in value['sources']}
 def namespace(self,name,target=None):
  target=target or self.target
  obj={'apiVersion':'v1','kind':'Namespace','metadata':{'name':name,'labels':{'monitor-mgr-run':self.a.prefix}}}
  created=json.loads(self.k('create','-f','-','-o','json',data=obj,target=target).stdout);self.namespace_uids[(target,name)]=created['metadata']['uid']
  self.resources.append((target,'namespace',name))
 def fixture(self,namespace,target=None,agent_rbac=True):
  target=target or self.target;self.namespace(namespace,target)
  self.apply({'apiVersion':'v1','kind':'ServiceAccount','metadata':{'name':'prometheus-agent','namespace':namespace}},target)
  if agent_rbac:
   role=namespace+'-agent';self.apply({'apiVersion':'rbac.authorization.k8s.io/v1','kind':'ClusterRole','metadata':{'name':role},'rules':[{'apiGroups':[''],'resources':['pods','services','endpoints','nodes','configmaps'],'verbs':['get','list','watch']},{'apiGroups':['networking.k8s.io'],'resources':['ingresses'],'verbs':['get','list','watch']},{'apiGroups':['discovery.k8s.io'],'resources':['endpointslices'],'verbs':['get','list','watch']}]},target)
   self.resources.append((target,'clusterrole',role));self.apply({'apiVersion':'rbac.authorization.k8s.io/v1','kind':'ClusterRoleBinding','metadata':{'name':role},'roleRef':{'apiGroup':'rbac.authorization.k8s.io','kind':'ClusterRole','name':role},'subjects':[{'kind':'ServiceAccount','name':'prometheus-agent','namespace':namespace}]},target);self.resources.append((target,'clusterrolebinding',role))
  self.apply({'apiVersion':'v1','kind':'Secret','metadata':{'name':'agent-auth','namespace':namespace},'type':'Opaque','stringData':self.secret_values},target)
 def prepare_target(self,index,target='control'):
  self.target=target;self.targetns=self.a.prefix+'-t'+str(index);self.crname='agent-'+uuid.uuid4().hex[:10]
  self.fixture(self.targetns,target,agent_rbac=self.case!='A13.insufficient-sa');self.cluster,_=self.registered(self.config(target));return self.cluster
 def runtime(self,i,namespace=None,name=None,target=None,capacity='1Gi'):
  namespace=namespace or self.targetns;name=name or self.crname;target=target or self.target;end=time.monotonic()+150;last={}
  while time.monotonic()<end:
   last=self.call('POST','/instances/'+i['id']+'/refresh',{})
   cr=self.cr(name,namespace,target);sts=self.get('statefulsets','-n',namespace,target=target)['items'];sts=[s for s in sts if any(o.get('uid')==cr['metadata']['uid'] and o.get('controller') for o in s['metadata'].get('ownerReferences',[]))]
   pods=self.get('pods','-n',namespace,target=target)['items'];pods=[p for p in pods if not p['metadata'].get('deletionTimestamp') and any(o.get('uid') in {s['metadata']['uid'] for s in sts} and o.get('controller') for o in p['metadata'].get('ownerReferences',[]))]
   ready=lambda p:any(c['type']=='Ready' and c['status']=='True' for c in p.get('status',{}).get('conditions',[]))
   if last['summary']=='Available' and last.get('desiredPods')==last.get('availablePods')==1 and len(sts)==len(pods)==1 and ready(pods[0]):
    pod=pods[0];prom=next(c for c in pod['spec']['containers'] if c['name']=='prometheus');assert prom['resources']=={'requests':{'cpu':'200m','memory':'512Mi'},'limits':{'memory':'1Gi'}}
    assert prom['image'].endswith('@sha256:2659f4c2ebb718e7695cb9b25ffa7d6be64db013daba13e05c875451cf51b0d3')
    status=next(c for c in pod['status']['containerStatuses'] if c['name']=='prometheus');image_proof=self.prometheus_image(pod,prom,status,target)
    mounts={m['name'] for m in prom['volumeMounts']};claims=[v['persistentVolumeClaim']['claimName'] for v in pod['spec']['volumes'] if 'persistentVolumeClaim' in v and v['name'] in mounts];assert len(claims)==1
    pvc=self.get('pvc',claims[0],'-n',namespace,target=target);assert pvc['status']['phase']=='Bound' and pvc['spec']['resources']['requests']['storage']==capacity and pvc['spec']['accessModes']==['ReadWriteOnce']
    return {'runtime':last,'cr':cr,'statefulSet':sts[0],'pod':{'name':pod['metadata']['name'],'uid':pod['metadata']['uid'],'node':pod['spec']['nodeName'],'imageID':status['imageID'],'imageProof':image_proof,'resources':prom['resources'],'mounts':prom['volumeMounts']},'pvc':{'name':pvc['metadata']['name'],'uid':pvc['metadata']['uid'],'spec':pvc['spec'],'phase':pvc['status']['phase']}}
   time.sleep(.5)
  raise AssertionError('Agent真实ST/Pod/PVC未收敛：'+str(last))
 def prometheus_image(self,pod,container,status,target):
  """绑定原Pod/运行容器和节点内容链，不把CRI报告官方index误判为镜像变化。"""
  index='sha256:2659f4c2ebb718e7695cb9b25ffa7d6be64db013daba13e05c875451cf51b0d3'
  node=pod['spec']['nodeName'];assert node in {n['name'] for n in self.environment[target]['nodes']}
  assert container['image']=='quay.io/prometheus/prometheus@'+index
  def node_json(*args):return json.loads(subprocess.check_output(['docker','exec',node,*args],stderr=subprocess.PIPE))
  def content(digest):
   raw=subprocess.check_output(['docker','exec',node,'ctr','-n','k8s.io','content','get',digest],stderr=subprocess.PIPE)
   assert 'sha256:'+hashlib.sha256(raw).hexdigest()==digest,'节点OCI内容摘要不符'
   return json.loads(raw)
  original=content(index);children=[m for m in original['manifests'] if m['platform']['os']=='linux' and m['platform']['architecture']==self.runtime_arch];assert len(children)==1;child=children[0]['digest']
  manifest=content(child);config_digest=manifest['config']['digest'];config=content(config_digest)
  assert config['os']=='linux' and config['architecture']==self.runtime_arch
  container_id=status['containerID'].removeprefix('containerd://');assert re.fullmatch('[0-9a-f]{64}',container_id)
  inspected=node_json('crictl','inspect',container_id);actual=inspected['status']
  # 关键：CRI查询限定原容器ID及原Pod UID，不能用同节点另一绿色容器冒充当前Pod。
  assert actual['id']==container_id and actual['labels']['io.kubernetes.pod.uid']==pod['metadata']['uid'] and actual['metadata']['name']=='prometheus'
  actual_ref=actual['imageRef'];assert inspected['info']['config']['image']['image']==config_digest,'原容器实际config与官方内容链不符'
  image=node_json('crictl','inspecti',config_digest)['status'];assert image['id']==config_digest
  reported=re.search(r'sha256:[0-9a-f]{64}$',status['imageID']);assert reported and reported[0] in {index,child,config_digest},'kubelet镜像ID不在已验证的官方内容链'
  return {'podUID':pod['metadata']['uid'],'containerID':container_id,'node':node,'indexDigest':index,'runtimeManifestDigest':child,'configDigest':config_digest,'criImageId':image['id'],'criImageRef':actual_ref,'criStatusImage':actual['image']['image'],'kubeletImageID':status['imageID'],'contentBytesSHA256Verified':True,'os':config['os'],'architecture':config['architecture']}
 def collection(self,kind,namespace,name='matched',label='yes'):
  version='v1alpha1' if kind=='ScrapeConfig' else 'v1'
  specs={'ServiceMonitor':{'selector':{},'endpoints':[{'port':'http'}]},'PodMonitor':{'selector':{},'podMetricsEndpoints':[{'port':'http'}]},'Probe':{'prober':{'url':'blackbox.example.invalid:9115'},'targets':{'staticConfig':{'static':['https://example.invalid']}}},'ScrapeConfig':{'staticConfigs':[{'targets':['example.invalid:9090']}]}}
  obj={'apiVersion':'monitoring.coreos.com/'+version,'kind':kind,'metadata':{'name':name,'namespace':namespace,'labels':{'mm18-match':label}},'spec':specs[kind]}
  self.apply(obj);return self.get(kind.lower(),name,'-n',namespace)
 def restricted_credentials(self,omit):
  # 真正RBAC拒绝，而不是合成HTTP403；身份切换仍走产品凭据candidate流程。
  name=self.targetns+'-reader';self.apply({'apiVersion':'v1','kind':'ServiceAccount','metadata':{'name':'reader','namespace':self.targetns}})
  core=['namespaces','nodes','serviceaccounts','pods','pods/proxy','configmaps','events','persistentvolumes']+[r for r in ['secrets','persistentvolumeclaims'] if r!=omit]
  monitoring=[r for r in ['prometheusagents','servicemonitors','podmonitors','probes','scrapeconfigs'] if r!=omit]
  rules=[{'apiGroups':[''],'resources':core,'verbs':['get','list','watch']},{'apiGroups':['apps'],'resources':['deployments','replicasets','statefulsets'],'verbs':['get','list','watch']},{'apiGroups':['monitoring.coreos.com'],'resources':monitoring,'verbs':['get','list','watch']},{'apiGroups':['monitoring.coreos.com'],'resources':['prometheusagents'],'verbs':['create','update','delete']},{'apiGroups':['storage.k8s.io'],'resources':['storageclasses'],'verbs':['get','list','watch']},{'apiGroups':['authentication.k8s.io'],'resources':['selfsubjectreviews'],'verbs':['create']},{'apiGroups':['authorization.k8s.io'],'resources':['selfsubjectaccessreviews','subjectaccessreviews'],'verbs':['create']},{'nonResourceURLs':['*'],'verbs':['get']}]
  self.apply({'apiVersion':'rbac.authorization.k8s.io/v1','kind':'ClusterRole','metadata':{'name':name},'rules':rules});self.resources.append((self.target,'clusterrole',name))
  self.apply({'apiVersion':'rbac.authorization.k8s.io/v1','kind':'ClusterRoleBinding','metadata':{'name':name},'roleRef':{'apiGroup':'rbac.authorization.k8s.io','kind':'ClusterRole','name':name},'subjects':[{'kind':'ServiceAccount','name':'reader','namespace':self.targetns}]});self.resources.append((self.target,'clusterrolebinding',name))
  token=self.k('-n',self.targetns,'create','token','reader','--duration=1h',target=self.target).stdout.decode().strip();cfg=self.config(self.target);cfg['users']=[{'name':'reader','user':{'token':token}}];cfg['contexts'][0]['context']['user']='reader'
  done=self.operation(self.accept(cfg,path='/clusters/'+self.cluster['id']+'/credentials'));assert done['state']=='SUCCEEDED',done
  return {'operationId':done['operationId'],'identity':'system:serviceaccount:'+self.targetns+':reader','omittedResource':omit}
 def restore_credentials(self):
  done=self.operation(self.accept(self.config(self.target),path='/clusters/'+self.cluster['id']+'/credentials'));assert done['state']=='SUCCEEDED'
 def generated_config(self,name=None):
  # 关键：生成配置含解析后的Secret值，只在受控内存比较，返回报告前必须转为布尔断言。
  secret=self.get('secret','prom-agent-'+(name or self.crname),'-n',self.targetns)
  return yaml.safe_load(gzip.decompress(base64.b64decode(secret['data']['prometheus.yaml.gz'])))
 def cleanup(self):
  # 关键：统一运行只按 Session 原 UID ledger 清理，不能先执行旧诊断恢复或前缀认领。
  if self.session:return self.session.cleanup_case(self)
  failures=[]
  for restore in reversed(self.restorers):
   try:restore()
   except Exception:failures.append('全局故障恢复失败')
  self.restorers=[]
  if hasattr(self,'forward'):
   self.forward.terminate();self.forward.wait(timeout=5);del self.forward
  for target,name,uid in self.ksms:
   try:
    actual=self.get('kubestatemetrics',name,target=target);assert actual['metadata']['uid']==uid
    self.k('delete','kubestatemetrics',name,'--wait=true','--timeout=90s',target=target)
   except Exception:failures.append('KSM独占fixture清理失败')
  self.ksms=[]
  # 关键：这是测试fixture清理，不是产品删除能力；先正常GC原Agent/负载，避免namespace按600s Pod grace估计重复空等。
  namespaces=[];workloads=[]
  # 清理读取必须区分真实NotFound和权限/网络错误；后者不能变成空集合。
  def existing(item):
   args=['get',item['kind'],item['name'],'-o','json']
   if item.get('namespace'):args+=['-n',item['namespace']]
   result=self.k(*args,target=item['target'],check=False)
   if result.returncode:
    if b'(NotFound)' in result.stderr:return None
    raise RuntimeError('清理读取失败，不能当作空集合')
   return json.loads(result.stdout)
  # 仅等待原UID消失并记录可见终态，不延长宽限、不处理替代UID。
  def wait_absent(items):
   pending=[item for item in items if not item.get('notFoundAt')];end=time.monotonic()+660
   while pending and time.monotonic()<end:
    for item in pending[:]:
     actual=existing(item)
     if actual is None:item['notFoundAt']=stamp();pending.remove(item);continue
     assert actual['metadata']['uid']==item['uid'],'清理遇到替代UID，停止等待与删除'
     item['lastDeletionTimestamp']=actual['metadata'].get('deletionTimestamp')
     if item['kind']=='pod':item.update({'lastPhase':actual.get('status',{}).get('phase'),'lastContainers':[{'name':c['name'],'terminated':{k:v for k,v in c.get('state',{}).get('terminated',{}).items() if k in ['exitCode','reason','startedAt','finishedAt']}} for c in actual.get('status',{}).get('containerStatuses',[])]})
    if pending:time.sleep(.5)
   assert not pending,'正常GC未在660s预算内结束'
  try:
   for target,kind,name in reversed(self.resources):
    if kind!='namespace':continue
    item={'target':target,'kind':'namespace','name':name,'uid':self.namespace_uids[(target,name)]};namespaces.append(item);actual=existing(item)
    if actual is None:item['notFoundAt']=stamp();continue
    assert actual['metadata']['uid']==item['uid'] and actual['metadata']['labels'].get('monitor-mgr-run')==self.a.prefix
    agents=self.get('prometheusagents','-n',name,target=target)['items'];agent_uids={a['metadata']['uid'] for a in agents}
    sts=[s for s in self.get('statefulsets','-n',name,target=target)['items'] if any(o.get('uid') in agent_uids and o.get('controller') for o in s['metadata'].get('ownerReferences',[]))];sts_uids={s['metadata']['uid'] for s in sts}
    pods=[p for p in self.get('pods','-n',name,target=target)['items'] if any(o.get('uid') in sts_uids and o.get('controller') for o in p['metadata'].get('ownerReferences',[]))]
    for resource,objects in [('prometheusagent',agents),('statefulset',sts),('pod',pods)]:
     for obj in objects:
      entry={'target':target,'kind':resource,'namespace':name,'name':obj['metadata']['name'],'uid':obj['metadata']['uid']}
      if resource=='pod':entry.update({'terminationGracePeriodSeconds':obj['spec'].get('terminationGracePeriodSeconds'),'initialDeletionTimestamp':obj['metadata'].get('deletionTimestamp'),'initialPhase':obj.get('status',{}).get('phase'),'initialContainers':[{'name':c['name'],'containerID':c.get('containerID'),'terminated':{k:v for k,v in c.get('state',{}).get('terminated',{}).items() if k in ['exitCode','reason','startedAt','finishedAt']}} for c in obj.get('status',{}).get('containerStatuses',[])]})
      workloads.append(entry)
    for obj in agents:
     path='/apis/'+obj['apiVersion']+'/namespaces/'+name+'/prometheusagents/'+obj['metadata']['name']
     self.k('delete','--raw',path,'-f','-',data={'apiVersion':'v1','kind':'DeleteOptions','preconditions':{'uid':obj['metadata']['uid']},'propagationPolicy':'Foreground'},target=target)
   wait_absent(workloads)
   # 所有namespace先按原UID发删除，统一等待；namespace全部完成之前保留RBAC。
   for item in namespaces:
    if item.get('notFoundAt'):continue
    actual=existing(item);assert actual and actual['metadata']['uid']==item['uid'] and actual['metadata']['labels'].get('monitor-mgr-run')==self.a.prefix
    self.k('delete','--raw','/api/v1/namespaces/'+item['name'],'-f','-',data={'apiVersion':'v1','kind':'DeleteOptions','preconditions':{'uid':item['uid']}},target=item['target']);item['deleteAcceptedAt']=stamp()
   wait_absent(namespaces)
  except Exception as error:failures.append('fixture正常GC清理失败：'+str(error))
  if all(item.get('notFoundAt') for item in namespaces) and len(namespaces)==sum(kind=='namespace' for _,kind,_ in self.resources):
   for target,kind,name in reversed(self.resources):
    if kind=='namespace':continue
    result=self.k('delete',kind,name,'--wait=true','--timeout=120s',target=target,check=False)
    if result.returncode:failures.append(kind+'/'+name+'清理失败')
  else:failures.append('namespace未确认全部NotFound，保留本case RBAC供恢复')
  self.resources=[]
  if hasattr(self,'ns'):del self.ns
  return {'success':not failures,'failures':failures,'namespaceResults':namespaces,'agentWorkloadResults':workloads,'mode':'测试工具按原UID正常Foreground/namespace GC；无force或finalizer修改'}
 def run(self):
  for index,case in enumerate(self.a.cases or KCASES):
   self.case=case;self.traces=[];self.targetns=None;self.projection_markers=[];record={**self.contract[case],'source':self.source,'testedCommit':self.a.source,'testedTree':self.bridge['productTree'] if self.bridge else self.source['tree'],'runnerCommit':self.source['commit'],'sourceBridge':self.bridge,'profile':self.a.profile,'manifestHash':hashlib.sha256((ROOT/'docs/acceptance/phase1.json').read_bytes()).hexdigest(),'contractFingerprint':fingerprint(self.contract[case]),'testSelector':case,'command':[sys.executable,*sys.argv],'processLocation':'host-driver/platform-pod','hostOS':platform.system(),'hostArch':platform.machine(),'runtimeOS':'linux','runtimeArch':self.runtime_arch,'environment':self.environment,'productImage':self.image_metadata,'outcome':'failed','startedAt':stamp()}
   try:
    record['platform']=self.deploy(case,index);assert record['platform']['imageID'].endswith(self.image_metadata.get('manifestDigest') or self.image_metadata['dockerInspectId'])
    self.prepare_target(index,'data' if case in ['C04.agent-crd','A05.primary'] else 'control');record['evidence']=run_browser(self,case) if self.a.layer=='B' else (projection_case(self,case) if case in PROJECTION_KCASES else run_case(self,case));record['outcome']='passed'
   except Exception as e:
    if case in PROJECTION_KCASES:record.update(projection_failure(self))
    else:
     record['error']=str(e);record['traceback']=traceback.format_exc()
     try:
      record['platformLogs']=self.k('logs','pod/'+self.pod,'-n',self.ns,'--tail=150').stdout.decode()
     except Exception:record['platformLogsError']='平台日志不可读'
     if self.targetns:
      try:
       record['failureEvents']=[{'reason':e.get('reason'),'message':e.get('message'),'object':e.get('involvedObject',{}),'lastTimestamp':e.get('lastTimestamp')} for e in self.get('events','-n',self.targetns)['items']]
       record['actualFixtureAgents']=self.get('prometheusagents','-n',self.targetns)['items']
      except Exception:record['failureEventsError']='事件读取失败'
   finally:
    record['traces']=self.traces
    # 即使浏览器中途失败，也登记本例namespace内已创建的KSM，不能遗留cluster-scoped对象。
    if self.a.layer=='B' and self.targetns:
     try:
      seen={v[1] for v in self.ksms}
      for cr in self.get('kubestatemetrics')['items']:
       if cr['spec'].get('targetNamespace')==self.targetns and cr['metadata']['name'] not in seen:self.ksms.append((self.target,cr['metadata']['name'],cr['metadata']['uid']))
     except Exception:record['browserCleanupInventoryError']='KSM列表失败，无法确认清理范围'
    try:record['cleanup']=self.cleanup()
    except Exception as e:record['cleanup']={'success':False,'failures':[str(e)]}
    if record.get('browserCleanupInventoryError'):record['cleanup']['success']=False;record['cleanup']['failures'].append(record['browserCleanupInventoryError'])
    if not record['cleanup']['success']:record['outcome']='failed'
    trace=self.out/(case+'.http.jsonl');trace.write_text(''.join(json.dumps(t,ensure_ascii=False)+'\n' for t in self.traces));record['evidenceRefs']=[{'path':trace.name,'sha256':hashlib.sha256(trace.read_bytes()).hexdigest()},*record.get('evidence',{}).get('evidenceRefs',[])]
    if case in PROJECTION_KCASES:
     record['projectionArtifactScan']=projection_artifact_scan(self,record)
     if not record['projectionArtifactScan']['noMarker']:record['outcome']='failed'
    record['finishedAt']=stamp();(self.out/(case+'.json')).write_text(json.dumps(record,ensure_ascii=False,indent=2));self.results.append(record);(self.out/'results.json').write_text(json.dumps(self.results,ensure_ascii=False,indent=2));print(case,record['outcome'],flush=True)
   if not record['cleanup']['success'] or record['outcome']!='passed' and self.a.fail_fast:break
  return 0 if all(r['outcome']=='passed' for r in self.results) else 1

def main():
 p=argparse.ArgumentParser();p.add_argument('--layer',choices=['Q','K','B','REG'],default='K');p.add_argument('--profile',choices=['LOCAL_FULL','CI_FAST','CI_SMOKE'],default='LOCAL_FULL');p.add_argument('--list',action='store_true');p.add_argument('--cases',nargs='*');p.add_argument('--output');p.add_argument('--allow-dirty-runner',action='store_true');p.add_argument('--allow-source-bridge',action='store_true');p.add_argument('--fail-fast',action='store_true')
 for field in ['kubectl','control-config','data-config','control-context','data-context','control-pod-config','data-pod-config','image','source','api-lane']:p.add_argument('--'+field)
 p.add_argument('--prefix',default='mm18-agent');p.add_argument('--port',type=int,default=18700);a=p.parse_args();contract=manifest()
 valid=list(REGCASES) if a.layer=='REG' else (list(QCASES) if a.layer=='Q' else (BCASES+['REG18-SOURCE-007'] if a.layer=='B' else KCASES+PROJECTION_KCASES))
 if a.cases is not None and (not a.cases or any(v not in valid for v in a.cases)):p.error('未知/层级不符子例')
 if a.list:
  selected=a.cases or (list(REGCASES) if a.layer=='REG' else (list(QCASES) if a.layer=='Q' else (BCASES if a.layer=='B' else KCASES)))
  print(json.dumps([{'id':v,'layer':'Q' if a.layer=='REG' else a.layer,'kind':'supplementary' if v.startswith('REG') else 'owned','testSelector':REGCASES[v] if a.layer=='REG' else QCASES.get(v,v),'profiles':contract[v]['profiles'] if v in contract else ['LOCAL_FULL']} for v in selected],ensure_ascii=False,indent=2));return 0
 if a.layer=='REG':
  if not a.output:p.error('补充回归需要output及已计划子例')
  return run_regressions(a)
 if any(a.profile not in (contract[v]['profiles'] if v in contract else ['LOCAL_FULL']) for v in a.cases or valid):p.error('子例不属于profile')
 if not a.output:p.error('必须显式output')
 if a.layer=='Q':
  from ksm_q import run_q
  return run_q(a,QCASES,contract,ROOT,source_state(),stamp)
 for field in ['kubectl','control_config','data_config','control_context','data_context','control_pod_config','data_pod_config','image','source','api_lane']:
  if not getattr(a,field):p.error('K层必须显式'+field)
 if a.profile!='LOCAL_FULL':p.error('本runner当前只执行实际双集群LOCAL_FULL；CI_SMOKE由最终单集群编排')
 if a.layer=='B':a.cases=a.cases or BCASES
 # 关键：全局namespace/CRD/SC故障会影响其他Operator实例，锁覆盖准备、整批及finally清理。
 with open(a.api_lane,'a') as lane:
  fcntl.flock(lane,fcntl.LOCK_EX);return Suite(a).run()
if __name__=='__main__':raise SystemExit(main())
