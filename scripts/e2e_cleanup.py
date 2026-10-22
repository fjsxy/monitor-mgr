"""浏览器明确发布的私有身份交接；只信任精确公开操作已确认的资源 UID。"""
import json
import re


class _RegistrationError(ValueError):
    """异常参数仅为固定原因码，不携带 API 消息或私有交接内容。"""


def register_browser_publications(s, path, allowed_instances, require_complete=False):
    """即使浏览器中途失败也回读已受理操作；缺少身份依据时保留清理失败门禁。"""
    result = dict(operations=0, resources=0, failures=[])
    covered, seen = set(), set()
    def reject(reason):
        if reason not in result['failures']: result['failures'].append(reason)
    try:
        if path.stat().st_size > 65536: raise _RegistrationError('invalid-publication-receipt')
        records = path.read_text().splitlines()
        if len(records) > 16: raise _RegistrationError('invalid-publication-receipt')
    except Exception:
        records = []; reject('invalid-publication-receipt')
    for line in records:
        try:
            record = json.loads(line)
            if not isinstance(record, dict) or set(record) != {'operationId', 'instanceId', 'versionId'}:
                raise _RegistrationError('invalid-publication-receipt')
            if any(not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9-]{1,128}', value) for value in record.values()):
                raise _RegistrationError('invalid-publication-receipt')
            if record['instanceId'] not in allowed_instances: raise _RegistrationError('foreign-publication-instance')
            if record['operationId'] in seen: raise _RegistrationError('duplicate-publication-receipt')
            seen.add(record['operationId'])
        except _RegistrationError as error:
            reject(error.args[0]); continue
        except Exception:
            reject('invalid-publication-receipt'); continue
        try:
            operation = s.call('GET', '/operations/' + record['operationId'])
        except Exception:
            reject('publication-read-failed'); continue
        try:
            if any(operation.get(key) != value for key, value in record.items()) or operation.get('clusterId') != s.cluster['id'] or operation.get('action') != 'PUBLISH':
                raise _RegistrationError('publication-identity-mismatch')
            resources = operation.get('resources')
            if not isinstance(resources, list) or not resources or len(resources) > 2:
                raise _RegistrationError('invalid-publication-resources')
            pending = {}
            for resource in resources:
                target, uid = resource['target'], resource.get('confirmedUid')
                kinds = {('component', 'monitoring.fjsxy.github.io/v1alpha1', 'KubeStateMetrics'): 'kubestatemetrics',
                         ('component', 'monitoring.coreos.com/v1alpha1', 'PrometheusAgent'): 'prometheusagent',
                         ('service-monitor', 'monitoring.coreos.com/v1', 'ServiceMonitor'): 'servicemonitor'}
                kind = kinds.get((target['key'], target['apiVersion'], target['kind']))
                if not kind or not target.get('name') or not isinstance(target.get('namespace'), str):
                    raise _RegistrationError('invalid-publication-resources')
                if not uid:
                    if resource.get('possiblySent') or resource.get('state') == 'SUCCEEDED':
                        raise _RegistrationError('unconfirmed-publication-uid')
                    continue
                key = (s.target, kind, target['namespace'], target['name'])
                # 关键：新 UID 只能来自本 run 明确受理操作；仍在登记的其他 UID 不可被覆盖。
                if s.owned_crs.get(key) not in (None, uid): raise _RegistrationError('conflicting-cleanup-uid')
                if key in pending: raise _RegistrationError('invalid-publication-resources')
                pending[key] = uid
            s.owned_crs.update(pending)
            covered.add(record['instanceId']); result['operations'] += 1; result['resources'] += len(pending)
        except _RegistrationError as error:
            reject(error.args[0])
        except Exception:
            reject('invalid-publication-resources')
    if require_complete and covered != set(allowed_instances): reject('missing-publication-receipt')
    s.cleanup_registration = result
    return result
