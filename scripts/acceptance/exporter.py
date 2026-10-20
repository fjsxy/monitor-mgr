#!/usr/bin/env python3
"""安全验收出口：完整验证后按白名单原样导出，在新路径再次验证。"""
import argparse
import hashlib
import json
import os
import pathlib
import re
import shutil
import subprocess
import tempfile
import uuid

import gate
import registry


PUBLIC_KINDS = {'receipt', 'go-events', 'protocol-events', 'browser-report', 'safe-summary',
                'safe-json', 'screenshot', 'image-manifest', 'image-config', 'hash', 'source-commit'}


def _public_path(path):
    """读文件前拒绝私有目录及越界；系统可信根以上的路径不在此相对路径检查内。"""
    gate.require(isinstance(path, str) and not path.startswith('/') and '\\' not in path and
                 all(part not in ['', '.', '..'] for part in path.split('/')), 'path-escape')
    lowered = path.lower()
    gate.require(not any(part in ['private', '.private', 'raw', 'private-source', 'private-checkout', '.git',
        'kubeconfig', 'kubeconfigs', 'credentials', 'cookies', 'session', 'node_modules']
        for part in lowered.split('/')), 'private-or-unapproved-artifact')
    gate.require(not re.search(r'(?:\.raw|\.db|\.sqlite\d*|[-.]wal|[-.]shm|\.key|\.pem|\.har|\.tar|\.tgz|\.zip|\.gz)$',
                               lowered), 'private-or-unapproved-artifact')


class DiagnosticEvidence(gate.Evidence):
    """typed gate 每次读原件之前过路径/类型门，读后按原bytes安全扫描，才加入公开文件集。"""
    def __init__(self, root, canaries, files):
        super().__init__(root)
        self.canaries, self.public_files = canaries, files

    def read(self, reference):
        gate.require(isinstance(reference, dict) and reference.get('visibility') == 'public' and
                     reference.get('kind') in PUBLIC_KINDS, 'private-or-unapproved-artifact')
        path = reference.get('path')
        _public_path(path)
        gate.require(not any(value and value in path.encode() for value in self.canaries), 'sensitive-canary')
        data = super().read(reference)
        _safe(path, data, reference['kind'], reference['visibility'], self.canaries)
        gate.require(path not in self.public_files or self.public_files[path] == data, 'export-path-collision')
        self.public_files[path] = data
        return data


def _credential_object(value):
    """识别可直接使用的认证材料，而非仅依赖文件扩展名。"""
    if isinstance(value, dict):
        if value.get('kind') == 'Config' and any(k in value for k in ['clusters', 'users', 'contexts']):
            return True
        for key, child in value.items():
            if key.lower() in ['client-key-data', 'client-certificate-data', 'sessiontoken', 'privatekey'] and child:
                return True
            if _credential_object(child):
                return True
    elif isinstance(value, list):
        return any(_credential_object(child) for child in value)
    return False


def _safe(path, data, kind, visibility, canaries):
    """显式批准类型是必要条件；私有材料即使获误标也不进入bundle。"""
    gate.require(visibility == 'public' and kind in PUBLIC_KINDS, 'private-or-unapproved-artifact')
    gate.require(not any(value and (value in data or value in path.encode()) for value in canaries), 'sensitive-canary')
    gate.require(not re.search(rb'-----BEGIN [A-Z ]*PRIVATE KEY-----', data) and
                 not data.startswith(b'SQLite format 3\x00'), 'private-or-unapproved-artifact')
    _public_path(path)
    suffix = pathlib.PurePosixPath(path).suffix
    if kind == 'screenshot':
        allowed = suffix in ['.png', '.jpg', '.jpeg', '.webp'] and (
            data.startswith(b'\x89PNG\r\n\x1a\n') or data.startswith(b'\xff\xd8\xff') or
            data.startswith(b'RIFF') and data[8:12] == b'WEBP')
        gate.require(allowed, 'unsafe-artifact-type')
    elif kind == 'go-events':
        gate.require(suffix == '.jsonl', 'unsafe-artifact-type')
        for line in data.splitlines():
            if line.strip():
                gate.json_bytes(line)
    elif ((kind == 'source-commit' and suffix == '.commit') or
          (kind == 'safe-summary' and suffix in ['.md', '.txt', '.log'])):
        try:
            data.decode('utf-8')
        except UnicodeError:
            raise gate.GateError('unsafe-artifact-type') from None
    else:
        gate.require(suffix == '.json', 'unsafe-artifact-type')
        value = gate.json_bytes(data)
        gate.require(not _credential_object(value), 'private-or-unapproved-artifact')


def export(index_path, destination, candidate, profile, supplemental=False, canaries=(), validator_test=False,
           diagnostic=False):
    """失败不留下可发布目录；hash与原件从不因脱敏/排除被重写。"""
    if diagnostic:
        return diagnostic_export(index_path, destination, candidate, profile, supplemental, canaries, validator_test)
    index, reader, raw = gate.inspect(index_path, candidate, profile, supplemental, validator_test)
    destination = pathlib.Path(destination).absolute()
    gate.require(not destination.exists() and not destination.is_symlink(), 'destination-exists')
    gate.require(not destination.resolve().is_relative_to(reader.root), 'destination-inside-run')
    encoded = tuple(value.encode() if isinstance(value, str) else value for value in canaries)
    files = {}
    for path, ref in reader.files.items():
        data = reader.bytes[path]
        _safe(path, data, ref['kind'], ref['visibility'], encoded)
        files[path] = data
    index_name = pathlib.Path(index_path).name
    _safe(index_name, raw, 'receipt', 'public', encoded)
    gate.require(index_name not in files, 'export-path-collision')
    files[index_name] = raw
    # 关键：复验所需注册源与验证器一同导出，不依赖原cwd、Git或第三方包。
    verifier_paths = ['scripts/acceptance/registry.py', 'scripts/acceptance/gate.py',
                      'docs/acceptance/phase1.json', 'docs/e2e/issue-21.md',
                      'docs/e2e/issue-21-plan-sources.json',
                      *[f'docs/e2e/issue-{number}.md' for number in range(14, 21)]]
    for path in verifier_paths:
        gate.require(path not in files, 'export-path-collision')
        data = (registry.ROOT / path).read_bytes()
        gate.require(not any(value and value in data for value in encoded), 'sensitive-canary')
        files[path] = data
    destination.parent.mkdir(parents=True, exist_ok=True)
    stage = pathlib.Path(tempfile.mkdtemp(prefix='.acceptance-export-', dir=destination.parent))
    try:
        for path, data in files.items():
            output = stage / path
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(data)
        gate.validate(stage / index_name, candidate, profile, supplemental, validator_test)
        inventory = {path: hashlib.sha256(data).hexdigest() for path, data in sorted(files.items())}
        (stage / 'bundle-files.json').write_text(json.dumps(inventory, sort_keys=True, indent=2) + '\n')
        # 发布前再次读全部落盘字节；public hash清单与run原始hash各自保持原义。
        gate.require(all(hashlib.sha256((stage / path).read_bytes()).hexdigest() == digest
                         for path, digest in inventory.items()), 'export-copy-mismatch')
        stage.rename(destination)
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    return dict(success=True, profile=profile, supplemental=supplemental, count=len(index['results']),
                files=len(files), executionBoundary='validator-test' if validator_test else 'acceptance',
                productGate=not validator_test, gatePassed=not validator_test, diagnosticOnly=False)


def diagnostic_export(index_path, destination, candidate, profile, supplemental=False, canaries=(), validator_test=False):
    """保留失败原件的安全部分；排除/missing明确记账，绝不形成绿色门禁。"""
    path = pathlib.Path(index_path).absolute()
    gate.require(not path.is_symlink(), 'missing-or-symlink-artifact')
    reader = gate.Evidence(path.parent)
    raw = path.read_bytes()
    index = gate.json_bytes(raw)
    gate.require(isinstance(index, dict) and isinstance(index.get('results'), list), 'invalid-index')
    destination = pathlib.Path(destination).absolute()
    gate.require(not destination.exists() and not destination.is_symlink(), 'destination-exists')
    gate.require(not destination.resolve().is_relative_to(reader.root), 'destination-inside-run')
    encoded = tuple(value.encode() if isinstance(value, str) else value for value in canaries)
    _safe(path.name, raw, 'receipt', 'public', encoded)
    files = {path.name: raw}
    reader = DiagnosticEvidence(path.parent, encoded, files)
    try:
        # 关键：完成态前置检查与后续typed遍历共用受限reader，任何阶段都不能先读private再过滤。
        gate.inspect(path, candidate, profile, supplemental, validator_test, evidence=reader)
        gate_code = 'diagnostic-requested'
    except gate.GateError as error:
        gate_code = error.code
    excluded, results = [], []
    protocol_entries = {entry['variantId']: entry for entry in registry.entries(profile, supplemental)
                        if entry['executionBoundary'] == 'kubernetes-protocol'}
    protocol_boundary = 'validator-test' if validator_test else 'kubernetes-protocol'
    def typed_protocol(value):
        return isinstance(value, dict) and value.get('variantId') in protocol_entries and \
               value.get('executionBoundary') == protocol_boundary
    seen = set()
    def collect(value):
        if isinstance(value, dict):
            if 'path' in value and 'sha256' in value:
                identity = (str(value.get('path')), str(value.get('sha256')))
                if identity in seen:
                    return
                seen.add(identity)
                try:
                    # 关键：private/raw目录根本不读；只有获准类型才进入字节扫描。
                    gate.require(value.get('visibility') == 'public' and value.get('kind') in PUBLIC_KINDS,
                                 'private-or-unapproved-artifact')
                    data = reader.read(value)
                    _safe(value['path'], data, value['kind'], value['visibility'], encoded)
                    gate.require(value['path'] not in files or files[value['path']] == data, 'export-path-collision')
                    files[value['path']] = data
                    if pathlib.PurePosixPath(value['path']).suffix in ['.json', '.jsonl']:
                        if value['kind'] == 'go-events':
                            for line in data.splitlines():
                                if line.strip():
                                    collect(gate.json_bytes(line))
                        else:
                            parsed = gate.json_bytes(data)
                            # 协议原件中的refs相对case根；不能在此当作run根下的任意JSON路径展开。
                            if not typed_protocol(parsed):
                                collect(parsed)
                except (gate.GateError, ValueError, TypeError, KeyError) as error:
                    excluded.append(dict(pathSHA256=hashlib.sha256(identity[0].encode()).hexdigest(),
                        sha256=identity[1] if re.fullmatch('[0-9a-f]{64}', identity[1]) else None,
                        code=error.code if isinstance(error, gate.GateError) else 'invalid-reference'))
            else:
                for child in value.values():
                    collect(child)
        elif isinstance(value, list):
            for child in value:
                collect(child)
    collect(index)
    protocol_closures = []
    for item in index['results']:
        if not isinstance(item, dict) or item.get('variantId') not in protocol_entries or item.get('layer') != 'K':
            continue
        if not item.get('receipt'):
            continue
        entry = protocol_entries[item['variantId']]
        record_ref = item['receipt']
        identity = str(record_ref.get('path')) if isinstance(record_ref, dict) else ''
        result = dict(variantId=entry['variantId'], complete=False)
        try:
            record = reader.json(record_ref)
            gate.require(isinstance(record, dict), 'invalid-original')
            base = 'cases/K.' + entry['variantId']
            gate.require(record.get('executionBoundary') == protocol_boundary and
                         record.get('originalEvidenceBase') == base, 'wrong-protocol-identity')
            gate._source(record, candidate, index.get('testedTree'))
            reader.closure(record)
            gate._original(record, entry, reader, candidate, index.get('testedTree'))
            proof = reader.json(record.get('kubernetesProtocolRef'))
            gate.require(isinstance(proof, dict) and proof.get('runId') == index.get('runId'), 'wrong-protocol-identity')
            # 关键：仅登记的八协议使用既有typed gate收集case及恢复原件；不合成、改写或全目录复制。
            gate.kubernetes_protocol(proof, entry, gate.RelativeEvidence(reader, base),
                {target['clusterUID'] for target in index.get('environment', {}).get('targets', [])},
                index.get('environment', {}).get('runtimeArch'), candidate, index.get('testedTree'), validator_test)
            result['complete'] = True
        except (gate.GateError, ValueError, TypeError, KeyError) as error:
            code = error.code if isinstance(error, gate.GateError) else 'invalid-reference'
            result['code'] = code
            excluded.append(dict(pathSHA256=hashlib.sha256(identity.encode()).hexdigest(), sha256=None, code=code))
        protocol_closures.append(result)
    for item in index['results']:
        if not isinstance(item, dict):
            continue
        receipt = item.get('receipt', {})
        record = {}
        if isinstance(receipt, dict) and receipt.get('path') in files:
            record = gate.json_bytes(files[receipt['path']])
        result = {k: item.get(k) for k in ['variantId', 'layer']}
        result['outcome'] = record.get('outcome', 'unavailable')
        result['cleanupSuccess'] = record.get('cleanup', {}).get('success')
        result['actualSource'] = {k: record.get(k) for k in
                                 ['testedCommit', 'testedTree', 'runnerCommit', 'dirty']}
        results.append(result)
    expected = [dict(variantId=e['variantId'], layer=e['layer']) for e in registry.entries(profile, supplemental)]
    summary = dict(schemaVersion=1, diagnosticOnly=True, gatePassed=False, productGate=False,
        originalIndexSHA256=hashlib.sha256(raw).hexdigest(), candidateCommit=candidate, profile=profile,
        supplemental=supplemental, gateCode=gate_code, expectedKeys=expected, results=results,
        excludedRefs=excluded, protocolClosures=protocol_closures, copiedFiles=len(files))
    summary_bytes = (json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode()
    _safe('diagnostic-summary.json', summary_bytes, 'safe-summary', 'public', encoded)
    gate.require('diagnostic-summary.json' not in files, 'export-path-collision')
    files['diagnostic-summary.json'] = summary_bytes
    verifier_paths = ['scripts/acceptance/registry.py', 'scripts/acceptance/gate.py',
                      'docs/acceptance/phase1.json', 'docs/e2e/issue-21.md',
                      'docs/e2e/issue-21-plan-sources.json',
                      *[f'docs/e2e/issue-{number}.md' for number in range(14, 21)]]
    for name in verifier_paths:
        gate.require(name not in files, 'export-path-collision')
        data = (registry.ROOT / name).read_bytes()
        gate.require(not any(value and value in data for value in encoded), 'sensitive-canary')
        files[name] = data
    destination.parent.mkdir(parents=True, exist_ok=True)
    stage = pathlib.Path(tempfile.mkdtemp(prefix='.acceptance-diagnostic-', dir=destination.parent))
    try:
        inventory = {}
        for name, data in files.items():
            output = stage / name
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(data)
            inventory[name] = hashlib.sha256(data).hexdigest()
        (stage / 'bundle-files.json').write_text(json.dumps(inventory, sort_keys=True, indent=2) + '\n')
        gate.require(all(hashlib.sha256((stage / name).read_bytes()).hexdigest() == digest
                         for name, digest in inventory.items()), 'export-copy-mismatch')
        stage.rename(destination)
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    return dict(success=True, diagnosticOnly=True, gatePassed=False, productGate=False,
                count=len(results), excluded=len(excluded), gateCode=gate_code)


def capture_command(command, private_directory, case_id):
    """子进程原输出直接落0600文件；公开只返回ID/exit/hash，不先tee。"""
    gate.require(bool(command) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,159}', case_id or ''), 'invalid-command')
    directory = pathlib.Path(private_directory).absolute()
    gate.require(not directory.is_symlink(), 'private-path-symlink')
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory.chmod(0o700)
    path = directory / (case_id + '-' + uuid.uuid4().hex + '.log')
    # 关键：所有异常也只进入原件；原始命令/异常文本可能携带认证数据。
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as original:
        try:
            process = subprocess.run(command, stdout=original, stderr=subprocess.STDOUT)
            exit_code = process.returncode
        except OSError:
            original.write(b'process-start-failed\n')
            exit_code = 127
        original.flush()
        os.fsync(original.fileno())
    return dict(caseId=case_id, commandExitCode=exit_code, outcome='passed' if exit_code == 0 else 'failed',
                privateRef=dict(path=path.name, sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                                visibility='private', kind='raw-output'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest='action', required=True)
    exporting = actions.add_parser('export')
    exporting.add_argument('--index', required=True)
    exporting.add_argument('--destination', required=True)
    exporting.add_argument('--candidate', required=True)
    exporting.add_argument('--profile', choices=registry.PROFILES, required=True)
    exporting.add_argument('--supplemental', action='store_true')
    exporting.add_argument('--validator-test', action='store_true')
    exporting.add_argument('--diagnostic', action='store_true')
    # canary由环境/文件交付，避免进入命令行或公开摘要；文件本身不导出。
    exporting.add_argument('--canary-file')
    capturing = actions.add_parser('capture')
    capturing.add_argument('--private-directory', required=True)
    capturing.add_argument('--case-id', required=True)
    capturing.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    try:
        if args.action == 'capture':
            summary = capture_command(args.command[1:] if args.command[:1] == ['--'] else args.command,
                                      args.private_directory, args.case_id)
            print(json.dumps(summary))
            code = summary['commandExitCode']
            return code if code >= 0 else 128 - code
        canaries = json.loads(pathlib.Path(args.canary_file).read_text()) if args.canary_file else []
        gate.require(isinstance(canaries, list) and all(isinstance(c, str) and c for c in canaries), 'invalid-canaries')
        summary = export(args.index, args.destination, args.candidate, args.profile,
                         args.supplemental, canaries, args.validator_test, args.diagnostic)
    except (gate.GateError, OSError, ValueError, TypeError, KeyError, AttributeError) as error:
        print(json.dumps(dict(success=False, code=error.code if isinstance(error, gate.GateError) else 'invalid-export')))
        return 1
    print(json.dumps(summary))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
