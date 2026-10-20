"""REG21真实出口测试；敏感输入仅使用本例合成canary和临时文件。"""
import io
import json
import pathlib
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

from gate_test import CANDIDATE, HERE, dump, fixture, ref
import exporter
import gate
import registry
from protocol_cases_test import validator_protocol_fixture


class ProtocolDiagnosticExporterTests(unittest.TestCase):
    def fixture(self, root, mutate=None):
        """八个明确validator协议原件保留case相对引用，索引仍为完整104键。"""
        run = root / 'run'
        results = []
        for entry in registry.entries('LOCAL_FULL', True):
            item = {k: entry[k] for k in ['variantId', 'layer']}
            item['outcome'] = 'not_run'
            if entry['executionBoundary'] == 'kubernetes-protocol':
                case = run / 'cases' / ('K.' + entry['variantId'])
                case.mkdir(parents=True)
                proof = validator_protocol_fixture(case, entry['variantId'])
                if mutate and entry['variantId'] == 'REG21-LEDGER-009.uid-replaced':
                    mutate(case, proof)
                dump(case / 'protocol-proof.json', proof)
                original = {k: proof[k] for k in ['runId', 'testedCommit', 'testedTree', 'runnerCommit',
                    'candidateCommit', 'dirty', 'source', 'manifestHash']}
                original.update({k: entry[k] for k in ['variantId', 'layer', 'purpose', 'steps', 'expected',
                                                      'contractFingerprint', 'testSelectors']})
                original.update(profile='LOCAL_FULL', outcome='passed', executionBoundary='validator-test',
                    cleanup={'success': True}, kubernetesProtocolRef=ref(case / 'protocol-proof.json', case),
                    evidenceRefs=[ref(case / 'protocol-proof.json', case)])
                dump(case / 'receipt.json', original)
                record = {**original, 'originalEvidenceBase': str(case.relative_to(run)),
                    'originalReceipt': ref(case / 'receipt.json', run, 'receipt'),
                    'kubernetesProtocolRef': ref(case / 'protocol-proof.json', run),
                    'evidenceRefs': [ref(case / 'protocol-proof.json', run)]}
                path = run / 'receipts' / ('K.' + entry['variantId'] + '.json')
                dump(path, record)
                item.update(outcome='passed', receipt=ref(path, run, 'receipt'))
            results.append(item)
        index = {'schemaVersion': 1, 'runId': 'validator-protocol', 'profile': 'LOCAL_FULL',
                 'candidateCommit': '1' * 40, 'testedCommit': '1' * 40, 'runnerCommit': '1' * 40,
                 'testedTree': '2' * 40, 'dirty': False, 'manifestHash': registry.MANIFEST_HASH,
                 'source': {'commit': '1' * 40, 'tree': '2' * 40, 'dirty': False},
                 'supplemental': True, 'state': 'failed', 'productGate': False,
                 'executionBoundary': 'validator-test', 'results': results,
                 'expectedKeys': [{k: e[k] for k in ['variantId', 'layer']} for e in results],
                 'environment': {'runtimeOS': 'darwin', 'runtimeArch': 'arm64', 'targets': [
                     {'name': 'control', 'clusterUID': 'validator-control', 'nodeCount': 3},
                     {'name': 'data', 'clusterUID': 'validator-data', 'nodeCount': 1}]}}
        path = run / 'index.json'
        dump(path, index)
        return path

    def invoke(self, path, destination, canaries=()):
        return exporter.export(path, destination, '1' * 40, 'LOCAL_FULL', supplemental=True,
                               validator_test=True, diagnostic=True, canaries=canaries)

    def test_typed_eight_closure_keeps_original_bytes_and_incomplete_104(self):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            path = self.fixture(root)
            original = path.read_bytes()
            destination = root / 'bundle'
            value = self.invoke(path, destination)
            self.assertIs(value['productGate'], False)
            self.assertEqual((destination / 'index.json').read_bytes(), original)
            summary = json.loads((destination / 'diagnostic-summary.json').read_bytes())
            self.assertEqual(len(summary['expectedKeys']), 104)
            self.assertEqual(len(summary['protocolClosures']), 8)
            self.assertTrue(all(v['complete'] for v in summary['protocolClosures']))
            self.assertEqual(summary['excludedRefs'], [])
            for entry in registry.entries('LOCAL_FULL', True):
                if entry['executionBoundary'] != 'kubernetes-protocol':
                    continue
                relative = pathlib.Path('cases') / ('K.' + entry['variantId'])
                proof = json.loads((destination / relative / 'protocol-proof.json').read_bytes())
                gate.kubernetes_protocol(proof, entry, gate.RelativeEvidence(gate.Evidence(destination), str(relative)),
                    {'validator-control'}, 'arm64', '1' * 40, '2' * 40, validator_test=True)
                self.assertEqual((destination / relative / 'checkpoint.json').read_bytes(),
                                 (path.parent / relative / 'checkpoint.json').read_bytes())

    def test_nested_missing_private_canary_path_and_credentials_remain_explicit_gaps(self):
        canary = 'synthetic-nested-export-canary-509b'
        for mode, code in [('missing', 'missing-or-symlink-artifact'),
                           ('private', 'private-or-unapproved-artifact'), ('canary', 'sensitive-canary'),
                           ('escape', 'path-escape'), ('symlink', 'missing-or-symlink-artifact'),
                           ('credentials', 'private-or-unapproved-artifact')]:
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                root = pathlib.Path(directory)
                def mutate(case, proof):
                    target = case / 'checkpoint.json'
                    if mode == 'missing':
                        target.unlink()
                    elif mode == 'escape':
                        proof['checkpointRef']['path'] = '../outside.json'
                    elif mode == 'symlink':
                        original = target.read_bytes()
                        target.unlink()
                        outside = root / 'outside.json'
                        outside.write_bytes(original)
                        target.symlink_to(outside)
                    else:
                        if mode == 'private':
                            target = case / '.private' / 'guard.json'
                        payload = {'canary': canary} if mode == 'canary' else (
                            {'apiVersion': 'v1', 'kind': 'Config', 'users': [{'user': {'token': 'synthetic'}}]}
                            if mode == 'credentials' else {'private': 'must-not-be-opened'})
                        dump(target, payload)
                        proof['checkpointRef'] = ref(target, case)
                path = self.fixture(root, mutate)
                destination = root / 'bundle'
                real_open = gate.os.open
                def guarded_open(path, *args, **kwargs):
                    if str(path) in ['.private', 'raw', 'private']:
                        raise AssertionError('private-directory-opened')
                    return real_open(path, *args, **kwargs)
                with mock.patch.object(gate.os, 'open', guarded_open):
                    self.invoke(path, destination, [canary])
                summary = json.loads((destination / 'diagnostic-summary.json').read_bytes())
                self.assertIs(summary['productGate'], False)
                self.assertIn(code, {v['code'] for v in summary['excludedRefs']})
                self.assertFalse(next(v for v in summary['protocolClosures']
                                      if v['variantId'] == 'REG21-LEDGER-009.uid-replaced')['complete'])
                self.assertFalse((destination / 'cases/K.REG21-LEDGER-009.uid-replaced/.private').exists())
                self.assertTrue(all(canary.encode() not in p.read_bytes()
                                    for p in destination.rglob('*') if p.is_file()))


class ExporterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = pathlib.Path(self.temp.name)
        self.run = self.root / 'run'
        self.index = fixture(self.run)
        self.destination = self.root / 'bundle'

    def invoke(self, *extra):
        return subprocess.run([sys.executable, str(HERE / 'exporter.py'), 'export',
            '--index', str(self.index), '--destination', str(self.destination),
            '--candidate', CANDIDATE, '--profile', 'CI_FAST', '--validator-test', *extra],
            capture_output=True, text=True)

    def add_evidence(self, path, kind='safe-json', visibility='public'):
        value = json.loads(self.index.read_text())
        item = value['results'][0]
        receipt_path = self.run / item['receipt']['path']
        record = json.loads(receipt_path.read_text())
        record['evidenceRefs'].append(ref(path, self.run, kind, visibility))
        dump(receipt_path, record)
        item['receipt'] = ref(receipt_path, self.run, 'receipt')
        dump(self.index, value)

    def safe_positive(self):
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        verified = subprocess.run([sys.executable, str(self.destination / 'scripts/acceptance/gate.py'),
            'validate', '--index', str(self.destination / 'index.json'), '--candidate', CANDIDATE,
            '--profile', 'CI_FAST', '--validator-test'], cwd='/', capture_output=True, text=True)
        self.assertEqual(verified.returncode, 0, verified.stdout + verified.stderr)
        self.assertEqual((self.destination / 'index.json').read_bytes(), self.index.read_bytes())
        return result

    def test_completed_diagnostic_validates_with_same_pre_read_private_guard(self):
        """完成态必须走完整首record校验，不能由failed索引早停掩盖先读private旁路。"""
        for segment in ['.private', 'private', 'raw']:
            with self.subTest(segment=segment):
                self.run = self.root / ('completed-' + segment)
                self.index = fixture(self.run)
                target = self.run / segment / 'guard.json'
                dump(target, {'synthetic': 'must-not-be-opened'})
                self.add_evidence(target)
                destination = self.root / ('bundle-' + segment)
                actual_open = gate.os.open
                def guarded_open(path, *args, **kwargs):
                    if str(path) == segment:
                        raise AssertionError('private-reference-opened-before-export-guard')
                    return actual_open(path, *args, **kwargs)
                with mock.patch.object(gate.os, 'open', guarded_open):
                    result = exporter.export(self.index, destination, CANDIDATE, 'CI_FAST',
                                              validator_test=True, diagnostic=True)
                self.assertIs(result['gatePassed'], False)
                summary = json.loads((destination / 'diagnostic-summary.json').read_bytes())
                self.assertEqual(summary['gateCode'], 'private-or-unapproved-artifact')
                self.assertIn('private-or-unapproved-artifact', {v['code'] for v in summary['excludedRefs']})
                self.assertFalse((destination / segment).exists())

    def test_safe_bundle_revalidates_from_other_cwd(self):
        self.safe_positive()

    def test_private_source(self):
        path = self.run / 'private-source/controller.go'
        path.parent.mkdir()
        path.write_text('package synthetic\n')
        self.add_evidence(path)
        result = self.invoke()
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertFalse(self.destination.exists())
        # 每项保留独立安全正控，不能因全拒绝而误通过。
        self.index = fixture(self.root / 'positive')
        self.safe_positive()

    def test_kubeconfig(self):
        path = self.run / 'evidence/approved.json'
        dump(path, dict(apiVersion='v1', kind='Config', clusters=[dict(name='synthetic', cluster={})],
                        users=[dict(name='synthetic', user={'token': 'fixture-only'})], contexts=[]))
        self.add_evidence(path)
        result = self.invoke()
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertFalse(self.destination.exists())
        self.index = fixture(self.root / 'positive')
        self.safe_positive()

    def test_raw_db(self):
        path = self.run / 'evidence/approved.json'
        database = sqlite3.connect(path)
        try:
            with database:
                database.execute('CREATE TABLE fixture(value TEXT)')
                database.execute("INSERT INTO fixture VALUES ('synthetic')")
        finally:
            # 事务上下文只提交/回滚；连接须显式关闭，避免后续Host测试收到延迟泄漏警告。
            database.close()
        self.add_evidence(path)
        result = self.invoke()
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertFalse(self.destination.exists())
        self.index = fixture(self.root / 'positive')
        self.safe_positive()

    def test_image_tar(self):
        path = self.run / 'evidence/approved.json'
        with tarfile.open(path, 'w') as archive:
            content = b'synthetic image layer'
            item = tarfile.TarInfo('layer.tar')
            item.size = len(content)
            archive.addfile(item, io.BytesIO(content))
        self.add_evidence(path)
        result = self.invoke()
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertFalse(self.destination.exists())
        self.index = fixture(self.root / 'positive')
        self.safe_positive()

    def test_canary(self):
        canary = 'fixture-only-export-canary-5d15f9'
        path = self.run / 'evidence/approved.json'
        dump(path, dict(syntheticAuthentication=canary))
        self.add_evidence(path)
        values = self.root / 'private-canaries.json'
        dump(values, [canary])
        values.chmod(0o600)
        result = self.invoke('--canary-file', str(values))
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertNotIn(canary, result.stdout + result.stderr)
        self.assertFalse(self.destination.exists())
        self.index = fixture(self.root / 'positive')
        self.safe_positive()

    def test_console(self):
        canary = 'fixture-only-console-canary-c72309'
        private = self.root / 'protected'
        command = [sys.executable, '-c',
                   f'import sys; print({canary!r}); print({canary!r},file=sys.stderr); sys.exit(17)']
        result = subprocess.run([sys.executable, str(HERE / 'exporter.py'), 'capture',
            '--private-directory', str(private), '--case-id', 'REG21-EXPORT-012.console', '--', *command],
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 17, result.stdout + result.stderr)
        self.assertNotIn(canary, result.stdout + result.stderr)
        summary = json.loads(result.stdout)
        self.assertEqual(summary['commandExitCode'], 17)
        self.assertEqual(summary['outcome'], 'failed')
        original = private / summary['privateRef']['path']
        self.assertEqual(original.stat().st_mode & 0o777, 0o600)
        self.assertEqual(private.stat().st_mode & 0o777, 0o700)
        self.assertEqual(original.read_text().count(canary), 2)
        self.assertEqual(ref(original, private)['sha256'], summary['privateRef']['sha256'])
        self._diagnostic_failure_stays_failed()

    def _diagnostic_failure_stays_failed(self):
        value = json.loads(self.index.read_text())
        value.update(state='failed', cleanup=dict(success=False))
        item = value['results'][0]
        receipt_path = self.run / item['receipt']['path']
        record = json.loads(receipt_path.read_text())
        record.update(outcome='failed', cleanup=dict(success=False), commandExitCode=17)
        record['evidenceRefs'].append(dict(path='missing.json', sha256='f' * 64,
                                           visibility='public', kind='safe-json'))
        private = self.run / '.private/raw.log'
        private.parent.mkdir()
        canary = 'diagnostic-private-only-canary-a11e19'
        private.write_text(canary)
        record['evidenceRefs'].append(ref(private, self.run, 'raw-output', 'private'))
        dump(receipt_path, record)
        item['receipt'] = ref(receipt_path, self.run, 'receipt')
        dump(self.index, value)
        strict = self.invoke()
        self.assertNotEqual(strict.returncode, 0, strict.stdout + strict.stderr)
        self.assertFalse(self.destination.exists())
        result = self.invoke('--diagnostic')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        summary = json.loads(result.stdout)
        self.assertIs(summary['gatePassed'], False)
        self.assertIs(summary['diagnosticOnly'], True)
        self.assertEqual((self.destination / 'index.json').read_bytes(), self.index.read_bytes())
        metadata = json.loads((self.destination / 'diagnostic-summary.json').read_text())
        self.assertEqual(len(metadata['results']), 58)
        self.assertEqual(metadata['results'][0]['outcome'], 'failed')
        self.assertIn('missing-or-symlink-artifact', {r['code'] for r in metadata['excludedRefs']})
        self.assertIn('private-or-unapproved-artifact', {r['code'] for r in metadata['excludedRefs']})
        for path in self.destination.rglob('*'):
            if path.is_file():
                self.assertNotIn(canary.encode(), path.read_bytes())
        verified = subprocess.run([sys.executable, str(self.destination / 'scripts/acceptance/gate.py'),
            'validate', '--index', str(self.destination / 'index.json'), '--candidate', CANDIDATE,
            '--profile', 'CI_FAST', '--validator-test'], cwd='/', capture_output=True, text=True)
        self.assertNotEqual(verified.returncode, 0, verified.stdout + verified.stderr)


if __name__ == '__main__':
    unittest.main()
