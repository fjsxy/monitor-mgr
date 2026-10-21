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
from transport import ProcessRunner

ROOT = Path(__file__).resolve().parents[1]
LOCK = json.loads((ROOT / 'scripts/acceptance/source-lock.json').read_text())
RUN_LABEL = 'monitor-mgr.test/run'


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
        self.operator_image = 'monitor-mgr-operator:e2e-' + LOCK['monitorOp']['source'][:12]

    def command(self, command, label='command', **kwargs):
        result = self.processes.run([str(v) for v in command], label=label, **kwargs)
        if result.returncode:
            raise RuntimeError(label + ': exit=' + str(result.returncode) + '；原始输出见私有 processes')
        return result

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

    def build(self):
        if not self.source:
            common = Path(self.command(['git', 'rev-parse', '--path-format=absolute', '--git-common-dir'], 'git-common', cwd=ROOT).stdout.decode().strip())
            self.source = common.parent.parent / 'monitor-op'
        source = self.private / 'monitor-op'
        self.command(['git', 'clone', '--quiet', '--no-hardlinks', '--no-checkout', self.source, source], 'clone-locked-operator', timeout=300)
        self.command(['git', '-C', source, 'checkout', '--quiet', '--detach', LOCK['monitorOp']['source']], 'checkout-locked-operator')
        assert self.command(['git', '-C', source, 'rev-parse', 'HEAD'], 'operator-sha').stdout.decode().strip() == LOCK['monitorOp']['source']
        self.operator_source = source
        dockerfile = (source / 'Dockerfile').read_text()
        assert hashlib.sha256(dockerfile.encode()).hexdigest() == LOCK['monitorOp']['dockerfileSHA256']
        # 使用 Docker 官方支持的本地 OCI build context，绕过 daemon 的远端 metadata 阻塞。
        from image_inputs import pinned_layout
        contexts={}
        for name in ('platform_node','platform_go','monitorop_go'):
            print('校验锁定 OCI 构建输入：'+name,flush=True)
            spec=LOCK['images'][name]
            cache=ROOT/'.scratch/oci-inputs'/(name+'-'+spec['indexDigest'][7:19]+'-'+self.platform.split('/')[1])
            contexts[name]=pinned_layout(spec,self.platform.split('/')[1],cache)
        platform_file=(ROOT/'Dockerfile').read_text()
        for name in ('platform_node','platform_go'):
            assert 'FROM '+LOCK['images'][name]['image'] in platform_file
            platform_file=platform_file.replace('FROM '+LOCK['images'][name]['image'],'FROM '+name)
        platform_path=self.private/'platform.Dockerfile';platform_path.write_text(platform_file)
        dockerfile=dockerfile.replace('FROM golang:1.22.12-alpine AS builder','FROM monitorop_go AS builder').replace('FROM gcr.io/distroless/static:nonroot','FROM '+LOCK['images']['monitorop_runtime']['image'])
        dockerfile+='\nLABEL org.opencontainers.image.revision="'+LOCK['monitorOp']['source']+'"\n'
        path=self.private/'operator.Dockerfile';path.write_text(dockerfile)
        build=['docker','build','--platform',self.platform,'--provenance=false','--sbom=false']
        print('构建同候选平台和锁定 Operator 镜像',flush=True)
        self.command([*build,'--build-context','platform_node='+contexts['platform_node'],'--build-context','platform_go='+contexts['platform_go'],'-f',platform_path,'--build-arg','SOURCE_SHA='+self.candidate,'-t',self.image,ROOT],'build-platform',timeout=1200)
        self.command([*build,'--build-context','monitorop_go='+contexts['monitorop_go'],'-f',path,'-t',self.operator_image,source],'build-operator',timeout=1200)
        proof=json.loads(self.command(['docker','image','inspect',self.image],'platform-image-source').stdout)[0]
        assert proof['Config']['Labels']['org.opencontainers.image.revision']==self.candidate and proof['Architecture']==self.platform.split('/')[1]
        self.platform_config=self.built_config(self.image,self.candidate,proof,'platform-image')
        operator=json.loads(self.command(['docker','image','inspect',self.operator_image],'operator-image-source').stdout)[0]
        assert operator['Config']['Labels']['org.opencontainers.image.revision']==LOCK['monitorOp']['source'];self.operator_config=self.built_config(self.operator_image,LOCK['monitorOp']['source'],operator,'operator-image')
        private_json(self.public/'images.json',dict(candidate=self.candidate,platformImage=self.image,configDigest=self.platform_config,platform=self.platform,operatorSource=LOCK['monitorOp']['source'],operatorConfigDigest=self.operator_config))

    def load_images(self):
        for role in self.targets:
            self.command(['kind', 'load', 'docker-image', '--name', self.targets[role]['name'], self.image, self.operator_image], 'load-local-images-' + role, timeout=600)
            # 每个节点保留锁定的官方 index 和版本标签；Pod 实际使用同一固定镜像。
            for node in self.targets[role]['nodes']:
                for name in ('ksm', 'prometheus', 'promop', 'reloader', 'python_fixture', 'provisioner'):
                    image = LOCK['images'][name]
                    self.command(['docker', 'exec', node['metadata']['name'], 'ctr', '-n', 'k8s.io', 'images', 'pull', '--platform', self.platform, image['image']], 'pull-' + name, timeout=600)
                    self.command(['docker', 'exec', node['metadata']['name'], 'ctr', '-n', 'k8s.io', 'images', 'tag', '--force', image['image'], image['tag']], 'tag-' + name)
                    if name not in self.official_configs:self.official_configs[name]=self.runtime_config(name,node['metadata']['name'])

    def operators(self):
        bundle = self.private / 'bundle.yaml'
        self.command(['curl', '-fsSL', '--proto', '=https', '-o', bundle, LOCK['prometheusOperator']['bundleURL']], 'fetch-operator-bundle', timeout=180)
        assert hashlib.sha256(bundle.read_bytes()).hexdigest() == LOCK['prometheusOperator']['bundleSHA256']
        crd = self.operator_source / 'config/crd/bases/monitoring.fjsxy.github.io_kubestatemetrics.yaml'
        assert hashlib.sha256(crd.read_bytes()).hexdigest() == LOCK['monitorOp']['crdSHA256']
        # 官方 CRD schema 中的裸 '=' 具有 YAML 1.1 value tag；仅为该标准 tag 补字符串构造。
        class BundleLoader(yaml.SafeLoader): pass
        BundleLoader.add_constructor('tag:yaml.org,2002:value', yaml.SafeLoader.construct_scalar)
        objects = list(yaml.load_all(bundle.read_text(), Loader=BundleLoader))
        namespace = 'mm-operators'; name = 'monitor-mgr-monitor-op'
        for obj in objects:
            if not obj: continue
            metadata = obj['metadata']; metadata.setdefault('labels', {})[RUN_LABEL] = self.run_id
            if metadata.get('namespace') == 'default' or obj['kind'] in ('ServiceAccount', 'Deployment', 'Service'): metadata['namespace'] = namespace
            if obj['kind'] == 'ClusterRoleBinding':
                for subject in obj.get('subjects', []):
                    if subject.get('kind') == 'ServiceAccount': subject['namespace'] = namespace
            if obj['kind'] == 'Deployment':
                c = obj['spec']['template']['spec']['containers'][0]; c['image'] = LOCK['images']['promop']['image']; c['imagePullPolicy'] = 'Never'
                c['args'] = [a for a in c.get('args', []) if not a.startswith('--prometheus-config-reloader=')] + ['--prometheus-config-reloader=' + LOCK['images']['reloader']['image']]
        role = yaml.safe_load((self.operator_source / 'config/rbac/role.yaml').read_text()); role['metadata'] = {'name': name}
        objects += [yaml.safe_load(crd.read_text()), role,
                    {'apiVersion': 'v1', 'kind': 'ServiceAccount', 'metadata': {'name': name, 'namespace': namespace}},
                    {'apiVersion': 'rbac.authorization.k8s.io/v1', 'kind': 'ClusterRoleBinding', 'metadata': {'name': name}, 'roleRef': {'apiGroup': 'rbac.authorization.k8s.io', 'kind': 'ClusterRole', 'name': name}, 'subjects': [{'kind': 'ServiceAccount', 'name': name, 'namespace': namespace}]},
                    {'apiVersion': 'rbac.authorization.k8s.io/v1', 'kind': 'Role', 'metadata': {'name': name, 'namespace': namespace}, 'rules': [{'apiGroups': ['coordination.k8s.io'], 'resources': ['leases'], 'verbs': ['get', 'list', 'watch', 'create', 'update', 'patch']}, {'apiGroups': [''], 'resources': ['events'], 'verbs': ['create', 'patch']}]},
                    {'apiVersion': 'rbac.authorization.k8s.io/v1', 'kind': 'RoleBinding', 'metadata': {'name': name, 'namespace': namespace}, 'roleRef': {'apiGroup': 'rbac.authorization.k8s.io', 'kind': 'Role', 'name': name}, 'subjects': [{'kind': 'ServiceAccount', 'name': name, 'namespace': namespace}]},
                    {'apiVersion': 'apps/v1', 'kind': 'Deployment', 'metadata': {'name': name, 'namespace': namespace}, 'spec': {'replicas': 1, 'selector': {'matchLabels': {'app': name}}, 'template': {'metadata': {'labels': {'app': name}}, 'spec': {'serviceAccountName': name, 'containers': [{'name': 'manager', 'image': self.operator_image, 'imagePullPolicy': 'Never', 'args': ['--leader-elect=true', '--health-probe-bind-address=:8081', '--log-level=info'], 'readinessProbe': {'httpGet': {'path': '/readyz', 'port': 8081}}}]}}}}]
        path = self.private / 'operator-values.json'; private_json(path, {'objects': [o for o in objects if o]})
        for role, target in self.targets.items():
            print('Helm 安装 Operator：' + role, flush=True)
            self.command(['helm', 'upgrade', '--install', 'operators', ROOT / 'charts/test-operators', '--kubeconfig', target['kubeconfig'], '--kube-context', target['context'], '--namespace', namespace, '--create-namespace', '-f', path, '--wait', '--timeout', '5m'], 'helm-operators-' + role, timeout=360)
            self.k(role, 'wait', '--for=condition=Established', 'crd/kubestatemetrics.monitoring.fjsxy.github.io', '--timeout=60s')
            self.k(role,'set','image','deployment/local-path-provisioner','-n','local-path-storage','local-path-provisioner='+LOCK['images']['provisioner']['image'])
            self.k(role,'rollout','status','deployment/local-path-provisioner','-n','local-path-storage','--timeout=120s')
            for deployment,config in [('prometheus-operator',self.official_configs['promop']),(name,self.operator_config)]:
                dep=self.get(role,'deployment',deployment,'-n',namespace)
                selector=','.join(k+'='+v for k,v in sorted(dep['spec']['selector']['matchLabels'].items()))
                pods=[p for p in self.get(role,'pods','-n',namespace,'-l',selector)['items'] if not p['metadata'].get('deletionTimestamp')]
                assert len(pods)==1
                proof=self.verify_container(pods[0],dep['spec']['template']['spec']['containers'][0]['name'],config)
                self.operator_proofs.append(dict(target=role,deployment=deployment,**proof))
        report=json.loads((self.public/'images.json').read_text());report.update(lockedRuntimeConfigs=self.official_configs,operators=self.operator_proofs);private_json(self.public/'images.json',report)


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
