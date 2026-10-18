"""Agent真实浏览器编排：调用方持共享API锁，每例新平台/目标fixture，私有参数finally销毁。"""
import hashlib,json,os,pathlib,re,subprocess,tempfile

def run_browser(s,case):
 root=pathlib.Path(__file__).resolve().parents[2];out=s.out/'browser'/case;out.mkdir(parents=True,exist_ok=True);os.chmod(out,0o700)
 fixture={'baseURL':s.base,'adminUsername':'admin','adminPassword':s.password,'clusterId':s.cluster['id'],'kubeconfigPath':s.a.control_pod_config,'kubectlKubeconfigPath':s.a.control_config,'kubeContext':s.a.control_context,'kubectl':s.a.kubectl,'namespace':s.targetns,'serviceAccountName':'prometheus-agent','remoteWriteURL':'http://receiver.example.invalid/write','secretName':'agent-auth','secretKeys':{k:k for k in ['username','password','credentials','ca','cert','key']},'runId':s.a.prefix}
 command=['npm','--prefix','web','run','test:e2e','--','tests/agent.spec.ts','--grep','^'+re.escape(case)+' ','--output',str(out/'artifacts')];report=out/'playwright.json'
 with tempfile.TemporaryDirectory(prefix='mm18-private-browser-') as private:
  path=pathlib.Path(private)/'fixture.json';path.write_text(json.dumps(fixture));os.chmod(path,0o600)
  env=dict(os.environ,BASE_URL=s.base,AGENT_UI_FIXTURE=str(path),PLAYWRIGHT_JSON_OUTPUT_NAME=str(report));env.pop('AGENT_UI_DRAFT_ONLY',None)
  result=subprocess.run(command,cwd=root,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
 # 关键：默认trace关闭；报告和日志仍检查解析值并替换后落盘，不把诊断失败变成秘密泄露。
 public=result.stdout.decode(errors='replace');leaked=False
 values=[s.password,*s.secret_values.values()]
 for value in values:
  if value in public:public=public.replace(value,'[敏感内容已隐藏]');leaked=True
 (out/'browser.log').write_text(public)
 assert report.exists(),'Playwright未产生结构化收据'
 text=report.read_text()
 for value in values:
  if value in text:text=text.replace(value,'[敏感内容已隐藏]');leaked=True
 report.write_text(text);data=json.loads(text);tests=[]
 def visit(suites):
  for suite in suites:
   for spec in suite.get('specs',[]):
    if spec['title'].startswith(case+' '):tests.extend(spec['tests'])
   visit(suite.get('suites',[]))
 visit(data.get('suites',[]));assert len(tests)==1 and tests[0]['status']=='expected' and tests[0]['results'][-1]['status']=='passed' and result.returncode==0,'真实浏览器未通过，见脱敏browser报告'
 assert not leaked,'浏览器输出出现敏感值，已脱敏但门禁失败'
 # 独占fixture可能同时创建KSM；namespace清理前保存其原UID并显式清理cluster-scoped CR。
 ksms=s.get('kubestatemetrics')['items']
 for cr in ksms:
  if cr['spec'].get('targetNamespace')==s.targetns:
   s.ksms.append((s.target,cr['metadata']['name'],cr['metadata']['uid']))
 refs=[{'path':str(p.relative_to(s.out)),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in sorted(out.rglob('*')) if p.is_file()]
 return {'selector':case,'browserAnnotations':tests[0].get('annotations',[]),'report':str(report.relative_to(s.out)),'command':command,'exitCode':result.returncode,'trace':'off','evidenceRefs':refs,'platformPodUID':s.initial_uid}
