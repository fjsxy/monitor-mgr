#!/usr/bin/env python3
"""Agent CRD 故障恢复的三接缝 Host 回归，不产生正式 K/B 通过。"""
import contextlib
import copy
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from agent_cases import global_fault
from execution import ExecutionSession
from kube_transport import KubeFailure, KubeTransport
from ledger import Ledger


class AgentCRDRestoreBoundary(unittest.TestCase):
    @contextlib.contextmanager
    def fixture(self):
        """只替换系统外部 API；使用真实 case、传输、账本与 cleanup。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            original = {
                'apiVersion': 'apiextensions.k8s.io/v1', 'kind': 'CustomResourceDefinition',
                'metadata': {'name': 'prometheusagents.monitoring.coreos.com',
                             'uid': 'original-crd', 'resourceVersion': '1'},
                'spec': {'group': 'monitoring.coreos.com'},
            }
            state = {'object': copy.deepcopy(original), 'original': original, 'creates': 0,
                     'read_error': None, 'lose_response': False, 'before_restore': lambda: None}
            session = ExecutionSession(root / 'run', expected=[], run_id='host-crd',
                profile='validator-test', candidate='fixture', protocol_fixture=True,
                lane=str(root / 'lane'))
            session.ledger = Ledger(root / 'ledger.json', run_id='host-crd',
                candidate='fixture', targets={'data': 'original-cluster'})
            session.ledger.case = 'K:C04.agent-crd'

            def execute(command, *, input=None, **kwargs):
                self.assertEqual(command[:5], ['must-not-execute', '--kubeconfig',
                    'host-only', '--context', 'host-only'])
                args = command[5:]
                if args[0] == 'get':
                    if args[1] in ['crd', 'customresourcedefinitions']:
                        if state['read_error']:
                            return subprocess.CompletedProcess(command, 1, b'', state['read_error'])
                        if state['object'] is None:
                            return subprocess.CompletedProcess(command, 1, b'', b'Error from server (NotFound)')
                        value = state['object']
                    else:
                        value = {'items': []}
                elif args[0] == 'delete':
                    options = json.loads(input)
                    self.assertEqual(options['preconditions']['uid'], state['object']['metadata']['uid'])
                    state['object'] = None
                    value = {}
                elif args[0] == 'create':
                    self.assertIsNone(state['object'])
                    state['creates'] += 1
                    state['object'] = json.loads(input)
                    state['object']['metadata'].update(uid='restored-crd', resourceVersion='2')
                    value = state['object']
                    if state['lose_response']:
                        return subprocess.CompletedProcess(command, 1, b'', b'controlled response unavailable')
                elif args[0] == 'wait':
                    self.assertIsNotNone(state['object'])
                    value = {}
                else:
                    raise AssertionError('Host fixture 未声明的 API 请求')
                return subprocess.CompletedProcess(command, 0, json.dumps(value).encode(), b'')

            session.kube = KubeTransport(ledger=session.ledger, execute=execute,
                targets={'data': {'kubeconfig': 'host-only', 'context': 'host-only'}},
                kubectl='must-not-execute')

            class Suite:
                target = 'data'
                targetns = 'owned-namespace'
                cluster = {'id': 'original-cluster'}
                case = 'C04.agent-crd'

                def k(self, *args, data=None, target=None, check=True):
                    return session.kube.command(target or self.target, list(args), data=data, check=check)

                def get(self, *args):
                    return json.loads(self.k('get', *args, '-o', 'json').stdout)

                def restore_credentials(self):
                    pass

                def call(self, *args):
                    return {'id': 'original-cluster', 'uid': 'original-cluster',
                            'capabilities': [{'component': 'Prometheus Agent', 'allowed': False,
                                              'reason': 'CRD missing'}]}

                def create(self):
                    return {'id': 'fixture-instance'}

                def preview(self, *args, **kwargs):
                    state['restore'] = self.restorers[-1]
                    state['before_restore']()
                    return {'code': 'TARGET_CAPABILITY_MISSING'}

            suite = Suite()
            suite.session = session
            suite.restorers = []
            try:
                yield session, suite, state
            finally:
                session.shutdown()
                session.close()

    def test_deleted_original_crd_is_restored_once_with_its_new_response_uid(self):
        """真实 C04 case 恢复 CRD 并闭合 cleanup，旧 UID 历史不能冒充新创建响应。"""
        with self.fixture() as (session, suite, state):
            evidence = global_fault(suite, 'C04.agent-crd')
            self.assertTrue(evidence['restoredSpecMatches'])
            state['restore']()
            self.assertTrue(session.cleanup_case(suite)['success'])
            self.assertEqual(state['creates'], 1)
            self.assertEqual(state['object']['metadata']['uid'], 'restored-crd')
            entries = list(Ledger.open(session.ledger.path).entries.values())
            baseline = next(e for e in entries if e['action'] == 'fault-baseline')
            restored = next(e for e in entries if e['action'] == 'create')
            self.assertEqual(baseline['uid'], 'original-crd')
            self.assertEqual(restored['uid'], 'restored-crd')
            self.assertEqual(restored['origin'], 'fault-restored')
            self.assertEqual(restored['stage'], 'confirmed')

    def test_restore_rejects_read_failure_and_same_name_replacement(self):
        """目标读取失败不是缺失，同名替代对象不能承接原故障恢复。"""
        for failure, expected in [('forbidden', 'Forbidden'), ('network', 'TransportUncertain'),
                                  ('replacement', 'replacement_uid')]:
            with self.subTest(failure=failure), self.fixture() as (session, suite, state):
                def inject():
                    if failure == 'replacement':
                        state['object'] = copy.deepcopy(state['original'])
                        state['object']['metadata']['uid'] = 'foreign-crd'
                    else:
                        state['read_error'] = (b'Error from server (Forbidden)' if failure == 'forbidden'
                                               else b'controlled connection unavailable')
                state['before_restore'] = inject
                with self.assertRaises(KubeFailure) as caught:
                    global_fault(suite, 'C04.agent-crd')
                self.assertEqual(caught.exception.code, expected)
                self.assertEqual(state['creates'], 0)
                self.assertFalse(session.cleanup_case(suite)['success'])
                self.assertEqual(state['creates'], 0)

    def test_restore_intent_persistence_failure_prevents_create_and_cleanup_success(self):
        """故障恢复的创建 intent 未持久化，不能发送 create 或让 cleanup 放行。"""
        with self.fixture() as (session, suite, state):
            replace = os.replace
            state['at_restore'] = False
            state['before_restore'] = lambda: state.update(at_restore=True)

            def fail_ledger_replace(source, destination):
                if state['at_restore'] and Path(destination) == session.ledger.path:
                    raise OSError('controlled ledger persistence failure')
                return replace(source, destination)

            with patch('session.os.replace', side_effect=fail_ledger_replace):
                with self.assertRaises(OSError):
                    global_fault(suite, 'C04.agent-crd')
                self.assertEqual(state['creates'], 0)
                self.assertFalse(session.cleanup_case(suite)['success'])
                self.assertEqual(state['creates'], 0)
            self.assertFalse(any(e['action'] == 'create' for e in Ledger.open(session.ledger.path).entries.values()))

    def test_lost_create_response_keeps_new_uid_unconfirmed_and_cleanup_failed(self):
        """API 创建生效但响应丢失时保留原 gap，不能从同名读取补认新 UID。"""
        with self.fixture() as (session, suite, state):
            state['before_restore'] = lambda: state.update(lose_response=True)
            with self.assertRaises(KubeFailure) as caught:
                global_fault(suite, 'C04.agent-crd')
            self.assertEqual(caught.exception.code, 'TransportUncertain')
            self.assertFalse(session.cleanup_case(suite)['success'])
            self.assertEqual(state['creates'], 1)
            entry = next(e for e in Ledger.open(session.ledger.path).entries.values() if e['action'] == 'create')
            self.assertEqual(entry['stage'], 'identity_unconfirmed')
            self.assertNotIn('uid', entry)

    def test_restore_requires_one_original_fault_for_case_target_identity_and_uid(self):
        """缺原故障、身份不符或原故障不唯一时，caller 必须在创建前拒绝。"""
        for mismatch in ['missing', 'duplicate', 'case', 'target', 'identity', 'uid']:
            with self.subTest(mismatch=mismatch), self.fixture() as (session, suite, state):
                def inject():
                    ledger = session.ledger
                    fault = next(e for e in ledger.entries.values() if e['action'] == 'fault-delete')
                    if mismatch == 'missing':
                        del ledger.entries[fault['id']]
                    elif mismatch == 'duplicate':
                        token = ledger.intent('data', 'fault-delete', fault['identity'],
                            origin='fault-existing', uid='original-crd', resource_version='1',
                            baseline=ledger.baseline(fault))
                        ledger.mark(token, 'absent')
                    elif mismatch == 'identity':
                        fault['identity'] = {**fault['identity'], 'name': 'another-crd'}
                    else:
                        fault[mismatch] = 'foreign-value'
                    ledger.flush()
                state['before_restore'] = inject
                with self.assertRaises(KubeFailure) as caught:
                    global_fault(suite, 'C04.agent-crd')
                self.assertEqual(caught.exception.code, 'fixture_crd_restore_fault_unconfirmed')
                self.assertEqual(state['creates'], 0)
                self.assertFalse(session.cleanup_case(suite)['success'])
                self.assertEqual(state['creates'], 0)


if __name__ == '__main__':
    unittest.main()
