"""固定验收键与真实执行选择器的公开查询测试。"""
import unittest

import registry


class RegistryTests(unittest.TestCase):
    def test_fixed_profiles_are_independently_expanded(self):
        self.assertEqual([len(registry.entries(p)) for p in
                          ['LOCAL_FULL', 'CI_FAST', 'CI_SMOKE']], [207, 58, 17])
        self.assertEqual(len(registry.entries('LOCAL_FULL', supplemental=True)), 104)
        self.assertEqual(len({e['variantId'] for e in registry.entries(
            'LOCAL_FULL', supplemental=True)}), 100)

    def test_focus_rejects_unknown_duplicate_and_wrong_profile(self):
        for profile, focus in [('LOCAL_FULL', ['unknown']),
                               ('LOCAL_FULL', ['T01.primary', 'T01.primary']),
                               ('CI_FAST', ['T01.primary']), ('LOCAL_FULL', [])]:
            with self.assertRaises(ValueError):
                registry.entries(profile, focus=focus)

    def test_disabled_owner_requires_all_three_browser_entries(self):
        entry = registry.entries('LOCAL_FULL', focus=['I07.disabled-owner'])[0]
        self.assertEqual(entry['testSelectors'], ['I07.disabled-owner.register',
            'I07.disabled-owner.transfer', 'I07.disabled-owner.reactivate'])

    def test_only_eight_reg21_k_use_kubernetes_protocol_boundary(self):
        import protocol_cases
        supplemental = registry.entries('LOCAL_FULL', True)
        actual = {e['variantId'] for e in supplemental if e['executionBoundary'] == 'kubernetes-protocol'}
        self.assertEqual(actual, set(protocol_cases.MODES))
        self.assertTrue(all(e['layer'] == 'K' and e['runner'] == 'protocol_cases' for e in supplemental
                            if e['variantId'] in actual))
        self.assertTrue(all(e['executionBoundary'] == 'platform-pod' for e in registry.entries('LOCAL_FULL')
                            if e['layer'] in ['K', 'B']))


if __name__ == '__main__':
    unittest.main()
