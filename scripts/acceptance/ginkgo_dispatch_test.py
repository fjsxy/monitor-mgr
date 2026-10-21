"""调度器 Host 自测：运行真实 Ginkgo child，但业务回调只操作临时文件。"""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from ginkgo_dispatch import run_specs
from transport import ProcessRunner


class GinkgoDispatchTests(unittest.TestCase):
    def run_fixture(self, outcomes, *, fail_fast=False):
        """先建立私有 Session 替身，再让真实 Go It 逐键请求确定性回调。"""
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name)
        private = root / '.private'
        private.mkdir(mode=0o700)
        entries = [dict(variantId='HOST-' + str(i), layer='K') for i in range(len(outcomes))]
        session = SimpleNamespace(out=root, private=private, index={'results': []}, checkpoint=lambda: None, flush=lambda: None)
        session.processes = ProcessRunner(private / 'processes')
        self.addCleanup(session.processes.close_auxiliary)
        called = []
        def execute(entry):
            called.append(entry['variantId'])
            session.index['results'].append({**entry, 'outcome': outcomes[int(entry['variantId'].split('-')[1])]})
        if all(value == 'passed' for value in outcomes):
            run_specs(session, entries, execute, fail_fast=fail_fast)
        else:
            with self.assertRaisesRegex(ValueError, 'actual-ginkgo-e2e-not-passed'):
                run_specs(session, entries, execute, fail_fast=fail_fast)
        report = json.loads((root / session.index['ginkgoDispatchRef']['path']).read_text())
        return called, report

    def test_real_specs_dispatch_each_key_once_and_keep_order(self):
        called, report = self.run_fixture(['passed', 'passed'])
        self.assertEqual(called, ['HOST-0', 'HOST-1'])
        self.assertEqual(report['commandExitCode'], 0)
        self.assertEqual([s['state'] for s in report['specs']], ['passed', 'passed'])
        self.assertTrue(all(s['attempts'] == 1 for s in report['specs']))

    def test_business_failure_is_a_ginkgo_failure_and_next_case_still_runs(self):
        called, report = self.run_fixture(['failed', 'passed'])
        self.assertEqual(called, ['HOST-0', 'HOST-1'])
        self.assertNotEqual(report['commandExitCode'], 0)
        self.assertEqual([s['state'] for s in report['specs']], ['failed', 'passed'])

    def test_fail_fast_keeps_remaining_case_skipped(self):
        called, report = self.run_fixture(['failed', 'passed'], fail_fast=True)
        self.assertEqual(called, ['HOST-0'])
        self.assertEqual(report['specs'][1]['state'], 'skipped')

    def test_duplicate_plan_fails_before_starting_child(self):
        session = SimpleNamespace()
        with self.assertRaisesRegex(ValueError, 'duplicate-ginkgo-dispatch-key'):
            run_specs(session, [dict(variantId='HOST', layer='K')] * 2, lambda entry: self.fail('不应派发'))
