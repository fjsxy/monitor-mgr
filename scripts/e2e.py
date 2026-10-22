#!/usr/bin/env python3
"""make e2e 的唯一入口：准备、逐例 Ginkgo 调度、结果校验和 finally 清理。"""
import argparse
import datetime
import json
import os
from pathlib import Path
import re
import signal
import socket
import sys
import tempfile
import time
import traceback

from e2e_environment import Environment, ROOT, private_json
from testing_contract import validate_specs
from e2e_shards import SHARDS, selection
from e2e_diagnostics import failure_summary


def source():
    import subprocess
    def git(*args): return subprocess.check_output(['git',*args],cwd=ROOT,text=True).strip()
    if git('status','--porcelain'): raise ValueError('E2E 必须从干净候选构建和运行，请先提交实现')
    return git('rev-parse','HEAD')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-id',default='mm-e2e-'+datetime.datetime.now().strftime('%m%d%H%M%S'))
    parser.add_argument('--shard',choices=list(SHARDS),help='仅运行固定 CI 组；省略时运行全部10项')
    parser.add_argument('--cleanup',help='独立按已保存的原身份清理中断运行');parser.add_argument('--output');parser.add_argument('--monitor-op-source');parser.add_argument('--candidate')
    args=parser.parse_args()
    if args.cleanup:
        root=Path(args.cleanup).resolve();saved=json.loads((root/'.private/environment.json').read_text())
        environment=object.__new__(Environment);environment.root=root;environment.private=root/'.private';environment.public=root/'public'
        environment.run_id=saved['runId'];environment.candidate=saved['candidate'];environment.targets=saved['targets']
        from transport import ProcessRunner
        environment.processes=ProcessRunner(environment.private/'cleanup-processes')
        return 1 if environment.cleanup_clusters() else 0
    candidate=source()
    if args.candidate and args.candidate!=candidate:raise ValueError('candidate-mismatch')
    if not re.fullmatch(r'mm-[a-z0-9-]{1,36}',args.run_id):raise ValueError('run-id 必须以 mm- 开头且最长39字符')
    directory=Path(args.output or ROOT/'.scratch/e2e'/args.run_id).resolve()
    environment=Environment(directory,args.run_id,candidate,args.monitor_op_source)
    # 现有 Python helpers 的进程调用进入同一个私有 ProcessRunner，不向 console 泄漏材料。
    from transport import ACTIVE
    token=ACTIVE.set(environment)
    from e2e_scenarios import Scenario, CASES
    required=selection(args.shard)
    assert set(selection())==set(CASES)
    results={id:dict(id=id,outcome='not_run',steps=[]) for id in required}
    summary=dict(schemaVersion=3,kind='e2e-shard' if args.shard else 'e2e-full',shard=args.shard,assigned=required,
                 candidate=candidate,runId=args.run_id,results=results,environment='preparing',cleanup=False,passed=False,timingsSeconds={})
    started=time.monotonic()
    child=None
    def stop(signum,frame):
        signal.signal(signal.SIGTERM,signal.SIG_IGN);signal.signal(signal.SIGINT,signal.SIG_IGN)
        raise InterruptedError('收到停止信号')
    signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
    try:
        # 分阶段记录耗时，后续调整分组可依据公开实测，而不依赖敏感原始日志。
        for name,operation in [('build',environment.build),('clusters',environment.prepare_clusters),('images',environment.load_images),('operators',environment.operators)]:
            stage=time.monotonic();operation();summary['timingsSeconds'][name]=round(time.monotonic()-stage,3)
        summary['environment']='ready'
        with tempfile.TemporaryDirectory(prefix='mm-e2e-') as temporary:
            address=str(Path(temporary)/'scheduler.sock')
            with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as server:
                server.bind(address);os.chmod(address,0o600);server.listen(1);server.settimeout(.2)
                env=dict(os.environ,MONITOR_MGR_E2E_SOCKET=address,MONITOR_MGR_E2E_CASES=json.dumps(required))
                report=environment.private/'ginkgo.json'
                child=environment.processes.popen(['go','test','./tests/e2e','-count=1','-timeout=120m','-args','-ginkgo.no-color','-ginkgo.fail-on-empty','-ginkgo.json-report='+str(report)],label='ginkgo-e2e',cwd=ROOT,env=env)
                seen=set()
                while child.poll() is None:
                    try:connection,_=server.accept()
                    except socket.timeout:continue
                    with connection,connection.makefile('rwb') as stream:
                        request=json.loads(stream.readline(4097));id=request.get('id')
                        if id not in required or id in seen:raise ValueError('unregistered-or-duplicate-case')
                        seen.add(id);result=results[id];result['outcome']='failed'
                        def emit(event):
                            if event['type']=='step':result['steps'].append(event['text'])
                            stream.write(json.dumps(event,ensure_ascii=False).encode()+b'\n');stream.flush()
                        case_started=time.monotonic();scenario=Scenario(environment,int(id[-2:]),emit)
                        try:
                            scenario.by('Helm 部署本例独立平台与 PVC，并经 Service 初始化登录');scenario.deploy()
                            CASES[id](scenario);result['outcome']='passed'
                        except Exception as error:
                            result['failedStep']=scenario.step;result['errorType']=type(error).__name__
                            result['diagnostic']=failure_summary(error)
                            path=environment.private/(id+'-failure.txt');path.write_text(traceback.format_exc());os.chmod(path,0o600)
                        finally:
                            # 失败日志先落私有目录；公开报告只保留步骤/错误类别与关联 ID。
                            try:
                                if hasattr(scenario,'ns'):
                                    log=scenario.k('logs','deployment/monitor-mgr','-n',scenario.ns,check=False).stdout
                                    path=environment.private/(id+'-platform.log');path.write_bytes(log);os.chmod(path,0o600)
                            except Exception:result['diagnosticFailure']=True
                            try:failures=scenario.cleanup()
                            except Exception:
                                failures=['cleanup-exception'];path=environment.private/(id+'-cleanup-failure.txt');path.write_text(traceback.format_exc());os.chmod(path,0o600)
                            result['cleanup']=failures
                            result['durationSeconds']=round(time.monotonic()-case_started,3)
                            if failures:result['outcome']='failed'
                            result['requests']=scenario.traces;result['platformContainers']=scenario.platform_proofs
                            private_json(environment.public/'results.json',summary)
                            emit(dict(type='result',id=id,outcome=result['outcome']))
                            print(id+' '+result['outcome'],flush=True)
                raw=json.loads(report.read_text());specs=[s for suite in raw for s in suite.get('SpecReports',[]) if s.get('LeafNodeType')=='It']
                summary['ginkgo']=[dict(id=s['LeafNodeText'].split(' ')[0],state=s['State'],attempts=s['NumAttempts']) for s in specs]
                validate_specs(raw,required,candidate,source())
                summary['passed']=child.returncode==0 and all(r['outcome']=='passed' for r in results.values())
    except Exception as error:
        summary['errorType']=type(error).__name__
        if summary['environment']=='preparing':summary['environment']='failed'
        path=environment.private/'run-failure.txt';path.write_text(traceback.format_exc());os.chmod(path,0o600)
        print('E2E 执行失败：'+type(error).__name__+'；原始诊断：'+str(path),flush=True)
    finally:
        if child and child.poll() is None:child.terminate();child.wait(timeout=15)
        failures=environment.processes.close_auxiliary()
        failures+=environment.cleanup_clusters();summary['cleanup']=not failures;summary['passed']=summary['passed'] and not failures
        summary['timingsSeconds']['total']=round(time.monotonic()-started,3)
        private_json(environment.public/'results.json',summary);ACTIVE.reset(token)
        print(json.dumps(dict(candidate=candidate,passed=sum(r['outcome']=='passed' for r in results.values()),failed=sum(r['outcome']=='failed' for r in results.values()),not_run=sum(r['outcome']=='not_run' for r in results.values()),cleanup=summary['cleanup'],report=str(environment.public/'results.json')),ensure_ascii=False),flush=True)
    return 0 if summary['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
