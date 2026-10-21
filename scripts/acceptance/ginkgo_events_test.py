"""原生 Ginkgo 报告的正负控：通过声明不能替代实际执行与完整子例。"""
import json
import unittest

import ginkgo_events


class GinkgoEventsTests(unittest.TestCase):
    def fixture(self):
        return [dict(SuiteSucceeded=True, SuiteConfig={'DryRun': False}, SpecReports=[dict(
            LeafNodeType='It', LeafNodeText='TestFixture', State='passed', NumAttempts=1,
            CapturedStdOutErr='private-auth-canary', Failure={'Message': 'private-auth-canary'},
            ReportEntries=[dict(Name='完成子例', Value={'AsJSON': json.dumps('TestFixture/required')})])])]

    def test_native_report_retains_root_and_completed_child_without_private_text(self):
        events = ginkgo_events.project(json.dumps(self.fixture()), {'TestFixture'}, 0)
        self.assertEqual(ginkgo_events.passed(events), {'TestFixture', 'TestFixture/required'})
        self.assertNotIn('private-auth-canary', json.dumps(events))

    def test_skip_failed_retry_dry_run_and_nonzero_exit_cannot_pass(self):
        mutations = [lambda r: r[0]['SuiteConfig'].update(DryRun=True),
                     lambda r: r[0].update(SuiteSucceeded=False),
                     lambda r: r[0]['SpecReports'][0].update(State='skipped'),
                     lambda r: r[0]['SpecReports'][0].update(State='failed'),
                     lambda r: r[0]['SpecReports'][0].update(NumAttempts=0),
                     lambda r: r[0]['SpecReports'][0].update(NumAttempts=2),
                     lambda r: r[0].update(SuiteHasProgrammaticFocus=True)]
        for mutate in mutations:
            value = self.fixture()
            mutate(value)
            with self.subTest(value=value):
                self.assertFalse(ginkgo_events.passed(ginkgo_events.project(json.dumps(value), {'TestFixture'}, 0)))
        self.assertFalse(ginkgo_events.passed(ginkgo_events.project(json.dumps(self.fixture()), {'TestFixture'}, 1)))

    def test_missing_or_duplicate_children_are_not_passing(self):
        value = self.fixture()
        value[0]['SpecReports'][0]['ReportEntries'] = []
        self.assertNotIn('TestFixture/required', ginkgo_events.passed(ginkgo_events.project(json.dumps(value), {'TestFixture'}, 0)))
        value = self.fixture()
        value[0]['SpecReports'][0]['ReportEntries'] *= 2
        self.assertFalse(ginkgo_events.passed(ginkgo_events.project(json.dumps(value), {'TestFixture'}, 0)))
        value = self.fixture()
        value[0]['SpecReports'] *= 2
        self.assertFalse(ginkgo_events.passed(ginkgo_events.project(json.dumps(value), {'TestFixture'}, 0)))

    def test_old_stdout_empty_report_and_unknown_root_cannot_pass(self):
        for raw in ['[]', '{}', '', json.dumps({'Action': 'pass', 'Test': 'TestFixture'})]:
            self.assertEqual(ginkgo_events.project(raw, {'TestFixture'}, 0), [])
        self.assertEqual(ginkgo_events.project(json.dumps(self.fixture()), {'Unknown'}, 0), [])
        with self.assertRaisesRegex(ValueError, 'empty-ginkgo-selection'):
            ginkgo_events.command([], '/tmp/unused.json')
