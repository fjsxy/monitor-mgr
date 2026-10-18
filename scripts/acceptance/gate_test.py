"""REG21 validator 协议测试；仅合成 validator-test，不代表产品K通过。"""
import hashlib
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

import registry

HERE = pathlib.Path(__file__).resolve().parent
TREE = '2' * 40
COMMIT_BYTES = (f'tree {TREE}\nauthor Validator <fixture@example.invalid> 0 +0000\n'
                'committer Validator <fixture@example.invalid> 0 +0000\n\nvalidator-test\n').encode()
CANDIDATE = hashlib.sha1(b'commit ' + str(len(COMMIT_BYTES)).encode() + b'\0' + COMMIT_BYTES).hexdigest()


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True))


def ref(path, root, kind='safe-json', visibility='public'):
    return dict(path=str(path.relative_to(root)), sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                kind=kind, visibility=visibility)


def fixture(root, profile='CI_FAST'):
    """完整精确键的安全合成基线；每份结果明确不能作为产品门禁。"""
    entries = registry.entries(profile)
    targets = [] if profile == 'CI_FAST' else [dict(name='control', clusterUID='control-fixture', nodeCount=3)]
    if profile == 'LOCAL_FULL':
        targets.append(dict(name='data', clusterUID='data-fixture', nodeCount=1))
    arch = 'arm64' if profile == 'LOCAL_FULL' else 'amd64'
    host_os = 'darwin' if profile == 'LOCAL_FULL' else 'linux'
    environment = dict(runtimeOS=host_os, runtimeArch=arch, targets=targets)
    common = dict(candidateCommit=CANDIDATE, testedCommit=CANDIDATE, runnerCommit=CANDIDATE,
                  testedTree=TREE, dirty=False, manifestHash=registry.MANIFEST_HASH, profile=profile)
    events = root / 'evidence/go.jsonl'
    events.parent.mkdir(parents=True)
    commit = root / 'evidence/source.commit'
    commit.write_bytes(COMMIT_BYTES)
    selectors = sorted({s for e in entries if e['layer'] == 'Q' for s in e['testSelectors']})
    events.write_text(''.join(json.dumps(dict(Action='pass', Test=s)) + '\n' for s in selectors))
    config = root / 'evidence/config.json'
    dump(config, dict(os='linux', architecture=arch,
                      config=dict(Labels={'org.opencontainers.image.revision': CANDIDATE})))
    config_digest = 'sha256:' + ref(config, root)['sha256']
    manifest = root / 'evidence/manifest.json'
    dump(manifest, dict(schemaVersion=2, config=dict(digest=config_digest)))
    manifest_digest = 'sha256:' + ref(manifest, root)['sha256']
    report = root / 'evidence/browser.json'
    dump(report, dict(suites=[dict(specs=[dict(title=selector + ' 合成协议fixture',
        tests=[dict(status='expected', results=[dict(status='passed')])])
        for e in entries if e['layer'] == 'B' for selector in e['testSelectors']])]))
    index = dict(schemaVersion=1, runId='validator-test-unique-attempt', **common, supplemental=False,
                 executionBoundary='validator-test', environment=environment,
                 sourceCommitRef=ref(commit, root, 'source-commit'),
                 expectedKeys=[dict(variantId=e['variantId'], layer=e['layer']) for e in entries],
                 results=[], startedAt='2026-10-19T00:00:00Z', finishedAt='2026-10-19T00:01:00Z',
                 state='completed', cleanup=dict(success=True))
    for e in entries:
        original = root / ('original/' + e['variantId'] + '.json')
        dump(original, dict(variantId=e['variantId'], layer=e['layer'], **common,
                            source=dict(commit=CANDIDATE, tree=TREE, dirty=False),
                            outcome='passed', cleanup=dict(success=True), executionBoundary='validator-test'))
        receipt = dict(**common, variantId=e['variantId'], layer=e['layer'],
                       contractFingerprint=e['contractFingerprint'], outcome='passed',
                       cleanup=dict(success=True), executionBoundary='validator-test',
                       originalReceipt=ref(original, root, 'receipt'), evidenceRefs=[ref(events, root, 'go-events')],
                       testSelectors=e['testSelectors'], runtimeOS=host_os if e['layer'] == 'Q' else 'linux',
                       runtimeArch=arch)
        if e['layer'] == 'Q':
            receipt.update(processLocation='host-Q', command=['go', 'test', './internal/platform', '-json'],
                           commandExitCode=0, noSkipped=True, goEventRef=ref(events, root, 'go-events'))
        else:
            receipt.update(productImage=dict(revision=CANDIDATE, os='linux', architecture=arch,
                manifestDigest=manifest_digest, configDigest=config_digest,
                manifestRef=ref(manifest, root, 'image-manifest'), configRef=ref(config, root, 'image-config')),
                platform=dict(podUID='synthetic-pod', nodeName='synthetic-node', imageID='containerd://' + config_digest,
                              clusterUID='control-fixture', serviceName='monitor-mgr'))
            if e['layer'] == 'B':
                receipt['browserEventRef'] = ref(report, root, 'browser-report')
        path = root / ('receipts/' + e['variantId'] + '.json')
        dump(path, receipt)
        index['results'].append(dict(variantId=e['variantId'], layer=e['layer'], receipt=ref(path, root, 'receipt')))
    path = root / 'index.json'
    dump(path, index)
    return path


class ValidatorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = pathlib.Path(self.temp.name)
        self.index = fixture(self.root)
        self.profile = 'CI_FAST'

    def invoke(self, *extra):
        return subprocess.run([sys.executable, str(HERE / 'gate.py'), 'validate', '--index', str(self.index),
            '--candidate', CANDIDATE, '--profile', self.profile, '--validator-test', *extra],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def change_index(self, change):
        value = json.loads(self.index.read_text())
        change(value)
        dump(self.index, value)

    def change_receipt(self, change):
        value = json.loads(self.index.read_text())
        item = value['results'][0]
        path = self.root / item['receipt']['path']
        record = json.loads(path.read_text())
        change(record)
        dump(path, record)
        item['receipt'] = ref(path, self.root, 'receipt')
        dump(self.index, value)

    def rejected(self, code):
        result = self.invoke()
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertEqual(json.loads(result.stdout)['code'], code, result.stdout + result.stderr)

    def test_valid_fixture_has_explicit_non_product_scope(self):
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)['executionBoundary'], 'validator-test')
        self.assertIs(json.loads(result.stdout)['productGate'], False)

    def test_full_and_smoke_fixtures_retain_oci_and_browser_evidence(self):
        for profile, count in [('LOCAL_FULL', 207), ('CI_SMOKE', 17)]:
            with self.subTest(profile=profile):
                self.root = pathlib.Path(self.temp.name) / profile
                self.profile = profile
                self.index = fixture(self.root, profile)
                result = self.invoke()
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(json.loads(result.stdout)['count'], count)

    def test_missing_key(self):
        self.change_index(lambda value: value['results'].pop())
        self.rejected('missing-key')

    def test_duplicate_key(self):
        self.change_index(lambda value: value['results'].append(value['results'][0]))
        self.rejected('duplicate-key')

    def test_unknown_key(self):
        self.change_index(lambda value: value['results'][0].update(variantId='UNKNOWN.primary'))
        self.rejected('unknown-key')

    def test_wrong_source(self):
        dimensions = [('wrapper', 'testedCommit'), ('wrapper', 'runnerCommit'), ('wrapper', 'dirty'),
                      ('wrapper', 'sourceBridge'), ('original', 'testedCommit'), ('original', 'runnerCommit'),
                      ('original', 'source.commit'), ('original', 'source.tree'),
                      ('original', 'dirty'), ('original', 'source.dirty'), ('original', 'sourceBridge')]
        for number, (where, field) in enumerate(dimensions):
            with self.subTest(where=where, field=field):
                self.root = pathlib.Path(self.temp.name) / str(number)
                self.index = fixture(self.root)
                value = json.loads(self.index.read_text())
                item = value['results'][0]
                path = self.root / item['receipt']['path']
                record = json.loads(path.read_text())
                target_path = path if where == 'wrapper' else self.root / record['originalReceipt']['path']
                target = record if where == 'wrapper' else json.loads(target_path.read_text())
                bad = True if field.endswith('dirty') else dict(verified=True) if field == 'sourceBridge' else '3' * 40
                if '.' in field:
                    parent, child = field.split('.')
                    target[parent][child] = bad
                else:
                    target[field] = bad
                dump(target_path, target)
                if where == 'original':
                    record['originalReceipt'] = ref(target_path, self.root, 'receipt')
                    dump(path, record)
                item['receipt'] = ref(path, self.root, 'receipt')
                dump(self.index, value)
                self.rejected('wrong-source')
        self.root = pathlib.Path(self.temp.name) / 'tree-mismatch'
        self.index = fixture(self.root)
        value = json.loads(self.index.read_text())
        value['testedTree'] = '4' * 40
        for item in value['results']:
            path = self.root / item['receipt']['path']
            record = json.loads(path.read_text())
            original_path = self.root / record['originalReceipt']['path']
            original = json.loads(original_path.read_text())
            original['testedTree'] = original['source']['tree'] = value['testedTree']
            dump(original_path, original)
            record.update(testedTree=value['testedTree'], originalReceipt=ref(original_path, self.root, 'receipt'))
            dump(path, record)
            item['receipt'] = ref(path, self.root, 'receipt')
        dump(self.index, value)
        self.rejected('wrong-source-tree')

    def test_wrong_profile(self):
        self.change_receipt(lambda record: record.update(profile='LOCAL_FULL', runtimeOS='darwin', runtimeArch='arm64'))
        self.rejected('wrong-profile')
        self.root = pathlib.Path(self.temp.name) / 'scope'
        self.index = fixture(self.root)
        self._reject_missing_or_unknown_scope()
        self.root = pathlib.Path(self.temp.name) / 'relabel'
        self.index = fixture(self.root)
        self._reject_fixture_relabelled()

    def test_changed_artifact(self):
        (self.root / 'evidence/go.jsonl').write_text('changed bytes')
        self.rejected('changed-artifact')

    def test_cleanup_failure(self):
        self.change_receipt(lambda record: record.update(cleanup=dict(success=False)))
        self.rejected('cleanup-failure')

    def test_contract_mismatch(self):
        self.change_receipt(lambda record: record.update(contractFingerprint='0' * 64))
        self.rejected('contract-mismatch')

    def test_missing_artifact(self):
        (self.root / 'evidence/go.jsonl').unlink()
        self.rejected('missing-or-symlink-artifact')

    def test_path_escape(self):
        outside = self.root.parent / (self.root.name + '-outside.json')
        outside.write_text('{}')
        self.addCleanup(outside.unlink)
        self.change_receipt(lambda record: record['evidenceRefs'].append(dict(
            path='../' + outside.name, sha256=hashlib.sha256(outside.read_bytes()).hexdigest(),
            visibility='public', kind='safe-json')))
        self.rejected('path-escape')

    def test_skipped(self):
        path = self.root / 'evidence/go.jsonl'
        value = json.loads(self.index.read_text())
        first = json.loads((self.root / value['results'][0]['receipt']['path']).read_text())
        path.write_text(path.read_text() + json.dumps(dict(Action='skip', Test=first['testSelectors'][0])) + '\n')
        for item in value['results']:
            receipt_path = self.root / item['receipt']['path']
            record = json.loads(receipt_path.read_text())
            record['goEventRef'] = ref(path, self.root, 'go-events')
            record['evidenceRefs'] = [ref(path, self.root, 'go-events')]
            dump(receipt_path, record)
            item['receipt'] = ref(receipt_path, self.root, 'receipt')
        dump(self.index, value)
        self.rejected('skipped-or-missing-pass')

    def test_wrong_image_source(self):
        self.root = self.root / 'full'
        self.index = fixture(self.root, 'LOCAL_FULL')
        self.profile = 'LOCAL_FULL'
        config_path = self.root / 'evidence/config.json'
        config = json.loads(config_path.read_text())
        config['config']['Labels']['org.opencontainers.image.revision'] = '3' * 40
        dump(config_path, config)
        config_digest = 'sha256:' + ref(config_path, self.root)['sha256']
        manifest_path = self.root / 'evidence/manifest.json'
        dump(manifest_path, dict(schemaVersion=2, config=dict(digest=config_digest)))
        manifest_digest = 'sha256:' + ref(manifest_path, self.root)['sha256']
        value = json.loads(self.index.read_text())
        for item in value['results']:
            path = self.root / item['receipt']['path']
            record = json.loads(path.read_text())
            if item['layer'] in ['K', 'B']:
                record['productImage'].update(configDigest=config_digest, manifestDigest=manifest_digest,
                    configRef=ref(config_path, self.root, 'image-config'),
                    manifestRef=ref(manifest_path, self.root, 'image-manifest'))
                record['platform']['imageID'] = 'containerd://' + config_digest
                dump(path, record)
                item['receipt'] = ref(path, self.root, 'receipt')
        dump(self.index, value)
        self.rejected('wrong-image-source')

    def test_original_evidence_cannot_be_omitted(self):
        value = json.loads(self.index.read_text())
        item = value['results'][0]
        path = self.root / item['receipt']['path']
        record = json.loads(path.read_text())
        original_path = self.root / record['originalReceipt']['path']
        original = json.loads(original_path.read_text())
        original['evidenceRefs'] = [dict(path='missing-private.json', sha256='a' * 64)]
        dump(original_path, original)
        record['originalReceipt'] = ref(original_path, self.root, 'receipt')
        dump(path, record)
        item['receipt'] = ref(path, self.root, 'receipt')
        dump(self.index, value)
        self.rejected('missing-original-reference')

    def _reject_missing_or_unknown_scope(self):
        for boundary in [None, 'protocol-fixture', 'unknown']:
            with self.subTest(boundary=boundary):
                self.change_index(lambda value: value.update(executionBoundary=boundary))
                result = subprocess.run([sys.executable, str(HERE / 'gate.py'), 'validate',
                    '--index', str(self.index), '--candidate', CANDIDATE, '--profile', 'CI_FAST'],
                    capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0, result.stdout)

    def _reject_fixture_relabelled(self):
        self.change_index(lambda value: value.update(executionBoundary='acceptance'))
        result = subprocess.run([sys.executable, str(HERE / 'gate.py'), 'validate',
            '--index', str(self.index), '--candidate', CANDIDATE, '--profile', 'CI_FAST'],
            capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0, result.stdout)


class ProtocolRunnerTests(unittest.TestCase):
    def test_cli_records_actual_unittest_result(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = pathlib.Path(temporary) / 'events.json'
            selector = 'registry_test.RegistryTests.test_fixed_profiles_are_independently_expanded'
            result = subprocess.run([sys.executable, str(HERE / 'gate.py'), 'protocol-tests',
                '--events', str(path), '--selectors', selector], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            events = json.loads(path.read_text())
            self.assertEqual(events['tests'], [dict(selector=selector, outcome='passed')])
            self.assertEqual(events['commandExitCode'], 0)
            self.assertEqual(events['executionBoundary'], 'validator-test')
            self.assertEqual(events['testsRun'], 1)


if __name__ == '__main__':
    unittest.main()
