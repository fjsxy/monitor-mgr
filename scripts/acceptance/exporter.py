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
                'safe-json', 'screenshot', 'image-manifest', 'image-config', 'hash'}


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
    gate.require(not any(value and value in data for value in canaries), 'sensitive-canary')
    gate.require(not re.search(rb'-----BEGIN [A-Z ]*PRIVATE KEY-----', data) and
                 not data.startswith(b'SQLite format 3\x00'), 'private-or-unapproved-artifact')
    lowered = path.lower()
    segments = lowered.split('/')
    gate.require(not any(part in ['private', 'private-source', 'private-checkout', '.git',
        'kubeconfig', 'kubeconfigs', 'credentials', 'cookies', 'session', 'node_modules'] for part in segments),
        'private-or-unapproved-artifact')
    gate.require(not re.search(r'(?:\.db|\.sqlite\d*|[-.]wal|[-.]shm|\.key|\.pem|\.har|\.tar|\.tgz|\.zip|\.gz)$',
                               lowered), 'private-or-unapproved-artifact')
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
    elif kind == 'safe-summary' and suffix in ['.md', '.txt', '.log']:
        try:
            data.decode('utf-8')
        except UnicodeError:
            raise gate.GateError('unsafe-artifact-type') from None
    else:
        gate.require(suffix == '.json', 'unsafe-artifact-type')
        value = gate.json_bytes(data)
        gate.require(not _credential_object(value), 'private-or-unapproved-artifact')


def export(index_path, destination, candidate, profile, supplemental=False, canaries=(), validator_test=False):
    """失败不留下可发布目录；hash与原件从不因脱敏/排除被重写。"""
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
                productGate=not validator_test)


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
                         args.supplemental, canaries, args.validator_test)
    except (gate.GateError, OSError, ValueError, TypeError, KeyError, AttributeError) as error:
        print(json.dumps(dict(success=False, code=error.code if isinstance(error, gate.GateError) else 'invalid-export')))
        return 1
    print(json.dumps(summary))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
