"""历史 exporter 的真实集群夹具与证据；仅操作本 run 明确确认原 UID 的对象。"""
import json
import uuid


def remove_fixture_companion(s, operation):
    """当前版本先公开发布，再删除本 run 的原 SM，形成旧版本只有 KSM 的真实目标。"""
    resource = next(p for p in operation['resources'] if p['target']['key'] == 'service-monitor')
    target, uid = resource['target'], resource['confirmedUid']
    assert uid
    current = s.get('servicemonitor', target['name'], '-n', target['namespace'])
    assert current['metadata']['uid'] == uid
    # 关键：这是历史夹具转换，产品升级/后台从不删除 SM；DELETE 自带已确认原 UID 条件。
    path = '/apis/monitoring.coreos.com/v1/namespaces/' + target['namespace'] + '/servicemonitors/' + target['name']
    s.delete_uid(s.target, path, uid)
    s.k('wait', '--for=delete', 'servicemonitor/' + target['name'], '-n', target['namespace'], '--timeout=30s', target=s.target)
    return {'namespace': target['namespace'], 'name': target['name'], 'removedFixtureUID': uid}


def publication_counts(s, name, sm):
    """只数平台代理的业务 mutation，排除 SSAR/读取和直接执行的夹具删除。"""
    component = '/apis/monitoring.fjsxy.github.io/v1alpha1/kubestatemetrics'
    companion = '/apis/monitoring.coreos.com/v1/namespaces/' + sm['namespace'] + '/servicemonitors'
    events = [e for e in s.capture_proxy() if e['stage'] == 'upstream_completed']
    result = {key: len([e for e in events if e['method'] == method and e['path'] == path])
            for key, method, path in [('componentPOST', 'POST', component), ('componentPUT', 'PUT', component + '/' + name),
                                      ('smPOST', 'POST', companion), ('smPUT', 'PUT', companion + '/' + sm['name'])]}
    result['smMutation'] = len([e for e in events if e['method'] in ('POST', 'PUT', 'PATCH', 'DELETE') and e['path'] in (companion, companion + '/' + sm['name'])])
    return result


def legacy_single_resource(s, instance, operation):
    """公开回读证明旧冻结版本和操作仍仅一份 KSM，不依赖数据库侧探针。"""
    version = s.call('GET', '/instances/' + instance['id'] + '/versions/' + operation['versionId'])
    current = s.call('GET', '/operations/' + operation['operationId'])
    assert len(version['resources']) == len(current['resources']) == 1
    assert version['resources'][0]['kind'] == current['resources'][0]['target']['kind'] == 'KubeStateMetrics'
    return {'versionId': version['versionId'], 'operationId': current['operationId'], 'resourceKeys': [r['key'] for r in version['resources']]}


def recovery(s, proxy):
    """I29-K02：真实旧集群只在新明确发布补建；明确拒绝可补齐，丢响应禁止认领。"""
    from historical_identity import upgrade_v10
    cases = []
    proxy.control('reset', {})
    for variant in ('reject', 'lost'):
        s.crname = 'legacy-' + variant + '-' + uuid.uuid4().hex[:8]
        instance = s.create()
        original = s.publish(instance)
        uid = s.call('GET', '/instances/' + instance['id'])['uid']
        sm = remove_fixture_companion(s, original)
        cases.append((variant, instance, original, uid, sm))
    upgrade_v10(s)
    receipt = {'testedCommit': s.env.candidate, 'podUID': s.platform_pod_uid, 'cases': []}
    for variant, instance, original, uid, sm in cases:
        s.crname = instance['name']
        legacy = legacy_single_resource(s, instance, original)
        collection = '/apis/monitoring.coreos.com/v1/namespaces/' + sm['namespace'] + '/servicemonitors'
        baseline = publication_counts(s, s.crname, sm)
        s.by('I29-K02：历史 exporter 明确补建 · ' + variant)
        if variant == 'reject':
            proxy.control('faults', {'readMode': 'none', 'rejectWritePath': collection})
        else:
            proxy.control('register-create', {'intentId': uuid.uuid4().hex, 'identity': {'apiVersion': 'monitoring.coreos.com/v1', 'kind': 'ServiceMonitor', 'namespace': sm['namespace'], 'name': sm['name']}})
            proxy.arm('POST', collection, 'drop_and_block_reads')
        operation = s.publish(instance, wait=False)
        state = 'FAILED' if variant == 'reject' else 'IDENTITY_UNCERTAIN'
        before = s.state(operation, state)
        component = next(r for r in before['resources'] if r['target']['key'] == 'component')
        assert component['state'] == 'SUCCEEDED' and component['confirmedUid'] == uid
        writes = publication_counts(s, s.crname, sm)
        assert writes['componentPOST'] == baseline['componentPOST'] and writes['componentPUT'] == baseline['componentPUT'] + 1
        if variant == 'lost':
            creates = [e for e in s.capture_proxy() if e['stage'] == 'original_create_response' and e['path'] == collection and e['originalCreate']['identity']['name'] == sm['name']]
            assert len(creates) == 1
            s.owned_crs[(s.target, 'servicemonitor', sm['namespace'], sm['name'])] = creates[0]['originalCreate']['uid']
        s.rebuild()
        proxy.control('reset', {})
        s.call('POST', '/operations/' + operation['operationId'] + '/refresh', {})
        if variant == 'reject':
            retry = s.action(operation, 'retry')
            after = s.state(operation, 'SUCCEEDED')
            assert retry['versionId'] == operation['versionId'] and retry['attemptId'] != operation['attemptId']
            sm_progress = next(r for r in after['resources'] if r['target']['key'] == 'service-monitor')
            s.owned_crs[(s.target, 'servicemonitor', sm['namespace'], sm['name'])] = sm_progress['confirmedUid']
            final = publication_counts(s, s.crname, sm)
            assert final['componentPUT'] == writes['componentPUT'] and final['smPOST'] == writes['smPOST'] + 1 and final['smMutation'] == writes['smMutation'] + 1
        else:
            after = s.state(operation, 'IDENTITY_UNCERTAIN')
            assert not next(r for r in after['resources'] if r['target']['key'] == 'service-monitor')['confirmedUid']
            s.action(operation, 'retry', 409)
            s.action(operation, 'continue-unsent', 409)
            final = publication_counts(s, s.crname, sm)
            assert final == writes
        assert s.cr()['metadata']['uid'] == uid
        assert legacy_single_resource(s, instance, original) == legacy
        receipt['cases'].append({'variant': variant, 'legacy': legacy, 'versionId': operation['versionId'], 'state': after['state'], 'componentUID': uid, 'before': baseline, 'afterWrite': writes, 'afterRecovery': final})
    (s.env.public / 'historical-exporter-recovery.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n')
