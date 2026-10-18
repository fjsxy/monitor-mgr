#!/usr/bin/env python3
"""验收结果门禁：独立重算精确键，逐字节核验原始结果与证据。"""
import argparse
import contextlib
import hashlib
import json
import os
import pathlib
import re
import stat
import subprocess
import tempfile
import unittest

import registry


class GateError(ValueError):
    """稳定错误码只描述边界，绝不把原始异常或敏感路径写到console。"""
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def require(condition, code):
    if not condition:
        raise GateError(code)


def json_bytes(data):
    """重复JSON字段不能覆盖先前失败/来源声明。"""
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, 'duplicate-json-field')
            result[key] = value
        return result
    try:
        return json.loads(data, object_pairs_hook=pairs)
    except (ValueError, UnicodeError) as error:
        if isinstance(error, GateError):
            raise
        raise GateError('invalid-json') from None


class Evidence:
    """相对run根打开证据；目录与末端均禁止symlink，不信声明hash。"""
    def __init__(self, root):
        self.root = pathlib.Path(root).resolve()
        self.files = {}
        self.bytes = {}

    def read(self, ref):
        require(isinstance(ref, dict), 'invalid-reference')
        path, digest = ref.get('path'), ref.get('sha256')
        require(isinstance(path, str) and bool(path) and '\\' not in path, 'path-escape')
        parts = path.split('/')
        require(all(part not in ['', '.', '..'] for part in parts) and not path.startswith('/'), 'path-escape')
        require(isinstance(digest, str) and re.fullmatch('[0-9a-f]{64}', digest), 'invalid-hash')
        require(ref.get('visibility') in ['public', 'private'] and isinstance(ref.get('kind'), str), 'invalid-reference')
        if path in self.files:
            require(self.files[path] == ref, 'conflicting-reference')
            return self.bytes[path]
        fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            for part in parts[:-1]:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd)
                fd = child
            child = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW, dir_fd=fd)
            with os.fdopen(child, 'rb') as stream:
                require(stat.S_ISREG(os.fstat(stream.fileno()).st_mode), 'invalid-artifact-type')
                data = stream.read()
        except OSError:
            raise GateError('missing-or-symlink-artifact') from None
        finally:
            os.close(fd)
        require(hashlib.sha256(data).hexdigest() == digest, 'changed-artifact')
        self.files[path] = dict(ref)
        self.bytes[path] = data
        return data

    def json(self, ref):
        return json_bytes(self.read(ref))

    def closure(self, value):
        """所有嵌套ref都必须闭合，private必需引用同样不能缺失。"""
        if isinstance(value, dict):
            if 'path' in value and 'sha256' in value:
                self.read(value)
            else:
                for child in value.values():
                    self.closure(child)
        elif isinstance(value, list):
            for child in value:
                self.closure(child)


def _source(value, candidate, tree):
    require(all(value.get(field) == candidate for field in
                ['candidateCommit', 'testedCommit', 'runnerCommit']), 'wrong-source')
    require(value.get('dirty') is False and value.get('testedTree') == tree, 'wrong-source')
    require(value.get('manifestHash') == registry.MANIFEST_HASH, 'contract-mismatch')
    _no_bridge(value)
    if 'source' in value:
        _actual_source(value['source'], candidate, tree)


def _no_bridge(value):
    require(all(value.get(field) in [None, False] for field in
                ['sourceBridge', 'source_bridge', 'bridge']), 'wrong-source')


def _actual_source(value, candidate, tree):
    require(isinstance(value, dict) and value.get('commit') == candidate and
            value.get('tree') == tree and value.get('dirty') is False, 'wrong-source')
    _no_bridge(value)


def _events(record, entry, reader):
    """读取实际测试事件；包级退出0和wrapper布尔声明均不能替代leaf。"""
    selectors = entry['testSelectors']
    if entry['layer'] == 'Q':
        require(record.get('processLocation') == 'host-Q', 'wrong-process-location')
        require(record.get('commandExitCode') == 0 and record.get('noSkipped') is True,
                'skipped-or-missing-pass')
        require(isinstance(record.get('command'), list) and record['command'], 'missing-command')
        if not entry['variantId'].startswith('REG21-'):
            events = []
            for line in reader.read(record['goEventRef']).splitlines():
                if line.strip():
                    events.append(json_bytes(line))
            roots = {selector.split('/')[0] for selector in selectors}
            relevant = [e for e in events if isinstance(e.get('Test'), str) and e['Test'].split('/')[0] in roots]
            passed = {e.get('Test') for e in relevant if e.get('Action') == 'pass'}
            require(set(selectors) <= passed and not any(e.get('Action') in ['skip', 'fail'] for e in relevant),
                    'skipped-or-missing-pass')
        else:
            events = reader.json(record.get('protocolEventRef'))
            require(events.get('schemaVersion') == 1 and events.get('executionBoundary') == 'validator-test' and
                    events.get('commandExitCode') == 0, 'skipped-or-missing-pass')
            _actual_source(events.get('source'), record['candidateCommit'], record['testedTree'])
            tests = events.get('tests', [])
            names = [e.get('selector') for e in tests]
            require(len(names) == len(set(names)) and set(selectors) <= set(names) and
                    all(e.get('outcome') == 'passed' for e in tests), 'skipped-or-missing-pass')
    elif entry['layer'] == 'B':
        report = reader.json(record.get('browserEventRef'))
        specs = []
        def visit(value):
            if isinstance(value, dict):
                specs.extend(value.get('specs', []))
                for suite in value.get('suites', []):
                    visit(suite)
        visit(report)
        for selector in selectors:
            found = [s for s in specs if s.get('title', '').split(' ', 1)[0] == selector or
                     s.get('title', '').startswith(selector + '.')]
            require(bool(found), 'skipped-or-missing-pass')
            for spec in found:
                require(bool(spec.get('tests')), 'skipped-or-missing-pass')
                for test in spec['tests']:
                    require(test.get('status') == 'expected' and bool(test.get('results')) and
                            all(result.get('status') == 'passed' and not result.get('error') and
                                not result.get('errors') for result in test['results']), 'skipped-or-missing-pass')
        require(not report.get('errors'), 'skipped-or-missing-pass')


def _original(record, entry, reader, candidate, tree):
    """组件原收据保持原字节；规范wrapper不能掩盖原件失败或旧源码。"""
    original = reader.json(record.get('originalReceipt'))
    require(isinstance(original, dict), 'invalid-original')
    require(original.get('testedCommit') == candidate and original.get('runnerCommit') == candidate and
            original.get('testedTree') == tree, 'wrong-source')
    _actual_source(original.get('source'), candidate, tree)
    _no_bridge(original)
    require(original.get('dirty', False) is False and original.get('source', {}).get('dirty', False) is False,
            'wrong-source')
    require(original.get('outcome') == 'passed', 'not-passed')
    require(original.get('cleanup', {}).get('success') is True, 'cleanup-failure')
    for field in ['variantId', 'layer', 'profile', 'purpose', 'steps', 'expected']:
        if field in original:
            require(original[field] == (record['profile'] if field == 'profile' else entry[field]), 'contract-mismatch')
    base = record.get('originalEvidenceBase', str(pathlib.PurePosixPath(record['originalReceipt']['path']).parent))
    require(isinstance(base, str) and '\\' not in base and not base.startswith('/') and
            all(part not in ['..', ''] for part in base.split('/')) if base else True, 'path-escape')
    def references(value):
        if isinstance(value, dict):
            if 'path' in value and 'sha256' in value:
                relative = value['path']
                require(isinstance(relative, str) and '\\' not in relative and
                        all(part not in ['', '.', '..'] for part in relative.split('/')), 'path-escape')
                normalized = str(pathlib.PurePosixPath(base) / relative)
                require(normalized in reader.files and reader.files[normalized]['sha256'] == value['sha256'],
                        'missing-original-reference')
            else:
                for child in value.values():
                    references(child)
        elif isinstance(value, list):
            for child in value:
                references(child)
    references(original)


def _environment(index, profile):
    """profile绑定真实OS/架构与目标集合；SMOKE不能用假data别名。"""
    environment = index.get('environment', {})
    host_os, arch = ('darwin', 'arm64') if profile == 'LOCAL_FULL' else ('linux', 'amd64')
    require(environment.get('runtimeOS') == host_os and environment.get('runtimeArch') == arch, 'wrong-profile')
    targets = environment.get('targets')
    require(isinstance(targets, list), 'wrong-environment')
    expected = {} if profile == 'CI_FAST' else {'control': 3, **({'data': 1} if profile == 'LOCAL_FULL' else {})}
    require(len(targets) == len(expected), 'wrong-environment')
    require({t.get('name'): t.get('nodeCount') for t in targets} == expected, 'wrong-environment')
    uids = [t.get('clusterUID') for t in targets]
    require(all(isinstance(uid, str) and uid for uid in uids) and len(uids) == len(set(uids)), 'wrong-environment')
    return host_os, arch, set(uids)


def _image(record, reader, candidate, arch, cluster_uids):
    """从原始OCI字节核manifest→config→revision，再绑定实际Pod链。"""
    image = record.get('productImage', {})
    require(image.get('revision') == candidate and image.get('os') == 'linux' and
            image.get('architecture') == arch, 'wrong-image-source')
    manifest_bytes = reader.read(image.get('manifestRef'))
    config_bytes = reader.read(image.get('configRef'))
    manifest = json_bytes(manifest_bytes)
    config = json_bytes(config_bytes)
    manifest_digest = 'sha256:' + hashlib.sha256(manifest_bytes).hexdigest()
    config_digest = 'sha256:' + hashlib.sha256(config_bytes).hexdigest()
    require(image.get('manifestDigest') == manifest_digest and image.get('configDigest') == config_digest and
            manifest.get('schemaVersion') == 2 and manifest.get('config', {}).get('digest') == config_digest,
            'wrong-image-digest')
    require(config.get('os') == 'linux' and config.get('architecture') == arch and
            config.get('config', {}).get('Labels', {}).get('org.opencontainers.image.revision') == candidate,
            'wrong-image-source')
    platform = record.get('platform', {})
    require(all(isinstance(platform.get(k), str) and platform[k] for k in
                ['podUID', 'nodeName', 'imageID', 'clusterUID', 'serviceName']), 'missing-platform-identity')
    require(platform['clusterUID'] in cluster_uids, 'wrong-environment')
    require(any(platform['imageID'].endswith(digest) for digest in [config_digest, manifest_digest]), 'wrong-image-digest')


def inspect(index_path, candidate, profile, supplemental=False, validator_test=False):
    """验证完整run；返回已实读的证据闭包供安全exporter使用。"""
    path = pathlib.Path(index_path).absolute()
    reader = Evidence(path.parent)
    require(not path.is_symlink(), 'missing-or-symlink-artifact')
    try:
        raw = path.read_bytes()
    except OSError:
        raise GateError('missing-index') from None
    index = json_bytes(raw)
    require(isinstance(index, dict) and index.get('schemaVersion') == 1, 'invalid-index')
    require(bool(re.fullmatch('[0-9a-f]{40}', candidate or '')), 'wrong-source')
    tree = index.get('testedTree')
    require(isinstance(tree, str) and re.fullmatch('[0-9a-f]{40}', tree), 'wrong-source')
    _source(index, candidate, tree)
    require(index.get('profile') == profile and index.get('supplemental') is supplemental, 'wrong-profile')
    boundary = index.get('executionBoundary')
    require(validator_test == (boundary == 'validator-test'), 'fixture-scope')
    require(index.get('state') == 'completed', 'incomplete-run')
    require(index.get('cleanup', {}).get('success') is True, 'cleanup-failure')
    require(all(isinstance(index.get(k), str) and index[k] for k in
                ['runId', 'startedAt', 'finishedAt']), 'invalid-index')
    host_os, arch, cluster_uids = _environment(index, profile)
    try:
        entries = registry.entries(profile, supplemental)
    except (ValueError, KeyError):
        raise GateError('registry-mismatch') from None
    expected = {(e['variantId'], e['layer']): e for e in entries}
    results = index.get('results')
    require(isinstance(results, list), 'invalid-index')
    keys = []
    for item in results:
        require(isinstance(item, dict), 'invalid-result')
        key = (item.get('variantId'), item.get('layer'))
        require(key in expected, 'unknown-key')
        require(key not in keys, 'duplicate-key')
        keys.append(key)
    require(set(keys) == set(expected), 'missing-key')
    declared = index.get('expectedKeys')
    require(isinstance(declared, list), 'expected-keys-mismatch')
    declared_keys = [(v.get('variantId'), v.get('layer')) for v in declared if isinstance(v, dict)]
    require(len(declared_keys) == len(declared) == len(expected) and
            set(declared_keys) == set(expected), 'expected-keys-mismatch')
    for item in results:
        record = reader.json(item.get('receipt'))
        entry = expected[(item['variantId'], item['layer'])]
        require(isinstance(record, dict), 'invalid-result')
        require(record.get('variantId') == entry['variantId'] and record.get('layer') == entry['layer'], 'unknown-key')
        _source(record, candidate, tree)
        require(record.get('profile') == profile, 'wrong-profile')
        require(record.get('contractFingerprint') == entry['contractFingerprint'], 'contract-mismatch')
        require(record.get('outcome') == 'passed', 'not-passed')
        require(record.get('cleanup', {}).get('success') is True, 'cleanup-failure')
        require(record.get('testSelectors') == entry['testSelectors'], 'selector-mismatch')
        require(isinstance(record.get('evidenceRefs'), list) and record['evidenceRefs'], 'missing-artifact')
        reader.closure(record)
        _original(record, entry, reader, candidate, tree)
        require(record.get('runtimeOS') == (host_os if entry['layer'] == 'Q' else 'linux') and
                record.get('runtimeArch') == arch, 'wrong-profile')
        if entry['layer'] in ['K', 'B']:
            _image(record, reader, candidate, arch, cluster_uids)
        _events(record, entry, reader)
    reader.closure(index)
    return index, reader, raw


def validate(index_path, candidate, profile, supplemental=False, validator_test=False):
    """公开接口仅返回安全计数；validator-test永远不产生产品门禁成功。"""
    index, _, _ = inspect(index_path, candidate, profile, supplemental, validator_test)
    return dict(success=True, profile=profile, supplemental=supplemental, count=len(index['results']),
                executionBoundary='validator-test' if validator_test else 'acceptance', productGate=not validator_test)


def protocol_tests(selectors, event_path):
    """执行真实unittest并记录机器事件；fixture Q不产生产品K结果。"""
    require(bool(selectors) and len(selectors) == len(set(selectors)) and
            all(re.fullmatch(r'[A-Za-z_][A-Za-z0-9_.]*', s) for s in selectors), 'invalid-selectors')
    def git(*args):
        result = subprocess.run(['git', *args], cwd=registry.ROOT, capture_output=True, text=True)
        require(result.returncode == 0, 'source-unavailable')
        return result.stdout.strip()
    source = dict(commit=git('rev-parse', 'HEAD'), tree=git('rev-parse', 'HEAD^{tree}'),
                  dirty=bool(git('status', '--porcelain')))
    events = []
    class Recorder(unittest.TextTestResult):
        def addSuccess(self, test):
            super().addSuccess(test)
            events.append(dict(selector=test.id(), outcome='passed'))
        def addFailure(self, test, error):
            super().addFailure(test, error)
            events.append(dict(selector=test.id(), outcome='failed'))
        def addError(self, test, error):
            super().addError(test, error)
            events.append(dict(selector=test.id(), outcome='failed'))
        def addSkip(self, test, reason):
            super().addSkip(test, reason)
            events.append(dict(selector=test.id(), outcome='skipped'))
    path = pathlib.Path(event_path).absolute()
    path.parent.mkdir(parents=True, exist_ok=True)
    private = path.parent / '.private'
    private.mkdir(mode=0o700, exist_ok=True)
    require(not private.is_symlink(), 'private-path-symlink')
    private.chmod(0o700)
    fd, log_path = tempfile.mkstemp(prefix='protocol-', suffix='.log', dir=private)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, 'w') as log, contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
        suite = unittest.defaultTestLoader.loadTestsFromNames(selectors)
        result = unittest.TextTestRunner(stream=log, verbosity=2, resultclass=Recorder).run(suite)
        log.flush()
        os.fsync(log.fileno())
    success = (result.wasSuccessful() and not result.skipped and result.testsRun == len(selectors) and
               {e['selector'] for e in events} == set(selectors))
    value = dict(schemaVersion=1, executionBoundary='validator-test', source=source,
                 commandExitCode=0 if success else 1, testsRun=result.testsRun, tests=events)
    fd, temporary = tempfile.mkstemp(prefix='.protocol-events-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream, sort_keys=True)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return dict(success=success, count=result.testsRun, executionBoundary='validator-test', productGate=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest='action', required=True)
    validating = actions.add_parser('validate')
    validating.add_argument('--index', required=True)
    validating.add_argument('--candidate', required=True)
    validating.add_argument('--profile', choices=registry.PROFILES, required=True)
    validating.add_argument('--supplemental', action='store_true')
    validating.add_argument('--validator-test', action='store_true')
    protocol = actions.add_parser('protocol-tests')
    protocol.add_argument('--events', required=True)
    protocol.add_argument('--selectors', nargs='+', required=True)
    args = parser.parse_args()
    try:
        if args.action == 'protocol-tests':
            summary = protocol_tests(args.selectors, args.events)
            print(json.dumps(summary))
            return 0 if summary['success'] else 1
        summary = validate(args.index, args.candidate, args.profile, args.supplemental, args.validator_test)
    except (GateError, TypeError, KeyError, AttributeError) as error:
        print(json.dumps(dict(success=False, code=error.code if isinstance(error, GateError) else 'invalid-schema')))
        return 1
    print(json.dumps(summary))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
