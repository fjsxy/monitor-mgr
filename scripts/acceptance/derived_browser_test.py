"""派生证据的 Host 字节协议；validator-test 绝不冒充真实父 FULL。"""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import types
import unittest

import gate
import registry
from gate_test import CANDIDATE, fixture, ref
from derived_browser import browser_pass, copy_closure, derive, pattern_proof


class DerivedBrowserProtocol(unittest.TestCase):
    def pattern(self):
        values = ['a', 'user.name', 'user-name', 'user_name', 'aA09._-', '中文', 'a b', 'a/b', '-name', 'a' * 64]
        form = {'pattern': '[A-Za-z][A-Za-z0-9._\\-]{0,62}', 'compilationError': '',
                'inputs': [{'value': v, 'valid': i < 5, 'patternMismatch': i >= 5} for i, v in enumerate(values)]}
        return {'browser-console.json': {'console': [], 'pageErrors': [], 'httpErrors': []},
                'browser-layout.json': {'viewport': 1200, 'scrollWidth': 1200},
                'account-pattern-login.json': form, 'account-pattern-create.json': copy.deepcopy(form)}

    def test_pattern_reads_all_twenty_actual_inputs(self):
        result = pattern_proof(self.pattern())
        self.assertEqual(result['inputCount'], 20)
        self.assertEqual(set(result['forms']), {'login', 'create'})
        self.assertNotIn('outcome', result)

    def test_pattern_missing_attachment_or_wrong_dom_or_compilation_is_rejected(self):
        for kind in ['missing', 'dom', 'compile', 'mismatch', 'console', 'layout']:
            with self.subTest(kind=kind):
                value = self.pattern()
                if kind == 'missing':del value['browser-console.json']
                elif kind == 'dom':value['account-pattern-create.json']['inputs'][0]['value'] = 'fake'
                elif kind == 'compile':value['account-pattern-login.json']['compilationError'] = 'SyntaxError'
                elif kind == 'mismatch':value['account-pattern-login.json']['inputs'][-1]['patternMismatch'] = False
                elif kind == 'console':value['browser-console.json']['pageErrors'] = ['actual error']
                else:value['browser-layout.json']['scrollWidth'] = 1201
                with self.assertRaises(ValueError):pattern_proof(value)

    def test_copy_closure_preserves_raw_parent_json_and_rejects_changed_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory);parent = root / 'parent';parent.mkdir();new = root / 'new';new.mkdir()
            attachment = parent / 'attachment.json';attachment.write_bytes(b'{"actual": true}\n')
            original = parent / 'original.json';original.write_bytes(json.dumps({'evidenceRefs': [ref(attachment, parent)]}).encode())
            wrapper = parent / 'wrapper.json';wrapper.write_bytes(json.dumps({'originalReceipt': ref(original, parent), 'evidenceRefs': [ref(attachment, parent)]}).encode())
            prefix = 'parents/fixed/' + 'a' * 64
            refs = copy_closure(gate.Evidence(parent), ref(wrapper, parent, 'receipt'), new, prefix)
            self.assertEqual((new / prefix / 'wrapper.json').read_bytes(), wrapper.read_bytes())
            self.assertEqual((new / prefix / 'original.json').read_bytes(), original.read_bytes())
            self.assertEqual(len(refs), 3)
            self.assertTrue(all(item['path'].startswith(prefix + '/') for item in refs))
            expected = ref(wrapper, parent, 'receipt');wrapper.write_bytes(b'{}')
            with self.assertRaises(ValueError):copy_closure(gate.Evidence(parent), expected, new, 'parents/fixed/' + 'b' * 64)

    def test_real_gate_refuses_validator_fixture_without_creating_derived_passing_record(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory);parent = root / 'parent';parent.mkdir();index = fixture(parent, 'LOCAL_FULL')
            out = root / 'new';out.mkdir()
            session = types.SimpleNamespace(out=out, index={'candidateCommit': CANDIDATE, 'profile': 'LOCAL_FULL', 'supplemental': True})
            entry = next(e for e in registry.entries('LOCAL_FULL', True) if e['variantId'] == 'REG18-PATTERN-011')
            with self.assertRaises(gate.GateError):derive(session, entry, index)
            self.assertEqual(list(out.iterdir()), [])

    def test_old_source_is_rejected_by_real_parent_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory);parent = root / 'parent';parent.mkdir();index = fixture(parent, 'LOCAL_FULL')
            out = root / 'new';out.mkdir()
            session = types.SimpleNamespace(out=out, index={'candidateCommit': 'e' * 40, 'profile': 'LOCAL_FULL', 'supplemental': True})
            entry = next(e for e in registry.entries('LOCAL_FULL', True) if e['variantId'] == 'REG19-HTTP-I64-001' and e['layer'] == 'B')
            with self.assertRaisesRegex(gate.GateError, 'wrong-source'):derive(session, entry, index)
            self.assertEqual(list(out.iterdir()), [])

    def test_i64_requires_actual_substep_selector_without_skip_or_failed_retry(self):
        report={'suites':[{'specs':[{'title':'REG19-HTTP-I64-001 Host原事件协议fixture',
            'tests':[{'status':'expected','results':[{'status':'passed'}]}]}]}]}
        browser_pass(report,'REG19-HTTP-I64-001')
        for mode in ['other-selector','skip','retry-failure','duplicate-success']:
            with self.subTest(mode=mode):
                value=copy.deepcopy(report);spec=value['suites'][0]['specs'][0]
                if mode=='other-selector':spec['title']='U05.primary Host原事件协议fixture'
                elif mode=='skip':spec['tests'][0]['results'][0]['status']='skipped'
                else:spec['tests'][0]['results'].insert(0,{'status':'failed' if mode=='retry-failure' else 'passed'})
                with self.assertRaises(ValueError):browser_pass(value,'REG19-HTTP-I64-001')


if __name__ == '__main__':unittest.main()
