"""新 UT/E2E 共用的最小结果与清理边界；不再解释旧 profile/收据矩阵。"""
from collections import Counter


def validate_specs(report, expected, candidate, actual_candidate):
    """关键：只有同来源、逐项唯一、无重试/跳过的完整 Ginkgo 报告才能通过。"""
    if candidate != actual_candidate:
        raise ValueError('candidate-mismatch')
    if len(report) != 1 or not report[0].get('SuiteSucceeded'):
        raise ValueError('suite-not-passed')
    suite = report[0]
    if suite.get('SuiteConfig', {}).get('DryRun') or suite.get('SuiteHasProgrammaticFocus'):
        raise ValueError('partial-suite')
    specs = [s for s in suite.get('SpecReports', []) if s.get('LeafNodeType') == 'It']
    actual = [s['LeafNodeText'].split(' ', 1)[0] for s in specs]
    if Counter(actual) != Counter(expected) or len(set(expected)) != len(expected):
        raise ValueError('missing-or-duplicate-case')
    if any(s.get('State') != 'passed' or s.get('NumAttempts') != 1 for s in specs):
        raise ValueError('case-not-passed')
    return specs


def owned_identity(run_id, recorded, actual):
    """关键：按原 UID 和本 run 标签判断清理资格；同名替代对象不能被借用。"""
    return (bool(recorded.get('uid')) and actual.get('uid') == recorded['uid']
            and actual.get('runId') == recorded.get('runId') == run_id)


def validate_agent_collection(receipt, candidate, run_id=None):
    """关键：来源、采用配置及真实目标样本同时成立，Ready/匹配或缺失证据不能补成采集通过。"""
    def require(value):
        if not value:
            raise ValueError('agent-collection-evidence-incomplete')
    def process(value):
        require(all(value.get(k) for k in ('podUID','containerID','configDigest')))
    require(isinstance(receipt,dict) and receipt.get('testedCommit')==candidate and receipt.get('metric')=='kube_pod_info')
    require(receipt.get('runId') and (run_id is None or receipt['runId']==run_id))
    boundaries=receipt.get('previewBoundaries',{})
    require(all(boundaries.get(k) is True for k in ('zeroMatchSuccessful','readFailureObserved','permissionFailureObserved','distinctReasons')))
    receiver=receipt.get('receiver',{});process(receiver)
    require(receiver.get('scrapeConfigs')==[] and receiver.get('configSHA256') and receiver.get('image'))
    operator=receipt.get('operator',{});process(operator)
    require(operator.get('agentControllerReady') is True and operator.get('scrapeConfigInformerObserved') is True)
    groups=receipt.get('groups',[]);require(len(groups)==2 and {g.get('role') for g in groups}=={'user','admin'})
    require(len({g.get('creatorUserId') for g in groups})==2)
    all_jobs={g['role']:{'serviceMonitor/'+e['namespace']+'/'+e['serviceMonitor']+'/0' for e in g.get('exports',[])} for g in groups}
    for group in groups:
        process(group.get('container',{}));require(group['container']['configDigest']==receiver['configDigest']);creator=group.get('creatorUserId');require(creator and group.get('uid') and group.get('configSHA256'))
        require(group.get('selector',{}).get('object',{}).get('selector',{}).get('matchLabels',{}).get('monitor-mgr.io/user-id')==creator)
        exports=group.get('exports',[]);samples=group.get('samples',[]);require(len(exports)==(2 if group['role']=='user' else 1) and len(samples)==len(exports))
        jobs=set(group.get('adoptedJobs',[]));require(all_jobs[group['role']]<=jobs and not jobs&all_jobs['admin' if group['role']=='user' else 'user'])
        for exporter,sample in zip(exports,samples):
            require(exporter.get('creatorUserId')==creator and all(exporter.get(k) for k in ('uid','serviceUID','serviceMonitorUID','podUID')))
            process(exporter.get('container',{}));require(exporter['container']['podUID']==exporter['podUID'])
            metric=sample.get('metric',{})
            expected={'__name__':'kube_pod_info','namespace':exporter['namespace'],'service':exporter['service'],'exported_pod':exporter['pod'],'uid':exporter['podUID'],'instance':exporter['instance'],'prometheus':group.get('externalPrometheus')}
            require(all(metric.get(k)==v for k,v in expected.items()) and sample.get('value')==1 and sample.get('receiverPodUID')==receiver['podUID'])
            require(isinstance(sample.get('sampleTime'),(float,int)) and sample.get('notBefore',float('inf'))<=sample['sampleTime']<=sample.get('queryTime',0)+5)
    user=next(g for g in groups if g['role']=='user');dynamic=receipt.get('dynamic',{})
    require(dynamic.get('agentUID')==user['uid'] and dynamic.get('unchangedPublication') is True)
    require(dynamic.get('versionBefore')==dynamic.get('versionAfter')==user.get('versionId') and user.get('versionId'))
    require(dynamic.get('oldConfigSHA256')==user.get('initialConfigSHA256') and dynamic.get('newConfigSHA256')==user.get('configSHA256') and dynamic.get('oldConfigSHA256')!=dynamic.get('newConfigSHA256'))
    require(dynamic.get('newExporterUID')==user['exports'][1]['uid'] and dynamic.get('newServiceMonitorUID')==user['exports'][1]['serviceMonitorUID'])
    new_job='serviceMonitor/'+user['exports'][1]['namespace']+'/'+user['exports'][1]['serviceMonitor']+'/0'
    require(new_job not in dynamic.get('jobsBefore',[]) and new_job in dynamic.get('jobsAfter',[]) and dynamic.get('jobsAfter')==user.get('adoptedJobs'))
    return receipt
