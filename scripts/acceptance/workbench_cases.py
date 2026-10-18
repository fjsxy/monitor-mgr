"""#19独占真实Pod工作台验收；每例独立资源，读模型必须来自实际目标。"""
import copy,datetime,hashlib,http.cookiejar,json,os,pathlib,platform,secrets,subprocess,sys,time,traceback,urllib.request,uuid
from agent import Suite as AgentSuite
from ksm_proxy import Proxy
from workbench import registry,fingerprint,stamp
ROOT=pathlib.Path(__file__).resolve().parents[2]

class Suite(AgentSuite):
 def __init__(self,a):
  super().__init__(a);self.contract=registry();self.instances=[]
 def register(self,target,config=None):
  done=self.operation(self.accept(config or self.config(target),self.owner,name=self.a.prefix+'-'+target));assert done['state']=='SUCCEEDED',done
  return self.call('GET','/clusters/'+done['clusterId'])
 def observe(self,i):return self.call('GET','/instances/'+i['id'])
 def current(self,i):return {v['source']:v for v in self.observe(i).get('sources',[])}
 def wait(self,read,predicate,description,seconds=65):
  deadline=time.monotonic()+seconds;last=None
  while time.monotonic()<deadline:
   last=read()
   if predicate(last):return last
   time.sleep(.4)
  raise AssertionError(description+'未在预算内出现；最后安全读模型='+json.dumps(last,ensure_ascii=False))
 def ksm_cfg(self,name,namespace,mode='Deployment'):
  cfg={'name':name,'targetNamespace':namespace,'deploymentMode':mode,'desiredState':'Running','resources':{'requestCPU':'100m','requestMemory':'250Mi','limitMemory':'500Mi'},'collectors':{'mode':'Default'},'metricFilter':{'mode':'All'},'namespaceFilter':{'mode':'Include','names':[namespace]},'advancedResources':False}
  if mode=='StatefulSetSharded':cfg['shards']=2
  return cfg
 def new_ksm(self,index,target='control',cluster=None,mode='Deployment',namespace=None):
  ns=namespace or self.a.prefix+'-t'+str(index)+'-'+target
  if (target,ns) not in self.namespace_uids:self.namespace(ns,target)
  cluster=cluster or self.register(target);name=self.a.prefix+'-ksm-'+str(index)+'-'+target
  i=self.call('POST','/clusters/'+cluster['id']+'/instances',{'component':'kube-state-metrics','ksm':self.ksm_cfg(name,ns,mode)},status=201)
  p=self.call('POST','/instances/'+i['id']+'/previews',{'draftRevision':1})
  op=self.call('POST','/instances/'+i['id']+'/publications',{'draftRevision':p['draftRevision'],'previewId':p['previewId'],'overwriteDrift':False},status=202)
  done=self.operation(op);assert done['state']=='SUCCEEDED',done
  cr=self.get('kubestatemetrics',name,target=target);self.ksms.append((target,name,cr['metadata']['uid']))
  data={'instance':i,'cluster':cluster,'target':target,'namespace':ns,'name':name,'uid':cr['metadata']['uid'],'versionId':done['versionId']};self.instances.append(data)
  self.wait(lambda:self.call('POST','/instances/'+i['id']+'/refresh',{}),lambda r:r.get('summary')=='Available' and r.get('desiredPods')==r.get('availablePods')==(2 if mode=='StatefulSetSharded' else 1),'真实KSM Ready',150)
  data['runtimeProof']=self.ksm_proof(data);return data
 def ksm_proof(self,item):
  target=item['target'];ns=item['namespace'];cr=self.get('kubestatemetrics',item['name'],target=target)
  assert cr['metadata']['uid']==item['uid']
  pods=[p for p in self.get('pods','-n',ns,target=target)['items'] if not p['metadata'].get('deletionTimestamp')]
  expected='sha256:2de1b6dacbf8f41a4f90ef74c487ad49c50bfcfe8d5dbf70b58b4968ffcb0076';proof=[]
  for pod in pods:
   container=next(c for c in pod['spec']['containers'] if 'kube-state-metrics' in c['image']);status=next(c for c in pod['status']['containerStatuses'] if c['name']==container['name'])
   node=pod['spec']['nodeName'];assert node in {n['name'] for n in self.environment[target]['nodes']}
   raw=subprocess.check_output(['docker','exec',node,'ctr','-n','k8s.io','content','get',expected],stderr=subprocess.PIPE);assert 'sha256:'+hashlib.sha256(raw).hexdigest()==expected
   manifest=json.loads(raw);config_id=manifest['config']['digest'];config_raw=subprocess.check_output(['docker','exec',node,'ctr','-n','k8s.io','content','get',config_id],stderr=subprocess.PIPE)
   assert 'sha256:'+hashlib.sha256(config_raw).hexdigest()==config_id;config=json.loads(config_raw);assert config['os']=='linux' and config['architecture']=='arm64'
   cid=status['containerID'].removeprefix('containerd://');actual=json.loads(subprocess.check_output(['docker','exec',node,'crictl','inspect',cid],stderr=subprocess.PIPE))
   assert actual['status']['labels']['io.kubernetes.pod.uid']==pod['metadata']['uid'] and actual['info']['config']['image']['image']==config_id
   proof.append({'podUID':pod['metadata']['uid'],'name':pod['metadata']['name'],'containerID':cid,'node':node,'image':container['image'],'imageID':status['imageID'],'runtimeManifestDigest':expected,'configDigest':config_id,'os':'linux','architecture':'arm64'})
  assert proof;return {'crUID':item['uid'],'generation':cr['metadata']['generation'],'status':cr.get('status',{}),'pods':proof}
 def use_proxy(self,target='control'):
  proxy=Proxy(self,target);self.restorers.append(proxy.close);self.proxies.append(proxy);return proxy
 def faults(self,proxy,mode='none',write=''):
  return proxy.control('faults',{'readMode':mode,'rejectWritePath':write})
 def cleanup(self):
  failures=[]
  for restore in reversed(self.restorers):
   try:restore()
   except Exception:failures.append('本例故障恢复失败')
  self.restorers=[];results=[]
  # 关键：测试清理也绑定原UID；检查与删除之间即使同名重建，API precondition仍禁止误删。
  for target,name,uid in self.ksms:
   result={'target':target,'name':name,'uid':uid};results.append(result)
   try:
    actual=self.get('kubestatemetrics',name,target=target);assert actual['metadata']['uid']==uid
    self.k('delete','--raw','/apis/'+actual['apiVersion']+'/kubestatemetrics/'+name,'-f','-',data={'apiVersion':'v1','kind':'DeleteOptions','preconditions':{'uid':uid},'propagationPolicy':'Foreground'},target=target)
    deadline=time.monotonic()+180
    while time.monotonic()<deadline:
     current=self.k('get','kubestatemetrics',name,'-o','json',target=target,check=False)
     if current.returncode:
      assert b'(NotFound)' in current.stderr,'清理读取失败不能当作NotFound';result['notFoundAt']=stamp();break
     assert json.loads(current.stdout)['metadata']['uid']==uid,'原资源被替代，停止清理'
     time.sleep(.4)
    assert result.get('notFoundAt'),'KSM正常Foreground未结束'
   except Exception:failures.append('原UID KSM正常清理失败')
  self.ksms=[];out=super().cleanup();out['ksmResults']=results;out['failures']+=failures;out['success']=not out['failures'];return out
 def r02(self,index):
  proxy=self.use_proxy();first=self.register('control',proxy.config);second=self.register('data')
  left=self.new_ksm(index,'control',first);right=self.new_ksm(index,'data',second)
  a,b=left['instance'],right['instance'];before={'left':self.current(a)['CR'],'right':self.current(b)['CR']}
  self.faults(proxy,'all')
  failed=self.wait(lambda:self.observe(a),lambda i:i['runtime']['summary']=='Unknown' and any(s['source']=='CR' and s['error'] and s['historical'] for s in i['sources']),'故障目标后台读取失败',45)
  unaffected=self.wait(lambda:self.current(b)['CR'],lambda s:s['lastSuccessAt']>before['right']['lastSuccessAt'] and not s['historical'],'另一目标后台继续成功',30)
  source=next(s for s in failed['sources'] if s['source']=='CR');assert source['lastSuccessAt']>=before['left']['lastSuccessAt'] and source['runtimeSnapshot']['summary']=='Available'
  assert urllib.request.urlopen(self.base+'/readyz',timeout=2).status==200
  self.faults(proxy)
  recovered=self.wait(lambda:self.observe(a),lambda i:i['runtime']['summary']=='Available' and any(s['source']=='CR' and not s['historical'] and s['lastSuccessAt']>source['lastSuccessAt'] for s in i['sources']),'故障恢复重新核对身份',45)
  assert recovered['uid']==left['uid'];return {'before':before,'failed':failed,'unaffected':unaffected,'recovered':recovered,'actualTargets':[left,right],'readyDuringFault':True}
 def r04(self,index):
  item=self.new_ksm(index,mode='StatefulSetSharded');ns=item['namespace'];i=item['instance']
  pods=self.get('pods','-n',ns)['items'];assert len(pods)==2;pod=pods[-1];name=pod['metadata']['name'];uid=pod['metadata']['uid'];container=pod['spec']['containers'][0];original=container['image']
  def patch(image):
   actual=self.get('pod',name,'-n',ns);assert actual['metadata']['uid']==uid
   self.k('patch','pod',name,'-n',ns,'--type=json','-p',json.dumps([{'op':'test','path':'/metadata/uid','value':uid},{'op':'test','path':'/metadata/resourceVersion','value':actual['metadata']['resourceVersion']},{'op':'replace','path':'/spec/containers/0/image','value':image}]))
  self.restorers.append(lambda:patch(original));patch('python:3.13.2-alpine')
  runtime=self.wait(lambda:self.call('POST','/instances/'+i['id']+'/refresh',{}),lambda r:r['summary']=='NotReady' and r.get('availablePods')==1 and r.get('desiredPods')==2,'实际部分副本未就绪',90)
  actual=self.get('pod',name,'-n',ns);assert actual['metadata']['uid']==uid and not any(c['type']=='Ready' and c['status']=='True' for c in actual.get('status',{}).get('conditions',[]))
  reasons=[c.get('state',{}) for c in actual['status'].get('containerStatuses',[])];assert runtime['error'] or any('waiting' in s or 'terminated' in s for s in reasons)
  query=self.call('GET','/clusters/'+item['cluster']['id']+'/instances?page=1&pageSize=100&summary=NotReady');assert any(v['id']==i['id'] for v in query['instances'])
  patch(original);self.restorers.pop();self.wait(lambda:self.call('POST','/instances/'+i['id']+'/refresh',{}),lambda r:r['summary']=='Available' and r.get('availablePods')==2,'故障原Pod恢复',120)
  return {'runtime':runtime,'podUID':uid,'podConditions':actual.get('status',{}).get('conditions',[]),'containerFault':reasons,'query':query,'actualReadyBefore':item['runtimeProof'],'restoredOriginalImage':original}
 def reader(self,item):
  ns=item['namespace'];name=ns+'-reader';self.apply({'apiVersion':'v1','kind':'ServiceAccount','metadata':{'name':'reader','namespace':ns}})
  rules=[{'apiGroups':[''],'resources':['namespaces','nodes','pods'],'verbs':['get','list','watch']},{'apiGroups':['apps'],'resources':['deployments','replicasets','statefulsets','daemonsets'],'verbs':['get','list','watch']},{'apiGroups':['monitoring.example.io'],'resources':['kubestatemetrics'],'verbs':['get','list','watch']},{'apiGroups':['monitoring.coreos.com'],'resources':['prometheusagents'],'verbs':['get','list','watch']},{'apiGroups':['authentication.k8s.io'],'resources':['selfsubjectreviews'],'verbs':['create']},{'apiGroups':['authorization.k8s.io'],'resources':['selfsubjectaccessreviews','subjectaccessreviews'],'verbs':['create']},{'nonResourceURLs':['/version'],'verbs':['get']}]
  # 固定KSM实际API group取自真实CR，避免用资源名称猜测上游路径。
  cr=self.get('kubestatemetrics',item['name']);rules[2]['apiGroups']=[cr['apiVersion'].split('/')[0]]
  role={'apiVersion':'rbac.authorization.k8s.io/v1','kind':'ClusterRole','metadata':{'name':name},'rules':rules};self.apply(role);self.resources.append(('control','clusterrole',name))
  self.apply({'apiVersion':'rbac.authorization.k8s.io/v1','kind':'ClusterRoleBinding','metadata':{'name':name},'roleRef':{'apiGroup':'rbac.authorization.k8s.io','kind':'ClusterRole','name':name},'subjects':[{'kind':'ServiceAccount','name':'reader','namespace':ns}]});self.resources.append(('control','clusterrolebinding',name))
  token=self.k('-n',ns,'create','token','reader','--duration=1h').stdout.decode().strip();cfg=self.config();cfg['users']=[{'name':'reader','user':{'token':token}}];cfg['contexts'][0]['context']['user']='reader'
  done=self.operation(self.accept(cfg,path='/clusters/'+item['cluster']['id']+'/credentials'));assert done['state']=='SUCCEEDED',done
  return role
 def r07(self,index):
  item=self.new_ksm(index);i=item['instance'];role=self.reader(item)
  self.call('POST','/instances/'+i['id']+'/refresh',{});before=self.current(i);assert before['Pod']['matches'] and not before['Pod']['historical']
  actual=self.get('clusterrole',role['metadata']['name']);original=copy.deepcopy(actual['rules']);changed=copy.deepcopy(original)
  changed[0]['resources'].remove('pods')
  def policy(rules):
   obj=self.get('clusterrole',role['metadata']['name']);assert obj['metadata']['uid']==actual['metadata']['uid'];obj['rules']=rules;self.k('replace','-f','-',data=obj)
  self.restorers.append(lambda:policy(original));policy(changed)
  failed=self.wait(lambda:self.observe(i),lambda v:any(s['source']=='Pod' and s['error'] and s['historical'] for s in v['sources']) and v['runtime']['summary']=='Available','仅Pod权限拒绝',45)
  sources={s['source']:s for s in failed['sources']};assert sources['Pod']['lastSuccessAt']>=before['Pod']['lastSuccessAt'] and sources['Pod']['matches']==before['Pod']['matches'] and not sources['CR']['historical']
  again=self.wait(lambda:self.current(i),lambda s:s['CR']['lastSuccessAt']>sources['CR']['lastSuccessAt'] and s['Pod']['lastAttemptAt']>sources['Pod']['lastAttemptAt'],'CR不能为Pod失败续期',30)
  assert again['Pod']['lastSuccessAt']==sources['Pod']['lastSuccessAt'] and again['Pod']['matches']==sources['Pod']['matches']
  policy(original);self.restorers.pop();restored=self.wait(lambda:self.current(i),lambda s:not s['Pod']['historical'] and s['Pod']['lastSuccessAt']>before['Pod']['lastSuccessAt'],'Pod权限恢复',45)
  assert restored['Pod']['uid']==item['uid'];return {'before':before,'failed':failed,'restored':restored,'actualKSM':item['runtimeProof'],'readerIdentity':'system:serviceaccount:'+item['namespace']+':reader','onlyPodPermissionRemoved':True}
 def restart(self):
  cookies=self.cookies;old=self.pod;old_uid=self.get('pod',old,'-n',self.ns,target='control')['metadata']['uid'];self.forward.terminate();self.forward.wait(timeout=5);del self.forward
  self.k('-n',self.ns,'patch','deployment','monitor-mgr','--type=merge','-p',json.dumps({'spec':{'template':{'metadata':{'annotations':{'workbench-restart':uuid.uuid4().hex}}}}}))
  self.k('-n',self.ns,'rollout','status','deployment/monitor-mgr','--timeout=100s')
  pods=self.get('pods','-n',self.ns,'-l','app=monitor-mgr',target='control')['items'];pods=[p for p in pods if not p['metadata'].get('deletionTimestamp')];assert len(pods)==1 and pods[0]['metadata']['uid']!=old_uid
  self.pod=pods[0]['metadata']['name'];self.connect();self.cookies=cookies;self.http=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookies));return {'originalPodUID':old_uid,'newPodUID':pods[0]['metadata']['uid']}
 def r08(self,index):
  proxy=self.use_proxy();cluster=self.register('control',proxy.config);item=self.new_ksm(index,cluster=cluster);i=item['instance'];before=self.current(i)['CR'];session=self.call('GET','/session');key=self.proof('key-before')
  self.call('POST','/instances/'+i['id']+'/refresh',{});self.faults(proxy,'all');before=self.current(i)['CR'];rebuild=self.restart();unknown=self.observe(i);source=next(s for s in unknown['sources'] if s['source']=='CR')
  assert unknown['runtime']['summary']=='Unknown' and source['historical'] and source['runtimeSnapshot']['summary']=='Available' and source['lastSuccessAt']==before['lastSuccessAt']
  age=(datetime.datetime.now(datetime.timezone.utc)-datetime.datetime.fromisoformat(source['lastSuccessAt'].replace('Z','+00:00'))).total_seconds();assert age<=30,'历史已自然过期，不能证明首次读取前epoch独立未知'
  assert self.call('GET','/session')['expiresAt']==session['expiresAt'];afterkey=self.proof('key-after');assert key==afterkey
  self.faults(proxy);recovered=self.wait(lambda:self.observe(i),lambda v:v['runtime']['summary']=='Available' and any(s['source']=='CR' and not s['historical'] for s in v['sources']),'重启首次成功',45)
  assert recovered['uid']==item['uid'];return {**rebuild,'before':before,'firstReadUnknown':unknown,'recovered':recovered,'sameKeyAndDatabase':key==afterkey,'sessionExpiryPreserved':True,'keyProof':key,'actualKSM':item['runtimeProof']}
 def run(self):
  for index,case in enumerate(self.a.cases):
   self.case=case;self.traces=[];self.instances=[];self.proxies=[];self.targetns=None;self.cookies=http.cookiejar.CookieJar()
   record={**self.contract[case],'source':self.source,'testedCommit':self.a.source,'testedTree':self.bridge['productTree'] if self.bridge else self.source['tree'],'runnerCommit':self.source['commit'],'sourceBridge':self.bridge,'profile':self.a.profile,'manifestHash':hashlib.sha256((ROOT/'docs/acceptance/phase1.json').read_bytes()).hexdigest(),'contractFingerprint':fingerprint(self.contract[case]),'testSelector':case,'command':[sys.executable,*sys.argv],'processLocation':'host-driver/platform-pod','hostOS':platform.system(),'hostArch':platform.machine(),'runtimeOS':'linux','runtimeArch':'arm64','environment':self.environment,'productImage':self.image_metadata,'outcome':'failed','startedAt':stamp()}
   try:
    record['platform']=self.deploy(case,index);assert record['platform']['imageID'].endswith(self.image_metadata.get('manifestDigest') or self.image_metadata['dockerInspectId'])
    if self.a.layer=='B':
     from workbench_browser import run_browser
     record['evidence']=run_browser(self,case,index)
    else:record['evidence']={'R02.primary':self.r02,'R04.primary':self.r04,'R07.pod':self.r07,'R08.primary':self.r08}[case](index)
    record['outcome']='passed'
   except Exception as error:
    record['error']=str(error);record['traceback']=traceback.format_exc()
    try:record['platformLogs']=self.k('logs','pod/'+self.pod,'-n',self.ns,'--tail=150').stdout.decode()
    except Exception:record['platformLogsError']='平台日志不可读'
   finally:
    # 关键：故障控制先复原，原始事件仅含白名单；读失败绝不能当作无cluster-scoped对象。
    for number,proxy in enumerate(self.proxies):
     try:(self.out/(case+'.proxy-'+str(number)+'.jsonl')).write_text(proxy.events())
     except Exception:record.setdefault('evidenceErrors',[]).append('proxy事件不可读')
    for target,kind,namespace in self.resources:
     if kind!='namespace' or namespace==getattr(self,'ns',None):continue
     try:
      for cr in self.get('kubestatemetrics',target=target)['items']:
       if cr['spec'].get('targetNamespace')==namespace and cr['metadata']['name'].startswith(self.a.prefix+'-') and (target,cr['metadata']['name'],cr['metadata']['uid']) not in self.ksms:self.ksms.append((target,cr['metadata']['name'],cr['metadata']['uid']))
     except Exception:record.setdefault('evidenceErrors',[]).append('KSM清理归属列表不可读')
    try:record['cleanup']=self.cleanup()
    except Exception as error:record['cleanup']={'success':False,'failures':[str(error)]}
    if record.get('evidenceErrors') or not record['cleanup']['success']:record['outcome']='failed'
    trace=self.out/(case+'.http.jsonl');trace.write_text(''.join(json.dumps(t,ensure_ascii=False)+'\n' for t in self.traces))
    record['evidenceRefs']=[{'path':str(p.relative_to(self.out)),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in sorted(self.out.rglob('*')) if p.is_file() and (p.name.startswith(case+'.') and p.name!=case+'.json' or 'browser' in p.parts)]
    record['finishedAt']=stamp();(self.out/(case+'.json')).write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n');self.results.append(record);(self.out/'results.json').write_text(json.dumps(self.results,ensure_ascii=False,indent=2)+'\n');print(case,record['outcome'],flush=True)
   if not record['cleanup']['success'] or self.a.fail_fast and record['outcome']!='passed':break
  return 0 if all(r['outcome']=='passed' for r in self.results) else 1
