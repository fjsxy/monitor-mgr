#!/usr/bin/env python3
"""#16 KSM验收：真实平台Pod→HTTP→Kitex→目标API；每例独立前置并finally清理。"""
import argparse,copy,datetime,platform,concurrent.futures,traceback,hashlib,http.cookiejar,json,os,pathlib,subprocess,time,urllib.error,urllib.request,uuid
from clusters import Suite as ClusterSuite
from ksm_cases import run_case
from ksm_proxy import Proxy
from ksm_browser import run_browser
ROOT=pathlib.Path(__file__).resolve().parents[2]
KCASES=['I03.primary','C04.ksm-crd','C05.primary','K02.primary','K03.primary','K04.primary','K05.primary','K06.node','K06.cluster','K06.unscheduled','K07.default-all','K07.default-allow','K07.default-deny','K07.include-all','K07.include-allow','K07.include-deny','K08.all','K08.union','K08.dynamic-pattern','K08.zero','K09.primary','P01.primary','P02.primary','P03.same','P03.different-content','P03.another-active','P05.supported-drift','P05.extra-spec','P06.status-only','P06.rv-only','P06.default-equivalent','P06.spec-change','P09.response-drop','P09.deadline']
BCASES=['U03.primary']
QCASES={'K10.name':'TestF03KSMValidationThroughHTTPAndDirectRPC','K10.collision':'TestF03KSMValidationThroughHTTPAndDirectRPC','K10.fixed-target':'TestP05DriftOverwritePreservesMetadataAndP06Equivalent','K10.unsupported':'TestF03KSMValidationThroughHTTPAndDirectRPC','P04.draft-race':'TestK10DraftNameAndRevision','P04.draft-after-preview':'TestP04DraftAndTargetPreviewRaces','P04.target-after-preview':'TestP04DraftAndTargetPreviewRaces','R03.ksm-suspended':'TestR03KSMEffectiveZeroValuesAndMissingStatus/ksm-suspended','R03.ksm-disabled':'TestR03KSMEffectiveZeroValuesAndMissingStatus/ksm-disabled','R03.ksm-no-match':'TestR03KSMEffectiveZeroValuesAndMissingStatus/ksm-no-match','F02.primary':'TestF02KSMHTTPHasRealRPCAndCannotBypassUnavailableRPC','F03.quantity':'TestF03KSMValidationThroughHTTPAndDirectRPC','F03.regexp':'TestF03KSMValidationThroughHTTPAndDirectRPC','F03.unknown-field':'TestF03KSMValidationThroughHTTPAndDirectRPC','F05.before-commit':'TestF05PublicationCrashBoundaries/beforeAcceptanceCommit','F05.after-commit':'TestF05PublicationCrashBoundaries/afterAcceptanceCommit'}
def stamp():return datetime.datetime.now(datetime.timezone.utc).isoformat()
def fingerprint(case):return hashlib.sha256(json.dumps(case,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
def source_state():return {'commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),'tree':subprocess.check_output(['git','rev-parse','HEAD^{tree}'],cwd=ROOT,text=True).strip(),'dirty':bool(subprocess.check_output(['git','status','--porcelain'],cwd=ROOT,text=True).strip())}
def manifest():
 x=json.load(open(ROOT/'docs/acceptance/phase1.json'));return {v['id']:{'variantId':v['id'],'caseId':c['id'],'layer':c['layer'],'purpose':c['purpose'],'steps':v['steps'],'expected':v['expected'],'profiles':c['profiles']} for c in x['cases'] for v in c['variants']}
class Suite(ClusterSuite):
 def __init__(self,a):
  super().__init__(a);self.contract=manifest();self.traces=[];self.source=source_state()
  if self.source['dirty'] and not a.allow_dirty_runner:raise SystemExit('K runner来源必须干净；诊断需明确allow-dirty-runner')
  metadata=json.loads(subprocess.check_output(['docker','image','inspect',a.image],text=True))[0];self.image_metadata={'configID':metadata['Id'],'manifest':metadata.get('Descriptor',{}).get('digest'),'os':metadata['Os'],'architecture':metadata['Architecture'],'source':metadata['Config'].get('Labels',{}).get('org.opencontainers.image.revision')};assert self.image_metadata['source']==a.source,'镜像revision与testedCommit不符'
  self.environment={}
  for target in ['control','data']:
   nodes=json.loads(self.k('get','nodes','-o','json',target=target).stdout)['items'];uid=json.loads(self.k('get','namespace','kube-system','-o','json',target=target).stdout)['metadata']['uid'];self.environment[target]={'uid':uid,'nodes':[{'name':n['metadata']['name'],'nodeInfo':n['status']['nodeInfo'],'taints':n['spec'].get('taints',[])} for n in nodes]};assert all(n['status']['nodeInfo']['kubeletVersion']=='v1.28.15' and n['status']['nodeInfo']['architecture']=='arm64' and n['status']['nodeInfo']['operatingSystem']=='linux' for n in nodes),'真实目标版本/架构不符'

 def call(self,method,path,body=None,key=None,status=200):
  req=urllib.request.Request(self.base+'/api/v1'+path,data=None if body is None else json.dumps(body).encode(),method=method,headers={'Origin':self.base,'Content-Type':'application/json','Idempotency-Key':key or str(uuid.uuid4())})
  try:r=self.http.open(req,timeout=18)
  except urllib.error.HTTPError as e:r=e
  value=json.load(r);self.traces.append({'at':stamp(),'method':method,'path':path,'status':r.code,'requestId':r.headers.get('X-Request-Id',''),'operationId':value.get('operationId','')})
  assert r.code==status,(path,r.code,value.get('code'),value.get('message'));return value
 def cfg(self,name=None,mode='Deployment'):
  out={'name':name or self.crname,'targetNamespace':self.targetns,'deploymentMode':mode,'desiredState':'Running','resources':{'requestCPU':'100m','requestMemory':'250Mi','limitMemory':'500Mi'},'collectors':{'mode':'Default'},'metricFilter':{'mode':'All'},'namespaceFilter':{'mode':'All'},'advancedResources':False}
  if mode=='StatefulSetSharded':out['shards']=2
  return out
 def create(self,cfg=None,cluster=None):return self.call('POST','/clusters/'+(cluster or self.cluster['id'])+'/instances',{'component':'kube-state-metrics','ksm':cfg or self.cfg()},status=201)
 def save(self,i,cfg,rev=None):
  if rev is None:rev=self.call('GET','/instances/'+i['id']+'/draft')['revision']
  return self.call('PUT','/instances/'+i['id']+'/draft',{'expectedDraftRevision':rev,'ksm':cfg})
 def preview(self,i,rev=None):
  if rev is None:rev=self.call('GET','/instances/'+i['id']+'/draft')['revision']
  return self.call('POST','/instances/'+i['id']+'/previews',{'draftRevision':rev})
 def publish(self,i,overwrite=False,preview=None,key=None,wait=True,status=202):
  p=preview or self.preview(i);o=self.call('POST','/instances/'+i['id']+'/publications',{'draftRevision':p['draftRevision'],'previewId':p['previewId'],'overwriteDrift':overwrite},key,status)
  if not wait or status!=202:return o
  done=self.operation(o);assert done['state']=='SUCCEEDED',done;return done
 def cr(self,name=None,target=None):return json.loads(self.k('get','kubestatemetrics',name or self.crname,'-o','json',target=target or self.target).stdout)
 def workloads(self):return json.loads(self.k('-n',self.targetns,'get','deployments,statefulsets,daemonsets,replicasets,pods','-o','json',target=self.target).stdout)['items']
 def runtime(self,i,summary='Available',timeout=100):
  end=time.monotonic()+timeout;last={}
  while time.monotonic()<end:
   last=self.call('POST','/instances/'+i['id']+'/refresh',{})
   if last['summary']==summary:
    if summary!='Available':return last
    objects=self.workloads();controllers=[o for o in objects if o['kind'] in ['Deployment','StatefulSet','DaemonSet']];pods=[o for o in objects if o['kind']=='Pod' and not o['metadata'].get('deletionTimestamp')];settled=len(pods)==last.get('desiredPods') and bool(pods)
    for pod in pods:
     match=next((w for w in controllers if all(pod['metadata']['labels'].get(k)==v for k,v in w['spec']['selector']['matchLabels'].items())),None)
     ready=any(c['type']=='Ready' and c['status']=='True' for c in pod.get('status',{}).get('conditions',[]))
     settled=settled and bool(match) and ready and pod['spec']['containers'][0].get('resources',{})==match['spec']['template']['spec']['containers'][0].get('resources',{}) and pod['spec']['containers'][0].get('args',[])==match['spec']['template']['spec']['containers'][0].get('args',[])
    if settled:return last
   time.sleep(.5)
  raise AssertionError('运行状态未收敛：'+str(last))
 def evidence(self,i):
  cr=self.cr();workloads=self.workloads();pods=[{'name':p['metadata']['name'],'uid':p['metadata']['uid'],'node':p['spec'].get('nodeName'),'images':[{'name':c['name'],'image':c['image'],'imageID':next((s.get('imageID') for s in p['status'].get('containerStatuses',[]) if s['name']==c['name']),None),'resources':c.get('resources',{}),'args':c.get('args',[])} for c in p['spec']['containers']]} for p in workloads if p['kind']=='Pod']
  return {'instance':self.call('GET','/instances/'+i['id']),'cr':cr,'workloads':[o for o in workloads if o['kind']!='Pod'],'pods':pods,'runtime':self.call('GET','/instances/'+i['id']+'/runtime')}
 def prepare_target(self,index,target='control'):
  self.target=target;self.targetns=self.a.prefix+'-target-'+str(index);self.crname=self.a.prefix+'-'+str(index);self.created_crs=[];self.extra_namespaces=[];self.traces=[]
  self.k('create','namespace',self.targetns,target=target);self.cluster,_=self.registered(self.config(target));return self.cluster
 def run_case(self,case):
  if case=='K02.primary':
   i=self.create();o=self.publish(i);r=self.runtime(i);cr=self.cr();assert cr['spec']['deployment']=={'mode':'Deployment'} and 'namespace' not in cr['metadata'];assert cr['spec']['podResources']=={'requests':{'cpu':'100m','memory':'250Mi'},'limits':{'memory':'500Mi'}}
   workloads=self.workloads();dep=[x for x in workloads if x['kind']=='Deployment'];assert len(dep)==1 and dep[0]['spec']['replicas']==1;pods=[x for x in workloads if x['kind']=='Pod'];assert len(pods)==1 and all(c.get('resources')==cr['spec']['podResources'] for p in pods for c in p['spec']['containers']);assert r['desiredPods']==1 and r['availablePods']==1
   return {'operation':o,**self.evidence(i)}
  return run_case(self,case)
 def setup_proxy(self):
  self.proxy=Proxy(self,self.target);done=self.operation(self.accept(self.proxy.config,path='/clusters/'+self.cluster['id']+'/credentials'));assert done['state']=='SUCCEEDED',done;return self.proxy
 def cleanup(self):
  failures=[]
  if hasattr(self,'proxy'):
   try:(self.out/(self.case+'.proxy.jsonl')).write_text(self.proxy.events());self.proxy.close()
   except Exception:failures.append('故障proxy清理失败')
   del self.proxy
  if getattr(self,'crd_restore',None):
   original=self.crd_restore;obj={k:original[k] for k in ['apiVersion','kind','spec']};obj['metadata']={k:v for k,v in original['metadata'].items() if k in ['name','labels','annotations']}
   r=self.k('apply','-f','-',data=obj,target=self.target,check=False)
   if r.returncode:failures.append('CRD恢复失败')
   else:
    r=self.k('wait','--for=condition=Established','crd/'+obj['metadata']['name'],'--timeout=60s',target=self.target,check=False)
    restored=json.loads(self.k('get','crd',obj['metadata']['name'],'-o','json',target=self.target).stdout)
    if r.returncode or restored['spec']!=original['spec']:failures.append('CRD恢复后结构不符')
   self.crd_restore=None
  for kind,name in getattr(self,'rbac_cleanup',[]):
   if self.k('delete',kind,name,target=self.target,check=False).returncode:failures.append('RBAC恢复失败')
  self.rbac_cleanup=[]
  if getattr(self,'other_cleanup',None):
   other=self.other_cleanup;items=json.loads(self.k('get','kubestatemetrics','-o','json',target=other['target']).stdout)['items']
   for cr in items:
    if cr['spec'].get('targetNamespace')==other['namespace'] and cr['metadata']['name'].startswith(self.a.prefix+'-'):
     self.k('delete','kubestatemetrics',cr['metadata']['name'],'--wait=true','--timeout=60s',target=other['target'],check=False)
   if self.k('delete','namespace',other['namespace'],'--wait=true','--timeout=90s',target=other['target'],check=False).returncode:failures.append('其他目标资源清理失败')
   self.other_cleanup=None
  if hasattr(self,'forward'):self.forward.terminate();self.forward.wait(timeout=5)
  if hasattr(self,'targetns'):
   # 关键：读取失败不能冒充空集；无法核对所属CR时不盲删，且cleanup必须失败。
   inventory=self.k('get','kubestatemetrics','-o','json',target=self.target,check=False);items=[]
   if inventory.returncode:failures.append('KSM完整列表读取失败，不能确认cluster-scoped残留')
   else:
    try:items=json.loads(inventory.stdout)['items']
    except (ValueError,KeyError,TypeError):failures.append('KSM列表响应不完整，不能确认cluster-scoped残留')
   for cr in items:
    if cr['metadata']['name'].startswith(self.a.prefix+'-') and cr['spec'].get('targetNamespace')==self.targetns:
     m=copy.deepcopy(cr);m['metadata']['finalizers']=[];self.k('replace','-f','-',data=m,target=self.target,check=False);r=self.k('delete','kubestatemetrics',cr['metadata']['name'],'--wait=true','--timeout=60s',target=self.target,check=False)
     if r.returncode:failures.append('KSM清理失败')
   for ns in [self.targetns]+self.extra_namespaces:
    r=self.k('delete','namespace',ns,'--wait=true','--timeout=90s',target=self.target,check=False)
    if r.returncode:failures.append('目标namespace清理失败')
  if hasattr(self,'ns'):
   logs=self.k('-n',self.ns,'logs','deployment/monitor-mgr',check=False).stdout;(self.out/(self.case+'.platform.log')).write_bytes(logs)
   r=self.k('delete','namespace',self.ns,'--wait=true','--timeout=90s',check=False)
   if r.returncode:failures.append('平台namespace清理失败')
  return {'success':not failures,'failures':failures}
 def run(self):
  for index,case in enumerate(self.a.cases or KCASES):
   self.case=case;self.extra_namespaces=[];self.traces=[];self.cookies=http.cookiejar.CookieJar();record={**self.contract[case],'source':self.source,'testedCommit':self.a.source,'runnerCommit':self.source['commit'],'profile':self.a.profile,'environment':self.environment,'productImage':self.image_metadata,'outcome':'failed','startedAt':stamp()};error=None
   try:
    product_image=self.a.image
    if case=='I03.primary':
     assert self.a.rpc_image,'I03需要只追加RPC探针的测试镜像';self.a.image=self.a.rpc_image
    record['platform']=self.deploy(case,index);self.a.image=product_image;record['platform']['baseProductImage']=product_image
    if case in BCASES:record['evidence']=run_browser(self)
    else:
     self.prepare_target(index,'data' if case in ['C04.ksm-crd','C05.primary'] else 'control');record['evidence']=self.run_case(case)
    record['outcome']='passed'
   except Exception as e:
    record['error']=str(e);record['traceback']=traceback.format_exc();error=e
    if hasattr(self,'targetns'):
     try:
      record['failureObjects']={'crs':json.loads(self.k('get','kubestatemetrics','-o','json',target=self.target).stdout),'workloads':self.workloads(),'events':json.loads(self.k('-n',self.targetns,'get','events','-o','json',target=self.target).stdout)}
     except Exception:record['failureObjectsError']='目标对象读取失败，不能据此推断为空'

   finally:
    record['traces']=self.traces
    try:record['cleanup']=self.cleanup()
    except Exception as cleanup_error:record['cleanup']={'success':False,'failures':[str(cleanup_error)]}
    record['finishedAt']=stamp()
    if not record['cleanup']['success']:record['outcome']='failed'
    (self.out/(case+'.json')).write_text(json.dumps(record,ensure_ascii=False,indent=2));self.results.append(record);(self.out/'results.json').write_text(json.dumps(self.results,ensure_ascii=False,indent=2));print(case,record['outcome'],flush=True)
   if error:raise error
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--kubectl');p.add_argument('--control-config');p.add_argument('--data-config');p.add_argument('--control-pod-config');p.add_argument('--data-pod-config');p.add_argument('--image');p.add_argument('--rpc-image');p.add_argument('--source');p.add_argument('--output');p.add_argument('--prefix',default='mm-phase1-ksm-k');p.add_argument('--port',type=int,default=18500);p.add_argument('--workers',type=int,choices=range(1,5),default=1);p.add_argument('--layer',choices=['Q','K','B'],default='K');p.add_argument('--profile',choices=['LOCAL_FULL','CI_FAST','CI_SMOKE'],default='LOCAL_FULL');p.add_argument('--allow-dirty-runner',action='store_true');p.add_argument('--list',action='store_true');p.add_argument('--cases',nargs='*');a=p.parse_args()
 if a.list:
  contracts=manifest();print(json.dumps([{'id':id,'layer':contracts[id]['layer'],'testSelector':QCASES.get(id,id),'profiles':contracts[id]['profiles']} for id in list(QCASES)+KCASES+['U03.primary']],ensure_ascii=False,indent=2));raise SystemExit(0)
 if not a.output:p.error('必须显式output')
 valid=list(QCASES) if a.layer=='Q' else (BCASES if a.layer=='B' else KCASES)
 if a.cases and any(c not in valid for c in a.cases):p.error('未知或层级不符的子例')
 if any(a.profile not in manifest()[c]['profiles'] for c in (a.cases or valid)):p.error('子例不属于所选profile')
 if a.layer=='Q':
  from ksm_q import run_q
  raise SystemExit(run_q(a,QCASES,manifest(),ROOT,source_state(),stamp))
 for field in ['kubectl','control_config','data_config','control_pod_config','data_pod_config','image','source']:
  if not getattr(a,field):p.error('K层必须显式'+field)
 if a.profile!='LOCAL_FULL':p.error('本票K runner当前为双目标LOCAL_FULL；CI_SMOKE由最终CI单集群编排执行')
 if a.layer=='B':a.cases=a.cases or BCASES
 if a.workers==1 or a.layer=='B':Suite(a).run()
 else:
  rootout=pathlib.Path(a.output);rootout.mkdir(parents=True,exist_ok=True);cases=a.cases or KCASES;parallel=[c for c in cases if c!='C04.ksm-crd'];results=[]
  def one(item):
   index,case=item;b=copy.copy(a);b.cases=[case];b.prefix=a.prefix+'-c'+str(index);b.port=a.port+index;b.output=str(rootout/case)
   try:Suite(b).run()
   except Exception as e:
    if not (pathlib.Path(b.output)/(case+'.json')).exists():(pathlib.Path(b.output)/(case+'.json')).write_text(json.dumps({'variantId':case,'outcome':'environment_failed','error':str(e),'testedCommit':a.source,'cleanup':{'success':False}},ensure_ascii=False))
   return json.load(open(pathlib.Path(b.output)/(case+'.json')))
  with concurrent.futures.ThreadPoolExecutor(max_workers=a.workers) as pool:results=list(pool.map(one,enumerate(parallel)))
  # CRD是全局故障；全部普通fixture完成并清理后，独占执行并finally恢复。
  if 'C04.ksm-crd' in cases:results.append(one((len(parallel),'C04.ksm-crd')))
  (rootout/'results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2));failed=[x['variantId'] for x in results if x['outcome']!='passed' or not x.get('cleanup',{}).get('success')];print(json.dumps({'total':len(results),'failed':failed},ensure_ascii=False));raise SystemExit(1 if failed else 0)

