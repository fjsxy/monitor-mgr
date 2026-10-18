#!/usr/bin/env python3
"""唯一租约内恢复原进程及原 UID；旧 run 只读，新报告不产生产品通过键。"""
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import time
import uuid

import gate
from kube_transport import KubeTransport
from ledger import Ledger
from prepare import cleanup_environment
from session import BatchSession, atomic_json, stamp
from transport import ProcessRunner

ROOT = Path(__file__).resolve().parents[2]


class RecoveryError(ValueError):
    """对外仅报告固定错误码，不暴露旧配置或原始子进程诊断。"""


class PersistenceStop(BaseException):
    """关键：持久化失败必须穿透旧恢复器的 except Exception，阻止下一次 mutation。"""


class RecoveryLedger(Ledger):
    def flush(self):
        try:super().flush()
        except Exception:raise PersistenceStop('recovery-ledger-fsync-failed') from None


def require(value, code):
    if not value:raise RecoveryError(code)


def durable(path, value):
    try:atomic_json(path, value)
    except Exception:raise PersistenceStop('recovery-report-fsync-failed') from None


def environment_receipt(report, targets, failure):
    """公开清理结果仅投影已核身份；私有 descriptor、命令及诊断不能进入 artifact。"""
    source = report.get('source')
    results = report.get('environment', {}).get('targets', {})
    return dict(schemaVersion=1, kind='environment-cleanup', executionBoundary='recovery', productGate=False,
                runId=report['runId'], candidateCommit=report['candidateCommit'],
                executionRunId=report.get('executionRunId'), environmentOwnerRunId=report.get('environmentOwnerRunId'),
                source={key: source[key] for key in ['commit', 'tree', 'dirty']} if source else None,
                success=bool(report['success'] and failure is None), state=report['state'],
                errorCode=report.get('errorCode'), startedAt=report['startedAt'], finishedAt=report.get('finishedAt'),
                targets={role: dict(clusterUID=uid, success=results.get(role, {}).get('success') is True,
                                    absent=results.get(role, {}).get('absent') is True,
                                    errorCode=results.get(role, {}).get('error', report.get('errorCode')))
                         for role, uid in targets.items()})


def read_input(root, relative):
    """固定旧 run 根内逐级拒绝 symlink；恢复不能从任意旁路文件认领身份。"""
    relative = Path(relative)
    require(not relative.is_absolute() and all(p not in ['', '.', '..'] for p in relative.parts), 'recovery-path-escape')
    path = root
    for part in relative.parts:
        path = path / part
        require(not path.is_symlink(), 'recovery-input-symlink')
    require(path.resolve().is_relative_to(root), 'recovery-path-escape')
    return path.read_bytes()


def source_state(execute, root=ROOT):
    """实际 Git stdout/stderr 先私有捕获；SHA/tree/dirty 不由调用方声明代填。"""
    def git(*args):
        result = execute(['git', *args], cwd=root, label='recovery-source', check=True)
        return result.stdout.decode().strip()
    return dict(commit=git('rev-parse', 'HEAD'), tree=git('rev-parse', 'HEAD^{tree}'),
                dirty=bool(git('status', '--porcelain')))


def descriptor_input(original, supplied):
    """优先最终私有 descriptor；部分 prepare 仅沿已持久快照，request不能补 UID。"""
    provided = {}
    if supplied and Path(supplied).exists():
        path = Path(supplied)
        require(not path.is_symlink(), 'recovery-input-symlink')
        provided = gate.json_bytes(path.read_bytes())
    path = original / '.private/environment.json'
    if path.exists():descriptor = gate.json_bytes(read_input(original, '.private/environment.json'))
    else:
        descriptor = None
        if (original / '.private/environment-ledger.jsonl').exists():
            for line in read_input(original, '.private/environment-ledger.jsonl').splitlines():
                if line.strip():
                    event = gate.json_bytes(line)
                    if isinstance(event.get('descriptor'), dict):descriptor = event['descriptor']
        if descriptor is None:
            descriptor = copy.deepcopy(provided)
            # 关键：外部request只能声明计划边界，不能补造从未持久观测的容器/集群UID。
            for target in descriptor.get('targets', {}).values():
                target.pop('containers', None);target.pop('clusterUID', None)
    require(isinstance(descriptor, dict) and descriptor.get('targets') is not None, 'recovery-descriptor-missing')
    if provided and provided is not descriptor:
        for field in ['profile', 'runId', 'apiLane']:
            if field in provided and field in descriptor:
                require(provided[field] == descriptor[field], 'recovery-descriptor-conflict')
        require(set(provided.get('targets', {})) == set(descriptor['targets']), 'recovery-descriptor-conflict')
        for name, target in provided.get('targets', {}).items():
            for field in ['name', 'context', 'kubeconfig', 'clusterUID', 'containers']:
                if field in target and field in descriptor['targets'][name]:
                    require(target[field] == descriptor['targets'][name][field], 'recovery-descriptor-conflict')
    return copy.deepcopy(descriptor)


def source_header(value, candidate, tree=None):
    require(all(value.get(k) == candidate for k in ['candidateCommit', 'testedCommit', 'runnerCommit'])
            and value.get('dirty') is False and all(value.get(k) in [None, False]
                for k in ['sourceBridge', 'source_bridge', 'bridge']), 'recovery-original-source-mismatch')
    if tree is not None:require(value.get('testedTree') == tree, 'recovery-original-source-mismatch')
    if 'source' in value:
        source = value['source']
        require(source.get('commit') == candidate and source.get('dirty') is False and
                (tree is None or source.get('tree') == tree) and all(source.get(k) in [None, False]
                    for k in ['sourceBridge', 'source_bridge', 'bridge']), 'recovery-original-source-mismatch')


def target_map(value):
    require(isinstance(value, list), 'recovery-targets-invalid')
    require(len({t.get('name') for t in value}) == len(value), 'recovery-targets-invalid')
    return {t['name']: t['clusterUID'] for t in value}


def clone_ledger(original, relative, destination, candidate, targets, run_id, baselines=True):
    """Ledger.open读原身份，基线按原hash复制；只让新的私有副本接收恢复阶段写入。"""
    raw = read_input(original, relative)
    path = original / relative
    source = Ledger.open(path)
    require(source.data.get('candidateCommit') == candidate and source.data.get('targets') == targets
            and source.data.get('runId') == run_id,
            'recovery-ledger-source-or-target-mismatch')
    for item in source.entries.values():
        require(item.get('target') in targets and item.get('clusterUID') == targets[item['target']], 'recovery-ledger-target-mismatch')
    destination.mkdir(parents=True, exist_ok=False, mode=0o700)
    for entry in source.entries.values():
        if baselines and entry.get('baseline'):
            reference = entry['baseline']
            data = gate.Evidence(path.parent).read(dict(reference, kind='safe-json', visibility='private'))
            private = destination / reference['path'];private.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with os.fdopen(os.open(private, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb') as stream:
                stream.write(data);stream.flush();os.fsync(stream.fileno())
    durable(destination / 'ledger.json', gate.json_bytes(raw))
    return RecoveryLedger.open(destination / 'ledger.json'), source.data.get('processes', {})


def merge_processes(records, events):
    """双持久出口仅允许同一进程的阶段推进，不能用另一PID或出生身份覆盖原 writer。"""
    require(isinstance(events, dict), 'recovery-process-manifest-invalid')
    for identifier, event in events.items():
        require(isinstance(event, dict) and event.get('id', identifier) == identifier, 'recovery-process-manifest-invalid')
        old = records.get(identifier, {})
        for field in ['pid', 'birth', 'pgid']:
            if old.get(field) is not None and event.get(field) is not None:
                require(old[field] == event[field], 'recovery-process-manifest-conflict')
        require(not (old.get('stage') == 'exited' and event.get('stage') not in ['exited', None]), 'recovery-process-manifest-conflict')
        records[identifier] = {**old, **event}


def process_inventory(execute):
    """只读取PID/PGID/出生时间和状态；原始ps同样进入新run私有捕获。"""
    result = execute(['ps', '-axo', 'pid=,pgid=,lstart=,stat='], label='recovery-process-identities', check=True)
    identities = {}
    for line in result.stdout.decode().splitlines():
        parts = line.split()
        if len(parts) == 8 and parts[0].isdigit() and parts[1].isdigit() and not parts[-1].startswith('Z'):
            identities[int(parts[0])] = dict(pgid=int(parts[1]), birth=' '.join(parts[2:7]))
    return identities


def stop_processes(records, execute, persist, grace=1):
    """原 PID+birth+独立 PGID 核实后停组；leader已走也不能盲杀同号新组。"""
    report = {'success': True, 'processes': []}
    for identifier, item in records.items():
        state = {'id': hashlib.sha256(str(identifier).encode()).hexdigest(), 'success': False}
        report['processes'].append(state)
        pid = item.get('pid')
        if pid in [os.getpid(), os.getppid()]:
            state['errorCode'] = 'process-is-active-recovery-coordinator';report['success'] = False
            persist(report);continue
        if not isinstance(pid, int) or pid <= 1:
            if item.get('stage') == 'exited':state.update(success=True, state='exited')
            else:state['errorCode'] = 'process-identity-unconfirmed';report['success'] = False
            persist(report);continue
        # ProcessRunner始终start_new_session；旧事件未独存pgid时，其原组必须实际仍等于原pid。
        pgid = item.get('pgid', pid);state.update(pid=pid, pgid=pgid)
        inventory = process_inventory(execute)
        group = {p: v['birth'] for p, v in inventory.items() if v['pgid'] == pgid}
        current = inventory.get(pid)
        if current is None and not group:
            state.update(success=True, state='absent');persist(report);continue
        birth = ' '.join(str(item.get('birth', '')).split())
        if not birth or not current or current['birth'] != birth or current['pgid'] != pgid or pgid != pid:
            state['errorCode'] = 'process-birth-or-group-unconfirmed';report['success'] = False;persist(report);continue
        state.update(state='stop-intent', members=[{'pid': p, 'birth': b} for p, b in group.items()])
        # 关键：停止意图与整组原身份先fsync；失败就不发信号，也不继续资源删除。
        persist(report)
        try:
            os.killpg(pgid, signal.SIGTERM)
            end = time.monotonic() + grace
            remaining = {p: v for p, v in inventory.items() if v['pgid'] == pgid}
            while time.monotonic() < end:
                remaining = {p: v for p, v in process_inventory(execute).items() if v['pgid'] == pgid}
                if not remaining:break
                time.sleep(.05)
            if remaining:
                require(any(p in group and group[p] == v['birth'] for p, v in remaining.items()), 'process-group-replaced')
                os.killpg(pgid, signal.SIGKILL)
                end = time.monotonic() + 5
                while time.monotonic() < end:
                    remaining = {p: v for p, v in process_inventory(execute).items() if v['pgid'] == pgid}
                    if not remaining:break
                    time.sleep(.05)
            require(not remaining, 'process-group-still-running')
            state.update(success=True, state='exited')
        except Exception:
            state['errorCode'] = 'process-stop-unconfirmed';report['success'] = False
        persist(report)
    return report


def recover(args):
    """run.py recover的唯一调用入口；返回exit并只向console输出固定安全摘要。"""
    session = None;failure = None;report = None;verified_targets = {}
    try:
        original = Path(args.output).resolve();output = Path(args.recovery_output)
        require(output.is_absolute() and original.is_dir(), 'recovery-output-invalid')
        output = output.resolve()
        require(not output.is_relative_to(original) and not original.is_relative_to(output)
                and not output.is_relative_to(ROOT.resolve()), 'recovery-output-overlaps-source')
        candidate = args.candidate
        session = BatchSession(output, expected=[], run_id='recovery-' + uuid.uuid4().hex[:16], profile='recovery',
                               candidate=candidate, inherited_lane_fd=getattr(args, 'inherited_lane_fd', None))
        session.index.update(kind='recovery', executionBoundary='recovery', productGate=False)
        session.flush()
        report = dict(schemaVersion=1, kind='recovery', executionBoundary='recovery', productGate=False,
                      runId=session.index['runId'], candidateCommit=candidate, success=False, state='pending',
                      startedAt=stamp(), identityGaps=[], resources=[], processes={'success': False}, environmentOnly=bool(args.environment_only))
        durable(output / 'recovery.json', report)
        private = output / '.private';private.mkdir(mode=0o700)
        new_processes = {}
        def process_event(event):
            new_processes[event['id']] = event;durable(private / 'processes.json', new_processes)
        runner = ProcessRunner(private / 'raw', checkpoint=session.checkpoint, event=process_event)
        descriptor = descriptor_input(original, args.environment_json)
        profile = descriptor.get('profile');expected = {'control', 'data'} if profile == 'LOCAL_FULL' else {'control'} if profile == 'CI_SMOKE' else set() if profile == 'CI_FAST' else None
        require(expected is not None and set(descriptor['targets']) == expected, 'recovery-targets-invalid')
        for target in descriptor['targets'].values():
            require(re.fullmatch(r'mm[a-z0-9-]{1,51}', target.get('name', '')) is not None
                    and target.get('context') == 'kind-' + target['name'] and
                    Path(target.get('kubeconfig', '')).is_absolute(), 'recovery-target-identity-invalid')
        known = [t['clusterUID'] for t in descriptor['targets'].values() if t.get('clusterUID')]
        require(len(known) == len(set(known)), 'recovery-targets-alias')
        require(descriptor.get('candidate', descriptor.get('source')) == candidate and
                descriptor.get('source', candidate) == candidate, 'recovery-original-source-mismatch')
        session.lane_path = descriptor.get('apiLane')
        require(isinstance(session.lane_path, str) and Path(session.lane_path).is_absolute(), 'recovery-lane-missing')
        session.start()
        actual = source_state(runner.run)
        require(re.fullmatch('[0-9a-f]{40}', candidate or '') is not None and actual['commit'] == candidate and not actual['dirty'], 'recovery-actual-source-mismatch')
        report['source'] = actual;session.index.update(testedCommit=actual['commit'], runnerCommit=actual['commit'], testedTree=actual['tree'], dirty=actual['dirty'])
        targets = {k: v.get('clusterUID') for k, v in descriptor['targets'].items()}
        execution_run_id = descriptor.get('runId')
        manifests = []
        if (original / 'index.json').exists():
            index = gate.json_bytes(read_input(original, 'index.json'));source_header(index, candidate, actual['tree'])
            require(isinstance(index.get('runId'), str) and bool(index['runId']) and index.get('profile') == profile, 'recovery-original-run-mismatch')
            execution_run_id = index['runId']
            recorded = index.get('environment', {}).get('targets', [])
            if recorded:require(target_map(recorded) == targets, 'recovery-original-target-mismatch')
            if ((index.get('executionBoundary') == 'acceptance' and recorded) or
                    'executionRunId' in index or 'environmentOwnerRunId' in index):
                require(index.get('executionRunId') == execution_run_id and
                        index.get('environmentOwnerRunId') == descriptor.get('runId'), 'recovery-environment-owner-mismatch')
        else:index = None
        if (original / 'case-runs.json').exists():
            manifest = gate.json_bytes(read_input(original, 'case-runs.json'));source_header(manifest, candidate, actual['tree'])
            require(isinstance(manifest.get('runId'), str) and bool(manifest['runId']) and
                    (index is None or manifest['runId'] == execution_run_id) and manifest.get('profile') == profile
                    and target_map(manifest.get('targets')) == targets, 'recovery-case-manifest-mismatch')
            execution_run_id = manifest['runId']
            manifests = manifest.get('entries', [])
            require(isinstance(manifests, list), 'recovery-case-manifest-mismatch')
        elif index and index.get('caseRuns'):manifests = index['caseRuns']
        # 关键：执行run与环境创建owner可不同；复用同C环境不能改写原owner/cluster标签。
        report.update(executionRunId=execution_run_id, environmentOwnerRunId=descriptor.get('runId'))
        verified_targets = targets
        paths = [('ledger.json', execution_run_id)] if (original / 'ledger.json').exists() else []
        process_roots = [original]
        for entry in manifests:
            relative = entry.get('ledgerPath');case = entry.get('output')
            require(isinstance(relative, str) and isinstance(case, str) and relative == case + '/ledger.json'
                    and re.fullmatch('[0-9a-f]{64}', entry.get('requestSHA256', '')) is not None
                    and entry.get('stage') in ['intent', 'exited'], 'recovery-case-manifest-mismatch')
            read_input_path = original / relative
            require(not Path(relative).is_absolute() and '..' not in Path(relative).parts, 'recovery-path-escape')
            request_path = entry.get('requestPath')
            require(isinstance(request_path, str), 'recovery-case-request-missing')
            request_bytes = read_input(original, request_path)
            require(hashlib.sha256(request_bytes).hexdigest() == entry['requestSHA256'], 'recovery-case-request-changed')
            request = gate.json_bytes(request_bytes)
            require(request.get('entry') == {k: entry[k] for k in ['variantId', 'layer']}
                    and request.get('candidate') == candidate and request.get('tree') == actual['tree']
                    and request.get('profile') == profile and request.get('parentRunId') == execution_run_id
                    and request.get('apiLane') == session.lane_path and isinstance(request.get('supplemental'), bool)
                    and isinstance(request.get('runId'), str) and bool(request['runId'])
                    and Path(request.get('output', '')).is_absolute()
                    and Path(request['output']).resolve() == (original / case).resolve()
                    and request.get('environment', {}).get('targets') == descriptor['targets']
                    and request['environment'].get('runId') == descriptor.get('runId')
                    and request['environment'].get('profile') == profile
                    and request['environment'].get('source', candidate) == candidate, 'recovery-case-request-mismatch')
            process_roots.append(original / case)
            if read_input_path.exists():paths.append((relative, request['runId']))
            else:report['identityGaps'].append({'variantId': entry.get('variantId'), 'layer': entry.get('layer'), 'errorCode': 'case-ledger-missing'})
        require(bool(paths) or bool(args.environment_only), 'recovery-ledger-missing')
        ledgers, processes = [], {}
        for i, (relative, run_id) in enumerate(dict.fromkeys(paths)):
            ledger, events = clone_ledger(original, relative, private / ('ledger-' + str(i)), candidate, targets, run_id,
                                          baselines=not args.environment_only)
            ledgers.append(ledger);merge_processes(processes, events)
        for root in process_roots:
            path = root / '.private/processes.json'
            if path.exists():
                relative = path.relative_to(original);events = gate.json_bytes(read_input(original, relative))
                merge_processes(processes, events)
        with session.cleanup_phase():
            report['processes'] = stop_processes(processes, runner.run, lambda value: durable(private / 'process-recovery.json', value))
            require(report['processes']['success'], 'recovery-processes-incomplete')
            if args.environment_only:
                environment_events = []
                def environment_event(event):
                    try:
                        environment_events.append(event)
                        atomic_json(private / 'environment-recovery.json', environment_events)
                    except Exception:raise PersistenceStop('recovery-environment-ledger-fsync-failed') from None
                report['environment'] = cleanup_environment(descriptor, execute=runner.run, ledger=environment_event)
                require(report['environment']['success'], 'recovery-environment-incomplete')
            else:
                require(all(targets.values()), 'recovery-cluster-uid-unknown')
                kubectl = descriptor.get('kubectl', 'kubectl');kubectl = [kubectl] if isinstance(kubectl, str) else kubectl
                for ledger in ledgers:
                    kube = KubeTransport(ledger=ledger, execute=runner.run, targets=descriptor['targets'], kubectl=kubectl, checkpoint=session.checkpoint)
                    # 所有原clusterUID先核完，再允许第一条资源mutation。
                    for role, uid in targets.items():
                        namespace = kube.get_obj(role, dict(apiVersion='v1', kind='Namespace', namespace='', name='kube-system'))
                        require(namespace is not None and namespace['metadata']['uid'] == uid, 'recovery-actual-cluster-uid-mismatch')
                    result = kube.recover_case();report['resources'].append(result)
                    durable(output / 'recovery.json', report)
                require(all(r['success'] for r in report['resources']) and not report['identityGaps'], 'recovery-resources-incomplete')
        report.update(success=True, state='completed')
    except BaseException as error:
        failure = error
        code = str(error) if isinstance(error, (RecoveryError, PersistenceStop)) else error.code if isinstance(error, gate.GateError) else 'recovery-failed'
        if report is not None:
            interrupted = bool(session and session.signals)
            report.update(success=False, state='interrupted' if interrupted else 'failed',
                          errorCode='recovery-interrupted' if interrupted else code)
    finally:
        if session is not None:
            with session.cleanup_phase():
                try:
                    if report is not None:report['finishedAt'] = stamp();durable(session.out / 'recovery.json', report)
                except BaseException as error:
                    failure = error
                    if report is not None:report.update(success=False, state='failed', errorCode='recovery-report-fsync-failed')
                if report is not None and report['environmentOnly']:
                    try:
                        # 关键：workflow只上传此白名单文件；失败也必须留安全结果，绝不借清理变成产品passing。
                        durable(session.out / 'public/environment-cleanup.json', environment_receipt(report, verified_targets, failure))
                    except BaseException as error:
                        failure = error;report.update(success=False, state='failed', errorCode='recovery-report-fsync-failed')
                try:session.close(failure, cleanup={'success': bool(report and report['success'] and failure is None), 'scope': '独立恢复；原run不改'})
                except BaseException as error:
                    failure = error
                    if report is not None:
                        report.update(success=False, state='failed', errorCode='recovery-report-fsync-failed')
                        if report['environmentOnly']:
                            try:durable(session.out / 'public/environment-cleanup.json', environment_receipt(report, verified_targets, failure))
                            except BaseException:pass
    success = bool(report and report['success'] and failure is None)
    print(json.dumps(dict(kind='recovery', productGate=False, runId=report['runId'] if report else None,
                          success=success, exit=0 if success else 1,
                          errorCode=report.get('errorCode') if report else 'recovery-output-invalid')))
    return 0 if success else 1


if __name__ == '__main__':raise SystemExit('recovery仅由run.py recover调用')
