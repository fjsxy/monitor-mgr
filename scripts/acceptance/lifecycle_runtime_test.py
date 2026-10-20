"""P14真实caller与运行身份校验的Host回归；合成原字节，不依赖外部运行产物。"""
import contextlib
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import lifecycle
from image_provenance import ImageProofError


class ReachedDeletionBoundary(Exception):
    """只标记已通过fixture身份检查，不把Host模拟计作P14业务通过。"""


class P14RuntimeBoundary(unittest.TestCase):
    @contextlib.contextmanager
    def fixture(self, image_kind='index'):
        """系统边界返回可复现OCI/CRI原字节，运行真实run_case和既有validator。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = json.dumps({'os': 'linux', 'architecture': 'arm64', 'config': {}}).encode()
            config_id = 'sha256:' + hashlib.sha256(config).hexdigest()
            manifest = json.dumps({'schemaVersion': 2, 'config': {'digest': config_id,
                'size': len(config)}, 'layers': []}).encode()
            manifest_id = 'sha256:' + hashlib.sha256(manifest).hexdigest()
            index = json.dumps({'schemaVersion': 2, 'manifests': [{'digest': manifest_id,
                'size': len(manifest), 'platform': {'os': 'linux', 'architecture': 'arm64'}}]}).encode()
            index_id = 'sha256:' + hashlib.sha256(index).hexdigest()
            content = {config_id: config, manifest_id: manifest, index_id: index}
            image = 'docker.io/library/python@' + index_id
            lock = {'images': {'python_fixture': {'image': image,
                'tag': 'docker.io/library/python:3.13.2-alpine', 'indexDigest': index_id,
                'arm64Manifest': manifest_id, 'arm64Config': config_id}}}
            lock_path = root / 'scripts/acceptance/source-lock.json'
            lock_path.parent.mkdir(parents=True)
            lock_path.write_text(json.dumps(lock))
            private = root / 'prepared'
            private.mkdir()
            refs = []
            for name, digest in [('index', index_id), ('manifest', manifest_id), ('config', config_id)]:
                path = private / (name + '.raw.json')
                path.write_bytes(content[digest])
                refs.append({'path': str(path), 'sha256': digest[7:]})
            node_id = 'd' * 64
            container_id = 'c' * 64
            pod_uid = '00000000-0000-0000-0000-000000000012'
            node_uid = '00000000-0000-0000-0000-000000000011'
            selected_id = {'index': index_id, 'manifest': manifest_id}[image_kind]
            proof = {'image': image, 'indexDigest': index_id, 'manifestDigest': manifest_id,
                'configDigest': config_id, 'platform': 'linux/arm64', 'evidenceRefs': refs}
            descriptor = {'platform': 'linux/arm64', 'privateRoot': str(private),
                'officialContent': {'python_fixture': proof}, 'targets': {'control': {
                    'containers': [{'name': 'node0', 'id': node_id}],
                    'nodeTopology': [{'name': 'node0', 'uid': node_uid}]}}}
            pod = {'metadata': {'name': 'residual', 'namespace': 'owned-moved', 'uid': pod_uid},
                'spec': {'nodeName': 'node0', 'containers': [{'name': 'residual', 'image': image}]},
                'status': {'conditions': [{'type': 'Ready', 'status': 'True'}], 'containerStatuses': [{
                    'name': 'residual', 'ready': True, 'imageID': 'repository@' + selected_id,
                    'containerID': 'containerd://' + container_id}]}}
            node = {'metadata': {'name': 'node0', 'uid': node_uid},
                'status': {'nodeInfo': {'operatingSystem': 'linux', 'architecture': 'arm64'}}}
            actual = {'status': {'id': container_id, 'metadata': {'name': 'residual'},
                'labels': {'io.kubernetes.pod.uid': pod_uid}, 'state': 'CONTAINER_RUNNING',
                'imageRef': 'repository@' + selected_id}, 'info': {'config': {'image': {'image': config_id}}}}
            state = SimpleNamespace(pod=pod, pods=[pod], node=node, actual=actual, content=content,
                descriptor=descriptor, lock=lock, config_id=config_id, node_id=node_id,
                created_image=None, runtime_calls=[], reached=False)
            suite = lifecycle.Suite.__new__(lifecycle.Suite)
            suite.a = SimpleNamespace(prefix='host-p14')
            suite.target = 'control'
            suite.targetns = 'owned'
            suite.crname = 'fixture-ksm'
            suite.extra_namespaces = []
            suite.cross_namespace_fixture = None
            suite.out = root / 'case'
            suite.out.mkdir()
            suite.session = SimpleNamespace(environment_descriptor=descriptor)
            suite.first = lambda: ({'id': 'fixture-instance'}, {'operationId': 'fixture-publication'})
            suite.cr = lambda: {'apiVersion': 'monitoring.fjsxy.github.io/v1alpha1',
                'kind': 'KubeStateMetrics', 'metadata': {'uid': 'fixture-original-cr'}}

            def k(*args, **kwargs):
                if 'apply' in args:
                    state.created_image = kwargs['data']['spec']['template']['spec']['containers'][0]['image']
                    pod['spec']['containers'][0]['image'] = state.created_image
                value = {'items': state.pods} if 'get' in args and 'pods' in args else (
                    node if 'get' in args and 'node' in args else {})
                return subprocess.CompletedProcess(args, 0, json.dumps(value).encode(), b'')

            def captured(command, **kwargs):
                state.runtime_calls.append(command)
                if command[:2] == ['docker', 'inspect']:
                    data = (state.node_id + '\n').encode()
                else:
                    self.assertEqual(command[:3], ['docker', 'exec', node_id])
                    data = (json.dumps(actual).encode() if command[3:5] == ['crictl', 'inspect']
                            else state.content.get(command[-1], b''))
                return subprocess.CompletedProcess(command, 0, data, b'')

            def next_boundary():
                state.reached = True
                raise ReachedDeletionBoundary()

            suite.k = k
            suite.run_captured = captured
            suite.setup_proxy = next_boundary
            with patch.object(lifecycle, 'ROOT', root):
                yield suite, state

    def test_actual_index_identity_reaches_deletion_only_after_full_runtime_chain_verification(self):
        """合法index/manifest都需真实CRI与原字节链；证明及typed原件均可回读。"""
        for image_kind in ['index', 'manifest']:
            with self.subTest(image_kind=image_kind), self.fixture(image_kind) as (suite, state):
                with self.assertRaises(ReachedDeletionBoundary):
                    suite.run_case('P14.pod-remains')
                self.assertEqual(state.created_image, state.lock['images']['python_fixture']['image'])
                evidence = suite.cross_namespace_fixture
                proof = evidence['runtimeImageProof']
                self.assertTrue(proof['actualRuntimeConfigVerified'])
                self.assertTrue(proof['originalContentBytesVerified'])
                self.assertEqual(proof['actualConfigDigest'], state.config_id)
                self.assertTrue(state.runtime_calls)
                self.assertEqual(sorted(r['kind'] for r in evidence['evidenceRefs']),
                                 ['image-config', 'image-manifest', 'image-manifest'])
                for ref in [*evidence['evidenceRefs'], evidence['runtimeImageRef']]:
                    data = (suite.out / ref['path']).read_bytes()
                    self.assertEqual(hashlib.sha256(data).hexdigest(), ref['sha256'])
                    self.assertEqual(ref['visibility'], 'public')
                self.assertEqual(json.loads((suite.out / evidence['runtimeImageRef']['path']).read_bytes()), proof)

    def test_runtime_rejects_wrong_config_pod_container_node_platform_and_missing_proof(self):
        """当前Pod的CRI身份与原OCI链缺一不可，不能仅凭index或准备声明放行。"""
        modes = ['config', 'pod-uid', 'container-id', 'container-name', 'node-uid', 'node-id',
                 'node-platform', 'not-running', 'not-ready', 'image-id', 'missing-proof', 'missing-content']
        for mode in modes:
            with self.subTest(mode=mode), self.fixture() as (suite, state):
                if mode == 'config':
                    state.actual['info']['config']['image']['image'] = 'sha256:' + 'f' * 64
                elif mode == 'pod-uid':
                    state.actual['status']['labels']['io.kubernetes.pod.uid'] = 'foreign-pod'
                elif mode == 'container-id':
                    state.actual['status']['id'] = 'f' * 64
                elif mode == 'container-name':
                    state.actual['status']['metadata']['name'] = 'another-container'
                elif mode == 'node-uid':
                    state.node['metadata']['uid'] = 'replacement-node'
                elif mode == 'node-id':
                    state.node_id = 'e' * 64
                elif mode == 'node-platform':
                    state.node['status']['nodeInfo']['architecture'] = 'amd64'
                elif mode == 'not-running':
                    state.actual['status']['state'] = 'CONTAINER_EXITED'
                elif mode == 'not-ready':
                    state.pod['status']['containerStatuses'][0]['ready'] = False
                elif mode == 'image-id':
                    value = 'repository@sha256:' + 'f' * 64
                    state.pod['status']['containerStatuses'][0]['imageID'] = value
                    state.actual['status']['imageRef'] = value
                elif mode == 'missing-proof':
                    state.descriptor['officialContent'] = {}
                elif mode == 'missing-content':
                    state.content.pop(state.config_id)
                with self.assertRaises(ImageProofError):
                    suite.run_case('P14.pod-remains')
                self.assertFalse(state.reached)
                self.assertIsNone(suite.cross_namespace_fixture)

    def test_original_single_pod_requirement_is_still_enforced_before_runtime_reads(self):
        """原单Pod计数约束不能因补运行身份检查而被跳过。"""
        for count in [0, 2]:
            with self.subTest(count=count), self.fixture() as (suite, state):
                state.pods = [copy.deepcopy(state.pod) for _ in range(count)]
                with self.assertRaises(AssertionError):
                    suite.run_case('P14.pod-remains')
                self.assertFalse(state.runtime_calls)
                self.assertFalse(state.reached)

    def test_runtime_without_original_typed_metadata_cannot_advance(self):
        """实际运行校验后仍须封存原typed OCI证明，不能只留下verified布尔值。"""
        with self.fixture() as (suite, state):
            state.descriptor['officialContent']['python_fixture']['evidenceRefs'] = []
            with self.assertRaises(AssertionError):
                suite.run_case('P14.pod-remains')
            self.assertTrue(state.runtime_calls)
            self.assertFalse(state.reached)
            self.assertIsNone(suite.cross_namespace_fixture)


if __name__ == '__main__':
    unittest.main()
