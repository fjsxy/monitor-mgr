"""独占双集群生命周期。每次运行只持有自己创建的容器、UID 和私有 kubeconfig。"""
import base64
import copy
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import time
import urllib.request

import yaml
from testing_contract import owned_identity
import sys
sys.path.insert(0, str(Path(__file__).parent / 'acceptance'))
from transport import ProcessRunner, ProcessFailure

ROOT = Path(__file__).resolve().parents[1]
LOCK = json.loads((ROOT / 'scripts/acceptance/source-lock.json').read_text())
RUN_LABEL = 'monitor-mgr.test/run'
COMMAND_STAGES = frozenset(['build-platform', 'fetch-public-release-source', 'fetch-published-operator',
    'clone-locked-operator', 'checkout-locked-operator', 'operator-sha', 'build-release-tool',
    'verify-published-operator', 'verify-release-bundle', 'inventory-kind', 'kind-create-control', 'kind-create-data']) | frozenset('kubectl-'+role+'-'+verb for role in ('control','data') for verb in ('get','patch','create','delete','wait','scale','rollout'))


def private_json(path, value):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2)); os.chmod(path, 0o600)


class Environment:
    def __init__(self, directory, run_id, candidate, operator_source=None):
        self.root = Path(directory).resolve(); self.root.mkdir(parents=True, exist_ok=False, mode=0o700)
        self.private = self.root / '.private'; self.private.mkdir(mode=0o700)
        self.public = self.root / 'public'; self.public.mkdir()
        self.run_id, self.candidate = run_id, candidate
        self.processes = ProcessRunner(self.private / 'processes')
        self.targets = {}; self.resources = []; self.source = operator_source; self.official_configs={};self.operator_proofs=[]
        arch = 'arm64' if platform.machine() in ('aarch64', 'arm64') else 'amd64'
        self.platform = 'linux/' + arch
        self.image = 'monitor-mgr:e2e-' + candidate[:12]
        image = LOCK['monitorOp']['release']['image']
        self.operator_image = image['repository'] + '@' + image['indexDigest']
        self.operator_content = []

    def command(self, command, label='command', **kwargs):
        """外部原始输出始终私有；失败只公开经过白名单的阶段和真实退出码。"""
        try:
            result = self.processes.run([str(v) for v in command], label=label, **kwargs)
        except ProcessFailure as error:
            self.command_failure(label, error.child_exit, timed_out=error.timed_out)
            raise
        if result.returncode:
            self.command_failure(label, result.returncode, stderr=result.stderr)
            raise RuntimeError(label + ': exit=' + str(result.returncode) + '；原始输出见私有 processes')
        return result

    def command_failure(self, label, exit_code, *, timed_out=False, stderr=b''):
        """关键：label 不可信，未知标签只投影 unknown，禁止原文/argv/路径进入公共 artifact。"""
        operation = label if type(label) is str and label in COMMAND_STAGES else 'unknown'
        code = 'E2E_COMMAND_TIMEOUT' if timed_out else 'E2E_COMMAND_FAILED'
        if operation == 'build-platform' and not timed_out:
            code = 'BUILD_COMMAND_FAILED'
            if b'unknown scheme oci-layout' in stderr: code = 'BUILD_OCI_LAYOUT_UNSUPPORTED'
            if b'no space left on device' in stderr: code = 'BUILD_DISK_FULL'
        diagnostic = dict(operation=operation, exit=exit_code if type(exit_code) is int and -255 <= exit_code <= 255 else None, errorCode=code)
        if operation.startswith('kubectl-'):
            from historical_cleanup import kubectl_failure
            reason=kubectl_failure(stderr)
            diagnostic['errorCode']={'not-found':'KUBERNETES_NOT_FOUND','conflict':'KUBERNETES_CONFLICT','invalid':'KUBERNETES_INVALID','precondition-failed':'KUBERNETES_PRECONDITION_FAILED','forbidden':'KUBERNETES_FORBIDDEN','wait-timeout':'KUBERNETES_WAIT_TIMEOUT'}.get(reason,code)
        private_json(self.public/'command-failure.json', diagnostic)
        print('外部步骤失败：'+json.dumps(diagnostic), flush=True)

    def checkpoint(self):
        # 持久保存已经观察到的身份，使失败后的独立 cleanup 仍能核验原资源。
        private_json(self.private / 'environment.json', dict(runId=self.run_id, candidate=self.candidate, targets=self.targets, resources=self.resources))

    def k(self, role, *args, data=None, check=True):
        target = self.targets[role]
        command = ['kubectl', '--kubeconfig', target['kubeconfig'], '--context', target['context'], *args]
        mutation=data is not None and any(v in args for v in ('create','apply'))
        if mutation and '-o' not in args and not any(str(v).startswith('--output') for v in args):command+=['-o','json']
        encoded = None if data is None else json.dumps(data).encode()
        result = self.processes.run(command, label='kubectl-' + role + '-' + str(args[0]), input=encoded, timeout=240)
        if check and result.returncode:
            self.command_failure('kubectl-'+role+'-'+str(args[0]),result.returncode,stderr=result.stderr)
            raise RuntimeError('kubectl-' + role + '-' + str(args[:2]) + ': exit=' + str(result.returncode))
        # 关键：原 UID 取自本次 create/apply 响应；后续同名 GET 不能成为认领依据。
        if mutation and result.returncode==0:
            response=json.loads(result.stdout)
            objects=response.get('items',[]) if response.get('kind')=='List' else [response]
            for obj in objects:
                metadata=obj.get('metadata',{})
                if obj.get('kind') and metadata.get('uid'):
                    item=dict(target=role,kind=obj['kind'],name=metadata['name'],namespace=metadata.get('namespace',''),uid=metadata['uid'],runId=self.run_id)
                    if not any(v['uid']==item['uid'] for v in self.resources):self.resources.append(item)
            self.checkpoint()
        return result

    def get(self, role, *args):
        return json.loads(self.k(role, 'get', *args, '-o', 'json').stdout)

    def built_config(self,image,revision,inspect,label):
        # 关键：inspect.Id 可能是 manifest；只用原 docker-save Config 字节的 SHA256 绑定实际 CRI config。
        from image_provenance import read_docker_archive
        path=self.private/(label+'.tar')
        try:
            self.command(['docker','save','--output',path,image],'save-'+label,timeout=180);os.chmod(path,0o600)
            proof=read_docker_archive(path,image=image,expected_source=revision,platform=self.platform,inspect=inspect,destination=self.private/(label+'-metadata'))
            return proof['configDigest']
        finally:path.unlink(missing_ok=True)

    def verify_container(self,pod,container,expected_config):
        # 关键：绑定真实 Pod UID、容器 ID 与 CRI 实际 config，不能仅凭镜像 tag 或部署 annotation 宣称同源。
        node=pod['spec']['nodeName'];assert node in {n['metadata']['name'] for t in self.targets.values() for n in t['nodes']}
        status=next(c for c in pod['status']['containerStatuses'] if c['name']==container)
        cid=status['containerID'].removeprefix('containerd://');assert re.fullmatch('[0-9a-f]{64}',cid)
        inspected=json.loads(self.command(['docker','exec',node,'crictl','inspect',cid],'verify-running-container').stdout)
        assert inspected['status']['labels']['io.kubernetes.pod.uid']==pod['metadata']['uid']
        assert inspected['info']['config']['image']['image']==expected_config
        return dict(podUID=pod['metadata']['uid'],containerID=cid,node=node,configDigest=expected_config,imageID=status['imageID'])

    def runtime_config(self,name,node):
        spec=LOCK['images'][name]
        def content(digest):
            raw=self.command(['docker','exec',node,'ctr','-n','k8s.io','content','get',digest],'verify-locked-runtime-content').stdout
            assert 'sha256:'+hashlib.sha256(raw).hexdigest()==digest;return json.loads(raw)
        index=content(spec['indexDigest']);arch=self.platform.split('/')[1]
        candidates=[m for m in index['manifests'] if m.get('platform',{}).get('os')=='linux' and m.get('platform',{}).get('architecture')==arch]
        assert len(candidates)==1
        return content(candidates[0]['digest'])['config']['digest']

    def prepare_clusters(self):
        # 关键：独立 kubeconfig 参数阻止 kind 写入用户默认 context。
        existing = self.command(['kind', 'get', 'clusters'], 'inventory-kind').stdout.decode().splitlines()
        for role in ('control', 'data'):
            name = self.run_id + '-' + role
            if name in existing: raise RuntimeError('独占集群名称已存在，拒绝接管')
            target = dict(name=name, context='kind-' + name, kubeconfig=str(self.private / (role + '.kubeconfig')), containers=[])
            self.targets[role] = target; self.checkpoint()
            config = dict(kind='Cluster', apiVersion='kind.x-k8s.io/v1alpha4', featureGates={'StatefulSetAutoDeletePVC': True},
                          nodes=[{'role': 'control-plane'}] + ([{'role': 'worker'}, {'role': 'worker'}] if role == 'control' else []))
            from cluster_evidence import audit_configuration
            policy = self.private / (role + '-audit-policy.json')
            audit = audit_configuration(policy); private_json(policy, audit['policy'])
            config['kubeadmConfigPatches'] = audit['kubeadmConfigPatches']
            config['nodes'][0].update(audit['nodes'][0])
            path = self.private / (role + '-kind.json'); private_json(path, config)
            print('准备独占 kind：' + name, flush=True)
            try:
                self.command(['kind', 'create', 'cluster', '--name', name, '--config', path, '--image', LOCK['images']['kind_node']['image'],
                              '--kubeconfig', target['kubeconfig'], '--wait', '180s'], 'kind-create-' + role, timeout=600)
            finally:
                rows = self.command(['docker', 'ps', '-a', '--no-trunc', '--filter', 'label=io.x-k8s.kind.cluster=' + name,
                                     '--format', '{{.ID}}'], 'cluster-container-identity').stdout.decode().splitlines()
                target['containers'] = rows; self.checkpoint()
            os.chmod(target['kubeconfig'], 0o600)
            target['clusterUID'] = self.get(role, 'namespace', 'kube-system')['metadata']['uid']
            self.k(role, 'wait', '--for=condition=Ready', 'nodes', '--all', '--timeout=180s')
            if role == 'data': self.k(role, 'taint', 'nodes', name + '-control-plane', 'node-role.kubernetes.io/control-plane-', check=False)
            config = json.loads(self.k(role, 'config', 'view', '--minify', '--flatten', '--raw', '-o', 'json').stdout)
            inspect = json.loads(self.command(['docker', 'inspect', name + '-control-plane'], 'node-address').stdout)[0]
            address = inspect['NetworkSettings']['Networks']['kind']['IPAddress']
            config['clusters'][0]['cluster']['server'] = 'https://' + address + ':6443'
            path = self.private / (role + '-pod.json'); private_json(path, config); target['podConfig'] = str(path)
            target['nodes'] = self.get(role, 'nodes')['items']; self.checkpoint()

    def platform_credentials(self, role, namespace, name='platform'):
        """生成真实受限 SA/token，并通过该凭据获取实际身份和 SSAR 权限原始结果。"""
        from cluster_evidence import platform_rbac
        target = self.targets[role]
        actual_namespace = self.get(role, 'namespace', namespace)
        assert actual_namespace['metadata'].get('labels', {}).get(RUN_LABEL) == self.run_id
        objects = platform_rbac(namespace, name, self.run_id)
        created = []
        for obj in objects:
            if obj['kind'] in ('ClusterRole', 'ClusterRoleBinding'):
                # 关键：cluster-scoped Namespace 是合法所有者，测试 namespace 删除时回收专用授权。
                obj['metadata']['ownerReferences'] = [dict(apiVersion='v1', kind='Namespace', name=namespace, uid=actual_namespace['metadata']['uid'])]
            response = json.loads(self.k(role, 'create', '-f', '-', data=obj).stdout)
            created.append(dict(target=role, kind=response['kind'], name=response['metadata']['name'], namespace=response['metadata'].get('namespace', ''), uid=response['metadata']['uid']))
        account = self.get(role, 'serviceaccount', name, '-n', namespace)
        token = self.k(role, 'create', 'token', name, '-n', namespace, '--duration=2h').stdout.decode().strip()
        config = json.loads(Path(target['podConfig']).read_text())
        config['users'] = [dict(name='platform', user=dict(token=token))]
        for context in config['contexts']: context['context']['user'] = 'platform'
        probe_config = copy.deepcopy(config)
        # 宿主核验仍连接私有 kubeconfig 的原宿主 API 地址；平台 Pod 使用 Docker 网络地址。
        host_config = json.loads(self.k(role, 'config', 'view', '--minify', '--flatten', '--raw', '-o', 'json').stdout)
        probe_config['clusters'] = host_config['clusters']
        path = self.private / ('platform-' + role + '-' + namespace + '-' + name + '.json')
        private_json(path, probe_config)
        def probe(api, body):
            result = self.command(['kubectl', '--kubeconfig', path, '--context', config['current-context'], 'create', '--raw', api, '-f', '-'],
                                  'platform-real-permission', input=json.dumps(body).encode())
            return json.loads(result.stdout)
        identity = probe('/apis/authentication.k8s.io/v1/selfsubjectreviews', dict(apiVersion='authentication.k8s.io/v1', kind='SelfSubjectReview'))
        permissions = []
        for group, resource, verbs in [('monitoring.coreos.com', 'servicemonitors', ('get', 'list', 'watch', 'create', 'update', 'patch', 'delete', 'deletecollection')),
                                       ('monitoring.fjsxy.github.io', 'kubestatemetrics', ('get', 'list', 'create', 'update', 'patch', 'delete')),
                                       ('apps', 'deployments', ('create', 'update')), ('apps', 'statefulsets', ('create', 'update')),
                                       ('apps', 'daemonsets', ('create', 'update')),
                                       ('apiextensions.k8s.io', 'customresourcedefinitions', ('get',))]:
            for verb in verbs:
                attrs = dict(group=group, resource=resource, verb=verb)
                if group == 'apps': attrs['namespace'] = namespace
                response = probe('/apis/authorization.k8s.io/v1/selfsubjectaccessreviews',
                                 dict(apiVersion='authorization.k8s.io/v1', kind='SelfSubjectAccessReview', spec=dict(resourceAttributes=attrs)))
                permissions.append(dict(**attrs, status=response['status']))
        return dict(config=config, identity=identity['status']['userInfo']['username'], serviceAccountUID=account['metadata']['uid'],
                    identityReview=identity, permissions=permissions, resources=created)

    def audit_events(self, role, identity=None):
        """从本 run 原 control-plane 容器读取 Metadata 日志，保留所有请求尝试供 Ginkgo 断言。"""
        from cluster_evidence import AUDIT_LOG, audit_metadata
        target = self.targets[role]
        node = next(n for n in target['nodes'] if n['metadata']['name'] == target['name'] + '-control-plane')
        actual = self.command(['docker', 'inspect', '--format', '{{.Id}}', node['metadata']['name']], 'audit-node-identity').stdout.decode().strip()
        assert actual in target['containers']
        raw = self.command(['docker', 'exec', actual, 'cat', AUDIT_LOG], 'read-apiserver-metadata-audit').stdout.decode()
        return audit_metadata(raw, identity)

    def build(self):
        source = self.private / 'monitor-op'
        if self.source:
            # 显式本地来源仍只读克隆提交，不复制未提交内容或修改原 checkout。
            self.command(['git', 'clone', '--quiet', '--no-hardlinks', '--no-checkout', self.source, source], 'clone-locked-operator', timeout=300)
        else:
            self.command(['make', 'operator-release-source', 'PYTHON='+sys.executable, 'OPERATOR_SOURCE_DESTINATION='+str(source)], 'fetch-public-release-source', cwd=ROOT, timeout=600)
        self.command(['git', '-C', source, 'checkout', '--quiet', '--detach', LOCK['monitorOp']['source']], 'checkout-locked-operator')
        assert self.command(['git', '-C', source, 'rev-parse', 'HEAD'], 'operator-sha').stdout.decode().strip() == LOCK['monitorOp']['source']
        self.operator_source = source
        # 关键：仅编译同源发布管理 CLI；Operator 本身必须消费原 release 镜像和资产。
        from operator_release import bundle_proof
        supplied = os.environ.get('MONITOR_OP_RELEASE_BUNDLE')
        self.release_bundle = self.private / 'operator-bundle'
        if supplied:
            bundle_proof(supplied)
            self.release_bundle.mkdir(mode=0o700)
            for asset in LOCK['monitorOp']['release']['assets']:
                shutil.copyfile(Path(supplied) / asset, self.release_bundle / asset)
                os.chmod(self.release_bundle / asset, 0o600)
        else:
            self.command(['make', 'operator-release-fetch', 'PYTHON='+sys.executable, 'OPERATOR_RELEASE_BUNDLE='+str(self.release_bundle)], 'fetch-published-operator', cwd=ROOT, timeout=600)
        self.command(['make', 'operator-release-verify', 'PYTHON='+sys.executable, 'OPERATOR_RELEASE_BUNDLE='+str(self.release_bundle)], 'verify-release-bundle', cwd=ROOT)
        self.release_proof = bundle_proof(self.release_bundle)
        self.release_tool = self.private / 'operator-release'
        self.command(['go', 'build', '-trimpath', '-o', self.release_tool, './cmd/release'], 'build-release-tool', cwd=source, timeout=600)
        self.command([self.release_tool, 'verify', '--source-commit', LOCK['monitorOp']['source'], '--bundle', self.release_bundle], 'verify-published-operator')
        # 使用 Docker 官方支持的本地 OCI build context，绕过 daemon 的远端 metadata 阻塞。
        from image_inputs import pinned_layout
        contexts={}
        for name in ('platform_node','platform_go'):
            print('校验锁定 OCI 构建输入：'+name,flush=True)
            spec=LOCK['images'][name]
            cache=ROOT/'.scratch/oci-inputs'/(name+'-'+spec['indexDigest'][7:19]+'-'+self.platform.split('/')[1])
            contexts[name]=pinned_layout(spec,self.platform.split('/')[1],cache)
        platform_file=(ROOT/'Dockerfile').read_text()
        for name in ('platform_node','platform_go'):
            assert 'FROM '+LOCK['images'][name]['image'] in platform_file
            platform_file=platform_file.replace('FROM '+LOCK['images'][name]['image'],'FROM '+name)
        platform_path=self.private/'platform.Dockerfile';platform_path.write_text(platform_file)
        build=['docker','buildx','build','--load','--platform',self.platform,'--provenance=false','--sbom=false']
        print('构建同候选平台；Operator 使用已校验原发布品',flush=True)
        self.command([*build,'--build-context','platform_node='+contexts['platform_node'],'--build-context','platform_go='+contexts['platform_go'],'-f',platform_path,'--build-arg','SOURCE_SHA='+self.candidate,'-t',self.image,ROOT],'build-platform',timeout=1200)
        proof=json.loads(self.command(['docker','image','inspect',self.image],'platform-image-source').stdout)[0]
        assert proof['Config']['Labels']['org.opencontainers.image.revision']==self.candidate and proof['Architecture']==self.platform.split('/')[1]
        self.platform_config=self.built_config(self.image,self.candidate,proof,'platform-image')
        private_json(self.public/'images.json',dict(candidate=self.candidate,platformImage=self.image,configDigest=self.platform_config,platform=self.platform,operatorSource=LOCK['monitorOp']['source'],operatorRelease=self.release_proof))

    def load_images(self):
        for role in self.targets:
            self.command(['kind', 'load', 'docker-image', '--name', self.targets[role]['name'], self.image], 'load-local-images-' + role, timeout=600)
            # 每个节点保留锁定的官方 index 和版本标签；Pod 实际使用同一固定镜像。
            for node in self.targets[role]['nodes']:
                for name in ('ksm', 'prometheus', 'promop', 'reloader', 'python_fixture', 'provisioner'):
                    image = LOCK['images'][name]
                    self.command(['docker', 'exec', node['metadata']['name'], 'ctr', '-n', 'k8s.io', 'images', 'pull', '--platform', self.platform, image['image']], 'pull-' + name, timeout=600)
                    self.command(['docker', 'exec', node['metadata']['name'], 'ctr', '-n', 'k8s.io', 'images', 'tag', '--force', image['image'], image['tag']], 'tag-' + name)
                    if name not in self.official_configs:self.official_configs[name]=self.runtime_config(name,node['metadata']['name'])
                self.command(['docker', 'exec', node['metadata']['name'], 'ctr', '-n', 'k8s.io', 'images', 'pull', '--platform', self.platform, self.operator_image], 'pull-published-operator', timeout=600)
                self.operator_content.append(self.published_operator_content(node['metadata']['name']))

    def published_operator_content(self, node):
        """关键：原 index→目标 manifest→原 config 及 revision 共同绑定已发布 RC4。"""
        from image_provenance import read_oci_graph
        spec = LOCK['monitorOp']['release']['image']
        def read(digest):
            return self.command(['docker', 'exec', node, 'ctr', '-n', 'k8s.io', 'content', 'get', digest], 'operator-content').stdout
        manifest = json.loads(read(spec['platforms'][self.platform]))
        config = manifest['config']['digest']
        proof = read_oci_graph(read, spec['indexDigest'], expected_config=config, expected_source=LOCK['monitorOp']['source'], platform=self.platform, require_manifest=True)
        assert proof['manifestDigest'] == spec['platforms'][self.platform]
        if hasattr(self, 'operator_config'):
            assert self.operator_config == config
        self.operator_config = config
        return dict(node=node, **proof)

    def operators(self):
        bundle = self.private / 'bundle.yaml'
        self.command(['curl', '-fsSL', '--proto', '=https', '-o', bundle, LOCK['prometheusOperator']['bundleURL']], 'fetch-operator-bundle', timeout=180)
        assert hashlib.sha256(bundle.read_bytes()).hexdigest() == LOCK['prometheusOperator']['bundleSHA256']
        # 官方 CRD 中裸 '=' 是标准 YAML tag，只为此补安全字符串构造。
        class BundleLoader(yaml.SafeLoader): pass
        BundleLoader.add_constructor('tag:yaml.org,2002:value', yaml.SafeLoader.construct_scalar)
        objects = [o for o in yaml.load_all(bundle.read_text(), Loader=BundleLoader) if o]
        namespace = 'mm-operators'
        for obj in objects:
            metadata = obj['metadata']; metadata.setdefault('labels', {})[RUN_LABEL] = self.run_id
            if metadata.get('namespace') == 'default' or obj['kind'] in ('ServiceAccount', 'Deployment', 'Service'):
                metadata['namespace'] = namespace
            if obj['kind'] == 'ClusterRoleBinding':
                for subject in obj.get('subjects', []):
                    if subject.get('kind') == 'ServiceAccount': subject['namespace'] = namespace
            if obj['kind'] == 'Deployment':
                container = obj['spec']['template']['spec']['containers'][0]
                container['image'] = LOCK['images']['promop']['image']; container['imagePullPolicy'] = 'Never'
                container['args'] = [a for a in container.get('args', []) if not a.startswith('--prometheus-config-reloader=')] + ['--prometheus-config-reloader=' + LOCK['images']['reloader']['image']]
        path = self.private / 'prometheus-operator-values.json'; private_json(path, {'objects': objects})
        prerequisites = self.private / 'prometheus-operator-prerequisites.json'
        private_json(prerequisites, {'objects': [o for o in objects if o['kind'] != 'Deployment']})
        crds = ['crd/' + o['metadata']['name'] for o in objects if o['kind'] == 'CustomResourceDefinition']
        deployment = next(o for o in objects if o['kind'] == 'Deployment' and o['metadata']['name'] == 'prometheus-operator')
        identity = 'system:serviceaccount:' + namespace + ':' + deployment['spec']['template']['spec']['serviceAccountName']
        preparation = []; self.operator_deployments = {}
        for role, target in self.targets.items():
            helm = ['helm', 'upgrade', '--install', 'operators', ROOT / 'charts/test-operators', '--kubeconfig', target['kubeconfig'], '--kube-context', target['context'], '--namespace', namespace, '--create-namespace']
            # 关键：该上游进程启动时判定一次能力；先让原 SA 的 CRD/discovery/RBAC 就绪。
            self.command([*helm, '-f', prerequisites, '--wait', '--timeout', '5m'], 'helm-prometheus-prerequisites-' + role, timeout=360)
            self.k(role, 'wait', '--for=condition=Established', *crds, '--timeout=90s')
            discovery = json.loads(self.k(role, 'get', '--raw', '/apis/monitoring.coreos.com/v1alpha1', '--as', identity).stdout)
            required = {'prometheusagents', 'scrapeconfigs'}
            assert required <= {r['name'] for r in discovery['resources']}
            for resource in sorted(required):
                for verb in ('get', 'list', 'watch'):
                    assert self.k(role, 'auth', 'can-i', verb, resource + '.monitoring.coreos.com', '--all-namespaces', '--as', identity).stdout.decode().strip() == 'yes'
                actual = json.loads(self.k(role, 'get', '--raw', '/apis/monitoring.coreos.com/v1alpha1/' + resource, '--as', identity).stdout)
                assert isinstance(actual.get('items'), list)
            self.command([*helm, '-f', path, '--wait', '--timeout', '5m'], 'helm-prometheus-operator-' + role, timeout=360)
            preparation.append(dict(target=role, allCRDsEstablished=True, discoveryServed=True, agentAndScrapeConfigPermissions=True))
            # 关键：使用同源发布 CLI 的 UID/RV 条件升级和原 tgz；不重构镜像、不改包。
            from operator_release import bundle_proof
            bundle_proof(self.release_bundle)
            crd_name = 'kubestatemetrics.monitoring.fjsxy.github.io'
            before = self.k(role, 'get', 'crd', crd_name, '--ignore-not-found', '-o', 'json').stdout.strip()
            release = 'monitor-mgr'
            env = dict(os.environ, KUBECONFIG=target['kubeconfig'])
            self.command([self.release_tool, 'upgrade', '--source-commit', LOCK['monitorOp']['source'], '--bundle', self.release_bundle,
                          '--context', target['context'], '--namespace', namespace, '--release', release], 'install-published-operator-' + role, timeout=360, env=env)
            self.k(role, 'wait', '--for=condition=Established', 'crd/' + crd_name, '--timeout=90s')
            deployed = self.get(role, 'crd', crd_name)
            if before: assert deployed['metadata']['uid'] == json.loads(before)['metadata']['uid']
            schema = self.private / ('deployed-crd-' + role + '.json'); private_json(schema, deployed)
            self.command([self.release_tool, 'capability', '--file', schema], 'deployed-operator-capability-' + role)
            matching = self.get(role, 'deployments', '-n', namespace, '-l', 'app.kubernetes.io/name=monitor-in-hand,app.kubernetes.io/instance=' + release)['items']
            assert len(matching) == 1
            installed = matching[0]; name = installed['metadata']['name']
            assert installed['spec']['template']['spec']['containers'][0]['image'] == self.operator_image
            service_account = installed['spec']['template']['spec']['serviceAccountName']
            self.operator_deployments[role] = dict(name=name, namespace=namespace, identity='system:serviceaccount:' + namespace + ':' + service_account, crdUID=deployed['metadata']['uid'])
            self.k(role, 'set', 'image', 'deployment/local-path-provisioner', '-n', 'local-path-storage', 'local-path-provisioner=' + LOCK['images']['provisioner']['image'])
            self.k(role, 'rollout', 'status', 'deployment/local-path-provisioner', '-n', 'local-path-storage', '--timeout=120s')
            for deployment_name, config in [('prometheus-operator', self.official_configs['promop']), (name, self.operator_config)]:
                dep = self.get(role, 'deployment', deployment_name, '-n', namespace)
                selector = ','.join(k + '=' + v for k, v in sorted(dep['spec']['selector']['matchLabels'].items()))
                pods = [p for p in self.get(role, 'pods', '-n', namespace, '-l', selector)['items'] if not p['metadata'].get('deletionTimestamp')]
                assert len(pods) == 1
                proof = self.verify_container(pods[0], dep['spec']['template']['spec']['containers'][0]['name'], config)
                self.operator_proofs.append(dict(target=role, deployment=deployment_name, source=LOCK['monitorOp']['source'] if deployment_name == name else LOCK['prometheusOperator']['source'], **proof))
        report = json.loads((self.public / 'images.json').read_text())
        report.update(lockedRuntimeConfigs=self.official_configs, operatorConfigDigest=self.operator_config, operatorContent=self.operator_content,
                      operators=self.operator_proofs, operatorPreparation=preparation, operatorDeployments=self.operator_deployments)
        private_json(self.public / 'images.json', report)


    def cleanup_clusters(self):
        failures = []
        for role, target in reversed(list(self.targets.items())):
            try:
                # 关键：顺序删除，先核容器完整身份，再核可读取的原 cluster UID。
                actual = self.command(['docker', 'ps', '-a', '--no-trunc', '--filter', 'label=io.x-k8s.kind.cluster=' + target['name'], '--format', '{{.ID}}'], 'cleanup-container-identity').stdout.decode().splitlines()
                if not actual: continue
                if set(actual) != set(target['containers']): raise RuntimeError('集群容器身份变化')
                if target.get('clusterUID'):
                    uid = self.get(role, 'namespace', 'kube-system')['metadata']['uid']
                    if not owned_identity(self.run_id, {'uid': target['clusterUID'], 'runId': self.run_id}, {'uid': uid, 'runId': self.run_id}): raise RuntimeError('集群 UID 变化')
                self.command(['kind', 'delete', 'cluster', '--name', target['name'], '--kubeconfig', target['kubeconfig']], 'delete-owned-' + role, timeout=240)
            except Exception as error: failures.append(dict(target=role, error=str(error)))
        private_json(self.public / 'cleanup.json', dict(success=not failures, failures=failures))
        return failures
