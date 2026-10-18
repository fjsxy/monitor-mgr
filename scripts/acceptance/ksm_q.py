"""Q层逐稳定子例收据；同一次真实Go/Kitex执行可支持分别断言的多个子例，不改写profile或架构。"""
import hashlib,json,pathlib,platform,subprocess,sys

def run_q(a,registry,contracts,root,source,stamp):
 out=pathlib.Path(a.output);out.mkdir(parents=True,exist_ok=True);cases=a.cases or list(registry);selected={i:registry[i] for i in cases};profile=a.profile;started=stamp()
 if profile=='CI_FAST' and (sys.platform!='linux' or platform.machine() not in ['x86_64','amd64']):raise SystemExit('CI_FAST需要实际Linux amd64执行，当前宿主不能冒充')
 if source['dirty'] and not a.allow_dirty_runner:raise SystemExit('Q来源必须干净；诊断运行需明确allow-dirty-runner且不作为门禁')
 roots=sorted({s.split('/')[0] for s in selected.values()});command=['go','test','./internal/platform','-count=1','-json','-run','^('+'|'.join(roots)+')$'];r=subprocess.run(command,cwd=root,stdout=subprocess.PIPE,stderr=subprocess.STDOUT);log=out/'go-test.jsonl';log.write_bytes(r.stdout);events=[]
 for line in r.stdout.splitlines():
  try:events.append(json.loads(line))
  except ValueError:pass
 passed={e.get('Test') for e in events if e.get('Action')=='pass'};results=[]
 for id,selector in selected.items():
  record={**contracts[id],'profile':profile,'testedCommit':source['commit'],'runnerCommit':source['commit'],'source':source,'processLocation':'host','runtimeOS':sys.platform,'runtimeArch':platform.machine(),'startedAt':started,'finishedAt':stamp(),'testSelector':selector,'command':command,'commandExitCode':r.returncode,'outcome':'passed' if r.returncode==0 and selector in passed else 'failed','assertionTest':selector,'evidenceRefs':[{'path':log.name,'sha256':hashlib.sha256(r.stdout).hexdigest()}],'cleanup':{'success':True,'scope':'Go t.TempDir、TLS test servers、真实Kitex listeners在测试内关闭'}}
  (out/(id+'.json')).write_text(json.dumps(record,ensure_ascii=False,indent=2));results.append(record)
 (out/'results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2));print(json.dumps({'layer':'Q','profile':profile,'passed':sum(x['outcome']=='passed' for x in results),'total':len(results),'exitCode':r.returncode}));return r.returncode
