"""平台适配参数边界测试；不运行API或浏览器，不产生产品通过键。"""
import types
import json
import os
import pathlib
import subprocess
import tempfile
import unittest

import platform_cases
import registry
import business


class PlatformCaseParameters(unittest.TestCase):
    def test_real_registry_routes_every_platform_case_in_its_declared_profiles(self):
        """使用真实注册表，避免手工entry掩盖继承owner把T01/T03分派到另一case。"""
        expected=set(platform_cases.K_CASES+platform_cases.B_CASES)
        observed=set()
        for profile in registry.PROFILES:
            for entry in registry.entries(profile):
                if entry['variantId'] not in expected:continue
                with self.subTest(profile=profile,variant=entry['variantId']):
                    route=business.plan(entry)
                    self.assertEqual(route,{'module':'agent','platform':True})
                    self.assertEqual(platform_cases._entry(entry),entry)
                    observed.add(entry['variantId'])
        self.assertEqual(observed,expected)

    def test_initialization_case_keeps_database_uninitialized(self):
        suite = types.SimpleNamespace(a=types.SimpleNamespace())
        platform_cases.before_deploy(suite, dict(variantId='I01.primary', profile='CI_SMOKE'))
        self.assertIs(suite.a.initialize, False)
        platform_cases.before_deploy(suite, dict(variantId='I07.reset', profile='LOCAL_FULL'))
        self.assertIs(suite.a.initialize, True)

    def test_disabled_owner_selects_all_three_existing_tests(self):
        plan = platform_cases.browser_plan(dict(variantId='I07.disabled-owner', profile='LOCAL_FULL'))
        self.assertEqual(plan['spec'], 'tests/clusters.spec.ts')
        self.assertEqual(plan['selectors'], ['I07.disabled-owner.register',
            'I07.disabled-owner.transfer', 'I07.disabled-owner.reactivate'])
        self.assertNotIn('集群生命周期', plan['grep'])

    def test_unknown_cases_and_unavailable_profiles_fail_before_setup(self):
        for case, profile in [('U01.primary', 'LOCAL_FULL'), ('T03.primary', 'CI_SMOKE'),
                              ('I07.reset', 'CI_SMOKE'), ('I01.primary', 'CI_FAST')]:
            suite = types.SimpleNamespace(a=types.SimpleNamespace())
            with self.assertRaises(ValueError):
                platform_cases.before_deploy(suite, dict(variantId=case, profile=profile))

    def test_private_wrapper_collects_the_original_three_tests(self):
        with tempfile.TemporaryDirectory() as directory:
            entry = dict(variantId='I07.disabled-owner', profile='LOCAL_FULL')
            plan = platform_cases.browser_harness(entry, pathlib.Path(directory))
            listed = pathlib.Path(directory) / 'listed.json'
            result = subprocess.run([*plan['command'], '--list', '--reporter=json'],
                cwd=platform_cases.ROOT, capture_output=True, text=True,
                env=dict(os.environ, PLAYWRIGHT_JSON_OUTPUT_FILE=str(listed)))
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            report = json.loads(listed.read_text())
            titles = []
            def visit(value):
                titles.extend(spec['title'].split(' ', 1)[0] for spec in value.get('specs', []))
                for suite in value.get('suites', []):
                    visit(suite)
            visit(report)
            self.assertEqual(titles, ['I07.disabled-owner.register',
                'I07.disabled-owner.transfer', 'I07.disabled-owner.reactivate'])

    def test_all_seven_account_cases_collect_without_launching_browser(self):
        actual = []
        for variant in ['I01.primary', 'I07.reset', 'I07.disable', 'I07.role-change',
                        'I07.last-admin-disable', 'I07.last-admin-demote', 'I07.disabled-owner']:
            with tempfile.TemporaryDirectory() as directory:
                plan = platform_cases.browser_harness(dict(variantId=variant, profile='LOCAL_FULL'), pathlib.Path(directory))
                listed = pathlib.Path(directory) / 'listed.json'
                result = subprocess.run([*plan['command'], '--list', '--reporter=json'],
                    cwd=platform_cases.ROOT, capture_output=True, text=True,
                    env=dict(os.environ, PLAYWRIGHT_JSON_OUTPUT_FILE=str(listed)))
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                def visit(value):
                    actual.extend(spec['title'].split(' ', 1)[0] for spec in value.get('specs', []))
                    for suite in value.get('suites', []):
                        visit(suite)
                visit(json.loads(listed.read_text()))
        self.assertEqual(actual, ['I01.primary', 'I07.reset', 'I07.disable', 'I07.role-change',
            'I07.last-admin-disable', 'I07.last-admin-demote', 'I07.disabled-owner.register',
            'I07.disabled-owner.transfer', 'I07.disabled-owner.reactivate'])


if __name__ == '__main__':
    unittest.main()
