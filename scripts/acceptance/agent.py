#!/usr/bin/env python3
"""#18真实Agent门禁：每例独立平台Pod和目标fixture，完整批次持有跨票API互斥锁。"""
import argparse,base64,copy,datetime,fcntl,gzip,hashlib,http.cookiejar,json,os,pathlib,platform,re,secrets,subprocess,sys,tarfile,tempfile,time,traceback,urllib.error,urllib.request,uuid
import yaml
from clusters import Suite as ClusterSuite
from ksm import manifest,source_state,fingerprint
from agent_cases import run_case

ROOT=pathlib.Path(__file__).resolve().parents[2]
QCASES={'R03.agent-paused':'TestR03AgentPausedReadOnly',**{'F01.'+v:'TestF01Agent/'+v for v in ['absent','empty-object','empty-list','unknown-mode']},**{'F03.'+v:'TestF03Agent' for v in ['selector','url','tls-pair']}}
def stamp():return datetime.datetime.now(datetime.timezone.utc).isoformat()
def owned():
 ids=re.findall(r'^\| ([A-Z]\d+\.[^ |]+) \|', (ROOT/'docs/e2e/issue-18.md').read_text(),re.M)
 assert len(ids)==len(set(ids))==69,'固定owned子例必须保持69且无重复'
 return ids
KCASES=[v for v in owned() if manifest()[v]['layer']=='K']
BCASES=['U02.primary','U06.primary']

class Suite(ClusterSuite):
 def __init__(self,a):
  super().__init__(a);self.contract=manifest();self.source=source_state();self.traces=[];self.resources=[];self.ksms=[];self.restorers=[];self.target='control';self.targetns=None
  assert not self.source['dirty'] or a.allow_dirty_runner,'门禁来源必须干净'
  assert a.source==self.source['commit'],'运行器与产品必须同源'
  m=json.loads(subprocess.check_output(['docker','image','inspect',a.image],text=True))[0]
  # Docker29 inspect.Id可能是manifest摘要；真正config摘要必须对docker-save中的配置字节实算。
  with tempfile.TemporaryFile() as saved:
   subprocess.run(['docker','save',a.image],stdout=saved,check=True);saved.seek(0)
   with tarfile.open(fileobj=saved) as archive:
    entry=json.load(archive.extractfile('manifest.json'))[0];config_bytes=archive.extractfile(entry['Config']).read();config=json.loads(config_bytes)
  config_digest='sha256:'+hashlib.sha256(config_bytes).hexdigest()
  self.image_metadata={'dockerInspectId':m['Id'],'manifestDigest':m.get('Descriptor',{}).get('digest'),'configDigest':config_digest,'configDigestMethod':'docker-save manifest.json Config bytes SHA256','os':config['os'],'architecture':config['architecture'],'source':config.get('config',{}).get('Labels',{}).get('org.opencontainers.image.revision')}
  assert self.image_metadata['source']==a.source and m['Os']=='linux' and m['Architecture']=='arm64','镜像来源/架构不符'
  self.environment={}
  for target,count in [('control',3),('data',1)]:
   nodes=self.get('nodes',target=target)['items'];uid=self.get('namespace','kube-system',target=target)['metadata']['uid']
   assert len(nodes)==count and all(n['status']['nodeInfo']['kubeletVersion']=='v1.28.15' and n['status']['nodeInfo']['architecture']=='arm64' for n in nodes),'目标拓扑/版本不符'
   self.environment[target]={'uid':uid,'nodes':[{'name':n['metadata']['name'],'nodeInfo':n['status']['nodeInfo']} for n in nodes]}
  # 证书仅用于独占fixture；私钥只驻留内存和0600临时文件，生成配置也不输出到证据。
  with tempfile.TemporaryDirectory(prefix='mm18-tls-') as d:
   cert=pathlib.Path(d)/'cert';key=pathlib.Path(d)/'key'
   subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-keyout',str(key),'-out',str(cert),'-days','1','-subj','/CN=receiver.example.invalid'],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
   os.chmod(key,0o600);self.secret_values={'username':secrets.token_urlsafe(12),'password':secrets.token_urlsafe(24),'credentials':secrets.token_urlsafe(32),'ca':cert.read_text(),'cert':cert.read_text(),'key':key.read_text()}

 def k(self,*args,data=None,target='control',check=True):
  cfg=self.a.control_config if target=='control' else self.a.data_config;context=self.a.control_context if target=='control' else self.a.data_context
  r=subprocess.run([self.a.kubectl,'--kubeconfig',cfg,'--context',context,*args],input=None if data is None else json.dumps(data).encode(),stdout=subprocess.PIPE,stderr=subprocess.PIPE)
  if check and r.returncode:raise RuntimeError('kubectl '+str(args[:2])+' 失败（参数/目标响应不输出）')
  return r
 def get(self,*args,target=None):return json.loads(self.k('get',*args,'-o','json',target=target or self.target).stdout)
 def apply(self,obj,target=None):self.k('create','-f','-',data=obj,target=target or self.target)
 def connect(self):
  self.base='http://127.0.0.1:'+str(self.port)
  self.forward=subprocess.Popen([self.a.kubectl,'--kubeconfig',self.a.control_config,'--context',self.a.control_context,'-n',self.ns,'port-forward','pod/'+self.pod,str(self.port)+':8080'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
  end=time.monotonic()+20
  while time.monotonic()<end:
   try:
    if urllib.request.urlopen(self.base+'/readyz',timeout=1).status==200:break
   except Exception:time.sleep(.2)
  else:raise RuntimeError('真实平台Pod不可达')
  self.cookies=http.cookiejar.CookieJar();self.http=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.cookies))
 def call(self,method,path,body=None,key=None,status=200):
  req=urllib.request.Request(self.base+'/api/v1'+path,data=None if body is None else json.dumps(body).encode(),method=method,headers={'Origin':self.base,'Content-Type':'application/json','Idempotency-Key':key or str(uuid.uuid4())})
  try:r=self.http.open(req,timeout=30)
  except urllib.error.HTTPError as e:r=e
  value=json.load(r);self.traces.append({'at':stamp(),'method':method,'path':path,'status':r.code,'requestId':r.headers.get('X-Request-Id',''),'operationId':value.get('operationId','')})
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
  self.apply({'apiVersion':'v1','kind':'Namespace','metadata':{'name':name,'labels':{'monitor-mgr-run':self.a.prefix}}},target)
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
    status=next(c for c in pod['status']['containerStatuses'] if c['name']=='prometheus');assert status['imageID'].endswith('sha256:f4def6b3b61109a6eeea59945d578bb7e926c36cb0e036a23e3ceb8b6de024ad')
    mounts={m['name'] for m in prom['volumeMounts']};claims=[v['persistentVolumeClaim']['claimName'] for v in pod['spec']['volumes'] if 'persistentVolumeClaim' in v and v['name'] in mounts];assert len(claims)==1
    pvc=self.get('pvc',claims[0],'-n',namespace,target=target);assert pvc['status']['phase']=='Bound' and pvc['spec']['resources']['requests']['storage']==capacity and pvc['spec']['accessModes']==['ReadWriteOnce']
    return {'runtime':last,'cr':cr,'statefulSet':sts[0],'pod':{'name':pod['metadata']['name'],'uid':pod['metadata']['uid'],'node':pod['spec']['nodeName'],'imageID':status['imageID'],'resources':prom['resources'],'mounts':prom['volumeMounts']},'pvc':{'name':pvc['metadata']['name'],'uid':pvc['metadata']['uid'],'spec':pvc['spec'],'phase':pvc['status']['phase']}}
   time.sleep(.5)
  raise AssertionError('Agent真实ST/Pod/PVC未收敛：'+str(last))
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
  # namespace先删除，保留采集权限直到workload退出；仅删除本例显式登记的资源。
  ordered=[r for r in reversed(self.resources) if r[1]=='namespace']+[r for r in reversed(self.resources) if r[1]!='namespace']
  for target,kind,name in ordered:
   r=self.k('delete',kind,name,'--wait=true','--timeout=120s',target=target,check=False)
   if r.returncode:failures.append(kind+'/'+name+'清理失败')
  self.resources=[]
  if hasattr(self,'ns'):
   if self.k('delete','namespace',self.ns,'--wait=true','--timeout=120s',check=False).returncode:failures.append('平台namespace清理失败')
   del self.ns
  return {'success':not failures,'failures':failures}
 def run(self):
  for index,case in enumerate(self.a.cases or KCASES):
   self.case=case;self.traces=[];self.targetns=None;record={**self.contract[case],'source':self.source,'testedCommit':self.a.source,'testedTree':self.source['tree'],'runnerCommit':self.source['commit'],'profile':self.a.profile,'manifestHash':hashlib.sha256((ROOT/'docs/acceptance/phase1.json').read_bytes()).hexdigest(),'contractFingerprint':fingerprint(self.contract[case]),'testSelector':case,'command':[sys.executable,*sys.argv],'processLocation':'host-driver/platform-pod','hostOS':platform.system(),'hostArch':platform.machine(),'runtimeOS':'linux','runtimeArch':'arm64','environment':self.environment,'productImage':self.image_metadata,'outcome':'failed','startedAt':stamp()}
   try:
    record['platform']=self.deploy(case,index);assert record['platform']['imageID'].endswith(self.image_metadata.get('manifestDigest') or self.image_metadata['dockerInspectId'])
    self.prepare_target(index,'data' if case in ['C04.agent-crd','A05.primary'] else 'control');record['evidence']=run_case(self,case);record['outcome']='passed'
   except Exception as e:
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
    try:record['cleanup']=self.cleanup()
    except Exception as e:record['cleanup']={'success':False,'failures':[str(e)]}
    if not record['cleanup']['success']:record['outcome']='failed'
    record['finishedAt']=stamp();(self.out/(case+'.json')).write_text(json.dumps(record,ensure_ascii=False,indent=2));self.results.append(record);(self.out/'results.json').write_text(json.dumps(self.results,ensure_ascii=False,indent=2));print(case,record['outcome'],flush=True)
   if record['outcome']!='passed' and self.a.fail_fast:break
  return 0 if all(r['outcome']=='passed' for r in self.results) else 1

def main():
 p=argparse.ArgumentParser();p.add_argument('--layer',choices=['Q','K'],default='K');p.add_argument('--profile',choices=['LOCAL_FULL','CI_FAST','CI_SMOKE'],default='LOCAL_FULL');p.add_argument('--list',action='store_true');p.add_argument('--cases',nargs='*');p.add_argument('--output');p.add_argument('--allow-dirty-runner',action='store_true');p.add_argument('--fail-fast',action='store_true')
 for field in ['kubectl','control-config','data-config','control-context','data-context','control-pod-config','data-pod-config','image','source','api-lane']:p.add_argument('--'+field)
 p.add_argument('--prefix',default='mm18-agent');p.add_argument('--port',type=int,default=18700);a=p.parse_args();contract=manifest()
 if a.list:
  print(json.dumps([{'id':v,'layer':contract[v]['layer'],'testSelector':QCASES.get(v,v),'profiles':contract[v]['profiles']} for v in owned()],ensure_ascii=False,indent=2));return 0
 valid=list(QCASES) if a.layer=='Q' else KCASES
 if a.cases and any(v not in valid for v in a.cases):p.error('未知/层级不符子例')
 if any(a.profile not in contract[v]['profiles'] for v in a.cases or valid):p.error('子例不属于profile')
 if not a.output:p.error('必须显式output')
 if a.layer=='Q':
  from ksm_q import run_q
  return run_q(a,QCASES,contract,ROOT,source_state(),stamp)
 for field in ['kubectl','control_config','data_config','control_context','data_context','control_pod_config','data_pod_config','image','source','api_lane']:
  if not getattr(a,field):p.error('K层必须显式'+field)
 if a.profile!='LOCAL_FULL':p.error('本runner当前只执行实际双集群LOCAL_FULL；CI_SMOKE由最终单集群编排')
 # 关键：全局namespace/CRD/SC故障会影响其他Operator实例，锁覆盖准备、整批及finally清理。
 with open(a.api_lane,'a') as lane:
  fcntl.flock(lane,fcntl.LOCK_EX);return Suite(a).run()
if __name__=='__main__':raise SystemExit(main())
