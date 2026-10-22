"""UT-18 仅保留三组会使测试结论失真的风险。"""
import copy
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
from testing_contract import owned_identity, validate_specs, validate_agent_collection
from e2e_shards import SHARDS, aggregate, selection
from e2e_diagnostics import failure_summary
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts/acceptance'))
from clusters import Suite as ClusterSuite
from fault_proxy_boundary import check_ssar_rule


class TrustBoundary(unittest.TestCase):
    def report(self):
        return [{'SuiteSucceeded': True, 'SpecReports': [
            {'LeafNodeType': 'It', 'LeafNodeText': 'E2E-01 示例', 'State': 'passed', 'NumAttempts': 1}]}]

    def test_events(self):
        # I28-R01：验收暂停规则也须通过公开 HTTP，避免代理误拦受理使授权结论失真。
        check_ssar_rule(self)
        # 接下来验证真实成功正控，再检查漏跑、失败、跳过、重复以及重试都不能变绿。
        validate_specs(self.report(), ['E2E-01'], 'candidate', 'candidate')
        for kind in ['missing', 'failed', 'skipped', 'duplicate', 'retry']:
            with self.subTest(kind=kind):
                report = copy.deepcopy(self.report()); specs = report[0]['SpecReports']
                if kind == 'missing': specs.clear()
                elif kind == 'duplicate': specs.append(copy.deepcopy(specs[0]))
                elif kind == 'retry': specs[0]['NumAttempts'] = 2
                else: specs[0]['State'] = kind
                with self.assertRaises(ValueError):
                    validate_specs(report, ['E2E-01'], 'candidate', 'candidate')
        # 分组只是调度；全部10项仍须同来源、各执行一次且清理成功，不能让部分绿灯成为完整结论。
        reports = [dict(schemaVersion=3, kind='e2e-shard', shard=shard, assigned=ids,
                        candidate='candidate', runId='mm-ci-1-1-'+shard, environment='ready', passed=True, cleanup=True,
                        results={id: dict(id=id, outcome='passed', cleanup=[]) for id in ids},
                        ginkgo=[dict(id=id, state='passed', attempts=1) for id in ids]) for shard, ids in SHARDS.items()]
        reports[0]['results']['E2E-04']['agentCollection']=self.collection()
        self.assertEqual(len(aggregate(reports, 'candidate', 'mm-ci-1-1', 'success')['results']), 10)
        self.assertEqual(selection(), [f'E2E-{n:02d}' for n in range(1, 11)])
        with self.assertRaises(ValueError): selection('unknown')
        for kind in ['missing-shard', 'duplicate-shard', 'job-failed', 'job-skipped', 'wrong-candidate', 'wrong-run',
                     'wrong-group', 'missing-collection', 'missing', 'duplicate', 'failed', 'skipped', 'retry', 'not_run', 'cleanup', 'case-cleanup', 'full-report']:
            with self.subTest(shard_failure=kind):
                broken=copy.deepcopy(reports); first=broken[0]; id=first['assigned'][0]; jobs='success'
                if kind == 'missing-shard': broken.pop()
                elif kind == 'duplicate-shard': broken.append(copy.deepcopy(first))
                elif kind.startswith('job-'): jobs=kind[4:]
                elif kind == 'wrong-candidate': first['candidate']='other'
                elif kind == 'wrong-run': first['runId']='mm-other'
                elif kind == 'wrong-group': first['assigned']=selection('storage')
                elif kind == 'missing-collection': del first['results']['E2E-04']['agentCollection']
                elif kind == 'missing': del first['results'][id]
                elif kind == 'duplicate': first['ginkgo'].append(copy.deepcopy(first['ginkgo'][0]))
                elif kind in ('failed', 'skipped'): first['ginkgo'][0]['state']=kind
                elif kind == 'retry': first['ginkgo'][0]['attempts']=2
                elif kind == 'not_run': first['results'][id]['outcome']='not_run'
                elif kind == 'cleanup': first['cleanup']=False
                elif kind == 'case-cleanup': first['results'][id]['cleanup']=['failure']
                elif kind == 'full-report': first['kind']='e2e-full'
                with self.assertRaises(ValueError): aggregate(broken, 'candidate', 'mm-ci-1-1', jobs)
        # 固定产品错误只投影代码位置；未知消息即使含凭据也不能进入公开诊断。
        known=failure_summary(AssertionError(('/ignored',409,'CONFLICT','原Operator进程未证明Agent controller及所需ScrapeConfig informer已就绪')))
        self.assertEqual(known['apiCode'],'CONFLICT')
        self.assertTrue(any(v['file']=='internal/control/agent_operator_metrics.go' for v in known['apiSites']))
        sentinel='PRIVATE_PASSWORD_SENTINEL'
        unknown=failure_summary(AssertionError(('/'+sentinel,409,sentinel,sentinel)))
        self.assertNotIn(sentinel,json.dumps(unknown));self.assertNotIn('apiSites',unknown)

    def collection(self):
        # 独立固定收据只验证证据门禁，真实样本仍须由E2E读取原进程和receiver API。
        receiver=dict(podUID='receiver-pod',containerID='receiver-container',configDigest='sha256:prom',scrapeConfigs=[],image='locked',configSHA256='r')
        groups=[]
        for role,n in [('user',2),('admin',1)]:
            exports=[];samples=[];jobs=[]
            for i in range(n):
                e=dict(uid=f'{role}-cr{i}',creatorUserId=role,namespace=role,service=f'service{i}',serviceUID=f'{role}-svc{i}',serviceMonitor=f'sm{i}',serviceMonitorUID=f'{role}-sm{i}',pod=f'pod{i}',podUID=f'{role}-pod{i}',instance=f'10.0.0.{i+1}:8080',container=dict(podUID=f'{role}-pod{i}',containerID=f'{role}-container{i}',configDigest='sha256:ksm'))
                exports.append(e);jobs.append(f'serviceMonitor/{role}/sm{i}/0')
                samples.append(dict(metric=dict(__name__='kube_pod_info',namespace=role,service=f'service{i}',exported_pod=f'pod{i}',uid=f'{role}-pod{i}',instance=f'10.0.0.{i+1}:8080',prometheus='collectors/'+role),value=1,sampleTime=101,queryTime=102,notBefore=100,receiverPodUID='receiver-pod'))
            groups.append(dict(role=role,creatorUserId=role,name=role,uid=role+'-agent',container=dict(podUID=role+'-agent-pod',containerID=role+'-agent-container',configDigest='sha256:prom'),externalPrometheus='collectors/'+role,configSHA256='after-'+role,initialConfigSHA256='before-'+role,adoptedJobs=jobs,selector=dict(object=dict(mode='Labels',selector=dict(matchLabels={'monitor-mgr.io/user-id':role}))),exports=exports,samples=samples,versionId=role+'-v1'))
        return dict(testedCommit='candidate',runId='mm-ci-1-1-topology',previewBoundaries=dict(zeroMatchSuccessful=True,readFailureObserved=True,permissionFailureObserved=True,distinctReasons=True),metric='kube_pod_info',receiver=receiver,operator=dict(podUID='op',containerID='op-container',configDigest='sha256:op',agentControllerReady=True,scrapeConfigInformerObserved=True),groups=groups,dynamic=dict(agentUID='user-agent',versionBefore='user-v1',versionAfter='user-v1',oldConfigSHA256='before-user',newConfigSHA256='after-user',newExporterUID='user-cr1',newServiceMonitorUID='user-sm1',jobsBefore=['serviceMonitor/user/sm0/0'],jobsAfter=['serviceMonitor/user/sm0/0','serviceMonitor/user/sm1/0'],unchangedPublication=True))

    def test_collection_evidence(self):
        validate_agent_collection(self.collection(),'candidate')
        for variant in ['missing-sample','wrong-pod','old-sample','missing-adoption','receiver-scrape','wrong-creator','changed-version','same-config','wrong-candidate','missing-informer','missing-boundaries','wrong-run']:
            with self.subTest(collection=variant):
                receipt=self.collection();g=receipt['groups'][0]
                if variant=='missing-sample': g['samples'].pop()
                elif variant=='wrong-pod': g['samples'][0]['metric']['uid']='another-pod'
                elif variant=='old-sample': g['samples'][0]['sampleTime']=99
                elif variant=='missing-adoption': g['adoptedJobs']=[]
                elif variant=='receiver-scrape': receipt['receiver']['scrapeConfigs']=[dict(job_name='self-scrape')]
                elif variant=='wrong-creator': g['exports'][0]['creatorUserId']='admin'
                elif variant=='changed-version': receipt['dynamic']['versionAfter']='v2'
                elif variant=='same-config': receipt['dynamic']['newConfigSHA256']='before-user'
                elif variant=='wrong-candidate': receipt['testedCommit']='other'
                elif variant=='missing-informer': receipt['operator']['scrapeConfigInformerObserved']=False
                elif variant=='missing-boundaries': del receipt['previewBoundaries']
                elif variant=='wrong-run': receipt['runId']='other-run'
                with self.assertRaises(ValueError): validate_agent_collection(receipt,'candidate','mm-ci-1-1-topology')

    def test_candidate(self):
        with self.assertRaises(ValueError):
            validate_specs(self.report(), ['E2E-01'], 'candidate', 'other')

    def test_cleanup(self):
        recorded = {'uid': 'original', 'runId': 'this-run'}
        self.assertTrue(owned_identity('this-run', recorded, recorded))
        for actual in [{'uid': 'replacement', 'runId': 'this-run'}, {'uid': 'original', 'runId': 'other'}]:
            self.assertFalse(owned_identity('this-run', recorded, actual))
        # 用真实Service身份校验读取0→1 Ready快照；同名替代或多个endpoint仍须立即拒绝。
        def fixture(states):
            suite=object.__new__(ClusterSuite);suite.ns='owned';suite.pod='platform';suite.platform_pod_uid='pod-uid'
            suite.a=SimpleNamespace(control_context='kind-owned');reads=[]
            pod=dict(metadata=dict(uid='pod-uid',name='platform'),status=dict(podIP='10.0.0.1',conditions=[dict(type='Ready',status='True')]))
            service=dict(metadata=dict(uid='service-uid'),spec=dict(selector=dict(app='platform')))
            endpoint=dict(conditions=dict(ready=True),targetRef=dict(kind='Pod',namespace='owned',name='platform',uid='pod-uid'),addresses=['10.0.0.1'])
            def k(*args):
                resource=args[3]
                if resource=='pod': value=pod
                elif resource=='service': value=service
                elif resource=='pods': value=dict(items=[pod])
                else:
                    state=states[min(len(reads),len(states)-1)];reads.append(state)
                    pending=copy.deepcopy(endpoint);pending['conditions']['ready']=False;points=[pending]
                    if state!='pending':
                        point=copy.deepcopy(endpoint)
                        if state=='replacement': point['targetRef']['uid']='replacement'
                        points=[point]* (2 if state=='multiple' else 1)
                    value=dict(items=[dict(metadata=dict(uid='slice-uid',ownerReferences=[dict(uid='service-uid',kind='Service')]),endpoints=points)])
                return SimpleNamespace(stdout=json.dumps(value).encode())
            suite.k=k
            return suite,reads
        suite,reads=fixture(['pending','ready'])
        self.assertEqual(suite.wait_service_identity()['podUID'],'pod-uid');self.assertEqual(len(reads),2)
        for state in ['replacement','multiple','pending']:
            suite,reads=fixture([state])
            with self.assertRaises(AssertionError): suite.wait_service_identity(timeout=0)
            self.assertEqual(len(reads),1)


if __name__ == '__main__': unittest.main()
