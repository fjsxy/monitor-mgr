"""真实 Pod 的写前权限与已发只读恢复编排；决定性原始观测交给 Ginkgo。"""
import http.cookiejar
import secrets
import urllib.request
import uuid


def authorization(s, proxy):
    """在真实 API 的只读 SSAR 前暂停 worker，分别观察撤权前后边界。"""
    username = 'dispatch-' + uuid.uuid4().hex[:12]
    temporary, personal = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
    actor = s.call('POST', '/accounts', {'username': username, 'password': temporary, 'role': 'ADMIN'}, status=201)
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    with s.using(opener):
        s.call('POST', '/sessions', {'username': username, 'password': temporary}, status=201)
        s.call('POST', '/session/password', {'currentPassword': temporary, 'newPassword': personal})
        s.call('POST', '/sessions', {'username': username, 'password': personal}, status=201)
    s.call('POST', '/clusters/' + s.cluster['id'] + '/transfer', {'ownerId': actor['id']})
    s.crname = 'auth-unsent-' + uuid.uuid4().hex[:8]
    with s.using(opener):
        instance = s.create()
        preview = s.preview(instance)

    def writes():
        return [e for e in s.capture_proxy() if e['stage'] == 'upstream_completed'
                and e['method'] in ('POST', 'PUT', 'PATCH', 'DELETE')
                and ('kubestatemetrics' in e['path'] or 'servicemonitors' in e['path'])]

    s.by('I25-K01.unsent：只读预检暂停时降级仍拥有集群的创建管理员')
    before = len(writes())
    proxy.arm('POST', '/apis/authorization.k8s.io/v1/selfsubjectaccessreviews',
              'pause_before_send', ssar_resource='kubestatemetrics')
    try:
        with s.using(opener):
            operation = s.publish(instance, preview=preview, wait=False)
        paused = proxy.paused()['rule']
        current = s.call('GET', '/operations/' + operation['operationId'])
        attempts = s.call('GET', '/operations/' + operation['operationId'] + '/attempts')['attempts']
        attempt = next(v for v in attempts if v['attemptId'] == current['attemptId'])
        s.call('PATCH', '/accounts/' + actor['id'], {'role': 'USER'})
        owner = s.call('GET', '/clusters/' + s.cluster['id'])['ownerId']
        proxy.control('release', {'ruleId': paused['id']})
        failed = s.state(operation, 'FAILED')
        absent = s.k('get', 'kubestatemetrics', s.crname, target=s.target, check=False)
        unsent = dict(actorId=actor['id'], creatorUserId=instance['creatorUserId'],
                      creatorRole=instance['creatorRole'], ownerAfterRevocation=owner,
                      pausedResource=paused.get('ssarResource'), stateBefore=current['state'],
                      possiblySent=attempt['possiblySent'], attemptActor=attempt['actorId'],
                      stateAfter=failed['state'], message=failed['message'],
                      businessWrites=len(writes()) - before,
                      notFound=absent.returncode != 0 and b'(NotFound)' in absent.stderr)
    finally:
        proxy.control('reset', {})

    # 关键：已发送请求只读恢复，不再存在需要平台续发的第二份 SM。
    s.call('PATCH', '/accounts/' + actor['id'], {'role': 'ADMIN'})
    with s.using(opener):
        s.call('POST', '/sessions', {'username': username, 'password': personal}, status=201)
        s.crname = 'auth-sent-' + uuid.uuid4().hex[:8]
        known = s.create()
        s.publish(known)
        original = s.call('GET', '/instances/' + known['id'])
        cfg = s.cfg()
        cfg['resources']['requestCPU'] = '244m'
        s.save(known, cfg)
        preview = s.preview(known)
    s.by('I25-K01.sent：更新完成但丢响应后停用账号，重建平台只读核验原 UID')
    path = '/apis/monitoring.fjsxy.github.io/v1alpha1/kubestatemetrics/' + s.crname
    proxy.arm('PUT', path, 'drop_and_block_reads')
    try:
        with s.using(opener):
            operation = s.publish(known, preview=preview, wait=False)
        uncertain = s.state(operation, 'RESULT_UNCERTAIN')
        count = len(writes())
        s.call('PATCH', '/accounts/' + actor['id'], {'enabled': False})
        s.rebuild()
        proxy.control('reset', {})
        s.call('POST', '/operations/' + operation['operationId'] + '/refresh', {})
        completed = s.state(operation, 'SUCCEEDED', timeout=60)
        actual = s.cr()
        refreshed = s.call('GET', '/instances/' + known['id'])
        attempts = s.call('GET', '/operations/' + operation['operationId'] + '/attempts')['attempts']
        attempt = next(v for v in attempts if v['attemptId'] == completed['attemptId'])
        sent = dict(stateUncertain=uncertain['state'], stateAfter=completed['state'],
                    originalUID=original['uid'], actualUID=actual['metadata']['uid'],
                    instanceUID=refreshed['uid'], actualCPU=actual['spec']['podResources']['requests']['cpu'],
                    creatorUserId=refreshed['creatorUserId'], actorId=actor['id'],
                    countBeforeRecovery=count, countAfterRecovery=len(writes()),
                    putCount=len([e for e in writes() if e['method'] == 'PUT' and e['path'] == path]),
                    possiblySent=attempt['possiblySent'],
                    resources=[dict(key=r['target']['key'], state=r['state']) for r in completed['resources']])
    finally:
        proxy.control('reset', {})
        s.call('POST', '/clusters/' + s.cluster['id'] + '/transfer', {'ownerId': s.owner})
    # 白名单不含 session、token、证书、HTTP 正文或完整配置。
    s.acceptance_evidence['issue25'] = dict(testedCommit=s.env.candidate, unsent=unsent, sent=sent,
                                 serviceMonitorMutations=[dict(method=e['method'], path=e['path'])
                                     for e in s.capture_proxy() if e['stage'] == 'frontend_received'
                                     and e['method'] in ('POST', 'PUT', 'PATCH', 'DELETE')
                                     and 'servicemonitors' in e['path']])
