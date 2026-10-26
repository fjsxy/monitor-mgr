"""迁移前后真实 Agent 进程 targets 与空 scrape receiver 样本采证。"""
import copy
import json
import time
from agent import Suite as AgentSuite
from agent_storage import wait
from e2e_environment import ROOT, LOCK


def _targets(s, namespace, pod):
    result=json.loads(s.k('get','--raw','/api/v1/namespaces/'+namespace+'/pods/'+pod+':9090/proxy/api/v1/targets?state=active',target=s.target).stdout)
    return [{'scrapePool':item.get('scrapePool'),'labels':{key:value for key,value in item.get('labels',{}).items() if key in ('job','instance','service','namespace','endpoint','pod')},'health':item.get('health'),'lastScrape':item.get('lastScrape')} for item in result.get('data',{}).get('activeTargets',[])]


def begin(s, records):
    from agent_collection import observe_agent, adopted_configuration
    exporter_ns=s.targetns;collector_ns=exporter_ns+'-agent';s.fixture(collector_ns,s.target)
    scope='monitor-mgr.test/migration-scope';s.k('label','namespace',exporter_ns,scope+'='+s.a.prefix,target=s.target)
    target=s.env.targets[s.target];receiver='migration-receiver'
    s.env.command(['helm','upgrade','--install',receiver,ROOT/'charts/test-receiver','--kubeconfig',target['kubeconfig'],'--kube-context',target['context'],'--namespace',collector_ns,'--set-string','image='+LOCK['images']['prometheus']['image'],'--set-string','runId='+s.env.run_id,'--wait','--timeout','120s'],'helm-migration-receiver',timeout=150)
    receiver_pod=s.get('pods','-n',collector_ns,'-l','app='+receiver,target=s.target)['items'][0]['metadata']['name']
    receiver_before=adopted_configuration(s,collector_ns,receiver_pod,target=s.target)
    user=s.call('GET','/instances/'+records[0]['instance']['id'])['creatorUserId'];name='migration-agent'
    cfg=AgentSuite.cfg(s,name=name,namespace=collector_ns)
    cfg['associations']['serviceMonitor']={'object':{'mode':'Labels','selector':{'matchLabels':{'monitor-mgr.io/user-id':user}}},'namespace':{'mode':'Labels','selector':{'matchLabels':{scope:s.a.prefix}}}}
    cfg['remoteWrite']=[{'url':'http://'+receiver+'.'+collector_ns+'.svc:9090/api/v1/write','auth':{'mode':'None'}}]
    s.component='agent';s.targetns=collector_ns;s.crname=name
    agent=s.create(cfg);s.publish(agent)
    identity=wait(lambda:observe_agent(s,agent,collector_ns,name,target=s.target),bool,timeout=150,message='迁移用真实Agent未Ready')
    pod=identity['pod']['metadata']['name'];expected={'serviceMonitor/'+record['old']['metadata']['namespace']+'/'+record['old']['metadata']['name']+'/0' for record in records}
    adopted=wait(lambda:adopted_configuration(s,collector_ns,pod,target=s.target),lambda value:set(value['jobs'])==expected,timeout=120,message='Agent未采用全部旧SM jobs')
    targets=wait(lambda:_targets(s,collector_ns,pod),lambda values:len(values)>=len(expected) and all(value['health']=='up' for value in values) and {value['scrapePool'] for value in values}==expected,timeout=150,message='旧SM真实targets未采集成功')
    s.component='ksm';s.targetns=exporter_ns
    return {'namespace':collector_ns,'receiverPod':receiver_pod,'receiverBefore':receiver_before,'agent':agent,'agentName':name,'agentPod':pod,'agentBefore':identity,'adoptedBefore':adopted,'targetsBefore':targets,'cutoverStarted':time.time()}


def finish(s, records, observation):
    from agent_collection import exporter_targets, receiver_query, observe_agent, adopted_configuration
    ns=observation['namespace'];pod=observation['agentPod'];exports=[exporter_targets(s,record['instance'],observation['cutoverStarted']) for record in records]
    expected={target['job'] for exported in exports for target in exported['targets']}
    adopted=wait(lambda:adopted_configuration(s,ns,pod,target=s.target),lambda value:set(value['jobs'])==expected,timeout=150,message='迁移后Agent未采用精确Operator jobs')
    targets=wait(lambda:_targets(s,ns,pod),lambda values:len(values)>=len(expected) and all(value['health']=='up' for value in values) and {value['scrapePool'] for value in values}==expected,timeout=150,message='迁移后真实targets未稳定')
    prometheus=adopted['externalLabels']['prometheus'];expression='kube_pod_info{namespace='+json.dumps(s.targetns)+',prometheus='+json.dumps(prometheus)+'}'
    expected_count=sum(len(exported['targets'])*len(exported['pods']) for exported in exports)
    pairs={(target['service'],target['instance'],target['endpoint']) for exported in exports for target in exported['targets']}
    selected=lambda values:[value for value in values if (value['metric'].get('service'),value['metric'].get('instance'),value['metric'].get('endpoint')) in pairs]
    samples=wait(lambda:selected(receiver_query(s,ns,observation['receiverPod'],expression,target=s.target)),lambda values:len(values)==expected_count,timeout=150,message='迁移后真实remote_write样本矩阵不完整')
    # Prometheus查询会保留lookback内旧job历史样本；显式只取当前采用jobs的样本，同时activeTargets必须精确无旧job。
    current_samples=samples
    timestamps=wait(lambda:selected(receiver_query(s,ns,observation['receiverPod'],'timestamp('+expression+')',target=s.target)),lambda values:len(values)==expected_count and all(float(value['value'][1])>=observation['cutoverStarted'] for value in values),timeout=150,message='切换后的原始样本时间未更新')
    return {key:observation[key] for key in ('receiverBefore','agentBefore','adoptedBefore','targetsBefore','cutoverStarted')}|{'receiverAfter':adopted_configuration(s,ns,observation['receiverPod'],target=s.target),'agentAfter':observe_agent(s,observation['agent'],ns,observation['agentName'],target=s.target),'adoptedAfter':adopted,'targetsAfter':targets,'exports':exports,'samples':current_samples,'timestamps':timestamps}
