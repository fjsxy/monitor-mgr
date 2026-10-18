#!/usr/bin/env python3
"""可信 Smoke 的固定私有源入口；密钥仅本步骤临时文件，源码/构建层不走公开出口。"""
import argparse
import hashlib
import json
import os
import pathlib
import shlex
import sys

from capture import capture

ROOT = pathlib.Path(__file__).resolve().parent
LOCK = json.loads((ROOT / 'source-lock.json').read_text())
SOURCE = LOCK['monitorOp']['source']
REPOSITORY = LOCK['monitorOp']['repository']


def fetch_source(destination, private_root):
    """只接受已授权仓库和固定 SHA；既有目录不 reset/clean。"""
    repository = os.environ.get('MONITOR_OP_SOURCE_REPOSITORY', '')
    key = os.environ.get('MONITOR_OP_DEPLOY_KEY', '')
    destination, private_root = pathlib.Path(destination), pathlib.Path(private_root)
    if repository != REPOSITORY or not key or not destination.is_absolute() or destination.exists() or not private_root.is_absolute():
        raise ValueError('SOURCE_INPUT_INVALID')
    private_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(private_root, 0o700)
    key_path = private_root / 'monitor-op-deploy-key'
    if hashlib.sha256((ROOT / 'github-known-hosts').read_bytes()).hexdigest() != LOCK['monitorOp']['sshHostsSHA256']:
        raise ValueError('SOURCE_HOST_KEYS_MISMATCH')
    key_descriptor = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(key_descriptor, 'w') as stream:
            stream.write(key.rstrip() + '\n')
    except BaseException:
        key_path.unlink(missing_ok=True)
        raise
    child_environment = {'PATH': os.environ['PATH'], 'LANG': 'C.UTF-8'}
    child_environment.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL='/dev/null', GIT_TERMINAL_PROMPT='0')
    child_environment['GIT_SSH_COMMAND'] = shlex.join(['ssh', '-F', '/dev/null', '-i', str(key_path), '-o', 'IdentitiesOnly=yes',
                                                     '-o', 'BatchMode=yes', '-o', 'IdentityAgent=none', '-o', 'StrictHostKeyChecking=yes',
                                                     '-o', 'UserKnownHostsFile=' + str(ROOT / 'github-known-hosts')])
    def run(argv, label):
        result = capture(argv, private_root=str(private_root / 'raw'), label=label, env=child_environment, publish=False, timeout=180)
        if result.returncode:
            raise ValueError('SOURCE_FETCH_FAILED')
        return result.stdout.decode().strip()
    try:
        destination.mkdir(parents=True, mode=0o700)
        run(['git', 'init', '--quiet', str(destination)], 'private-source-init')
        run(['git', '-C', str(destination), 'remote', 'add', 'origin', 'git@github.com:' + REPOSITORY + '.git'], 'private-source-remote')
        run(['git', '-C', str(destination), 'fetch', '--depth=1', '--no-tags', 'origin', SOURCE], 'private-source-fetch')
        run(['git', '-C', str(destination), 'checkout', '--quiet', '--detach', 'FETCH_HEAD'], 'private-source-checkout')
        actual = run(['git', '-C', str(destination), 'rev-parse', 'HEAD'], 'private-source-revision')
        dirty = run(['git', '-C', str(destination), 'status', '--porcelain'], 'private-source-clean')
        if actual != SOURCE or dirty:
            raise ValueError('SOURCE_REVISION_MISMATCH')
        return {'id': 'private-source', 'exit': 0, 'source': actual, 'dirty': False}
    finally:
        # 关键：无论 fetch/checkout 成败，密钥不留给下一步编译或 Go/Docker cache。
        key_path.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description='可信 Smoke 固定私有源码')
    parser.add_argument('--destination', required=True)
    parser.add_argument('--private-root', required=True)
    args = parser.parse_args()
    try:
        summary = fetch_source(args.destination, args.private_root)
    except (ValueError, OSError):
        summary = {'id': 'private-source', 'exit': 1}
    print(json.dumps(summary), flush=True)
    return summary['exit']


if __name__ == '__main__':
    sys.exit(main())
