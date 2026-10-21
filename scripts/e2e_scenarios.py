"""10 条纵向真实集群场景；复用 HTTP/Operator 断言，不再展开旧 207 个执行键。"""
import base64
import copy
import http.cookiejar
import json
import os
from pathlib import Path
import secrets
import socket
import time
import urllib.request
import uuid
from types import SimpleNamespace

from e2e_environment import ROOT, RUN_LABEL
from clusters import Suite as ClusterSuite
# 旧模块保留为场景实现库；新入口不会调用它们的 run/main/profile 分派。
from ksm import Suite as KSMSuite
from agent import Suite as AgentSuite
from lifecycle import Suite as LifecycleSuite
from ksm_proxy import Proxy
from agent_storage import owned_graph, policies, volume_result, delete as delete_agent, wait
from ksm_cases import controller, args as workload_args, counts, stopped_workloads


class Scenario(LifecycleSuite, AgentSuite):
    def __init__(self, environment, index, emit):
        self.env, self.index, self.emit = environment, index, emit
        prefix = environment.run_id + '-c' + str(index)
        a = SimpleNamespace(session=None, output=str(environment.private / ('case-' + str(index))),
                            prefix=prefix, port=19000 + index * 3, kubectl='kubectl', initialize=True,
                            source=environment.candidate, image=environment.image, profile=None)
        for role, target in environment.targets.items():
            setattr(a, role + '_config', target['kubeconfig']); setattr(a, role + '_context', target['context']); setattr(a, role + '_pod_config', target['podConfig'])
        ClusterSuite.__init__(self, a)
        self.component = 'ksm'; self.target = 'control'; self.case = f'E2E-{index:02d}'
        self.traces=[]; self.resources=[]; self.namespace_uids={}; self.ksms=[]; self.restorers=[]; self.proxies=[]
        self.extra_namespaces=[]; self.created_crs=[]; self.owned_crs={}; self.clusters={};self.platform_proofs=[]; self.step='环境准备'
        self.environment = {role: {'nodes': [{'name': n['metadata']['name']} for n in t['nodes']]} for role,t in environment.targets.items()}
        # 证书夹具只在私有目录生成；Secret 内容只用于内存比较。
        cert = environment.private / 'receiver.crt'; key = environment.private / 'receiver.key'
        if not cert.exists():
            environment.command(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-keyout',key,'-out',cert,'-days','1','-subj','/CN=receiver.example.invalid'], 'fixture-receiver-pki')
            os.chmod(key,0o600)
        self.secret_values={'username':secrets.token_urlsafe(12),'password':secrets.token_urlsafe(24),'credentials':secrets.token_urlsafe(32),'ca':cert.read_text(),'cert':cert.read_text(),'key':key.read_text()}

    def by(self, text):
        self.step=text; self.emit(dict(type='step', text=text)); print(self.case+' '+text, flush=True)

    def k(self, *args, data=None, target='control', check=True):
        # 旧实现库的 namespace 命令也收敛到带 run 标签的原创建响应。
        direct_namespace=len(args)>=3 and args[0]=='create' and args[1] in ('namespace','ns')
        if direct_namespace:
            data={'apiVersion':'v1','kind':'Namespace','metadata':{'name':args[2]}};args=('create','-f','-','-o','json')
        if data is not None and any(v in args for v in ('create','apply')):
            data=copy.deepcopy(data)
            for obj in data.get('items',[]) if data.get('kind')=='List' else [data]:
                obj.setdefault('metadata',{}).setdefault('labels',{})[RUN_LABEL]=self.env.run_id
        result=self.env.k(target,*args,data=data,check=check)
        if direct_namespace and result.returncode==0:
            metadata=json.loads(result.stdout)['metadata'];self.namespace_uids[(target,metadata['name'])]=metadata['uid']
        return result

    def get(self,*args,target=None): return json.loads(self.k('get',*args,'-o','json',target=target or self.target).stdout)
    def call(self,*args,**kwargs): return AgentSuite.call(self,*args,**kwargs)
    def operation(self,*args,**kwargs): return AgentSuite.operation(self,*args,**kwargs)

    def connect(self):
        # Pod 重建必须沿用原 Cookie；Session 持久化是 E2E-01 的被测行为。
        ClusterSuite.connect(self)
        pod=self.get('pod',self.pod,'-n',self.ns,target='control')
        self.platform_proofs.append(self.env.verify_container(pod,'platform',self.env.platform_config))
        marker=('Forwarding from 127.0.0.1:'+str(self.port)+' -> 8080').encode()
        assert self.forward.poll() is None and marker in self.forward.private_stdout_path.read_bytes(),'不能借用其他进程的 HTTP endpoint'

    def deploy(self):
        self.ns=self.a.prefix+'-platform'
        # 为平台和故障代理选择空闲端口组，避免并行 run 争用固定宿主机端口。
        while True:
            port=20000+secrets.randbelow(33000);reserved=[]
            try:
                for value in (port,port+200):
                    listener=socket.socket();reserved.append(listener);listener.bind(('127.0.0.1',value))
                self.port=port;break
            except OSError:pass
            finally:
                for listener in reserved:listener.close()
        self.namespace(self.ns,'control')
        t=self.env.targets['control']
        self.env.command(['helm','upgrade','--install','platform',ROOT/'charts/monitor-mgr','--kubeconfig',t['kubeconfig'],'--kube-context',t['context'],'--namespace',self.ns,
                          '--set-string','image='+self.env.image,'--set-string','imagePullPolicy=Never','--set-string','source='+self.env.candidate,'--set-string','runId='+self.env.run_id,'--wait','--timeout','3m'], 'helm-platform',timeout=210)
        self.k('-n',self.ns,'rollout','status','deployment/monitor-mgr','--timeout=120s')
        pod=self.get('pods','-n',self.ns,'-l','app=monitor-mgr',target='control')['items'][0]
        self.pod=pod['metadata']['name'];self.platform_pod_uid=self.initial_uid=pod['metadata']['uid'];self.connect()
        self.call('POST','/initialization',{'username':'admin','password':self.password},status=201)
        self.call('POST','/sessions',{'username':'admin','password':self.password},status=201)
        self.owner=self.call('GET','/session')['account']['id']
        assert pod['spec']['containers'][0]['image']==self.env.image
        assert pod['metadata']['annotations']['monitor-mgr-source']==self.env.candidate

    def user(self,suffix):
        username='owner-'+suffix;temporary=secrets.token_urlsafe(20);password=secrets.token_urlsafe(20)
        account=self.call('POST','/accounts',{'username':username,'password':temporary,'role':'USER'},status=201)
        opener=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        with self.using(opener):
            self.call('POST','/sessions',{'username':username,'password':temporary},status=201)
            self.call('POST','/session/password',{'currentPassword':temporary,'newPassword':password})
            self.call('POST','/sessions',{'username':username,'password':password},status=201)
        return account,opener

    def register(self, role, config=None):
        if role in self.clusters: return self.clusters[role]
        op=self.operation(self.accept(config or self.config(role),self.owner,name=self.a.prefix+'-'+role))
        assert op['state']=='SUCCEEDED',op
        self.clusters[role]=self.call('GET','/clusters/'+op['clusterId']);return self.clusters[role]

    def prepare(self, suffix='', target='control', component='ksm'):
        self.component=component;self.target=target;self.targetns=self.a.prefix+'-target'+suffix;self.crname='m'+uuid.uuid4().hex[:12]
        if component=='agent': self.fixture(self.targetns,target)
        else: self.namespace(self.targetns,target)
        self.cluster=self.register(target);return self.cluster

    def cfg(self,name=None,mode='Deployment',namespace=None):
        if self.component=='agent': return AgentSuite.cfg(self,name,namespace)
        value=KSMSuite.cfg(self,name,mode);value['namespaceFilter']={'mode':'Include','names':[self.targetns]};return value
    def create(self,*args,**kwargs): return (AgentSuite.create if self.component=='agent' else KSMSuite.create)(self,*args,**kwargs)
    def save(self,*args,**kwargs): return (AgentSuite.save if self.component=='agent' else KSMSuite.save)(self,*args,**kwargs)
    def preview(self,*args,**kwargs): return (AgentSuite.preview if self.component=='agent' else KSMSuite.preview)(self,*args,**kwargs)
    def publish(self,i,**kwargs):
        result=KSMSuite.publish(self,i,**kwargs)
        # 原 UID 优先来自平台已确认的实例，未知创建结果只能使用代理保存的原 API 响应。
        current=self.call('GET','/instances/'+i['id']);uid=current.get('uid')
        if uid:self.track_cr(uid,name=current['name'],namespace=current.get('targetNamespace',self.targetns) if self.component=='agent' else '')
        return result
    def track_cr(self, uid, name=None, namespace=None, component=None, target=None):
        kind='prometheusagent' if (component or self.component)=='agent' else 'kubestatemetrics'
        self.owned_crs[(target or self.target,kind,namespace if namespace is not None else (self.targetns if kind=='prometheusagent' else ''),name or self.crname)]=uid
    def cr(self,*args,**kwargs): return (AgentSuite.cr if self.component=='agent' else KSMSuite.cr)(self,*args,**kwargs)
    def runtime(self,*args,**kwargs):
        result=(AgentSuite.runtime if self.component=='agent' else KSMSuite.runtime)(self,*args,**kwargs)
        if self.component=='ksm' and result['summary']=='Available':
            for pod in self.workloads():
                if pod['kind']=='Pod' and not pod['metadata'].get('deletionTimestamp'):
                    self.env.verify_container(pod,pod['spec']['containers'][0]['name'],self.env.official_configs['ksm'])
        return result
    def setup_proxy(self):
        proxy=Proxy(self,self.target);self.proxies.append(proxy);self.proxy=proxy
        done=self.operation(self.accept(proxy.config,path='/clusters/'+self.cluster['id']+'/credentials'));assert done['state']=='SUCCEEDED';return proxy
    def rebuild(self):
        cookies=self.cookies;self.forward.terminate();self.forward.wait(timeout=5);del self.forward
        old=self.platform_pod_uid
        self.k('-n',self.ns,'delete','pod',self.pod,'--wait=true','--timeout=50s')
        self.k('-n',self.ns,'rollout','status','deployment/monitor-mgr','--timeout=120s')
        pods=[p for p in self.get('pods','-n',self.ns,'-l','app=monitor-mgr',target='control')['items'] if not p['metadata'].get('deletionTimestamp')]
        assert len(pods)==1
        self.pod=pods[0]['metadata']['name'];self.platform_pod_uid=pods[0]['metadata']['uid'];assert self.platform_pod_uid!=old
        self.cookies=cookies;self.connect();return self.platform_pod_uid

    def cleanup(self):
        failures=[]
        for proxy in self.proxies:
            try: proxy.close()
            except Exception: failures.append('proxy-cleanup')
        if hasattr(self,'forward'):
            self.forward.terminate();self.forward.wait(timeout=5)
        # 关键：仅清理原 UID；替代 CR 如果由本例显式构造，会另外保存自己的创建身份。
        for (target,kind,namespace,name),uid in reversed(list(self.owned_crs.items())):
            try:
                args=['get',kind,name,'-o','json']+(['-n',namespace] if namespace else [])
                r=self.k(*args,target=target,check=False)
                if r.returncode:
                    assert b'(NotFound)' in r.stderr;continue
                obj=json.loads(r.stdout);assert obj['metadata']['uid']==uid
                finalizers=obj['metadata'].get('finalizers',[])
                ours=[f for f in finalizers if f in ('phase1.fixture/hold','fixture.monitor-mgr/hold')]
                if ours:
                    patch=[{'op':'test','path':'/metadata/uid','value':uid},{'op':'test','path':'/metadata/resourceVersion','value':obj['metadata']['resourceVersion']},{'op':'replace','path':'/metadata/finalizers','value':[f for f in finalizers if f not in ours]}]
                    self.k('patch',kind,name,*(['-n',namespace] if namespace else []),'--type=json','-p',json.dumps(patch),target=target)
                # DELETE precondition 由原 UID 的 JSON DeleteOptions 提供，避免 GET/DELETE 间同名替换。
                path='/apis/monitoring.coreos.com/v1alpha1/namespaces/'+namespace+'/prometheusagents/'+name if kind=='prometheusagent' else '/apis/monitoring.fjsxy.github.io/v1alpha1/kubestatemetrics/'+name
                self.delete_uid(target,path,uid)
            except Exception: failures.append('original-cr-cleanup')
        for (target,namespace),uid in reversed(list(self.namespace_uids.items())):
            try:
                actual=self.get('namespace',namespace,target=target)
                from testing_contract import owned_identity
                assert owned_identity(self.env.run_id,{'uid':uid,'runId':self.env.run_id},{'uid':actual['metadata']['uid'],'runId':actual['metadata'].get('labels',{}).get(RUN_LABEL)})
                # fixture 清理可加速自己的 Pod 退出；所有业务删除断言已经在场景中完成。
                for pod in self.get('pods','-n',namespace,target=target)['items']:
                    self.delete_uid(target,'/api/v1/namespaces/'+namespace+'/pods/'+pod['metadata']['name'],pod['metadata']['uid'],grace=0)
                self.delete_uid(target,'/api/v1/namespaces/'+namespace,uid)
                self.k('wait','--for=delete','namespace/'+namespace,'--timeout=100s',target=target)
            except Exception: failures.append('original-namespace-cleanup')
        return failures

    def delete_uid(self,target,path,uid,grace=None):
        # kubectl proxy 只绑定随机 loopback；凭据不出现在 argv 或公开报告中。
        import socket
        with socket.socket() as listener: listener.bind(('127.0.0.1',0));port=listener.getsockname()[1]
        t=self.env.targets[target]
        process=self.env.processes.popen(['kubectl','--kubeconfig',t['kubeconfig'],'--context',t['context'],'proxy','--port='+str(port),'--address=127.0.0.1'],label='uid-delete-proxy')
        try:
            body={'apiVersion':'v1','kind':'DeleteOptions','preconditions':{'uid':uid},'propagationPolicy':'Foreground'}
            if grace is not None:body['gracePeriodSeconds']=grace
            request=urllib.request.Request('http://127.0.0.1:'+str(port)+path,data=json.dumps(body).encode(),method='DELETE',headers={'Content-Type':'application/json'})
            for attempt in range(30):
                try:
                    with urllib.request.urlopen(request,timeout=10) as response: assert response.status in (200,202)
                    return
                except ConnectionError:time.sleep(.1)
                except urllib.error.URLError as e:
                    if isinstance(e,urllib.error.HTTPError):
                        if e.code==404:return
                        raise
                    time.sleep(.1)
            raise RuntimeError('UID DELETE proxy unavailable')
        finally:process.terminate();process.wait(timeout=5)


def platform(s):
    s.by('经 Service HTTP 保存管理记录并确认原 PVC 与 Session')
    cluster=s.register('control');before=s.call('GET','/session');proof=s.proof('store-before');assert proof['keyUID']==10001 and proof['keyMode']=='0o600' and proof['integrity']=='ok';pvc=s.get('pvc','monitor-mgr-data','-n',s.ns,target='control')['metadata']['uid']
    s.by('重建真实平台 Pod，沿用原 Cookie 读回管理状态')
    s.rebuild();assert s.proof('store-after')==proof;assert s.call('GET','/session')['expiresAt']==before['expiresAt'];assert s.call('GET','/clusters/'+cluster['id'])['uid']==cluster['uid']
    assert s.get('pvc','monitor-mgr-data','-n',s.ns,target='control')['metadata']['uid']==pvc
    s.by('核对 HTTP 唯一 Service 端口和容器 RPC loopback 监听')
    service=s.get('service','monitor-mgr','-n',s.ns,target='control');assert [p['port'] for p in service['spec']['ports']]==[8080]
    pod=s.get('pod',s.pod,'-n',s.ns,target='control');container=pod['spec']['containers'][0]
    assert [p['containerPort'] for p in container['ports']]==[8080]
    # 使用共享 Pod 网络的临时诊断容器读取 /proc/net/tcp，产品镜像不新增调试端口。
    s.k('debug','-n',s.ns,s.pod,'--image=python:3.13.2-alpine','--target=platform','--profile=general','--attach=true','--','python','-c',"import json; x=open('/proc/net/tcp').read(); assert '0100007F:22B8' in x; print('loopback-rpc-ok')")


def clusters(s):
    s.by('真实证书与 token 分别登记 control/data，重复 UID 和 TLS 错误均拒绝')
    s.prepare();control=s.cluster
    token_ns=s.a.prefix+'-token';s.namespace(token_ns,'data')
    s.k('create','serviceaccount','verifier','-n',token_ns,target='data')
    s.k('create','clusterrolebinding',token_ns,'--clusterrole=cluster-admin','--serviceaccount='+token_ns+':verifier',target='data')
    token=s.k('create','token','verifier','-n',token_ns,'--duration=1h',target='data').stdout.decode().strip()
    cfg=s.config('data');cfg['users']=[{'name':'token','user':{'token':token}}];cfg['contexts'][0]['context']['user']='token'
    data=s.register('data',cfg);assert data['identity']=='system:serviceaccount:'+token_ns+':verifier'
    duplicate=s.operation(s.accept(s.config(),s.owner,name='duplicate'));assert duplicate['state']=='FAILED'
    bad=s.config();bad['clusters'][0]['cluster']['tls-server-name']='wrong.invalid';assert s.operation(s.accept(bad,s.owner,name='tls-bad'))['state']=='FAILED'
    s.by('失败候选保留旧凭据；受限 namespace 凭据可登记但不能写入 CR')
    old=s.call('GET','/clusters/'+control['id']);bad=s.config();bad['users'][0]['user']={'token':'invalid-candidate'}
    assert s.operation(s.accept(bad,path='/clusters/'+control['id']+'/credentials'))['state']=='FAILED'
    assert s.call('GET','/clusters/'+control['id'])['credentialRevision']==old['credentialRevision']
    from ksm_cases import restricted_namespace
    allowed=restricted_namespace(s)
    assert s.operation(s.accept(s.config(),path='/clusters/'+control['id']+'/credentials'))['state']=='SUCCEEDED'
    s.by('成功切换凭据并转移归属，当前权限立即改变')
    i=allowed['instance'];op=allowed['successfulAuthorizedNamespace'];s.runtime(i);uid=s.cr()['metadata']['uid']
    a,clienta=s.user('a');b,clientb=s.user('b')
    for owner,previous in [(a,None),(b,clienta)]:
        s.call('POST','/clusters/'+control['id']+'/transfer',{'ownerId':owner['id']})
        if previous:
            with s.using(previous):s.call('GET','/instances/'+i['id']+'/draft',status=403)
    s.by('注销清空访问凭据而保留原 CR/版本；同 UID 重新启用不自动发布')
    mutations_before=s.k('logs','deployment/monitor-mgr','-n',s.ns).stdout.count('目标API写请求'.encode());assert mutations_before==1
    s.call('POST','/clusters/'+control['id']+'/unregister',{});assert s.cr()['metadata']['uid']==uid
    assert s.call('GET','/instances/'+i['id']+'/versions')['versions'][0]['versionId']==op['versionId']
    script="import json,sqlite3;c=sqlite3.connect('file:/data/platform.db?mode=ro',uri=True);print(json.dumps({'cleared':c.execute('select credential is null from clusters where id=?',("+repr(control['id'])+",)).fetchone()[0]}))"
    assert s.job('credential-cleared',script,True)['cleared']==1
    assert s.operation(s.accept(s.config(),owner=s.owner,path='/clusters/'+control['id']+'/reactivate'))['state']=='SUCCEEDED'
    assert s.cr()['metadata']['uid']==uid
    assert s.k('logs','deployment/monitor-mgr','-n',s.ns).stdout.count('目标API写请求'.encode())==mutations_before


def ksm(s):
    s.prepare();s.by('保存草稿不创建 CR；发布 Deployment 并回读真实参数与 Pod')
    cfg=s.cfg();i=s.create(cfg)
    assert s.k('get','kubestatemetrics',s.crname,target=s.target,check=False).returncode!=0
    s.publish(i);s.runtime(i);assert len([x for x in s.workloads() if x['kind']=='Deployment'])==1
    s.by('替换为双分片 StatefulSet，再替换为三角色 DaemonSet')
    for mode in ('StatefulSetSharded','DaemonSetSharded'):
        cfg=s.cfg(mode=mode)
        if mode=='DaemonSetSharded':
            cfg.update(advancedResources=True,collectors={'mode':'Include','names':['pods','nodes']},metricFilter={'mode':'Deny','patterns':['kube_pod_labels']})
            for role,cpu in [('nodeResources','110m'),('clusterResources','120m'),('unscheduledResources','130m')]:cfg[role]={'mode':'Override','resources':{'requestCPU':cpu,'requestMemory':'64Mi','limitMemory':'256Mi'}}
        else:cfg.update(collectors={'mode':'Include','names':['pods','nodes']},metricFilter={'mode':'Allow','patterns':['kube_.*']})
        s.save(i,cfg);s.publish(i);s.runtime(i)
        if mode=='StatefulSetSharded':assert controller(s,'StatefulSet')['spec']['replicas']==2
        else:
            assert len([x for x in s.workloads() if x['kind']=='DaemonSet'])==1
            for role,cpu in [('node','110m'),('cluster','120m'),('unscheduled','130m')]:
                obj=controller(s,'DaemonSet' if role=='node' else 'Deployment',role);assert obj['spec']['template']['spec']['containers'][0]['resources']['requests']['cpu']==cpu
            assert all('--metric-denylist=kube_pod_labels' in a for a in workload_args(s))
    s.by('零匹配、挂起、恢复和停用分别收敛；data 部署默认 KSM')
    cfg['namespaceFilter']={'mode':'Include','namePatterns':['^no-match-'+s.a.prefix+'$']};s.save(i,cfg);s.publish(i);s.runtime(i,'NoMatch');stopped_workloads(s)
    cfg['namespaceFilter']={'mode':'Include','names':[s.targetns]};cfg['desiredState']='Suspended';s.save(i,cfg);s.publish(i);s.runtime(i,'Suspended');stopped_workloads(s)
    cfg['desiredState']='Running';s.save(i,cfg);s.publish(i);s.runtime(i)
    cfg['desiredState']='Disabled';s.save(i,cfg);s.publish(i);s.runtime(i,'Disabled');stopped_workloads(s)
    s.prepare('-data','data');j=s.create();s.publish(j);s.runtime(j)
    s.by('外部 CR 只能发现，不能通过平台编辑发布或删除')
    obj={k:s.cr()[k] for k in ('apiVersion','kind','spec')};obj['metadata']={'name':s.crname+'-external'}
    s.k('create','-f','-',data=obj,target='data');outside=s.get('kubestatemetrics',obj['metadata']['name'],target='data');s.track_cr(outside['metadata']['uid'],name=obj['metadata']['name'])
    found=next(v for v in s.call('GET','/clusters/'+s.cluster['id']+'/instances')['instances'] if v['uid']==outside['metadata']['uid']);assert found['readOnly']
    s.call('DELETE','/instances/'+found['id'],status=403)
    s.call('PUT','/instances/'+found['id']+'/draft',{'expectedDraftRevision':1,'ksm':s.cfg()},status=403)
    s.call('POST','/instances/'+found['id']+'/publications',{'draftRevision':1,'previewId':'forged','overwriteDrift':False},status=403)


def agent(s):
    s.prepare(target='data',component='agent');s.by('在 data 发布默认 Agent，核对实际 StatefulSet/Pod/PVC 与 None/Own')
    i=s.create();s.publish(i);s.runtime(i);spec=s.cr()['spec']
    assert all(spec.get(k+'Selector') is None and spec.get(k+'NamespaceSelector') is None for k in ('serviceMonitor','podMonitor','probe','scrapeConfig'))
    s.by('一次配置四类真实采集 CR，各自采用代表规则并动态增删匹配')
    cfg=s.cfg();foreign=s.targetns+'-other';s.namespace(foreign,'data');s.k('label','namespace',foreign,'mm18-scope=yes',target='data')
    kinds=[('ServiceMonitor','All','Own'),('PodMonitor','Labels','All'),('Probe','All','Labels'),('ScrapeConfig','Labels','Own')]
    originals={}
    for kind,om,nm in kinds:
        key=kind[0].lower()+kind[1:];rule={'object':{'mode':om},'namespace':{'mode':nm}}
        if om=='Labels':rule['object']['selector']={'matchLabels':{'mm18-match':'yes'}}
        if nm=='Labels':rule['namespace']['selector']={'matchLabels':{'mm18-scope':'yes'}}
        cfg['associations'][key]=rule;obj=s.collection(kind,foreign if nm=='Labels' else s.targetns);originals[kind]=obj['metadata']['uid']
    ref=lambda k:{'name':'agent-auth','key':k}
    cfg['remoteWrite']=[{'url':'http://receiver.example.invalid/none','auth':{'mode':'None'}},{'url':'https://receiver.example.invalid/basic','auth':{'mode':'BasicAuth','username':ref('username'),'password':ref('password')}},{'url':'https://receiver.example.invalid/bearer','auth':{'mode':'Bearer','credentials':ref('credentials')},'tls':{'ca':ref('ca'),'cert':ref('cert'),'key':ref('key'),'serverName':'receiver.example.invalid'}}]
    s.save(i,cfg);s.publish(i);s.runtime(i)
    sources=s.association_preview(i)
    for kind,_,_ in kinds:assert originals[kind] in {v['uid'] for v in sources['association:'+kind]['matches']}
    added=s.collection('PodMonitor',s.targetns,'added');assert added['metadata']['uid'] in {v['uid'] for v in s.association_preview(i)['association:PodMonitor']['matches']}
    s.k('delete','podmonitor','added','-n',s.targetns,target='data');assert added['metadata']['uid'] not in {v['uid'] for v in s.association_preview(i)['association:PodMonitor'].get('matches',[])}
    s.k('label','namespace',foreign,'mm18-scope=no','--overwrite',target='data');assert originals['Probe'] not in {v['uid'] for v in s.association_preview(i)['association:Probe'].get('matches',[])}
    s.by('Operator 生成真实认证/TLS 配置；Secret 缺 key 时阻止发布')
    generated=wait(lambda:s.generated_config(),lambda value:len(value.get('remote_write',[]))==3,message='Agent 发送配置未生成')
    assert len(generated['remote_write'])==3
    assert generated['remote_write'][1]['basic_auth']['username']==s.secret_values['username'] and generated['remote_write'][1]['basic_auth']['password']==s.secret_values['password']
    assert generated['remote_write'][2]['authorization']['credentials']==s.secret_values['credentials']
    assert generated['remote_write'][2]['tls_config'].get('cert_file') and generated['remote_write'][2]['tls_config'].get('key_file')
    s.k('patch','secret','agent-auth','-n',s.targetns,'--type=json','-p','[{"op":"remove","path":"/data/credentials"}]',target='data')
    s.preview(i,status=422)
    s.prepare('-control','control','agent');j=s.create();s.publish(j);s.runtime(j)


def storage(s):
    s.by('两个新 Agent 分别采用两种反向混合策略；逐步核对原 PVC UID')
    for number,(scaled,deleted) in enumerate([('Retain','Delete'),('Delete','Retain')]):
        s.prepare('-mixed'+str(number),'data','agent');cfg=s.cfg();cfg['shards']=2;cfg['storage'].update(whenScaled=scaled,whenDeleted=deleted)
        i=s.create(cfg);s.publish(i);before=owned_graph(s,i,2,1);policies(s,2,scaled,deleted)
        original={v['name']:v['uid'] for v in before['volumes']}
        s.by('策略 '+scaled+'/'+deleted+'：shards 2→1→挂起零副本→恢复→删除')
        cfg['shards']=1;s.save(i,cfg);shrink=s.publish(i);after=owned_graph(s,i,1,1)
        remaining={v['metadata']['uid'] for v in s.get('pvc','-n',s.targetns)['items']}
        assert len(set(original.values())&remaining)==(2 if deleted=='Retain' else 1)
        cfg['desiredState']='Suspended';s.save(i,cfg);suspend=s.publish(i);owned_graph(s,i,1,0)
        volume_result(s,suspend,'Retained' if scaled=='Retain' else 'Deleted')
        active=after['volumes'][0];exists=not s.k('get','pvc',active['name'],'-n',s.targetns,target='data',check=False).returncode
        assert exists==(scaled=='Retain')
        cfg['desiredState']='Running';s.save(i,cfg);s.publish(i);restored=owned_graph(s,i,1,1)
        assert (restored['volumes'][0]['uid']==active['uid'])==(scaled=='Retain')
        bad=copy.deepcopy(cfg);bad['storage']['capacity']='2Gi';s.save(i,bad,status=409)
        bad=copy.deepcopy(cfg);bad['storage']={'mode':'emptyDir','whenScaled':'Retain','whenDeleted':'Retain'};s.save(i,bad,status=409)
        _,op=delete_agent(s,i);volume_result(s,op,'Retained' if deleted=='Retain' else 'Deleted')
        current={v['metadata']['uid'] for v in s.get('pvc','-n',s.targetns)['items']}
        assert (restored['volumes'][0]['uid'] in current)==(deleted=='Retain')
        if deleted=='Retain':
            s.by('替代同名 Retain PVC 后，历史操作仍绑定原 UID，不认领新卷')
            volume=restored['volumes'][0];actual=s.get('pvc',volume['name'],'-n',s.targetns)
            s.delete_uid(s.target,'/api/v1/namespaces/'+s.targetns+'/persistentvolumeclaims/'+volume['name'],volume['uid'])
            s.k('wait','--for=delete','pvc/'+volume['name'],'-n',s.targetns,'--timeout=60s',target=s.target)
            replacement={'apiVersion':'v1','kind':'PersistentVolumeClaim','metadata':{'name':volume['name'],'namespace':s.targetns},'spec':{'accessModes':['ReadWriteOnce'],'storageClassName':actual['spec']['storageClassName'],'resources':{'requests':{'storage':'1Gi'}}}}
            response=s.k('create','-f','-',data=replacement,target=s.target);new_uid=json.loads(response.stdout)['metadata']['uid'];assert new_uid!=volume['uid']
            historical=s.call('POST','/operations/'+op['operationId']+'/refresh',{});identities={v['uid'] for v in historical['storageVolumes']}
            assert volume['uid'] in identities and new_uid not in identities
            assert s.get('pvc',volume['name'],'-n',s.targetns)['metadata']['uid']==new_uid
    s.by('独立 emptyDir 实例运行与删除均无 WAL PVC')
    s.prepare('-empty','data','agent');cfg=s.cfg();cfg['storage']={'mode':'emptyDir','whenScaled':'Retain','whenDeleted':'Retain'}
    i=s.create(cfg);s.publish(i);owned_graph(s,i,1,1,'emptyDir');assert not s.get('pvc','-n',s.targetns)['items'];delete_agent(s,i)


def versions(s):
    s.prepare();s.by('发布 v1 后编辑草稿 v2，同幂等键不产生第二个版本或写入')
    i=s.create();p=s.preview(i);key='publish-once';op=s.publish(i,preview=p,key=key);uid=s.cr()['metadata']['uid']
    writes_before=s.k('logs','deployment/monitor-mgr','-n',s.ns).stdout.count('目标API写请求'.encode());assert writes_before==1
    again=s.publish(i,preview=p,key=key);assert s.k('logs','deployment/monitor-mgr','-n',s.ns).stdout.count('目标API写请求'.encode())==writes_before;assert again['operationId']==op['operationId'];assert len(s.call('GET','/instances/'+i['id']+'/versions')['versions'])==1
    cfg=s.cfg();cfg['resources']['requestCPU']='222m';s.save(i,cfg)
    old=s.call('GET','/instances/'+i['id']+'/versions/'+op['versionId']);assert old['ksm']['resources']['requestCPU']=='100m'
    s.by('外部 spec 漂移必须明确覆盖，foreign annotation 和 status 保留')
    s.k('patch','kubestatemetrics',s.crname,'--type=merge','-p',json.dumps({'metadata':{'annotations':{'external-note':'keep'}},'spec':{'suspended':True}}))
    p=s.preview(i);assert p['drift'];s.publish(i,preview=p,wait=False,status=409);s.publish(i,preview=p,overwrite=True);s.runtime(i)
    assert s.cr()['metadata']['uid']==uid and s.cr()['metadata']['annotations']['external-note']=='keep' and s.cr().get('status')
    s.by('仅 status/resourceVersion 改变仍可使用原预览')
    p=s.preview(i);s.k('patch','kubestatemetrics',s.crname,'--type=merge','--subresource=status','-p','{"status":{"availablePods":0}}');s.publish(i,preview=p);s.runtime(i)


def faults(s):
    s.prepare('-data','data');data_cluster=s.cluster;proxy=s.setup_proxy();i=s.create();s.publish(i);s.runtime(i);data_name=s.crname;data_ns=s.targetns
    s.prepare('-control','control');j=s.create(s.cfg(mode='StatefulSetSharded'));s.publish(j);s.runtime(j)
    s.by('独占代理拒绝 data 读取，control 继续刷新；等待独立来源超过 30 秒')
    proxy.control('faults',{'readMode':'all','rejectWritePath':''})
    deadline=time.monotonic()+45;failed=None
    while time.monotonic()<deadline:
        s.call('POST','/instances/'+i['id']+'/refresh',{});s.call('POST','/instances/'+j['id']+'/refresh',{})
        failed=s.call('GET','/instances/'+i['id']);healthy=s.call('GET','/instances/'+j['id'])
        if any(v.get('stale') and v.get('error') for v in failed.get('sources',[])):break
        time.sleep(1)
    assert any(v.get('stale') and v.get('error') and v.get('historical') for v in failed['sources']);assert healthy['runtime']['summary']=='Available';assert failed['runtime']['summary']!='Deleted'
    assert urllib.request.urlopen(s.base+'/readyz',timeout=2).status==200
    s.by('恢复真实 API 后重新观察；制造单个真实 Pod 未 Ready')
    proxy.control('reset',{});wait(lambda:s.call('POST','/instances/'+i['id']+'/refresh',{}),lambda v:v['summary']=='Available',message='data恢复失败')
    pods=[p for p in s.workloads() if p['kind']=='Pod' and not p['metadata'].get('deletionTimestamp')];assert len(pods)==2
    pod=pods[0];original=pod['spec']['containers'][0]['image'];patch=[{'op':'test','path':'/metadata/uid','value':pod['metadata']['uid']},{'op':'replace','path':'/spec/containers/0/image','value':'python:3.13.2-alpine'}]
    s.k('patch','pod',pod['metadata']['name'],'-n',s.targetns,'--type=json','-p',json.dumps(patch))
    wait(lambda:s.call('POST','/instances/'+j['id']+'/refresh',{}),lambda v:v['summary']=='NotReady' and v.get('availablePods')==1,message='未观察到部分Ready')
    patch[-1]['value']=original;s.k('patch','pod',pod['metadata']['name'],'-n',s.targetns,'--type=json','-p',json.dumps(patch));s.runtime(j)


def recovery(s):
    s.prepare();i=s.create();s.publish(i);s.runtime(i);proxy=s.setup_proxy();cfg=s.cfg();cfg['resources']['requestCPU']='222m';s.save(i,cfg)
    s.by('真实更新完成后丢失响应并阻断读取，重建平台只核验原 UID')
    path='/apis/monitoring.fjsxy.github.io/v1alpha1/kubestatemetrics/'+s.crname
    proxy.arm('PUT',path,'drop_and_block_reads');op=s.publish(i,wait=False);s.state(op,'RESULT_UNCERTAIN')
    before=len(s.mutations(s.capture_proxy(),'PUT'));assert before==1;s.rebuild();proxy.control('reset',{})
    s.call('POST','/operations/'+op['operationId']+'/refresh',{});s.state(op,'SUCCEEDED',timeout=60)
    assert len(s.mutations(s.capture_proxy(),'PUT'))==before
    s.by('首次创建丢响应且没有原 UID，禁止按同名对象补认或重建')
    s.crname='unknown-'+uuid.uuid4().hex[:8];unknown=s.create()
    # 关键：先登记明确的创建身份，代理才会在丢响应前保存原 API UID；不能事后按名字补认。
    proxy.control('register-create',{'intentId':uuid.uuid4().hex,'identity':{'apiVersion':'monitoring.fjsxy.github.io/v1alpha1','kind':'KubeStateMetrics','namespace':'','name':s.crname}})
    proxy.arm('POST','/apis/monitoring.fjsxy.github.io/v1alpha1/kubestatemetrics','drop_after_upstream');o=s.publish(unknown,wait=False);s.state(o,'IDENTITY_UNCERTAIN')
    events=s.capture_proxy();created=[e for e in events if e['stage']=='original_create_response' and e['path'].endswith('/kubestatemetrics')];assert len(created)==1
    s.track_cr(created[0]['originalCreate']['uid']);uid=s.cr()['metadata']['uid'];assert uid==created[0]['originalCreate']['uid'];s.rebuild();s.action(o,'retry',409);s.action(o,'continue-unsent',409);s.call('POST','/instances/'+unknown['id']+'/deletions',{'versionId':o['versionId']},status=409);assert len(s.mutations(s.capture_proxy(),'POST'))==1;assert s.call('GET','/instances/'+unknown['id'])['uid']=='' and s.cr()['metadata']['uid']==uid
    s.by('确定失败后显式 retry，保留原冻结版本并产生新 attempt')
    proxy.control('reset',{});s.crname='retry-'+uuid.uuid4().hex[:8];retry=s.create();p=s.preview(retry)
    writepath='/apis/monitoring.fjsxy.github.io/v1alpha1/kubestatemetrics';proxy.control('faults',{'readMode':'none','rejectWritePath':writepath})
    failure=s.publish(retry,preview=p,wait=False);s.state(failure,'FAILED');cfg=s.cfg();cfg['resources']['requestCPU']='999m';s.save(retry,cfg);proxy.control('reset',{})
    again=s.action(failure,'retry');s.state(again,'SUCCEEDED');assert again['versionId']==failure['versionId'] and again['attemptId']!=failure['attemptId'];assert s.cr()['spec']['podResources']['requests']['cpu']=='100m';s.track_cr(s.cr()['metadata']['uid'])


def deletion(s):
    s.prepare();unrelated=s.a.prefix+'-unrelated';s.namespace(unrelated)
    kept={'apiVersion':'v1','kind':'ConfigMap','metadata':{'name':'keep','namespace':unrelated},'data':{'marker':'owned-fixture'}}
    kept_uid=json.loads(s.k('create','-f','-',data=kept).stdout)['metadata']['uid']
    s.by('原 CR finalizer 阻塞真实 Foreground 删除；移除后等待原图完全消失')
    i=s.create();s.publish(i);s.runtime(i);original=s.cr();uid=original['metadata']['uid']
    finalizers=original['metadata'].get('finalizers',[])+['phase1.fixture/hold']
    s.k('patch','kubestatemetrics',s.crname,'--type=json','-p',json.dumps([{'op':'test','path':'/metadata/uid','value':uid},{'op':'add','path':'/metadata/finalizers','value':finalizers}]))
    op=s.delete(i,False);s.state(op,'DELETION_BLOCKED','DELETION_VERIFYING');assert s.call('GET','/operations/'+op['operationId'])['state']!='SUCCEEDED'
    s.call('POST','/clusters/'+s.cluster['id']+'/unregister',{},status=409);s.call('POST','/clusters/'+s.cluster['id']+'/transfer',{'ownerId':s.owner},status=409)
    cr=s.cr();s.k('patch','kubestatemetrics',s.crname,'--type=json','-p',json.dumps([{'op':'test','path':'/metadata/uid','value':uid},{'op':'test','path':'/metadata/resourceVersion','value':cr['metadata']['resourceVersion']},{'op':'replace','path':'/metadata/finalizers','value':[f for f in cr['metadata'].get('finalizers',[]) if f!='phase1.fixture/hold']}]))
    wait(lambda:s.call('POST','/operations/'+op['operationId']+'/refresh',{}),lambda value:value['state']=='SUCCEEDED',message='前台删除原图未结束')
    assert not s.workloads() and s.k('get','kubestatemetrics',s.crname,check=False).returncode!=0
    s.by('同名 CR 替换成新 UID 后，旧平台实例不能删除替代对象')
    s.crname='replace-'+uuid.uuid4().hex[:8];i=s.create();s.publish(i);s.runtime(i);old=s.cr();uid=old['metadata']['uid']
    p=s.call('POST','/instances/'+i['id']+'/deletion-previews',{});s.delete_uid(s.target,'/apis/monitoring.fjsxy.github.io/v1alpha1/kubestatemetrics/'+s.crname,uid)
    wait(lambda:s.k('get','kubestatemetrics',s.crname,check=False).returncode,lambda v:v!=0,message='旧CR未删除')
    obj={k:old[k] for k in ('apiVersion','kind','spec')};obj['metadata']={'name':s.crname};s.k('create','-f','-',data=obj);newuid=s.cr()['metadata']['uid'];assert uid!=newuid;s.track_cr(newuid)
    s.call('POST','/instances/'+i['id']+'/deletions',{'versionId':p['versionId'],'previewId':p['previewId']},status=409);assert s.cr()['metadata']['uid']==newuid
    assert s.get('namespace',unrelated)['metadata']['uid']==s.namespace_uids[('control',unrelated)]
    preserved=s.get('configmap','keep','-n',unrelated);assert preserved['metadata']['uid']==kept_uid and preserved['data']==kept['data']


def browser(s):
    s.prepare();s.by('停止平台夹具，预置超出 JavaScript 安全整数范围的草稿 revision')
    i=s.create();cookies=s.cookies;s.forward.terminate();s.forward.wait(timeout=5);del s.forward
    s.k('scale','deployment/monitor-mgr','-n',s.ns,'--replicas=0');s.k('wait','--for=delete','pods','-l','app=monitor-mgr','-n',s.ns,'--timeout=60s')
    script="import json,sqlite3;c=sqlite3.connect('/data/platform.db');c.execute('update drafts set revision=? where instance_id=?',(9007199254740993,"+repr(i['id'])+"));c.commit();print(json.dumps({'ok':True}))"
    assert s.job('large-revision',script)['ok'];s.k('scale','deployment/monitor-mgr','-n',s.ns,'--replicas=1');s.k('rollout','status','deployment/monitor-mgr','-n',s.ns,'--timeout=120s')
    pod=s.get('pods','-n',s.ns,'-l','app=monitor-mgr',target='control')['items'][0];s.pod=pod['metadata']['name'];s.platform_pod_uid=pod['metadata']['uid'];s.cookies=cookies;s.connect()
    fixture=s.out/'browser-fixture.json';fixture.write_text(json.dumps(dict(password=s.password,clusterId=s.cluster['id'],namespace=s.targetns,largeId=i['id'],largeName=i['name'])));os.chmod(fixture,0o600)
    s.by('真实 Chromium 完成表单保存、精确大 revision、YAML/diff、发布、工作台与删除')
    out=s.out/'playwright';report_path=s.out/'playwright.json';env=dict(os.environ,BASE_URL=s.base,PHASE1_E2E_FIXTURE=str(fixture),PLAYWRIGHT_JSON_OUTPUT_FILE=str(report_path))
    r=s.env.processes.run(['npm','--prefix','web','run','test:e2e','--','tests/phase1.spec.ts','--reporter=json','--output',str(out)],label='playwright-main-flow',cwd=ROOT,env=env,timeout=240)
    report=json.loads(report_path.read_text()) if report_path.is_file() else {}
    # 仅截图和数字结果公开；原 Playwright 输出含本地 fixture 路径，保留在私有目录。
    for path in out.rglob('*.png'):
        import shutil
        destination=s.env.public/'browser';destination.mkdir(exist_ok=True);shutil.copyfile(path,destination/path.name)
    assert r.returncode==0 and report.get('stats',{}).get('expected')==1 and not report['stats']['unexpected'] and not report['stats']['skipped'],'真实浏览器主流程失败'
    draft=s.call('GET','/instances/'+i['id']+'/draft');assert draft['revision']=='9007199254740994'
    current=s.call('GET','/instances/'+i['id']);assert current['lifecycle']=='DELETED'


CASES={'E2E-01':platform,'E2E-02':clusters,'E2E-03':ksm,'E2E-04':agent,'E2E-05':storage,'E2E-06':versions,'E2E-07':faults,'E2E-08':recovery,'E2E-09':deletion,'E2E-10':browser}
