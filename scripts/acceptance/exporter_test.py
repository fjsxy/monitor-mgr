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

from gate_test import CANDIDATE, HERE, dump, fixture, ref


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
        with sqlite3.connect(path) as database:
            database.execute('CREATE TABLE fixture(value TEXT)')
            database.execute("INSERT INTO fixture VALUES ('synthetic')")
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
