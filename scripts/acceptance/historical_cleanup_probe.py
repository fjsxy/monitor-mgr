"""UT-18 的受控 HTTP 输入驱动；只输出事实，业务断言由 Ginkgo 完成。"""
import copy
import http.server
import http.client
from pathlib import Path
import tempfile
import json
import sys
import threading
import urllib.error
import urllib.request
from types import SimpleNamespace
from historical_cleanup import cleanup_registered, handoff_publication, fixture_finalizers, require_fixture_patch, remove_legacy_service_monitor, fixture_cluster_role_rules


# 执行独立清理夹具并返回真实请求和失败阶段。
def run(variant):
    uid='original-uid';events=[]
    obj={'metadata':{'name':'old','namespace':'ns','uid':'replacement' if variant=='replaced' else uid,'resourceVersion':'rv','finalizers':['phase1.fixture/hold'] if variant in ('finalizers','patch-gone','patch-conflict','patch-replaced') else []}}
    state={'obj':obj,'patches':0}
    class Handler(http.server.BaseHTTPRequestHandler):
        # 抑制测试 HTTP 服务的原始请求日志。
        def log_message(self,*args):pass
        # 返回当前受控资源或操作快照。
        def do_GET(self):
            events.append({'method':'GET','path':self.path})
            if variant=='read':self.send_response(500);self.end_headers();self.wfile.write(b'private-sentinel');return
            self.send_response(200 if state['obj'] else 404);self.end_headers();self.wfile.write(json.dumps(state['obj'] or {}).encode())
        # 模拟 finalizer 更新并保留实际请求。
        def do_PATCH(self):
            patch=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            # 真实1.28的JSONPatch test失败只得到泛化422；不能伪造含RV文字的stderr让旧helper变绿。
            if isinstance(patch,list):
                events.append({'method':'PATCH','preconditions':{},'patchType':'json'})
                self.send_response(422);self.end_headers();self.wfile.write(b'{"kind":"Status","reason":"Invalid","message":"the server rejected our request due to an error in our request","code":422}');return
            conditions=patch.get('metadata',{})
            events.append({'method':'PATCH','preconditions':{'uid':conditions.get('uid'),'resourceVersion':conditions.get('resourceVersion')},'patchType':'merge'});state['patches']+=1
            if variant=='patch-conflict' and state['patches']==1:
                self.send_response(409);self.end_headers();return
            if variant in ('patch-gone','patch-conflict','patch-replaced'):
                if variant=='patch-gone':state['obj']=None
                elif variant=='patch-replaced':state['obj']['metadata']['uid']='replacement'
                else:state['obj']['metadata'].update(finalizers=[],resourceVersion='rv2')
                self.send_response(200);self.end_headers();return
            self.send_response(403);self.end_headers();self.wfile.write(b'private-sentinel')
        # 模拟有 UID 条件的资源删除。
        def do_DELETE(self):
            data=json.loads(self.rfile.read(int(self.headers['Content-Length'])));events.append({'method':'DELETE','preconditions':data['preconditions']})
            self.send_response(403 if variant=='delete' else 200);self.end_headers()
            if variant not in ('delete','blocked'):state['obj']=None
    server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler);threading.Thread(target=server.serve_forever,daemon=True).start()
    address='http://127.0.0.1:'+str(server.server_port)+'/object'
    # 发起受控 HTTP 请求并保留响应状态。
    def request(method,data=None):
        try:
            with urllib.request.urlopen(urllib.request.Request(address,data=json.dumps(data).encode() if data is not None else None,method=method),timeout=2) as response:return SimpleNamespace(returncode=0,stdout=response.read(),stderr=b'')
        except urllib.error.HTTPError as error:return SimpleNamespace(returncode=1,stdout=b'',stderr=b'(NotFound)' if error.code==404 else b'(Conflict)' if error.code==409 else b'private-sentinel')
    key=('control','servicemonitor','ns','old')
    class Suite:
        # 初始化本例原资源身份和清理登记。
        def __init__(self):self.owned_crs={key:uid}
        # 模拟受控 Kubernetes 读取，不访问真实集群。
        def k(self,*args,**kwargs):
            if args[0]=='wait':
                if state['obj']:raise RuntimeError('private-sentinel')
                return SimpleNamespace(returncode=0,stdout=b'',stderr=b'')
            result=request('PATCH',json.loads(args[args.index('-p')+1])) if args[0]=='patch' else request('GET')
            if result.returncode and kwargs.get('check',True):raise RuntimeError('private-sentinel')
            return result
        # 按原 UID 与 RV 执行受控删除请求。
        def delete_uid(self,target,path,original,resource_version=None):
            result=request('DELETE',{'preconditions':{'uid':original,'resourceVersion':resource_version}})
            if result.returncode:raise RuntimeError('private-sentinel')
    s=Suite()
    try:receipt=cleanup_registered(s,key,uid)
    finally:server.shutdown();server.server_close()
    return {'variant':variant,'receipt':receipt,'registryRetained':key in s.owned_crs,'events':events}


# 验证发布响应后只登记已确认原 UID 的清理交接。
def handoff(variant):
    expected={'instance':{'id':'instance','clusterId':'cluster','name':'old'},'operationId':'old-operation','parentUid':'original-uid'}
    identity={'instanceId':'instance','versionId':'version','operationId':'new-operation'}
    operation={'operationId':'new-operation','instanceId':'instance','versionId':'version','clusterId':'cluster','action':'PUBLISH','resources':[{'target':{'key':'component','kind':'KubeStateMetrics','apiVersion':'monitoring.fjsxy.github.io/v1alpha1','namespace':'','name':'old'},'confirmedUid':'original-uid','possiblySent':True}]}
    if variant=='foreign-instance':operation['instanceId']='other'
    if variant=='foreign-version':operation['versionId']='other'
    if variant=='foreign-operation':identity['operationId']='old-operation'
    if variant in ('unknown','unsent'):operation['resources'][0]['confirmedUid']=''
    if variant=='unsent':operation['resources'][0]['possiblySent']=False
    class Handler(http.server.BaseHTTPRequestHandler):
        # 抑制测试 HTTP 服务的原始请求日志。
        def log_message(self,*args):pass
        # 返回当前受控资源或操作快照。
        def do_GET(self):self.send_response(200);self.end_headers();self.wfile.write(json.dumps(operation).encode())
    server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler);threading.Thread(target=server.serve_forever,daemon=True).start()
    class Suite:
        tracked=[]
        # 返回公开操作状态供清理交接核验。
        def call(self,method,path):
            with urllib.request.urlopen('http://127.0.0.1:'+str(server.server_port)+'/api/v1'+path,timeout=2) as response:return json.load(response)
        # 登记已确认原 UID，禁止以同名查询结果补认。
        def track_cr(self,uid,**kwargs):self.tracked.append(uid)
    s=Suite();s.tracked=[];error=''
    try:handoff_publication(s,expected,identity)
    except Exception as exception:error=str(exception)
    finally:server.shutdown();server.server_close()
    return {'variant':variant,'tracked':s.tracked,'error':error}


# 条件写协议独立走真实HTTP，必要输入由Go现有UT-18断言，不连接正在验收的集群。
def conditional(variant):
    uid='original-uid';events=[];state={'patches':0,'obj':{'metadata':{'uid':uid,'name':'old','resourceVersion':'rv','finalizers':['other.fixture/hold','phase1.fixture/hold']},'spec':{'serviceMonitor':{'enabled':False},'targetNamespace':'ns','enabled':True}}}
    state['obj']['rules']=[{'apiGroups':['monitoring.coreos.com'],'resources':['servicemonitors'],'verbs':['get','list']}]
    desired_rules=[{'apiGroups':[''],'resources':['pods'],'verbs':['get']}]
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_GET(self):
            events.append({'method':'GET'})
            self.send_response(200);self.end_headers();self.wfile.write(json.dumps(state['obj']).encode())
        def do_PATCH(self):
            patch=json.loads(self.rfile.read(int(self.headers['Content-Length'])));state['patches']+=1
            events.append({'method':'PATCH','patchType':self.headers.get('Content-Type'),'preconditions':patch.get('metadata',{}) if isinstance(patch,dict) else {}})
            if variant=='network':self.close_connection=True;return
            code=0
            if variant in ('conflict','legacy-conflict','rules-conflict','replacement','legacy-replacement') and state['patches']==1:
                state['obj']['metadata']['resourceVersion']='rv2'
                if variant in ('replacement','legacy-replacement'):state['obj']['metadata']['uid']='replacement'
                code=409
            elif variant=='conflict-limit':state['obj']['metadata']['resourceVersion']='rv'+str(state['patches']+1);code=409
            elif variant=='invalid':code=422
            elif variant=='forbidden':code=403
            if code:
                self.send_response(code);self.end_headers();self.wfile.write(b'PRIVATE_SENTINEL');return
            state['obj']['metadata'].update(patch.get('metadata',{}));state['obj']['metadata']['resourceVersion']='rv3'
            if 'rules' in patch:state['obj']['rules']=patch['rules']
            if 'serviceMonitor' in patch.get('spec',{}) and patch['spec']['serviceMonitor'] is None:state['obj']['spec'].pop('serviceMonitor',None)
            self.send_response(200);self.end_headers();self.wfile.write(json.dumps(state['obj']).encode())
    server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler);threading.Thread(target=lambda:server.serve_forever(poll_interval=.01),daemon=True).start()
    address='http://127.0.0.1:'+str(server.server_port)+'/object'
    class Suite:
        target='control';crname='old'
        def k(self,*args,**kwargs):
            method='PATCH' if args[0]=='patch' else 'GET';data=json.loads(args[args.index('-p')+1]) if method=='PATCH' else None
            headers={'Content-Type':'application/merge-patch+json' if '--type=merge' in args else 'application/json-patch+json'}
            try:
                with urllib.request.urlopen(urllib.request.Request(address,data=json.dumps(data).encode() if data is not None else None,method=method,headers=headers),timeout=2) as response:return SimpleNamespace(returncode=0,stdout=response.read(),stderr=b'')
            except urllib.error.HTTPError as error:
                fixed={409:b'Error from server (Conflict): PRIVATE_SENTINEL',422:b'The request is invalid: PRIVATE_SENTINEL',403:b'Error from server (Forbidden): PRIVATE_SENTINEL'}
                return SimpleNamespace(returncode=1,stdout=b'',stderr=fixed.get(error.code,b'PRIVATE_SENTINEL'))
            except (OSError,urllib.error.URLError,http.client.HTTPException):return SimpleNamespace(returncode=1,stdout=b'',stderr=b'PRIVATE_SENTINEL')
    with tempfile.TemporaryDirectory() as temporary:
        suite=Suite();suite.env=SimpleNamespace(public=Path(temporary));receipt={};diagnostic={}
        try:
            if variant=='rules-conflict':
                receipt=fixture_cluster_role_rules(suite,'control','old',uid,desired_rules)
            elif variant.startswith('legacy-'):
                receipt=remove_legacy_service_monitor(suite,uid)
            else:
                receipt=fixture_finalizers(suite,('control','servicemonitor','ns','old'),uid,remove=('phase1.fixture/hold',))
                require_fixture_patch(suite,receipt,'fixture-hold-release')
        except AssertionError:
            diagnostic=json.loads((Path(temporary)/'fixture-conditional-patch.json').read_text())
            if not receipt:receipt=dict(success=False,**diagnostic)
        finally:server.shutdown();server.server_close()
    return dict(variant=variant,receipt={k:receipt[k] for k in ('success','reason','phase','attempts')},events=events,diagnostic=diagnostic,
                otherFinalizerPreserved='other.fixture/hold' in state['obj']['metadata']['finalizers'],
                otherSpecPreserved=state['obj']['spec'].get('targetNamespace')=='ns' and state['obj']['spec'].get('enabled') is True,
                legacyFieldAbsent='serviceMonitor' not in state['obj']['spec'],rulesApplied=state['obj']['rules']==desired_rules)

print(json.dumps({'conditional':[conditional(variant) for variant in ('preserve','conflict','legacy-conflict','rules-conflict','invalid','forbidden','network','conflict-limit','replacement','legacy-replacement')], 'cleanup':[run(variant) for variant in ('gone','blocked','replaced','read','finalizers','delete','patch-gone','patch-conflict','patch-replaced')], 'handoff':[handoff(variant) for variant in ('confirmed','unknown','unsent','foreign-instance','foreign-version','foreign-operation')]}))
