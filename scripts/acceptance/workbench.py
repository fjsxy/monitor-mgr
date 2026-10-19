#!/usr/bin/env python3
"""#19工作台逐稳定子例入口；Q经真实HTTP/Kitex，K/B只在显式独占API lane下运行。"""
import argparse,datetime,fcntl,hashlib,json,os,pathlib,platform,re,sys
from transport import process as subprocess

ROOT=pathlib.Path(__file__).resolve().parents[2]
QCASES={
 'R01.intervals':['TestR01WorkbenchIntervals',*[f'TestR01WorkbenchIntervals/{v}' for v in [1,10,20]]],
 'R01.freshness':['TestR01WorkbenchSourceFreshness'],
 'R01.early-error':['TestR01WorkbenchEarlyError'],
 'R03.missing-status':['TestR03WorkbenchMissingStatus'],
 'R03.incomplete-status':['TestR03WorkbenchIncompleteStatus'],
 'R05.page':['TestR05WorkbenchFailedInventoryPage'],
 'R05.item':['TestR05WorkbenchIndependentItems'],
 'R06.primary':['TestR06Workbench'+v for v in ['CoalescesReads','RemainingBudget','ConcurrentBudgets','SubmillisecondBudget','ServerBudgetMaximum',*[f'ServerBudgetMaximum/{v}' for v in ['missing','100000','0','invalid']]]],
}
KCASES=['R02.primary','R04.primary','R07.pod','R08.primary']
BCASES=['U01.primary','U04.primary','U05.primary']
REGCASES={
 'REG19-ACTIVITY-001':['TestWorkbenchActivitySequencePersists'],
 'REG19-HTTP-I64-001':['TestWorkbenchHTTPInt64RoundTrip'],
 'REG19-SOURCE-001':['TestWorkbenchLateObservationAfterUnregister','TestWorkbenchLateObservationAfterCredentialSwitch','TestWorkbenchLateObservationIdentityRead'],
}

def manifest():
 data=json.loads((ROOT/'docs/acceptance/phase1.json').read_text())
 return {v['id']:{'variantId':v['id'],'caseId':c['id'],'layer':c['layer'],'purpose':c['purpose'],'steps':v['steps'],'expected':v['expected'],'profiles':c['profiles']} for c in data['cases'] for v in c['variants']}
def source_state():
 def git(*args):return subprocess.check_output(['git',*args],cwd=ROOT,text=True).strip()
 return {'commit':git('rev-parse','HEAD'),'tree':git('rev-parse','HEAD^{tree}'),'dirty':bool(git('status','--porcelain'))}
def fingerprint(value):return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True).encode()).hexdigest()

def stamp():return datetime.datetime.now(datetime.timezone.utc).isoformat()
def registry():
 """精确校验15责任ID与不可变manifest，补充回归单列不增固定矩阵。"""
 contracts=manifest();ids=re.findall(r'^\| ([A-Z]\d+\.[^ |]+) \|',(ROOT/'docs/e2e/issue-19.md').read_text(),re.M)
 assert len(ids)==len(set(ids))==15 and set(ids)==set(QCASES)|set(KCASES)|set(BCASES),'#19责任子例集合不一致'
 for layer,items in [('Q',QCASES),('K',KCASES),('B',BCASES)]:
  assert all(contracts[v]['layer']==layer for v in items),'责任层级不一致'
 for line in (ROOT/'docs/e2e/issue-19.md').read_text().splitlines():
  cells=[v.strip() for v in line.strip('|').split('|')]
  if len(cells)==5 and cells[0] in REGCASES:contracts[cells[0]]={'variantId':cells[0],'caseId':cells[0],'layer':'Q','kind':'supplementary','purpose':cells[1],'steps':cells[2],'expected':cells[3],'profiles':['LOCAL_FULL']}
 assert all(v in contracts for v in REGCASES)
 return contracts

def run_q(a,contracts,selected,case_tests=None,owned_count=15):
 source=source_state();assert not source['dirty'] or a.allow_dirty_runner,'正式Q来源必须干净'
 if a.profile=='CI_FAST':assert sys.platform=='linux' and platform.machine() in ['amd64','x86_64'],'CI_FAST必须在实际Linux amd64运行'
 selected_tests={v:(case_tests if case_tests is not None else REGCASES if a.layer=='REG' else QCASES)[v] for v in selected}
 roots=sorted({test.split('/')[0] for tests in selected_tests.values() for test in tests})
 command=['go','test','./internal/platform','-count=1','-json','-run','^('+'|'.join(roots)+')$']
 out=pathlib.Path(a.output);out.mkdir(parents=True,exist_ok=True);os.chmod(out,0o700)
 started=stamp();result=subprocess.run(command,cwd=ROOT,stdout=subprocess.PIPE,stderr=subprocess.STDOUT);log=out/'go-test.jsonl';log.write_bytes(result.stdout)
 passed,skipped=set(),set()
 for line in result.stdout.splitlines():
  try:event=json.loads(line)
  except ValueError:continue
  if event.get('Action')=='pass':passed.add(event.get('Test'))
  if event.get('Action')=='skip':skipped.add(event.get('Test'))
 records=[]
 for variant,selectors in selected_tests.items():
  no_skip=not any(test==root or test.startswith(root+'/') for test in skipped if test for root in {s.split('/')[0] for s in selectors})
  record={**contracts[variant],'profile':a.profile,'manifestHash':hashlib.sha256((ROOT/'docs/acceptance/phase1.json').read_bytes()).hexdigest(),'contractFingerprint':fingerprint(contracts[variant]),'source':source,'testedCommit':source['commit'],'testedTree':source['tree'],'runnerCommit':source['commit'],'processLocation':'host','runtimeOS':sys.platform,'runtimeArch':platform.machine(),'startedAt':started,'finishedAt':stamp(),'testSelectors':selectors,'command':command,'commandExitCode':result.returncode,'noSkippedRequiredTests':no_skip,'outcome':'passed' if result.returncode==0 and no_skip and all(s in passed for s in selectors) else 'failed','evidenceRefs':[{'path':log.name,'sha256':hashlib.sha256(result.stdout).hexdigest()}],'cleanup':{'success':True,'scope':'Go测试finally关闭真实HTTP/Kitex/TLS listeners及t.TempDir'}}
  if variant=='REG19-HTTP-I64-001':record.update({'executionScope':'Q协议子步骤；完整REG另需真实浏览器B保留值证据','fullRegressionComplete':False})
  (out/(variant+'.json')).write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n');records.append(record)
 (out/'results.json').write_text(json.dumps(records,ensure_ascii=False,indent=2)+'\n')
 success=bool(records) and all(r['outcome']=='passed' for r in records)
 print(json.dumps({'layer':a.layer,'profile':a.profile,'passed':sum(r['outcome']=='passed' for r in records),'total':len(records),'ownedCount':owned_count,'success':success},ensure_ascii=False))
 return 0 if success else 1

def main():
 p=argparse.ArgumentParser();p.add_argument('--layer',choices=['Q','K','B','REG'],default='Q');p.add_argument('--profile',choices=['LOCAL_FULL','CI_FAST','CI_SMOKE'],default='LOCAL_FULL');p.add_argument('--list',action='store_true');p.add_argument('--cases',nargs='*');p.add_argument('--output');p.add_argument('--allow-dirty-runner',action='store_true');p.add_argument('--allow-source-bridge',action='store_true');p.add_argument('--fail-fast',action='store_true')
 for name in ['kubectl','control-config','data-config','control-context','data-context','control-pod-config','data-pod-config','image','source','api-lane']:p.add_argument('--'+name)
 p.add_argument('--prefix',default='mm19-workbench');p.add_argument('--port',type=int,default=18900)
 a=p.parse_args();contracts=registry();valid=list({'Q':QCASES,'K':KCASES,'B':BCASES,'REG':REGCASES}[a.layer])
 selected=a.cases if a.cases is not None else [v for v in valid if a.profile in contracts[v]['profiles']]
 if not selected or len(selected)!=len(set(selected)) or any(v not in valid or a.profile not in contracts[v]['profiles'] for v in selected):p.error('未知/重复/层级或profile不符子例')
 if a.list:print(json.dumps([{**contracts[v],'testSelectors':(REGCASES if a.layer=='REG' else QCASES).get(v,[v])} for v in selected],ensure_ascii=False,indent=2));return 0
 if not a.output:p.error('必须显式output')
 if a.layer in ['Q','REG']:return run_q(a,contracts,selected)
 for field in ['kubectl','control_config','data_config','control_context','data_context','control_pod_config','data_pod_config','image','source','api_lane']:
  if not getattr(a,field):p.error('真实K/B必须显式'+field)
 if a.profile!='LOCAL_FULL':p.error('当前双目标driver仅执行LOCAL_FULL；CI_SMOKE由最终单目标编排调用浏览器合同')
 a.cases=selected
 from workbench_cases import Suite
 # 关键：禁止在其他票的独占故障/清理中准备资源；锁覆盖本批次准备、执行和自然finally。
 with open(a.api_lane,'a') as lane:
  fcntl.flock(lane,fcntl.LOCK_EX);return Suite(a).run()

if __name__=='__main__':raise SystemExit(main())
