"""#30真实数据面：独占空采集 receiver，仅以 Agent remote_write 收到的目标样本验收。"""
import copy
import hashlib
import json
import re
import time
import urllib.parse
import uuid
import yaml

from agent import Suite as AgentSuite
from agent_storage import wait
from e2e_environment import ROOT, LOCK, RUN_LABEL
from testing_contract import validate_agent_collection

USER_LABEL = 'monitor-mgr.io/user-id'
SCOPE_LABEL = 'monitor-mgr.test/exporters'


def _api(s, namespace, pod, path):
    return json.loads(s.k('get', '--raw', '/api/v1/namespaces/'+namespace+'/pods/'+pod+':9090/proxy'+path, target='data').stdout)


def _adopted(s, namespace, pod):
    response = _api(s, namespace, pod, '/api/v1/status/config')
    assert response['status'] == 'success'
    return yaml.safe_load(response['data']['yaml'])


def wait_associations(s, instance, timeout=60):
    """只等待真实公开预检收敛，超时保留原产品拒绝；不以 Deployment Ready 替代能力。"""
    deadline = time.monotonic()+timeout
    while True:
        sources = s.association_preview(instance)
        errors = {k:v['error'] for k,v in sources.items() if v.get('error')}
        if not errors:
            return sources
        if time.monotonic() >= deadline:
            raise AssertionError('Agent真实关联依赖未收敛：'+json.dumps(errors,ensure_ascii=False))
        time.sleep(1)


def _operator_proof(s):
    """白名单能力证据绑定同原 Pod/container；指标原文只保留在私有命令记录。"""
    dep=s.get('deployment','prometheus-operator','-n','mm-operators',target='data')
    rs={r['metadata']['uid'] for r in s.get('replicasets','-n','mm-operators',target='data')['items'] if any(o.get('uid')==dep['metadata']['uid'] and o.get('controller') for o in r['metadata'].get('ownerReferences',[]))}
    pods=[p for p in s.get('pods','-n','mm-operators',target='data')['items'] if not p['metadata'].get('deletionTimestamp') and any(o.get('uid') in rs and o.get('controller') for o in p['metadata'].get('ownerReferences',[]))]
    assert len(pods)==1
    pod=pods[0];proof=s.env.verify_container(pod,'prometheus-operator',s.env.official_configs['promop'])
    raw=s.k('get','--raw','/api/v1/namespaces/mm-operators/pods/'+pod['metadata']['name']+':8080/proxy/metrics',target='data').stdout.decode()
    metrics=[]
    for line in raw.splitlines():
        fields=line.split()
        if len(fields)>=2:
            try:metrics.append((fields[0],float(fields[1])))
            except ValueError:pass
    agent=any(m.startswith('prometheus_operator_ready{') and 'controller="prometheus-agent"' in m and v==1 for m,v in metrics)
    scrape=any(m.startswith('prometheus_operator_kubernetes_client_http_request_duration_seconds_count{') and re.search(r'endpoint="/apis/monitoring\.coreos\.com/v1alpha1/(namespaces/[^/]+/)?scrapeconfigs"',m) and v>0 for m,v in metrics)
    after=s.get('pod',pod['metadata']['name'],'-n','mm-operators',target='data')
    before_id=next(c['containerID'] for c in pod['status']['containerStatuses'] if c['name']=='prometheus-operator')
    assert after['metadata']['uid']==pod['metadata']['uid'] and next(c['containerID'] for c in after['status']['containerStatuses'] if c['name']=='prometheus-operator')==before_id
    assert agent and scrape
    return dict(**proof,agentControllerReady=agent,scrapeConfigInformerObserved=scrape)


def creator_collection(s):
    """I30-K01/02：每个角色独立配置/样本，加同用户新 exporter 的不重发动态发现。"""
    s.by('I30-K01：Helm部署空scrape的锁定Prometheus receiver，普通用户和管理员各创建exporter/Agent')
    s.prepare('-collection','data','agent')
    collector_ns=s.targetns;cluster=s.cluster;target=s.env.targets['data'];receiver='remote-write'
    s.env.command(['helm','upgrade','--install',receiver,ROOT/'charts/test-receiver','--kubeconfig',target['kubeconfig'],'--kube-context',target['context'],'--namespace',collector_ns,'--set-string','image='+LOCK['images']['prometheus']['image'],'--set-string','runId='+s.env.run_id,'--wait','--timeout','120s'],'helm-test-receiver',timeout=150)
    receiver_pods=s.get('pods','-n',collector_ns,'-l','app='+receiver,target='data')['items'];assert len(receiver_pods)==1
    receiver_pod=receiver_pods[0];receiver_name=receiver_pod['metadata']['name']
    receiver_proof=s.env.verify_container(receiver_pod,'prometheus',s.env.official_configs['prometheus'])
    receiver_config=_adopted(s,collector_ns,receiver_name)
    assert receiver_config.get('scrape_configs',[])==[] and not receiver_config.get('remote_write')
    assert '--web.enable-remote-write-receiver' in receiver_pod['spec']['containers'][0]['args']
    url='http://'+receiver+'.'+collector_ns+'.svc:9090/api/v1/write'
    receipt=dict(testedCommit=s.env.candidate,runId=s.env.run_id,metric='kube_pod_info',receiver=dict(**receiver_proof,namespace=collector_ns,pod=receiver_name,image=LOCK['images']['prometheus']['image'],scrapeConfigs=[],configSHA256=hashlib.sha256(yaml.safe_dump(receiver_config).encode()).hexdigest()),groups=[])
    user,user_client=s.user('collection');s.call('POST','/clusters/'+cluster['id']+'/transfer',{'ownerId':user['id']})
    admin_id=s.call('GET','/session')['account']['id']

    def context(component,namespace,name):
        s.component=component;s.target='data';s.targetns=namespace;s.crname=name;s.cluster=cluster

    def exporter(role,number,client):
        namespace=s.a.prefix+'-'+role+'-exp'+str(number);s.namespace(namespace,'data')
        s.k('label','namespace',namespace,SCOPE_LABEL+'='+s.a.prefix,target='data')
        name=role+'-exporter-'+uuid.uuid4().hex[:8];context('ksm',namespace,name)
        cfg=s.cfg();cfg['collectors']={'mode':'Include','names':['pods']}
        started=time.time()
        with s.using(client):
            instance=s.create(cfg);operation=s.publish(instance);s.runtime(instance)
        current=s.call('GET','/instances/'+instance['id']);version=s.call('GET','/instances/'+instance['id']+'/versions/'+operation['versionId'])
        frozen=next(r for r in version['resources'] if r['key']=='service-monitor');sm=s.get('servicemonitor',frozen['name'],'-n',namespace,target='data')
        assert sm['spec']==yaml.safe_load(frozen['yaml'])['spec'] and sm['metadata']['labels'][USER_LABEL]==current['creatorUserId']
        selector=sm['spec']['selector']['matchLabels'];services=[v for v in s.get('services','-n',namespace,target='data')['items'] if all(v['metadata'].get('labels',{}).get(k)==x for k,x in selector.items())];assert len(services)==1
        service=services[0];assert any(o.get('uid')==current['uid'] and o.get('controller') for o in service['metadata']['ownerReferences'])
        endpoints=wait(lambda:s.get('endpoints',service['metadata']['name'],'-n',namespace,target='data'),lambda v:bool(v.get('subsets')) and any(x.get('addresses') for x in v['subsets']),message='exporter真实Endpoints未收敛')
        addresses=[(a,p['port']) for subset in endpoints['subsets'] for a in subset.get('addresses',[]) for p in subset['ports'] if p['name']=='http-metrics'];assert len(addresses)==1
        address,port=addresses[0];ref=address['targetRef'];assert ref['kind']=='Pod'
        pod=s.get('pod',ref['name'],'-n',namespace,target='data');assert pod['metadata']['uid']==ref['uid']
        proof=s.env.verify_container(pod,pod['spec']['containers'][0]['name'],s.env.official_configs['ksm'])
        return dict(instanceId=instance['id'],creatorUserId=current['creatorUserId'],uid=current['uid'],namespace=namespace,name=name,service=service['metadata']['name'],serviceUID=service['metadata']['uid'],serviceMonitor=sm['metadata']['name'],serviceMonitorUID=sm['metadata']['uid'],pod=pod['metadata']['name'],podUID=pod['metadata']['uid'],instance=address['ip']+':'+str(port),container=proof,createdAt=started)

    def sample(group,exported,not_before):
        labels={'namespace':exported['namespace'],'service':exported['service'],'exported_pod':exported['pod'],'uid':exported['podUID'],'prometheus':group['externalPrometheus']}
        expression='kube_pod_info{'+','.join(k+'='+json.dumps(v) for k,v in labels.items())+'}'
        def query(expr,at=None):
            params={'query':expr}
            if at is not None:params['time']=at
            response=_api(s,collector_ns,receiver_name,'/api/v1/query?'+urllib.parse.urlencode(params));assert response['status']=='success' and response['data']['resultType']=='vector';return response['data']['result']
        result=wait(lambda:query(expression),lambda values:len(values)==1,message='receiver未收到本例Agent/exporter实际kube_pod_info样本')
        value=result[0];assert float(value['value'][1])==1 and value['metric']['instance']==exported['instance']
        timestamps=query('timestamp('+expression+')',value['value'][0]);assert len(timestamps)==1
        sample_time=float(timestamps[0]['value'][1]);assert not_before<=sample_time<=time.time()+5
        return dict(metric=value['metric'],value=float(value['value'][1]),sampleTime=sample_time,queryTime=float(value['value'][0]),notBefore=not_before,receiverPodUID=receiver_proof['podUID'])

    def adopted(group,expected,excluded):
        def read():
            value=_adopted(s,collector_ns,group['pod']);jobs={j['job_name'] for j in value.get('scrape_configs',[])}
            return value,jobs
        value,jobs=wait(read,lambda v:expected in v[1],message='Agent运行进程未采用本例配套ServiceMonitor')
        assert not (jobs&set(excluded)) and [v['url'] for v in value['remote_write']]==[url]
        current=s.get('pod',group['pod'],'-n',collector_ns,target='data');proof=s.env.verify_container(current,'prometheus',s.env.official_configs['prometheus']);assert proof['podUID']==group['container']['podUID'] and proof['containerID']==group['container']['containerID']
        group['configSHA256']=hashlib.sha256(yaml.safe_dump(value).encode()).hexdigest();group['adoptedJobs']=sorted(jobs)
        group['externalPrometheus']=value['global']['external_labels']['prometheus']
        assert group['externalPrometheus']==collector_ns+'/'+group['name']

    admin_client=s.http
    exports={role:exporter(role,1,client) for role,client in [('user',user_client),('admin',admin_client)]}
    for role,client,creator in [('user',user_client,user['id']),('admin',admin_client,admin_id)]:
        name=role+'-agent-'+uuid.uuid4().hex[:8];context('agent',collector_ns,name)
        cfg=s.cfg();cfg['remoteWrite']=[{'url':url,'auth':{'mode':'None'}}]
        with s.using(client):
            instance=s.create(cfg);assert instance['creatorUserId']==creator
            assert cfg['associations']['serviceMonitor']['object']['mode']=='None'
            cfg['associations']['serviceMonitor']['object']={'mode':'Labels','selector':{'matchLabels':{USER_LABEL:creator}}}
            s.save(instance,cfg);own=wait_associations(s,instance)['association:ServiceMonitor'];assert not own.get('matches') and not own.get('error')
            cfg['associations']['serviceMonitor']['namespace']={'mode':'Labels','selector':{'matchLabels':{RUN_LABEL:s.env.run_id,SCOPE_LABEL:s.a.prefix}}}
            s.save(instance,cfg);matches=wait_associations(s,instance)['association:ServiceMonitor'];assert {v['uid'] for v in matches['matches']}=={exports[role]['serviceMonitorUID']}
            started=time.time();operation=s.publish(instance);runtime=s.runtime(instance)
        group=dict(role=role,creatorUserId=creator,instanceId=instance['id'],name=name,uid=runtime['cr']['metadata']['uid'],pod=runtime['pod']['name'],container=runtime['pod']['imageProof'],versionId=operation['versionId'],selector=cfg['associations']['serviceMonitor'],exports=[exports[role]])
        expected='serviceMonitor/'+exports[role]['namespace']+'/'+exports[role]['serviceMonitor']+'/0'
        other=exports['admin' if role=='user' else 'user'];excluded=['serviceMonitor/'+other['namespace']+'/'+other['serviceMonitor']+'/0']
        adopted(group,expected,excluded);group['initialConfigSHA256']=group['configSHA256'];group['initialAdoptedJobs']=list(group['adoptedJobs']);group['samples']=[sample(group,exports[role],started)]
        receipt['groups'].append(group)
        if role=='user':user_group,user_instance,user_cfg=group,instance,copy.deepcopy(cfg)
        else:
            # 普通用户对管理员Agent写原生同user标签仍须被服务端拒绝。
            with s.using(user_client):s.save(instance,cfg,status=403)
    s.by('I30-K02：管理员编辑用户Agent不改目标，既有Agent动态采用同creator新exporter并收到新样本')
    context('agent',collector_ns,user_group['name']);user_cfg['resources']['requestCPU']='210m'
    s.save(user_instance,user_cfg)
    saved=s.call('GET','/instances/'+user_instance['id']+'/draft');assert saved['agent']['associations']['serviceMonitor']['object']['selector']['matchLabels'][USER_LABEL]==user['id']
    before=s.call('GET','/instances/'+user_instance['id']);new_export=exporter('user',2,user_client)
    context('agent',collector_ns,user_group['name'])
    expected='serviceMonitor/'+new_export['namespace']+'/'+new_export['serviceMonitor']+'/0';other=exports['admin']
    adopted(user_group,expected,['serviceMonitor/'+other['namespace']+'/'+other['serviceMonitor']+'/0'])
    user_group['exports'].append(new_export);user_group['samples'].append(sample(user_group,new_export,new_export['createdAt']))
    after=s.call('GET','/instances/'+user_instance['id']);assert before['uid']==after['uid']==user_group['uid'] and before['currentVersionId']==after['currentVersionId']==user_group['versionId']
    receipt['dynamic']=dict(agentUID=after['uid'],versionBefore=before['currentVersionId'],versionAfter=after['currentVersionId'],oldConfigSHA256=user_group['initialConfigSHA256'],newConfigSHA256=user_group['configSHA256'],jobsBefore=user_group['initialAdoptedJobs'],jobsAfter=user_group['adoptedJobs'],newExporterUID=new_export['uid'],newServiceMonitorUID=new_export['serviceMonitorUID'],unchangedPublication=True)
    # 接收端和发送端都必须仍是本例原进程，空scrape约束在收到样本后再次核验。
    receiver_after=s.get('pod',receiver_name,'-n',collector_ns,target='data');assert s.env.verify_container(receiver_after,'prometheus',s.env.official_configs['prometheus'])==receiver_proof
    assert not _adopted(s,collector_ns,receiver_name).get('scrape_configs')
    receipt['operator']=_operator_proof(s)
    admin_group=next(g for g in receipt['groups'] if g['role']=='admin')
    admin_export=exports['admin']
    adopted(admin_group,'serviceMonitor/'+admin_export['namespace']+'/'+admin_export['serviceMonitor']+'/0',user_group['adoptedJobs'])
    s.by('I30-K03：零匹配、真实读取失败与实际ServiceMonitor权限不足分别返回独立结果')
    proxy=s.setup_proxy()
    try:
        proxy.control('faults',{'readMode':'all','rejectWritePath':''})
        failed=s.association_preview(user_instance)['association:ServiceMonitor']
        assert failed.get('error','').startswith('TARGET_ERROR') and not failed.get('lastSuccessAt')
    finally:
        proxy.control('reset',{})
    s.restricted_credentials('servicemonitors')
    try:
        forbidden=s.association_preview(user_instance)['association:ServiceMonitor']
        assert forbidden.get('error','').startswith('TARGET_FORBIDDEN') and not forbidden.get('lastSuccessAt')
        assert forbidden['error']!=failed['error']
    finally:
        s.restore_credentials()
    receipt['previewBoundaries']=dict(zeroMatchSuccessful=True,readFailureObserved=True,permissionFailureObserved=True,distinctReasons=True)
    validate_agent_collection(receipt,s.env.candidate,s.env.run_id)
    (s.env.public/'agent-collection.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n')
    s.agent_collection=receipt
