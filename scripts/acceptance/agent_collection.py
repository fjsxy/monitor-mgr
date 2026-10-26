"""#30 真实采集编排只返回原始观测，业务不变式由 Ginkgo/Gomega 判定。"""
import copy
import hashlib
import json
import re
import time
import urllib.parse
import uuid
import yaml
from agent import Suite as AgentSuite
from agent_storage import wait
from e2e_environment import ROOT, LOCK, RUN_LABEL
from exporter_collection import snapshot, _object
from ksm_proxy import Proxy

USER_LABEL = 'monitor-mgr.io/user-id'
SCOPE_LABEL = 'monitor-mgr.test/exporters'


def _explicit(config):
    """平台入口显式选context；保留同一受限SA，不回退管理员凭据。"""
    value = copy.deepcopy(json.loads(config) if isinstance(config, str) else config)
    selected = next(c for c in value['contexts'] if c['name'] == value['current-context'])
    selected['name'] = 'explicit'; value['contexts'] = [selected]; value['current-context'] = 'explicit'
    return value


def _api(s, namespace, pod, path, target='data'):
    return json.loads(s.k('get', '--raw', '/api/v1/namespaces/'+namespace+'/pods/'+pod+':9090/proxy'+path, target=target).stdout)


def _adopted(s, namespace, pod, target='data'):
    value = _api(s, namespace, pod, '/api/v1/status/config', target=target)
    if value.get('status') != 'success': raise RuntimeError('实际Prometheus进程配置不可读')
    return yaml.safe_load(value['data']['yaml'])


def _process(s, namespace, pod, target='data'):
    actual = s.get('pod', pod, '-n', namespace, target=target)
    return s.env.verify_container(actual, 'prometheus', s.env.official_configs['prometheus'])


def _configuration(s, namespace, pod, target='data'):
    actual = _adopted(s, namespace, pod, target)
    targets = _api(s, namespace, pod, '/api/v1/targets?state=active', target)
    if targets.get('status') != 'success' or not isinstance(targets.get('data', {}).get('activeTargets'), list):
        raise RuntimeError('实际Prometheus进程targets不可读')
    label_keys = {'namespace', 'service', 'endpoint', 'instance', 'pod', 'job'}
    discovery_keys = {'__meta_kubernetes_pod_uid', '__meta_kubernetes_namespace', '__meta_kubernetes_service_name', '__meta_kubernetes_pod_name', '__address__'}
    active = [{key: value for key, value in item.items() if key in ('scrapePool', 'health', 'lastScrape', 'scrapeUrl')} |
              {'labels': {key: value for key, value in item.get('labels', {}).items() if key in label_keys},
               'discoveredLabels': {key: value for key, value in item.get('discoveredLabels', {}).items() if key in discovery_keys}} for item in targets['data']['activeTargets']]
    # 本专用Agent只使用None认证；公开结果不输出任意Secret值。
    return {'process': _process(s, namespace, pod, target), 'configSHA256': hashlib.sha256(yaml.safe_dump(actual).encode()).hexdigest(),
            'externalLabels': actual.get('global', {}).get('external_labels', {}),
            'jobs': [value['job_name'] for value in actual.get('scrape_configs', [])],
            'remoteWrite': [value['url'] for value in actual.get('remote_write', [])],
            'scrapeConfigs': actual.get('scrape_configs', []), 'activeTargets': active}


def _source(value):
    # HTTP对空matches省略optional字段；proof显式保留空数组，使Go可拒绝缺失/null证据。
    return dict(value, matches=value.get('matches', []))


def wait_associations(s, instance, timeout=90):
    return wait(lambda: s.association_preview(instance), lambda sources: not any(value.get('error') for value in sources.values()),
                timeout=timeout, message='公开Agent依赖预检在有限时间内未收敛')


def _selector(selector):
    """仅把实际原生LabelSelector交给API server，名称/标签/端口规划仍属于Operator。"""
    values = [key+'='+value for key, value in selector.get('matchLabels', {}).items()]
    for expression in selector.get('matchExpressions', []):
        key, operator = expression['key'], expression['operator']
        if operator in ('In', 'NotIn'):
            values.append(key+(' in (' if operator == 'In' else ' notin (')+','.join(expression['values'])+')')
        else: values.append(('!' if operator == 'DoesNotExist' else '')+key)
    return ','.join(values)


def _agent_identity(s, instance, namespace, name, target='data'):
    current = s.call('GET', '/instances/'+instance['id'])
    cr = s.get('prometheusagent', name, '-n', namespace, target=target)
    workloads = s.get('statefulsets', '-n', namespace, target=target)['items']
    owned = {value['metadata']['uid'] for value in workloads if any(o.get('controller') and o.get('uid') == cr['metadata']['uid'] for o in value['metadata'].get('ownerReferences', []))}
    pods = [value for value in s.get('pods', '-n', namespace, target=target)['items'] if not value['metadata'].get('deletionTimestamp') and any(o.get('controller') and o.get('uid') in owned for o in value['metadata'].get('ownerReferences', []))]
    if len(pods) != 1 or not any(c['type'] == 'Ready' and c['status'] == 'True' for c in pods[0].get('status', {}).get('conditions', [])): return None
    return {'instance': {key: current.get(key) for key in ('id', 'uid', 'name', 'creatorUserId', 'currentVersionId')}, 'cr': _object(cr),
            'version': s.call('GET', '/instances/'+instance['id']+'/versions/'+current['currentVersionId']),
            'workloads': [_object(value) for value in workloads if value['metadata']['uid'] in owned], 'pod': _object(pods[0]),
            'container': s.env.verify_container(pods[0], 'prometheus', s.env.official_configs['prometheus'])}


def _operator(s):
    dep = s.get('deployment', 'prometheus-operator', '-n', 'mm-operators', target='data')
    selector = _selector(dep['spec']['selector'])
    pod = next(value for value in s.get('pods', '-n', 'mm-operators', '-l', selector, target='data')['items'] if not value['metadata'].get('deletionTimestamp'))
    before = s.env.verify_container(pod, 'prometheus-operator', s.env.official_configs['promop'])
    def metrics():
        raw = s.k('get', '--raw', '/api/v1/namespaces/mm-operators/pods/'+pod['metadata']['name']+':8080/proxy/metrics', target='data').stdout.decode()
        out = []
        for line in raw.splitlines():
            parts = line.split()
            if len(parts) == 2 and (parts[0].startswith('prometheus_operator_ready{') or parts[0].startswith('prometheus_operator_kubernetes_client_http_request_duration_seconds_count{') and 'scrapeconfigs' in parts[0]):
                out.append({'metric': parts[0], 'value': float(parts[1])})
        return out
    observed = wait(metrics, lambda values: any('controller="prometheus-agent"' in v['metric'] and v['value'] == 1 for v in values) and any('scrapeconfigs' in v['metric'] and v['value'] > 0 for v in values), timeout=90, message='原Prometheus Operator进程能力未收敛')
    after = s.env.verify_container(s.get('pod', pod['metadata']['name'], '-n', 'mm-operators', target='data'), 'prometheus-operator', s.env.official_configs['promop'])
    return {'before': before, 'after': after, 'metrics': observed}


def exporter_targets(s, instance, started_at):
    """独立复用：采集已发布父实例的status/SM/Service/全部Endpoints/源Pod原始身份。"""
    parent = s.get('kubestatemetrics', instance['name'], target=s.target)
    ns = parent['spec']['targetNamespace']
    previous = s.targetns
    s.targetns = ns
    try:
        observed = snapshot(s, instance)
        pods = wait(lambda: s.get('pods', '-n', ns, target=s.target)['items'], lambda values: bool(values) and all(any(c['type'] == 'Ready' and c['status'] == 'True' for c in v.get('status', {}).get('conditions', [])) for v in values), timeout=100, message='exporter原Pod未Ready')
        sm = observed['companion']; selector = _selector(sm['spec']['selector'])
        services = s.get('services', '-n', ns, '-l', selector, target=s.target)['items']
        # Pod Ready与Endpoints更新是不同观察；等待实际Service selector选中的全部Pod进入Ready地址，避免截到首副本。
        for service in services:
            pod_selector = _selector({'matchLabels': service['spec']['selector']})
            selected = {p['metadata']['uid'] for p in s.get('pods', '-n', ns, '-l', pod_selector, target=s.target)['items'] if not p['metadata'].get('deletionTimestamp')}
            wait(lambda: s.get('endpoints', service['metadata']['name'], '-n', ns, target=s.target),
                 lambda value: selected and {a.get('targetRef', {}).get('uid') for subset in value.get('subsets', []) for a in subset.get('addresses', [])} == selected,
                 timeout=90, message='Service实际全部Ready Pod尚未进入Endpoints')
        observed = snapshot(s, instance)
        targets = []
        for service in services:
            endpoints = s.get('endpoints', service['metadata']['name'], '-n', ns, target=s.target)
            for index, scrape in enumerate(sm['spec']['endpoints']):
                for subset in endpoints.get('subsets', []):
                    for port in subset['ports']:
                        if port['name'] != scrape['port']: continue
                        for address in subset.get('addresses', []):
                            ref = address['targetRef']
                            targets.append({'namespace': ns, 'service': service['metadata']['name'], 'serviceUID': service['metadata']['uid'], 'endpointUID': endpoints['metadata']['uid'], 'endpoint': port['name'], 'instance': address['ip']+':'+str(port['port']), 'pod': ref['name'], 'podUID': ref['uid'], 'job': 'serviceMonitor/'+sm['metadata']['namespace']+'/'+sm['metadata']['name']+'/'+str(index)})
        # 关键：按真实全部Endpoints采证，不能假定默认一个副本或一个样本；KSM每个endpoint可能报告namespace的多个Pod。
        return {'snapshot': observed, 'startedAt': started_at, 'pods': [_object(p) for p in pods], 'workloads': [_object(v) for v in s.get('deployments,replicasets,statefulsets,daemonsets', '-n', ns, target=s.target)['items']],
                'containers': [s.env.verify_container(p, p['spec']['containers'][0]['name'], s.env.official_configs['ksm']) for p in pods], 'targets': targets}
    finally:
        s.targetns = previous


def receiver_query(s, namespace, receiver_pod, expression, at=None, target='data'):
    """独立复用：真实receiver Pod proxy查询，只保留指标白名单与原始value。"""
    params = {'query': expression}
    if at is not None: params['time'] = at
    response = _api(s, namespace, receiver_pod, '/api/v1/query?'+urllib.parse.urlencode(params), target=target)
    if response.get('status') != 'success' or response.get('data', {}).get('resultType') != 'vector': raise RuntimeError('receiver查询失败或非vector')
    allowed = {'__name__', 'namespace', 'service', 'exported_pod', 'uid', 'instance', 'prometheus', 'pod', 'endpoint', 'job'}
    return [{'metric': {key: value for key, value in row['metric'].items() if key in allowed}, 'value': row['value']} for row in response['data']['result']]


def creator_collection(s):
    s.by('I30-K01：独占空scrape receiver、真实SM只读平台SA与两个创建用户')
    s.prepare('-creator-collection', 'data', 'agent')
    namespace, cluster = s.targetns, s.cluster
    credentials = s.env.platform_credentials('data', namespace, 'creator-platform')
    config = _explicit(credentials['config'])
    s.operation(s.accept(config, path='/clusters/'+cluster['id']+'/credentials'))
    proof = {'testedCommit': s.env.candidate, 'runId': s.env.run_id, 'metric': 'kube_pod_info', 'groups': [],
             'credentials': {key: credentials[key] for key in ('identity', 'serviceAccountUID', 'identityReview', 'permissions')},
             'operatorIdentity': s.env.operator_deployments['data']['identity'], 'operatorProofs': s.env.operator_proofs, 'operatorContent': s.env.operator_content, 'runtimeConfigs': s.env.official_configs}
    baseline = {event['auditID'] for event in s.env.audit_events('data')}
    target = s.env.targets['data']; receiver = 'creator-receiver'
    s.env.command(['helm', 'upgrade', '--install', receiver, ROOT/'charts/test-receiver', '--kubeconfig', target['kubeconfig'], '--kube-context', target['context'], '--namespace', namespace, '--set-string', 'image='+LOCK['images']['prometheus']['image'], '--set-string', 'runId='+s.env.run_id, '--wait', '--timeout', '120s'], 'helm-creator-receiver', timeout=150)
    receiver_pod = s.get('pods', '-n', namespace, '-l', 'app='+receiver, target='data')['items'][0]
    receiver_name = receiver_pod['metadata']['name']
    def receiver_state():
        cfg = _adopted(s, namespace, receiver_name)
        return {'process': _process(s, namespace, receiver_name), 'scrapeConfigs': cfg.get('scrape_configs', []), 'remoteWrite': cfg.get('remote_write', []), 'configSHA256': hashlib.sha256(yaml.safe_dump(cfg).encode()).hexdigest(), 'args': receiver_pod['spec']['containers'][0]['args']}
    def monitor_operator():
        deployment = s.env.operator_deployments['data']
        dep = s.get('deployment', deployment['name'], '-n', deployment['namespace'], target='data')
        pod = next(p for p in s.get('pods', '-n', deployment['namespace'], '-l', _selector(dep['spec']['selector']), target='data')['items'] if not p['metadata'].get('deletionTimestamp'))
        return s.env.verify_container(pod, dep['spec']['template']['spec']['containers'][0]['name'], s.env.operator_config)
    proof['monitorOpBefore'] = monitor_operator()
    proof['receiverBefore'] = receiver_state()
    proof['receiverInitialSamples'] = receiver_query(s, namespace, receiver_name, 'kube_pod_info')
    url = 'http://'+receiver+'.'+namespace+'.svc:9090/api/v1/write'; proof['receiverURL'] = url
    admin_id = s.call('GET', '/session')['account']['id']; admin_client = s.http
    user, user_client = s.user('creator-collection')
    s.call('POST', '/clusters/'+cluster['id']+'/transfer', {'ownerId': user['id']})
    def context(component, ns, name):
        s.component = component; s.target = 'data'; s.targetns = ns; s.crname = name; s.cluster = cluster
    def exporter(role, number, client):
        ns = s.a.prefix+'-'+role+'-export'+str(number); s.namespace(ns, 'data')
        s.k('label', 'namespace', ns, SCOPE_LABEL+'='+s.a.prefix, target='data')
        name = role+'-exporter-'+uuid.uuid4().hex[:8]; context('ksm', ns, name)
        cfg = s.cfg(); cfg['collectors'] = {'mode': 'Include', 'names': ['pods']}
        cfg['resources'] = {'requestCPU': '50m', 'requestMemory': '64Mi', 'limitMemory': '192Mi'}
        cfg['serviceMonitor'] = {'enabled': True, 'interval': '10s', 'scrapeTimeout': '5s'}
        started = time.time()
        with s.using(client): instance = s.create(cfg); s.publish(instance)
        return exporter_targets(s, instance, started)
    def query(expression, at=None): return receiver_query(s, namespace, receiver_name, expression, at)
    def expression(labels): return 'kube_pod_info{'+','.join(key+'='+json.dumps(value) for key, value in labels.items())+'}'
    def adopted(group):
        jobs = {target['job'] for exported in group['exports'] for target in exported['targets']}
        count = sum(len(exported['targets']) for exported in group['exports'])
        return wait(lambda: _configuration(s, namespace, group['identity']['pod']['metadata']['name']),
                    lambda value: set(value['jobs']) == jobs and len(value['activeTargets']) == count and all(t['health'] == 'up' for t in value['activeTargets']),
                    timeout=120, message='Agent原进程未采用精确配套jobs或实际targets未收敛')
    def samples(group):
        prometheus = group['adopted']['externalLabels']['prometheus']; observations = []
        for exported in group['exports']:
            for endpoint in exported['targets']:
                for pod in exported['pods']:
                    labels = {'namespace': endpoint['namespace'], 'service': endpoint['service'], 'instance': endpoint['instance'], 'endpoint': endpoint['endpoint'], 'exported_pod': pod['metadata']['name'], 'uid': pod['metadata']['uid'], 'prometheus': prometheus}
                    expr = expression(labels)
                    rows = wait(lambda: query(expr), lambda values: len(values) == 1, timeout=120, message='未收到实际Endpoint/PodUID绑定的kube_pod_info')
                    times = query('timestamp('+expr+')', rows[0]['value'][0])
                    observations.append({'query': labels, 'results': rows, 'timestampResults': times, 'notBefore': max(group['publishedAt'], exported['startedAt']), 'receiverPodUID': proof['receiverBefore']['process']['podUID']})
        return observations
    def negatives(group, other):
        # 关键：动态前后分别实际查询，不能用最终负样本倒推新增前的隔离结果。
        identity = group['adopted']['externalLabels']['prometheus']
        return [{'query': {'prometheus': identity, 'namespace': e['snapshot']['companion']['metadata']['namespace'], 'uid': p['metadata']['uid']},
                 'results': query(expression({'prometheus': identity, 'namespace': e['snapshot']['companion']['metadata']['namespace'], 'uid': p['metadata']['uid']})), 'observedAt': time.time()} for e in other['exports'] for p in e['pods']]
    exports = {role: exporter(role, 1, client) for role, client in [('user', user_client), ('admin', admin_client)]}
    for role, client, creator in [('user', user_client, user['id']), ('admin', admin_client, admin_id)]:
        name = role+'-agent-'+uuid.uuid4().hex[:8]; context('agent', namespace, name); cfg = s.cfg()
        cfg['remoteWrite'] = [{'url': url, 'auth': {'mode': 'None'}}]
        with s.using(client):
            instance = s.create(cfg); initial = s.call('GET', '/instances/'+instance['id']+'/draft')
            cfg['associations']['serviceMonitor']['object'] = {'mode': 'Labels', 'selector': {'matchLabels': {USER_LABEL: creator}}}
            s.save(instance, cfg); own = _source(wait_associations(s, instance)['association:ServiceMonitor'])
            cfg['associations']['serviceMonitor']['namespace'] = {'mode': 'Labels', 'selector': {'matchLabels': {RUN_LABEL: s.env.run_id, SCOPE_LABEL: s.a.prefix}}}
            s.save(instance, cfg); preview = _source(wait_associations(s, instance)['association:ServiceMonitor']); started = time.time(); s.publish(instance)
        identity = wait(lambda: _agent_identity(s, instance, namespace, name), lambda v: v is not None, timeout=150, message='Agent原工作负载未Ready')
        group = {'role': role, 'creatorUserId': creator, 'initialDraft': initial, 'ownPreview': own, 'matchingPreview': preview, 'publishedAt': started, 'identity': identity, 'exports': [exports[role]]}
        group['adopted'] = adopted(group); group['initialAdopted'] = copy.deepcopy(group['adopted']); group['samples'] = samples(group)
        group['initialSamples'] = copy.deepcopy(group['samples'])
        proof['groups'].append(group)
        if role == 'user': user_group, user_instance, user_cfg = group, instance, copy.deepcopy(cfg)
        else:
            with s.using(user_client): denied = s.save(instance, cfg, status=403)
            proof['protectedAgentSave'] = denied
    for group in proof['groups']:
        other = next(g for g in proof['groups'] if g['role'] != group['role'])
        group['initialNegativeQueries'] = negatives(group, other)
    s.by('I30-K02：管理员编辑只保存草稿；既有Agent不重发而发现新增同创建者exporter')
    context('agent', namespace, user_group['identity']['instance']['name']); user_cfg['resources']['requestCPU'] = '210m'
    s.save(user_instance, user_cfg); proof['adminEditedDraft'] = s.call('GET', '/instances/'+user_instance['id']+'/draft')
    proof['dynamicBefore'] = _agent_identity(s, user_instance, namespace, user_group['identity']['instance']['name'])
    proof['dynamicAuditBefore'] = s.env.audit_events('data', credentials['identity'])
    extra = exporter('user', 2, user_client); user_group['exports'].append(extra)
    context('agent', namespace, user_group['identity']['instance']['name']); user_group['adopted'] = adopted(user_group); user_group['samples'] = samples(user_group)
    proof['dynamicAfter'] = _agent_identity(s, user_instance, namespace, user_group['identity']['instance']['name'])
    proof['dynamicAuditAfter'] = s.env.audit_events('data', credentials['identity'])
    for group in proof['groups']:
        group['adopted'] = adopted(group)
        group['allSamples'] = query(expression({'prometheus': group['adopted']['externalLabels']['prometheus']}))
        other = next(g for g in proof['groups'] if g['role'] != group['role'])
        group['negativeQueries'] = negatives(group, other)
    proof['prometheusOperator'] = _operator(s)
    s.by('I30-K03：同一受限SA上真实读取故障与RBAC拒绝，恢复后不提升权限')
    proxy = Proxy(s, 'data', backend_config=config); s.proxies.append(proxy)
    try:
        s.operation(s.accept(proxy.config, path='/clusters/'+cluster['id']+'/credentials'))
        proxy.control('faults', {'readMode': 'error', 'rejectWritePath': ''})
        proof['readFailure'] = _source(s.association_preview(user_instance)['association:ServiceMonitor'])
    finally:
        proxy.control('reset', {}); s.operation(s.accept(config, path='/clusters/'+cluster['id']+'/credentials'))
    role = next(r for r in credentials['resources'] if r['kind'] == 'ClusterRole'); original = s.get('clusterrole', role['name'], target='data')
    rules = copy.deepcopy(original['rules'])
    for rule in rules:
        if 'servicemonitors' in rule.get('resources', []): rule['resources'].remove('servicemonitors')
    rules = [r for r in rules if r.get('resources') or r.get('nonResourceURLs')]
    def set_rules(value):
        current = s.get('clusterrole', role['name'], target='data')
        s.k('patch', 'clusterrole', role['name'], '--type=json', '-p', json.dumps([{'op': 'test', 'path': '/metadata/uid', 'value': original['metadata']['uid']}, {'op': 'test', 'path': '/metadata/resourceVersion', 'value': current['metadata']['resourceVersion']}, {'op': 'replace', 'path': '/rules', 'value': value}]), target='data')
    set_rules(rules)
    try:
        proof['permissionFailure'] = _source(wait(lambda: s.association_preview(user_instance)['association:ServiceMonitor'], lambda v: v.get('error', '').startswith('TARGET_FORBIDDEN'), timeout=30, message='真实SA的SM读取Forbidden未生效'))
    finally: set_rules(original['rules'])
    proof['recoveredPreview'] = _source(wait_associations(s, user_instance)['association:ServiceMonitor'])
    proof['monitorOpAfter'] = monitor_operator()
    proof['receiverAfter'] = receiver_state(); proof['audit'] = [event for event in s.env.audit_events('data') if event['auditID'] not in baseline]
    s.acceptance_evidence['issue30'] = proof
    (s.env.public/'agent-collection.json').write_text(json.dumps(proof, ensure_ascii=False, indent=2)+'\n')


observe_agent = _agent_identity
adopted_configuration = _configuration
