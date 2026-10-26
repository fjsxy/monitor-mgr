#!/usr/bin/env python3
"""核验并获取已发布的 Operator 原始资产；不构建或重新打包 Operator。"""
import argparse
import hashlib
import json
from pathlib import Path
import stat
import subprocess

LOCK_PATH = Path(__file__).with_name('source-lock.json')


class ReleaseError(ValueError):
    """错误码只标记可信边界，不投影私有路径或凭据。"""


def bundle_proof(directory, lock=None):
    """关键：原 manifest 与每个原资产均匹配锁定字节，才允许调用安装工具。"""
    lock = lock or json.loads(LOCK_PATH.read_text())['monitorOp']
    release = lock['release']
    directory = Path(directory)
    for name, expected in release['assets'].items():
        if name != Path(name).name or name in ('.', '..') or '\\' in name:
            raise ReleaseError('RELEASE_ASSET_NAME_INVALID')
        path = directory / name
        try:
            regular = stat.S_ISREG(path.lstat().st_mode)
            raw = path.read_bytes() if regular else b''
        except OSError:
            raise ReleaseError('RELEASE_ASSET_MISSING') from None
        if not regular or hashlib.sha256(raw).hexdigest() != expected:
            raise ReleaseError('RELEASE_ASSET_DIGEST_MISMATCH')
    raw = (directory / 'release.json').read_bytes()
    if hashlib.sha256(raw).hexdigest() != release['manifestSHA256']:
        raise ReleaseError('RELEASE_MANIFEST_DIGEST_MISMATCH')
    manifest = json.loads(raw)
    if (manifest['sourceCommit'] != lock['source'] or manifest['testedCommit'] != lock['testedCommit']
            or manifest['version'] != release['version'] or manifest['image'] != release['image']):
        raise ReleaseError('RELEASE_SOURCE_MISMATCH')
    for name, digest in manifest['files'].items():
        if release['assets'].get(name) != digest:
            raise ReleaseError('RELEASE_FILE_LOCK_MISMATCH')
    return dict(version=manifest['version'], source=manifest['sourceCommit'], testedCommit=manifest['testedCommit'],
                image=manifest['image']['repository']+'@'+manifest['image']['indexDigest'],
                indexDigest=manifest['image']['indexDigest'], platforms=manifest['image']['platforms'],
                manifestSHA256=release['manifestSHA256'], assets=dict(release['assets']), chart=manifest['chart'],
                crd=manifest['crd'], rbac=manifest['rbac'], contract=manifest['contract'])


def fetch_bundle(directory):
    """发布下载只使用 gh 既有认证；不将 token 传入 argv、源码获取或后续构建。"""
    lock = json.loads(LOCK_PATH.read_text())['monitorOp']
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False, mode=0o700)
    command = ['gh', 'release', 'download', lock['release']['tag'], '--repo', lock['repository'], '--dir', str(directory)]
    for name in lock['release']['assets']:
        command += ['--pattern', name]
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=240)
    if result.returncode:
        raise ReleaseError('RELEASE_FETCH_FAILED')
    return bundle_proof(directory, lock)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('fetch', 'verify'))
    parser.add_argument('--bundle', required=True)
    args = parser.parse_args()
    try:
        proof = fetch_bundle(args.bundle) if args.action == 'fetch' else bundle_proof(args.bundle)
    except (ReleaseError, OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        # 私有 gh 原文不进入 CI；调用者保留本步骤失败，不回退源码重构。
        print(json.dumps(dict(error=str(error) if isinstance(error, ReleaseError) else 'RELEASE_VALIDATION_FAILED')))
        return 1
    print(json.dumps(proof, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
