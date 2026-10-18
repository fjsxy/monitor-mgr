"""工作台真实Chromium编排；业务请求经实际平台Pod，私有控制材料仅留Node fixture。"""
import base64,copy,hashlib,json,os,pathlib,re,subprocess,tempfile,time,urllib.request,uuid
from agent import Suite as AgentSuite
from workbench import stamp
ROOT=pathlib.Path(__file__).resolve().parents[2]

def service_access(s):
 """B层显式经本例Service访问；Pod UID另外绑定，不借用旧Service选择到的离场Pod。"""
 s.forward.terminate();s.forward.wait(timeout=5)
 s.forward=subprocess.Popen([s.a.kubectl,'--kubeconfig',s.a.control_config,'--context',s.a.control_context,'-n',s.ns,'port-forward','service/monitor-mgr',str(s.port)+':8080'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
 deadline=time.monotonic()+15
 while time.monotonic()<deadline:
  try:
   if urllib.request.urlopen(s.base+'/readyz',timeout=1).status==200:return
  except Exception:time.sleep(.2)
 raise AssertionError('本例平台Service不可达')

def large_revision(s,cluster,namespace):
 """仅受限旧库fixture设置大i64初值；原PVC/密钥保留且应用停止时写入，随后通过HTTP验证。"""
 name=s.a.prefix+'-large-i64';i=s.call('POST','/clusters/'+cluster['id']+'/instances',{'component':'kube-state-metrics','ksm':s.ksm_cfg(name,namespace)},status=201)
 cookies=s.cookies;s.forward.terminate();s.forward.wait(timeout=5);del s.forward
 s.k('-n',s.ns,'scale','deployment/monitor-mgr','--replicas=0');s.k('-n',s.ns,'wait','--for=delete','pods','-l','app=monitor-mgr','--timeout=65s')
 script="""import json,sqlite3
c=sqlite3.connect('/data/platform.db');c.execute('pragma foreign_keys=on');r=c.execute('update drafts set revision=? where instance_id=?',(9007199254740992,INSTANCE));assert r.rowcount==1;c.commit();c.close();print(json.dumps({'fixtureUpdated':True,'revision':'9007199254740992'}))""".replace('INSTANCE',repr(i['id']))
 proof=s.job('large-i64-fixture',script);assert proof=={'fixtureUpdated':True,'revision':'9007199254740992'}
 s.k('-n',s.ns,'scale','deployment/monitor-mgr','--replicas=1');s.k('-n',s.ns,'rollout','status','deployment/monitor-mgr','--timeout=100s')
 pods=[p for p in s.get('pods','-n',s.ns,'-l','app=monitor-mgr')['items'] if not p['metadata'].get('deletionTimestamp')];assert len(pods)==1;s.pod=pods[0]['metadata']['name'];s.connect();s.cookies=cookies;s.http=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookies))
 draft=s.call('GET','/instances/'+i['id']+'/draft');assert draft['revision']=='9007199254740992';return i

def prepare(s,case,index):
 s.target='control';s.targetns=s.a.prefix+'-browser-'+str(index);s.crname=s.a.prefix+'-agent-'+str(index)
 s.fixture(s.targetns);proxy=s.use_proxy() if case=='U05.primary' else None;s.cluster=s.register('control',proxy.config if proxy else None)
 fixture={'baseURL':s.base,'adminUsername':'admin','adminPassword':s.password,'clusterId':s.cluster['id'],'clusterName':s.cluster['name'],'namespace':s.targetns,'runId':s.a.prefix}
 evidence={}
 if case=='U01.primary':
  second=s.register('data');fixture.update({'secondClusterId':second['id'],'secondClusterName':second['name']})
 if case=='U05.primary':
  ksm=s.new_ksm(index,cluster=s.cluster,namespace=s.targetns);agent=AgentSuite.create(s);AgentSuite.publish(s,agent);agent_proof=AgentSuite.runtime(s,agent)
  entries=[]
  for instance,component,cpu,cr in [(ksm['instance'],'kube-state-metrics','100m',s.get('kubestatemetrics',ksm['name'])),(agent,'Prometheus Agent','200m',s.cr())]:
   path='/apis/'+cr['apiVersion']+('/namespaces/'+cr['metadata']['namespace'] if cr['metadata'].get('namespace') else '')+'/'+('prometheusagents' if component=='Prometheus Agent' else 'kubestatemetrics')+'/'+cr['metadata']['name']
   entries.append({'id':instance['id'],'component':component,'name':cr['metadata']['name'],'namespace':s.targetns,'expectedRequestCPU':cpu,'expectedSourceDraftRevision':1,'failureControl':{'baseURL':proxy.base,'controlToken':proxy.token,'writePath':path}})
  fixture['versionedInstances']=entries;evidence.update({'actualKSM':ksm['runtimeProof'],'actualAgent':agent_proof})
  external=copy.deepcopy(s.get('kubestatemetrics',ksm['name']));external={k:external[k] for k in ['apiVersion','kind','spec']};external['metadata']={'name':s.a.prefix+'-outside'};s.apply(external)
  actual=s.get('kubestatemetrics',external['metadata']['name']);s.ksms.append(('control',actual['metadata']['name'],actual['metadata']['uid']))
  discovered=s.call('GET','/clusters/'+s.cluster['id']+'/instances')['instances'];readonly=next(v for v in discovered if v['uid']==actual['metadata']['uid'] and v['readOnly']);assert not readonly.get('currentVersionId');fixture['unmanagedInstanceId']=readonly['id']
  large=large_revision(s,s.cluster,s.targetns);fixture.update({'largeRevisionInstanceId':large['id'],'largeRevision':'9007199254740992','expectedLargeRevision':'9007199254740993'})
 else:
  ksm=s.call('POST','/clusters/'+s.cluster['id']+'/instances',{'component':'kube-state-metrics','ksm':s.ksm_cfg(s.a.prefix+'-layout-ksm',s.targetns)},status=201)
  agent=AgentSuite.create(s);fixture['draftInstances']=[{'id':ksm['id'],'component':'kube-state-metrics'},{'id':agent['id'],'component':'Prometheus Agent'}]
 service_access(s);pod=s.get('pod',s.pod,'-n',s.ns);evidence['browserPlatform']={'service':'monitor-mgr','namespace':s.ns,'podUID':pod['metadata']['uid'],'imageID':pod['status']['containerStatuses'][0]['imageID']};return fixture,evidence

def execute(s,selector,fixture):
 out=s.out/'browser'/selector;out.mkdir(parents=True,exist_ok=True);os.chmod(out,0o700);report=out/'playwright.json'
 command=['npm','--prefix','web','run','test:e2e','--','tests/workbench.spec.ts','--grep',r'(?:^|\s)'+re.escape(selector)+' ','--output',str(out/'artifacts')]
 with tempfile.TemporaryDirectory(prefix='mm19-private-browser-') as private:
  path=pathlib.Path(private)/'fixture.json';path.write_text(json.dumps(fixture));os.chmod(path,0o600)
  env=dict(os.environ,BASE_URL=s.base,WORKBENCH_UI_FIXTURE=str(path),PLAYWRIGHT_JSON_OUTPUT_FILE=str(report));result=subprocess.run(command,cwd=ROOT,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
 # 关键：trace固定关闭，stdout/报告/文本附件必须逐值脱敏，发现泄露仍判失败。
 secrets=[s.password,*s.secret_values.values(),*[p.token for p in s.proxies]];leaked=False
 def public(raw):
  nonlocal leaked
  for value in secrets:
   if value in raw:raw=raw.replace(value,'[敏感内容已隐藏]');leaked=True
  return raw
 (out/'browser.log').write_text(public(result.stdout.decode(errors='replace')));assert report.exists(),'浏览器未产生结构化收据'
 text=public(report.read_text());report.write_text(text);data=json.loads(text);tests=[]
 def visit(suites):
  for suite in suites:
   for spec in suite.get('specs',[]):
    if spec['title'].startswith(selector+' '):tests.extend(spec['tests'])
   visit(suite.get('suites',[]))
 visit(data.get('suites',[]));artifacts=out/'evidence';artifacts.mkdir(exist_ok=True)
 if len(tests)==1:
  for attachment in tests[0].get('results',[])[-1].get('attachments',[]):
   name=attachment['name']
   if name.endswith('.json'):
    content=base64.b64decode(attachment['body']).decode() if attachment.get('body') else pathlib.Path(attachment['path']).read_text();(artifacts/pathlib.Path(name).name).write_text(public(content))
 for path in out.rglob('*'):
  if path.is_file() and path.suffix in ['.txt','.md','.json','.log']:
   raw=path.read_text(errors='replace');clean=public(raw)
   if clean!=raw:path.write_text(clean)
 assert not leaked,'浏览器证据出现敏感值，已脱敏但门禁失败'
 assert len(tests)==1 and tests[0]['status']=='expected' and tests[0]['results'][-1]['status']=='passed' and result.returncode==0,'真实浏览器失败，见安全日志'
 assert (artifacts/'browser-console.json').exists() and (artifacts/'browser-layout.json').exists(),'缺少实际浏览器console/layout证据'
 return {'selector':selector,'browserAnnotations':tests[0].get('annotations',[]),'command':command,'exitCode':result.returncode,'trace':'off','report':str(report.relative_to(s.out)),'evidenceRefs':[{'path':str(p.relative_to(s.out)),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in sorted(out.rglob('*')) if p.is_file()]}

def run_browser(s,case,index):
 fixture,evidence=prepare(s,case,index);evidence['browser']=execute(s,case,fixture)
 if case=='U05.primary':
  evidence['i64Browser']=execute(s,'REG19-HTTP-I64-001',fixture)
  record={'variantId':'REG19-HTTP-I64-001','kind':'supplementary','executionScope':'真实Pod浏览器子步骤；Q协议收据独立保存','testedCommit':s.a.source,'runnerCommit':s.source['commit'],'source':s.source,'profile':s.a.profile,'layer':'B','outcome':'passed','evidence':evidence['i64Browser'],'fullRegressionRequiresQReceipt':True,'finishedAt':stamp()}
  (s.out/'REG19-HTTP-I64-001.browser.json').write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n')
 return evidence
