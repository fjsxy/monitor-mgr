#!/usr/bin/env python3
"""提前评审补充REG独立入口；保留原15责任来源，不修改固定manifest或状态机。"""
import argparse,fcntl,json,pathlib
from workbench import ROOT,run_q

REPLAY_LEAVES=['current-state','unauthenticated','revoked-role','disabled','no-second-verification']
def replay(actions):
 return [f'TestReviewCredentialReplayCurrent/{action}/{state}/{leaf}' for action in actions for state in ['SUCCEEDED','FAILED','WAITING_CONFIRMATION'] for leaf in [*REPLAY_LEAVES,*(['changed-content'] if action!='CONTINUE' else [])]]
QCASES={
 'REG19-STORAGE-SCOPE-001':['TestReviewStorageScope/identity-failure'],
 'REG19-STORAGE-SCOPE-002':['TestReviewStorageScope/pvc-success'],
 'REG19-HTTP-DELETE-001':['TestReviewDeleteRoute/'+v for v in ['draft','confirmed','missing-preview','stale-impact','replaced-uid','other-owner','idempotent']],
 'REG19-HTTP-CONTINUE-PUBLISH-001':['TestReviewContinueRoute/PUBLISH/'+v for v in ['unsent','wrong-revision','other-owner','possibly-sent']],
 'REG19-HTTP-CONTINUE-DELETE-001':['TestReviewContinueRoute/DELETE/'+v for v in ['unsent','wrong-revision','other-owner','possibly-sent','stale-preview']],
 'REG19-HTTP-CONTINUE-CREDENTIAL-001':replay(['CONTINUE']),
 'REG19-HTTP-CAPABILITIES-001':['TestReviewCapabilitiesRoute/'+v for v in ['admin','owner','other-owner','disabled','unauthenticated','missing']],
 'REG19-CREDENTIAL-REPLAY-001':replay(['REGISTER','CREDENTIAL','REACTIVATE']),
 'REG19-CONTINUE-REPLAY-001':replay(['CONTINUE']),
}
BCASES=['REG19-AGENT-DELETE-OP-001','REG19-AGENT-HISTORY-OP-001']

def registry():
 contracts={}
 for line in (ROOT/'docs/e2e/issue-19.md').read_text().splitlines():
  c=[v.strip() for v in line.strip('|').split('|')]
  if len(c)==5 and c[0] in [*QCASES,*BCASES]:
   contracts[c[0]]={'variantId':c[0],'caseId':c[0],'layer':'Q' if c[0] in QCASES else 'B','kind':'supplementary','purpose':c[1],'steps':c[2],'expected':c[3],'profiles':['LOCAL_FULL']}
 assert set(contracts)==set(QCASES)|set(BCASES),'评审补充计划与registry不一致'
 return contracts

def main():
 p=argparse.ArgumentParser();p.add_argument('--layer',choices=['Q','B'],default='Q');p.add_argument('--profile',choices=['LOCAL_FULL'],default='LOCAL_FULL');p.add_argument('--list',action='store_true');p.add_argument('--cases',nargs='*');p.add_argument('--output');p.add_argument('--fail-fast',action='store_true')
 for name in ['kubectl','control-config','data-config','control-context','data-context','control-pod-config','data-pod-config','image','source','api-lane']:p.add_argument('--'+name)
 p.add_argument('--prefix',default='mm19-review');p.add_argument('--port',type=int,default=19200)
 a=p.parse_args();a.allow_dirty_runner=False;a.allow_source_bridge=False;contracts=registry();valid=QCASES if a.layer=='Q' else BCASES;selected=a.cases if a.cases is not None else list(valid)
 if not selected or len(set(selected))!=len(selected) or any(v not in valid for v in selected):p.error('未知/重复或层级不符子例')
 if a.list:print(json.dumps([{**contracts[v],'testSelectors':QCASES.get(v,[v])} for v in selected],ensure_ascii=False,indent=2));return 0
 if not a.output:p.error('必须显式output')
 if a.layer=='Q':return run_q(a,contracts,selected,QCASES,owned_count=0)
 for field in ['kubectl','control_config','data_config','control_context','data_context','control_pod_config','data_pod_config','image','source','api_lane']:
  if not getattr(a,field):p.error('真实B必须显式'+field)
 a.cases=selected
 from workbench_cases import Suite
 # 关键：沿同一双目标环境与原UID清理基类；锁覆盖构造、准备、浏览器和全部自然finally。
 with open(a.api_lane,'a') as lane:
  fcntl.flock(lane,fcntl.LOCK_EX);s=Suite(a);s.contract.update(contracts);return s.run()
if __name__=='__main__':raise SystemExit(main())
