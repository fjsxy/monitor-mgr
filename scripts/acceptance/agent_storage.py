#!/usr/bin/env python3
"""#20存储门禁：复用真实Pod/HTTP/Kitex环境，每例独立fixture，逐例保存卷UID结果。"""
import argparse,copy,fcntl,hashlib,json,pathlib,signal,sys,time,urllib.request
from transport import process as subprocess
import agent as base
from ksm import manifest,source_state,fingerprint
from ksm_proxy import Proxy
from lifecycle import Suite as LifecycleSuite

ROOT=pathlib.Path(__file__).resolve().parents[2]
OWNED=['A02.primary','A06.primary','A07.retain','A07.delete','A08.retain','A08.delete','A09.retain','A09.delete','A10.fixed-mode','A10.new-instance','P14.policy-pending','R03.agent-zero']
KOWNED=[v for v in OWNED if manifest()[v]['layer']=='K']
KREG=['REG20-STORAGE-001.'+v for v in ['orphan','forbidden','timeout','dispatch-race','new-name','owned-retain-suspend','owned-retain-shard']]
QREG={
 'REG20-STORAGE-002.'+action:['TestAgentStorageResponsibilityOutlivesInstance/'+action+'-'+p for p in ['Delete','Retain']] for action in ['transfer','unregister']}
QREG.update({'REG20-STORAGE-003.'+v:['TestAgentStoragePreviewImpact/'+v] for v in ['st-uid-accept','st-uid-dispatch','pvc-uid-accept','pvc-uid-dispatch','policy-accept','policy-dispatch','status-rv','same-st-pod-roll']})
QREG.update({'REG20-STORAGE-004.'+v:['TestAgentStorageIndependentOwnerPolicy/'+v] for v in ['scaled-retain-deleted-delete','delete-with-st-owner']})
QREG.update({'REG20-STORAGE-005.pending-'+v:['TestAgentStoragePendingDeleteBlocksRestore/'+v+'/'+p for p in ['Delete','Retain']] for v in ['suspend','shard']})
QOWNED={'R03.agent-zero':['TestR03AgentZeroUsesApplicableStatus/'+v for v in ['valid-zero','missing-status','old-generation']]}

def contracts():
 out=manifest()
 for line in (ROOT/'docs/e2e/issue-20.md').read_text().splitlines():
  cells=[v.strip() for v in line.strip('|').split('|')]
  if len(cells)==5 and cells[0] in KREG+list(QREG):out[cells[0]]={'variantId':cells[0],'caseId':cells[0],'kind':'supplementary','layer':'K' if cells[0] in KREG else 'Q','purpose':cells[1],'steps':cells[2],'expected':cells[3],'profiles':['LOCAL_FULL']}
 assert len(OWNED)==12 and len(KOWNED)==11 and len(KREG)==7 and len(QREG)==14
 assert set(OWNED)=={v.split('|')[1].strip() for v in (ROOT/'docs/e2e/issue-20.md').read_text().splitlines() if v.startswith('| ') and v.split('|')[1].strip() in manifest()}
 return out

def wait(read,ok=lambda v:bool(v),timeout=150,message='实际资源未收敛'):
 end=time.monotonic()+timeout;last=None
 while time.monotonic()<end:
  last=read()
  if ok(last):return last
  time.sleep(.4)
 raise AssertionError(message+': '+json.dumps(last,ensure_ascii=False,default=str))

def absent(s,resource,name):
 result=s.k('get',resource,name,'-n',s.targetns,'-o','json',target=s.target,check=False)
 if result.returncode:
  assert b'(NotFound)' in result.stderr,'读取错误不能当NotFound';return True
 return False

def owned_graph(s,i,expected_shards,replicas,mode='PVC',capacity='1Gi'):
 """实际CR/ST/Pod/PVC UID链、镜像运行链与Ready/mount同时核验。"""
 def read():
  runtime=s.call('POST','/instances/'+i['id']+'/refresh',{});cr=s.cr();uid=cr['metadata']['uid']
  sts=[v for v in s.get('statefulsets','-n',s.targetns)['items'] if any(o.get('uid')==uid and o.get('controller') for o in v['metadata'].get('ownerReferences',[]))]
  owners={v['metadata']['uid'] for v in sts}
  pods=[v for v in s.get('pods','-n',s.targetns)['items'] if any(o.get('uid') in owners and o.get('controller') for o in v['metadata'].get('ownerReferences',[]))]
  desired=expected_shards*replicas
  if len(sts)!=expected_shards or len(pods)!=desired or any(v['spec'].get('replicas',1)!=replicas for v in sts):return None
  if cr['spec'].get('shards',1)!=expected_shards or cr['spec'].get('replicas',1)!=replicas or cr['spec'].get('paused',False):return None
  if runtime.get('desiredPods')!=desired or runtime.get('availablePods')!=desired or runtime['summary']!=('Available' if desired else 'Suspended'):return None
  volumes=[];pod_proofs=[]
  for pod in pods:
   if not any(c['type']=='Ready' and c['status']=='True' for c in pod.get('status',{}).get('conditions',[])):return None
   prom=next(v for v in pod['spec']['containers'] if v['name']=='prometheus');status=next(v for v in pod['status']['containerStatuses'] if v['name']=='prometheus')
   assert prom['resources']=={'requests':{'cpu':'200m','memory':'512Mi'},'limits':{'memory':'1Gi'}}
   image=s.prometheus_image(pod,prom,status,s.target)
   mounts={v['name'] for v in prom.get('volumeMounts',[])};wal=[v for v in pod['spec']['volumes'] if v['name'] in mounts and ('persistentVolumeClaim' in v or 'emptyDir' in v and v['name'].endswith('-db'))]
   if mode=='PVC':
    claims=[v['persistentVolumeClaim']['claimName'] for v in wal if 'persistentVolumeClaim' in v]
    if len(claims)!=1:return None
    pvc=s.get('pvc',claims[0],'-n',s.targetns)
    if pvc.get('status',{}).get('phase')!='Bound':return None
    assert pvc['spec']['resources']['requests']['storage']==capacity and pvc['spec']['accessModes']==['ReadWriteOnce']
    volumes.append({'name':pvc['metadata']['name'],'uid':pvc['metadata']['uid'],'spec':pvc['spec'],'ownerReferences':pvc['metadata'].get('ownerReferences',[]),'phase':pvc['status']['phase']})
   else:assert any('emptyDir' in v and v['name'].endswith('-db') for v in wal),'实际WAL未使用emptyDir'
   pod_proofs.append({'name':pod['metadata']['name'],'uid':pod['metadata']['uid'],'ownerReferences':pod['metadata']['ownerReferences'],'node':pod['spec']['nodeName'],'image':image,'mounts':prom['volumeMounts'],'resources':prom['resources'],'containerStatuses':[{'name':v['name'],'containerID':v.get('containerID'),'restartCount':v.get('restartCount',0)} for v in pod['status']['containerStatuses']]})
  return {'runtime':runtime,'cr':cr,'statefulSets':sts,'pods':pod_proofs,'volumes':volumes}
 return wait(read,message='原Agent实际shards/副本/卷未收敛')

def policies(s,count,scaled,deleted):
 def read():
  cr=s.cr();sts=[v for v in s.get('statefulsets','-n',s.targetns)['items'] if any(o.get('uid')==cr['metadata']['uid'] and o.get('controller') for o in v['metadata'].get('ownerReferences',[]))]
  if len(sts)!=count:return None
  for st in sts:
   p=st['spec'].get('persistentVolumeClaimRetentionPolicy',{})
   if p.get('whenScaled','Retain')!=scaled or p.get('whenDeleted','Retain')!=deleted:return None
  return [{'name':v['metadata']['name'],'uid':v['metadata']['uid'],'policy':v['spec'].get('persistentVolumeClaimRetentionPolicy',{})} for v in sts]
 return wait(read,message='实际ST尚未采用两项策略')

def publish(s,i,overwrite=False):
 p=s.preview(i);o=s.call('POST','/instances/'+i['id']+'/publications',{'draftRevision':p['draftRevision'],'previewId':p['previewId'],'overwriteDrift':overwrite},status=202);result=s.operation(o);assert result['state']=='SUCCEEDED',result;return result

def volume_result(s,op,expected):
 return wait(lambda:s.call('POST','/operations/'+op['operationId']+'/refresh'),lambda v:v.get('storageResult')==expected,message='原PVC独立结果未核实')

def delete(s,i):
 p=s.call('POST','/instances/'+i['id']+'/deletion-previews',{});o=s.call('POST','/instances/'+i['id']+'/deletions',{'versionId':p['versionId'],'previewId':p['previewId']},status=202)
 done=wait(lambda:s.call('POST','/operations/'+o['operationId']+'/refresh'),lambda v:v['state']=='SUCCEEDED',timeout=180,message='原CR/workload/Pod未完成清理')
 assert absent(s,'prometheusagent',s.crname)
 current=s.call('GET','/instances/'+i['id']);assert current['lifecycle']=='DELETED'
 return p,done

def first(s,shards=1,mode='PVC',capacity='1Gi',scaled='Retain',deleted='Retain'):
 cfg=s.cfg();cfg['shards']=shards;cfg['storage'].update({'mode':mode,'whenScaled':scaled,'whenDeleted':deleted})
 if mode=='PVC':cfg['storage']['capacity']=capacity
 else:cfg['storage'].pop('capacity',None)
 i=s.create(cfg);op=s.publish(i);proof=owned_graph(s,i,shards,1,mode,capacity);return i,cfg,op,proof

def storage_case(s,case):
 if case=='A02.primary':
  i,cfg,initial,before=first(s,2);adopted=policies(s,2,'Retain','Retain');cfg['desiredState']='Suspended';s.save(i,cfg);suspend=s.publish(i);zero=owned_graph(s,i,2,0);retained=volume_result(s,suspend,'Retained');cfg['desiredState']='Running';s.save(i,cfg);restore=s.publish(i);after=owned_graph(s,i,2,1)
  assert {v['uid'] for v in before['volumes']}=={v['uid'] for v in after['volumes']};return {'before':before,'adopted':adopted,'suspend':suspend,'zero':zero,'retained':retained,'restore':restore,'after':after}
 if case=='A06.primary':
  i,cfg,initial,before=first(s,mode='emptyDir');assert s.get('pvc','-n',s.targetns)['items']==[]
  pod=before['pods'][0];marker='mm20-'+__import__('uuid').uuid4().hex
  s.k('exec',pod['name'],'-n',s.targetns,'-c','prometheus','--','/bin/sh','-c','printf %s '+marker+' > /prometheus/mm20-emptydir-marker')
  s.k('exec',pod['name'],'-n',s.targetns,'-c','prometheus','--','/bin/sh','-c','kill -TERM 1',check=False)
  def restarted():
   p=s.get('pod',pod['name'],'-n',s.targetns)
   c=next(v for v in p.get('status',{}).get('containerStatuses',[]) if v['name']=='prometheus')
   return p if c.get('restartCount',0)>0 and c.get('ready') else None
  same=wait(restarted);assert same['metadata']['uid']==pod['uid'];assert s.k('exec',pod['name'],'-n',s.targetns,'-c','prometheus','--','cat','/prometheus/mm20-emptydir-marker').stdout.decode()==marker
  cfg['desiredState']='Suspended';s.save(i,cfg);suspend=s.publish(i);zero=owned_graph(s,i,1,0,'emptyDir');cfg['desiredState']='Running';s.save(i,cfg);restore=s.publish(i);after=owned_graph(s,i,1,1,'emptyDir');assert after['pods'][0]['uid']!=pod['uid']
  s.k('exec',after['pods'][0]['name'],'-n',s.targetns,'-c','prometheus','--','/bin/sh','-c','test ! -e /prometheus/mm20-emptydir-marker')
  preview,deletion=delete(s,i);assert s.get('pvc','-n',s.targetns)['items']==[] and s.call('GET','/operations/'+deletion['operationId'])['storageResult']=='NOT_APPLICABLE'
  return {'before':before,'samePodContainerRestartPreservedMarker':True,'suspend':suspend,'zero':zero,'restore':restore,'after':after,'newPodLostEmptyDirMarker':True,'deletePreview':preview,'deletion':deletion,'walPVCCount':0}
 if case.startswith(('A07.','A08.','A09.')):
  policy='Retain' if case.endswith('retain') else 'Delete';count=2 if case.startswith('A09.') else 1
  i,cfg,initial,before=first(s,count);key='whenScaled' if case.startswith('A07.') else 'whenDeleted';cfg['storage'][key]=policy;s.save(i,cfg);policy_op=s.publish(i);adopted=policies(s,count,cfg['storage']['whenScaled'],cfg['storage']['whenDeleted'])
  if case.startswith('A08.'):preview,op=delete(s,i);after={'instance':s.call('GET','/instances/'+i['id'])}
  else:
   if case.startswith('A07.'):cfg['desiredState']='Suspended'
   else:cfg['shards']=1
   s.save(i,cfg);op=s.publish(i);after=owned_graph(s,i,1 if count==2 else 1,1 if count==2 else 0)
  result=volume_result(s,op,'Retained' if policy=='Retain' else 'Deleted')
  old={v['uid'] for v in before['volumes']};remaining=s.get('pvc','-n',s.targetns)['items'];actual={v['metadata']['uid'] for v in remaining}
  if policy=='Retain':assert old<=actual
  elif count==1:assert not old&actual
  else:
   assert len(old&actual)==1 and {v['uid'] for v in after['volumes']}==old&actual
   before_st={v['metadata']['name']:v['metadata']['uid'] for v in before['statefulSets']};assert all(before_st[v['metadata']['name']]==v['metadata']['uid'] for v in after['statefulSets'])
  return {'before':before,'policyOperation':policy_op,'actualAdoption':adopted,'operation':op,'after':after,'independentStorageResult':result,'remainingPVCs':[{'name':v['metadata']['name'],'uid':v['metadata']['uid'],'ownerReferences':v['metadata'].get('ownerReferences',[])} for v in remaining]}
 if case=='A10.fixed-mode':
  i,cfg,op,before=first(s,capacity='2Gi');bad=copy.deepcopy(cfg);bad['storage']={'mode':'emptyDir','whenScaled':'Retain','whenDeleted':'Retain'};mode_error=s.save(i,bad,status=409)
  bad=copy.deepcopy(cfg);bad['storage']['capacity']='3Gi';capacity_error=s.save(i,bad,status=409)
  bad=copy.deepcopy(cfg);bad['name']='different-agent';name_error=s.save(i,bad,status=409);bad=copy.deepcopy(cfg);bad['namespace']='default';namespace_error=s.save(i,bad,status=409)
  assert s.cr()['metadata']['uid']==before['cr']['metadata']['uid'];return {'customCapacity':before,'modeError':mode_error,'capacityError':capacity_error,'nameError':name_error,'namespaceError':namespace_error}
 if case=='A10.new-instance':
  i,cfg,op,before=first(s);template=s.call('GET','/instances/'+i['id']+'/copy-template');assert template['agent']['name']==''
  preview,deletion=delete(s,i);retained=volume_result(s,deletion,'Retained');old_name=s.crname;s.crname=old_name+'-new';cfg=template['agent'];cfg['name']=s.crname;cfg['storage']['capacity']='2Gi';new=s.create(cfg);new_op=s.publish(new);after=owned_graph(s,new,1,1,capacity='2Gi')
  assert new['id']!=i['id'] and after['cr']['metadata']['uid']!=before['cr']['metadata']['uid'] and not {v['uid'] for v in before['volumes']}&{v['uid'] for v in after['volumes']}
  for v in before['volumes']:assert s.get('pvc',v['name'],'-n',s.targetns)['metadata']['uid']==v['uid']
  return {'old':before,'oldDeletion':deletion,'retained':retained,'newInstance':new,'newOperation':new_op,'new':after,'oldRetainVolumesUnmodified':True}
 if case=='P14.policy-pending':return policy_pending(s)
 if case in KREG:return supplemental(s,case)
 raise AssertionError('未实现的#20输入 '+case)


def policy_pending(s):
 i,cfg,op,before=first(s,2)
 # 仅暂停本例Agent调谐，避免Operator马上修回测试工具刻意注入的ST旧策略；Operator进程保持Ready。
 s.k('patch','prometheusagent',s.crname,'-n',s.targetns,'--type=merge','-p',json.dumps({'spec':{'paused':True}}))
 for st in before['statefulSets']:s.k('patch','statefulset',st['metadata']['name'],'-n',s.targetns,'--type=merge','-p',json.dumps({'spec':{'persistentVolumeClaimRetentionPolicy':{'whenScaled':'Delete','whenDeleted':'Delete'}}}))
 actual=policies(s,2,'Delete','Delete');blocked={}
 for action in ['suspend','shard']:
  changed=copy.deepcopy(cfg)
  if action=='suspend':changed['desiredState']='Suspended'
  else:changed['shards']=1
  s.save(i,changed);blocked[action]=s.preview(i,status=409)
 blocked['delete']=s.call('POST','/instances/'+i['id']+'/deletion-previews',{},status=409)
 cfg['storage']['whenScaled']=cfg['storage']['whenDeleted']='Delete';s.save(i,cfg);policy_op=publish(s,i,overwrite=True);adopted=policies(s,2,'Delete','Delete')
 cfg['desiredState']='Suspended';s.save(i,cfg);danger=s.publish(i);zero=owned_graph(s,i,2,0);deleted=volume_result(s,danger,'Deleted')
 return {'before':before,'actualMismatchedPolicies':actual,'blockedIndependently':blocked,'separatePolicyOperation':policy_op,'adopted':adopted,'continued':danger,'zero':zero,'storage':deleted}


def orphan(s):
 claim='prom-agent-'+s.crname+'-db-prom-agent-'+s.crname+'-0'
 s.apply({'apiVersion':'v1','kind':'PersistentVolumeClaim','metadata':{'namespace':s.targetns,'name':claim},'spec':{'accessModes':['ReadWriteOnce'],'resources':{'requests':{'storage':'1Gi'}}}})
 marker='fixture-'+__import__('uuid').uuid4().hex;probe='old-volume-probe'
 s.apply({'apiVersion':'v1','kind':'Pod','metadata':{'namespace':s.targetns,'name':probe},'spec':{'containers':[{'name':'probe','image':'python:3.13.2-alpine','imagePullPolicy':'Never','command':['python','-c','import pathlib,time;pathlib.Path("/work/marker").write_text("'+marker+'");time.sleep(3600)'],'volumeMounts':[{'name':'old','mountPath':'/work'}]}],'volumes':[{'name':'old','persistentVolumeClaim':{'claimName':claim}}]}})
 s.k('wait','--for=condition=Ready','pod/'+probe,'-n',s.targetns,'--timeout=120s');pvc=s.get('pvc',claim,'-n',s.targetns);assert not pvc['metadata'].get('ownerReferences')
 def verify():
  actual=s.get('pvc',claim,'-n',s.targetns);assert actual['metadata']['uid']==pvc['metadata']['uid'] and not actual['metadata'].get('ownerReferences');assert s.k('exec',probe,'-n',s.targetns,'--','cat','/work/marker').stdout.decode()==marker
  return {'name':claim,'uid':pvc['metadata']['uid'],'phase':actual['status']['phase'],'ownerReferences':actual['metadata'].get('ownerReferences',[]),'contentSHA256':hashlib.sha256(marker.encode()).hexdigest(),'contentUnchanged':True}
 return verify


def supplemental(s,case):
 v=case.split('.')[-1]
 if v=='orphan':
  verify=orphan(s);before=verify();i=s.create();error=s.preview(i,status=409);assert absent(s,'prometheusagent',s.crname);return {'orphanBefore':before,'error':error,'orphanAfter':verify(),'noCandidateCR':True}
 if v=='new-name':
  verify=orphan(s);old=verify();s.crname+='-clean';i=s.create();op=s.publish(i);proof=owned_graph(s,i,1,1);assert proof['volumes'][0]['uid']!=old['uid'];return {'oldOrphan':old,'newOperation':op,'new':proof,'oldAfter':verify()}
 if v=='forbidden':
  i=s.create();identity=s.restricted_credentials('persistentvolumeclaims');error=s.preview(i,status=403);assert absent(s,'prometheusagent',s.crname);s.restore_credentials();repaired=s.preview(i);return {'identity':identity,'error':error,'repairPreview':repaired,'noCandidateCR':True}
 if v=='timeout':
  proxy=Proxy(s,s.target);s.proxy=proxy;baseline=proxy.verify_frontend();done=s.operation(s.accept(proxy.config,path='/clusters/'+s.cluster['id']+'/credentials'));assert done['state']=='SUCCEEDED';i=s.create();path='/api/v1/namespaces/'+s.targetns+'/persistentvolumeclaims/prom-agent-'+s.crname+'-db-prom-agent-'+s.crname+'-0';proxy.arm('GET',path);started=time.monotonic();error=s.preview(i,status=502);duration=time.monotonic()-started;state=proxy.control('state');assert state['rule']['claimed'] and duration>=5;proxy.control('reset',{});s.restore_credentials();assert absent(s,'prometheusagent',s.crname);events=proxy.events();(s.out/(case+'.proxy.jsonl')).write_text(events);return {'verifiedProxy':baseline,'error':error,'durationSeconds':duration,'faultState':state,'noCandidateCR':True}
 if v=='dispatch-race':
  i=s.create();p=s.preview(i);s.connect_gate();s.gate('arm',{'stage':'beforeDispatch'});o=s.call('POST','/instances/'+i['id']+'/publications',{'draftRevision':p['draftRevision'],'previewId':p['previewId'],'overwriteDrift':False},status=202);barrier=s.barrier('beforeDispatch');verify=orphan(s);old=verify();s.gate('release',{'stage':'beforeDispatch'});done=s.operation(o);assert done['state']=='FAILED' and absent(s,'prometheusagent',s.crname);return {'applicationBarrier':barrier,'operation':done,'orphanBefore':old,'orphanAfter':verify(),'noCandidateCR':True}
 if v in ['owned-retain-suspend','owned-retain-shard']:
  count=2 if v.endswith('shard') else 1;i,cfg,op,before=first(s,count);adopted=policies(s,count,'Retain','Retain')
  if count==1:cfg['desiredState']='Suspended'
  else:cfg['shards']=1
  s.save(i,cfg);reduced=s.publish(i);middle=owned_graph(s,i,1,0 if count==1 else 1);retained=volume_result(s,reduced,'Retained');cfg['shards']=count;cfg['desiredState']='Running';s.save(i,cfg);restored=s.publish(i);after=owned_graph(s,i,count,1)
  assert {v['uid'] for v in before['volumes']}=={v['uid'] for v in after['volumes']};return {'before':before,'adopted':adopted,'reduced':reduced,'middle':middle,'retained':retained,'restore':restored,'after':after,'sameOriginalPVCUIDsMounted':True}
 raise AssertionError(case)


class Suite(base.Suite):
 def __init__(self,a):super().__init__(a);self.contract=contracts()
 gate=LifecycleSuite.gate
 barrier=LifecycleSuite.barrier
 def connect_gate(self):
  self.gateport=self.port+400;self.gatebase='http://127.0.0.1:'+str(self.gateport)
  self.gateforward=subprocess.Popen([self.a.kubectl,'--kubeconfig',self.a.control_config,'--context',self.a.control_context,'-n',self.ns,'port-forward','pod/'+self.pod,str(self.gateport)+':19091'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
  end=time.monotonic()+10
  while time.monotonic()<end:
   try:self.gate('state');return
   except Exception:time.sleep(.1)
  raise AssertionError('应用fixture入口不可达')
 def cleanup(self):
  # 关键：统一运行只按 Session 原 UID ledger 清理，不能先执行旧诊断恢复或前缀认领。
  if self.session:return self.session.cleanup_case(self)
  # 关键：SIGTERM也走同一finally；代理先恢复、关闭，再按原UID正常GC独占资源。
  failures=[]
  if hasattr(self,'proxy'):
   try:self.proxy.control('reset',{});self.restore_credentials();self.proxy.close()
   except Exception as e:failures.append('独占proxy恢复失败：'+str(e))
   del self.proxy
  if hasattr(self,'gateforward'):
   try:
    gates=self.gate('state')
    if gates:
     # 未完成屏障在清理前关闭平台worker，不能通过释放屏障触发一次迟到写。
     self.k('-n',self.ns,'scale','deployment/monitor-mgr','--replicas=0',target='control')
     self.k('-n',self.ns,'wait','--for=delete','pods','-l','app=monitor-mgr','--timeout=60s',target='control')
    self.gateforward.terminate();self.gateforward.wait(timeout=5)
   except Exception:failures.append('应用fixture清理失败')
   del self.gateforward
  result=super().cleanup();result['failures']+=failures;result['success']=result['success'] and not failures;return result


def quick(a,selected,contract):
 source=source_state();assert not source['dirty'],'Q正式收据要求干净source'
 mapping=QOWNED if a.layer=='Q' else QREG;roots=sorted({v.split('/')[0] for case in selected for v in mapping[case]});command=['go','test','./internal/platform','-count=1','-json','-run','^('+'|'.join(roots)+')$'];started=base.stamp();r=subprocess.run(command,cwd=ROOT,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
 out=pathlib.Path(a.output);out.mkdir(parents=True,exist_ok=True);log=out/'go-test.jsonl';log.write_bytes(r.stdout);passed=set()
 for line in r.stdout.decode().splitlines():
  try:
   event=json.loads(line)
   if event.get('Action')=='pass':passed.add(event.get('Test'))
  except ValueError:pass
 results=[]
 for case in selected:
  record={**contract[case],'kind':'owned' if case in QOWNED else 'supplementary','source':source,'testedCommit':source['commit'],'testedTree':source['tree'],'profile':a.profile,'runnerCommit':source['commit'],'testSelectors':mapping[case],'command':command,'commandExitCode':r.returncode,'startedAt':started,'finishedAt':base.stamp(),'outcome':'passed' if r.returncode==0 and all(v in passed for v in mapping[case]) else 'failed','evidenceRefs':[{'path':log.name,'sha256':hashlib.sha256(r.stdout).hexdigest()}],'executionBoundary':'真实HTTP→生成Kitex→SQLite与受控TLS API','cleanup':{'success':True,'scope':'t.TempDir/listener/屏障按测试finally清理'}}
  (out/(case+'.json')).write_text(json.dumps(record,ensure_ascii=False,indent=2));results.append(record)
 (out/'results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2));print(json.dumps({'executed':len(results),'passed':sum(v['outcome']=='passed' for v in results),'owned':sum(v['kind']=='owned' for v in results)}));return 0 if all(v['outcome']=='passed' for v in results) else 1


def main():
 p=argparse.ArgumentParser();p.add_argument('--layer',choices=['Q','K','REG'],default='K');p.add_argument('--profile',choices=['LOCAL_FULL','CI_FAST','CI_SMOKE'],default='LOCAL_FULL');p.add_argument('--list',action='store_true');p.add_argument('--cases',nargs='*');p.add_argument('--output');p.add_argument('--fail-fast',action='store_true');p.add_argument('--allow-dirty-runner',action='store_true');p.add_argument('--allow-source-bridge',action='store_true')
 for field in ['kubectl','control-config','data-config','control-context','data-context','control-pod-config','data-pod-config','image','fixture-image','source','api-lane']:p.add_argument('--'+field)
 p.add_argument('--prefix',default='mm20-storage');p.add_argument('--port',type=int,default=19100);a=p.parse_args();contract=contracts();valid=KOWNED+KREG if a.layer=='K' else list(QOWNED if a.layer=='Q' else QREG);selected=a.cases or valid
 assert selected and all(v in valid for v in selected),'未知/层级不符子例'
 assert all(a.profile in contract[v]['profiles'] for v in selected),'子例不属于所选profile'
 if a.list:print(json.dumps([{'id':v,'layer':a.layer,'kind':'owned' if v in OWNED else 'supplementary','profiles':contract[v]['profiles']} for v in selected],ensure_ascii=False,indent=2));return 0
 if not a.output:p.error('必须显式output')
 if a.layer!='K':return quick(a,selected,contract)
 if a.profile!='LOCAL_FULL':p.error('当前真实双集群驱动仅LOCAL_FULL；最终CI由统一编排执行')
 for field in ['kubectl','control_config','data_config','control_context','data_context','control_pod_config','data_pod_config','image','source','api_lane']:
  if not getattr(a,field):p.error('K需要显式'+field)
 if 'REG20-STORAGE-001.dispatch-race' in selected and not a.fixture_image:p.error('派发竞态必须明确同来源fixture-image')
 # 进程中止先展开当前case finally，不能在API写后跳过恢复直接退出。
 signal.signal(signal.SIGTERM,lambda signum,frame:(_ for _ in ()).throw(SystemExit(128+signum)))
 base.run_case=storage_case;results=[];out=pathlib.Path(a.output);out.mkdir(parents=True,exist_ok=True)
 with open(a.api_lane,'a') as lane:
  fcntl.flock(lane,fcntl.LOCK_EX)
  for index,case in enumerate(selected):
   current=copy.copy(a);current.cases=[case];current.prefix=a.prefix+'-'+str(index);current.port=a.port+index;current.image=a.fixture_image if case.endswith('dispatch-race') else a.image
   suite=Suite(current);code=suite.run();results+=suite.results;(out/'results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2))
   if code and a.fail_fast:break
 return 0 if len(results)==len(selected) and all(v['outcome']=='passed' for v in results) else 1
if __name__=='__main__':raise SystemExit(main())
