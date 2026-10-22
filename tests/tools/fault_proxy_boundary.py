"""经真实 loopback HTTP 验证 SSAR 故障规则，防止验收屏障选中受理请求。"""
import concurrent.futures
import json
from pathlib import Path
import socket
import ssl
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import urllib.error
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts/acceptance/fixtures'))
from fault_proxy import APIHandler, ControlHandler, EventLog, FaultState, FixtureServer


def check_ssar_rule(test):
    """仅验证真实 HTTP 的选中和暂停边界；上游故意不可达，不宣称 TLS/Kubernetes 验收。"""
    path = '/apis/authorization.k8s.io/v1/selfsubjectaccessreviews'
    with tempfile.TemporaryDirectory() as directory, socket.socket() as unavailable:
        unavailable.bind(('127.0.0.1', 0))
        log = EventLog(Path(directory) / 'events.jsonl')
        app = SimpleNamespace(state=FaultState(), log=log, control_token='fixture-control',
                              limits=dict(io_timeout_seconds=1, pause_timeout_seconds=3,
                                          max_body_bytes=65536, max_response_bytes=65536),
                              backend=dict(host='127.0.0.1', port=unavailable.getsockname()[1], server_name='localhost'),
                              backend_context=ssl.create_default_context())
        servers = [FixtureServer(('127.0.0.1', 0), handler) for handler in (APIHandler, ControlHandler)]
        for server in servers:
            server.app = app
            threading.Thread(target=server.serve_forever, daemon=True).start()
        api, control = ['http://127.0.0.1:' + str(server.server_port) for server in servers]

        def request(base, target, body=None):
            req = urllib.request.Request(base + target, data=None if body is None else json.dumps(body).encode(),
                                         headers={'X-Fault-Control': app.control_token, 'Content-Type': 'application/json'})
            try:
                with urllib.request.urlopen(req, timeout=4) as response:
                    return response.status, json.load(response)
            except urllib.error.HTTPError as error:
                return error.code, json.load(error)

        def review(resource):
            return dict(apiVersion='authorization.k8s.io/v1', kind='SelfSubjectAccessReview',
                        spec=dict(resourceAttributes=dict(group='monitoring.fjsxy.github.io', resource=resource, verb='create')))

        rule = dict(method='POST', path=path, action='pause_before_send', ssarResource='kubestatemetrics')
        try:
            # 关键：非法字段和值不得退化成只匹配路径，否则会再次截断发布受理。
            for invalid in [dict(rule, ssarResource='servicemonitors'), dict(rule, unexpected='x'),
                            dict(rule, method='GET'), dict(rule, path='/apis/example/v1/items')]:
                test.assertEqual(request(control, '/rule', invalid)[0], 409)
            test.assertEqual(request(control, '/rule', rule)[0], 200)
            for body, target in [(review('servicemonitors'), path), ({'spec': {}}, path),
                                 (review('kubestatemetrics'), path + '?dryRun=All')]:
                test.assertEqual(request(api, target, body)[0], 502)
                state = request(control, '/state')[1]['rule']
                test.assertFalse(state['claimed'])
                test.assertEqual(state['matchingRequests'], 0)
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                pending = pool.submit(request, api, path, review('kubestatemetrics'))
                end = time.monotonic() + 2
                while time.monotonic() < end:
                    state = request(control, '/state')[1]['rule']
                    if state['stage'] == 'paused_before_send':
                        break
                    time.sleep(.01)
                test.assertEqual(state['stage'], 'paused_before_send')
                test.assertEqual(state['matchingRequests'], 1)
                test.assertFalse(pending.done())
                test.assertEqual(request(control, '/release', dict(ruleId=state['id']))[0], 200)
                test.assertEqual(pending.result()[0], 502)
            # 旧三字段规则仍选中同一路径的任意请求；释放后才触发故意不可达的上游。
            request(control, '/reset', {})
            legacy = dict(method='POST', path=path, action='pause_before_send')
            test.assertEqual(request(control, '/rule', legacy)[0], 200)
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                pending = pool.submit(request, api, path, review('servicemonitors'))
                end = time.monotonic() + 2
                while time.monotonic() < end:
                    state = request(control, '/state')[1]['rule']
                    if state['stage'] == 'paused_before_send':
                        break
                    time.sleep(.01)
                test.assertEqual(state['stage'], 'paused_before_send')
                test.assertFalse(pending.done())
                request(control, '/release', dict(ruleId=state['id']))
                test.assertEqual(pending.result()[0], 502)
        finally:
            app.state.reset()
            for server in servers:
                server.shutdown()
                server.server_close()
            log.close()
