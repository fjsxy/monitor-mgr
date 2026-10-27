#!/usr/bin/env python3
"""核验并获取已发布的 Operator 原始资产；不构建或重新打包 Operator。"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import urllib.error
import urllib.parse
import urllib.request

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


def anonymous_bytes(url):
    """公开浏览地址只走经 TLS 校验的 HTTPS，不读 gh/用户登录材料。"""
    class HTTPSRedirect(urllib.request.HTTPRedirectHandler):
        # 关键：所有重定向仍须使用 HTTPS，禁止降级传输。
        def redirect_request(self, request, fp, code, message, headers, newurl):
            if urllib.parse.urlsplit(newurl).scheme != 'https':
                raise ReleaseError('RELEASE_HTTPS_REQUIRED')
            return super().redirect_request(request, fp, code, message, headers, newurl)
    if urllib.parse.urlsplit(url).scheme != 'https':
        raise ReleaseError('RELEASE_HTTPS_REQUIRED')
    opener = urllib.request.build_opener(HTTPSRedirect())
    request = urllib.request.Request(url, headers={'User-Agent': 'monitor-mgr-release-verifier'})
    try:
        with opener.open(request, timeout=60) as response:
            content = response.read(16 * 1024 * 1024 + 1)
            if response.status != 200 or len(content) > 16 * 1024 * 1024:
                raise ReleaseError('RELEASE_DOWNLOAD_INVALID')
            return content
    except (OSError, urllib.error.URLError):
        raise ReleaseError('RELEASE_FETCH_FAILED') from None


def fetch_bundle(directory, download=None):
    """关键：匿名获取原资产后逐字节核锁定摘要，任何失败均不降级为重构。"""
    lock = json.loads(LOCK_PATH.read_text())['monitorOp']
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False, mode=0o700)
    download = download or anonymous_bytes
    base = 'https://github.com/' + lock['repository'] + '/releases/download/' + lock['release']['tag'] + '/'
    for name, expected in lock['release']['assets'].items():
        content = download(base + urllib.parse.quote(name, safe=''))
        if hashlib.sha256(content).hexdigest() != expected:
            raise ReleaseError('RELEASE_ASSET_DIGEST_MISMATCH')
        path = directory / name
        with path.open('xb') as stream: stream.write(content)
        path.chmod(0o600)
    return bundle_proof(directory, lock)


def fetch_source(directory):
    """只获取公开仓库的锁定 Git 对象，禁用交互及用户 credential helper。"""
    lock = json.loads(LOCK_PATH.read_text())['monitorOp']
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False, mode=0o700)
    repository = 'https://github.com/' + lock['repository'] + '.git'
    # 关键：公开消费不继承 token/用户 Git 配置或 SSH；本地显式源码入口仍由调用者选择。
    env = {key: value for key, value in os.environ.items() if not key.startswith(('GIT_', 'GH_')) and key not in ('GITHUB_TOKEN', 'SSH_AUTH_SOCK')}
    env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull, GIT_TERMINAL_PROMPT='0',
               GIT_ASKPASS='false', GIT_CONFIG_COUNT='1', GIT_CONFIG_KEY_0='credential.helper', GIT_CONFIG_VALUE_0='')
    # 执行禁用用户凭据的 Git 命令，原输出仅留在内存供摘要核验。
    def run(*args):
        result = subprocess.run(['git', '-C', str(directory), *args], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=180)
        if result.returncode: raise ReleaseError('RELEASE_SOURCE_FETCH_FAILED')
        return result.stdout.decode().strip()
    run('init', '--quiet')
    run('remote', 'add', 'origin', repository)
    run('fetch', '--quiet', '--depth=1', '--no-tags', 'origin', lock['source'])
    run('checkout', '--quiet', '--detach', lock['source'])
    actual = run('rev-parse', 'HEAD'); dirty = run('status', '--porcelain')
    if actual != lock['source'] or dirty: raise ReleaseError('RELEASE_SOURCE_MISMATCH')
    return dict(source=actual, repository=repository, dirty=False, anonymous=True)


# 解析 source/fetch/verify CLI 参数并仅输出安全错误码。
def main():
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest='action', required=True)
    for action in ('fetch', 'verify'):
        actions.add_parser(action).add_argument('--bundle', required=True)
    actions.add_parser('source').add_argument('--destination', required=True)
    args = parser.parse_args()
    try:
        proof = (fetch_source(args.destination) if args.action == 'source' else
                 fetch_bundle(args.bundle) if args.action == 'fetch' else bundle_proof(args.bundle))
    except (ReleaseError, OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        # 外部请求原文不进入 CI；调用者保留本步骤失败，不回退源码重构。
        print(json.dumps(dict(error=str(error) if isinstance(error, ReleaseError) else 'RELEASE_VALIDATION_FAILED')))
        return 1
    print(json.dumps(proof, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
