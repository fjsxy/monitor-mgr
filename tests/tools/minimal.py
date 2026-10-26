"""UT-18 仅保留三组会使测试结论失真的风险。"""
import copy
import json
from pathlib import Path
import sys
import tempfile
import subprocess
import time
from types import SimpleNamespace
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
from testing_contract import owned_identity, validate_specs
from e2e_shards import SHARDS, aggregate, selection
from e2e_diagnostics import failure_summary
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts/acceptance'))
from clusters import Suite as ClusterSuite


class TrustBoundary(unittest.TestCase):
    def test_browser_failure_projection(self):
        """浏览器失败只投影固定步骤、可信源码位置及状态枚举，任意报告内容仍私有。"""
        from browser_diagnostics import browser_summary
        root = Path(__file__).resolve().parents[2]
        location = dict(file=str(root/'web/tests/phase1.spec.ts'),line=50,column=1)
        secret = 'PRIVATE_FIXTURE_TOKEN_SENTINEL'
        error = dict(message='Expected: "Ready"\nReceived: "Reconciling"\n'+secret, location=location, stack=secret)
        step = dict(title='查看 diff，明确确认后才发布到真实集群',error=error,steps=[dict(title=secret,error=error)])
        result = dict(status='failed',steps=[step],stdout=[secret],stderr=[secret],attachments=[dict(path=secret)])
        report = dict(suites=[dict(specs=[dict(tests=[dict(results=[result])])])],stats=dict(expected=0,unexpected=1,skipped=0))
        value = browser_summary(report,root)
        self.assertEqual(value['failures'][0]['step'],step['title'])
        self.assertEqual(value['failures'][0]['file'],'web/tests/phase1.spec.ts')
        self.assertEqual(value['failures'][0]['expected'],'Ready')
        self.assertEqual(value['failures'][0]['actual'],'Reconciling')
        self.assertNotIn(secret,json.dumps(value));self.assertNotIn(str(root),json.dumps(value))
        step['title']=secret;step.pop('steps');error['location']['file']='/private/'+secret
        error['message']='Expected: "'+secret+'"\nReceived: "'+secret+'"'
        value=browser_summary(report,root)
        self.assertEqual(value['failures'][0]['step'],'unknown')
        self.assertEqual(value['failures'][0]['expected'],'unknown')
        self.assertEqual(value['failures'][0]['actual'],'unknown')
        self.assertNotIn(secret,json.dumps(value));self.assertNotIn('file',value['failures'][0])

    def test_fixture_namespace_names(self):
        """长CI/run-id派生namespace稳定且保留唯一性，短名保持原可读身份。"""
        import re
        from fixture_names import namespace_name
        self.assertEqual(namespace_name('mm-short-c8-target-historical-migration'), 'mm-short-c8-target-historical-migration')
        values = []
        for run in ['mm-ci-40333823884-1-recovery', 'mm-'+'a'*36, 'mm-'+'a'*35+'b']:
            exporter = namespace_name(run+'-c8-target-historical-migration')
            collector = namespace_name(exporter+'-agent')
            for name in [exporter, collector]:
                self.assertLessEqual(len(name),63)
                self.assertRegex(name,r'^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$')
                self.assertEqual(namespace_name(name),name)
            self.assertNotEqual(exporter,collector)
            self.assertEqual(namespace_name(exporter+'-agent'),collector)
            values.append(collector)
        self.assertEqual(len(set(values)),3)

    def test_capture_failure_tail(self):
        """大体积原日志的末尾失败和超时仍须经冻结源码核验后安全投影。"""
        from capture import project_output
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); private = root/'private'
            (root/'internal/platform').mkdir(parents=True)
            (root/'internal/platform/consolidated_test.go').write_text('package platform_test\nvar _ = ginkgo.It("UT-18 来源边界", func() {})\n')
            (root/'Makefile').write_text('ut:\n\tgo test ./...\n')
            # 只在临时目录冻结本例静态源码，不读取用户Git身份或远端凭据。
            def git(*args):
                return subprocess.run(['git','-C',str(root),*args],check=True,capture_output=True).stdout.decode().strip()
            git('init','--quiet');git('add','Makefile','internal/platform/consolidated_test.go')
            git('-c','user.name=验收夹具','-c','user.email=fixture@example.invalid','commit','--quiet','-m','test: 冻结诊断夹具')
            candidate = git('rev-parse','HEAD')
            output = b'x\n'*600000+b'panic: test timed out after 15m0s\n[FAIL] [It] UT-18 PRIVATE_FAILURE_MESSAGE\n'
            result = subprocess.CompletedProcess([],2,output,b'make: *** [Makefile:2: ut] Error 1\n')
            result.capture_started_at=time.time()
            report=root/'internal/platform/.scratch/ut.json';report.parent.mkdir()
            report.write_text(json.dumps([dict(SpecReports=[dict(LeafNodeType='It',LeafNodeText='UT-18 PRIVATE_TITLE',State='failed',NumAttempts=1,Failure=dict(Message='PRIVATE_FAILURE_MESSAGE',Location=dict(FileName=str(root/'internal/platform/consolidated_test.go'),LineNumber=2,FullStackTrace='PRIVATE_STACK')))])]))
            value = project_output(result,label='ut',candidate=candidate,private_root=private,root=root)
            self.assertIn('go-test-timeout',value['observedMarkers'])
            self.assertTrue(any(v.get('caseId')=='UT-18' for v in value['testFailures']))
            self.assertTrue(any(v['file']=='Makefile' and v['line']==2 for v in value['repoFrames']))
            self.assertEqual(value['nativeFailures'][0]['caseId'],'UT-18')
            self.assertEqual(value['nativeFailures'][0]['state'],'failed')
            self.assertNotIn('PRIVATE_',json.dumps(value))

    def test_environment_command_diagnostic(self):
        """公开命令失败必须可定位，未知标签和私有输出不能穿越安全边界。"""
        from e2e_environment import Environment
        for label, delay, stage, code in [('fetch-public-release-source', False, 'fetch-public-release-source', 'E2E_COMMAND_FAILED'),
                ('fetch-published-operator', False, 'fetch-published-operator', 'E2E_COMMAND_FAILED'),
                ('build-platform', False, 'build-platform', 'BUILD_COMMAND_FAILED'),
                ('PRIVATE_TOKEN_/private/path', False, 'unknown', 'E2E_COMMAND_FAILED'),
                ('fetch-public-release-source', True, 'fetch-public-release-source', 'E2E_COMMAND_TIMEOUT')]:
            with self.subTest(label=label, timeout=delay), tempfile.TemporaryDirectory() as temporary:
                env = Environment(Path(temporary)/'run', 'mm-test', 'candidate')
                child = 'import sys,time; print("PRIVATE_OUTPUT_SENTINEL", flush=True); '+('time.sleep(10)' if delay else 'sys.exit(17)')
                with self.assertRaises(RuntimeError) as raised:
                    try:
                        env.command([sys.executable, '-c', child], label, timeout=.1 if delay else 5)
                    except RuntimeError as error:
                        source = failure_summary(error)
                        raise
                diagnostic = json.loads((env.public/'command-failure.json').read_text())
                self.assertEqual(diagnostic, dict(operation=stage, exit=-9 if delay else 17, errorCode=code))
                self.assertNotIn('PRIVATE_', json.dumps(diagnostic))
                self.assertTrue(any(v['file']=='scripts/e2e_environment.py' for v in source['frames']))
                self.assertNotIn('PRIVATE_', json.dumps(source))
                captures = list((env.private/'processes').iterdir())
                self.assertTrue(any(b'PRIVATE_OUTPUT_SENTINEL' in p.read_bytes() for p in captures))
                self.assertTrue(all(p.stat().st_mode & 0o077 == 0 for p in captures))

    def report(self):
        return [{'SuiteSucceeded': True, 'SpecReports': [
            {'LeafNodeType': 'It', 'LeafNodeText': 'E2E-01 示例', 'State': 'passed', 'NumAttempts': 1}]}]

    def test_events(self):
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
        self.assertEqual(len(aggregate(reports, 'candidate', 'mm-ci-1-1', 'success')['results']), 10)
        self.assertEqual(selection(), [f'E2E-{n:02d}' for n in range(1, 11)])
        with self.assertRaises(ValueError): selection('unknown')
        for kind in ['missing-shard', 'duplicate-shard', 'job-failed', 'job-skipped', 'wrong-candidate', 'wrong-run',
                     'wrong-group', 'missing', 'duplicate', 'failed', 'skipped', 'retry', 'not_run', 'cleanup', 'case-cleanup', 'full-report']:
            with self.subTest(shard_failure=kind):
                broken=copy.deepcopy(reports); first=broken[0]; id=first['assigned'][0]; jobs='success'
                if kind == 'missing-shard': broken.pop()
                elif kind == 'duplicate-shard': broken.append(copy.deepcopy(first))
                elif kind.startswith('job-'): jobs=kind[4:]
                elif kind == 'wrong-candidate': first['candidate']='other'
                elif kind == 'wrong-run': first['runId']='mm-other'
                elif kind == 'wrong-group': first['assigned']=selection('storage')
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
