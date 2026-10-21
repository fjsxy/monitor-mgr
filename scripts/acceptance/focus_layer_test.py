"""精确分层增量选择的Host协议；不运行产品Q、真实K/B或任何目标集群。"""
from collections import Counter
import contextlib
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import gate
import prepare
import protocol_cases
import registry
import run as entrypoint
from session import atomic_json


# 已发布计划的独立20键；不是从被测选择函数反推预期。
REMAINING = [
    ('REG18-PROJECTION-013.proxy-parse-error', 'K'), ('REG18-PROJECTION-013.url-parse-error', 'K'),
    ('REG18-SOURCE-007', 'B'), ('REG19-AGENT-DELETE-OP-001', 'B'), ('REG19-AGENT-HISTORY-OP-001', 'B'),
    ('REG20-STORAGE-001.dispatch-race', 'K'), ('REG20-STORAGE-001.forbidden', 'K'),
    ('REG20-STORAGE-001.new-name', 'K'), ('REG20-STORAGE-001.orphan', 'K'),
    ('REG20-STORAGE-001.owned-retain-shard', 'K'), ('REG20-STORAGE-001.owned-retain-suspend', 'K'),
    ('REG20-STORAGE-001.timeout', 'K'), ('REG21-LEDGER-009.uid-replaced', 'K'),
    ('REG21-LEDGER-010.read-failure', 'K'), ('REG21-RECOVER-008.kill-then-recover', 'K'),
    ('REG21-RESULT-005.setup-failure', 'K'), ('REG21-RESULT-006.cleanup-failure', 'K'),
    ('REG21-SIGNAL-002.term-after-setup', 'K'), ('REG21-SIGNAL-003.keyboard-interrupt', 'K'),
    ('REG21-SIGNAL-004.repeat-during-cleanup', 'K'),
]


def cli(*args):
    """走真实argparse/公开入口；只捕获本Host命令的JSON与错误退出。"""
    output, errors = io.StringIO(), io.StringIO()
    with patch.object(sys, 'argv', ['run.py', *args]), contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
        try: code = entrypoint.main()
        except SystemExit as error: code = error.code
    return code, output.getvalue(), errors.getvalue()


class FocusLayer(unittest.TestCase):
    def test_owner_and_layer_select_only_requested_business_without_mutating_profile(self):
        """组件 Make 入口使用所属 Issue 与层级交集，空交集不能退化成全量执行。"""
        code, output, errors = cli('list', '--profile', 'LOCAL_FULL', '--focus-owner', '16', '--focus-layer', 'B')
        self.assertEqual(code, 0, errors)
        selected = json.loads(output)
        self.assertTrue(selected)
        self.assertTrue(all(e['owner'] == 16 and e['layer'] == 'B' for e in selected))
        self.assertEqual(len(registry.entries('LOCAL_FULL')), 207)
        code, output, errors = cli('list', '--profile', 'CI_FAST', '--focus-owner', '16', '--focus-layer', 'B')
        self.assertNotEqual(code, 0)
        self.assertFalse(json.loads(output)['success'])

    def test_list_intersection_is_exact_twenty_and_does_not_select_overlapping_q(self):
        """REG21-FOCUS-LAYER-001：仅17K3B；两013 K与SOURCE B的同ID Q不得被选中。"""
        code, output, errors = cli('list', '--profile', 'LOCAL_FULL', '--supplemental',
            '--focus', *[v for v, _ in REMAINING], '--focus-layer', 'K', 'B')
        self.assertEqual(code, 0, errors)
        actual = json.loads(output)
        self.assertEqual([(e['variantId'], e['layer']) for e in actual], REMAINING)
        self.assertEqual(Counter(e['layer'] for e in actual), {'K': 17, 'B': 3})
        baseline = registry.entries('LOCAL_FULL', True)
        self.assertEqual(len(baseline), 104)
        self.assertTrue(all(e == next(v for v in baseline if (v['variantId'], v['layer']) == (e['variantId'], e['layer'])) for e in actual))

    def test_default_cli_and_focus_without_layer_keep_existing_contracts(self):
        for profile, supplemental in [('LOCAL_FULL', False), ('CI_FAST', False), ('CI_SMOKE', False), ('LOCAL_FULL', True)]:
            with self.subTest(profile=profile, supplemental=supplemental):
                code, output, errors = cli('list', '--profile', profile, *(['--supplemental'] if supplemental else []))
                self.assertEqual(code, 0, errors)
                self.assertEqual(json.loads(output), registry.entries(profile, supplemental))
        code, output, _ = cli('list', '--profile', 'LOCAL_FULL', '--supplemental',
                              '--focus', *[v for v, _ in REMAINING])
        self.assertEqual(code, 0)
        self.assertEqual(Counter(e['layer'] for e in json.loads(output)), {'Q': 3, 'K': 17, 'B': 3})

    def test_invalid_layers_and_empty_intersection_reject_before_environment_or_dispatch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); output = root / 'not-created'
            common = ['--profile', 'LOCAL_FULL', '--supplemental']
            cases = [['--focus-layer', 'X'], ['--focus-layer'], ['--focus-layer', 'K', 'K'],
                     ['--focus', 'REG18-SOURCE-007', '--focus-layer', 'K']]
            with patch('transport.ProcessRunner.run', side_effect=AssertionError('非法选择不能派发外部进程')) as process:
                for options in cases:
                    for command in ['list', 'run']:
                        with self.subTest(options=options, command=command):
                            required = ['--candidate', '0' * 40, '--run-id', 'host-selection', '--output', str(output)] if command == 'run' else []
                            code, _, _ = cli(command, *common, *required, *options)
                            self.assertNotEqual(code, 0)
                process.assert_not_called()
            self.assertFalse(output.exists())
        for profile, layer in [('CI_FAST', 'K'), ('CI_SMOKE', 'Q')]:
            self.assertNotEqual(cli('list', '--profile', profile, '--focus-layer', layer)[0], 0)

    def test_run_dispatch_matches_list_keeps_104_seed_and_partial_strict_failure(self):
        """真实batch/index/关闭判定；Git、环境快照与child结果是隔离Host系统边界输入。"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); output = root / 'run'; tree = '2' * 40
            commit = b'tree ' + tree.encode() + b'\n\nHost synthetic commit\n'
            candidate = hashlib.sha1(b'commit ' + str(len(commit)).encode() + b'\0' + commit).hexdigest()
            environment = {'source': candidate, 'profile': 'LOCAL_FULL', 'runId': 'host-environment-owner',
                           'targets': {}, 'kubectl': 'HOST_ONLY_NO_EXEC'}
            env_path = root / 'environment.json'; env_path.write_text(json.dumps(environment))
            dispatched, initial = [], []
            def git(argv, **kwargs):
                if argv == ['git', 'rev-parse', 'HEAD']: return candidate
                if argv == ['git', 'rev-parse', 'HEAD^{tree}']: return tree
                if argv == ['git', 'status', '--porcelain']: return ''
                if argv == ['git', 'cat-file', 'commit', candidate]: return commit
                raise AssertionError('Host只允许已声明的Git边界')
            def observe(entry):
                if not initial: initial.append(json.loads((output / 'index.json').read_text()))
                dispatched.append((entry['variantId'], entry['layer']))
            def child(session, entry, index, supplemental):
                observe(entry)
                # 只模拟外部child的结束消息；没有目标API、Pod或真实case通过声明。
                session.store(entry, {**session.header(entry), 'outcome': 'passed', 'diagnosticOnly': True,
                    'productGate': False, 'cleanup': {'success': True}, 'evidenceRefs': []})
            def protocol(ctx, entry):
                observe(entry)
                value = {'outcome': 'passed', 'diagnosticOnly': True, 'productGate': False,
                         'cleanup': {'success': True}, 'evidenceRefs': []}
                atomic_json(Path(ctx.request['output']) / 'receipt.json', value)
                return value
            # 关键：这里只声明合成LOCAL_FULL的host输入；不能借运行Host的平台或修改真实profile guard。
            with patch.object(entrypoint.sys, 'platform', 'darwin'), \
                 patch.object(entrypoint.platform, 'machine', return_value='arm64'), \
                 patch.object(entrypoint.subprocess, 'check_output', side_effect=git), \
                 patch.object(prepare, 'verify_environment', return_value=environment), \
                 patch.object(entrypoint, 'run_business_child', side_effect=child), \
                 patch.object(protocol_cases, 'run_case', side_effect=protocol), \
                 patch('transport.ProcessRunner.run', side_effect=AssertionError('Host不能派发Go/Kube/Docker业务进程')) as process:
                code, stdout, errors = cli('run', '--profile', 'LOCAL_FULL', '--supplemental',
                    '--candidate', candidate, '--run-id', 'host-layer-selection', '--output', str(output),
                    '--environment-json', str(env_path), '--api-lane', str(root / 'host-lane'),
                    '--focus', *[v for v, _ in REMAINING], '--focus-layer', 'K', 'B')
                process.assert_not_called()
            self.assertEqual(code, 1, errors)
            self.assertEqual(dispatched, REMAINING, json.loads((output / 'index.json').read_text()).get('failure'))
            self.assertEqual(len(initial[0]['expectedKeys']), 104)
            self.assertTrue(all(v['outcome'] == 'not_run' for v in initial[0]['results']))
            final = json.loads((output / 'index.json').read_text())
            self.assertEqual(final['expectedKeys'], initial[0]['expectedKeys'])
            self.assertEqual(Counter(v['outcome'] for v in final['results']), {'passed': 20, 'not_run': 84})
            self.assertTrue(all(v['outcome'] == 'not_run' for v in final['results'] if v['layer'] == 'Q'))
            self.assertEqual(final['state'], 'failed'); self.assertTrue(final['focused'])
            self.assertTrue(final['cleanup']['success']); self.assertNotIn('failure', final)
            self.assertFalse(json.loads(stdout)['productGate'])
            with self.assertRaises(gate.GateError) as failure:
                gate.validate(output / 'index.json', candidate, 'LOCAL_FULL', True)
            self.assertEqual(failure.exception.code, 'incomplete-run')

    def test_real_mismatched_host_rejects_before_environment_or_business_dispatch(self):
        """REG21-FOCUS-LAYER-002：真实host不符仍拒绝，不能把合成声明扩散到正式入口。"""
        for host, machine, arch in [('linux', 'x86_64', 'amd64'), ('linux', 'aarch64', 'arm64'),
                                    ('darwin', 'x86_64', 'amd64')]:
            with self.subTest(host=host, machine=machine), tempfile.TemporaryDirectory() as directory:
                root = Path(directory); output = root / 'rejected'; tree = '3' * 40
                commit = b'tree ' + tree.encode() + b'\n\nHost mismatch synthetic commit\n'
                candidate = hashlib.sha1(b'commit ' + str(len(commit)).encode() + b'\0' + commit).hexdigest()
                def git(argv, **kwargs):
                    if argv == ['git', 'rev-parse', 'HEAD']: return candidate
                    if argv == ['git', 'rev-parse', 'HEAD^{tree}']: return tree
                    if argv == ['git', 'status', '--porcelain']: return ''
                    if argv == ['git', 'cat-file', 'commit', candidate]: return commit
                    raise AssertionError('Host只允许已声明的Git边界')
                # 关键：保留真实host guard；任何后续环境、业务或外部进程动作都使本回归失败。
                with patch.object(entrypoint.sys, 'platform', host), \
                     patch.object(entrypoint.platform, 'machine', return_value=machine), \
                     patch.object(entrypoint.subprocess, 'check_output', side_effect=git), \
                     patch.object(prepare, 'verify_environment', side_effect=AssertionError('host拒绝前不能读目标环境')) as verify, \
                     patch.object(entrypoint, 'run_business_child', side_effect=AssertionError('host拒绝不能派发业务')) as child, \
                     patch.object(protocol_cases, 'run_case', side_effect=AssertionError('host拒绝不能派发协议case')) as protocol, \
                     patch('transport.ProcessRunner.run', side_effect=AssertionError('host拒绝不能派发外部进程')) as process:
                    code, stdout, errors = cli('run', '--profile', 'LOCAL_FULL', '--supplemental',
                        '--candidate', candidate, '--run-id', 'host-profile-rejection', '--output', str(output),
                        '--api-lane', str(root / 'host-lane'), '--focus', *[v for v, _ in REMAINING],
                        '--focus-layer', 'K', 'B')
                    verify.assert_not_called(); child.assert_not_called()
                    protocol.assert_not_called(); process.assert_not_called()
                self.assertEqual(code, 1, errors)
                final = json.loads((output / 'index.json').read_text())
                self.assertEqual(final['environment'], {'runtimeOS': host, 'runtimeArch': arch, 'targets': []})
                self.assertEqual(final['failure'], {'type': 'ValueError', 'stage': 'source_verification'})
                self.assertEqual(len(final['expectedKeys']), 104)
                self.assertEqual(Counter(v['outcome'] for v in final['results']), {'environment_failed': 1, 'not_run': 103})
                self.assertEqual(final['state'], 'failed'); self.assertTrue(final['cleanup']['success'])
                self.assertFalse(json.loads(stdout)['productGate'])


if __name__ == '__main__':
    unittest.main()
