"""UT-18 仅保留三组会使测试结论失真的风险。"""
import copy
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
from testing_contract import owned_identity, validate_specs
from e2e_shards import SHARDS, aggregate, selection


class TrustBoundary(unittest.TestCase):
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

    def test_candidate(self):
        with self.assertRaises(ValueError):
            validate_specs(self.report(), ['E2E-01'], 'candidate', 'other')

    def test_cleanup(self):
        recorded = {'uid': 'original', 'runId': 'this-run'}
        self.assertTrue(owned_identity('this-run', recorded, recorded))
        for actual in [{'uid': 'replacement', 'runId': 'this-run'}, {'uid': 'original', 'runId': 'other'}]:
            self.assertFalse(owned_identity('this-run', recorded, actual))


if __name__ == '__main__': unittest.main()
