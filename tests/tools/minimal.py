"""UT-18 仅保留三组会使测试结论失真的风险。"""
import copy
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
from testing_contract import owned_identity, validate_specs


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

    def test_candidate(self):
        with self.assertRaises(ValueError):
            validate_specs(self.report(), ['E2E-01'], 'candidate', 'other')

    def test_cleanup(self):
        recorded = {'uid': 'original', 'runId': 'this-run'}
        self.assertTrue(owned_identity('this-run', recorded, recorded))
        for actual in [{'uid': 'replacement', 'runId': 'this-run'}, {'uid': 'original', 'runId': 'other'}]:
            self.assertFalse(owned_identity('this-run', recorded, actual))


if __name__ == '__main__': unittest.main()
