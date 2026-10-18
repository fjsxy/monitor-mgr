#!/usr/bin/env python3
"""环境准备公开协议自测；受控执行器不连接任何真实 Docker/Kubernetes。"""
import json
import hashlib
import io
import tarfile
import pathlib
import subprocess
import tempfile
import unittest

from prepare import PrepareError, prepare_environment, cleanup_environment, verify_environment


class PrepareProtocolTest(unittest.TestCase):
    def test_fast_has_no_cluster_and_never_invokes_environment_commands(self):
        with tempfile.TemporaryDirectory() as directory:
            request = {'profile': 'CI_FAST', 'runId': 'protocol-fast', 'candidate': 'a' * 40,
                       'privateRoot': directory, 'targets': {}}
            calls = []
            def execute(argv, **kwargs):
                calls.append(argv)
                raise AssertionError('FAST 不得准备集群')
            result = prepare_environment(request, execute=execute, ledger=lambda event: None)
            self.assertEqual(result['targets'], {})
            self.assertEqual(result['source'], 'a' * 40)
            self.assertEqual(calls, [])

    def test_existing_cluster_is_rejected_before_any_intent_or_write(self):
        with tempfile.TemporaryDirectory() as directory:
            request = {'profile': 'CI_SMOKE', 'runId': 'protocol-smoke', 'candidate': 'a' * 40,
                       'privateRoot': directory, 'platform': 'linux/amd64', 'targets': {'control': {
                           'name': 'mm21-protocol-control',
                           'kubeconfig': str(pathlib.Path(directory) / 'control.kubeconfig'),
                           'context': 'kind-mm21-protocol-control'}},
                       'monitorOpSource': directory}
            calls, events = [], []
            def execute(argv, **kwargs):
                calls.append(argv)
                return subprocess.CompletedProcess(argv, 0, b'mm21-protocol-control\n', b'')
            with self.assertRaisesRegex(PrepareError, 'PREPARE_CLUSTER_EXISTS'):
                prepare_environment(request, execute=execute, ledger=events.append)
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0][-2:], ['get', 'clusters'])
            self.assertEqual(events, [])

    def test_partial_create_failure_carries_observed_identity_to_session(self):
        with tempfile.TemporaryDirectory() as directory:
            request = {'profile': 'CI_SMOKE', 'runId': 'protocol-partial', 'candidate': 'a' * 40,
                       'privateRoot': directory, 'platform': 'linux/amd64', 'buildImages': False,
                       'targets': {'control': {'name': 'mm21-partial-control',
                           'kubeconfig': str(pathlib.Path(directory) / 'control.kubeconfig'),
                           'context': 'kind-mm21-partial-control'}}, 'monitorOpSource': directory}
            events = []
            def execute(argv, **kwargs):
                output, code = b'', 0
                if argv[-1] == 'clusters': output = b''
                elif argv[-1] == 'version': output = b'kind v0.33.0'
                elif argv[:2] == ['kubectl', 'version']: output = b'{"clientVersion":{"gitVersion":"v1.28.15"}}'
                elif argv[:1] == ['uname']: output = b'Linux x86_64'
                elif 'rev-parse' in argv: output = b'2b2a8b79a52070fb89a08f5afae2b7698901c999'
                elif argv[:3] == ['docker', 'image', 'inspect']:
                    source = '2b2a8b79a52070fb89a08f5afae2b7698901c999' if 'monitor-op' in argv[-1] else 'a' * 40
                    config = json.dumps({'os': 'linux', 'architecture': 'amd64', 'config': {'Labels': {'org.opencontainers.image.revision': source}}}).encode()
                    output = json.dumps([{'Id': 'sha256:' + hashlib.sha256(config).hexdigest(), 'Os': 'linux', 'Architecture': 'amd64'}]).encode()
                elif argv[:2] == ['docker', 'save']:
                    source = '2b2a8b79a52070fb89a08f5afae2b7698901c999' if 'monitor-op' in argv[-1] else 'a' * 40
                    config = json.dumps({'os': 'linux', 'architecture': 'amd64', 'config': {'Labels': {'org.opencontainers.image.revision': source}}}).encode()
                    config_name = hashlib.sha256(config).hexdigest() + '.json'
                    with tarfile.open(argv[3], 'w') as archive:
                        for name, data in [('manifest.json', json.dumps([{'Config': config_name, 'RepoTags': [argv[-1]], 'Layers': []}]).encode()), (config_name, config)]:
                            member = tarfile.TarInfo(name)
                            member.size = len(data)
                            archive.addfile(member, io.BytesIO(data))
                elif argv[:3] == ['kind', 'create', 'cluster']: code = 9
                elif argv[:2] == ['docker', 'ps']:
                    output = json.dumps({'ID': 'd' * 64, 'Names': 'mm21-partial-control-control-plane'}).encode()
                return subprocess.CompletedProcess(argv, code, output, b'')
            with self.assertRaises(PrepareError) as caught:
                prepare_environment(request, execute=execute, ledger=events.append)
            self.assertEqual(caught.exception.descriptor['targets']['control']['containers'][0]['id'], 'd' * 64)
            self.assertNotIn('clusterUID', caught.exception.descriptor['targets']['control'])
            self.assertEqual(events[-1]['event'], 'prepare_failed')

    def test_smoke_recovery_refuses_unplanned_data_before_commands(self):
        calls = []
        def execute(argv, **kwargs):
            calls.append(argv)
            raise AssertionError('非法 data 不得接触实际环境')
        with self.assertRaisesRegex(PrepareError, 'RECOVER_TARGETS_INVALID'):
            cleanup_environment({'profile': 'CI_SMOKE', 'runId': 'protocol-invalid', 'targets': {'data': {}}},
                                execute=execute, ledger=lambda event: None)
        self.assertEqual(calls, [])

    def recovery_descriptor(self, directory):
        return {'profile': 'CI_SMOKE', 'runId': 'protocol-recover', 'kubectl': 'kubectl', 'kind': 'kind',
                'targets': {'control': {'name': 'mm21-recover-control', 'context': 'kind-mm21-recover-control',
                    'kubeconfig': str(pathlib.Path(directory) / 'control.kubeconfig'), 'clusterUID': 'original-cluster',
                    'containers': [{'id': 'd' * 64, 'name': 'mm21-recover-control-control-plane'}]}}}

    def test_recovery_read_403_is_failure_and_never_delete(self):
        with tempfile.TemporaryDirectory() as directory:
            calls = []
            def execute(argv, **kwargs):
                calls.append(argv)
                if argv[:2] == ['docker', 'ps']:
                    return subprocess.CompletedProcess(argv, 0, json.dumps({'ID': 'd' * 64, 'Names': 'mm21-recover-control-control-plane'}).encode(), b'')
                return subprocess.CompletedProcess(argv, 1, b'', b'Forbidden SYNTHETIC-AUTH')
            result = cleanup_environment(self.recovery_descriptor(directory), execute=execute, ledger=lambda event: None)
            self.assertFalse(result['success'])
            self.assertEqual(result['targets']['control']['error'], 'RECOVER_CLUSTER_READ_FAILED')
            self.assertFalse(any('delete' in argv for argv in calls))
            self.assertNotIn('SYNTHETIC-AUTH', json.dumps(result))

    def test_recovery_uid_replacement_is_not_deleted(self):
        with tempfile.TemporaryDirectory() as directory:
            calls = []
            def execute(argv, **kwargs):
                calls.append(argv)
                output = json.dumps({'ID': 'd' * 64, 'Names': 'mm21-recover-control-control-plane'}).encode() if argv[0] == 'docker' else b'{"metadata":{"uid":"replacement-cluster"}}'
                return subprocess.CompletedProcess(argv, 0, output, b'')
            result = cleanup_environment(self.recovery_descriptor(directory), execute=execute, ledger=lambda event: None)
            self.assertFalse(result['success'])
            self.assertEqual(result['targets']['control']['error'], 'RECOVER_CLUSTER_UID_CHANGED')
            self.assertFalse(any('delete' in argv for argv in calls))

    def test_recovery_uses_explicit_config_and_confirms_container_absence(self):
        with tempfile.TemporaryDirectory() as directory:
            calls, events = [], []
            state = {'removed': False}
            def execute(argv, **kwargs):
                calls.append(argv)
                output = b''
                if argv[0] == 'docker' and not state['removed']:
                    output = json.dumps({'ID': 'd' * 64, 'Names': 'mm21-recover-control-control-plane'}).encode()
                elif argv[0] == 'kubectl': output = b'{"metadata":{"uid":"original-cluster"}}'
                elif argv[:3] == ['kind', 'delete', 'cluster']: state['removed'] = True
                return subprocess.CompletedProcess(argv, 0, output, b'')
            result = cleanup_environment(self.recovery_descriptor(directory), execute=execute, ledger=events.append)
            self.assertTrue(result['success'])
            deletion = next(argv for argv in calls if argv[0] == 'kind')
            self.assertEqual(deletion[-2:], ['--kubeconfig', str(pathlib.Path(directory) / 'control.kubeconfig')])
            self.assertEqual(events[0]['event'], 'environment_cleanup_intent')
            self.assertEqual(events[-1]['event'], 'environment_cleanup_result')

    def test_ledger_refusal_never_issues_prepare_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            request = {'profile': 'CI_SMOKE', 'runId': 'protocol-ledger', 'candidate': 'a' * 40,
                       'privateRoot': directory, 'platform': 'linux/amd64', 'targets': {'control': {
                           'name': 'mm21-ledger-control', 'kubeconfig': str(pathlib.Path(directory) / 'control.kubeconfig'),
                           'context': 'kind-mm21-ledger-control'}}, 'monitorOpSource': directory}
            calls = []
            def execute(argv, **kwargs):
                calls.append(argv)
                output = b'kind v0.33.0' if argv[-1] == 'version' else b'{"clientVersion":{"gitVersion":"v1.28.15"}}' if argv[0] == 'kubectl' else b'Linux x86_64' if argv[0] == 'uname' else b''
                return subprocess.CompletedProcess(argv, 0, output, b'')
            def refuse(event): raise OSError('synthetic ledger unavailable')
            with self.assertRaises(OSError):
                prepare_environment(request, execute=execute, ledger=refuse)
            self.assertFalse(any('create' in argv or 'build' in argv or 'apply' in argv for argv in calls))


if __name__ == '__main__':
    unittest.main()
