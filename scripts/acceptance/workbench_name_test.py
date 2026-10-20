#!/usr/bin/env python3
"""REG21-U05-FIXTURE-001：真实prepare→创建/发布/runtime读取的名称接缝；不运行集群或浏览器。"""
from types import SimpleNamespace
import unittest

from agent import Suite as AgentSuite
from workbench_browser import prepare


class StopAtStatefulRead(Exception):
    """在明确的Kubernetes读取系统边界停止，不伪造ST/Pod/PVC就绪。"""


class ControlledSuite(AgentSuite):
    def __init__(self, prefix):
        self.a = SimpleNamespace(prefix=prefix)
        self.target_names = ['control']
        self.base = 'http://host-fixture.invalid'
        self.password = 'host-only-placeholder'
        self.created_names, self.cr_reads = [], []

    def fixture(self, namespace):
        self.fixture_namespace = namespace

    def use_proxy(self):
        return SimpleNamespace(config='host-only', base='http://host-only.invalid', token='host-only')

    def register(self, target, config=None):
        return {'id': 'original-cluster', 'name': 'host-only'}

    def new_ksm(self, index, **kwargs):
        return {'instance': {'id': 'fixture-ksm'}, 'runtimeProof': {}, 'uid': 'original-ksm', 'name': 'fixture-ksm'}

    def call(self, method, path, body=None, status=200):
        if path.endswith('/instances'):
            self.created_names.append(body['agent']['name'])
            return {'id': 'fixture-agent'}
        if path.endswith('/draft'):
            return {'revision': 1}
        if path.endswith('/previews'):
            return {'draftRevision': 1, 'previewId': 'fixture-preview'}
        if path.endswith('/publications'):
            return {'operationId': 'fixture-operation'}
        if path.startswith('/operations/'):
            return {'state': 'SUCCEEDED'}
        if path.endswith('/refresh'):
            return {'summary': 'NotReady'}
        raise AssertionError('未声明的Host HTTP边界')

    def get(self, kind, *args, **kwargs):
        if kind == 'prometheusagent':
            self.cr_reads.append((args[0], 'original-agent-uid'))
            return {'metadata': {'name': args[0], 'uid': 'original-agent-uid'}}
        if kind == 'statefulsets':
            raise StopAtStatefulRead()
        raise AssertionError('未声明的Host Kubernetes边界')


class WorkbenchAgentFixtureName(unittest.TestCase):
    def test_original_long_prefix_reaches_create_and_runtime_with_bounded_distinct_identity(self):
        names = []
        for prefix, index in [('mm21-incremental-64f763f-local15-c13', 0),
                              ('mm21-incremental-64f763f-local15-c14', 0),
                              ('mm21-incremental-64f763f-local15-c13', 1)]:
            suite = ControlledSuite(prefix)
            with self.assertRaises(StopAtStatefulRead):
                prepare(suite, 'U05.primary', index)
            self.assertEqual(suite.created_names, [suite.crname])
            self.assertEqual(suite.cr_reads, [(suite.crname, 'original-agent-uid')])
            self.assertLessEqual(len(suite.crname), 41)
            self.assertRegex(suite.crname, r'^[a-z0-9]([-a-z0-9]*[a-z0-9])?$')
            names.append(suite.crname)
        self.assertEqual(len(set(names)), 3)
        repeated = ControlledSuite('mm21-incremental-64f763f-local15-c13')
        with self.assertRaises(StopAtStatefulRead):
            prepare(repeated, 'U05.primary', 0)
        self.assertEqual(repeated.crname, names[0])


if __name__ == '__main__':
    unittest.main()
