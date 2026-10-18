#!/usr/bin/env python3
"""#15 独占真实 Pod/Kubernetes 验收；敏感 kubeconfig 只在内存及 0600 临时文件中。"""
import argparse,copy,http.cookiejar,json,os,pathlib,secrets,subprocess,time,urllib.error,urllib.request,uuid
import yaml
ROOT=pathlib.Path(__file__).resolve().parents[2]
CASES=['T04.loopback','T04.wrong-ca','T04.wrong-name','C01.client-cert','C01.token','C02.primary','C06.bad-candidate','C08.primary','T06.primary']
class Suite:
 def __init__(self,args):
  self.a=args;self.out=pathlib.Path(args.output);self.out.mkdir(parents=True,exist_ok=True);os.chmod(self.out,0o700)
  self.control=json.load(open(args.control_pod_config));self.data=json.load(open(args.data_pod_config));self.password=secrets.token_urlsafe(24);self.results=[]
 def k(self,*args,data=None,target='control',check=True):
  cfg=self.a.control_config if target=='control' else self.a.data_config
  r=subprocess.run([self.a.kubectl,'--kubeconfig',cfg,*args],input=None if data is None else json.dumps(data).encode(),stdout=subprocess.PIPE,stderr=subprocess.PIPE)
  if check and r.returncode:raise RuntimeError('kubectl '+str(args[:2])+' 失败（敏感参数已隐藏）')
  return r
 def config(self,target='control'):
  c=copy.deepcopy(self.control if target=='control' else self.data);c['contexts'][0]['name']='explicit';c['current-context']='explicit';return c
 def call(self,method,path,body=None,key=None,status=200):
  req=urllib.request.Request(self.base+'/api/v1'+path,data=None if body is None else json.dumps(body).encode(),method=method,headers={'Origin':self.base,'Content-Type':'application/json','Idempotency-Key':key or str(uuid.uuid4())})
  try:r=self.http.open(req,timeout=18)
  except urllib.error.HTTPError as e:r=e
  data=json.load(r)
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
  self.pod=pod['metadata']['name'];self.initial_uid=pod['metadata']['uid'];self.connect()
  self.call('POST','/initialization',{'username':'admin','password':self.password},status=201)
  self.call('POST','/sessions',{'username':'admin','password':self.password},status=201)
  self.owner=self.call('GET','/session')['account']['id']
  return {'podUID':pod['metadata']['uid'],'node':pod['spec']['nodeName'],'imageID':pod['status']['containerStatuses'][0]['imageID'],'source':self.a.source}
 def connect(self):
  self.base='http://127.0.0.1:'+str(self.port)
  self.forward=subprocess.Popen([self.a.kubectl,'--kubeconfig',self.a.control_config,'-n',self.ns,'port-forward','pod/'+self.pod,str(self.port)+':8080'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
  end=time.monotonic()+20
  while time.monotonic()<end:
   try:
    if urllib.request.urlopen(self.base+'/readyz',timeout=1).status==200:break
   except Exception:time.sleep(.2)
  else:raise RuntimeError('真实 Pod port-forward 不可用')
  if not hasattr(self,'cookies'):self.cookies=http.cookiejar.CookieJar()
  self.http=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.cookies))
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
   ns=self.a.prefix+'-token';self.k('create','namespace',ns,target='data');self.token_ns=ns
   self.k('-n',ns,'create','serviceaccount','verifier',target='data');self.k('create','clusterrolebinding',ns,'--clusterrole=cluster-admin','--serviceaccount='+ns+':verifier',target='data')
   token=self.k('-n',ns,'create','token','verifier','--duration=1h',target='data').stdout.decode().strip();cfg=self.config('data');cfg['users']=[{'name':'token-user','user':{'token':token}}];cfg['contexts'][0]['context']['user']='token-user';c,o=self.registered(cfg);assert c['identity']=='system:serviceaccount:'+ns+':verifier';return {'cluster':c,'operation':o}
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
   self.k('-n',self.ns,'scale','deployment/monitor-mgr','--replicas=1');self.k('-n',self.ns,'rollout','status','deployment/monitor-mgr','--timeout=100s');new=json.loads(self.k('-n',self.ns,'get','pods','-l','app=monitor-mgr','-o','json').stdout)['items'][0];self.pod=new['metadata']['name'];self.connect();self.call('GET','/clusters/'+c['id']+'/namespaces');assert self.call('GET','/session')['expiresAt']==session['expiresAt'];after=self.proof('key-after');assert before==after and before['keyUID']==10001 and before['keyMode']=='0o600' and before['integrity']=='ok';assert new['metadata']['uid']!=self.initial_uid;return {'before':before,'after':after,'missingKeyFailed':True,**restored,'rebuildPodUID':new['metadata']['uid'],'sessionExpiryPreserved':True}
  raise AssertionError(case)
 def run(self):
  for index,case in enumerate(self.a.cases or CASES):
   self.cookies=http.cookiejar.CookieJar();self.token_ns=None;record={'variantId':case,'layer':'K','source':self.a.source,'status':'failed'};before={t:self.inventory(t) for t in ['control','data']}
   try:
    record['runtime']=self.deploy(case,index);record['evidence']=self.run_case(case);after={t:self.inventory(t) for t in ['control','data']};assert before==after,'注册/凭据操作创建或修改了观测 CR';record['observabilityInventoryUnchanged']=True;record['status']='passed'
   except Exception as e:record['error']=str(e);raise
   finally:
    if hasattr(self,'forward'):self.forward.terminate();self.forward.wait(timeout=5)
    if hasattr(self,'ns'):
     logs=self.k('-n',self.ns,'logs','deployment/monitor-mgr',check=False).stdout
     (self.out/(case+'.platform.log')).write_bytes(logs)
     self.k('delete','namespace',self.ns,'--wait=true','--timeout=90s',check=False)
    if self.token_ns:self.k('delete','clusterrolebinding',self.token_ns,target='data',check=False);self.k('delete','namespace',self.token_ns,'--wait=true',target='data',check=False)
    (self.out/(case+'.json')).write_text(json.dumps(record,ensure_ascii=False,indent=2));self.results.append(record);(self.out/'results.json').write_text(json.dumps(self.results,ensure_ascii=False,indent=2));print(case,record['status'],flush=True)
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--kubectl',required=True);p.add_argument('--control-config',required=True);p.add_argument('--data-config',required=True);p.add_argument('--control-pod-config',required=True);p.add_argument('--data-pod-config',required=True);p.add_argument('--image',required=True);p.add_argument('--source',required=True);p.add_argument('--output',required=True);p.add_argument('--prefix',default='mm-phase1-clusters-k');p.add_argument('--port',type=int,default=18400);p.add_argument('--cases',nargs='*',choices=CASES);Suite(p.parse_args()).run()
