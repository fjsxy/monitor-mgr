"""独占集群的 Metadata 审计与真实平台 SA；只提供环境配置和原始白名单观测。"""
import json
import re

AUDIT_POLICY = '/etc/kubernetes/monitor-mgr-audit-policy.json'
AUDIT_LOG = '/var/log/kubernetes/monitor-mgr-audit.log'
READ = ['get', 'list', 'watch']


def audit_configuration(policy_file):
    """关键：RequestReceived 也保留，未完成或被拒的写尝试不能漏出零写证明。"""
    policy = dict(apiVersion='audit.k8s.io/v1', kind='Policy', omitStages=['ResponseStarted'], rules=[
        dict(level='Metadata', resources=[
            dict(group='monitoring.fjsxy.github.io', resources=['kubestatemetrics']),
            dict(group='monitoring.coreos.com', resources=['servicemonitors', 'prometheusagents'])]),
        dict(level='None')])
    patch = dict(kind='ClusterConfiguration', apiServer=dict(
        # 关键：同步审计不借异步缓冲掩盖请求，短生命周期日志不轮转以保留完整计数。
        extraArgs={'audit-policy-file': AUDIT_POLICY, 'audit-log-path': AUDIT_LOG, 'audit-log-mode': 'blocking-strict',
                   'audit-log-maxsize': '0'},
        extraVolumes=[dict(name='monitor-mgr-audit-policy', hostPath=AUDIT_POLICY, mountPath=AUDIT_POLICY, readOnly=True, pathType='File'),
                      dict(name='monitor-mgr-audit-log', hostPath='/var/log/kubernetes', mountPath='/var/log/kubernetes', readOnly=False, pathType='DirectoryOrCreate')]))
    return dict(policy=policy, kubeadmConfigPatches=[json.dumps(patch)],
                nodes=[dict(extraMounts=[dict(hostPath=str(policy_file), containerPath=AUDIT_POLICY, readOnly=True)])])


def platform_rbac(namespace, name, run_id):
    """父 CR 管理和 namespace 授权预检沿用既有规则；SM/CRD 始终只读。"""
    if not all(re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', value) for value in (namespace, name)):
        raise ValueError('PLATFORM_IDENTITY_INVALID')
    role = namespace + '-' + name
    labels = {'monitor-mgr.test/run': run_id}
    rules = [
        dict(apiGroups=[''], resources=['namespaces', 'nodes', 'serviceaccounts', 'pods', 'pods/proxy', 'services', 'services/proxy',
                                       'endpoints', 'secrets', 'configmaps', 'events', 'persistentvolumes', 'persistentvolumeclaims'], verbs=READ),
        # 沿用目标 namespace 管理授权预检；平台仍只写父 CR，不以此取得 SM 写权限。
        dict(apiGroups=['apps'], resources=['deployments', 'statefulsets', 'daemonsets'], verbs=READ + ['create', 'update']),
        dict(apiGroups=['apps'], resources=['replicasets'], verbs=READ),
        dict(apiGroups=['discovery.k8s.io'], resources=['endpointslices'], verbs=READ),
        dict(apiGroups=['storage.k8s.io'], resources=['storageclasses'], verbs=READ),
        dict(apiGroups=['apiextensions.k8s.io'], resources=['customresourcedefinitions'], verbs=READ),
        dict(apiGroups=['monitoring.fjsxy.github.io'], resources=['kubestatemetrics'], verbs=READ + ['create', 'update', 'patch', 'delete']),
        dict(apiGroups=['monitoring.coreos.com'], resources=['prometheusagents'], verbs=READ + ['create', 'update', 'patch', 'delete']),
        dict(apiGroups=['monitoring.coreos.com'], resources=['servicemonitors', 'podmonitors', 'probes', 'scrapeconfigs'], verbs=READ),
        dict(apiGroups=['authentication.k8s.io'], resources=['selfsubjectreviews'], verbs=['create']),
        dict(apiGroups=['authorization.k8s.io'], resources=['selfsubjectaccessreviews', 'subjectaccessreviews'], verbs=['create']),
        dict(nonResourceURLs=['/version', '/version/*', '/api', '/api/*', '/apis', '/apis/*', '/readyz', '/readyz/*'], verbs=['get'])]
    return [
        dict(apiVersion='v1', kind='ServiceAccount', metadata=dict(name=name, namespace=namespace, labels=labels)),
        dict(apiVersion='rbac.authorization.k8s.io/v1', kind='ClusterRole', metadata=dict(name=role, labels=labels), rules=rules),
        dict(apiVersion='rbac.authorization.k8s.io/v1', kind='ClusterRoleBinding', metadata=dict(name=role, labels=labels),
             roleRef=dict(apiGroup='rbac.authorization.k8s.io', kind='ClusterRole', name=role),
             subjects=[dict(kind='ServiceAccount', name=name, namespace=namespace)])]


def audit_metadata(raw, identity=None):
    """只投影真实 API audit event 白名单；损坏日志必须失败，不能解释为零请求。"""
    events = []
    for line in raw.splitlines():
        if not line.strip(): continue
        try:
            event = json.loads(line)
            if event.get('apiVersion') != 'audit.k8s.io/v1' or event.get('kind') != 'Event' or not event.get('auditID'):
                raise ValueError('AUDIT_EVENT_INVALID')
            if event.get('level') != 'Metadata': raise ValueError('AUDIT_LEVEL_INVALID')
            username = event.get('user', {}).get('username', '')
            if identity is not None and username != identity: continue
            ref = event.get('objectRef', {})
            events.append(dict(auditID=event['auditID'], stage=event.get('stage'), username=username, verb=event.get('verb'),
                               requestReceivedTimestamp=event.get('requestReceivedTimestamp'), stageTimestamp=event.get('stageTimestamp'),
                               objectRef={k: ref[k] for k in ('apiGroup', 'apiVersion', 'resource', 'subresource', 'namespace', 'name', 'uid') if k in ref},
                               responseStatus={k: event.get('responseStatus', {})[k] for k in ('code', 'reason') if k in event.get('responseStatus', {})}))
        except (ValueError, TypeError, KeyError, AttributeError):
            raise ValueError('AUDIT_EVENT_INVALID') from None
    return events
