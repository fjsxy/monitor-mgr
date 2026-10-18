#!/usr/bin/env python3
"""固定来源的环境准备；所有外部动作归调用者唯一 BatchSession 和持久 ledger。"""
import copy
import hashlib
import json
import os
import pathlib
import platform
import re
import time
import uuid

from image_provenance import ImageProofError, read_docker_archive

ROOT = pathlib.Path(__file__).resolve().parents[2]
LOCK = json.loads((pathlib.Path(__file__).with_name('source-lock.json')).read_text())
RUN_LABEL = 'monitor-mgr.test/run'
NAMESPACE = 'mm-phase1-operators'


class PrepareError(RuntimeError):
    """固定诊断码不携带子进程输出、凭据或私有源码内容。"""


def _bytes(value):
    return value if isinstance(value, bytes) else str(value or '').encode()


def _json(result):
    try:
        return json.loads(result.stdout)
    except (ValueError, TypeError):
        raise PrepareError('PREPARE_RESPONSE_INVALID') from None


def _check(result, label):
    if result.returncode:
        raise PrepareError(label + '_FAILED')
    return result


def _private_json(path, value):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'w') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write('\n')


def _request(request):
    result = copy.deepcopy(request)
    profile = result.get('profile')
    if profile not in ('LOCAL_FULL', 'CI_SMOKE', 'CI_FAST'):
        raise PrepareError('PREPARE_PROFILE_INVALID')
    candidate = result.get('candidate', result.get('source', ''))
    if not re.fullmatch(r'[0-9a-f]{40}', candidate):
        raise PrepareError('PREPARE_SOURCE_INVALID')
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,53}', result.get('runId', '')):
        raise PrepareError('PREPARE_RUN_ID_INVALID')
    private = pathlib.Path(result.get('privateRoot', ''))
    if not private.is_absolute() or private.resolve().is_relative_to(ROOT):
        raise PrepareError('PREPARE_PRIVATE_ROOT_INVALID')
    targets = result.get('targets', {})
    expected = {'control', 'data'} if profile == 'LOCAL_FULL' else {'control'} if profile == 'CI_SMOKE' else set()
    if set(targets) != expected:
        raise PrepareError('PREPARE_TARGETS_INVALID')
    names, configs = set(), set()
    for target in targets.values():
        name = target.get('name', '')
        path = pathlib.Path(target.get('kubeconfig', ''))
        if (not re.fullmatch(r'mm[a-z0-9-]{1,51}', name) or name in names or
                not path.is_absolute() or not path.resolve().is_relative_to(private.resolve()) or
                str(path.resolve()) in configs or target.get('context') != 'kind-' + name):
            raise PrepareError('PREPARE_TARGET_IDENTITY_INVALID')
        if path.exists():
            raise PrepareError('PREPARE_KUBECONFIG_EXISTS')
        names.add(name)
        configs.add(str(path.resolve()))
    result.update(schemaVersion=1, source=candidate, candidate=candidate,
                  kubectl=result.get('kubectl', 'kubectl'), kind=result.get('kind', 'kind'),
                  platform=result.get('platform', 'linux/' + ('arm64' if platform.machine() in ('arm64', 'aarch64') else 'amd64')),
                  privateRoot=str(private), sourceLock=copy.deepcopy(LOCK))
    if profile == 'CI_SMOKE' and result['platform'] != 'linux/amd64':
        raise PrepareError('PREPARE_SMOKE_REQUIRES_LINUX_AMD64')
    return result


def operator_bundle_objects(text):
    """固定官方bundle转对象；保留SafeLoader的标准类型及安全边界。"""
    import yaml
    class OperatorBundleSafeLoader(yaml.SafeLoader):
        pass
    # 关键：YAML1.1将裸'='标为标准value；只在局部SafeLoader补字符串构造，不放开Python标签或改原bundle。
    OperatorBundleSafeLoader.add_constructor('tag:yaml.org,2002:value', yaml.SafeLoader.construct_scalar)
    return list(yaml.load_all(text, Loader=OperatorBundleSafeLoader))


class _Preparation:
    """只组合公开命令和 checkpoint；不管理 lane 或自行启动子进程。"""
    def __init__(self, request, execute, ledger):
        self.d, self.execute, self.ledger = request, execute, ledger
        self.private = pathlib.Path(request['privateRoot'])
        self.sequence = 0

    def command(self, argv, label, **kwargs):
        self.sequence += 1
        return _check(self.execute([str(x) for x in argv], label=label, **kwargs), label)

    def mutate(self, argv, label, identity, **kwargs):
        # 关键：调用者必须先 fsync intent；ledger 异常时绝不发出变更。
        self.ledger({'event': 'prepare_intent', 'runId': self.d['runId'], 'action': label, 'identity': identity})
        return self.command(argv, label, **kwargs)

    def k(self, role, *args, **kwargs):
        target = self.d['targets'][role]
        return self.command([self.d['kubectl'], '--kubeconfig', target['kubeconfig'],
                             '--context', target['context'], *args], 'prepare-' + role + '-' + args[0], **kwargs)

    def apply(self, role, obj):
        metadata = obj.setdefault('metadata', {})
        metadata.setdefault('labels', {})[RUN_LABEL] = self.d['runId']
        identity = {'target': role, 'apiVersion': obj['apiVersion'], 'kind': obj['kind'],
                    'name': metadata['name'], 'namespace': metadata.get('namespace', '')}
        self.ledger({'event': 'prepare_intent', 'runId': self.d['runId'], 'action': 'apply', 'identity': identity})
        applied = _json(self.k(role, 'apply', '--server-side', '--field-manager=monitor-mgr-acceptance', '-f', '-', '-o', 'json',
                               input=json.dumps(obj).encode(), timeout=300))
        uid = applied.get('metadata', {}).get('uid')
        if not uid:
            raise PrepareError('PREPARE_APPLIED_UID_MISSING')
        observed = dict(identity, uid=uid, resourceVersion=applied['metadata'].get('resourceVersion'))
        self.ledger({'event': 'prepare_observed', 'runId': self.d['runId'], 'identity': observed})
        self.d['targets'][role].setdefault('baseline', []).append(observed)
        return applied

    def make_data_node_schedulable(self, node_name):
        """单节点kind可原本无taint；只条件移除本轮控制面taint并验证真实调度状态。"""
        role='data';target=self.d['targets'][role]
        def read_node(value=None):
            node=_json(self.k(role,'get','node',node_name,'-o','json')) if value is None else value
            if not isinstance(node,dict):raise PrepareError('PREPARE_DATA_NODE_RESPONSE_INVALID')
            metadata=node.get('metadata',{});spec=node.get('spec',{});status=node.get('status',{})
            if (node.get('kind')!='Node' or not isinstance(metadata,dict) or metadata.get('name')!=node_name or
                    any(not isinstance(metadata.get(key),str) or not metadata[key] for key in ['uid','resourceVersion']) or
                    not isinstance(spec,dict) or not isinstance(status,dict)):
                raise PrepareError('PREPARE_DATA_NODE_RESPONSE_INVALID')
            taints=spec.get('taints',[])
            if not isinstance(taints,list) or any(not isinstance(t,dict) or not isinstance(t.get('key'),str) or
                    not t['key'] or t.get('effect') not in ['NoSchedule','PreferNoSchedule','NoExecute'] for t in taints):
                raise PrepareError('PREPARE_DATA_NODE_RESPONSE_INVALID')
            return node,metadata,spec,status,taints
        _,metadata,_,_,taints=read_node();uid=metadata['uid'];revision=metadata['resourceVersion']
        indices=[n for n,t in enumerate(taints) if t['key']=='node-role.kubernetes.io/control-plane']
        if indices:
            # 关键：原UID/RV是API原子前置条件；只删准确taint索引，不覆盖其他字段或误写替代Node。
            patch=[{'op':'test','path':'/metadata/uid','value':uid},
                   {'op':'test','path':'/metadata/resourceVersion','value':revision},
                   *[{'op':'remove','path':'/spec/taints/'+str(n)} for n in reversed(indices)]]
            changed=_json(self.mutate([self.d['kubectl'],'--kubeconfig',target['kubeconfig'],'--context',target['context'],
                                      'patch','node',node_name,'--type=json','-p',json.dumps(patch),'-o','json'],
                                     'make-data-node-schedulable',{'target':role,'clusterUID':target['clusterUID'],
                                      'node':node_name,'nodeUID':uid,'resourceVersion':revision}))
            _,confirmed,_,_,_=read_node(changed)
            if confirmed['uid']!=uid or confirmed['resourceVersion']==revision:
                raise PrepareError('PREPARE_DATA_NODE_IDENTITY_CHANGED')
            self.ledger({'event':'prepare_data_node_taints_removed','runId':self.d['runId'],'target':role,
                         'nodeUID':uid,'resourceVersion':confirmed['resourceVersion']})
        _,current,spec,status,taints=read_node()
        if current['uid']!=uid:raise PrepareError('PREPARE_DATA_NODE_IDENTITY_CHANGED')
        conditions=status.get('conditions',[])
        if not isinstance(conditions,list) or any(not isinstance(c,dict) for c in conditions):
            raise PrepareError('PREPARE_DATA_NODE_RESPONSE_INVALID')
        ready=[c.get('status') for c in conditions if c.get('type')=='Ready']
        if (ready!=['True'] or spec.get('unschedulable',False) is not False or
                any(t['effect'] in ['NoSchedule','NoExecute'] for t in taints)):
            raise PrepareError('PREPARE_DATA_NODE_NOT_SCHEDULABLE')
        self.ledger({'event':'prepare_data_node_schedulable','runId':self.d['runId'],'target':role,
                     'nodeUID':uid,'resourceVersion':current['resourceVersion'],'ready':True,'unschedulable':False,
                     'blockingTaintCount':0})

    def cluster(self, role):
        target = self.d['targets'][role]
        config = {'kind': 'Cluster', 'apiVersion': 'kind.x-k8s.io/v1alpha4', 'name': target['name'],
                  'featureGates': {'StatefulSetAutoDeletePVC': True},
                  'nodes': [{'role': 'control-plane'}] + ([{'role': 'worker'}, {'role': 'worker'}] if role == 'control' else [])}
        path = self.private / (role + '-kind.json')
        _private_json(path, config)
        target['kindConfig'] = str(path)
        try:
            self.mutate([self.d['kind'], 'create', 'cluster', '--name', target['name'], '--config', str(path),
                         '--image', LOCK['images']['kind_node']['image'], '--kubeconfig', target['kubeconfig'], '--wait', '180s', '--retain'],
                        'create-cluster', {'target': role, 'name': target['name'], 'absentBefore': True}, timeout=600)
        finally:
            # create 失败也尽量保存已创建容器身份；未知身份必须留待独立恢复，不能按名字猜删。
            observed = self.execute(['docker', 'ps', '-a', '--no-trunc', '--filter',
                                     'label=io.x-k8s.kind.cluster=' + target['name'], '--format', '{{json .}}'], label='prepare-container-checkpoint')
            if observed.returncode == 0:
                try:
                    containers = [json.loads(line) for line in _bytes(observed.stdout).splitlines() if line]
                    target['containers'] = [{'id': item['ID'], 'name': item['Names']} for item in containers]
                    self.ledger({'event': 'prepare_cluster_observed', 'runId': self.d['runId'], 'target': role,
                                 'name': target['name'], 'containers': target['containers']})
                except (ValueError, KeyError):
                    raise PrepareError('PREPARE_CONTAINER_CHECKPOINT_INVALID') from None
        if not target.get('containers'):
            raise PrepareError('PREPARE_CONTAINER_ID_MISSING')
        os.chmod(target['kubeconfig'], 0o600)
        namespace = _json(self.k(role, 'get', 'namespace', 'kube-system', '-o', 'json'))
        target['clusterUID'] = namespace['metadata']['uid']
        self.ledger({'event': 'prepare_cluster_uid', 'runId': self.d['runId'], 'target': role,
                     'name': target['name'], 'uid': target['clusterUID'], 'containers': target['containers']})
        config = _json(self.k(role, 'config', 'view', '--minify', '--flatten', '--raw', '-o', 'json'))
        node_name = target['name'] + '-control-plane'
        node = _json(self.command(['docker', 'inspect', node_name], 'inspect-control-plane-address'))[0]
        network = node.get('NetworkSettings', {}).get('Networks', {}).get('kind', {})
        address = network.get('IPAddress') or network.get('GlobalIPv6Address')
        if not address:
            raise PrepareError('PREPARE_POD_API_ADDRESS_MISSING')
        target['apiAddress'] = address
        config['clusters'][0]['cluster']['server'] = 'https://' + ('[' + address + ']' if ':' in address else address) + ':6443'
        pod_config = self.private / (role + '-pod.kubeconfig.json')
        _private_json(pod_config, config)
        target['podConfig'] = str(pod_config)
        self.k(role, 'wait', '--for=condition=Ready', 'nodes', '--all', '--timeout=180s', timeout=200)
        if role == 'data':
            self.make_data_node_schedulable(node_name)
        for container in target['containers']:
            if container['name'].endswith('-control-plane'):
                gate = self.command(['docker', 'exec', container['name'], 'cat', '/etc/kubernetes/manifests/kube-controller-manager.yaml'], 'verify-pvc-gate')
                if b'StatefulSetAutoDeletePVC=true' not in _bytes(gate.stdout):
                    raise PrepareError('PREPARE_PVC_GATE_NOT_ENABLED')

    def build(self):
        source = pathlib.Path(self.d.get('monitorOpSource', ''))
        if not source.is_absolute():
            raise PrepareError('PREPARE_PRIVATE_SOURCE_REQUIRED')
        actual = _bytes(self.command(['git', '-C', source, 'rev-parse', 'HEAD'], 'verify-monitor-op-source').stdout).decode().strip()
        dirty = self.command(['git', '-C', source, 'status', '--porcelain'], 'verify-monitor-op-clean').stdout
        if actual != LOCK['monitorOp']['source'] or _bytes(dirty).strip():
            raise PrepareError('PREPARE_PRIVATE_SOURCE_MISMATCH')
        self.d['monitorOpCommit'] = actual
        self.d.setdefault('image', 'monitor-mgr:phase1-' + self.d['source'][:12])
        fixtures = self.d.setdefault('fixtureImages', {})
        for name in ('rpc', 'lifecycle', 'environment'):
            fixtures.setdefault(name, 'monitor-mgr:phase1-' + name + '-' + self.d['source'][:12])
        self.d.setdefault('monitorOpImage', 'monitor-mgr-private/monitor-op:' + actual[:12])
        if self.d.get('buildImages', True):
            actual_source = _bytes(self.command(['git', '-C', ROOT, 'rev-parse', 'HEAD'], 'verify-candidate').stdout).decode().strip()
            dirty_source = self.command(['git', '-C', ROOT, 'status', '--porcelain'], 'verify-candidate-clean').stdout
            if actual_source != self.d['source'] or _bytes(dirty_source).strip():
                raise PrepareError('PREPARE_CANDIDATE_DIRTY_OR_WRONG')
            common = ['docker', 'build', '--platform', self.d['platform'], '--provenance=false', '--sbom=false']
            self.mutate([*common, '--build-arg', 'SOURCE_SHA=' + self.d['source'], '-t', self.d['image'], str(ROOT)],
                        'build-platform', {'source': self.d['source']}, timeout=900)
            for name in ('rpc', 'lifecycle', 'environment'):
                self.mutate([*common, '--build-arg', 'SOURCE_SHA=' + self.d['source'], '--build-arg', 'PLATFORM_IMAGE=' + self.d['image'],
                             '-f', str(ROOT / ('scripts/acceptance/Dockerfile.' + ('rpcprobe' if name == 'rpc' else name))),
                             '-t', fixtures[name], str(ROOT)], 'build-' + name, {'source': self.d['source']}, timeout=900)
            dockerfile = (source / 'Dockerfile').read_bytes()
            if hashlib.sha256(dockerfile).hexdigest() != LOCK['monitorOp']['dockerfileSHA256']:
                raise PrepareError('PREPARE_PRIVATE_DOCKERFILE_CHANGED')
            # 关键：仅在私有临时路径替换固定来源的两个 FROM；不发布私有源码或 Docker layer。
            patched = dockerfile.decode().replace('FROM golang:1.22.12-alpine AS builder', 'FROM ' + LOCK['images']['monitorop_go']['image'] + ' AS builder')
            patched = patched.replace('FROM gcr.io/distroless/static:nonroot', 'FROM ' + LOCK['images']['monitorop_runtime']['image'])
            patched += '\nLABEL org.opencontainers.image.revision="' + actual + '"\n'
            private_dockerfile = self.private / 'monitor-op.Dockerfile'
            private_dockerfile.write_text(patched)
            os.chmod(private_dockerfile, 0o600)
            self.mutate([*common, '-f', str(private_dockerfile), '-t', self.d['monitorOpImage'], str(source)],
                        'build-private-operator', {'source': actual}, timeout=900)
        self.d['imageProofs'] = {}
        for image, revision in [(self.d['image'], self.d['source']), *[(v, self.d['source']) for v in fixtures.values()],
                                (self.d['monitorOpImage'], actual)]:
            proof = _json(self.command(['docker', 'image', 'inspect', image], 'inspect-built-image'))[0]
            archive_path = self.private / ('image-' + uuid.uuid4().hex + '.tar')
            try:
                self.command(['docker', 'save', '--output', str(archive_path), image], 'save-original-image-metadata', timeout=600)
                os.chmod(archive_path, 0o600)
                metadata = read_docker_archive(archive_path, image=image, expected_source=revision, platform=self.d['platform'],
                                              inspect=proof, destination=self.private / 'built-image-proofs' / uuid.uuid4().hex)
            except ImageProofError as error:
                raise PrepareError(str(error)) from None
            finally:
                # 关键：只保留经 hash 核验的元数据；含私有 layer 的 tar 不进入公开出口。
                archive_path.unlink(missing_ok=True)
            self.d['imageProofs'][image] = metadata


    def load_images(self, role):
        target = self.d['targets'][role]
        local_images = [self.d['image'], *self.d['fixtureImages'].values(), self.d['monitorOpImage']]
        for image in local_images:
            self.mutate([self.d['kind'], 'load', 'docker-image', '--name', target['name'], image],
                        'load-image', {'target': role, 'clusterUID': target['clusterUID'], 'image': image}, timeout=400)
        target['nodeImageProofs'] = []
        self.d.setdefault('officialContent', {})
        for node in target['containers']:
            for image in local_images:
                cri = _json(self.command(['docker', 'exec', node['name'], 'crictl', 'inspecti', image], 'verify-local-cri-image'))
                status = cri.get('status', {})
                if status.get('id') != self.d['imageProofs'][image]['configDigest']:
                    raise PrepareError('PREPARE_LOCAL_CRI_IMAGE_MISMATCH')
                target['nodeImageProofs'].append({'node': node['name'], 'nodeId': node['id'], 'image': image,
                    'configDigest': status['id'], 'repoDigests': status.get('repoDigests', [])})
            for name in ('ksm', 'prometheus', 'promop', 'reloader', 'provisioner', 'python_fixture'):
                locked = LOCK['images'][name]
                # 关键：由 node containerd 拉取原官方 index，而非重写单架构 manifest 后伪装原 digest。
                self.mutate(['docker', 'exec', node['name'], 'ctr', '--namespace', 'k8s.io', 'images', 'pull',
                             '--platform', self.d['platform'], locked['image']],
                            'pull-official-node-image', {'nodeId': node['id'], 'image': locked['image']}, timeout=600)
                content = self.official_content(node['name'], name)
                self.d['officialContent'][name] = content
                self.mutate(['docker', 'exec', node['name'], 'ctr', '--namespace', 'k8s.io', 'images', 'tag', '--force', locked['image'], locked['tag']],
                            'retain-official-version-tag', {'nodeId': node['id'], 'image': locked['image'], 'tag': locked['tag']})
                until = time.monotonic() + 30
                while True:
                    cri = _json(self.command(['docker', 'exec', node['name'], 'crictl', 'inspecti', locked['image']], 'verify-official-cri-image'))
                    status = cri.get('status', {})
                    if status.get('id') == content['configDigest'] and locked['tag'] in status.get('repoTags', []):
                        break
                    if time.monotonic() >= until:
                        raise PrepareError('PREPARE_CRI_IMAGE_OR_VERSION_TAG_MISMATCH')
                    time.sleep(.2)
                self.d['imageProofs'][locked['image']] = {'configDigest': content['configDigest'], 'indexDigest': locked['indexDigest'],
                                                         'manifestDigest': content['manifestDigest'], 'platform': self.d['platform']}
                target['nodeImageProofs'].append({'node': node['name'], 'nodeId': node['id'], 'image': locked['image'],
                    'configDigest': content['configDigest'], 'manifestDigest': content['manifestDigest'],
                    'indexDigest': locked['indexDigest'], 'repoDigests': status.get('repoDigests', []), 'repoTags': status.get('repoTags', []),
                    'originalContentBytesVerified': True})

    def official_content(self, node, name):
        """原 index→本架构 child→config 三段内容逐字节 hash，原件只保存公开官方元数据。"""
        locked = LOCK['images'][name]
        directory = self.private / 'official-oci' / name
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        def content(digest, kind):
            raw = _bytes(self.command(['docker', 'exec', node, 'ctr', '--namespace', 'k8s.io', 'content', 'get', digest],
                                      'verify-original-oci-' + kind).stdout)
            if 'sha256:' + hashlib.sha256(raw).hexdigest() != digest:
                raise PrepareError('PREPARE_OCI_CONTENT_BYTES_MISMATCH')
            path = directory / (kind + '.raw.json')
            if path.exists() and path.read_bytes() != raw:
                raise PrepareError('PREPARE_OCI_CONTENT_CHANGED')
            if not path.exists():
                with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb') as stream:
                    stream.write(raw)
            return json.loads(raw), {'path': str(path), 'sha256': digest.split(':', 1)[1], 'bytes': len(raw)}
        index, index_ref = content(locked['indexDigest'], 'index')
        architecture = self.d['platform'].split('/')[1]
        manifests = [entry for entry in index.get('manifests', []) if entry.get('platform', {}).get('os') == 'linux'
                     and entry.get('platform', {}).get('architecture') == architecture]
        if len(manifests) != 1:
            raise PrepareError('PREPARE_OCI_PLATFORM_AMBIGUOUS')
        manifest_digest = manifests[0]['digest']
        manifest, manifest_ref = content(manifest_digest, 'manifest')
        config_digest = manifest['config']['digest']
        config, config_ref = content(config_digest, 'config')
        if config.get('os') != 'linux' or config.get('architecture') != architecture:
            raise PrepareError('PREPARE_OCI_PLATFORM_MISMATCH')
        if architecture == 'amd64' and (manifest_digest != locked['amd64Manifest'] or config_digest != locked['amd64Config']):
            raise PrepareError('PREPARE_OCI_AMD64_LOCK_MISMATCH')
        if architecture == 'arm64' and locked.get('arm64Manifest') and (manifest_digest != locked['arm64Manifest'] or config_digest != locked['arm64Config']):
            raise PrepareError('PREPARE_OCI_ARM64_LOCK_MISMATCH')
        return {'image': locked['image'], 'tag': locked['tag'], 'indexDigest': locked['indexDigest'],
                'manifestDigest': manifest_digest, 'configDigest': config_digest, 'platform': self.d['platform'],
                'evidenceRefs': [index_ref, manifest_ref, config_ref], 'originalContentBytesVerified': True}

    def operators(self, role):
        import yaml
        self.apply(role, {'apiVersion': 'v1', 'kind': 'Namespace', 'metadata': {'name': NAMESPACE}})
        bundle = self.private / 'prometheus-operator-bundle.yaml'
        if not bundle.exists():
            self.command(['curl', '--fail', '--silent', '--show-error', '--location', '--proto', '=https',
                          '--output', str(bundle), LOCK['prometheusOperator']['bundleURL']], 'fetch-public-operator-bundle', timeout=180)
            os.chmod(bundle, 0o600)
        if hashlib.sha256(bundle.read_bytes()).hexdigest() != LOCK['prometheusOperator']['bundleSHA256']:
            raise PrepareError('PREPARE_PUBLIC_BUNDLE_DIGEST_MISMATCH')
        objects = operator_bundle_objects(bundle.read_text())
        for obj in objects:
            if not obj:
                continue
            metadata = obj['metadata']
            if metadata.get('namespace') == 'default':
                metadata['namespace'] = NAMESPACE
            if obj['kind'] == 'ServiceAccount' or obj['kind'] in ('Deployment', 'Service'):
                metadata['namespace'] = NAMESPACE
            if obj['kind'] == 'ClusterRoleBinding':
                for subject in obj.get('subjects', []):
                    if subject.get('kind') == 'ServiceAccount':
                        subject['namespace'] = NAMESPACE
            if obj['kind'] == 'Deployment':
                container = obj['spec']['template']['spec']['containers'][0]
                container['image'] = LOCK['images']['promop']['image']
                container['imagePullPolicy'] = 'Never'
                container['args'] = [arg for arg in container.get('args', []) if not arg.startswith('--prometheus-config-reloader=')]
                container['args'].append('--prometheus-config-reloader=' + LOCK['images']['reloader']['image'])
            self.apply(role, obj)
        source = pathlib.Path(self.d['monitorOpSource'])
        crd_path = source / 'config/crd/bases/monitoring.fjsxy.github.io_kubestatemetrics.yaml'
        crd = crd_path.read_bytes()
        if hashlib.sha256(crd).hexdigest() != LOCK['monitorOp']['crdSHA256']:
            raise PrepareError('PREPARE_PRIVATE_CRD_CHANGED')
        self.apply(role, yaml.safe_load(crd))
        name = 'monitor-mgr-monitor-op'
        self.apply(role, {'apiVersion': 'v1', 'kind': 'ServiceAccount', 'metadata': {'name': name, 'namespace': NAMESPACE}})
        # 固定上游生成的 Role 在运行时私有读取；不复制到公开仓库或 artifact。
        role_object = yaml.safe_load((source / 'config/rbac/role.yaml').read_text())
        role_object['metadata'] = {'name': name}
        self.apply(role, role_object)
        self.apply(role, {'apiVersion': 'rbac.authorization.k8s.io/v1', 'kind': 'ClusterRoleBinding', 'metadata': {'name': name},
                          'roleRef': {'apiGroup': 'rbac.authorization.k8s.io', 'kind': 'ClusterRole', 'name': name},
                          'subjects': [{'kind': 'ServiceAccount', 'name': name, 'namespace': NAMESPACE}]})
        # leader election 使用独立命名空间 Lease；上游 manager Role 不必包含 election Role。
        self.apply(role, {'apiVersion': 'rbac.authorization.k8s.io/v1', 'kind': 'Role', 'metadata': {'name': name + '-leader', 'namespace': NAMESPACE},
                          'rules': [{'apiGroups': ['coordination.k8s.io'], 'resources': ['leases'], 'verbs': ['get', 'list', 'watch', 'create', 'update', 'patch']},
                                    {'apiGroups': [''], 'resources': ['events'], 'verbs': ['create', 'patch']}]})
        self.apply(role, {'apiVersion': 'rbac.authorization.k8s.io/v1', 'kind': 'RoleBinding', 'metadata': {'name': name + '-leader', 'namespace': NAMESPACE},
                          'roleRef': {'apiGroup': 'rbac.authorization.k8s.io', 'kind': 'Role', 'name': name + '-leader'},
                          'subjects': [{'kind': 'ServiceAccount', 'name': name, 'namespace': NAMESPACE}]})
        self.apply(role, {'apiVersion': 'apps/v1', 'kind': 'Deployment', 'metadata': {'name': name, 'namespace': NAMESPACE},
                          'spec': {'replicas': 1, 'selector': {'matchLabels': {'app': name}}, 'template': {
                              'metadata': {'labels': {'app': name}}, 'spec': {'serviceAccountName': name,
                                  'securityContext': {'runAsNonRoot': True}, 'containers': [{
                                      'name': 'manager', 'image': self.d['monitorOpImage'], 'imagePullPolicy': 'Never',
                                      'args': ['--leader-elect=true', '--health-probe-bind-address=:8081', '--log-level=info'],
                                      'readinessProbe': {'httpGet': {'path': '/readyz', 'port': 8081}},
                                      'livenessProbe': {'httpGet': {'path': '/healthz', 'port': 8081}}}]}}}})
        for name in ('prometheus-operator', 'monitor-mgr-monitor-op'):
            self.k(role, '-n', NAMESPACE, 'rollout', 'status', 'deployment/' + name, '--timeout=180s', timeout=200)
        crds = _json(self.k(role, 'get', 'crds', '-o', 'json'))['items']
        for obj in crds:
            if obj['metadata'].get('labels', {}).get(RUN_LABEL) == self.d['runId']:
                if not any(condition.get('type') == 'Established' and condition.get('status') == 'True' for condition in obj.get('status', {}).get('conditions', [])):
                    raise PrepareError('PREPARE_CRD_NOT_ESTABLISHED')
        self.d['targets'][role]['crds'] = [{'name': obj['metadata']['name'], 'uid': obj['metadata']['uid'],
            'specSHA256': hashlib.sha256(json.dumps(obj['spec'], sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()}
            for obj in crds if obj['metadata'].get('labels', {}).get(RUN_LABEL) == self.d['runId']]
        # 新集群也固定默认 provisioner 的字节；不沿用未观察的旧 SC/Pod 状态。
        provisioners = _json(self.k(role, '-n', 'local-path-storage', 'get', 'deployment/local-path-provisioner', '-o', 'json'))
        self.ledger({'event': 'prepare_baseline_before', 'runId': self.d['runId'], 'target': role,
                     'kind': 'Deployment', 'namespace': 'local-path-storage', 'name': 'local-path-provisioner', 'uid': provisioners['metadata']['uid']})
        target = self.d['targets'][role]
        self.mutate([self.d['kubectl'], '--kubeconfig', target['kubeconfig'], '--context', target['context'], '-n', 'local-path-storage',
                     'set', 'image', 'deployment/local-path-provisioner', 'local-path-provisioner=' + LOCK['images']['provisioner']['image']],
                    'pin-default-provisioner', {'target': role, 'uid': provisioners['metadata']['uid']})
        self.k(role, '-n', 'local-path-storage', 'rollout', 'status', 'deployment/local-path-provisioner', '--timeout=180s', timeout=200)

    def delete_owned(self, role, identity, resource, path):
        """探针只删除刚回读仍匹配的原 UID，正常 Foreground 等待，不重写 cleanup 成功。"""
        current = _json(self.k(role, '-n', NAMESPACE, 'get', resource, identity['metadata']['name'], '-o', 'json'))
        if current['metadata']['uid'] != identity['metadata']['uid']:
            raise PrepareError('PREPARE_PROBE_UID_CHANGED')
        options = {'apiVersion': 'v1', 'kind': 'DeleteOptions', 'preconditions': {
            'uid': current['metadata']['uid'], 'resourceVersion': current['metadata']['resourceVersion']}, 'propagationPolicy': 'Foreground'}
        self.ledger({'event': 'prepare_probe_cleanup_intent', 'runId': self.d['runId'], 'target': role,
                     'kind': current['kind'], 'name': current['metadata']['name'], 'uid': current['metadata']['uid']})
        self.k(role, 'delete', '--raw=' + path, '-f', '-', input=json.dumps(options).encode())
        self.k(role, '-n', NAMESPACE, 'wait', '--for=delete', resource + '/' + current['metadata']['name'], '--timeout=300s', timeout=320)
        remaining = self.k(role, '-n', NAMESPACE, 'get', resource, current['metadata']['name'], '--ignore-not-found=true', '-o', 'json')
        if _bytes(remaining.stdout).strip():
            raise PrepareError('PREPARE_PROBE_STILL_PRESENT')
        self.ledger({'event': 'prepare_probe_cleanup_result', 'runId': self.d['runId'], 'target': role,
                     'uid': current['metadata']['uid'], 'success': True, 'notFound': True})

    def runtime_container(self, role, pod, container, status, image_name):
        """核正在/已经真正运行的容器 config，不能以 inspecti 元数据替代成功启动。"""
        target = self.d['targets'][role]
        node = next((item for item in target['containers'] if item['name'] == pod['spec']['nodeName']), None)
        if node is None or not status.get('containerID'):
            raise PrepareError('PREPARE_RUNTIME_CONTAINER_ID_MISSING')
        official = self.d['officialContent'][image_name]
        if container['image'] != official['image']:
            raise PrepareError('PREPARE_RUNTIME_IMAGE_REF_MISMATCH')
        digest = re.search(r'sha256:[0-9a-f]{64}$', status.get('imageID', ''))
        if not digest or digest[0] not in {official['indexDigest'], official['manifestDigest'], official['configDigest']}:
            raise PrepareError('PREPARE_RUNTIME_IMAGE_DIGEST_MISMATCH')
        container_id = status['containerID'].split('://', 1)[-1]
        actual = _json(self.command(['docker', 'exec', node['name'], 'crictl', 'inspect', container_id], 'verify-running-container-config'))
        if actual.get('info', {}).get('config', {}).get('image', {}).get('image') != official['configDigest']:
            raise PrepareError('PREPARE_RUNTIME_CONFIG_DIGEST_MISMATCH')
        return {'name': container['name'], 'containerID': status['containerID'], 'image': container['image'],
                'imageID': status['imageID'], 'configDigest': official['configDigest'],
                'indexDigest': official['indexDigest'], 'manifestDigest': official['manifestDigest'],
                'actualRuntimeConfigVerified': True}

    def runtime(self, role):
        """原 index 的实际启动是准备门禁；每 node reloader 和每 target Agent 独立取证。"""
        target = self.d['targets'][role]
        proof = {'reloaderNodes': [], 'agent': None, 'cleanup': {'success': False}}
        target['runtimeProof'] = proof
        for number, node in enumerate(target['containers']):
            name = 'mm21-reloader-preflight-' + str(number)
            created = self.apply(role, {'apiVersion': 'v1', 'kind': 'Pod', 'metadata': {'name': name, 'namespace': NAMESPACE},
                'spec': {'nodeName': node['name'], 'restartPolicy': 'Never', 'automountServiceAccountToken': False,
                         'containers': [{'name': 'reloader', 'image': LOCK['images']['reloader']['image'], 'imagePullPolicy': 'Never',
                                         'command': ['/bin/prometheus-config-reloader', '--version']}]}})
            self.k(role, '-n', NAMESPACE, 'wait', '--for=jsonpath={.status.phase}=Succeeded', 'pod/' + name, '--timeout=120s', timeout=140)
            pod = _json(self.k(role, '-n', NAMESPACE, 'get', 'pod', name, '-o', 'json'))
            if pod['metadata']['uid'] != created['metadata']['uid'] or pod.get('status', {}).get('phase') != 'Succeeded':
                raise PrepareError('PREPARE_RELOADER_NOT_EXECUTED')
            status = pod['status']['containerStatuses'][0]
            if status.get('state', {}).get('terminated', {}).get('exitCode') != 0:
                raise PrepareError('PREPARE_RELOADER_VERSION_EXIT_FAILED')
            actual = self.runtime_container(role, pod, pod['spec']['containers'][0], status, 'reloader')
            output = _bytes(self.k(role, '-n', NAMESPACE, 'logs', name).stdout)
            if not re.search(rb'version\s+0\.83\.0\b', output):
                raise PrepareError('PREPARE_RELOADER_VERSION_MISMATCH')
            directory = self.private / 'official-runtime'
            directory.mkdir(exist_ok=True, mode=0o700)
            path = directory / (role + '-' + name + '-version.txt')
            with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb') as stream:
                stream.write(output)
            proof['reloaderNodes'].append({'node': node['name'], 'nodeId': node['id'], 'podUID': pod['metadata']['uid'],
                                          'phase': 'Succeeded', 'exit': 0, 'version': '0.83.0', 'container': actual,
                                          'evidenceRefs': [{'path': str(path), 'sha256': hashlib.sha256(output).hexdigest(), 'bytes': len(output)}]})
            self.delete_owned(role, pod, 'pods', '/api/v1/namespaces/' + NAMESPACE + '/pods/' + name)
        name = 'mm21-agent-preflight'
        self.apply(role, {'apiVersion': 'v1', 'kind': 'ServiceAccount', 'metadata': {'name': name, 'namespace': NAMESPACE}})
        agent = self.apply(role, {'apiVersion': 'monitoring.coreos.com/v1alpha1', 'kind': 'PrometheusAgent',
            'metadata': {'name': name, 'namespace': NAMESPACE}, 'spec': {'replicas': 1, 'shards': 1, 'version': 'v2.55.1',
                'image': LOCK['images']['prometheus']['image'], 'serviceAccountName': name, 'storage': {'emptyDir': {}},
                'remoteWrite': [{'url': 'http://127.0.0.1:1/api/v1/write'}]}})
        stateful_name = 'prom-agent-' + name
        until = time.monotonic() + 180
        while True:
            result = self.k(role, '-n', NAMESPACE, 'get', 'statefulset', stateful_name, '--ignore-not-found=true', '-o', 'json')
            if _bytes(result.stdout).strip():
                break
            if time.monotonic() >= until:
                raise PrepareError('PREPARE_AGENT_STATEFULSET_NOT_CREATED')
            time.sleep(.5)
        stateful = _json(result)
        if not any(owner.get('uid') == agent['metadata']['uid'] and owner.get('controller') is True for owner in stateful['metadata'].get('ownerReferences', [])):
            raise PrepareError('PREPARE_AGENT_STATEFULSET_OWNER_MISMATCH')
        self.k(role, '-n', NAMESPACE, 'rollout', 'status', 'statefulset/' + stateful_name, '--timeout=180s', timeout=200)
        pod = _json(self.k(role, '-n', NAMESPACE, 'get', 'pod', stateful_name + '-0', '-o', 'json'))
        if not any(owner.get('uid') == stateful['metadata']['uid'] and owner.get('controller') is True for owner in pod['metadata'].get('ownerReferences', [])):
            raise PrepareError('PREPARE_AGENT_POD_OWNER_MISMATCH')
        actual_containers = []
        for kind, status_kind in [('initContainers', 'initContainerStatuses'), ('containers', 'containerStatuses')]:
            for container in pod['spec'].get(kind, []):
                status = next((item for item in pod.get('status', {}).get(status_kind, []) if item['name'] == container['name']), None)
                if status is None or (kind == 'containers' and status.get('ready') is not True) or (kind == 'initContainers' and status.get('state', {}).get('terminated', {}).get('exitCode') != 0):
                    raise PrepareError('PREPARE_AGENT_CONTAINER_NOT_RUNNING')
                image_name = 'prometheus' if container['name'] == 'prometheus' else 'reloader'
                actual_containers.append(self.runtime_container(role, pod, container, status, image_name))
        if not {'prometheus', 'config-reloader', 'init-config-reloader'}.issubset({item['name'] for item in actual_containers}):
            raise PrepareError('PREPARE_AGENT_RELOADER_PATH_NOT_EXECUTED')
        proof['agent'] = {'crUID': agent['metadata']['uid'], 'statefulSetUID': stateful['metadata']['uid'],
                          'podUID': pod['metadata']['uid'], 'node': pod['spec']['nodeName'], 'ready': True,
                          'containers': actual_containers, 'remoteWriteDeliveryClaimed': False}
        self.delete_owned(role, agent, 'prometheusagents', '/apis/monitoring.coreos.com/v1alpha1/namespaces/' + NAMESPACE + '/prometheusagents/' + name)
        for resource, identity in [('statefulsets', stateful), ('pods', pod)]:
            self.k(role, '-n', NAMESPACE, 'wait', '--for=delete', resource + '/' + identity['metadata']['name'], '--timeout=300s', timeout=320)
            remaining = self.k(role, '-n', NAMESPACE, 'get', resource, identity['metadata']['name'], '--ignore-not-found=true', '-o', 'json')
            if _bytes(remaining.stdout).strip():
                raise PrepareError('PREPARE_AGENT_PROBE_GC_INCOMPLETE')
        proof['cleanup'] = {'success': True, 'agentUID': agent['metadata']['uid'], 'statefulSetUID': stateful['metadata']['uid'],
                            'podUID': pod['metadata']['uid'], 'notFound': True, 'reloaderPodsDeleted': len(proof['reloaderNodes'])}
        self.ledger({'event': 'prepare_runtime_verified', 'runId': self.d['runId'], 'target': role, 'proof': proof})

    def volume(self, role):
        name = 'mm21-volume-preflight'
        self.apply(role, {'apiVersion': 'v1', 'kind': 'PersistentVolumeClaim', 'metadata': {'name': name, 'namespace': NAMESPACE},
                          'spec': {'accessModes': ['ReadWriteOnce'], 'resources': {'requests': {'storage': '1Gi'}}}})
        self.apply(role, {'apiVersion': 'batch/v1', 'kind': 'Job', 'metadata': {'name': name, 'namespace': NAMESPACE},
                          'spec': {'backoffLimit': 0, 'template': {'metadata': {'labels': {RUN_LABEL: self.d['runId']}},
                              'spec': {'restartPolicy': 'Never', 'securityContext': {'runAsUser': 10001, 'runAsGroup': 10001, 'fsGroup': 10001},
                                  'containers': [{'name': 'probe', 'image': self.d['fixtureImages']['environment'], 'imagePullPolicy': 'Never',
                                                  'volumeMounts': [{'name': 'data', 'mountPath': '/data'}]}],
                                  'volumes': [{'name': 'data', 'persistentVolumeClaim': {'claimName': name}}]}}}})
        self.k(role, '-n', NAMESPACE, 'wait', '--for=condition=complete', 'job/' + name, '--timeout=180s', timeout=200)
        proof = _json(self.k(role, '-n', NAMESPACE, 'logs', 'job/' + name))
        if (proof.get('uid') != 10001 or proof.get('journalMode') != 'wal' or proof.get('synchronous') != 2 or
                proof.get('lockBlocked') is not True or proof.get('reopenValue') != 21 or proof.get('keyMode') != '0600'):
            raise PrepareError('PREPARE_VOLUME_CAPABILITY_FAILED')
        self.d['targets'][role]['volumeProof'] = proof
        # 预检卷留在本 run 基线直到整体清理；不将部分 finally 当环境成功。


def prepare_environment(request, *, execute, ledger):
    """由唯一 child 持整批锁后调用；成功返回私有 descriptor，异常由主 Session finally 接管。"""
    descriptor = _request(request)
    if descriptor['profile'] == 'CI_FAST':
        descriptor['lifecycle'] = 'ready'
        return descriptor
    preparation = _Preparation(descriptor, execute, ledger)
    existing = _bytes(preparation.command([descriptor['kind'], 'get', 'clusters'], 'list-existing-clusters').stdout).decode().splitlines()
    if any(target['name'] in existing for target in descriptor['targets'].values()):
        raise PrepareError('PREPARE_CLUSTER_EXISTS')
    kind_version = _bytes(preparation.command([descriptor['kind'], 'version'], 'verify-kind-version').stdout)
    kubectl_version = _json(preparation.command([descriptor['kubectl'], 'version', '--client=true', '-o', 'json'], 'verify-kubectl-version'))
    if b'v0.33.0' not in kind_version or kubectl_version.get('clientVersion', {}).get('gitVersion') != 'v1.28.15':
        raise PrepareError('PREPARE_TOOL_VERSION_MISMATCH')
    if descriptor['profile'] == 'CI_SMOKE':
        host = preparation.command(['uname', '-sm'], 'verify-linux-host')
        if _bytes(host.stdout).strip() != b'Linux x86_64':
            raise PrepareError('PREPARE_SMOKE_HOST_MISMATCH')
    descriptor['lifecycle'] = 'preparing'
    ledger({'event': 'prepare_request', 'descriptor': descriptor})
    preparation.private.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(preparation.private, 0o700)
    # 关键：将同一个可变 descriptor 交调用者 checkpoint；部分失败也保留已观察的 UID。
    try:
        preparation.build()
        for role in descriptor['targets']:
            preparation.cluster(role)
            preparation.load_images(role)
            preparation.operators(role)
            preparation.runtime(role)
            preparation.volume(role)
            ledger({'event': 'prepare_target_checkpoint', 'target': role, 'descriptor': descriptor})
        verified = verify_environment(descriptor, execute=execute)
        descriptor.update(verified)
        descriptor['lifecycle'] = 'ready'
        ledger({'event': 'prepare_ready', 'descriptor': descriptor})
        return descriptor
    except BaseException as error:
        # 关键：部分失败把已观察身份交还主 Session；不把中断改成成功或覆盖原异常。
        descriptor['lifecycle'] = 'prepare_failed'
        error.descriptor = copy.deepcopy(descriptor)
        try:
            ledger({'event': 'prepare_failed', 'descriptor': descriptor, 'error': 'PREPARE_FAILED'})
        except Exception:
            error.prepareLedgerFailed = True
        raise



def verify_environment(descriptor, *, execute):
    """回读身份与实际就绪；读取错误始终失败，不能按空集处理。"""
    result = copy.deepcopy(descriptor)
    for role, target in result.get('targets', {}).items():
        prefix = [result['kubectl'], '--kubeconfig', target['kubeconfig'], '--context', target['context']]
        current = _json(_check(execute([*prefix, 'get', 'namespace', 'kube-system', '-o', 'json'], label='verify-cluster-uid'), 'VERIFY_CLUSTER_UID'))
        if current['metadata']['uid'] != target['clusterUID']:
            raise PrepareError('PREPARE_CLUSTER_UID_CHANGED')
        nodes = _json(_check(execute([*prefix, 'get', 'nodes', '-o', 'json'], label='verify-node-topology'), 'VERIFY_NODE_TOPOLOGY'))['items']
        roles = sorted('control-plane' if 'node-role.kubernetes.io/control-plane' in node['metadata'].get('labels', {}) else 'worker' for node in nodes)
        if roles != (['control-plane', 'worker', 'worker'] if role == 'control' else ['control-plane']):
            raise PrepareError('PREPARE_NODE_TOPOLOGY_MISMATCH')
        for node in nodes:
            if (node.get('status', {}).get('nodeInfo', {}).get('architecture') != result['platform'].split('/')[1] or
                    node.get('status', {}).get('nodeInfo', {}).get('kubeletVersion') != 'v1.28.15') or not any(
                    condition.get('type') == 'Ready' and condition.get('status') == 'True' for condition in node.get('status', {}).get('conditions', [])):
                raise PrepareError('PREPARE_NODE_NOT_READY')
            if role == 'data' and (node.get('spec', {}).get('unschedulable') or node.get('spec', {}).get('taints')):
                raise PrepareError('PREPARE_DATA_NOT_SCHEDULABLE')
        target['nodeTopology'] = [{'name': node['metadata']['name'], 'uid': node['metadata']['uid'],
                                   'role': 'control-plane' if 'node-role.kubernetes.io/control-plane' in node['metadata'].get('labels', {}) else 'worker',
                                   'version': node['status']['nodeInfo']['kubeletVersion']} for node in nodes]
        classes = _json(_check(execute([*prefix, 'get', 'storageclasses', '-o', 'json'], label='verify-default-storage'), 'VERIFY_DEFAULT_STORAGE'))['items']
        defaults = [item for item in classes if item['metadata'].get('annotations', {}).get('storageclass.kubernetes.io/is-default-class') == 'true']
        if len(defaults) != 1:
            raise PrepareError('PREPARE_DEFAULT_STORAGE_AMBIGUOUS')
        target['storageClass'] = {'name': defaults[0]['metadata']['name'], 'uid': defaults[0]['metadata']['uid'], 'provisioner': defaults[0]['provisioner']}
        pods = _json(_check(execute([*prefix, '-n', NAMESPACE, 'get', 'pods', '-o', 'json'], label='verify-controller-pods'), 'VERIFY_CONTROLLER_PODS'))['items']
        controllers = []
        for name, image in [('prometheus-operator', LOCK['images']['promop']['image']), ('monitor-mgr-monitor-op', result['monitorOpImage'])]:
            selected = [pod for pod in pods if pod['metadata']['name'].startswith(name + '-') and not pod['metadata'].get('deletionTimestamp')]
            if len(selected) != 1 or not all(status.get('ready') for status in selected[0].get('status', {}).get('containerStatuses', [])) or not selected[0].get('status', {}).get('containerStatuses'):
                raise PrepareError('PREPARE_CONTROLLER_NOT_READY')
            pod = selected[0]
            status = pod['status']['containerStatuses'][0]
            if pod['spec']['containers'][0]['image'] != image or not status.get('imageID'):
                raise PrepareError('PREPARE_CONTROLLER_IMAGE_MISMATCH')
            image_proof = next((item for item in target.get('nodeImageProofs', [])
                                if item['node'] == pod['spec']['nodeName'] and item['image'] == image), None)
            normalize = lambda value: value.rsplit('@', 1)[-1].split('://', 1)[-1]
            if image_proof is None or normalize(status['imageID']) not in {normalize(item) for item in
                    [image_proof['configDigest'], *image_proof['repoDigests']]}:
                raise PrepareError('PREPARE_CONTROLLER_RUNTIME_DIGEST_MISMATCH')
            controllers.append({'name': name, 'podUID': pod['metadata']['uid'], 'image': image, 'imageID': status['imageID']})
        target['controllers'] = controllers
    return result


def cleanup_environment(descriptor, *, execute, ledger):
    """只删除已记录且仍匹配的原 cluster/container UID；恢复成功不修改旧收据。"""
    expected = {'control', 'data'} if descriptor.get('profile') == 'LOCAL_FULL' else {'control'} if descriptor.get('profile') == 'CI_SMOKE' else set()
    if set(descriptor.get('targets', {})) != expected:
        raise PrepareError('RECOVER_TARGETS_INVALID')
    report = {'schemaVersion': 1, 'runId': descriptor['runId'], 'kind': 'environment-recovery', 'targets': {}, 'success': True}
    safe_errors = {'RECOVER_CONTAINER_UID_UNKNOWN', 'RECOVER_CONTAINER_READ_FAILED',
                   'RECOVER_CONTAINER_UID_CHANGED', 'RECOVER_CLUSTER_UID_UNKNOWN',
                   'RECOVER_CLUSTER_READ_FAILED', 'RECOVER_CLUSTER_UID_CHANGED',
                   'RECOVER_CLUSTER_DELETE_FAILED', 'RECOVER_CONFIRM_READ_FAILED',
                   'RECOVER_CLUSTER_STILL_PRESENT'}
    class LedgerFailure(PrepareError):
        pass
    def record(event):
        # 关键：ledger/fsync 失败意味着后续动作不能持久归属，必须停止整批写入。
        try:
            ledger(event)
        except Exception:
            report['success'] = False
            if event.get('target') in report['targets']:
                report['targets'][event['target']].update(success=False, error='RECOVER_LEDGER_FAILED')
            error = LedgerFailure('RECOVER_LEDGER_FAILED')
            error.report = report
            raise error from None
    # 关键：顺序删除独立 kubeconfig，避免 kind 并发写同一默认配置。
    for role in reversed(list(descriptor.get('targets', {}))):
        target = descriptor['targets'][role]
        state = {'name': target.get('name'), 'success': False}
        report['targets'][role] = state
        try:
            prefix = [descriptor['kubectl'], '--kubeconfig', target['kubeconfig'], '--context', target['context']]
            if not target.get('containers'):
                raise PrepareError('RECOVER_CONTAINER_UID_UNKNOWN')
            current = execute(['docker', 'ps', '-a', '--no-trunc', '--filter', 'label=io.x-k8s.kind.cluster=' + target['name'], '--format', '{{json .}}'], label='recover-containers')
            _check(current, 'RECOVER_CONTAINER_READ')
            actual = [json.loads(line) for line in _bytes(current.stdout).splitlines() if line]
            if not actual:
                state.update(success=True, absent=True)
                record({'event': 'environment_cleanup_result', 'runId': descriptor['runId'], 'target': role, 'result': state})
                continue
            if {item['ID'] for item in actual} != {item['id'] for item in target['containers']}:
                raise PrepareError('RECOVER_CONTAINER_UID_CHANGED')
            if not target.get('clusterUID'):
                raise PrepareError('RECOVER_CLUSTER_UID_UNKNOWN')
            uid = _json(_check(execute([*prefix, 'get', 'namespace', 'kube-system', '-o', 'json'], label='recover-cluster-uid'), 'RECOVER_CLUSTER_READ'))['metadata']['uid']
            if uid != target['clusterUID']:
                raise PrepareError('RECOVER_CLUSTER_UID_CHANGED')
            record({'event': 'environment_cleanup_intent', 'runId': descriptor['runId'], 'target': role, 'clusterUID': uid, 'containers': target['containers']})
            _check(execute([descriptor['kind'], 'delete', 'cluster', '--name', target['name'], '--kubeconfig', target['kubeconfig']],
                           label='recover-delete-owned-cluster', timeout=300), 'RECOVER_CLUSTER_DELETE')
            remaining = execute(['docker', 'ps', '-a', '--no-trunc', '--filter', 'label=io.x-k8s.kind.cluster=' + target['name'], '--format', '{{json .}}'], label='recover-confirm-absent')
            _check(remaining, 'RECOVER_CONFIRM_READ')
            if _bytes(remaining.stdout).strip():
                raise PrepareError('RECOVER_CLUSTER_STILL_PRESENT')
            state.update(success=True, absent=True, clusterUID=uid)
        except LedgerFailure:
            state.update(success=False, error='RECOVER_LEDGER_FAILED')
            raise
        except Exception as error:
            # 单个目标的执行超时/OS错误不妨碍另一已确认目标；公开结果只保留固定码。
            if isinstance(error, PrepareError) and str(error) in safe_errors:
                state['error'] = str(error)
            elif isinstance(error, (ValueError, KeyError, TypeError)) or str(error) == 'PREPARE_RESPONSE_INVALID':
                state['error'] = 'RECOVER_RESPONSE_INVALID'
            else:
                state['error'] = 'RECOVER_EXECUTION_FAILED'
            report['success'] = False
        record({'event': 'environment_cleanup_result', 'runId': descriptor['runId'], 'target': role, 'result': state})
    return report


if __name__ == '__main__':
    raise SystemExit('prepare 仅由持锁的主 run/recover 调用；不提供独立 API 写入口')
