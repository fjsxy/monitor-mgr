#!/usr/bin/env python3
"""REG21 的八个 Kubernetes 协议：协调真实 child，业务执行仍由现有 Session 负责。"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys
import time
import uuid

from session import BatchSession, atomic_json, stamp
from transport import ProcessRunner, group_members, process_identity


MODES = {
    'REG21-SIGNAL-002.term-after-setup': 'term',
    'REG21-SIGNAL-003.keyboard-interrupt': 'int',
    'REG21-SIGNAL-004.repeat-during-cleanup': 'repeat',
    'REG21-RESULT-005.setup-failure': 'setup',
    'REG21-RESULT-006.cleanup-failure': 'cleanup-denied',
    'REG21-RECOVER-008.kill-then-recover': 'kill',
    'REG21-LEDGER-009.uid-replaced': 'replaced',
    'REG21-LEDGER-010.read-failure': 'read-denied',
}
HOST_MODES = ('term', 'int', 'repeat', 'setup', 'kill')
HERE = Path(__file__).resolve()
ROOT = HERE.parents[2]


class ProtocolError(RuntimeError):
    """异常只输出稳定错误码，原始 API/进程输出留在 ProcessRunner 私有捕获。"""


def require(value: object, code: str) -> None:
    if not value:
        raise ProtocolError(code)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def atomic_bytes(path: Path, raw: bytes) -> None:
    """原响应保持原 bytes，并在发 checkpoint 前把文件和目录项同步到磁盘。"""
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.new')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def reference(path: Path, root: Path, kind: str = 'safe-json') -> dict:
    """仅建立本例根内的普通文件引用，不允许 private handoff 或捕获进入公开闭包。"""
    require(path.resolve().is_relative_to(root.resolve()) and not path.is_symlink(), 'reference-outside-case')
    relative = path.relative_to(root).as_posix()
    require('.private' not in Path(relative).parts, 'private-reference')
    return {'path': relative, 'sha256': sha(path), 'kind': kind, 'visibility': 'public'}


def wait_file(path: Path, child: subprocess.Popen | None = None, *, timeout: float = 30,
              session: BatchSession | None = None) -> dict:
    """屏障只协调真实进程，不凭 sleep 推断 UID 已落盘或清理已经进入。"""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if path.exists():
            return json.loads(path.read_bytes())
        if child is not None and child.poll() is not None:
            raise ProtocolError('child-exited-before-checkpoint')
        if session is not None:
            session.checkpoint()
            if session.cleaning and session.signals != session.index.get('signals'):
                session.flush()
        time.sleep(.02)
    raise ProtocolError('protocol-barrier-timeout')


def binding(request: dict) -> dict:
    """所有控制屏障、恢复报告使用同一身份绑定；路径名称不构成所有权。"""
    return {k: copy.deepcopy(request[k]) for k in
            ['runId', 'profile', 'candidateCommit', 'testedTree', 'runnerCommit', 'dirty', 'variantId', 'targetUID']}


def private_request(path: Path, request: dict) -> str:
    atomic_json(path, request)
    return sha(path)


def load_request(path: str, digest: str, *, host: bool) -> dict:
    """关键：handoff 必须是本用户 0600 原件；hash、run/profile/C/目标 UID 在 child 内重核。"""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as stream:
        mode = os.fstat(stream.fileno())
        require(stat.S_ISREG(mode.st_mode) and mode.st_uid == os.getuid() and
                stat.S_IMODE(mode.st_mode) == 0o600, 'unsafe-handoff')
        raw = stream.read()
    require(hashlib.sha256(raw).hexdigest() == digest, 'changed-handoff')
    request = json.loads(raw)
    for key in ['runId', 'profile', 'candidateCommit', 'testedTree', 'runnerCommit', 'variantId', 'targetUID',
                'output', 'apiLane', 'source', 'mode', 'scope']:
        require(key in request, 'incomplete-handoff')
    require(request['scope'] == ('validator-test' if host else 'kubernetes-protocol'), 'wrong-handoff-scope')
    require(request['dirty'] is False and all(request.get(k) in [None, False] for k in ['sourceBridge', 'source_bridge', 'bridge']) and request['source'] == {
        'commit': request['candidateCommit'], 'tree': request['testedTree'], 'dirty': False} and
        request['runnerCommit'] == request['candidateCommit'], 'wrong-handoff-source')
    if host:
        require(request['profile'] == 'validator-test' and request['targetUID'] is None and
                request['mode'] in HOST_MODES and request['variantId'] == 'host.protocol', 'host-scope')
    else:
        import registry
        require(request['profile'] == 'LOCAL_FULL' and request['variantId'] in MODES and
                request['mode'] == MODES[request['variantId']], 'wrong-handoff-case')
        require(re.fullmatch('[0-9a-f]{40}', request['candidateCommit']) and
                re.fullmatch('[0-9a-f]{40}', request['testedTree']), 'wrong-handoff-source')
        require(request.get('target') == 'control' and
                request['targets']['control']['clusterUID'] == request['targetUID'] and request['targetUID'],
                'wrong-handoff-target')
        entry = registry.entries('LOCAL_FULL', True, [request['variantId']])[0]
        require(entry['layer'] == 'K' and request['contractFingerprint'] == entry['contractFingerprint'],
                'wrong-handoff-contract')
    return request


def check_binding(value: dict, request: dict) -> None:
    require(all(value.get(k) == v for k, v in binding(request).items()), 'checkpoint-identity-mismatch')


def common(request: dict) -> dict:
    import registry
    return {**binding(request), 'testedCommit': request['candidateCommit'], 'source': request['source'],
            'manifestHash': registry.MANIFEST_HASH}


def new_session(request: dict, directory: Path, fd: int | None, *, host: bool) -> BatchSession:
    if host:
        expected = [{'variantId': 'fixture.first', 'layer': 'Q'}, {'variantId': 'fixture.second', 'layer': 'Q'}]
    else:
        import registry
        expected = [{k: e[k] for k in ['variantId', 'layer']} for e in registry.entries(request['profile'])]
    return BatchSession(directory, expected=expected, run_id=request['runId'], profile=request['profile'],
                        candidate=request['candidateCommit'], source=request['source'], lane=request['apiLane'],
                        protocol_fixture=host, inherited_lane_fd=fd,
                        environment={} if host else request['environment'])


def restricted_objects(request: dict, mode: str) -> list[dict]:
    """403 fixture 只有显式 get 权限；读取失败场景连 victim 的 get 也不授予。"""
    require(mode in ['cleanup-denied', 'read-denied'], 'unsupported-restricted-policy')
    ns, name = request['fixtureNamespace'], request['rbacName']
    names = ['kube-system'] + ([request['namespace']] if mode == 'cleanup-denied' else [])
    return [
        {'apiVersion': 'v1', 'kind': 'Namespace', 'metadata': {'name': ns}},
        {'apiVersion': 'v1', 'kind': 'ServiceAccount', 'metadata': {'name': name, 'namespace': ns}},
        {'apiVersion': 'rbac.authorization.k8s.io/v1', 'kind': 'ClusterRole', 'metadata': {'name': name},
         'rules': [{'apiGroups': [''], 'resources': ['namespaces'], 'resourceNames': names, 'verbs': ['get']},
                   {'apiGroups': [''], 'resources': ['nodes'], 'verbs': ['get', 'list']}]},
        {'apiVersion': 'rbac.authorization.k8s.io/v1', 'kind': 'ClusterRoleBinding', 'metadata': {'name': name},
         'roleRef': {'apiGroup': 'rbac.authorization.k8s.io', 'kind': 'ClusterRole', 'name': name},
         'subjects': [{'kind': 'ServiceAccount', 'name': name, 'namespace': ns}]},
    ]


class KubernetesFixture:
    """仅创建协议所需 Namespace/RBAC；所有写入和恢复复用既有 KubeTransport。"""

    def __init__(self, request: dict, processes: ProcessRunner, ledger: object, session: BatchSession,
                 *, directory: Path | None = None, restricted: bool = False) -> None:
        from kube_transport import KubeTransport
        self.request, self.processes, self.ledger, self.session = request, processes, ledger, session
        self.directory = directory or Path(request['output'])
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.namespace = None
        self.restricted = restricted
        self.targets = copy.deepcopy(request['targets'])
        if restricted:
            config = Path(request['output']) / '.private/restricted-config.json'
            require(not config.is_symlink() and stat.S_IMODE(config.stat().st_mode) == 0o600, 'unsafe-restricted-config')
            self.targets[request['target']]['kubeconfig'] = str(config)
            self.targets[request['target']]['context'] = 'reg21-restricted'
        self.kt = KubeTransport(ledger=ledger, execute=self.execute, targets=self.targets,
                                kubectl=request['kubectl'], checkpoint=session.checkpoint)
        self.api_events = []

    def execute(self, argv: list, **kwargs: object) -> subprocess.CompletedProcess:
        """原始 namespace/环境响应另存原 bytes；错误原输出只留私有捕获及实际 hash。"""
        result = self.processes.run(argv, **kwargs)
        from kube_transport import KubeTransport
        event = {'at': stamp(), 'label': kwargs.get('label'), 'returncode': result.returncode,
                 'stdoutSHA256': hashlib.sha256(result.stdout).hexdigest(),
                 'stderrSHA256': hashlib.sha256(result.stderr).hexdigest(),
                 'reason': KubeTransport.error(result) if result.returncode else None,
                 'restrictedIdentity': self.restricted}
        self.api_events.append(event)
        atomic_json(self.directory / 'api-events.json', self.api_events)
        if not result.returncode:
            try:
                value = json.loads(result.stdout)
                if isinstance(value, dict) and value.get('kind') == 'Namespace':
                    name = value.get('metadata', {}).get('name')
                    filename = ('cluster-system-response.json' if name == 'kube-system' else
                                'namespace-response.json' if name == self.request['namespace'] and
                                kwargs.get('label') == 'kube-create' else 'observed-namespace.json')
                    # 原 API JSON 字节不做摘要替换；此 fixture 的 Namespace 没有用户数据或认证字段。
                    destination = self.directory / filename
                    atomic_bytes(destination, result.stdout)
            except (ValueError, UnicodeError):
                pass
        return result

    def verify_source(self) -> None:
        """实际执行的 helper 必须就是干净 C；新 wrapper 无权把旧 runner 洗成 C。"""
        def git(*args: str) -> str:
            result = self.processes.run(['git', '-C', str(ROOT), *args], label='protocol-source')
            require(result.returncode == 0, 'source-read-failed')
            return result.stdout.decode().strip()
        require(git('rev-parse', 'HEAD') == self.request['candidateCommit'] and
                git('rev-parse', 'HEAD^{tree}') == self.request['testedTree'] and
                not git('status', '--porcelain'), 'wrong-actual-source')

    def verify_environment(self) -> dict:
        """每个新 child/recover 在写之前重核目标 UID 与 Linux 节点架构。"""
        target = self.request['target']
        system = self.kt.get_obj(target, {'apiVersion': 'v1', 'kind': 'Namespace', 'name': 'kube-system', 'namespace': ''})
        require(system and system['metadata']['uid'] == self.request['targetUID'], 'wrong-actual-cluster')
        result = self.kt.command(target, ['get', 'nodes', '-o', 'json'])
        require(result.returncode == 0, 'node-read-failed')
        response = json.loads(result.stdout)
        require(isinstance(response, dict) and response.get('apiVersion') == 'v1' and
                response.get('kind') in ['List', 'NodeList'] and isinstance(response.get('items'), list),
                'wrong-actual-nodes')
        nodes = response['items']
        environment = self.request['environment']
        expected = next(t for t in environment['targets'] if t['name'] == target)
        arch = environment['runtimeArch']
        require(len(nodes) == expected['nodeCount'] and all(
            isinstance(n, dict) and n.get('kind') == 'Node' and isinstance(n.get('metadata'), dict) and
            isinstance(n.get('status'), dict) and isinstance(n['status'].get('nodeInfo'), dict) and
            n['metadata'].get('uid') and n['metadata'].get('name') and
            n.get('status', {}).get('nodeInfo', {}).get('operatingSystem') == 'linux' and
            n.get('status', {}).get('nodeInfo', {}).get('architecture') == arch for n in nodes), 'wrong-actual-nodes')
        # 关键：只封存明确 get nodes 的成功原 bytes；不把通用 List 认作节点或改写成 NodeList。
        atomic_bytes(self.directory / 'node-response.json', result.stdout)
        value = {**common(self.request), 'clusterUID': system['metadata']['uid'], 'runtimeOS': 'linux',
                 'runtimeArch': arch, 'nodes': [{'name': n['metadata']['name'], 'uid': n['metadata']['uid'],
                     'os': n['status']['nodeInfo']['operatingSystem'], 'arch': n['status']['nodeInfo']['architecture']}
                     for n in nodes]}
        atomic_json(self.directory / 'environment.json', value)
        return value

    def prepare(self) -> None:
        self.verify_source()
        self.verify_environment()
        self.namespace = self.kt.create_obj(self.request['target'],
            {'apiVersion': 'v1', 'kind': 'Namespace', 'metadata': {'name': self.request['namespace']}})
        if self.request['mode'] in ['cleanup-denied', 'read-denied']:
            self.create_restricted_identity()

    def create_restricted_identity(self) -> None:
        """本 run 实际 SA/TokenRequest；只读取公开 cluster/CA 字段，不读取管理员密钥。"""
        from ledger import Ledger
        from kube_transport import KubeTransport
        root = Path(self.request['output'])
        path = root / 'fixture-owner-ledger.json'
        owner = Ledger(path, run_id=self.request['runId'], candidate=self.request['candidateCommit'],
                       targets={k: v['clusterUID'] for k, v in self.targets.items()})
        owner.case = self.request['variantId'] + ':restricted-owner'
        kt = KubeTransport(ledger=owner, execute=self.execute, targets=self.targets,
                           kubectl=self.request['kubectl'], checkpoint=self.session.checkpoint)
        objects = restricted_objects(self.request, self.request['mode'])
        responses = [kt.create_obj(self.request['target'], obj) for obj in objects]
        sa = next(o for o in responses if o['kind'] == 'ServiceAccount')
        token_result = kt.command(self.request['target'], ['create', 'token', self.request['rbacName'],
            '-n', self.request['fixtureNamespace'], '--duration=15m'])
        require(token_result.returncode == 0 and token_result.stdout.strip(), 'token-request-failed')
        config = self.targets[self.request['target']]
        prefix = [*self.request['kubectl'], '--kubeconfig', config['kubeconfig'], '--context', config['context'],
                  'config', 'view', '--minify', '--flatten', '--raw']
        cluster = {}
        for field in ['server', 'certificate-authority-data', 'insecure-skip-tls-verify']:
            response = self.processes.run([*prefix, '-o', 'jsonpath={.clusters[0].cluster.' + field + '}'],
                                          label='public-cluster-setting')
            require(response.returncode == 0, 'public-cluster-setting-failed')
            value = response.stdout.decode().strip()
            if value:
                cluster[field] = value == 'true' if field == 'insecure-skip-tls-verify' else value
        require(cluster.get('server'), 'missing-public-server')
        restricted = {'apiVersion': 'v1', 'kind': 'Config', 'clusters': [{'name': 'target', 'cluster': cluster}],
                      'users': [{'name': 'fixture', 'user': {'token': token_result.stdout.decode().strip()}}],
                      'contexts': [{'name': 'reg21-restricted', 'context': {'cluster': 'target', 'user': 'fixture'}}],
                      'current-context': 'reg21-restricted'}
        atomic_json(root / '.private/restricted-config.json', restricted)
        expected = 'system:serviceaccount:' + self.request['fixtureNamespace'] + ':' + self.request['rbacName']
        command = [*self.request['kubectl'], '--kubeconfig', str(root / '.private/restricted-config.json'),
                   '--context', 'reg21-restricted', 'auth', 'whoami', '-o', 'json']
        result = self.processes.run(command, label='actual-restricted-identity')
        require(result.returncode == 0, 'restricted-identity-read-failed')
        actual = json.loads(result.stdout).get('status', {}).get('userInfo', {}).get('username')
        require(actual == expected, 'wrong-restricted-identity')
        atomic_json(root / 'restricted-identity.json', {**common(self.request), 'username': actual,
                    'serviceAccountUID': sa['metadata']['uid'], 'mode': self.request['mode'], 'actual': True})
        restricted_targets = copy.deepcopy(self.targets)
        restricted_targets[self.request['target']].update(kubeconfig=str(root / '.private/restricted-config.json'),
                                                         context='reg21-restricted')
        reader = KubeTransport(ledger=owner, execute=self.execute, targets=restricted_targets,
                               kubectl=self.request['kubectl'], checkpoint=self.session.checkpoint)
        # RoleBinding 生效必须由实际权限读确认，不能用固定等待假设已经传播。
        deadline = time.monotonic() + 30
        while True:
            system = reader.command(self.request['target'], ['auth', 'can-i', 'get', 'namespace/kube-system'], check=False)
            victim = reader.command(self.request['target'], ['auth', 'can-i', 'get', 'namespace/' + self.request['namespace']], check=False)
            delete = reader.command(self.request['target'], ['auth', 'can-i', 'delete', 'namespace/' + self.request['namespace']], check=False)
            expected_read = self.request['mode'] == 'cleanup-denied'
            if (system.returncode == 0 and system.stdout.strip() == b'yes' and
                (victim.returncode == 0 and victim.stdout.strip() == b'yes') is expected_read and
                delete.returncode != 0 and delete.stdout.strip() == b'no'):
                break
            require(time.monotonic() < deadline, 'restricted-policy-not-observed')
            time.sleep(.2)
        atomic_json(root / 'restricted-access.json', {**common(self.request), 'serviceAccountUID': sa['metadata']['uid'],
                    'systemReadAllowed': True, 'victimReadAllowed': expected_read, 'victimDeleteAllowed': False})

    def cleanup(self, *, restricted: bool = False) -> dict:
        """只从原 ledger 清理原 UID；403 不允许悄悄切回高权限身份。"""
        if restricted:
            fixture = KubernetesFixture(self.request, self.processes, self.ledger, self.session,
                                        directory=Path(self.request['output']) / 'restricted-cleanup', restricted=True)
            fixture.verify_environment()
            return fixture.kt.recover_case(self.request['variantId'])
        return self.kt.recover_case(self.request['variantId'])


def case_request(ctx: object, entry: dict) -> dict:
    """公开 DI 边界只消费 Session 已确认的信息；不读取默认 context 或全局凭据。"""
    import registry
    require(entry.get('variantId') in MODES and entry.get('layer') == 'K', 'unsupported-protocol-case')
    request = copy.deepcopy(ctx.request)
    for key in ['runId', 'profile', 'candidateCommit', 'testedTree', 'runnerCommit', 'dirty', 'source',
                'targets', 'environment', 'kubectl', 'apiLane', 'output']:
        require(key in request, 'incomplete-protocol-context')
    require(request['profile'] == 'LOCAL_FULL' and request['dirty'] is False and
            all(request.get(k) in [None, False] for k in ['sourceBridge', 'source_bridge', 'bridge']) and
            request['source'] == {'commit': request['candidateCommit'], 'tree': request['testedTree'], 'dirty': False}
            and request['runnerCommit'] == request['candidateCommit'], 'wrong-protocol-context')
    require(isinstance(request['kubectl'], list) and request['kubectl'] and request['apiLane'], 'explicit-tools-required')
    require(isinstance(request['targets'], dict) and set(request['targets']) == {'control', 'data'} and
            all(v.get('clusterUID') and v.get('kubeconfig') and v.get('context') for v in request['targets'].values()),
            'explicit-targets-required')
    environment = request['environment']
    require(environment.get('runtimeOS') == 'darwin' and environment.get('runtimeArch') == 'arm64' and
            {t.get('name'): (t.get('clusterUID'), t.get('nodeCount')) for t in environment.get('targets', [])} ==
            {name: (target['clusterUID'], 3 if name == 'control' else 1) for name, target in request['targets'].items()},
            'wrong-protocol-environment')
    registered = registry.entries('LOCAL_FULL', True, [entry['variantId']])[0]
    require(entry['contractFingerprint'] == registered['contractFingerprint'] and
            registered['executionBoundary'] == 'kubernetes-protocol', 'wrong-protocol-contract')
    suffix = uuid.uuid4().hex[:12]
    request.update(variantId=entry['variantId'], contractFingerprint=entry['contractFingerprint'],
                   target='control', targetUID=request['targets']['control']['clusterUID'],
                   namespace='reg21-victim-' + suffix, fixtureNamespace='reg21-owner-' + suffix,
                   rbacName='reg21-reader-' + suffix, scope='kubernetes-protocol', mode=MODES[entry['variantId']])
    return request


def run_case(ctx: object, entry: dict) -> dict:
    """owner 的主 Session 提供 ProcessRunner 与可选整批 lane fd；此处不创建第二个业务 Suite。"""
    request = case_request(ctx, entry)
    return coordinate(request, ctx.processes, getattr(ctx, 'lane_fd', None), host=False)


def original_ledger(request: dict) -> object:
    """恢复入口必须同时绑定 index、ledger 与私有 request，不能只信调用方传来的名字。"""
    from ledger import Ledger
    root = Path(request['output'])
    index = json.loads((root / 'child/index.json').read_bytes())
    require(index.get('runId') == request['runId'] and index.get('profile') == request['profile'] and
            index.get('candidateCommit') == request['candidateCommit'] and
            index.get('testedCommit') == request['candidateCommit'] and
            index.get('runnerCommit') == request['candidateCommit'] and index.get('testedTree') == request['testedTree'] and
            index.get('dirty') is False, 'original-index-identity-mismatch')
    ledger = Ledger.open(root / 'child/ledger.json')
    require(ledger.data['runId'] == request['runId'] and ledger.data['candidateCommit'] == request['candidateCommit'] and
            ledger.data['targets'] == {k: v['clusterUID'] for k, v in request['targets'].items()},
            'original-ledger-identity-mismatch')
    checkpoint_path = root / 'checkpoint.json'
    checkpoint = json.loads(checkpoint_path.read_bytes()) if checkpoint_path.exists() else None
    if checkpoint is not None:
        check_binding(checkpoint, request)
    matches = [e for e in ledger.entries.values() if e.get('case') == request['variantId'] and
               e['action'] == 'create' and e['identity']['kind'] == 'Namespace' and
               e.get('uid') and (checkpoint is None or e['uid'] == checkpoint.get('namespaceUID')) and
               e['identity']['name'] == request['namespace'] and e['target'] == request['target']]
    require(len(matches) == 1 and matches[0]['clusterUID'] == request['targetUID'], 'original-ledger-uid-mismatch')
    return ledger


def recovery_cli(request: dict, fd: int | None, action: str, attempt: str | None) -> dict:
    """每次恢复都是新进程、新 index；只打开原 ledger，不覆盖原 index 或重跑业务。"""
    from ledger import Ledger
    require(attempt in ['initial-recover', 'owner-recover', 'owner-replace', 'owner-cleanup', 'coordinator-recover'], 'unknown-recovery-attempt')
    require((action == 'recover' and attempt in ['initial-recover', 'owner-recover', 'coordinator-recover']) or action == attempt,
            'recovery-action-mismatch')
    root = Path(request['output'])
    directory = root / attempt
    session = new_session(request, directory, fd, host=False)
    report = {**common(request), 'action': action, 'attempt': attempt, 'success': False,
              'replaysBusiness': False, 'originalIndexSHA256': sha(root / 'child/index.json')}
    error = None
    ledger = None
    try:
        session.start()
        original = original_ledger(request)
        if action == 'owner-replace':
            ledger = Ledger(directory / 'ledger.json', run_id=request['runId'], candidate=request['candidateCommit'],
                            targets=original.data['targets'])
            ledger.case = request['variantId'] + ':replacement-owner'
        elif action == 'owner-cleanup':
            ledger = Ledger.open(root / 'owner-replace/ledger.json')
            require(ledger.data['runId'] == request['runId'] and
                    ledger.data['candidateCommit'] == request['candidateCommit'] and ledger.data['targets'] == original.data['targets'],
                    'replacement-owner-identity-mismatch')
        else:
            ledger = original
        processes = ProcessRunner(directory / '.private', checkpoint=session.checkpoint, event=ledger.process_event)
        restricted = action == 'recover' and attempt == 'initial-recover' and request['mode'] == 'read-denied'
        fixture = KubernetesFixture(request, processes, ledger, session, directory=directory, restricted=restricted)
        fixture.verify_source()
        fixture.verify_environment()
        auxiliary = stop_recorded_auxiliary(original)
        if action == 'owner-replace':
            original_entry = next(e for e in original.entries.values() if e['action'] == 'create' and
                e['identity']['kind'] == 'Namespace' and e['identity']['name'] == request['namespace'])
            token = ledger.intent(request['target'], 'controlled-original-delete', original_entry['identity'],
                                  origin='independent-fixture-owner', uid=original_entry['uid'],
                                  resource_version=original_entry['resourceVersion'])
            fixture.kt.delete_uid(ledger.entries[token])
            replacement = fixture.kt.create_obj(request['target'], {'apiVersion': 'v1', 'kind': 'Namespace',
                                               'metadata': {'name': request['namespace']}})
            require(replacement['metadata']['uid'] != original_entry['uid'], 'replacement-uid-not-independent')
            report.update(success=not auxiliary, originalUID=original_entry['uid'],
                          replacementUID=replacement['metadata']['uid'], cleanup={'success': False, 'state': 'held_for_uid_test'})
        else:
            with session.cleanup_phase():
                cleaned = fixture.kt.recover_case(ledger.case if action == 'owner-cleanup' else request['variantId'])
                report.update(cleanup=cleaned, success=cleaned['success'] and not auxiliary)
                if action == 'recover' and request['mode'] == 'replaced':
                    current = fixture.kt.get_obj(request['target'], {'apiVersion': 'v1', 'kind': 'Namespace',
                                                  'namespace': '', 'name': request['namespace']})
                    report['observedReplacementUID'] = None if current is None else current['metadata']['uid']
                if action == 'recover' and attempt in ['owner-recover', 'coordinator-recover']:
                    owner_path = root / 'fixture-owner-ledger.json'
                    if owner_path.exists():
                        owner = Ledger.open(owner_path)
                        require(owner.data['runId'] == request['runId'] and
                                owner.data['candidateCommit'] == request['candidateCommit'] and
                                owner.data['targets'] == original.data['targets'], 'restricted-owner-identity-mismatch')
                        owner_fixture = KubernetesFixture(request, processes, owner, session, directory=directory / 'fixture-owner')
                        owner_cleaned = owner_fixture.kt.recover_case(request['variantId'] + ':restricted-owner')
                        report['fixtureOwnerCleanup'] = owner_cleaned
                        report['success'] = report['success'] and owner_cleaned['success']
        report['auxiliaryFailures'] = auxiliary
    except BaseException as failure:
        error = failure
        report.update(success=False, errorCode=str(failure) if isinstance(failure, ProtocolError) else getattr(failure, 'code', 'recovery-failed'))
    finally:
        # 关键：原 index bytes 在恢复前后必须相同，成功恢复只能新增独立记录。
        report['originalIndexUnchanged'] = report['originalIndexSHA256'] == sha(root / 'child/index.json')
        report['success'] = report['success'] and report['originalIndexUnchanged']
        try:
            if ledger is not None:
                atomic_json(directory / 'ledger-snapshot.json', ledger.data)
            for filename, key in [('environment.json', 'environmentRef'), ('cluster-system-response.json', 'clusterSystemResponseRef'),
                                  ('node-response.json', 'nodeResponseRef'), ('ledger-snapshot.json', 'ledgerRef'),
                                  ('api-events.json', 'apiEventsRef'), ('observed-namespace.json', 'observedNamespaceRef'),
                                  ('namespace-response.json', 'namespaceResponseRef')]:
                if (directory / filename).exists():
                    report[key] = reference(directory / filename, root)
            atomic_json(directory / 'report.json', report)
        finally:
            # 新恢复 index 和全部独立原件写完才释放自己的锁；borrowed 租约仍只 close 自己的 dup。
            session.close(error, report.get('cleanup', {'success': False, 'state': 'recovery_failed'}))
    return {'success': report['success'], 'report': reference(directory / 'report.json', root)}


def finish_kubernetes(request: dict, processes: ProcessRunner, fd: int | None, path: Path, digest: str) -> dict:
    """协议预期失败与最终资源回收分开判定，恢复成功不能更改 child 的非绿原件。"""
    root = Path(request['output'])
    mode = request['mode']
    reports = []

    def recover(action: str, attempt: str) -> dict:
        command, options = child_command(request, path, digest, fd, host=False, action=action, attempt=attempt)
        result = processes.run(command, label='protocol-' + attempt, timeout=1500, **options)
        report_path = root / attempt / 'report.json'
        require(report_path.exists(), 'missing-recovery-report')
        report = json.loads(report_path.read_bytes())
        check_binding(report, request)
        require((result.returncode == 0) is report['success'], 'recovery-exit-mismatch')
        reports.append(reference(report_path, root))
        return report

    if mode == 'kill':
        recover('recover', 'owner-recover')
    elif mode == 'cleanup-denied':
        recover('recover', 'owner-recover')
    elif mode == 'read-denied':
        try:
            recover('recover', 'initial-recover')
        finally:
            recover('recover', 'owner-recover')
    elif mode == 'replaced':
        replacement_created = False
        try:
            owner = recover('owner-replace', 'owner-replace')
            replacement_created = owner['success']
            require(replacement_created, 'replacement-fixture-failed')
            recover('recover', 'initial-recover')
        finally:
            if (root / 'owner-replace/ledger.json').exists():
                recover('owner-cleanup', 'owner-cleanup')
    checkpoint = json.loads((root / 'checkpoint.json').read_bytes())
    proof = {**common(request), 'schemaVersion': 1, 'executionBoundary': 'kubernetes-protocol',
             'actualKubernetes': True, 'mode': mode, 'namespaceUID': checkpoint['namespaceUID'],
             'namespaceResponseRef': reference(root / 'namespace-response.json', root),
             'checkpointRef': reference(root / 'checkpoint.json', root),
             'creationLedgerRef': reference(root / 'creation-ledger.json', root),
             'childIndexRef': reference(root / 'child/index.json', root),
             'childProcessRef': reference(root / 'child-process.json', root),
             'environmentRef': reference(root / 'environment.json', root),
             'clusterSystemResponseRef': reference(root / 'cluster-system-response.json', root),
             'nodeResponseRef': reference(root / 'node-response.json', root), 'recoveryRefs': reports,
             'childLedgerRef': reference(root / 'child/ledger.json', root)}
    for filename, key in [('kill-observation.json', 'killObservationRef'), ('killed-index.json', 'killedIndexRef'),
                          ('restricted-identity.json', 'restrictedIdentityRef'), ('restricted-access.json', 'restrictedAccessRef'),
                          ('fixture-owner-ledger.json', 'restrictedOwnerLedgerRef'), ('cleanup-waiting.json', 'cleanupBarrierRef')]:
        if (root / filename).exists():
            proof[key] = reference(root / filename, root)
    for report in reports:
        directory = root / Path(report['path']).parent
        for filename in ['environment.json', 'cluster-system-response.json', 'node-response.json', 'ledger-snapshot.json',
                         'namespace-response.json', 'observed-namespace.json', 'api-events.json']:
            if (directory / filename).exists():
                proof.setdefault('recoveryEvidenceRefs', []).append(reference(directory / filename, root))
    if (root / 'restricted-cleanup/api-events.json').exists():
        proof['restrictedCleanupEventsRef'] = reference(root / 'restricted-cleanup/api-events.json', root)
    if mode == 'read-denied' and (root / 'initial-recover/api-events.json').exists():
        proof['restrictedReadEventsRef'] = reference(root / 'initial-recover/api-events.json', root)
    proof_path = root / 'protocol-proof.json'
    atomic_json(proof_path, proof)
    import gate
    import registry
    entry = registry.entries(request['profile'], True, [request['variantId']])[0]
    reader = gate.Evidence(root)
    gate.kubernetes_protocol(proof, entry, reader, {v['clusterUID'] for v in request['targets'].values()},
                             request['environment']['runtimeArch'], request['candidateCommit'], request['testedTree'])
    receipt = {**common(request), 'variantId': request['variantId'], 'layer': 'K', 'profile': request['profile'],
               'purpose': entry['purpose'], 'steps': entry['steps'], 'expected': entry['expected'],
               'contractFingerprint': entry['contractFingerprint'], 'outcome': 'passed',
               'cleanup': {'success': True, 'scope': 'protocol-fixture-final-owner-cleanup'},
               'executionBoundary': 'kubernetes-protocol', 'runtimeOS': 'linux',
               'runtimeArch': request['environment']['runtimeArch'], 'testSelectors': entry['testSelectors'],
               'kubernetesProtocolRef': reference(proof_path, root), 'evidenceRefs': [reference(proof_path, root)]}
    atomic_json(root / 'receipt.json', receipt)
    return receipt


def stop_recorded_auxiliary(ledger: object) -> list:
    """恢复仅终止本协议启动且出生身份仍吻合的辅助进程组，不从 inventory 或名字认领。"""
    failures = []
    for item in list(ledger.data['processes'].values()):
        if item.get('label') != 'protocol-owned-auxiliary' or item.get('stage') != 'started':
            continue
        pid, birth = item.get('pid'), item.get('birth')
        try:
            actual = process_identity(pid)
            if actual is not None:
                require(actual == birth and os.getpgid(pid) == pid, 'auxiliary-identity-mismatch')
                members = group_members(pid)
                os.killpg(pid, signal.SIGTERM)
                end = time.monotonic() + 5
                while group_members(pid) and time.monotonic() < end:
                    time.sleep(.02)
                remaining = group_members(pid)
                if remaining:
                    require(any(remaining.get(p) == b for p, b in members.items()), 'auxiliary-group-replaced')
                    os.killpg(pid, signal.SIGKILL)
                    end = time.monotonic() + 5
                    while group_members(pid) and time.monotonic() < end:
                        time.sleep(.02)
                require(not group_members(pid), 'auxiliary-still-running')
            ledger.process_event({**item, 'stage': 'recovered-exit', 'gone': True, 'at': stamp()})
        except Exception:
            failures.append({'id': item['id'], 'errorCode': 'auxiliary-exit-unconfirmed'})
    return failures


def child_lifecycle(request: dict, fd: int | None, *, host: bool) -> int:
    """同一 Session 的 try/finally 覆盖部分 setup、当前键失败和一次性正常清理。"""
    root = Path(request['output'])
    session = new_session(request, root / 'child', fd, host=host)
    from ledger import Ledger
    ledger = Ledger(session.out / 'ledger.json', run_id=request['runId'], candidate=request['candidateCommit'],
                    targets={} if host else {k: v['clusterUID'] for k, v in request['targets'].items()})
    ledger.case = request['variantId']
    processes = ProcessRunner(session.out / '.private', checkpoint=session.checkpoint, event=ledger.process_event)
    resources = None
    error = None
    cleanup = {'success': False, 'state': 'not_started'}
    try:
        session.start()
        if not host:
            resources = KubernetesFixture(request, processes, ledger, session)
            resources.prepare()
        processes.popen([sys.executable, '-c', 'import time; time.sleep(600)'], label='protocol-owned-auxiliary')
        session.index['stage'] = 'namespace_checkpoint' if not host else 'host_checkpoint'
        session.flush()
        checkpoint = {**binding(request), 'stage': session.index['stage'],
                      'namespaceUID': None if host else resources.namespace['metadata']['uid'],
                      'resourceVersion': None if host else resources.namespace['metadata']['resourceVersion'],
                      'actualKubernetes': not host, 'at': stamp()}
        atomic_json(root / 'creation-ledger.json', ledger.data)
        # 关键：最后才发布可供父信号/KILL的屏障，避免原响应/创建快照尚未持久就被杀死。
        atomic_json(root / 'checkpoint.json', checkpoint)
        if request['mode'] == 'setup':
            session.result(session.index['results'][0]['variantId'], session.index['results'][0]['layer'],
                           outcome='environment_failed')
            raise RuntimeError('controlled-setup-failure')
        if request['mode'] in ['cleanup-denied', 'repeat']:
            raise AssertionError('controlled-original-assertion-failure')
        if request['mode'] in ['replaced', 'read-denied']:
            wait_file(root / 'release.json', session=session)
            raise AssertionError('controlled-before-independent-recovery')
        while True:
            session.checkpoint()
            time.sleep(.02)
    except BaseException as failure:
        error = failure
        first = session.index['results'][0]
        if first['outcome'] == 'not_run':
            session.result(first['variantId'], first['layer'], outcome='failed')
    finally:
        with session.cleanup_phase():
            session.index['stage'] = 'cleanup'
            session.flush()
            if request['mode'] == 'repeat' and error is not None:
                atomic_json(root / 'cleanup-waiting.json', {**binding(request), 'stage': 'cleanup_waiting'})
                try:
                    wait_file(root / 'release.json', timeout=30, session=session)
                except Exception:
                    cleanup = {'success': False, 'state': 'cleanup_barrier_failed'}
            auxiliary = processes.close_auxiliary()
            if request['mode'] in ['replaced', 'read-denied'] and resources is not None:
                cleanup = {'success': False, 'state': 'deferred_to_independent_recovery'}
            elif resources is not None:
                try:
                    cleanup = resources.cleanup(restricted=request['mode'] == 'cleanup-denied')
                except BaseException as failure:
                    cleanup = {'success': False, 'state': 'cleanup_failed',
                               'failures': [{'errorCode': getattr(failure, 'code', 'cleanup_failed')}]}
            elif host:
                cleanup = {'success': True, 'scope': 'host-processes-only', 'actualKubernetes': False}
            cleanup['auxiliaryFailures'] = auxiliary
            cleanup['success'] = cleanup['success'] and not auxiliary
            session.close(error, cleanup)
    return 1


def child_command(request: dict, path: Path, digest: str, fd: int | None, *, host: bool,
                  action: str = 'child', attempt: str | None = None) -> tuple[list, dict]:
    """只传已有 lane 的一个 fd；凭据只通过 0600 handoff 引用，不进入 argv。"""
    command = [sys.executable, str(HERE), 'host-protocol-child' if host else action,
               '--request', str(path), '--request-sha256', digest]
    if attempt:
        command += ['--attempt', attempt]
    options = {}
    if fd is not None:
        command += ['--inherited-lane-fd', str(fd)]
        options['pass_fds'] = (fd,)
    return command, options


def coordinate(request: dict, processes: ProcessRunner, fd: int | None, *, host: bool) -> dict:
    """父协调器不另取 flock；等 child/recover 完全退出后才返回主批次 owner。"""
    root = Path(request['output']).resolve()
    require(not root.exists(), 'case-output-exists')
    root.mkdir(parents=True, mode=0o700)
    request['output'] = str(root)
    private = root / '.private'
    private.mkdir(mode=0o700)
    path = private / 'request.json'
    digest = private_request(path, request)
    command, options = child_command(request, path, digest, fd, host=host)
    child = processes.popen(command, label='protocol-case-child', **options)
    birth = process_identity(child.pid)
    try:
        checkpoint = wait_file(root / 'checkpoint.json', child, timeout=90 if not host else 10)
        check_binding(checkpoint, request)
        require(checkpoint.get('actualKubernetes') is (not host), 'checkpoint-scope')
        if not host:
            require(bool(checkpoint.get('namespaceUID')) and bool(checkpoint.get('resourceVersion')), 'missing-original-uid')
        mode = request['mode']
        sent = []
        if mode in ['term', 'int', 'kill']:
            sig = {'term': signal.SIGTERM, 'int': signal.SIGINT, 'kill': signal.SIGKILL}[mode]
            require(child.poll() is None and process_identity(child.pid) == birth, 'child-identity-mismatch')
            os.kill(child.pid, sig)
            sent.append(signal.Signals(sig).name)
        elif mode == 'repeat':
            waiting = wait_file(root / 'cleanup-waiting.json', child)
            check_binding(waiting, request)
            for sig in [signal.SIGTERM, signal.SIGINT]:
                os.kill(child.pid, sig)
                sent.append(signal.Signals(sig).name)
                deadline = time.monotonic() + 5
                while signal.Signals(sig).name not in [s['name'] for s in
                        json.loads((root / 'child/index.json').read_bytes())['signals']]:
                    require(child.poll() is None and time.monotonic() < deadline, 'cleanup-signal-not-recorded')
                    time.sleep(.02)
            atomic_json(root / 'release.json', {**binding(request), 'stage': 'release_cleanup'})
        elif mode in ['replaced', 'read-denied']:
            atomic_json(root / 'release.json', {**binding(request), 'stage': 'release_to_recovery'})
        child.wait(timeout=720 if not host else 12)
        atomic_json(root / 'child-process.json', {**binding(request), 'pid': child.pid, 'birth': birth,
                    'returncode': child.returncode, 'signalsSent': sent, 'exitedAt': stamp()})
        require(child.returncode != 0, 'protocol-child-must-not-pass')
        if mode == 'kill':
            require(child.returncode == -signal.SIGKILL, 'kill-not-observed')
            original = root / 'child' / 'index.json'
            snapshot = root / 'killed-index.json'
            atomic_bytes(snapshot, original.read_bytes())
            atomic_json(root / 'kill-observation.json', {**binding(request), 'state': 'interrupted',
                'returncode': child.returncode, 'finallyExecuted': False, 'cleanup': {'success': False, 'state': 'unknown'},
                'originalIndexSHA256': sha(snapshot), 'pid': child.pid, 'birth': birth})
            if host:
                from ledger import Ledger
                original_ledger = Ledger.open(root / 'child' / 'ledger.json')
                require(not stop_recorded_auxiliary(original_ledger), 'host-orphan-cleanup-failed')
        if host:
            value = json.loads((root / 'child' / 'index.json').read_bytes())
            summary = {'executionBoundary': 'validator-test', 'actualKubernetes': False, 'productGate': False,
                       'childIndex': value, 'childProcess': json.loads((root / 'child-process.json').read_bytes()),
                       'kubernetesUID': None}
            atomic_json(root / 'summary.json', summary)
            return {k: summary[k] for k in ['executionBoundary', 'actualKubernetes', 'productGate']}
        return finish_kubernetes(request, processes, fd, path, digest)
    finally:
        # 关键：协调器异常也先结束自己启动的 child，不能释放父 lane 后让旧 child 继续 API 动作。
        if child.poll() is None:
            os.kill(child.pid, signal.SIGTERM)
            try:
                child.wait(timeout=720 if not host else 10)
            except subprocess.TimeoutExpired:
                os.kill(child.pid, signal.SIGKILL)
                child.wait(timeout=10)
        if host:
            from ledger import Ledger
            ledger_path = root / 'child/ledger.json'
            if ledger_path.exists():
                require(not stop_recorded_auxiliary(Ledger.open(ledger_path)), 'host-orphan-cleanup-failed')
        elif not (root / 'receipt.json').exists() and (root / 'child/ledger.json').exists():
            # 关键：即使父协调失败，也由新 Session 尝试原 UID 清理，且不把此安全退出记为 K 通过。
            # 父业务 checkpoint 可能正处于停止状态；清理专用 ProcessRunner 不再启动任何业务动作。
            cleanup_runner = ProcessRunner(root / '.private/coordinator-cleanup', event=processes.event)
            command, options = child_command(request, path, digest, fd, host=False,
                                             action='recover', attempt='coordinator-recover')
            try:
                cleanup_result = cleanup_runner.run(command, label='protocol-coordinator-cleanup', timeout=1500, **options)
                atomic_json(root / 'coordinator-cleanup-observation.json', {**binding(request),
                            'success': cleanup_result.returncode == 0, 'returncode': cleanup_result.returncode,
                            'acceptancePassed': False})
            except Exception:
                atomic_json(root / 'coordinator-cleanup-observation.json', {**binding(request),
                            'success': False, 'errorCode': 'coordinator-cleanup-unconfirmed', 'acceptancePassed': False})


def host_scenario(output: str, mode: str, lane: str, fd: int | None = None) -> dict:
    """资源为空的真实 OS 协议试验，永不调用 KubernetesFixture 或生成 K 收据。"""
    request = {'runId': 'host-' + uuid.uuid4().hex, 'profile': 'validator-test',
               'candidateCommit': 'host-fixture', 'runnerCommit': 'host-fixture', 'testedTree': 'host-fixture',
               'dirty': False, 'source': {'commit': 'host-fixture', 'tree': 'host-fixture', 'dirty': False},
               'variantId': 'host.protocol', 'targetUID': None, 'output': output, 'apiLane': lane,
               'mode': mode, 'scope': 'validator-test'}
    runner = ProcessRunner(Path(output).parent / ('.host-captures-' + uuid.uuid4().hex))
    return coordinate(request, runner, fd, host=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['host-scenario', 'host-protocol-child', 'child', 'recover', 'owner-replace', 'owner-cleanup'])
    parser.add_argument('--request')
    parser.add_argument('--request-sha256')
    parser.add_argument('--output')
    parser.add_argument('--mode', choices=HOST_MODES)
    parser.add_argument('--api-lane')
    parser.add_argument('--inherited-lane-fd', type=int)
    parser.add_argument('--attempt')
    args = parser.parse_args()
    try:
        if args.command == 'host-scenario':
            value = host_scenario(args.output, args.mode, args.api_lane, args.inherited_lane_fd)
        else:
            host = args.command == 'host-protocol-child'
            request = load_request(args.request, args.request_sha256, host=host)
            if host or args.command == 'child':
                return child_lifecycle(request, args.inherited_lane_fd, host=host)
            value = recovery_cli(request, args.inherited_lane_fd, args.command, args.attempt)
        print(json.dumps(value, ensure_ascii=False))
        return 0 if value.get('success', True) else 1
    except Exception as error:
        print(json.dumps({'success': False, 'code': str(error) if isinstance(error, ProtocolError) else 'protocol-failed'}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
