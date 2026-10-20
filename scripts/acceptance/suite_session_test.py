#!/usr/bin/env python3
"""旧业务 Suite 的 Host 协议测试；受控 API 响应不构成 K/B 验收。"""
import io
import http.server
import json
import pathlib
import subprocess
import tempfile
import threading
import types
import unittest
import urllib.error

from clusters import Suite
from transport import ACTIVE, ProcessRunner


class SessionFixture:
    def __init__(self, directory):
        self.events = []
        self.environment_descriptor = {'profile': 'CI_SMOKE', 'platform': 'linux/amd64', 'targets': {'control': {
            'context': 'kind-protocol-control', 'kubeconfig': str(pathlib.Path(directory) / 'control'),
            'clusterUID': 'original-control', 'nodeTopology': [
                {'name': 'node-' + str(i), 'uid': 'node-uid-' + str(i), 'role': 'control-plane' if i == 0 else 'worker'}
                for i in range(3)]}}}
        self.kube = self
        self.response = subprocess.CompletedProcess([], 17, b'original-response', b'private-error')
        self.browser_kubectl = '/private/protocol-kubectl'
        self.handler = None
        self.processes = ControlledProcesses(directory, self.events)

    def command(self, target, args, *, data=None, check=True):
        self.events.append(('kube', target, args, data, check))
        if self.handler:return self.handler(target, args, data, check)
        return self.response

    def register_secrets(self, *values):
        self.events.append(('secrets', values))

    def http_intent(self, suite, method, path, body):
        self.events.append(('intent', method, path, body))

    def http_response(self, suite, method, path, body, value, **kwargs):
        self.events.append(('response', method, path, body, value, kwargs['status']))

    def cleanup_case(self, suite):
        self.events.append(('cleanup', type(suite).__module__))
        return {'success': True, 'scope': 'session-protocol-fixture'}

    def platform_access(self, suite, base):
        self.events.append(('platform', base, suite.actualEndpoint.copy()))
        return base

    def fixture_gate_intent(self, suite, action, body):
        self.events.append(('gate-intent', action, body))

    def fixture_gate_response(self, suite, action, body, value):
        self.events.append(('gate-response', action, body, value))

    def image_metadata(self, image):
        return {'source': 'a' * 40, 'os': 'linux', 'architecture': 'amd64',
                'configDigest': 'sha256:' + 'b' * 64, 'manifestDigest': 'sha256:' + 'c' * 64,
                'configDigestMethod': 'protocol-only-no-product-gate'}

    def node_response(self, target, args, data, check):
        if args == ['get', 'nodes', '-o', 'json']:
            value = {'items': [{'metadata': {'name': item['name'], 'uid': item['uid'], 'labels':
                {'node-role.kubernetes.io/control-plane': ''} if item['role'] == 'control-plane' else {}},
                'spec': {}, 'status': {'nodeInfo': {'architecture': 'amd64', 'operatingSystem': 'linux',
                'kubeletVersion': 'v1.28.15'}}} for item in self.environment_descriptor['targets'][target]['nodeTopology']]}
        elif args == ['get', 'namespace', 'kube-system', '-o', 'json']:
            value = {'metadata': {'uid': 'original-control'}}
        else:raise AssertionError('Host fixture 拒绝未计划的 API 命令')
        return subprocess.CompletedProcess(args, 0, json.dumps(value).encode(), b'')


class ControlledProcesses:
    """只允许协议 git 响应和本例新生成的 openssl；绝不运行 Docker/kubectl。"""
    def __init__(self, directory, events):
        self.events = events
        self.actual = ProcessRunner(pathlib.Path(directory) / 'private-process')

    def run(self, argv, **kwargs):
        self.events.append(('process', argv))
        if argv[:2] == ['git', 'rev-parse']:
            value = ('d' if argv[-1].endswith('^{tree}') else 'a') * 40 + '\n'
        elif argv[:2] == ['git', 'status']:value = ''
        elif argv[0] == 'openssl':return self.actual.run(argv, **kwargs)
        else:raise AssertionError('Host fixture 拒绝未计划的外部进程')
        return subprocess.CompletedProcess(argv, 0, value if kwargs.get('text') else value.encode(), '' if kwargs.get('text') else b'')

    def popen(self, argv, **kwargs):
        self.events.append(('forward', argv))
        if 'port-forward' not in argv:raise AssertionError('Host fixture 仅接受记录 forward 参数')
        return types.SimpleNamespace(args=argv)


def arguments(directory, session):
    config = pathlib.Path(directory) / 'pod-config.json'
    config.write_text(json.dumps({'apiVersion': 'v1', 'clusters': [], 'users': [], 'contexts': [
        {'name': 'original', 'context': {'cluster': 'fixture', 'user': 'fixture'}}]}))
    return types.SimpleNamespace(session=session, output=str(pathlib.Path(directory) / 'out'),
        control_pod_config=str(config), data_pod_config=None, control_config='/private/control', data_config=None,
        control_context='kind-protocol-control', data_context=None, kubectl='NEVER-NATIVE-KUBECTL',
        profile='CI_SMOKE', image='protocol-image', source='a' * 40, allow_dirty_runner=False,
        allow_source_bridge=False, prefix='protocol-suite', port=18210)


class SuiteSessionProtocolTest(unittest.TestCase):
    def test_smoke_accepts_only_actual_control_and_delegates_failed_command(self):
        with tempfile.TemporaryDirectory() as directory:
            session = SessionFixture(directory)
            suite = Suite(arguments(directory, session))
            self.assertEqual(suite.target_names, ['control'])
            self.assertEqual(suite.runtime_arch, 'amd64')
            result = suite.k('get', 'pods', check=False)
            self.assertIs(result, session.response)
            self.assertEqual(result.returncode, 17)
            self.assertEqual(session.events[-1], ('kube', 'control', ['get', 'pods'], None, False))
            with self.assertRaisesRegex(ValueError, 'SUITE_TARGET_UNAVAILABLE'):
                suite.config('data')
            self.assertIn(suite.password, session.events[0][1])

    def test_non_initialization_case_cannot_skip_initialization(self):
        with tempfile.TemporaryDirectory() as directory:
            session = SessionFixture(directory);args = arguments(directory, session);args.initialize = False
            suite = Suite(args)
            with self.assertRaisesRegex(ValueError, 'SUITE_INITIALIZATION_SKIP_INVALID'):
                suite.deploy('C01.token', 0)
            self.assertFalse(any(event[0] == 'kube' for event in session.events))

    def test_actual_http_value_is_observed_before_original_status_assertion(self):
        with tempfile.TemporaryDirectory() as directory:
            session = SessionFixture(directory)
            suite = Suite(arguments(directory, session))
            suite.base = 'http://127.0.0.1:1'
            class Opener:
                def open(self, request, timeout):
                    session.events.append(('send', request.method))
                    raise urllib.error.HTTPError(request.full_url, 409, 'conflict', {},
                        io.BytesIO(b'{"code":"actual-conflict","message":"actual response"}'))
            suite.http = Opener()
            with self.assertRaises(AssertionError):
                suite.call('POST', '/instances', {'fixture': True}, status=201)
            self.assertEqual([event[0] for event in session.events[1:]], ['intent', 'send', 'response'])
            self.assertEqual(session.events[-1][4]['code'], 'actual-conflict')
            self.assertEqual(session.events[-1][5], 409)

    def test_base_cleanup_returns_session_result_before_legacy_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            session = SessionFixture(directory)
            suite = Suite(arguments(directory, session))
            self.assertTrue(suite.cleanup()['success'])
            self.assertEqual(session.events[-1][0], 'cleanup')
            self.assertFalse(any(event[0] == 'kube' for event in session.events))

    def test_smoke_token_case_uses_real_control_target_without_data_alias(self):
        with tempfile.TemporaryDirectory() as directory:
            session = SessionFixture(directory);suite = Suite(arguments(directory, session));suite.owner = 'protocol-owner'
            suite.base = 'http://127.0.0.1:1'
            def response(target, args, data, check):
                output = b'protocol-token' if 'token' in args else b'{}'
                return subprocess.CompletedProcess(args, 0, output, b'')
            session.handler = response
            class Opener:
                def open(self, request, timeout):
                    if request.method == 'POST':value, code = {'operationId': 'protocol-op'}, 202
                    elif '/operations/' in request.full_url:value, code = {'state': 'SUCCEEDED', 'clusterId': 'protocol-cluster'}, 200
                    else:value, code = {'identity': 'system:serviceaccount:protocol-suite-token:verifier'}, 200
                    result = io.BytesIO(json.dumps(value).encode());result.code = code;result.headers = {}
                    return result
            suite.http = Opener();value = suite.run_case('C01.token')
            self.assertEqual(value['cluster']['identity'], 'system:serviceaccount:protocol-suite-token:verifier')
            self.assertEqual(suite.token_target, 'control')
            self.assertTrue(all(event[1] == 'control' for event in session.events if event[0] == 'kube'))
            self.assertFalse(hasattr(suite, 'data'))

    def test_all_inherited_constructors_accept_smoke_and_cleanup_stops_before_legacy_restore(self):
        from agent import Suite as AgentSuite
        from ksm import Suite as KSMSuite
        from lifecycle import Suite as LifecycleSuite
        from workbench_cases import Suite as WorkbenchSuite
        from agent_storage import Suite as StorageSuite
        for kind in [AgentSuite, KSMSuite, LifecycleSuite, WorkbenchSuite, StorageSuite]:
            with self.subTest(suite=kind.__module__), tempfile.TemporaryDirectory() as directory:
                session = SessionFixture(directory);session.handler = session.node_response
                token = ACTIVE.set(session)
                try:
                    suite = kind(arguments(directory, session))
                    self.assertEqual(set(suite.environment), {'control'})
                    self.assertEqual(suite.image_metadata['architecture'], 'amd64')
                    self.assertEqual(suite.image_metadata['configID'], 'sha256:' + 'b' * 64)
                    def forbidden():raise AssertionError('Session 清理不能先调用旧 restore')
                    suite.restorers = [forbidden]
                    before = len(session.events)
                    self.assertTrue(suite.cleanup()['success'])
                    self.assertEqual(session.events[before:], [('cleanup', kind.__module__)])
                    if hasattr(suite, 'secret_values'):
                        registered = {value for event in session.events if event[0] == 'secrets' for value in event[1]}
                        self.assertTrue(set(suite.secret_values.values()) <= registered)
                finally:ACTIVE.reset(token)

    def test_session_rejects_wrong_source_before_image_or_api_work(self):
        from ksm import Suite as KSMSuite
        with tempfile.TemporaryDirectory() as directory:
            session = SessionFixture(directory);args = arguments(directory, session);args.source = 'e' * 40
            token = ACTIVE.set(session)
            try:
                with self.assertRaisesRegex(AssertionError, '正式Session'):
                    KSMSuite(args)
                self.assertFalse(any(event[0] == 'kube' for event in session.events))
            finally:ACTIVE.reset(token)

    def service_fixture(self, directory, endpoint_uid='original-pod'):
        session = SessionFixture(directory)
        pod = {'metadata': {'name': 'platform', 'uid': 'original-pod'}, 'status': {
            'podIP': '10.244.0.5', 'conditions': [{'type': 'Ready', 'status': 'True'}]}}
        service = {'metadata': {'uid': 'original-service'}, 'spec': {'selector': {'app': 'monitor-mgr'}}}
        slices = {'items': [{'metadata': {'uid': 'original-slice', 'ownerReferences': [
            {'kind': 'Service', 'uid': 'original-service'}]}, 'endpoints': [{'conditions': {'ready': True},
            'addresses': ['10.244.0.5'], 'targetRef': {'kind': 'Pod', 'namespace': 'protocol-ns',
                'name': 'platform', 'uid': endpoint_uid}}]}]}
        def response(target, args, data, check):
            if args[3] == 'pod':value = pod
            elif args[3] == 'pods':value = {'items': [pod]}
            elif args[3] == 'service':value = service
            elif args[3] == 'endpointslices':value = slices
            else:raise AssertionError('Host fixture 拒绝未计划的 Service 读取')
            return subprocess.CompletedProcess(args, 0, json.dumps(value).encode(), b'')
        session.handler = response
        suite = Suite(arguments(directory, session));suite.ns = 'protocol-ns';suite.pod = 'platform';suite.platform_pod_uid = 'original-pod'
        return session, suite

    def test_service_forward_uses_explicit_context_and_rechecks_original_endpoint_before_proxy(self):
        class Ready(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200 if self.path == '/readyz' else 404);self.end_headers();self.wfile.write(b'{}')
            def log_message(self, *args):pass
        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Ready)
        thread = threading.Thread(target=server.serve_forever, daemon=True);thread.start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                session, suite = self.service_fixture(directory);suite.port = server.server_port
                token = ACTIVE.set(session)
                try:suite.connect()
                finally:ACTIVE.reset(token)
                forward = next(event[1] for event in session.events if event[0] == 'forward')
                self.assertIn('service/monitor-mgr', forward)
                self.assertEqual(forward[forward.index('--context') + 1], 'kind-protocol-control')
                self.assertEqual(suite.actualEndpoint['podUID'], 'original-pod')
                self.assertEqual(session.events[-1][0], 'platform')
                self.assertEqual(sum(event[0] == 'kube' and 'endpointslices' in event[2] for event in session.events), 2)
        finally:server.shutdown();server.server_close();thread.join()

    def test_replacement_endpoint_refuses_forward_before_any_process(self):
        with tempfile.TemporaryDirectory() as directory:
            session, suite = self.service_fixture(directory, 'replacement-pod');suite.port = 18210
            token = ACTIVE.set(session)
            try:
                with self.assertRaisesRegex(AssertionError, 'EndpointSlice'):
                    suite.connect()
            finally:ACTIVE.reset(token)
            self.assertFalse(any(event[0] in ['forward', 'platform'] for event in session.events))

    def test_original_pod_uid_cannot_be_replaced_before_forward(self):
        with tempfile.TemporaryDirectory() as directory:
            session, suite = self.service_fixture(directory);suite.platform_pod_uid = 'different-original';suite.port = 18210
            token = ACTIVE.set(session)
            try:
                with self.assertRaisesRegex(AssertionError, '原Pod UID'):
                    suite.connect()
            finally:ACTIVE.reset(token)
            self.assertFalse(any(event[0] in ['forward', 'platform'] for event in session.events))

    def test_inherited_http_helpers_preserve_actual_failed_response_and_hooks(self):
        from agent import Suite as AgentSuite
        from ksm import Suite as KSMSuite
        for kind in [AgentSuite, KSMSuite]:
            with self.subTest(suite=kind.__module__), tempfile.TemporaryDirectory() as directory:
                session = SessionFixture(directory);session.handler = session.node_response;token = ACTIVE.set(session)
                try:suite = kind(arguments(directory, session))
                finally:ACTIVE.reset(token)
                suite.base = 'http://127.0.0.1:1'
                class Opener:
                    def open(self, request, timeout):
                        session.events.append(('send', request.method))
                        response = io.BytesIO(b'{"operationId":"actual-failure","code":"conflict"}')
                        response.code = 409;response.headers = {'X-Request-Id': 'actual-request'}
                        return response
                suite.http = Opener();start = len(session.events)
                with self.assertRaises(AssertionError):suite.call('PUT', '/instances/fixture/draft', {}, status=200)
                self.assertEqual([event[0] for event in session.events[start:]], ['intent', 'send', 'response'])
                self.assertEqual(suite.traces[-1]['operationId'], 'actual-failure')
                self.assertEqual(suite.traces[-1]['status'], 409)

    def test_fixture_gate_records_actual_barrier_and_release_order_for_storage_alias(self):
        from lifecycle import Suite as LifecycleSuite
        from agent_storage import Suite as StorageSuite
        for kind in [LifecycleSuite, StorageSuite]:
            with self.subTest(suite=kind.__module__), tempfile.TemporaryDirectory() as directory:
                session = SessionFixture(directory);session.handler = session.node_response;token = ACTIVE.set(session)
                try:suite = kind(arguments(directory, session))
                finally:ACTIVE.reset(token)
                class Barrier(http.server.BaseHTTPRequestHandler):
                    def do_GET(self):
                        session.events.append(('gate-send', 'state'))
                        self.send_response(200);self.end_headers()
                        self.wfile.write(b'{"afterSendMarker":{"arrived":true,"operationId":"original-op"}}')
                    def do_POST(self):
                        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                        session.events.append(('gate-send', 'release', body))
                        self.send_response(200);self.end_headers();self.wfile.write(b'{"released":true}')
                    def log_message(self, *args):pass
                server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Barrier)
                thread = threading.Thread(target=server.serve_forever, daemon=True);thread.start()
                suite.gatebase = 'http://127.0.0.1:' + str(server.server_port)
                try:
                    start = len(session.events);value = suite.barrier('afterSendMarker')
                    self.assertEqual(value, {'arrived': True, 'operationId': 'original-op'})
                    self.assertEqual([event[0] for event in session.events[start:]], ['gate-intent', 'gate-send', 'gate-response'])
                    self.assertFalse(any(event[1] == 'release' for event in session.events[start:]))
                    start = len(session.events);self.assertTrue(suite.gate('release', {'stage': 'afterSendMarker'})['released'])
                    self.assertEqual([event[0] for event in session.events[start:]], ['gate-intent', 'gate-send', 'gate-response'])
                finally:server.shutdown();server.server_close();thread.join()


if __name__ == '__main__':
    unittest.main()
