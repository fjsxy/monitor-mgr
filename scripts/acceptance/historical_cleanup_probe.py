"""UT-18 的受控 HTTP 输入驱动；只输出事实，业务断言由 Ginkgo 完成。"""
import copy
import http.server
import json
import sys
import threading
import urllib.error
import urllib.request
from types import SimpleNamespace
from historical_cleanup import cleanup_registered, handoff_publication


# 执行独立清理夹具并返回真实请求和失败阶段。
def run(variant):
    uid='original-uid';events=[]
    obj={'metadata':{'name':'old','namespace':'ns','uid':'replacement' if variant=='replaced' else uid,'resourceVersion':'rv','finalizers':['phase1.fixture/hold'] if variant=='finalizers' else []}}
    state={'obj':obj}
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
            events.append({'method':'PATCH','path':self.path});self.send_response(403);self.end_headers();self.wfile.write(b'private-sentinel')
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
        except urllib.error.HTTPError as error:return SimpleNamespace(returncode=1,stdout=b'',stderr=b'(NotFound)' if error.code==404 else b'private-sentinel')
    key=('control','servicemonitor','ns','old')
    class Suite:
        # 初始化本例原资源身份和清理登记。
        def __init__(self):self.owned_crs={key:uid}
        # 模拟受控 Kubernetes 读取，不访问真实集群。
        def k(self,*args,**kwargs):
            if args[0]=='wait':
                if state['obj']:raise RuntimeError('private-sentinel')
                return SimpleNamespace(returncode=0,stdout=b'',stderr=b'')
            result=request('PATCH',{}) if args[0]=='patch' else request('GET')
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

print(json.dumps({'cleanup':[run(variant) for variant in ('gone','blocked','replaced','read','finalizers','delete')], 'handoff':[handoff(variant) for variant in ('confirmed','unknown','unsent','foreign-instance','foreign-version','foreign-operation')]}))
