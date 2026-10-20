"""status fixture的真实传输/账本Host接缝；只替换外部API，不计正式K通过。"""
import copy
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from agent_projection import reason_runtime_projection
from kube_transport import KubeTransport, apply_json_patch, identity
from ledger import Ledger


class StatusAPI:
    """隔离的API系统边界；原UID precondition与RV CAS独立于被测传输实现。"""

    def __init__(self):
        self.namespace = {'apiVersion': 'v1', 'kind': 'Namespace',
                          'metadata': {'name': 'owned', 'uid': 'namespace-original', 'resourceVersion': '1'}}
        self.agents = {name: {'apiVersion': 'monitoring.coreos.com/v1alpha1', 'kind': 'PrometheusAgent',
            'metadata': {'name': name, 'namespace': 'owned', 'uid': 'original-' + name,
                         'resourceVersion': '2', 'generation': 1}, 'spec': {}, 'status': {}}
            for name in ['managed', 'outside']}
        self.requests = []
        self.agent_reads = {'managed': 0, 'outside': 0}
        self.stale_reads = 0
        self.replace_at_read = None
        self.replace_before_put = False
        self.write_faults = []

    def execute(self, argv, *, input=None, **kwargs):
        if argv[:5] != ['HOST_ONLY', '--kubeconfig', 'host-only', '--context', 'host-only']:
            raise AssertionError('Host不能派发真实进程或连接目标API')
        args = argv[5:]
        if args[0] == 'get':
            if args[1] != 'namespaces':
                name = args[2]; self.agent_reads[name] += 1
                if (name, self.agent_reads[name]) == self.replace_at_read:
                    self.agents[name]['metadata']['uid'] = 'replacement'
                if name == 'managed' and self.agent_reads[name] % 2 == 0 and self.stale_reads:
                    self.agents[name]['metadata']['resourceVersion'] = str(int(self.agents[name]['metadata']['resourceVersion']) + 1)
                    self.stale_reads -= 1
            obj = self.namespace if args[1] == 'namespaces' else self.agents[args[2]]
            return subprocess.CompletedProcess(argv, 0, json.dumps(obj).encode(), b'')
        self.requests.append({'args': args, 'input': input, 'label': kwargs['label']})
        if args[0] == 'patch' and '--subresource=status' in args:
            # 与原通用422同类的合成拒绝；不冒称还原了缺失的原-p payload或服务器原因。
            return subprocess.CompletedProcess(argv, 1, b'', b'Error from server: the server rejected our request due to an error in our request')
        if args[:2] == ['replace', '--raw']:
            desired = json.loads(input); name = desired['metadata']['name']; actual = self.agents[name]
            if self.replace_before_put:
                actual['metadata']['uid'] = 'replacement'; self.replace_before_put = False
            if desired['metadata']['uid'] != actual['metadata']['uid']:
                return subprocess.CompletedProcess(argv, 1, b'', b'Error from server (Conflict): UID precondition failed')
            if desired['metadata']['resourceVersion'] != actual['metadata']['resourceVersion']:
                return subprocess.CompletedProcess(argv, 1, b'', b'Error from server (Conflict): resourceVersion changed')
            fault = self.write_faults.pop(0) if self.write_faults else None
            if fault == 'Conflict':
                actual['metadata']['resourceVersion'] = str(int(actual['metadata']['resourceVersion']) + 1)
            if fault in ['Conflict', 'Forbidden', 'Invalid', 'generic422', 'spoofed-stale']:
                message = (b'Error from acceptance boundary (stale_fixture_revision)' if fault == 'spoofed-stale' else
                           b'Error from server: the server rejected our request due to an error in our request' if fault == 'generic422' else
                           ('Error from server (' + fault + '): Host synthetic rejection').encode())
                return subprocess.CompletedProcess(argv, 1, b'', message)
            if fault == 'network':
                raise TimeoutError('Host synthetic unknown transport')
            updated = copy.deepcopy(actual); updated['status'] = desired['status']
        elif args[0] == 'patch':
            name = args[2]; updated = apply_json_patch(self.agents[name], json.loads(args[args.index('-p') + 1]))
        else:
            raise AssertionError('未声明的Host外部边界')
        updated['metadata']['resourceVersion'] = str(int(updated['metadata']['resourceVersion']) + 1)
        self.agents[name] = updated
        if args[0] == 'replace':
            if fault == 'response-lost':
                raise TimeoutError('Host synthetic response lost after commit')
            if fault == 'wrong-response-uid':
                updated = copy.deepcopy(updated); updated['metadata']['uid'] = 'replacement'
            if fault == 'missing-response-rv':
                updated = copy.deepcopy(updated); updated['metadata'].pop('resourceVersion')
            if fault == 'missing-response':
                return subprocess.CompletedProcess(argv, 0, b'', b'')
        return subprocess.CompletedProcess(argv, 0, json.dumps(updated).encode(), b'')


class StatusFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.api = StatusAPI()
        self.ledger = Ledger(Path(self.temp.name) / 'ledger.json', run_id='host-status',
                             candidate='host-fixture', targets={'control': 'cluster-original'})
        for obj in [self.api.namespace, *self.api.agents.values()]:
            token = self.ledger.intent('control', 'create', identity(obj)); self.ledger.confirm(token, obj)
        self.kube = KubeTransport(ledger=self.ledger, execute=self.api.execute, kubectl='HOST_ONLY',
            targets={'control': {'kubeconfig': 'host-only', 'context': 'host-only'}})
        transport = self.kube
        class Suite:
            targetns = 'owned'; cluster = {'id': 'host-cluster'}
            def __init__(self):
                self.calls = []
            def cr(self, name):
                return json.loads(self.k('get', 'prometheusagents', name, '-n', self.targetns, '-o', 'json').stdout)
            def k(self, *args, data=None, check=True):
                return transport.command('control', list(args), data=data, check=check)
            def call(self, method, path, body=None):
                self.calls.append((method, path))
                # 此Host仅验证status写入接缝；平台公开模型为合成响应，不验证产品脱敏实现。
                return {'error': '[敏感内容已隐藏]'} if method == 'POST' else {
                    'instances': [{'name': 'outside', 'readOnly': True, 'runtime': {'error': '[敏感内容已隐藏]'}}]}
        self.suite = Suite()

    def project(self):
        return reason_runtime_projection(self.suite, {'name': 'managed', 'id': 'host-instance'}, 'outside',
            ['SYNTHETIC_HOST_MARKER'], lambda marker: 'https://reader:' + marker + '@example.invalid/%zz', timeout=0)

    def test_status_replace_preserves_native_put_and_original_uid_rv(self):
        """REG21-STATUS-FIXTURE-001：真实caller写两原UID，status PUT body保留本轮RV。"""
        self.project()
        self.assertEqual(len(self.api.requests), 2)
        for request, name in zip(self.api.requests, ['managed', 'outside']):
            self.assertEqual(request['args'], ['replace', '--raw',
                '/apis/monitoring.coreos.com/v1alpha1/namespaces/owned/prometheusagents/' + name + '/status', '-f', '-'])
            body = json.loads(request['input'])
            self.assertEqual((body['metadata']['uid'], body['metadata']['resourceVersion']), ('original-' + name, '2'))
            self.assertEqual(self.api.agents[name]['metadata']['uid'], 'original-' + name)
        mutations = [v for v in self.ledger.entries.values() if v['action'] == 'mutate']
        self.assertEqual([v['stage'] for v in mutations], ['confirmed', 'confirmed'])
        self.assertTrue(self.suite.projection_reason_branch['branchObserved'])

    def test_local_stale_revision_reloads_same_uid_before_put(self):
        """caller读取后RV前进时，第一轮不写，只按本地明确stale code重新GET。"""
        self.api.stale_reads = 1
        self.project()
        self.assertEqual(len(self.api.requests), 2)
        first = json.loads(self.api.requests[0]['input'])
        self.assertEqual((first['metadata']['uid'], first['metadata']['resourceVersion']), ('original-managed', '3'))
        self.assertEqual(self.api.agent_reads['managed'], 5)
        self.assertEqual([v['stage'] for v in self.ledger.entries.values() if v['action'] == 'mutate'],
                         ['confirmed', 'confirmed'])

    def test_standard_conflict_reloads_current_rv_and_keeps_original_uid(self):
        self.api.write_faults = ['Conflict']
        self.project()
        self.assertEqual(len(self.api.requests), 3)
        self.assertEqual([json.loads(v['input'])['metadata']['resourceVersion'] for v in self.api.requests], ['2', '3', '2'])
        mutations = [v for v in self.ledger.entries.values() if v['action'] == 'mutate']
        self.assertEqual([v['stage'] for v in mutations], ['identity_unconfirmed', 'confirmed', 'confirmed'])
        self.assertEqual([v['uid'] for v in mutations], ['original-managed', 'original-managed', 'original-outside'])

    def test_conflict_and_local_stale_each_exhaust_at_eight_without_refresh(self):
        for fault in ['Conflict', 'local-stale']:
            with self.subTest(fault=fault):
                if fault == 'local-stale':
                    self.api.requests.clear(); self.api.agent_reads = {'managed': 0, 'outside': 0}; self.api.stale_reads = 8
                else:
                    self.api.write_faults = ['Conflict'] * 8
                with self.assertRaisesRegex(AssertionError, 'reason来源fixture写入失败'):
                    self.project()
                self.assertEqual(len(self.api.requests), 8 if fault == 'Conflict' else 0)
                self.assertEqual(self.api.agent_reads, {'managed': 16, 'outside': 0})
                self.assertEqual(self.suite.calls, [])

    def test_replacement_before_guard_or_after_get_never_binds_new_uid(self):
        for when in ['before-guard', 'after-get']:
            with self.subTest(when=when):
                # 各子情景使用新的真实ledger和API边界，不能沿用已失败intent冒充新run。
                fixture = StatusFixture(); fixture.setUp()
                try:
                    if when == 'before-guard': fixture.api.replace_at_read = ('managed', 2)
                    else: fixture.api.replace_before_put = True
                    with self.assertRaisesRegex(AssertionError, 'reason来源fixture写入失败'): fixture.project()
                    self.assertEqual(len(fixture.api.requests), 0 if when == 'before-guard' else 1)
                    self.assertTrue(all(v.get('uid') != 'replacement' for v in fixture.ledger.entries.values()))
                    self.assertEqual(fixture.suite.calls, [])
                finally: fixture.doCleanups()

    def test_forbidden_invalid_unknown_and_response_loss_never_retry_or_confirm(self):
        for fault in ['Forbidden', 'Invalid', 'generic422', 'spoofed-stale', 'network',
                      'response-lost', 'wrong-response-uid', 'missing-response-rv', 'missing-response']:
            with self.subTest(fault=fault):
                fixture = StatusFixture(); fixture.setUp()
                try:
                    fixture.api.write_faults = [fault]
                    with self.assertRaises((AssertionError, TimeoutError, ValueError)): fixture.project()
                    self.assertEqual(len(fixture.api.requests), 1)
                    mutations = [v for v in fixture.ledger.entries.values() if v['action'] == 'mutate']
                    self.assertEqual([(v['stage'], v['uid']) for v in mutations], [('identity_unconfirmed', 'original-managed')])
                    self.assertEqual(fixture.suite.calls, [])
                finally: fixture.doCleanups()

    def test_intent_persistence_failure_dispatches_no_write(self):
        original_replace = __import__('os').replace
        def fail_ledger(source, destination):
            if Path(destination) == self.ledger.path: raise OSError('Host synthetic fsync/rename failure')
            return original_replace(source, destination)
        with patch('session.os.replace', fail_ledger), self.assertRaises(OSError): self.project()
        self.assertEqual(self.api.requests, [])
        self.assertEqual(len(Ledger.open(self.ledger.path).entries), 3)
        self.assertEqual(self.suite.calls, [])

    def test_namespace_and_unconfirmed_identity_guards_still_precede_put(self):
        self.api.namespace['metadata']['uid'] = 'replacement-namespace'
        with self.assertRaisesRegex(AssertionError, 'reason来源fixture写入失败'): self.project()
        self.assertEqual(self.api.requests, [])
        self.api.namespace['metadata']['uid'] = 'namespace-original'
        unknown = copy.deepcopy(self.api.agents['managed']); unknown['metadata']['name'] = 'unowned'
        self.api.agents['unowned'] = unknown; self.api.agent_reads['unowned'] = 0
        path = '/apis/monitoring.coreos.com/v1alpha1/namespaces/owned/prometheusagents/unowned/status'
        result = self.kube.command('control', ['replace', '--raw', path, '-f', '-'], data=unknown, check=False)
        self.assertNotEqual(result.returncode, 0); self.assertIn(b'(mutation_identity_unconfirmed)', result.stderr)
        self.assertEqual(self.api.requests, [])

    def test_plain_replace_patch_and_other_subresource_keep_uid_rv_jsonpatch(self):
        for verb, subresource in [('replace', None), ('replace', 'scale'), ('patch', None), ('patch', 'status')]:
            with self.subTest(verb=verb, subresource=subresource):
                fixture = StatusFixture(); fixture.setUp()
                try:
                    desired = copy.deepcopy(fixture.api.agents['managed']); desired['spec']['paused'] = True
                    args = ['replace', '-f', '-'] if verb == 'replace' else [
                        'patch', 'prometheusagents', 'managed', '-n', 'owned', '--type=merge', '-p', '{"spec":{"paused":true}}']
                    if subresource: args.append('--subresource=' + subresource)
                    fixture.kube.command('control', args, data=desired if verb == 'replace' else None, check=False)
                    self.assertEqual(len(fixture.api.requests), 1)
                    request = fixture.api.requests[0]; self.assertEqual(request['args'][0], 'patch')
                    self.assertEqual(request['label'], 'kube-conditional-patch'); self.assertIsNone(request['input'])
                    payload = json.loads(request['args'][request['args'].index('-p') + 1])
                    self.assertEqual(payload[:2], [{'op': 'test', 'path': '/metadata/uid', 'value': 'original-managed'},
                                                  {'op': 'test', 'path': '/metadata/resourceVersion', 'value': '2'}])
                    self.assertIn({'op': 'add', 'path': '/spec/paused', 'value': True}, payload)
                finally: fixture.doCleanups()

    def test_invalid_raw_status_path_fails_before_any_dispatch(self):
        desired = copy.deepcopy(self.api.agents['managed'])
        result = self.kube.command('control', ['replace', '--raw',
            '/apis/monitoring.coreos.com/v1alpha1/namespaces/foreign/prometheusagents/managed/status', '-f', '-'],
            data=desired, check=False)
        self.assertNotEqual(result.returncode, 0); self.assertIn(b'(undeclared_replace_raw_path)', result.stderr)
        self.assertEqual(self.api.requests, [])


if __name__ == '__main__':
    unittest.main()
