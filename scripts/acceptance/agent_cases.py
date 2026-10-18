"""#18逐稳定子例断言；kubectl仅构造独占fixture或核对真实API，业务经平台HTTP/Kitex。"""
import copy,json,time,uuid

def wait(test,message,seconds=90):
 end=time.monotonic()+seconds
 while time.monotonic()<end:
  try:
   value=test()
   if value:return value
  except (KeyError,AssertionError,RuntimeError):pass
  time.sleep(.5)
 raise AssertionError(message)
def published(s,cfg=None):
 i=s.create(cfg);op=s.publish(i);return i,op
def ref(key):return {'name':'agent-auth','key':key}
def resource_name(kind):return {'ServiceMonitor':'servicemonitors','PodMonitor':'podmonitors','Probe':'probes','ScrapeConfig':'scrapeconfigs'}[kind]
def assert_none(s):
 assert not s.get('prometheusagents','-n',s.targetns)['items'],'受阻后出现Agent'
 assert not s.get('statefulsets,pods,pvc','-n',s.targetns)['items'],'受阻后出现负载或卷'
def association(s,case):
 kind_key,om,nm=case.split('.')[1].split('-');kind={'servicemonitor':'ServiceMonitor','podmonitor':'PodMonitor','probe':'Probe','scrapeconfig':'ScrapeConfig'}[kind_key];prefix=kind[0].lower()+kind[1:];cfg=s.cfg();rule=cfg['associations'][prefix];rule['object']={'mode':om.title()};rule['namespace']={'mode':'Own' if nm=='own' else nm.title()}
 if om=='labels':rule['object']['selector']={'matchLabels':{'mm18-match':'yes'},'matchExpressions':[{'key':'mm18-match','operator':'Exists'}]}
 foreign=s.targetns+'-other';s.namespace(foreign)
 for ns in [s.targetns,foreign]:s.k('label','namespace',ns,'mm18-scope='+s.targetns,target=s.target)
 if nm=='labels':rule['namespace']['selector']={'matchLabels':{'mm18-scope':s.targetns}}
 own=s.collection(kind,s.targetns);other=s.collection(kind,foreign);i=s.create(cfg)
 def selected():
  source=s.association_preview(i)['association:'+kind];assert not source['error'] and source['lastSuccessAt'];return {v['uid'] for v in source.get('matches',[])}
 first=selected();expected=set() if om=='none' else ({own['metadata']['uid']} if nm=='own' else {own['metadata']['uid'],other['metadata']['uid']})
 assert first.intersection({own['metadata']['uid'],other['metadata']['uid']})==expected
 added=s.collection(kind,s.targetns,'added');second=selected();assert (added['metadata']['uid'] in second)==(om!='none')
 s.k('label',resource_name(kind),'matched','mm18-match=no','--overwrite','-n',s.targetns,target=s.target)
 third=selected();assert (own['metadata']['uid'] in third)==(om=='all')
 s.k('delete',resource_name(kind),'added','--wait=true','-n',s.targetns,target=s.target);fourth=selected();assert added['metadata']['uid'] not in fourth
 op=s.publish(i);actual=s.cr()['spec'];p=prefix+'Selector';n=prefix+'NamespaceSelector'
 if om=='none':assert p not in actual or actual[p] is None
 else:assert actual[p]==({} if om=='all' else rule['object']['selector'])
 if nm=='own':assert n not in actual or actual[n] is None
 else:assert actual[n]==({} if nm=='all' else rule['namespace']['selector'])
 # 还需证明namespace标签变化被重新求值，而非保存一次匹配快照。
 if nm=='labels':
  s.k('label','namespace',foreign,'mm18-scope=excluded','--overwrite',target=s.target);assert other['metadata']['uid'] not in selected()
 s.k('delete',resource_name(kind),'matched','--wait=true','-n',s.targetns,target=s.target);assert own['metadata']['uid'] not in selected()
 return {'operation':op,'kind':kind,'objectMode':om,'namespaceMode':nm,'fixtureUIDs':[own['metadata']['uid'],other['metadata']['uid'],added['metadata']['uid']],'snapshots':[sorted(v) for v in [first,second,third,fourth]],'actualSelectors':{k:v for k,v in actual.items() if k.endswith('Selector')},'dynamicAddDeleteRelabelVerified':True,'sampleDeliveryClaimed':False}

def remote(s,case):
 cfg=s.cfg();variant=case.split('.')[1];rw=cfg['remoteWrite'][0]
 if variant!='http-none':rw['url']='https://receiver.example.invalid/write'
 if variant=='https-basic':rw['auth']={'mode':'BasicAuth','username':ref('username'),'password':ref('password')}
 if variant=='https-bearer':rw['auth']={'mode':'Bearer','credentials':ref('credentials')}
 if variant in ['tls-ca','tls-client']:rw['tls']={'ca':ref('ca')}
 if variant=='tls-client':rw['tls'].update({'cert':ref('cert'),'key':ref('key'),'serverName':'receiver.example.invalid'})
 if variant=='multiple':
  rw.update({'name':'first','remoteTimeout':'1m30s'});cfg['remoteWrite'].append({'url':'http://second.example.invalid/write','name':'second','remoteTimeout':'2m','auth':{'mode':'None'}})
 i,op=published(s,cfg);runtime=s.runtime(i)
 generated=wait(lambda:s.generated_config(),'Operator未生成实际配置');actual=generated['remote_write'];assert len(actual)==len(cfg['remoteWrite'])
 for wanted,got in zip(cfg['remoteWrite'],actual):
  assert got['url']==wanted['url']
  if 'name' in wanted:assert got['name']==wanted['name']
  if 'remoteTimeout' in wanted:assert got['remote_timeout'] in {'1m30s':['1m30s','90s'],'2m':['2m','2m0s','120s']}[wanted['remoteTimeout']]
 if variant=='https-basic':assert actual[0]['basic_auth']['username']==s.secret_values['username'] and actual[0]['basic_auth']['password']==s.secret_values['password']
 if variant=='https-bearer':assert actual[0]['authorization']['type']=='Bearer' and actual[0]['authorization']['credentials']==s.secret_values['credentials']
 if variant in ['tls-ca','tls-client']:
  tls=actual[0]['tls_config'];assert tls.get('ca_file') and not tls.get('insecure_skip_verify',False)
  if variant=='tls-client':assert tls.get('cert_file') and tls.get('key_file') and tls['server_name']=='receiver.example.invalid'
  # Operator资产Secret只在内存按内容核对，不序列化生成配置或资产。
  import base64
  assets=s.get('secrets','-n',s.targetns)['items'];data=[base64.b64decode(v).decode() for x in assets if '-tls-assets' in x['metadata']['name'] for v in x.get('data',{}).values()]
  assert s.secret_values['ca'] in data
  if variant=='tls-client':assert s.secret_values['key'] in data
 # 检查平台输出内无已解析的秘密；日志不含请求体，由业务安全回归另验。
 detail=s.call('GET','/instances/'+i['id']);draft=s.call('GET','/instances/'+i['id']+'/draft');versions=s.call('GET','/instances/'+i['id']+'/versions');public=json.dumps([detail,draft,versions])
 assert not any(value in public for value in s.secret_values.values())
 return {'operation':op,'runtime':runtime,'actualCRRemoteWrite':s.cr()['spec']['remoteWrite'],'generatedEndpointCount':len(actual),'generatedAuthenticationMatchesSecret':True,'generatedTLSAssetsVerified':variant in ['tls-ca','tls-client'],'platformOutputContainsNoResolvedSecrets':True,'sampleDeliveryClaimed':False}

def blocked(s,case):
 cfg=s.cfg();variant=case.split('.')[1];expected=404;rbac=None
 if variant=='missing-sa':cfg['serviceAccountName']='not-created'
 elif variant=='insufficient-sa':
  s.collection('ServiceMonitor',s.targetns);cfg['associations']['serviceMonitor']['object']={'mode':'All'};expected=403
 else:
  cfg['remoteWrite'][0]['auth']={'mode':'Bearer','credentials':ref('credentials')}
  if variant=='missing-secret':cfg['remoteWrite'][0]['auth']['credentials']['name']='not-created'
  if variant=='missing-key':cfg['remoteWrite'][0]['auth']['credentials']['key']='not-created';expected=422
  if variant=='forbidden-secret':rbac=s.restricted_credentials('secrets');expected=403
 i=s.create(cfg);error=s.preview(i,status=expected);assert error.get('code') in ['TARGET_NOT_FOUND','TARGET_FORBIDDEN','INVALID_INPUT'];assert_none(s)
 if variant=='forbidden-secret':assert 'Secret' in error['message'] and error.get('fieldErrors'),'必须实际到达Secret读取边界'
 # 恢复fixture后同一草稿需明确重新预览，平台从未自动补建依赖。
 if variant=='forbidden-secret':s.restore_credentials()
 if variant=='missing-secret':s.apply({'apiVersion':'v1','kind':'Secret','metadata':{'name':'not-created','namespace':s.targetns},'stringData':{'credentials':s.secret_values['credentials']}})
 if variant=='missing-key':s.k('patch','secret','agent-auth','-n',s.targetns,'--type=merge','-p',json.dumps({'stringData':{'not-created':s.secret_values['credentials']}}),target=s.target)
 if variant=='missing-sa':s.apply({'apiVersion':'v1','kind':'ServiceAccount','metadata':{'name':'not-created','namespace':s.targetns}})
 if variant=='insufficient-sa':
  s.apply({'apiVersion':'rbac.authorization.k8s.io/v1','kind':'ClusterRoleBinding','metadata':{'name':s.targetns+'-fix'},'roleRef':{'apiGroup':'rbac.authorization.k8s.io','kind':'ClusterRole','name':'view'},'subjects':[{'kind':'ServiceAccount','name':'prometheus-agent','namespace':s.targetns}]});s.resources.append((s.target,'clusterrolebinding',s.targetns+'-fix'))
 repaired=s.preview(i);assert repaired['draftRevision']==1
 return {'error':error,'restrictedIdentity':rbac,'repairPreviewId':repaired['previewId'],'noCRWorkloadWhileBlocked':True,'dependencyCreatedByTestToolOnly':True}

def freshness(s,case):
 cfg=s.cfg();match=s.collection('ServiceMonitor',s.targetns);cfg['associations']['serviceMonitor']['object']={'mode':'Labels','selector':{'matchLabels':{'mm18-match':'yes'}}};i,op=published(s,cfg);s.runtime(i);s.call('POST','/instances/'+i['id']+'/refresh',{});before=s.sources(i)
 resource='persistentvolumeclaims' if case=='R07.pvc' else 'servicemonitors';source='PVC' if case=='R07.pvc' else 'association:ServiceMonitor';assert before[source]['lastSuccessAt'] and not before[source]['error']
 identity=s.restricted_credentials(resource);current_runtime=s.call('POST','/instances/'+i['id']+'/refresh',{});during=s.sources(i)[source]
 assert current_runtime['summary']=='Available' and during['error'] and during['historical'] and during['lastSuccessAt']==before[source]['lastSuccessAt'] and during['lastAttemptAt']!=before[source]['lastAttemptAt']
 assert during.get('matches')==before[source].get('matches') and during.get('volumes')==before[source].get('volumes')
 s.restore_credentials();s.call('POST','/instances/'+i['id']+'/refresh',{});after=s.sources(i)[source];assert not after['error'] and not after['historical'] and after['lastSuccessAt']!=during['lastSuccessAt']
 return {'operation':op,'source':source,'before':before[source],'during':during,'after':after,'mainRuntimeDuringFailure':current_runtime,'faultIdentity':identity,'matchedUID':match['metadata']['uid']}

def global_fault(s,case):
 if case=='A05.primary':
  classes=s.get('storageclasses')['items'];original=[(c['metadata']['name'],copy.deepcopy(c['metadata'].get('annotations',{}))) for c in classes]
  def restore():
   for name,annotations in original:
    s.k('patch','storageclass',name,'--type=json','-p',json.dumps([{'op':'add','path':'/metadata/annotations','value':annotations}]),target=s.target)
    assert s.get('storageclass',name)['metadata'].get('annotations',{})==annotations
  s.restorers.append(restore)
  for name,annotations in original:
   patch={k:None for k in ['storageclass.kubernetes.io/is-default-class','storageclass.beta.kubernetes.io/is-default-class'] if k in annotations}
   if patch:s.k('patch','storageclass',name,'--type=merge','-p',json.dumps({'metadata':{'annotations':patch}}),target=s.target)
  i=s.create();error=s.preview(i,status=409);assert_none(s);assert s.call('GET','/instances/'+i['id']+'/draft')['revision']==1
  restore();s.restorers.remove(restore);op=s.publish(i);return {'blocked':error,'operationAfterExplicitRetry':op,'restored':True,'runtime':s.runtime(i)}
 # 删除CRD前必须证明数据集群没有其他票/用户的Agent，不能破坏未获授权对象。
 assert not s.get('prometheusagents','-A')['items'],'数据集群仍有Agent，禁止删除CRD'
 crd=s.get('crd','prometheusagents.monitoring.coreos.com')
 obj={k:copy.deepcopy(crd[k]) for k in ['apiVersion','kind','spec']};obj['metadata']={k:v for k,v in crd['metadata'].items() if k in ['name','labels','annotations']};obj['metadata'].get('annotations',{}).pop('kubectl.kubernetes.io/last-applied-configuration',None)
 def restore():
  s.k('apply','--server-side','-f','-',data=obj,target=s.target);s.k('wait','--for=condition=Established','crd/prometheusagents.monitoring.coreos.com','--timeout=90s',target=s.target);assert s.get('crd','prometheusagents.monitoring.coreos.com')['spec']==crd['spec']
 s.restorers.append(restore);s.k('delete','crd','prometheusagents.monitoring.coreos.com','--wait=true','--timeout=90s',target=s.target)
 # 换入相同UID的凭据重新探测能力，证明缺CRD没有使登记身份失效。
 s.restore_credentials();cluster=s.call('GET','/clusters/'+s.cluster['id']);cap=next(c for c in cluster['capabilities'] if c['component']=='Prometheus Agent');assert not cap['allowed'] and cap['reason']
 i=s.create();error=s.preview(i,status=409);assert not s.get('statefulsets,pods','-n',s.targetns)['items'];restore();s.restorers.remove(restore)
 return {'clusterId':cluster['id'],'uid':cluster['uid'],'capability':cap,'blocked':error,'restoredSpecMatches':True}

def ksm(s,cluster,namespace,target,name):
 cfg={'name':name,'targetNamespace':namespace,'deploymentMode':'Deployment','desiredState':'Running','resources':{'requestCPU':'100m','requestMemory':'250Mi','limitMemory':'500Mi'},'collectors':{'mode':'Default'},'metricFilter':{'mode':'All'},'namespaceFilter':{'mode':'All'},'advancedResources':False}
 i=s.call('POST','/clusters/'+cluster['id']+'/instances',{'component':'kube-state-metrics','ksm':cfg},status=201);op=s.publish(i);cr=s.get('kubestatemetrics',name,target=target);s.ksms.append((target,name,cr['metadata']['uid']))
 def ready():
  r=s.call('POST','/instances/'+i['id']+'/refresh',{});return r if r['summary']=='Available' and r.get('availablePods')==1 else None
 return {'operation':op,'runtime':wait(ready,'双集群KSM未就绪'),'cr':cr}

def topology(s):
 proof={}
 for target in ['control','data']:
  operators=s.get('pods','-n','mm-phase1-operators',target=target)['items'];assert operators
  summaries=[]
  for pod in operators:
   assert any(c['type']=='Ready' and c['status']=='True' for c in pod['status']['conditions'])
   summaries.append({'name':pod['metadata']['name'],'uid':pod['metadata']['uid'],'node':pod['spec']['nodeName'],'images':[{'name':c['name'],'image':c['image'],'imageID':next(x['imageID'] for x in pod['status']['containerStatuses'] if x['name']==c['name'])} for c in pod['spec']['containers']]})
  prom=next(p for p in operators if p['metadata']['labels'].get('app.kubernetes.io/name')=='prometheus-operator');text=s.k('get','--raw','/api/v1/namespaces/mm-phase1-operators/pods/'+prom['metadata']['name']+':8080/proxy/metrics',target=target).stdout.decode()
  assert 'version="0.83.0"' in text and 'revision="5cf2f5d' in text and 'prometheus_operator_ready{controller="prometheus-agent"} 1' in text
  crds={k:s.get('crd',k,target=target)['spec']['versions'] for k in ['kubestatemetrics.monitoring.fjsxy.github.io','prometheusagents.monitoring.coreos.com','scrapeconfigs.monitoring.coreos.com']}
  provisioner=s.get('pods','-n','local-path-storage',target=target)['items'];assert provisioner and any('sha256:282f619d10d4dda6c93fe329249dca6155c9deff5e96eef4420b6f6c30ff5a83' in c.get('imageID','') for p in provisioner for c in p['status']['containerStatuses'])
  proof[target]={'operators':summaries,'agentControllerReady':True,'operatorBuildVersion':'0.83.0','operatorBuildRevision':'5cf2f5d','crdVersions':crds,'provisionerRuntimeDigestVerified':True}
 i,op=published(s);return {'environment':s.environment,'dependencies':proof,'operation':op,'agent':s.runtime(i)}

def identity(s):
 first=s.cfg();i,op=published(s,first);a=s.runtime(i);other=s.targetns+'-other';s.fixture(other);b_cfg=s.cfg(namespace=other);j,jop=published(s,b_cfg);b=s.runtime(j,namespace=other)
 data_ns=s.targetns+'-data';s.fixture(data_ns,'data');data,_=s.registered(s.config('data'));k=s.create(s.cfg(namespace=data_ns),data['id']);kop=s.publish(k);c=s.runtime(k,namespace=data_ns,target='data')
 external_name='external-'+uuid.uuid4().hex[:8];external=copy.deepcopy(a['cr']);external={key:external[key] for key in ['apiVersion','kind','spec']};external['metadata']={'name':external_name,'namespace':other};s.apply(external)
 listing=s.call('GET','/clusters/'+s.cluster['id']+'/instances')['instances'];outside=next(v for v in listing if v['name']==external_name and v['readOnly']);assert not s.call('GET','/instances/'+outside['id']+'/operations')['operations']
 s.call('PUT','/instances/'+outside['id']+'/draft',{'expectedDraftRevision':1,'agent':s.cfg(external_name,other)},status=403)
 first['remoteWrite'][0]['url']='http://updated.example.invalid/write';s.save(i,first);s.publish(i)
 assert s.cr(namespace=other)['metadata']['uid']==b['cr']['metadata']['uid'] and s.cr(namespace=other)['spec']==b['cr']['spec'];assert s.cr(namespace=data_ns,target='data')['metadata']['uid']==c['cr']['metadata']['uid'] and s.cr(namespace=data_ns,target='data')['spec']==c['cr']['spec']
 assert len({x['cr']['metadata']['uid'] for x in [a,b,c]})==3 and len({x['pvc']['uid'] for x in [a,b,c]})==3
 return {'instances':[{'id':x['id'],'namespace':x['targetNamespace'],'uid':x['uid']} for x in [s.call('GET','/instances/'+v['id']) for v in [i,j,k]]],'pvcUIDs':[x['pvc']['uid'] for x in [a,b,c]],'external':outside,'otherSpecsUnchanged':True}

def run_case(s,case):
 if case.startswith('A11.'):return association(s,case)
 if case.startswith('A12.'):return remote(s,case)
 if case.startswith('A13.'):return blocked(s,case)
 if case.startswith('R07.'):return freshness(s,case)
 if case in ['A05.primary','C04.agent-crd']:return global_fault(s,case)
 if case=='T01.primary':return topology(s)
 if case=='A03.primary':return identity(s)
 if case=='T03.primary':
  i,op=published(s);a=s.runtime(i);k1=ksm(s,s.cluster,s.targetns,s.target,'ksm-'+uuid.uuid4().hex[:10]);ns=s.targetns+'-data';s.fixture(ns,'data');cluster,_=s.registered(s.config('data'));assert cluster['uid']!=s.cluster['uid'];j=s.create(s.cfg(namespace=ns),cluster['id']);jop=s.publish(j);b=s.runtime(j,namespace=ns,target='data');k2=ksm(s,cluster,ns,'data','ksm-'+uuid.uuid4().hex[:10]);return {'control':{'agent':a,'ksm':k1,'operation':op},'data':{'agent':b,'ksm':k2,'operation':jop},'clusterUIDs':[s.cluster['uid'],cluster['uid']]}
 if case in ['A01.primary','A04.primary','A10.initial-capacity','A10.fixed-capacity','A10.fixed-identity']:
  cfg=s.cfg();capacity='2Gi' if case=='A10.initial-capacity' else '1Gi';cfg['storage']['capacity']=capacity;i,op=published(s,cfg);evidence=s.runtime(i,capacity=capacity);cr=evidence['cr'];assert cr['spec']['shards']==cr['spec']['replicas']==1 and not cr['spec'].get('paused',False)
  if case=='A10.fixed-capacity':
   cfg['storage']['capacity']='3Gi';error=s.save(i,cfg,status=409);assert s.cr()['metadata']['uid']==cr['metadata']['uid'] and s.get('pvc',evidence['pvc']['name'],'-n',s.targetns)['spec']['resources']['requests']['storage']=='1Gi';evidence['rejectedCapacity']=error
  if case=='A10.fixed-identity':
   cfg['name']='renamed';name_error=s.save(i,cfg,status=409);cfg['name']=s.crname;cfg['namespace']=s.targetns+'-other';ns_error=s.save(i,cfg,status=409);assert s.cr()['metadata']['uid']==cr['metadata']['uid'];evidence.update({'rejectedName':name_error,'rejectedNamespace':ns_error})
  return {'operation':op,**evidence}
 raise AssertionError('没有逐例实现：'+case)
