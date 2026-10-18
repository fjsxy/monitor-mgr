"""KSM子例断言：所有读写经平台HTTP或显式kubectl读取真实目标，后者仅构造外部故障/fixture。"""
import concurrent.futures,copy,http.client,http.cookiejar,json,socket,threading,time,urllib.request,uuid
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer

def controller(s,kind,role=None):
 out=[o for o in s.workloads() if o['kind']==kind]
 if role=='cluster':out=[o for o in out if o['metadata']['name'].endswith('-cluster')]
 if role=='unscheduled':out=[o for o in out if o['metadata']['name'].endswith('-unscheduled')]
 if role=='node':out=[o for o in out if o['kind']=='DaemonSet']
 assert len(out)==1,(kind,role,[o['metadata']['name'] for o in out]);return out[0]
def stopped_workloads(s,timeout=90):
 end=time.monotonic()+timeout
 while time.monotonic()<end:
  if not s.workloads():return
  time.sleep(.5)
 raise AssertionError('停止意图已写入，但真实workload/Pod仍未完全消失')
def counts(s,expected,timeout=100):
 end=time.monotonic()+timeout
 while time.monotonic()<end:
  cr=s.cr();st=cr.get('status',{})
  if st.get('observedGeneration')==cr['metadata']['generation'] and st.get('effectiveNamespaceCount',0)==expected:return cr
  time.sleep(.5)
 raise AssertionError('namespace有效集合未收敛')
def args(s):return [c.get('args',[]) for o in s.workloads() if o['kind']=='Pod' for c in o['spec']['containers']]
def patch(s,body,subresource=None):
 command=['patch','kubestatemetrics',s.crname,'--type=merge','-p',json.dumps(body)]
 if subresource:command+=['--subresource='+subresource]
 return s.k(*command,target=s.target)
def base_published(s,mode='Deployment'):
 cfg=s.cfg(mode=mode)
 if mode=='DaemonSetSharded':cfg['collectors']={'mode':'Include','names':['pods','nodes']}
 i=s.create(cfg);o=s.publish(i);s.runtime(i);return i,cfg,o

def run_case(s,case):
 if case=='K03.primary':
  cfg=s.cfg(mode='StatefulSetSharded');bad=copy.deepcopy(cfg);bad['shards']=1;s.call('POST','/clusters/'+s.cluster['id']+'/instances',{'component':'kube-state-metrics','ksm':bad},status=422)
  i=s.create(cfg);o=s.publish(i);r=s.runtime(i);cr=s.cr();assert cr['spec']['deployment']=={'mode':'StatefulSetSharded','statefulSetSharded':{'shards':2}};st=controller(s,'StatefulSet');assert st['spec']['replicas']==2 and r['desiredPods']==2 and r['availablePods']==2;return {'operation':o,**s.evidence(i)}
 if case=='K04.primary':
  bad=s.cfg(mode='DaemonSetSharded');bad['collectors']={'mode':'Include','names':['pods']};s.call('POST','/clusters/'+s.cluster['id']+'/instances',{'component':'kube-state-metrics','ksm':bad},status=422)
  i,cfg,o=base_published(s,'DaemonSetSharded');cr=s.cr();assert cr['spec']['deployment']=={'mode':'DaemonSetSharded','daemonSetSharded':{}};controller(s,'DaemonSet');controller(s,'Deployment','cluster');controller(s,'Deployment','unscheduled');return {'operation':o,**s.evidence(i)}
 if case=='K05.primary':
  i,cfg,o=base_published(s);history=[s.evidence(i)];transitions=[]
  for mode,kind in [('StatefulSetSharded','StatefulSet'),('DaemonSetSharded','DaemonSet'),('Deployment','Deployment')]:
   old=[o['metadata']['uid'] for o in s.workloads() if o['kind'] in ['Deployment','StatefulSet','DaemonSet']];cfg=s.cfg(mode=mode)
   if mode=='DaemonSetSharded':cfg['collectors']={'mode':'Include','names':['pods','nodes']}
   s.save(i,cfg);p=s.preview(i);assert any('重建' in w for w in p['warnings']);o=s.publish(i,preview=p);seen=[];end=time.monotonic()+100
   while time.monotonic()<end:
    loads=[x for x in s.workloads() if x['kind'] in ['Deployment','StatefulSet','DaemonSet']];uids=[x['metadata']['uid'] for x in loads];new=[x for x in loads if x['kind']==kind and x['metadata']['uid'] not in old];remaining=[x for x in old if x in uids];seen.append({'at':time.time(),'controllers':[{'kind':x['kind'],'uid':x['metadata']['uid']} for x in loads]})
    if new:assert not remaining,'旧模式尚存在时出现新模式'
    if new and not remaining:break
    time.sleep(.25)
   else:raise AssertionError('模式替换未形成新UID')
   s.runtime(i);transitions.append({'oldUIDs':old,'newMode':mode,'observations':seen});history.append(s.evidence(i))
  events=json.loads(s.k('-n',s.targetns,'get','events','-o','json',target=s.target).stdout)['items'];return {'transitions':transitions,'history':history,'events':events}
 if case.startswith('K06.'):
  role=case.split('.')[1];i,cfg,o=base_published(s,'DaemonSetSharded');history=[s.evidence(i)];field=role+'Resources';block=role+'PodResources';cfg[field]={'mode':'Inherit'};cfg['advancedResources']=True;s.save(i,cfg);s.publish(i);s.runtime(i);assert block not in s.cr()['spec']['deployment']['daemonSetSharded'];history.append(s.evidence(i))
  own={'requestMemory':'64Mi','limitMemory':'128Mi'};cfg[field]={'mode':'Override','resources':own};s.save(i,cfg);s.publish(i);s.runtime(i);expected={'requests':{'memory':'64Mi'},'limits':{'memory':'128Mi'}};assert s.cr()['spec']['deployment']['daemonSetSharded'][block]==expected
  w=controller(s,'DaemonSet' if role=='node' else 'Deployment',role);assert w['spec']['template']['spec']['containers'][0]['resources']==expected;history.append(s.evidence(i))
  cfg['advancedResources']=False;s.save(i,cfg);saved=s.call('GET','/instances/'+i['id']+'/draft');assert saved['ksm'][field]['resources']==own;s.publish(i);s.runtime(i);assert block not in s.cr()['spec']['deployment']['daemonSetSharded'];w=controller(s,'DaemonSet' if role=='node' else 'Deployment',role);assert w['spec']['template']['spec']['containers'][0]['resources']==s.cr()['spec']['podResources'];history.append(s.evidence(i))
  cfg['advancedResources']=True;s.save(i,cfg);s.publish(i);s.runtime(i);assert s.cr()['spec']['deployment']['daemonSetSharded'][block]==expected;history.append(s.evidence(i));return {'role':role,'history':history}
 if case.startswith('K07.'):
  collectors,metric=case.split('.')[1].split('-');cfg=s.cfg();cfg['collectors']={'mode':'Default'} if collectors=='default' else {'mode':'Include','names':['pods','nodes']};cfg['metricFilter']={'mode':metric.title()}
  if metric!='all':cfg['metricFilter']['patterns']=['kube_pod_.*','kube_node_info']
  i=s.create(cfg);o=s.publish(i);s.runtime(i);cr=s.cr();assert cr['spec']['collectors']==cfg['collectors'] and cr['spec']['metricFilter']==cfg['metricFilter']
  commands=args(s);assert commands
  for command in commands:
   resources=[x for x in command if x.startswith('--resources=')];assert (not resources) if collectors=='default' else resources==['--resources=pods,nodes']
   filters=[x for x in command if x.startswith('--metric-allowlist=') or x.startswith('--metric-denylist=')]
   assert (not filters) if metric=='all' else filters==['--metric-'+('allowlist' if metric=='allow' else 'denylist')+'=kube_pod_.*,kube_node_info']
  return {'operation':o,**s.evidence(i)}
 if case.startswith('K08.'):
  flavor=case.split('.')[1];cfg=s.cfg()
  if flavor=='union':
   ns=s.targetns+'-union';s.k('create','namespace',ns,target=s.target);s.extra_namespaces.append(ns);cfg['namespaceFilter']={'mode':'Include','names':[s.targetns],'namePatterns':[ns]}
  if flavor in ['zero','dynamic-pattern']:cfg['namespaceFilter']={'mode':'Include','names':[],'namePatterns':[s.targetns+'-dynamic-[ab]']}
  i=s.create(cfg);o=s.publish(i);history=[]
  if flavor in ['zero','dynamic-pattern']:
   s.runtime(i,'NoMatch');cr=counts(s,0);assert not [x for x in s.workloads() if x['kind'] in ['Deployment','StatefulSet','DaemonSet','Pod']];r=s.call('GET','/instances/'+i['id']+'/runtime');assert r['desiredPods']==0 and r['availablePods']==0;history.append(s.evidence(i))
   if flavor=='zero':return {'operation':o,'history':history}
   ns=s.targetns+'-dynamic-a';s.k('create','namespace',ns,target=s.target);s.extra_namespaces.append(ns);counts(s,1);s.runtime(i);assert all('--namespaces='+ns in a for a in args(s));history.append(s.evidence(i));s.k('delete','namespace',ns,'--wait=true',target=s.target);s.extra_namespaces.remove(ns);counts(s,0);s.runtime(i,'NoMatch');assert not [x for x in s.workloads() if x['kind'] in ['Deployment','StatefulSet','DaemonSet','Pod']];history.append(s.evidence(i));return {'operation':o,'history':history}
  s.runtime(i)
  if flavor=='all':assert s.cr()['spec']['namespaceFilter']=={'mode':'All'}
  if flavor=='union':counts(s,2);expected='--namespaces='+','.join(sorted([s.targetns,ns]));assert all(expected in a for a in args(s))
  return {'operation':o,**s.evidence(i)}
 if case in ['K09.primary','P01.primary']:
  i,cfg,o=base_published(s);before=s.cr();beforelog=s.k('-n',s.ns,'logs','deployment/monitor-mgr').stdout.decode();cfg['resources']['requestCPU']='150m'
  if case=='K09.primary':cfg['desiredState']='Suspended'
  saved=s.save(i,cfg);after=s.cr();afterlog=s.k('-n',s.ns,'logs','deployment/monitor-mgr').stdout.decode();assert before['spec']==after['spec'];assert beforelog.count('目标API写请求')==afterlog.count('目标API写请求')
  # 独立status推进不能被错误归于保存产生的mutation。
  patch(s,{'status':{'availablePods':0}},'status');afterstatus=s.cr();assert afterstatus['spec']==before['spec'];assert afterstatus['metadata']['resourceVersion']!=before['metadata']['resourceVersion']
  if case=='P01.primary':return {'savedRevision':saved['revision'],'platformMutationDelta':0,'before':before,'afterSave':after,'independentStatus':afterstatus,**s.evidence(i)}
  history=[];s.publish(i);s.runtime(i,'Suspended');stopped_workloads(s);assert not [x for x in s.workloads() if x['kind'] in ['Deployment','StatefulSet','DaemonSet','Pod']];services=json.loads(s.k('-n',s.targetns,'get','services,serviceaccounts','-o','json',target=s.target).stdout)['items'];assert services;history.append(s.evidence(i))
  cfg['desiredState']='Running';s.save(i,cfg);s.publish(i);s.runtime(i);history.append(s.evidence(i));cfg['desiredState']='Disabled';s.save(i,cfg);s.publish(i);s.runtime(i,'Disabled');stopped_workloads(s);assert s.cr()['spec']['enabled'] is False and s.cr()['spec']['suspended'] is False;assert not [x for x in s.workloads() if x['kind'] in ['Deployment','StatefulSet','DaemonSet','Pod']];assert not [x for x in json.loads(s.k('-n',s.targetns,'get','services,serviceaccounts','-o','json',target=s.target).stdout)['items'] if any(o.get('uid')==s.cr()['metadata']['uid'] for o in x['metadata'].get('ownerReferences',[]))];history.append(s.evidence(i));return {'saveDidNotMutate':True,'history':history}
 if case in ['P02.primary','P03.another-active']:
  proxy=s.setup_proxy();i=s.create();p=s.preview(i);proxy.arm('POST','/apis/monitoring.fjsxy.github.io/v1alpha1/kubestatemetrics');o=s.publish(i,preview=p,wait=False);paused=proxy.paused();attempts=s.call('GET','/operations/'+o['operationId']+'/attempts')['attempts'];assert len(attempts)==1 and attempts[0]['possiblySent'] is True;state=s.call('GET','/instances/'+i['id']);assert not state['currentVersionId'];cfg=s.cfg();cfg['resources']['requestCPU']='300m';s.save(i,cfg)
  if case=='P03.another-active':s.publish(i,preview=p,key='another',wait=False,status=409)
  proxy.control('release',{'ruleId':paused['rule']['id']});done=s.operation(o);assert done['state']=='SUCCEEDED',done;v=s.call('GET','/instances/'+i['id']+'/versions/'+o['versionId']);assert v['sourceDraftRevision']==1 and v['ksm']['resources']['requestCPU']=='100m';assert s.cr()['spec']['podResources']['requests']['cpu']=='100m';s.runtime(i);return {'operation':done,'sourceVersion':v,'currentDraft':s.call('GET','/instances/'+i['id']+'/draft'),'paused':paused,'persistedAttemptsAtNetworkBarrier':attempts,**s.evidence(i)}
 if case in ['P03.same','P03.different-content']:
  i=s.create();p=s.preview(i);body={'previewId':p['previewId'],'draftRevision':p['draftRevision'],'overwriteDrift':False};key='same-acceptance-key'
  if case=='P03.same':
   with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:responses=list(pool.map(lambda _:s.call('POST','/instances/'+i['id']+'/publications',body,key,202),range(6)))
   assert len({o['operationId'] for o in responses})==1;o=responses[0]
  else:
   o=s.call('POST','/instances/'+i['id']+'/publications',body,key,202);other={**body,'overwriteDrift':True};s.call('POST','/instances/'+i['id']+'/publications',other,key,409);responses=[o]
  done=s.operation(o);assert done['state']=='SUCCEEDED';s.runtime(i);history=s.call('GET','/instances/'+i['id']+'/versions')['versions'];assert len(history)==1;logs=s.k('-n',s.ns,'logs','deployment/monitor-mgr').stdout.decode();assert logs.count('目标API写请求')==1;return {'responses':responses,'operation':done,'actualMutationAttempts':1,**s.evidence(i)}
 if case.startswith('P05.'):
  i,cfg,o=base_published(s);extra=case.endswith('extra-spec');spec={'enabled':False} if not extra else {'podResources':{'requests':{'ephemeral-storage':'1Mi'},'limits':{'ephemeral-storage':'2Mi'}}};patch(s,{'metadata':{'labels':{'external-owner':'keep'},'annotations':{'external-note':'keep'},'finalizers':s.cr()['metadata'].get('finalizers',[])+['fixture.monitor-mgr/hold']},'spec':spec});left=s.cr();p=s.preview(i);assert p['drift'] is True
  if extra:assert 'ephemeral-storage' in p['leftYAML'] and 'ephemeral-storage' not in p['rightYAML']
  s.publish(i,preview=p,wait=False,status=409);o=s.publish(i,preview=p,overwrite=True);s.runtime(i);after=s.cr();assert after['metadata']['uid']==left['metadata']['uid'];assert after['metadata']['labels']['external-owner']=='keep' and after['metadata']['annotations']['external-note']=='keep' and set(after['metadata']['finalizers'])>=set(left['metadata']['finalizers']);assert after.get('status') and after['spec']['enabled'] is True;assert 'ephemeral-storage' not in after['spec']['podResources']['requests'];return {'beforeOverwrite':left,'preview':p,'operation':o,**s.evidence(i)}
 if case.startswith('P06.'):
  i,cfg,o=base_published(s);p=s.preview(i);before=s.cr();flavor=case.split('.')[1]
  if flavor=='status-only':patch(s,{'status':{'availablePods':0}},'status')
  elif flavor=='rv-only':patch(s,{'metadata':{'annotations':{'rv-only':'keep'}}})
  elif flavor=='default-equivalent':
   s.k('patch','kubestatemetrics',s.crname,'--type=json','-p','[{"op":"remove","path":"/spec/suspended"}]',target=s.target)
  else:patch(s,{'spec':{'suspended':True}})
  changed=s.cr()
  if flavor=='spec-change':s.publish(i,preview=p,wait=False,status=409);assert s.cr()['spec']['suspended'] is True;return {'before':before,'changed':changed,'oldPreviewRejected':True}
  assert before['spec']==changed['spec'];o=s.publish(i,preview=p);s.runtime(i);assert s.cr()['metadata']['uid']==before['metadata']['uid'];return {'before':before,'changed':changed,'operation':o,**s.evidence(i)}
 if case.startswith('P09.'):
  i=s.create();p=s.preview(i);key='response-loss';body={'previewId':p['previewId'],'draftRevision':1,'overwriteDrift':False};parent=s;accepted=threading.Event();box={}
  class Front(BaseHTTPRequestHandler):
   def log_message(self,*a):pass
   def do_POST(self):
    size=int(self.headers['Content-Length']);data=json.loads(self.rfile.read(size));box['operation']=parent.call('POST',self.path,data,key,202);box['acceptedAt']=time.monotonic();accepted.set()
    if case.endswith('deadline'):time.sleep(.4)
    self.send_response(202);self.end_headers()
    if case.endswith('response-drop'):self.close_connection=True;return
    try:self.wfile.write(json.dumps(box['operation']).encode())
    except (BrokenPipeError,ConnectionResetError):pass
  front=ThreadingHTTPServer(('127.0.0.1',0),Front);thread=threading.Thread(target=front.serve_forever,daemon=True);thread.start();conn=http.client.HTTPConnection('127.0.0.1',front.server_port,timeout=.15)
  try:
   conn.request('POST','/instances/'+i['id']+'/publications',body=json.dumps(body),headers={'Content-Type':'application/json'})
   # 固定故障时序：先有真实平台202，再让客户端读取响应超时，避免把受理前慢请求冒充本例。
   assert accepted.wait(4),'真实平台未完成受理'
   try:resp=conn.getresponse();assert case.endswith('response-drop') and resp.status==202
   except (TimeoutError,socket.timeout):assert case.endswith('deadline');box['clientDeadlineAt']=time.monotonic()
   assert accepted.wait(4)
   if case.endswith('deadline'):assert box['acceptedAt']<=box['clientDeadlineAt'],'deadline发生在真正受理之前'
   found=s.call('GET','/operations?instanceId='+i['id']+'&idempotencyKey='+key)['operations'];assert len(found)==1 and found[0]['operationId']==box['operation']['operationId'];again=s.call('POST','/instances/'+i['id']+'/publications',body,key,202);assert again['operationId']==found[0]['operationId'];done=s.operation(again);assert done['state']=='SUCCEEDED';s.runtime(i);return {'operation':done,'lookup':found,'realAcceptanceBeforeClientDeadline':True,**s.evidence(i)}
  finally:conn.close();front.shutdown();front.server_close();thread.join(timeout=2)
 if case=='C04.ksm-crd':
  name='kubestatemetrics.monitoring.fjsxy.github.io';original=json.loads(s.k('get','crd',name,'-o','json',target=s.target).stdout);s.crd_restore=original
  s.k('delete','crd',name,'--wait=true',target=s.target);op=s.operation(s.accept(s.config(s.target),path='/clusters/'+s.cluster['id']+'/credentials'));assert op['state']=='SUCCEEDED';cl=s.call('GET','/clusters/'+s.cluster['id']);cap=next(x for x in cl['capabilities'] if x['component']=='kube-state-metrics');assert cap['allowed'] is False
  i=s.create();denied=s.call('POST','/instances/'+i['id']+'/previews',{'draftRevision':1},status=409);s.call('POST','/instances/'+i['id']+'/publications',{'previewId':'missing-crd','draftRevision':1,'overwriteDrift':False},status=409);return {'cluster':cl,'denied':denied,'registeredWithoutCRD':True}
 if case=='C05.primary':return restricted_namespace(s)
 if case=='I03.primary':return isolation(s)
 raise AssertionError('没有子例执行器：'+case)

def restricted_namespace(s):
 ns=s.targetns;allowed=ns+'-allowed';s.k('create','namespace',allowed,target=s.target);s.extra_namespaces.append(allowed);s.rbac_cleanup=[]
 sa={'apiVersion':'v1','kind':'ServiceAccount','metadata':{'name':'limited','namespace':ns}}
 role={'apiVersion':'rbac.authorization.k8s.io/v1','kind':'ClusterRole','metadata':{'name':ns},'rules':[{'apiGroups':['authentication.k8s.io'],'resources':['selfsubjectreviews'],'verbs':['create']},{'apiGroups':['authorization.k8s.io'],'resources':['selfsubjectaccessreviews'],'verbs':['create']},{'apiGroups':[''],'resources':['namespaces'],'verbs':['get','list']},{'apiGroups':['monitoring.fjsxy.github.io'],'resources':['kubestatemetrics'],'verbs':['get','list','create','update','delete']}]}
 binding={'apiVersion':'rbac.authorization.k8s.io/v1','kind':'ClusterRoleBinding','metadata':{'name':ns},'roleRef':{'apiGroup':'rbac.authorization.k8s.io','kind':'ClusterRole','name':ns},'subjects':[{'kind':'ServiceAccount','name':'limited','namespace':ns}]}
 r={'apiVersion':'rbac.authorization.k8s.io/v1','kind':'Role','metadata':{'name':'workload-write','namespace':allowed},'rules':[{'apiGroups':['apps'],'resources':['deployments'],'verbs':['get','list','create','update','delete']}]};b={'apiVersion':'rbac.authorization.k8s.io/v1','kind':'RoleBinding','metadata':{'name':'workload-write','namespace':allowed},'roleRef':{'apiGroup':'rbac.authorization.k8s.io','kind':'Role','name':'workload-write'},'subjects':binding['subjects']}
 for obj in [sa,role,binding,r,b]:s.k('apply','-f','-',data=obj,target=s.target)
 s.rbac_cleanup=[('clusterrolebinding',ns),('clusterrole',ns)];token=s.k('-n',ns,'create','token','limited','--duration=1h',target=s.target).stdout.decode().strip();cfg=s.config(s.target);cfg['users']=[{'name':'limited','user':{'token':token}}];cfg['contexts'][0]['context']['user']='limited';o=s.operation(s.accept(cfg,path='/clusters/'+s.cluster['id']+'/credentials'));assert o['state']=='SUCCEEDED';i=s.create();denied=s.call('POST','/instances/'+i['id']+'/previews',{'draftRevision':1},status=403);assert not [x for x in json.loads(s.k('get','kubestatemetrics','-o','json',target=s.target).stdout)['items'] if x['metadata']['name']==s.crname];good=s.cfg(name=s.crname+'-allowed');good['targetNamespace']=allowed;j=s.create(good);published=s.publish(j);s.targetns=allowed;s.crname=good['name'];s.extra_namespaces.append(ns);s.extra_namespaces.remove(allowed);s.runtime(j);return {'deniedNamespace':ns,'allowedNamespace':allowed,'denied':denied,'successfulAuthorizedNamespace':published,**s.evidence(j)}

def isolation(s):
 adminjar=s.cookies;adminhttp=s.http;users=[]
 for number in [1,2]:
  username='owner'+str(number);created=s.call('POST','/accounts',{'username':username,'password':s.password,'role':'USER'},status=201);jar=http.cookiejar.CookieJar();s.cookies=jar;s.http=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar));s.call('POST','/sessions',{'username':username,'password':s.password},status=201);personal=s.password+'x';s.call('POST','/session/password',{'currentPassword':s.password,'newPassword':personal});s.call('POST','/sessions',{'username':username,'password':personal},status=201);users.append({'id':created['id'],'jar':jar,'http':s.http});s.cookies=adminjar;s.http=adminhttp
 first=s.cluster;s.call('POST','/clusters/'+first['id']+'/transfer',{'ownerId':users[0]['id']});secondop=s.operation(s.accept(s.config('data'),users[1]['id'],name='data-owner'));assert secondop['state']=='SUCCEEDED';second=s.call('GET','/clusters/'+secondop['clusterId']);data_ns=s.targetns+'-data';s.k('create','namespace',data_ns,target='data');s.other_cleanup={'target':'data','namespace':data_ns}
 instances=[]
 for number,cluster in enumerate([first,second]):
  cfg=s.cfg(name=s.crname+'-'+str(number));cfg['targetNamespace']=s.targetns if number==0 else data_ns;i=s.create(cfg,cluster['id']);s.publish(i);instances.append(i)
 results=[]
 for number,user in enumerate(users):
  s.cookies=user['jar'];s.http=user['http'];own,other=[first,second][number],[first,second][1-number];foreign=instances[1-number];listed=s.call('GET','/clusters')['clusters'];assert [x['id'] for x in listed]==[own['id']];s.call('GET','/clusters/'+own['id']+'/instances')
  for method,path,body in [('GET','/clusters/'+other['id']+'/instances',None),('GET','/instances/'+foreign['id'],None),('GET','/instances/'+foreign['id']+'/draft',None),('PUT','/instances/'+foreign['id']+'/draft',{'expectedDraftRevision':1,'ksm':s.cfg()}),('POST','/instances/'+foreign['id']+'/publications',{'draftRevision':1,'previewId':'forged','overwriteDrift':False}),('DELETE','/instances/'+foreign['id'],None),('GET','/instances/'+foreign['id']+'/versions',None),('GET','/clusters/'+other['id']+'/references?kind=Secret&namespace='+data_ns,None)]:s.call(method,path,body,status=403)
  token=next(c.value for c in user['jar'] if c.name=='monitor_session');raw=s.k('-n',s.ns,'exec','-i',s.pod,'--','/rpcprobe',data={'token':token,'clusterId':other['id'],'instanceId':foreign['id'],'ksm':s.cfg()}).stdout.decode();start=raw.find('[{"');assert start>=0,raw;probe=json.loads(raw[start:]);assert all(x['code']=='FORBIDDEN' for x in probe),probe;results.append({'ownerId':user['id'],'ownCluster':own['id'],'forbiddenRPC':probe})
 s.cookies=adminjar;s.http=adminhttp
 own=s.k('get','kubestatemetrics',instances[0]['name'],'-o','json',target='control');other=s.k('get','kubestatemetrics',instances[1]['name'],'-o','json',target='data');return {'owners':results,'instances':instances,'controlCR':json.loads(own.stdout),'dataCR':json.loads(other.stdout)}
