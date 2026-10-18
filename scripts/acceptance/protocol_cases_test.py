"""协议K的Host参数/真实child测试；没有Kubernetes UID或K通过记录。"""
import json
import fcntl
import hashlib
import pathlib
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace

from session import BatchSession
from transport import process_identity
import protocol_cases
import registry
import gate

HERE = pathlib.Path(__file__).resolve().parent


def validator_protocol_fixture(root, variant):
    """仅构造标记 validator-test 的门禁输入；不是 API 结果或产品 K 收据。"""
    mode = protocol_cases.MODES[variant]
    request = {'runId': 'validator-protocol', 'profile': 'LOCAL_FULL', 'candidateCommit': '1' * 40,
               'runnerCommit': '1' * 40, 'testedTree': '2' * 40, 'dirty': False, 'variantId': variant,
               'targetUID': 'validator-control', 'source': {'commit': '1' * 40, 'tree': '2' * 40, 'dirty': False}}
    common = protocol_cases.common(request)
    def artifact(name, value):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))
        return protocol_cases.reference(path, root)
    def cleanup(uid='validator-original'):
        return {'success': True, 'resources': [{'id': 'create', 'uid': uid, 'notFound': True}], 'failures': [],
                'mode': 'original-uid-normal-gc', 'force': False, 'genericFinalizerRemoval': False, 'entryCount': 1,
                'auxiliaryFailures': []}
    namespace = {'apiVersion': 'v1', 'kind': 'Namespace', 'metadata': {'name': 'validator-victim',
                 'uid': 'validator-original', 'resourceVersion': '23'}}
    entry = {'id': 'create', 'action': 'create', 'case': variant, 'target': 'control',
             'clusterUID': request['targetUID'], 'stage': 'confirmed', 'uid': namespace['metadata']['uid'],
             'resourceVersion': '23', 'identity': {'apiVersion': 'v1', 'kind': 'Namespace', 'namespace': '', 'name': 'validator-victim'}}
    auxiliary = {'id': 'aux', 'label': 'protocol-owned-auxiliary', 'stage': 'started', 'pid': 999999,
                 'birth': 'validator-only'}
    ledger = {'schemaVersion': 1, 'runId': request['runId'], 'candidateCommit': request['candidateCommit'],
              'targets': {'control': request['targetUID'], 'data': 'validator-data'}, 'entries': {'create': entry},
              'processes': {'aux': auxiliary}}
    expected = [{k: e[k] for k in ['variantId', 'layer']} for e in registry.entries('LOCAL_FULL')]
    results = [{**k, 'outcome': 'not_run'} for k in expected]
    if mode != 'kill':
        results[0]['outcome'] = 'environment_failed' if mode == 'setup' else 'failed'
    signal_names = {'term': ['SIGTERM'], 'int': ['SIGINT'], 'repeat': ['SIGTERM', 'SIGINT']}.get(mode, [])
    index = {**common, 'expectedKeys': expected, 'results': results, 'state': 'interrupted' if signal_names else 'failed',
             'cleanup': cleanup(), 'signals': [{'name': n} for n in signal_names], 'finishedAt': 'validator-only',
             'failure': {'type': 'KeyboardInterrupt' if mode == 'int' else 'RuntimeError' if mode == 'setup' else 'AssertionError'}}
    if mode in ['replaced', 'read-denied', 'cleanup-denied']:
        index['cleanup'] = {'success': False, 'entryCount': 1, 'auxiliaryFailures': [],
                            'failures': [{'errorCode': 'Forbidden'}] if mode == 'cleanup-denied' else []}
    if mode == 'kill':
        index.update(state='running', cleanup={'success': False, 'state': 'not_started'})
        index.pop('finishedAt')
    index_ref = artifact('child/index.json', index)
    proof = {**common, 'schemaVersion': 1, 'executionBoundary': 'validator-test', 'actualKubernetes': False,
             'mode': mode, 'namespaceUID': 'validator-original',
             'namespaceResponseRef': artifact('namespace-response.json', namespace),
             'checkpointRef': artifact('checkpoint.json', {**protocol_cases.binding(request), 'stage': 'namespace_checkpoint',
                 'namespaceUID': 'validator-original', 'resourceVersion': '23', 'actualKubernetes': False}),
             'creationLedgerRef': artifact('creation-ledger.json', ledger), 'childIndexRef': index_ref,
             'childProcessRef': artifact('child-process.json', {**protocol_cases.binding(request), 'pid': 999998,
                 'birth': 'validator-only', 'returncode': -9 if mode == 'kill' else 1,
                 'signalsSent': ['SIGKILL'] if mode == 'kill' else signal_names}),
             'environmentRef': artifact('environment.json', {**common, 'clusterUID': request['targetUID'],
                 'runtimeOS': 'linux', 'runtimeArch': 'arm64', 'nodes': [
                     {'name': f'node-{n}', 'uid': f'uid-{n}', 'os': 'linux', 'arch': 'arm64'} for n in range(3)]}),
             'clusterSystemResponseRef': artifact('system.json', {'apiVersion': 'v1', 'kind': 'Namespace',
                 'metadata': {'name': 'kube-system', 'uid': request['targetUID']}}),
             'nodeResponseRef': artifact('nodes.json', {'kind': 'NodeList', 'items': [
                 {'metadata': {'name': f'node-{n}', 'uid': f'uid-{n}'}, 'status': {'nodeInfo':
                     {'operatingSystem': 'linux', 'architecture': 'arm64'}}} for n in range(3)]}), 'recoveryRefs': []}
    final = json.loads(json.dumps(ledger))
    final['processes']['aux']['stage'] = 'recovered-exit' if mode == 'kill' else 'exited'
    proof['childLedgerRef'] = artifact('child/ledger.json', final)
    if mode == 'repeat':
        proof['cleanupBarrierRef'] = artifact('cleanup-waiting.json', {**protocol_cases.binding(request), 'stage': 'cleanup_waiting'})
    if mode == 'kill':
        copied = root / 'killed-index.json'
        copied.write_bytes((root / 'child/index.json').read_bytes())
        proof['killedIndexRef'] = protocol_cases.reference(copied, root)
        proof['killObservationRef'] = artifact('kill-observation.json', {**protocol_cases.binding(request),
            'returncode': -9, 'state': 'interrupted', 'finallyExecuted': False, 'cleanup': {'success': False},
            'originalIndexSHA256': index_ref['sha256'], 'pid': 999998, 'birth': 'validator-only'})
    if mode in ['cleanup-denied', 'read-denied']:
        proof['restrictedIdentityRef'] = artifact('restricted-identity.json', {**common, 'actual': True,
            'serviceAccountUID': 'validator-sa', 'username': 'system:serviceaccount:validator-owner:reader', 'mode': mode})
        events = artifact('restricted-api-events.json', [{'reason': 'Forbidden', 'returncode': 1,
            'label': 'kube-delete' if mode == 'cleanup-denied' else 'kube-read', 'restrictedIdentity': True}])
        proof['restrictedCleanupEventsRef' if mode == 'cleanup-denied' else 'restrictedReadEventsRef'] = events
    def recovery(attempt, success, cleaned, **extra):
        environment = json.loads((root / proof['environmentRef']['path']).read_bytes())
        system = json.loads((root / proof['clusterSystemResponseRef']['path']).read_bytes())
        nodes = json.loads((root / proof['nodeResponseRef']['path']).read_bytes())
        recorded = json.loads(json.dumps(final))
        recorded['entries']['create']['stage'] = 'absent' if cleaned.get('success') else 'confirmed'
        if attempt == 'owner-cleanup':
            recorded['entries']['create'].update(uid='validator-replacement', case=variant + ':replacement-owner')
        return artifact(attempt + '/report.json', {**common, 'attempt': attempt, 'success': success,
            'cleanup': cleaned, 'originalIndexSHA256': index_ref['sha256'], 'originalIndexUnchanged': True,
            'replaysBusiness': False, 'auxiliaryFailures': [],
            'environmentRef': artifact(attempt + '/environment.json', environment),
            'clusterSystemResponseRef': artifact(attempt + '/system.json', system),
            'nodeResponseRef': artifact(attempt + '/nodes.json', nodes),
            'ledgerRef': artifact(attempt + '/ledger-snapshot.json', recorded), **extra})
    if mode in ['kill', 'cleanup-denied', 'read-denied']:
        if mode == 'read-denied':
            proof['recoveryRefs'].append(recovery('initial-recover', False, {'success': False,
                'failures': [{'errorCode': 'Forbidden'}], 'resources': []}))
        proof['recoveryRefs'].append(recovery('owner-recover', True, cleanup(),
            **({'fixtureOwnerCleanup': cleanup('validator-owner')} if mode in ['cleanup-denied', 'read-denied'] else {})))
    if mode == 'replaced':
        proof['recoveryRefs'] = [recovery('owner-replace', True, {'success': False},
            originalUID='validator-original', replacementUID='validator-replacement'),
            recovery('initial-recover', False, {'success': False, 'failures': [{'errorCode': 'replacement_uid'}],
                'resources': []}, observedReplacementUID='validator-replacement'),
            recovery('owner-cleanup', True, cleanup('validator-replacement'))]
        replacement = json.loads(json.dumps(namespace))
        replacement['metadata']['uid'] = 'validator-replacement'
        created = json.loads((root / proof['recoveryRefs'][0]['path']).read_bytes())
        created['namespaceResponseRef'] = artifact('owner-replace/namespace-response.json', replacement)
        owner_ledger = json.loads(json.dumps(ledger))
        owner_ledger['entries']['create'].update(case=variant + ':replacement-owner', uid='validator-replacement')
        created['ledgerRef'] = artifact('owner-replace/ledger-snapshot.json', owner_ledger)
        artifact(proof['recoveryRefs'][0]['path'], created)
        proof['recoveryRefs'][0] = protocol_cases.reference(root / proof['recoveryRefs'][0]['path'], root)
        failed = json.loads((root / proof['recoveryRefs'][1]['path']).read_bytes())
        failed['observedNamespaceRef'] = artifact('initial-recover/observed.json', replacement)
        proof['recoveryRefs'][1] = artifact(proof['recoveryRefs'][1]['path'], failed)
    if mode in ['cleanup-denied', 'read-denied']:
        owner = json.loads(json.dumps(ledger))
        owner['entries'] = {'sa': {'id': 'sa', 'action': 'create', 'case': variant + ':restricted-owner',
            'target': 'control', 'clusterUID': request['targetUID'], 'uid': 'validator-sa', 'stage': 'confirmed',
            'identity': {'apiVersion': 'v1', 'kind': 'ServiceAccount', 'namespace': 'validator-owner', 'name': 'reader'}}}
        proof['restrictedOwnerLedgerRef'] = artifact('fixture-owner-ledger.json', owner)
        proof['restrictedAccessRef'] = artifact('restricted-access.json', {**common, 'serviceAccountUID': 'validator-sa',
            'systemReadAllowed': True, 'victimReadAllowed': mode == 'cleanup-denied', 'victimDeleteAllowed': False})
    return proof


class KubernetesProtocolGateTests(unittest.TestCase):
    def test_validator_fixtures_cover_all_eight_without_product_pass(self):
        for variant in protocol_cases.MODES:
            with self.subTest(variant=variant), tempfile.TemporaryDirectory() as directory:
                root = pathlib.Path(directory)
                proof = validator_protocol_fixture(root, variant)
                entry = registry.entries('LOCAL_FULL', True, [variant])[0]
                result = gate.kubernetes_protocol(proof, entry, gate.Evidence(root), {'validator-control'}, 'arm64',
                                                  '1' * 40, '2' * 40, validator_test=True)
                self.assertIs(result['productGate'], False)

    def test_missing_or_wrong_protocol_inputs_fail_closed(self):
        variant = next(iter(protocol_cases.MODES))
        mutations = [('missing-uid', lambda p: p.pop('namespaceResponseRef')),
                     ('missing-checkpoint', lambda p: p.pop('checkpointRef')),
                     ('wrong-boundary', lambda p: p.update(executionBoundary='platform-pod')),
                     ('wrong-target', lambda p: p.update(targetUID='different')),
                     ('wrong-source', lambda p: p.update(testedCommit='3' * 40)),
                     ('unknown-mode', lambda p: p.update(mode='invented'))]
        for name, mutate in mutations:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                root = pathlib.Path(directory)
                proof = validator_protocol_fixture(root, variant)
                mutate(proof)
                with self.assertRaises(gate.GateError):
                    gate.kubernetes_protocol(proof, registry.entries('LOCAL_FULL', True, [variant])[0],
                        gate.Evidence(root), {'validator-control'}, 'arm64', '1' * 40, '2' * 40, validator_test=True)

    def test_host_fixture_cannot_be_used_as_actual_k(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            variant = next(iter(protocol_cases.MODES))
            proof = validator_protocol_fixture(root, variant)
            with self.assertRaises(gate.GateError):
                gate.kubernetes_protocol(proof, registry.entries('LOCAL_FULL', True, [variant])[0], gate.Evidence(root),
                    {'validator-control'}, 'arm64', '1' * 40, '2' * 40)

    def test_deep_original_inputs_cannot_be_replaced_by_matching_outer_wrapper(self):
        mutations = [
            ('namespaceResponseRef', lambda p: p['metadata'].update(uid='changed')),
            ('checkpointRef', lambda p: p.update(resourceVersion='changed')),
            ('creationLedgerRef', lambda p: p['entries']['create'].update(uid='changed')),
            ('childIndexRef', lambda p: p.update(testedCommit='3' * 40)),
            ('childIndexRef', lambda p: p['expectedKeys'].pop()),
            ('childIndexRef', lambda p: p['results'][0].update(outcome='passed')),
            ('childIndexRef', lambda p: p['cleanup'].update(force=True)),
            ('nodeResponseRef', lambda p: p['items'][0]['status']['nodeInfo'].update(architecture='amd64')),
            ('childProcessRef', lambda p: p.update(returncode=0)),
        ]
        for field, mutate in mutations:
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                root = pathlib.Path(directory)
                variant = next(iter(protocol_cases.MODES))
                proof = validator_protocol_fixture(root, variant)
                path = root / proof[field]['path']
                value = json.loads(path.read_bytes())
                mutate(value)
                path.write_text(json.dumps(value))
                proof[field] = protocol_cases.reference(path, root)
                with self.assertRaises(gate.GateError):
                    gate.kubernetes_protocol(proof, registry.entries('LOCAL_FULL', True, [variant])[0], gate.Evidence(root),
                        {'validator-control'}, 'arm64', '1' * 40, '2' * 40, validator_test=True)

    def test_protocol_ref_base_stays_inside_canonical_run_root(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            case = root / 'cases/protocol'
            case.mkdir(parents=True)
            variant = next(iter(protocol_cases.MODES))
            proof = validator_protocol_fixture(case, variant)
            reader = gate.Evidence(root)
            gate.kubernetes_protocol(proof, registry.entries('LOCAL_FULL', True, [variant])[0],
                gate.RelativeEvidence(reader, 'cases/protocol'), {'validator-control'}, 'arm64',
                '1' * 40, '2' * 40, validator_test=True)
            self.assertIn('cases/protocol/child/index.json', reader.files)
            with self.assertRaises(gate.GateError):
                gate.RelativeEvidence(reader, 'cases/protocol').read({'path': '../../outside.json',
                    'sha256': '1' * 64, 'kind': 'safe-json', 'visibility': 'public'})

    def test_recovery_actual_target_restricted_sa_and_replacement_originals_are_required(self):
        cases = [('REG21-RESULT-006.cleanup-failure', None, 'restrictedOwnerLedgerRef',
                  lambda p: p['entries']['sa'].update(uid='different')),
                 ('REG21-RESULT-006.cleanup-failure', None, 'restrictedAccessRef',
                  lambda p: p.update(victimDeleteAllowed=True)),
                 ('REG21-RECOVER-008.kill-then-recover', 0, 'clusterSystemResponseRef',
                  lambda p: p['metadata'].update(uid='different')),
                 ('REG21-RECOVER-008.kill-then-recover', 0, 'ledgerRef',
                  lambda p: p['entries']['create'].update(uid='different')),
                 ('REG21-LEDGER-009.uid-replaced', 0, 'namespaceResponseRef',
                  lambda p: p['metadata'].update(uid='different'))]
        for variant, report_number, field, mutate in cases:
            with self.subTest(variant=variant, field=field), tempfile.TemporaryDirectory() as directory:
                root = pathlib.Path(directory)
                proof = validator_protocol_fixture(root, variant)
                container = proof if report_number is None else json.loads(
                    (root / proof['recoveryRefs'][report_number]['path']).read_bytes())
                path = root / container[field]['path']
                value = json.loads(path.read_bytes())
                mutate(value)
                path.write_text(json.dumps(value))
                container[field] = protocol_cases.reference(path, root)
                if report_number is not None:
                    report_path = root / proof['recoveryRefs'][report_number]['path']
                    report_path.write_text(json.dumps(container))
                    proof['recoveryRefs'][report_number] = protocol_cases.reference(report_path, root)
                with self.assertRaises(gate.GateError):
                    gate.kubernetes_protocol(proof, registry.entries('LOCAL_FULL', True, [variant])[0], gate.Evidence(root),
                        {'validator-control'}, 'arm64', '1' * 40, '2' * 40, validator_test=True)


class ProtocolCaseHostCLI(unittest.TestCase):
    def scenario(self, mode):
        """只调用明确host-protocol入口，不允许隐式降级真实K。"""
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = pathlib.Path(temporary.name)
        self.root = root
        result = subprocess.run([sys.executable, str(HERE / 'protocol_cases.py'), 'host-scenario',
            '--mode', mode, '--output', str(root / 'case'), '--api-lane', str(root / 'lane')],
            capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        value = json.loads(result.stdout)
        self.assertIs(value['actualKubernetes'], False)
        self.assertIs(value['productGate'], False)
        self.assertEqual(value['executionBoundary'], 'validator-test')
        return json.loads((root / 'case' / 'summary.json').read_text())

    def test_term_preserves_complete_index_and_natural_cleanup(self):
        value = self.scenario('term')
        self.assertEqual(value['childIndex']['state'], 'interrupted')
        self.assertEqual([r['outcome'] for r in value['childIndex']['results']], ['failed', 'not_run'])
        self.assertTrue(value['childIndex']['cleanup']['success'])
        self.assertEqual(value['childIndex']['cleanup']['entryCount'], 1)
        self.assertEqual(value['childIndex']['signals'][0]['name'], 'SIGTERM')

    def test_int_retains_keyboard_interrupt(self):
        value = self.scenario('int')
        self.assertEqual(value['childIndex']['failure']['type'], 'KeyboardInterrupt')
        self.assertEqual(value['childIndex']['signals'][0]['name'], 'SIGINT')
        self.assertTrue(value['childIndex']['cleanup']['success'])

    def test_repeat_signals_do_not_reenter_cleanup(self):
        value = self.scenario('repeat')
        self.assertEqual(value['childIndex']['cleanup']['entryCount'], 1)
        self.assertEqual([s['name'] for s in value['childIndex']['signals']], ['SIGTERM', 'SIGINT'])
        self.assertEqual(value['childIndex']['failure']['type'], 'AssertionError')
        self.assertTrue(value['childIndex']['cleanup']['success'])

    def test_setup_failure_keeps_environment_failed_and_pending(self):
        value = self.scenario('setup')
        self.assertEqual([r['outcome'] for r in value['childIndex']['results']], ['environment_failed', 'not_run'])
        self.assertEqual(value['childIndex']['state'], 'failed')
        self.assertTrue(value['childIndex']['cleanup']['success'])

    def test_kill_has_no_finally_claim_and_cleans_recorded_auxiliary(self):
        value = self.scenario('kill')
        root = self.root / 'case'
        self.assertEqual(value['childProcess']['returncode'], -9)
        original = (root / 'child/index.json').read_bytes()
        self.assertEqual((root / 'killed-index.json').read_bytes(), original)
        self.assertIs(json.loads(original)['cleanup']['success'], False)
        self.assertNotIn('finishedAt', json.loads(original))
        observation = json.loads((root / 'kill-observation.json').read_text())
        self.assertEqual(observation['originalIndexSHA256'], hashlib.sha256(original).hexdigest())
        self.assertIs(observation['finallyExecuted'], False)
        ledger = json.loads((root / 'child/ledger.json').read_text())
        owned = [p for p in ledger['processes'].values() if p.get('label') == 'protocol-owned-auxiliary']
        self.assertEqual(len(owned), 1)
        self.assertEqual(owned[0]['stage'], 'recovered-exit')
        self.assertIsNone(process_identity(owned[0]['pid']))

    def test_inherited_lease_does_not_deadlock_or_unlock_parent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            parent = BatchSession(root / 'parent', expected=[], run_id='host-owner', profile='validator-test',
                                  candidate='host', lane=str(root / 'lane'), protocol_fixture=True)
            parent.start()
            try:
                fd = parent.lane.fileno()
                result = subprocess.run([sys.executable, str(HERE / 'protocol_cases.py'), 'host-scenario',
                    '--mode', 'term', '--output', str(root / 'case'), '--api-lane', str(root / 'lane'),
                    '--inherited-lane-fd', str(fd)], pass_fds=(fd,), capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                index = json.loads((root / 'case/child/index.json').read_text())
                self.assertEqual(index['laneOwnership'], 'inherited-batch-lease')
                with open(root / 'lane', 'a') as other:
                    with self.assertRaises(BlockingIOError):
                        fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
            finally:
                parent.close()
            with open(root / 'lane', 'a') as other:
                fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def test_restricted_policy_has_no_delete_and_read_deny_keeps_only_system_identity(self):
        request = {'namespace': 'reg21-victim', 'fixtureNamespace': 'reg21-owner', 'rbacName': 'reg21-reader'}
        for mode, names in [('cleanup-denied', ['kube-system', 'reg21-victim']),
                            ('read-denied', ['kube-system'])]:
            objects = protocol_cases.restricted_objects(request, mode)
            role = next(o for o in objects if o['kind'] == 'ClusterRole')
            ns_rule = next(r for r in role['rules'] if r['resources'] == ['namespaces'])
            self.assertEqual(ns_rule['verbs'], ['get'])
            self.assertEqual(ns_rule['resourceNames'], names)
            self.assertTrue(all(not set(r['verbs']) & {'create', 'update', 'patch', 'delete', '*'} for r in role['rules']))

    def test_changed_private_handoff_is_rejected_before_child_setup(self):
        self.scenario('term')
        path = self.root / 'case/.private/request.json'
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        value = json.loads(path.read_text())
        value['runId'] = 'other'
        path.write_text(json.dumps(value))
        result = subprocess.run([sys.executable, str(HERE / 'protocol_cases.py'), 'host-protocol-child',
            '--request', str(path), '--request-sha256', digest], capture_output=True, text=True)
        self.assertEqual(json.loads(result.stdout)['code'], 'changed-handoff')
        self.assertNotEqual(result.returncode, 0)

    def test_real_protocol_context_rejects_cross_target_before_any_child_or_api(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            variant = next(iter(protocol_cases.MODES))
            entry = registry.entries('LOCAL_FULL', True, [variant])[0]
            context = {'runId': 'context-fixture', 'profile': 'LOCAL_FULL', 'candidateCommit': '1' * 40,
                'runnerCommit': '1' * 40, 'testedTree': '2' * 40, 'dirty': False,
                'source': {'commit': '1' * 40, 'tree': '2' * 40, 'dirty': False},
                'targets': {name: {'clusterUID': name + '-fixture', 'kubeconfig': '/not-read/' + name,
                    'context': name} for name in ['control', 'data']},
                'environment': {'runtimeOS': 'darwin', 'runtimeArch': 'arm64', 'targets': [
                    {'name': 'control', 'clusterUID': 'control-fixture', 'nodeCount': 3},
                    {'name': 'data', 'clusterUID': 'data-fixture', 'nodeCount': 1}]},
                'kubectl': ['/not-executed/kubectl'], 'apiLane': str(root / 'lane'), 'output': str(root / 'case')}
            request = protocol_cases.case_request(SimpleNamespace(request=context), entry)
            request['targetUID'] = 'data-fixture'
            path = root / 'request.json'
            digest = protocol_cases.private_request(path, request)
            result = subprocess.run([sys.executable, str(HERE / 'protocol_cases.py'), 'child',
                '--request', str(path), '--request-sha256', digest], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(json.loads(result.stdout)['code'], 'wrong-handoff-target')
            self.assertFalse((root / 'case').exists())

    def test_protocol_child_seeds_exact_207_not_run_before_lock_or_api(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            request = {'runId': 'seed-fixture', 'profile': 'LOCAL_FULL', 'candidateCommit': '1' * 40,
                       'source': {'commit': '1' * 40, 'tree': '2' * 40, 'dirty': False},
                       'apiLane': str(root / 'not-opened-lane'), 'environment': {}}
            session = protocol_cases.new_session(request, root / 'child', None, host=False)
            value = json.loads((root / 'child/index.json').read_bytes())
            self.assertEqual(len(value['expectedKeys']), 207)
            self.assertEqual(len(value['results']), 207)
            self.assertEqual({r['outcome'] for r in value['results']}, {'not_run'})
            self.assertEqual(value['state'], 'pending')
            self.assertIsNone(session.lane)
            self.assertFalse((root / 'not-opened-lane').exists())


if __name__ == '__main__':
    unittest.main()
