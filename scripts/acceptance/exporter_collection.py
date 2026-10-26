"""#28 真实发布/观察编排：只收集白名单原始证据，业务判据由 Ginkgo 执行。"""
import copy
import json
import time
import uuid
from agent_storage import wait


def _metadata(obj):
    m = obj.get('metadata', {})
    return {key: copy.deepcopy(m[key]) for key in ('name', 'namespace', 'uid', 'resourceVersion', 'generation', 'labels', 'ownerReferences', 'deletionTimestamp') if key in m}


def _object(obj):
    return {key: copy.deepcopy(obj[key]) for key in ('apiVersion', 'kind', 'spec', 'status') if key in obj} | {'metadata': _metadata(obj)}


def _parent(s, instance):
    return s.get('kubestatemetrics', instance['name'], target=s.target)


def _settled(s, instance, state='Ready'):
    # 等待只决定采证时间；完整身份/状态/所有权结论仍由 Go 断言。
    return wait(lambda: _parent(s, instance), lambda cr: cr.get('status', {}).get('serviceMonitor', {}).get('state') == state and cr.get('status', {}).get('serviceMonitor', {}).get('observedGeneration') == cr['metadata']['generation'], timeout=100, message='Operator 配套观察未到达采证阶段 '+state)


def snapshot(s, instance, state='Ready'):
    parent = _settled(s, instance, state)
    s.call('POST', '/instances/' + instance['id'] + '/refresh', {})
    current = s.call('GET', '/instances/' + instance['id'])
    reference = parent.get('status', {}).get('serviceMonitor', {}).get('resource')
    companion = None
    if reference and reference.get('uid'):
        value = s.k('get', 'servicemonitor', reference['name'], '-n', reference['namespace'], '-o', 'json', target=s.target, check=False)
        if value.returncode == 0:
            companion = _object(json.loads(value.stdout))
    services = s.get('services', '-n', s.targetns, target=s.target)['items']
    endpoints = s.get('endpoints', '-n', s.targetns, target=s.target)['items']
    version = s.call('GET', '/instances/' + instance['id'] + '/versions/' + current['currentVersionId']) if current.get('currentVersionId') else None
    sources = [source for source in current.get('sources', []) if source['source'] == 'ServiceMonitor']
    return {'instance': {key: current.get(key) for key in ('id', 'uid', 'name', 'creatorUserId', 'currentVersionId', 'lifecycle')}, 'parent': _object(parent), 'companion': companion, 'sources': sources, 'version': version, 'services': [_object(value) for value in services], 'endpoints': [_object(value) | {'subsets': value.get('subsets', [])} for value in endpoints]}


def topology(s, instance):
    proof = s.acceptance_evidence.setdefault('issue28', {'testedCommit': s.env.candidate, 'topologies': []})
    proof['topologies'].append(snapshot(s, instance))


def lifecycle(s):
    s.by('I28-K01：真实 SM read/no-write SA 发布两个 exporter，审计全部 SM 写尝试')
    s.prepare('-collection', 'control')
    credentials = s.env.platform_credentials(s.target, s.targetns, 'collection-platform')
    config = credentials['config']
    if isinstance(config, str): config = json.loads(config)
    s.operation(s.accept(config, path='/clusters/' + s.cluster['id'] + '/credentials'))
    proof = s.acceptance_evidence.setdefault('issue28', {'testedCommit': s.env.candidate, 'topologies': []})
    proof['credentials'] = {key: credentials[key] for key in ('identity', 'serviceAccountUID', 'identityReview', 'permissions')}
    before = {event['auditID'] for event in s.env.audit_events(s.target)}
    instances = []
    for suffix in ('a', 'b'):
        cfg = s.cfg(name='collection-' + suffix + '-' + uuid.uuid4().hex[:8])
        cfg['resources'] = {'requestCPU': '50m', 'requestMemory': '64Mi', 'limitMemory': '192Mi'}
        instance = s.create(cfg)
        draft = s.call('GET', '/instances/' + instance['id'] + '/draft')
        s.publish(instance)
        instances.append(instance)
        proof.setdefault('initial', []).append(snapshot(s, instance) | {'draft': draft})
    first, second = instances
    second_before = snapshot(s, second)
    first_before = snapshot(s, first)
    cfg = copy.deepcopy(s.call('GET', '/instances/' + first['id'] + '/draft')['ksm'])
    cfg['serviceMonitor'].update(interval='45s', scrapeTimeout='12s')
    s.save(first, cfg); s.publish(first)
    proof['updated'] = snapshot(s, first)
    proof['otherAfterUpdate'] = snapshot(s, second)

    s.by('I28-K01：删除 Operator 已记录的子 UID，等待其自动重建，平台父版本不变')
    reference = proof['updated']['parent']['status']['serviceMonitor']['resource']
    s.delete_uid(s.target, '/apis/monitoring.coreos.com/v1/namespaces/' + reference['namespace'] + '/servicemonitors/' + reference['name'], reference['uid'])
    wait(lambda: _parent(s, first), lambda cr: cr.get('status', {}).get('serviceMonitor', {}).get('state') == 'Ready' and cr.get('status', {}).get('serviceMonitor', {}).get('resource', {}).get('uid') not in ('', None, reference['uid']), timeout=100, message='配套未按新 UID 重建')
    proof['rebuilt'] = snapshot(s, first)

    s.by('I28-K02：外部同名 SM 冲突保留 exporter，由 Operator 恢复而不重发父 CR')
    deployment = s.env.operator_deployments[s.target]
    operator_name, operator_ns = deployment['name'], deployment['namespace']
    s.k('scale', 'deployment/' + operator_name, '-n', operator_ns, '--replicas=0', target=s.target)
    s.k('wait', '--for=delete', 'pods', '-n', operator_ns, '-l', 'app.kubernetes.io/instance=operator', '--timeout=60s', target=s.target, check=False)
    try:
        reference = proof['rebuilt']['parent']['status']['serviceMonitor']['resource']
        s.delete_uid(s.target, '/apis/monitoring.coreos.com/v1/namespaces/' + reference['namespace'] + '/servicemonitors/' + reference['name'], reference['uid'])
        foreign = copy.deepcopy(proof['rebuilt']['companion'])
        foreign['metadata'] = {'name': reference['name'], 'namespace': reference['namespace']}
        foreign.pop('status', None)
        response = json.loads(s.k('create', '-f', '-', '-o', 'json', data=foreign, target=s.target).stdout)
        foreign_uid = response['metadata']['uid']
        s.k('scale', 'deployment/' + operator_name, '-n', operator_ns, '--replicas=1', target=s.target)
        s.k('rollout', 'status', 'deployment/' + operator_name, '-n', operator_ns, '--timeout=100s', target=s.target)
        proof['conflict'] = snapshot(s, first, 'Degraded')
        proof['foreign'] = _object(response)
        s.delete_uid(s.target, '/apis/monitoring.coreos.com/v1/namespaces/' + reference['namespace'] + '/servicemonitors/' + reference['name'], foreign_uid)
        proof['repaired'] = snapshot(s, first)
    finally:
        s.k('scale', 'deployment/' + operator_name, '-n', operator_ns, '--replicas=1', target=s.target)
        s.k('rollout', 'status', 'deployment/' + operator_name, '-n', operator_ns, '--timeout=100s', target=s.target)

    s.by('I28-K01：挂起保留、关闭仅清配套、停用清全部；恢复并按父原 UID 删除')
    cfg['desiredState'] = 'Suspended'
    s.save(first, cfg); s.publish(first); proof['suspended'] = snapshot(s, first)
    cfg['desiredState'] = 'Running'
    cfg['serviceMonitor']['enabled'] = False
    s.save(first, cfg); s.publish(first); proof['closed'] = snapshot(s, first, 'Disabled')
    cfg['desiredState'] = 'Disabled'
    s.save(first, cfg); s.publish(first); proof['disabled'] = snapshot(s, first, 'Disabled')
    cfg['desiredState'] = 'Running'; cfg['serviceMonitor']['enabled'] = True
    s.save(first, cfg); s.publish(first); proof['restored'] = snapshot(s, first)
    preview = s.call('POST', '/instances/' + first['id'] + '/deletion-previews', {})
    deletion = s.call('POST', '/instances/' + first['id'] + '/deletions', {'versionId': preview['versionId'], 'previewId': preview['previewId']}, status=202)
    done = wait(lambda: s.call('POST', '/operations/' + deletion['operationId'] + '/refresh', {}), lambda value: value['state'] == 'SUCCEEDED', timeout=100, message='父 CR 与原配套清理未完成')
    proof['deletion'] = done
    proof['deletedInstance'] = s.call('GET', '/instances/' + first['id'])
    proof['otherFinal'] = snapshot(s, second)
    proof['otherBaseline'] = second_before
    proof['audit'] = [event for event in s.env.audit_events(s.target) if event['auditID'] not in before]
    proof['operatorIdentity'] = deployment['identity']
    proof['operatorProofs'] = s.env.operator_proofs
