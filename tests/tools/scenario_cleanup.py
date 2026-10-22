"""UT-18：实际清理 helper 经隔离 HTTP 验证明确发布身份，不依赖同名资源认领。"""
import copy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import subprocess
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import urllib.error
import urllib.request


def check_scenario_cleanup(test):
    from e2e_scenarios import Scenario
    from historical_exporter import remove_fixture_companion
    collection = '/apis/monitoring.coreos.com/v1/namespaces/fixture/servicemonitors'
    path = collection + '/companion'
    key = ('control', 'servicemonitor', 'fixture', 'companion')
    private = 'PRIVATE_STDERR_CONFIG_SENTINEL'
    store, operations, deleted = {}, {}, []
    failures = set()

    class Target(BaseHTTPRequestHandler):
        def log_message(self, *_): pass
        def send(self, status, value):
            self.send_response(status); self.send_header('Content-Type', 'application/json'); self.end_headers()
            self.wfile.write(json.dumps(value).encode())
        def do_GET(self):
            if self.path.startswith('/operations/'):
                return self.send(200, operations[self.path.rsplit('/', 1)[1]])
            if 'read' in failures: return self.send(403, dict(message=private))
            self.send(200, store[self.path]) if self.path in store else self.send(404, dict(reason='NotFound'))
        def do_POST(self):
            obj = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            store[self.path + '/' + obj['metadata']['name']] = obj
            self.send(201, obj)
        def do_DELETE(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            if 'delete' in failures: return self.send(403, dict(message=private))
            obj = store.get(self.path)
            if obj is None: return self.send(404, dict(reason='NotFound'))
            uid = body['preconditions']['uid']
            if uid != obj['metadata']['uid']: return self.send(409, dict(reason='Conflict'))
            deleted.append(uid); del store[self.path]; self.send(200, dict(status='Success'))

    server = ThreadingHTTPServer(('127.0.0.1', 0), Target)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    base = 'http://127.0.0.1:' + str(server.server_port)
    def request(method, target, body=None):
        req = urllib.request.Request(base + target, data=None if body is None else json.dumps(body).encode(), method=method, headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=2) as response: return json.load(response)

    class Fixture(Scenario):
        def __init__(self):
            self.target = 'control'; self.owned_crs = {}; self.namespace_uids = {}; self.proxies = []
            self.cluster = dict(id='cluster'); self.wait_blocked = False; self.wait_left_object = False
        def k(self, *args, **kwargs):
            if args[0] == 'wait':
                if self.wait_blocked: raise RuntimeError(private)
                if self.wait_left_object:
                    store[path] = dict(metadata=dict(name='companion', uid='old-uid', resourceVersion='1'))
                return SimpleNamespace(returncode=0, stdout=b'', stderr=b'')
            if args[0] == 'patch':
                if 'finalizers' in failures: raise RuntimeError(private)
                patch = json.loads(args[args.index('-p') + 1]); obj = store[path]
                test.assertEqual(patch[0], dict(op='test', path='/metadata/uid', value=obj['metadata']['uid']))
                test.assertEqual(patch[1], dict(op='test', path='/metadata/resourceVersion', value=obj['metadata']['resourceVersion']))
                obj['metadata']['finalizers'] = patch[2]['value']; return SimpleNamespace(returncode=0)
            test.assertEqual(args[0], 'get')
            if args[1] == 'servicemonitor':
                location = '/apis/monitoring.coreos.com/v1/namespaces/' + args[args.index('-n')+1] + '/servicemonitors/' + args[2]
            else:
                test.assertEqual(args[1], 'kubestatemetrics')
                location = '/apis/monitoring.fjsxy.github.io/v1alpha1/kubestatemetrics/' + args[2]
            try: return SimpleNamespace(returncode=0, stdout=json.dumps(request('GET', location)).encode(), stderr=b'')
            except urllib.error.HTTPError as error:
                return SimpleNamespace(returncode=1, stdout=b'', stderr=b'(NotFound)' if error.code == 404 else private.encode())
        def delete_uid(self, target, location, uid, grace=None):
            return request('DELETE', location, dict(apiVersion='v1', kind='DeleteOptions', preconditions=dict(uid=uid)))
        def call(self, method, location): return request(method, location)

    def operation(uid='new-uid', **changes):
        result = dict(operationId='new-operation', instanceId='instance', versionId='new-version', clusterId='cluster', action='PUBLISH', resources=[dict(
            target=dict(key='service-monitor', apiVersion='monitoring.coreos.com/v1', kind='ServiceMonitor', namespace='fixture', name='companion'),
            confirmedUid=uid, possiblySent=True, state='SUCCEEDED')])
        result.update(changes); return result
    def reset(uid='old-uid'):
        store.clear(); operations.clear(); deleted.clear(); failures.clear()
        request('POST', collection, dict(metadata=dict(name='companion', namespace='fixture', uid=uid, resourceVersion='1')))
        s = Fixture(); s.owned_crs[key] = uid; return s
    try:
        # I29-R01：必须先确认原对象消失，再且仅移除匹配旧 UID 的登记。
        s = reset(); remove_fixture_companion(s, operation('old-uid'))
        test.assertNotIn(key, s.owned_crs, '旧SM已确认消失却保留旧UID清理登记')
        for variant in ('wait-failed', 'still-present'):
            with test.subTest(retirement=variant):
                s = reset(); s.wait_blocked = variant == 'wait-failed'; s.wait_left_object = variant == 'still-present'
                with test.assertRaises(Exception): remove_fixture_companion(s, operation('old-uid'))
                test.assertEqual(s.owned_crs[key], 'old-uid')

        from e2e_cleanup import register_browser_publications
        with tempfile.TemporaryDirectory(prefix='scenario-cleanup-') as raw:
            ledger = Path(raw) / 'publications.jsonl'
            record = dict(operationId='new-operation', instanceId='instance', versionId='new-version')
            def handoff(s, receipt=record, op=None):
                ledger.write_text(json.dumps(receipt) + '\n'); operations['new-operation'] = op or operation()
                return register_browser_publications(s, ledger, {'instance'}, require_complete=True)
            s = reset(); remove_fixture_companion(s, operation('old-uid'))
            request('POST', collection, dict(metadata=dict(name='companion', namespace='fixture', uid='new-uid', resourceVersion='2')))
            result = handoff(s)
            test.assertEqual(result, dict(operations=1, resources=1, failures=[])); test.assertEqual(s.owned_crs[key], 'new-uid')
            test.assertEqual(s.cleanup(), []); test.assertEqual(deleted, ['old-uid', 'new-uid']); test.assertFalse(store)

            # 原浏览器两个受理点共享实际 rememberPublication；回读large已删资源和历史新SM的四份UID。
            s = reset(); remove_fixture_companion(s, operation('old-uid'))
            root = Path(__file__).resolve().parents[2]
            source = (root / 'web/tests/phase1.spec.ts').read_text()
            remember = source[source.index('  const rememberPublication='):source.index('  let browserOwner:')]
            remember = remember.replace('(op:{operationId:string;instanceId:string;versionId:string})', '(op)')
            records = [dict(operationId='large-operation', instanceId='large-instance', versionId='large-version'), record]
            ledger.write_text('')
            script = "import {appendFileSync} from 'node:fs';const fixture=" + json.dumps(dict(publicationLedger=str(ledger))) + ';' + remember
            script += ';for(const op of ' + json.dumps(records) + ')rememberPublication(op);'
            subprocess.run(['node', '--input-type=module', '-e', script], check=True, capture_output=True)
            test.assertEqual([json.loads(line) for line in ledger.read_text().splitlines()], records)
            component = dict(target=dict(key='component', apiVersion='monitoring.fjsxy.github.io/v1alpha1', kind='KubeStateMetrics', namespace='', name='historical'), confirmedUid='ksm-uid', possiblySent=True, state='SUCCEEDED')
            current = operation(); current['resources'].insert(0, component)
            large = copy.deepcopy(current); large.update(records[0]); large['resources'][0]['target']['name'] = 'large'
            large['resources'][0]['confirmedUid'] = 'large-uid'; large['resources'][1]['target']['name'] = 'large-sm'; large['resources'][1]['confirmedUid'] = 'large-sm-uid'
            operations.update({'large-operation': large, 'new-operation': current})
            ksm_collection = '/apis/monitoring.fjsxy.github.io/v1alpha1/kubestatemetrics'
            request('POST', ksm_collection, dict(metadata=dict(name='historical', uid='ksm-uid', resourceVersion='1')))
            s.owned_crs[('control', 'kubestatemetrics', '', 'historical')] = 'ksm-uid'
            request('POST', collection, dict(metadata=dict(name='companion', namespace='fixture', uid='new-uid', resourceVersion='2')))
            # large 两资源在浏览器中已明确删除，目标只返回404；原操作中的确认UID仍须登记后逐一核验。
            result = register_browser_publications(s, ledger, {'large-instance', 'instance'}, require_complete=True)
            test.assertEqual(result, dict(operations=2, resources=4, failures=[]))
            test.assertEqual(s.cleanup(), []); test.assertEqual(set(deleted), {'old-uid', 'new-uid', 'ksm-uid'}); test.assertFalse(store)

            # I29-R02：没有已确认 UID、错实例/版本/操作或未列入本 run 的实例均不能接管现存对象。
            variants = {'unknown': operation(''), 'instance': operation(instanceId='foreign'), 'version': operation(versionId='foreign'),
                        'operation': operation(operationId='foreign'), 'cluster': operation(clusterId='foreign'), 'action': operation(action='DELETE')}
            for variant, op in variants.items():
                with test.subTest(handoff=variant):
                    s = reset('new-uid'); s.owned_crs.clear(); result = handoff(s, op=op)
                    test.assertTrue(result['failures']); test.assertFalse(s.owned_crs)
                    test.assertTrue(s.cleanup()); test.assertEqual(deleted, [])
                    test.assertNotIn(private, json.dumps(s.cleanup_details))
            s = reset('new-uid'); s.owned_crs.clear()
            unsent = operation(''); unsent['resources'][0].update(possiblySent=False, state='QUEUED')
            test.assertEqual(handoff(s, op=unsent), dict(operations=1, resources=0, failures=[]))
            test.assertEqual(s.cleanup(), []); test.assertEqual(deleted, []); test.assertIn(path, store)
            s = reset('new-uid'); s.owned_crs.clear()
            test.assertTrue(handoff(s, receipt={**record, 'instanceId': 'foreign'})['failures']); test.assertFalse(s.owned_crs)
            # 已登记不同 UID 不得被新回读静默覆盖；缺失或损坏交接文件不能被成功退出掩盖。
            s = reset('old-uid'); test.assertTrue(handoff(s)['failures']); test.assertEqual(s.owned_crs[key], 'old-uid')
            for content in ('', private, json.dumps([record])):
                s = reset(); ledger.write_text(content)
                test.assertTrue(register_browser_publications(s, ledger, {'instance'}, require_complete=True)['failures'])

            # I29-R03：替代身份、读失败、条件PATCH和DELETE失败分开投影，异常正文永不公开。
            for variant, phase, reason in [('replaced', 'identity', 'uid-mismatch'), ('read', 'read', 'read-failed'),
                                           ('finalizers', 'finalizers', 'finalizer-patch-failed'), ('delete', 'delete', 'delete-failed')]:
                with test.subTest(cleanup=variant):
                    s = reset(); store[path]['metadata']['finalizers'] = ['fixture.monitor-mgr/hold', 'external.example/keep']
                    if variant == 'replaced': store[path]['metadata']['uid'] = 'replacement-uid'
                    else: failures.add(variant)
                    test.assertEqual(s.cleanup(), ['original-cr-cleanup']); test.assertEqual(deleted, [])
                    test.assertEqual(s.cleanup_details[-1], dict(scope='cr', resource='servicemonitor', stage=phase, reason=reason))
                    test.assertNotIn(private, json.dumps(s.cleanup_details))
            # 成功移除 fixture hold 仍保留外部 finalizer；DELETE 自带原 UID。
            s = reset(); store[path]['metadata']['finalizers'] = ['fixture.monitor-mgr/hold', 'external.example/keep']
            test.assertEqual(s.cleanup(), []); test.assertEqual(deleted, ['old-uid'])
    finally:
        server.shutdown(); server.server_close(); thread.join()
