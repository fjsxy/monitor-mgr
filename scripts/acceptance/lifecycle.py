#!/usr/bin/env python3
"""#17：独占平台Pod和真实目标API；原UID、可能已发边界与清理图分别取证。"""
import argparse,contextlib,copy,fcntl,hashlib,http.cookiejar,json,os,pathlib,platform,secrets,subprocess,sys,time,traceback,urllib.error,urllib.request,uuid
from ksm import Suite as KSMSuite,manifest,source_state,stamp,fingerprint,ROOT
from ksm_q import run_q
KCASES=['T05.primary','T08.primary','I04.primary','I05.primary','C06.same-uid','C07.primary','K01.primary','P07.primary','P10.primary','P11.primary','P12.new-uid','P12.missing','P13.unsent','P13.possibly-sent','P14.finalizer','P14.pod-remains','P14.read-failure','P14.delete-response-drop']
QCASES={'I06.primary':'TestI06CookieExpiryDoesNotCancelAcceptedOperation','P08.cancel-wins':'TestP08CancellationCompetesWithPersistentDispatch/cancel-wins','P08.dispatch-wins':'TestP08CancellationCompetesWithPersistentDispatch/dispatch-wins','R03.deleted':'TestP14ForegroundDeletionKeepsUIDGraphProtection/complete','F04.success':'TestF04LifecycleSchema5Upgrade/success','F04.failure':'TestF04LifecycleSchema5Upgrade/failure'}
FIXTURE_CASES={'T08.primary','I05.primary','P07.primary','P13.unsent','P13.possibly-sent'}
class Suite(KSMSuite):
 def cfg(self,name=None,mode='Deployment'):
  c=super().cfg(name,mode);c['namespaceFilter']={'mode':'Include','names':[self.targetns]};return c
 def state(self,o,*states,timeout=30):
  until=time.monotonic()+timeout;last={}
  while time.monotonic()<until:
   last=self.call('GET','/operations/'+o['operationId'])
   if last['state'] in states:return last
   time.sleep(.08)
  raise AssertionError('生命周期未收敛 '+str(last))
 def request_revision(self,o):
  op=self.call('GET','/operations/'+o['operationId']);attempts=self.call('GET','/operations/'+o['operationId']+'/attempts')['attempts'];a=next(x for x in attempts if x['attemptId']==op['attemptId']);return {'expectedOperationRevision':op['revision'],'expectedAttemptId':a['attemptId'],'expectedAttemptRevision':a['revision']}
 def action(self,o,action,status=202):return self.call('POST','/operations/'+o['operationId']+'/'+action,self.request_revision(o),status=status)
 def delete(self,i,wait=True):
  preview=self.call('POST','/instances/'+i['id']+'/deletion-previews',{});o=self.call('POST','/instances/'+i['id']+'/deletions',{'versionId':preview['versionId'],'previewId':preview['previewId']},status=202)
  return self.state(o,'SUCCEEDED',timeout=100) if wait else o
 def first(self):
  i=self.create();o=self.publish(i);self.runtime(i);return i,o
 def gate(self,action,body=None):
  req=urllib.request.Request(self.gatebase+'/'+action,data=None if body is None else json.dumps(body).encode(),method='GET' if action=='state' else 'POST',headers={'Content-Type':'application/json'});return json.load(urllib.request.urlopen(req,timeout=4))
 def barrier(self,stage):
  until=time.monotonic()+10
  while time.monotonic()<until:
   g=self.gate('state').get(stage,{})
   if g.get('arrived'):return g
   time.sleep(.05)
  raise AssertionError('应用持久屏障未命中 '+stage)
 def connect_gate(self):
  self.gateport=self.port+400;self.gatebase='http://127.0.0.1:'+str(self.gateport);self.gateforward=subprocess.Popen([self.a.kubectl,'--kubeconfig',self.a.control_config,'-n',self.ns,'port-forward','pod/'+self.pod,str(self.gateport)+':19091'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
  until=time.monotonic()+10
  while time.monotonic()<until:
   try:self.gate('state');return
   except Exception:time.sleep(.1)
  raise AssertionError('独占应用fixture入口不可达')
 def stop_forward(self):
  for attr in ['forward','gateforward']:
   pf=getattr(self,attr,None)
   if pf:pf.terminate();pf.wait(timeout=5);delattr(self,attr)
 def rebuild(self,force=False):
  old=self.k('-n',self.ns,'logs','pod/'+self.pod,check=False).stdout;(self.out/(self.case+'.before-rebuild.log')).write_bytes(old)
  self.stop_forward()
  self.k('-n',self.ns,'scale','deployment/monitor-mgr','--replicas=0')
  if force:self.k('-n',self.ns,'delete','pod',self.pod,'--grace-period=0','--force',check=False)
  self.k('-n',self.ns,'wait','--for=delete','pods','-l','app=monitor-mgr','--timeout=50s')
  self.k('-n',self.ns,'scale','deployment/monitor-mgr','--replicas=1');self.k('-n',self.ns,'rollout','status','deployment/monitor-mgr','--timeout=100s')
  pod=json.loads(self.k('-n',self.ns,'get','pods','-l','app=monitor-mgr','-o','json').stdout)['items'][0];self.pod=pod['metadata']['name'];self.connect()
  if self.case in FIXTURE_CASES:self.connect_gate()
  assert pod['metadata']['uid']!=self.initial_uid;return {'oldPodUID':self.initial_uid,'newPodUID':pod['metadata']['uid'],'imageID':pod['status']['containerStatuses'][0]['imageID']}
 def capture_proxy(self):return [json.loads(x) for x in self.proxy.events().splitlines() if x.strip()]
 def mutations(self,events,method=None):return [x for x in events if x['stage']=='upstream_completed' and x['method'] in ([method] if method else ['POST','PUT','PATCH','DELETE']) and 'kubestatemetrics' in x['path']]
 def cr_absent(self,name=None):return self.k('get','kubestatemetrics',name or self.crname,target=self.target,check=False).returncode!=0
 @contextlib.contextmanager
 def using(self,opener):
  original=self.http;self.http=opener
  try:yield
  finally:self.http=original
 def user(self,suffix):
  name=self.a.prefix+'-'+suffix;temporary=secrets.token_urlsafe(20);password=secrets.token_urlsafe(20)
  account=self.call('POST','/accounts',{'username':name,'password':temporary,'role':'USER'},status=201)
  jar=http.cookiejar.CookieJar();opener=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
  with self.using(opener):
   self.call('POST','/sessions',{'username':name,'password':temporary},status=201);self.call('POST','/session/password',{'currentPassword':temporary,'newPassword':password});self.call('POST','/sessions',{'username':name,'password':password},status=201)
  return account,opener
 def restricted(self):
  name=self.a.prefix+'-restricted';self.rbac_cleanup=[('clusterrolebinding',name),('clusterrole',name)]
  self.k('-n',self.ns,'create','serviceaccount',name,target=self.target)
  role={'apiVersion':'rbac.authorization.k8s.io/v1','kind':'ClusterRole','metadata':{'name':name},'rules':[{'apiGroups':[''],'resources':['namespaces','pods'],'verbs':['get','list']},{'apiGroups':['monitoring.fjsxy.github.io'],'resources':['kubestatemetrics'],'verbs':['get','list']},{'apiGroups':['monitoring.coreos.com'],'resources':['prometheusagents'],'verbs':['get','list']},{'apiGroups':['apps'],'resources':['deployments','statefulsets','daemonsets','replicasets'],'verbs':['get','list']},{'apiGroups':['authentication.k8s.io'],'resources':['selfsubjectreviews'],'verbs':['create']},{'apiGroups':['authorization.k8s.io'],'resources':['selfsubjectaccessreviews'],'verbs':['create']},{'nonResourceURLs':['/version'],'verbs':['get']}]}
  self.k('apply','-f','-',data=role,target=self.target);self.k('create','clusterrolebinding',name,'--clusterrole='+name,'--serviceaccount='+self.ns+':'+name,target=self.target)
  token=self.k('-n',self.ns,'create','token',name,'--duration=1h',target=self.target).stdout.decode().strip();cfg=self.config(self.target);cfg['users']=[{'name':'restricted','user':{'token':token}}];cfg['contexts'][0]['context']['user']='restricted';return cfg
 def browser_retry(self,i,o):
  out=self.out/(self.case+'.browser');out.mkdir(parents=True,exist_ok=True)
  env=dict(os.environ,BASE_URL=self.base,ADMIN_PASSWORD=self.password,LIFECYCLE_UI_OPERATION=o['operationId'],LIFECYCLE_UI_INSTANCE=i['id'],LIFECYCLE_UI_VERSION=o['versionId'],LIFECYCLE_UI_NAME=i['name'])
  command=['npm','--prefix','web','run','test:e2e','--','lifecycle.spec.ts','--reporter=json','--output='+str(out)]
  result=subprocess.run(command,cwd=ROOT,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=80)
  raw=result.stdout.decode();start=raw.find('{\n  "config"');assert start>=0,'浏览器未产生JSON报告'
  report=json.loads(raw[start:]);clean=json.dumps(report,ensure_ascii=False).replace(self.password,'[REDACTED]');(out/'results.json').write_text(clean);(out/'stderr.log').write_text(result.stderr.decode().replace(self.password,'[REDACTED]'))
  stats=report['stats'];assert result.returncode==0 and stats['expected']==1 and not stats['unexpected'] and not stats['skipped'],stats
  screenshots=list(out.rglob('lifecycle-original-version-retry.png'));assert len(screenshots)==1
  return {'command':command,'exitCode':result.returncode,'stats':stats,'screenshot':str(screenshots[0].relative_to(self.out))}
 def run_case(self,case):
  if case=='T05.primary':
   i,o=self.first();draft=self.call('GET','/instances/'+i['id']+'/draft');session=self.call('GET','/session');proof=self.proof('before-rebuild');rebuild=self.rebuild();after=self.proof('after-rebuild');assert proof==after and after['keyMode']=='0o600' and after['integrity']=='ok';assert self.call('GET','/session')['expiresAt']==session['expiresAt'];assert self.call('GET','/instances/'+i['id']+'/draft')==draft;assert self.call('GET','/operations/'+o['operationId'])['state']=='SUCCEEDED';versions=self.call('GET','/instances/'+i['id']+'/versions')['versions'];assert len(versions)==1;self.save(i,self.cfg());return {'rebuild':rebuild,'persistentProof':after,'sessionPreserved':True,'versionIds':[v['versionId'] for v in versions],**self.evidence(i)}
  if case=='P07.primary':
   i=self.create();preview=self.preview(i);self.gate('arm',{'stage':'beforeDispatch'});o=self.publish(i,preview=preview,wait=False);self.barrier('beforeDispatch');restricted=self.restricted();changed=self.operation(self.accept(restricted,path='/clusters/'+self.cluster['id']+'/credentials'));assert changed['state']=='SUCCEEDED';self.gate('release',{'stage':'beforeDispatch'});failed=self.state(o,'FAILED');assert self.cr_absent()
   cfg=self.cfg();cfg['resources']['requestCPU']='900m';self.save(i,cfg);restored=self.operation(self.accept(self.config(),path='/clusters/'+self.cluster['id']+'/credentials'));assert restored['state']=='SUCCEEDED';browser=self.browser_retry(i,o);retry=self.call('GET','/operations/'+o['operationId']);done=self.state(o,'SUCCEEDED');assert retry['operationId']==o['operationId'] and retry['versionId']==o['versionId'] and retry['attemptId']!=o['attemptId'];assert self.cr()['spec']['podResources']['requests']['cpu']=='100m';attempts=self.call('GET','/operations/'+o['operationId']+'/attempts')['attempts'];assert len(attempts)==2 and attempts[0]['state']=='FAILED' and attempts[1]['sequence']==2;return {'failed':failed,'retry':retry,'done':done,'attempts':attempts,'browser':browser,**self.evidence(i)}
  if case in ['P10.primary','P11.primary','C06.same-uid']:
   known=case!='P11.primary';i,o=self.first() if known else (self.create(),None)
   cfg=self.cfg();cfg['resources']['requestCPU']='200m'
   if known:self.save(i,cfg)
   proxy=self.setup_proxy();path='/apis/monitoring.fjsxy.github.io/v1alpha1/kubestatemetrics'+('/'+self.crname if known else '')
   proxy.arm('PUT' if known else 'POST',path,'drop_and_block_reads' if case=='C06.same-uid' else 'drop_after_upstream');op=self.publish(i,wait=False);unknown=self.state(op,'RESULT_UNCERTAIN','IDENTITY_UNCERTAIN','SUCCEEDED');cr=self.cr();uid=cr['metadata']['uid']
   if not known:
    assert unknown['state']=='IDENTITY_UNCERTAIN';assert self.call('GET','/instances/'+i['id'])['uid']=='';self.action(op,'retry',409);self.action(op,'continue-unsent',409);self.call('POST','/instances/'+i['id']+'/deletions',{'versionId':op['versionId']},status=409);self.call('POST','/operations/'+op['operationId']+'/refresh',{});assert self.call('GET','/operations/'+op['operationId'])['state']=='IDENTITY_UNCERTAIN'
   elif case=='C06.same-uid':
    assert unknown['state']=='RESULT_UNCERTAIN';before=self.call('GET','/clusters/'+self.cluster['id']);bad=self.config();bad['users'][0]['user']={'token':'invalid-fixture-candidate'};failed=self.operation(self.accept(bad,path='/clusters/'+self.cluster['id']+'/credentials'));assert failed['state']=='FAILED';assert self.call('GET','/clusters/'+self.cluster['id'])['credentialRevision']==before['credentialRevision'];good=self.operation(self.accept(self.config(),path='/clusters/'+self.cluster['id']+'/credentials'));assert good['state']=='SUCCEEDED' and good['credentialRevision']==before['credentialRevision']+1;self.state(op,'SUCCEEDED');assert self.cr()['metadata']['uid']==uid
   else:self.state(op,'SUCCEEDED');assert self.cr()['spec']['podResources']['requests']['cpu']=='200m'
   events=self.capture_proxy();writes=self.mutations(events,'PUT' if known else 'POST');assert len(writes)==1,events;return {'unknown':unknown,'operation':self.call('GET','/operations/'+op['operationId']),'mutationCount':len(writes),'uid':uid,'proxyEvents':events,'cr':cr}
  return self.run_more(case)
 def run_more(self,case):
  if case in ['P13.unsent','P13.possibly-sent']:
   stage='beforeDispatch' if case.endswith('unsent') else 'afterSendMarker';i=self.create();self.gate('arm',{'stage':stage});o=self.publish(i,wait=False);barrier=self.barrier(stage);before=self.call('GET','/operations/'+o['operationId']+'/attempts')['attempts'];rebuild=self.rebuild(force=True);pending=self.state(o,'WAITING_CONFIRMATION' if case.endswith('unsent') else 'IDENTITY_UNCERTAIN');assert self.cr_absent();time.sleep(1.2);assert self.cr_absent()
   if case.endswith('unsent'):
    cfg=self.cfg();cfg['resources']['requestCPU']='900m';self.save(i,cfg);next=self.action(o,'continue-unsent');assert next['operationId']==o['operationId'] and next['versionId']==o['versionId'] and next['attemptId']!=o['attemptId'];self.state(o,'SUCCEEDED');assert self.cr()['spec']['podResources']['requests']['cpu']=='100m'
   else:
    self.action(o,'retry',409);self.action(o,'continue-unsent',409);self.action(o,'cancel',409);assert self.call('GET','/instances/'+i['id'])['uid']==''
   return {'barrier':barrier,'beforeAttempts':before,'rebuild':rebuild,'pending':pending,'afterAttempts':self.call('GET','/operations/'+o['operationId']+'/attempts')['attempts'],'noAutomaticReplay':True}
  if case=='T08.primary':
   i=self.create();self.gate('arm',{'stage':'afterSendMarker'});sent=self.publish(i,wait=False);self.barrier('afterSendMarker')
   other=self.create(self.cfg(self.crname+'-queued'));self.gate('arm',{'stage':'beforeDispatch'});unsent=self.publish(other,wait=False);self.barrier('beforeDispatch');start=time.monotonic()
   self.k('-n',self.ns,'delete','pod',self.pod,'--wait=false');observed=[]
   until=time.monotonic()+5
   while time.monotonic()<until:
    try:
     req=urllib.request.Request(self.base+'/api/v1/instances/'+i['id']+'/editing-activity',data=b'{}',method='POST',headers={'Origin':self.base,'Idempotency-Key':str(uuid.uuid4()),'Content-Type':'application/json'})
     try:r=self.http.open(req,timeout=1)
     except urllib.error.HTTPError as e:r=e
     observed.append(r.code)
     if r.code==503:break
    except Exception:pass
    time.sleep(.05)
   assert 503 in observed,'停止起点未拒绝HTTP新受理'
   try:urllib.request.urlopen(self.base+'/readyz',timeout=1);raise AssertionError('退出仍ready')
   except urllib.error.HTTPError as e:assert e.code==503
   self.k('-n',self.ns,'wait','--for=delete','pod/'+self.pod,'--timeout=35s');elapsed=time.monotonic()-start;assert elapsed>=9 and elapsed<30,elapsed
   self.stop_forward();self.k('-n',self.ns,'rollout','status','deployment/monitor-mgr','--timeout=100s');pod=json.loads(self.k('-n',self.ns,'get','pods','-l','app=monitor-mgr','-o','json').stdout)['items'][0];self.pod=pod['metadata']['name'];self.connect();self.connect_gate();a=self.state(sent,'IDENTITY_UNCERTAIN');b=self.state(unsent,'WAITING_CONFIRMATION');assert self.cr_absent() and self.cr_absent(self.crname+'-queued');return {'shutdownSeconds':elapsed,'admissionStatuses':observed,'possiblySent':a,'provenUnsent':b,'podUID':pod['metadata']['uid'],'noReplay':True}
  if case.startswith('P12.'):
   i,o=self.first();original=self.cr();uid=original['metadata']['uid'];preview=self.call('POST','/instances/'+i['id']+'/deletion-previews',{});self.k('delete','kubestatemetrics',self.crname,'--wait=true','--timeout=90s',target=self.target)
   if case.endswith('new-uid'):
    replacement={k:original[k] for k in ['apiVersion','kind','spec']};replacement['metadata']={'name':self.crname};self.k('apply','-f','-',data=replacement,target=self.target);newuid=self.cr()['metadata']['uid'];assert newuid!=uid
   else:newuid=None
   self.call('POST','/instances/'+i['id']+'/previews',{'draftRevision':1},status=409);failed=self.call('POST','/instances/'+i['id']+'/deletions',{'versionId':preview['versionId'],'previewId':preview['previewId']},status=409);current=self.call('GET','/instances/'+i['id']);assert current['uid']==uid and current['currentVersionId']==o['versionId'];runtime=self.call('POST','/instances/'+i['id']+'/refresh',{})
   if newuid:assert self.cr()['metadata']['uid']==newuid and runtime['summary']=='Unknown'
   else:assert self.cr_absent() and runtime['summary']=='Missing'
   return {'originalUID':uid,'replacementUID':newuid,'deleteRejected':failed,'runtime':runtime,'historyPreserved':current['currentVersionId']}
  if case.startswith('P14.'):
   i,pub=self.first();original=self.cr();uid=original['metadata']['uid'];proxy=None;orphan=None
   if case.endswith('pod-remains'):
    moved=self.targetns+'-moved';self.k('create','namespace',moved,target=self.target);self.extra_namespaces.append(moved)
    self.k('patch','kubestatemetrics',self.crname,'--type=merge','-p',json.dumps({'spec':{'targetNamespace':moved}}),target=self.target)
    until=time.monotonic()+100
    while time.monotonic()<until:
     pods=json.loads(self.k('-n',moved,'get','pods','-o','json',target=self.target).stdout)['items']
     if any(any(c['type']=='Ready' and c['status']=='True' for c in p.get('status',{}).get('conditions',[])) for p in pods):break
     time.sleep(.5)
    else:raise AssertionError('外部漂移后的跨namespace工作负载未建立')
   if case.endswith('finalizer'):
    self.k('patch','kubestatemetrics',self.crname,'--type=merge','-p',json.dumps({'metadata':{'finalizers':['phase1.fixture/hold']}}),target=self.target)
   if case.endswith('pod-remains') or case.endswith('read-failure') or case.endswith('delete-response-drop'):
    proxy=self.setup_proxy();action='pause_before_send' if case.endswith('pod-remains') else ('drop_and_block_aux' if case.endswith('read-failure') else 'drop_after_upstream');proxy.arm('DELETE','/apis/monitoring.fjsxy.github.io/v1alpha1/kubestatemetrics/'+self.crname,action)
   op=self.delete(i,False)
   if case.endswith('pod-remains'):
    proxy.paused();pods=json.loads(self.k('-n',moved,'get','pods','-o','json',target=self.target).stdout)['items'];assert pods;orphan=pods[0];labels={k:None for k in orphan['metadata'].get('labels',{})};labels['phase1.fixture/orphan']=self.a.prefix;self.k('-n',moved,'patch','pod',orphan['metadata']['name'],'--type=merge','-p',json.dumps({'metadata':{'ownerReferences':[],'labels':labels}}),target=self.target);proxy.control('release',{'ruleId':proxy.control('state')['rule']['id']})
   if case.endswith('delete-response-drop'):
    done=self.state(op,'SUCCEEDED',timeout=100);events=self.capture_proxy();assert len(self.mutations(events,'DELETE'))==1;return {'operation':done,'originalUID':uid,'mutationCount':1,'events':events,'resourcesGone':not self.workloads() and self.cr_absent()}
   blocked=self.state(op,'DELETION_BLOCKED','DELETION_VERIFYING');time.sleep(.5);assert self.call('GET','/operations/'+op['operationId'])['state']!='SUCCEEDED';self.action(op,'retry',409);self.call('POST','/clusters/'+self.cluster['id']+'/unregister',{},status=409)
   retained=self.workloads()+([x for x in json.loads(self.k('-n',moved,'get','pods','-o','json',target=self.target).stdout)['items']] if orphan else []);activity=self.call('GET','/instances/'+i['id'])['activityAt']
   if case.endswith('finalizer'):
    cr=self.cr();assert 'phase1.fixture/hold' in cr['metadata']['finalizers'];self.k('patch','kubestatemetrics',self.crname,'--type=merge','-p',json.dumps({'metadata':{'finalizers':[]}}),target=self.target)
   if orphan:
    assert any(x['metadata']['uid']==orphan['metadata']['uid'] for x in retained);self.k('-n',orphan['metadata']['namespace'],'delete','pod',orphan['metadata']['name'],'--wait=true','--timeout=60s',target=self.target)
   events=self.capture_proxy() if proxy else []
   if case.endswith('read-failure'):
    assert self.cr_absent();assert any(x['stage']=='controlled_read_rejected' for x in events);proxy.control('reset',{})
   self.call('POST','/operations/'+op['operationId']+'/refresh',{});done=self.state(op,'SUCCEEDED',timeout=100);deleted=self.call('GET','/instances/'+i['id']);assert deleted['lifecycle']=='DELETED' and self.call('GET','/instances/'+i['id']+'/runtime')['summary']=='Deleted';assert deleted['activityAt']==activity,'异步完成改变显式活动排序';return {'blocked':blocked,'done':done,'originalUID':uid,'retainedResources':retained,'orphanUID':orphan['metadata']['uid'] if orphan else None,'events':events,'activityAtBeforeComplete':activity,'activityAtAfterComplete':deleted['activityAt']}
  if case=='I04.primary':
   i,o=self.first();a,clienta=self.user('owner-a');b,clientb=self.user('owner-b');self.call('POST','/clusters/'+self.cluster['id']+'/transfer',{'ownerId':a['id']})
   with self.using(clienta):self.call('GET','/instances/'+i['id']+'/draft')
   with self.using(clientb):self.call('GET','/instances/'+i['id']+'/draft',status=403)
   self.call('POST','/clusters/'+self.cluster['id']+'/transfer',{'ownerId':b['id']})
   with self.using(clienta):self.call('GET','/instances/'+i['id']+'/draft',status=403);self.call('GET','/operations/'+o['operationId'],status=403)
   with self.using(clientb):draft=self.call('GET','/instances/'+i['id']+'/draft');history=self.call('GET','/operations?instanceId='+i['id'])
   self.call('GET','/instances/'+i['id']+'/draft');return {'previousOwner':a['id'],'currentOwner':b['id'],'sourceRevision':draft['revision'],'historyIds':[x['operationId'] for x in history['operations']],'uidPreserved':self.cr()['metadata']['uid']}
  if case=='C07.primary':
   i,o=self.first();uid=self.cr()['metadata']['uid'];proxy=self.setup_proxy();cfg=self.cfg();cfg['resources']['requestCPU']='200m';self.save(i,cfg);proxy.arm('PUT','/apis/monitoring.fjsxy.github.io/v1alpha1/kubestatemetrics/'+self.crname,'drop_and_block_reads');pending=self.publish(i,wait=False);self.state(pending,'RESULT_UNCERTAIN');self.call('POST','/clusters/'+self.cluster['id']+'/transfer',{'ownerId':self.owner},status=409);self.call('POST','/clusters/'+self.cluster['id']+'/unregister',{},status=409);proxy.control('reset',{});self.state(pending,'SUCCEEDED');before=self.cr();archived=self.call('POST','/clusters/'+self.cluster['id']+'/unregister',{});assert archived['state']=='ARCHIVED';proof=self.proof('archived-credentials');assert proof['credentialRows']==0;assert self.cr()['metadata']['uid']==uid and self.cr()['metadata']['resourceVersion']==before['metadata']['resourceVersion'];self.call('GET','/clusters/'+self.cluster['id']+'/namespaces',status=409);reactivated=self.operation(self.accept(self.config(),owner=self.owner,path='/clusters/'+self.cluster['id']+'/reactivate'));assert reactivated['state']=='SUCCEEDED';time.sleep(1);assert self.cr()['metadata']['resourceVersion']==before['metadata']['resourceVersion'];return {'archived':archived,'reactivated':reactivated,'credentialsRemoved':proof,'originalUID':uid,'noReplayRV':before['metadata']['resourceVersion']}
  if case=='K01.primary':
   i,o=self.first();first=self.cr();second=self.create(self.cfg(self.crname+'-two'));secondop=self.publish(second);external={k:first[k] for k in ['apiVersion','kind','spec']};external['metadata']={'name':self.crname+'-external'};self.k('apply','-f','-',data=external,target=self.target);items=self.call('GET','/clusters/'+self.cluster['id']+'/instances')['instances'];outside=next(x for x in items if x['name']==self.crname+'-external');assert outside['readOnly'];self.call('POST','/instances/'+outside['id']+'/deletions',{'versionId':''},status=403);self.call('DELETE','/instances/'+outside['id'],status=403);self.delete(i);other=self.cr(self.crname+'-two');assert other['metadata']['uid']==self.call('GET','/instances/'+second['id'])['uid'];assert self.cr(self.crname+'-external')['metadata']['uid']==outside['uid'];assert not self.call('GET','/operations?instanceId='+outside['id'])['operations'];return {'deleted':o['operationId'],'untouchedManagedUID':other['metadata']['uid'],'externalUID':outside['uid'],'secondOperation':secondop['operationId']}
  if case=='I05.primary':
   a,clienta=self.user('dispatch-a');self.call('POST','/clusters/'+self.cluster['id']+'/transfer',{'ownerId':a['id']});self.gate('arm',{'stage':'beforeDispatch'})
   with self.using(clienta):i=self.create();op=self.publish(i,wait=False)
   self.barrier('beforeDispatch');self.call('PATCH','/accounts/'+a['id'],{'enabled':False});self.gate('release',{'stage':'beforeDispatch'});blocked=self.state(op,'FAILED');assert self.cr_absent()
   b,clientb=self.user('dispatch-b');self.call('POST','/clusters/'+self.cluster['id']+'/transfer',{'ownerId':b['id']});proxy=self.setup_proxy();other=self.create(self.cfg(self.crname+'-sent'));proxy.arm('POST','/apis/monitoring.fjsxy.github.io/v1alpha1/kubestatemetrics','pause_before_send')
   with self.using(clientb):sent=self.publish(other,wait=False)
   proxy.paused();self.call('PATCH','/accounts/'+b['id'],{'enabled':False});proxy.control('release',{'ruleId':proxy.control('state')['rule']['id']});done=self.state(sent,'SUCCEEDED');assert self.cr(self.crname+'-sent')['metadata']['uid']==self.call('GET','/instances/'+other['id'])['uid'];return {'beforeSendBlocked':blocked,'afterSendConfirmed':done,'events':self.capture_proxy()}
  raise AssertionError(case)
 def cleanup(self):
  # 仅验收fixture负责最终残留清理；KSM外部漂移到本run另一namespace也必须回收CR。
  if getattr(self,'targetns',None):
   raw=self.k('get','kubestatemetrics','-o','json',target=self.target,check=False)
   if raw.returncode==0:
    for cr in json.loads(raw.stdout)['items']:
     if cr['metadata']['name'].startswith(self.a.prefix+'-') and cr['spec'].get('targetNamespace') in self.extra_namespaces:
      obj=copy.deepcopy(cr);obj['metadata']['finalizers']=[];self.k('replace','-f','-',data=obj,target=self.target,check=False);self.k('delete','kubestatemetrics',cr['metadata']['name'],'--wait=true','--timeout=60s',target=self.target,check=False)
  if hasattr(self,'gateforward'):self.gateforward.terminate();self.gateforward.wait(timeout=5);del self.gateforward
  return super().cleanup()
 def run(self):
  product=self.a.image;product_meta=copy.deepcopy(self.image_metadata)
  for index,case in enumerate(self.a.cases or KCASES):
   self.case=case;self.extra_namespaces=[];self.traces=[];self.cookies=http.cookiejar.CookieJar();record={**self.contract[case],'testedCommit':self.a.source,'testedTree':subprocess.check_output(['git','rev-parse',self.a.source+'^{tree}'],cwd=ROOT,text=True).strip(),'runnerCommit':self.source['commit'],'source':self.source,'profile':self.a.profile,'manifestHash':hashlib.sha256((ROOT/'docs/acceptance/phase1.json').read_bytes()).hexdigest(),'contractFingerprint':fingerprint(self.contract[case]),'testSelector':case,'command':[sys.executable,*sys.argv],'processLocation':'host-driver/platform-pod','hostOS':platform.system(),'hostArch':platform.machine(),'runtimeOS':'linux','runtimeArch':'arm64','environment':self.environment,'productImage':product_meta,'outcome':'failed','startedAt':stamp()};error=None
   try:
    actual_image=self.a.fixture_image if case in FIXTURE_CASES else product;assert actual_image,'此子例需要独占应用持久屏障fixture镜像'
    meta=json.loads(subprocess.check_output(['docker','image','inspect',actual_image],text=True))[0];assert meta['Config']['Labels']['org.opencontainers.image.revision']==self.a.source
    self.a.image=actual_image;record['platform']=self.deploy(case,index);self.a.image=product;record['platform']['baseProductImage']=product
    expected=meta.get('Descriptor',{}).get('digest') or meta['Id'];assert record['platform']['imageID'].endswith(expected),'实际Pod镜像与冻结输入不符'
    if case in FIXTURE_CASES:record['applicationFixture']={'image':actual_image,'configID':meta['Id'],'manifest':meta.get('Descriptor',{}).get('digest'),'source':self.a.source,'seam':'Config.OperationBoundary only; HTTP业务仍经真实Kitex'};self.connect_gate()
    self.prepare_target(index);record['evidence']=self.run_case(case);record['outcome']='passed'
   except Exception as e:
    self.a.image=product;record['error']=str(e);record['traceback']=traceback.format_exc();error=e
    if hasattr(self,'targetns'):
     try:record['failureObjects']={'crs':json.loads(self.k('get','kubestatemetrics','-o','json',target=self.target).stdout),'workloads':self.workloads(),'events':json.loads(self.k('-n',self.targetns,'get','events','-o','json',target=self.target).stdout)}
     except Exception:record['failureObjectsError']='对象读取失败不能当作空集'
   finally:
    record['traces']=self.traces
    try:record['cleanup']=self.cleanup()
    except Exception as e:record['cleanup']={'success':False,'failures':[str(e)]}
    record['finishedAt']=stamp();record['evidenceRefs']=[{'path':str(p.relative_to(self.out)),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in sorted(self.out.rglob('*')) if p.is_file() and (p.name.startswith(case+'.') or any(x.startswith(case+'.') for x in p.parts)) and p.name!=case+'.json']
    if not record['cleanup']['success']:record['outcome']='failed';error=error or AssertionError('finally清理失败')
    (self.out/(case+'.json')).write_text(json.dumps(record,ensure_ascii=False,indent=2));self.results.append(record);(self.out/'results.json').write_text(json.dumps(self.results,ensure_ascii=False,indent=2));print(case,record['outcome'],flush=True)
   if error:raise error

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--kubectl');p.add_argument('--control-config');p.add_argument('--data-config');p.add_argument('--control-pod-config');p.add_argument('--data-pod-config');p.add_argument('--image');p.add_argument('--fixture-image');p.add_argument('--source');p.add_argument('--output');p.add_argument('--prefix',default='mm-phase1-lifecycle');p.add_argument('--port',type=int,default=18720);p.add_argument('--layer',choices=['Q','K'],default='K');p.add_argument('--profile',choices=['LOCAL_FULL','CI_FAST','CI_SMOKE'],default='LOCAL_FULL');p.add_argument('--allow-dirty-runner',action='store_true');p.add_argument('--list',action='store_true');p.add_argument('--cases',nargs='*');p.add_argument('--lane-lock',default='/tmp/monitor-mgr-implementation/environment/real-api-lane.lock');a=p.parse_args();registry=QCASES if a.layer=='Q' else KCASES
 if a.list:print(json.dumps([{'id':id,'layer':manifest()[id]['layer'],'testSelector':QCASES.get(id,id),'profiles':manifest()[id]['profiles']} for id in list(QCASES)+KCASES],ensure_ascii=False,indent=2));raise SystemExit(0)
 if not a.output:p.error('必须显式output')
 if a.cases and any(id not in registry for id in a.cases):p.error('子例未知或层级不符')
 if any(a.profile not in manifest()[id]['profiles'] for id in (a.cases or registry)):p.error('子例不属于所选profile')
 if a.layer=='Q':raise SystemExit(run_q(a,QCASES,manifest(),ROOT,source_state(),stamp))
 for field in ['kubectl','control_config','data_config','control_pod_config','data_pod_config','image','source']:
  if not getattr(a,field):p.error('K必须显式'+field)
 if a.profile!='LOCAL_FULL':p.error('本运行环境为双集群LOCAL_FULL；Linux CI由最终CI在其环境实际运行')
 # 关键：覆盖整批及finally，共用API的Agent/KSM inventory不得互相污染。
 with open(a.lane_lock,'a+') as lane:
  print('等待独占真实API验收lane',flush=True);fcntl.flock(lane,fcntl.LOCK_EX);print('已取得独占真实API验收lane',flush=True);Suite(a).run()
