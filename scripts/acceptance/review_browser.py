"""评审B2真实平台Pod编排；使用原共享Service、代理、脱敏和原UID自然清理。"""
import json
from agent import Suite as AgentSuite
from workbench_browser import service_access,execute

def run_browser(s,case,index):
 s.target='control';s.targetns=s.a.prefix+'-browser-'+str(index);s.crname=s.a.prefix+'-agent-'+str(index)
 s.fixture(s.targetns);proxy=s.use_proxy();s.cluster=s.register('control',proxy.config)
 agent=AgentSuite.create(s);AgentSuite.publish(s,agent);proof=AgentSuite.runtime(s,agent)
 cr=proof['cr'];path='/apis/'+cr['apiVersion']+'/namespaces/'+s.targetns+'/prometheusagents/'+s.crname
 fixture={'adminUsername':'admin','adminPassword':s.password,'clusterId':s.cluster['id'],'clusterName':s.cluster['name'],'namespace':s.targetns,'runId':s.a.prefix,'reviewAgent':{'id':agent['id'],'name':s.crname,'uid':cr['metadata']['uid'],'failureControl':{'baseURL':proxy.base,'controlToken':proxy.token,'writePath':path}}}
 service_access(s);pod=s.get('pod',s.pod,'-n',s.ns)
 statuses={v['name']:v for v in pod['status']['containerStatuses']}
 evidence={'actualAgent':proof,'browserPlatform':{'service':'monitor-mgr','namespace':s.ns,'podUID':pod['metadata']['uid'],'node':pod['spec']['nodeName'],'containers':[{'name':v['name'],'image':v['image'],'containerID':statuses[v['name']]['containerID'],'imageID':statuses[v['name']]['imageID']} for v in pod['spec']['containers']]}}
 # 执行前先落原资源/容器身份，后续浏览器断言失败也不能丢掉真实前置；不保存私有fixture。
 (s.out/(case+'.preparation.json')).write_text(json.dumps(evidence,ensure_ascii=False,indent=2)+'\n')
 evidence['browser']=execute(s,case,fixture,test_file='tests/review.spec.ts')
 evidence['currentInstance']=s.call('GET','/instances/'+agent['id'])
 return evidence
