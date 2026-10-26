"""#29 的旧库/旧资源夹具与原始证据，仅操作本 run 独占平台和已登记 UID。"""
import copy
import json
import time
import uuid
from agent_storage import wait


# 提取旧操作不可改写的原状态、UID 与发送事实。
def _facts(operation):
    return {'operationId': operation['operationId'], 'versionId': operation['versionId'], 'state':operation['state'], 'attemptId':operation['attemptId'],
            'resources': [{key: copy.deepcopy(resource[key]) for key in ('target', 'state', 'confirmedUid', 'possiblySent')} for resource in operation['resources']]}


def pure_enable(s, instance, old_operation, original_uid, automatic_before, automatic_after):
    """复用 E2E-01 已真实升级的单 CR，新的明确发布才写采集意图。"""
    s.by('I29-K01：真实 v10 升级后原父 UID 保留，新的明确发布只冻结父 CR')
    old_version = s.call('GET', '/instances/' + instance['id'] + '/versions/' + old_operation['versionId'])
    old = _facts(s.call('GET', '/operations/' + old_operation['operationId']))
    before = s.call('GET', '/instances/' + instance['id'])
    cfg = copy.deepcopy(s.call('GET', '/instances/' + instance['id'] + '/draft')['ksm'])
    cfg['serviceMonitor'] = {'enabled': True, 'interval': '30s', 'scrapeTimeout': '10s'}
    s.save(instance, cfg)
    published = s.publish(instance)
    parent = wait(lambda: s.get('kubestatemetrics', instance['name'], target=s.target), lambda obj: obj.get('status', {}).get('serviceMonitor', {}).get('state') == 'Ready', timeout=100, message='历史 exporter 显式启用未收敛')
    reference = parent['status']['serviceMonitor']['resource']
    companion = s.get('servicemonitor', reference['name'], '-n', reference['namespace'], target=s.target)
    sm_writes = [event for event in s.capture_proxy() if '/servicemonitors' in event['path'] and event['method'] in ('POST','PUT','PATCH','DELETE')]
    s.acceptance_evidence['issue29'] = {'testedCommit':s.env.candidate, 'kind':'pure', 'originalUid':original_uid,
        'currentUid':s.call('GET','/instances/'+instance['id'])['uid'], 'automaticBefore':automatic_before,'automaticAfter':automatic_after,
        'oldVersion':old_version,'oldVersionAfter':s.call('GET','/instances/'+instance['id']+'/versions/'+old_operation['versionId']),
        'oldOperation':old,'oldOperationAfter':_facts(s.call('GET','/operations/'+old_operation['operationId'])),
        'newOperation':_facts(published),'newVersion':s.call('GET','/instances/'+instance['id']+'/versions/'+published['versionId']),
        'creatorUserId':before['creatorUserId'],'parent':parent,'companion':companion,'platformSMWrites':len(sm_writes)}


def _offline(s, script):
    """只在独占平台停止后变更旧夹具，原 PVC/会话和其它实例保持。"""
    s.stop_forward()
    s.k('scale','deployment/monitor-mgr','-n',s.ns,'--replicas=0')
    s.k('wait','--for=delete','pods','-l','app=monitor-mgr','-n',s.ns,'--timeout=60s')
    result = s.job('historical-exporter', script)
    s.k('scale','deployment/monitor-mgr','-n',s.ns,'--replicas=1')
    s.k('rollout','status','deployment/monitor-mgr','-n',s.ns,'--timeout=120s')
    pod = next(p for p in s.get('pods','-n',s.ns,'-l','app=monitor-mgr',target='control')['items'] if not p['metadata'].get('deletionTimestamp'))
    s.pod=pod['metadata']['name'];s.platform_pod_uid=pod['metadata']['uid'];s.connect()
    return result


def prepare_legacy(s, variants=('SUCCEEDED','IDENTITY_UNCERTAIN')):
    """原独立 v12 DDL 的 SM 行由夹具重建，产品不会创建旧 SM 或伪造执行记录。"""
    records=[]
    for variant in variants:
        s.crname='legacy-'+uuid.uuid4().hex[:10]
        cfg=s.cfg();cfg['serviceMonitor']={'enabled':False,'interval':'30s','scrapeTimeout':'10s'}
        instance=s.create(cfg);operation=s.publish(instance);parent=s.cr()
        uid=parent['metadata']['uid']
        if 'serviceMonitor' in parent['spec']:
            s.k('patch','kubestatemetrics',s.crname,'--type=json','-p',json.dumps([{'op':'test','path':'/metadata/uid','value':uid},{'op':'test','path':'/metadata/resourceVersion','value':parent['metadata']['resourceVersion']},{'op':'remove','path':'/spec/serviceMonitor'}]),target=s.target)
        services=s.get('services','-n',s.targetns,target=s.target)['items']
        service=wait(lambda:s.get('services','-n',s.targetns,target=s.target)['items'],lambda values:any(any(owner.get('uid')==uid for owner in obj['metadata'].get('ownerReferences',[])) for obj in values),timeout=90,message='旧 exporter 指标 Service 未就绪')
        service=next(obj for obj in service if any(owner.get('uid')==uid for owner in obj['metadata'].get('ownerReferences',[])))
        port=next(port['name'] for port in service['spec']['ports'] if port['name']=='http-metrics')
        old={'apiVersion':'monitoring.coreos.com/v1','kind':'ServiceMonitor','metadata':{'name':'platform-old-'+uuid.uuid4().hex[:10],'namespace':s.targetns,'labels':{'monitor-mgr.io/user-id':s.call('GET','/instances/'+instance['id'])['creatorUserId']}},'spec':{'selector':{'matchLabels':service['metadata']['labels']},'namespaceSelector':{'matchNames':[s.targetns]},'endpoints':[{'port':port,'path':'/metrics','interval':'30s'}]}}
        created=json.loads(s.k('create','-f','-','-o','json',data=old,target=s.target).stdout)
        old['spec']=copy.deepcopy(created['spec']);old['metadata']['labels']=copy.deepcopy(created['metadata']['labels'])
        s.owned_crs[(s.target,'servicemonitor',s.targetns,old['metadata']['name'])]=created['metadata']['uid']
        records.append({'instance':instance,'operation':operation,'variant':variant,'parentUid':uid,'old':old,'oldUid':created['metadata']['uid'],'oldResourceVersion':created['metadata']['resourceVersion']})
    script="""import json,sqlite3
records=json.loads(RECORDS)
c=sqlite3.connect('/data/platform.db');c.execute('BEGIN')
for record in records:
    instance=record['instance']['id'];operation=record['operation']['operationId'];version=record['operation']['versionId']
    for table,key,columns in [('drafts','instance_id',['config']),('publication_versions','id',['config','effective_spec']),('publication_previews','instance_id',['desired_spec'])]:
        for column in columns:
            identity=version if table=='publication_versions' else instance
            for rowid,raw in c.execute('SELECT rowid,'+column+' FROM '+table+' WHERE '+key+'=?',(identity,)).fetchall():
                obj=json.loads(raw);obj.pop('serviceMonitor',None);c.execute('UPDATE '+table+' SET '+column+'=? WHERE rowid=?',(json.dumps(obj),rowid))
    for table,key,identity in [('version_resources','version_id',version),('operation_resources','operation_id',operation)]:
        raw=c.execute('SELECT document FROM '+table+' WHERE '+key+'=? AND resource_key=?',(identity,'component')).fetchone()[0]
        doc=json.loads(raw);doc['spec'].pop('serviceMonitor',None)
        c.execute('UPDATE '+table+' SET document=? WHERE '+key+'=? AND resource_key=?',(json.dumps(doc),identity,'component'))
    path='/apis/monitoring.coreos.com/v1/namespaces/'+record['old']['metadata']['namespace']+'/servicemonitors/'+record['old']['metadata']['name']
    collection=path.rsplit('/',1)[0];doc=json.dumps(record['old'],indent=1).encode();uid=record['oldUid'] if record['variant']=='SUCCEEDED' else ''
    c.execute('INSERT INTO version_resources VALUES(?,?,?,?,?,?)',(version,'legacy-sm',1,collection,path,doc))
    c.execute('INSERT INTO operation_resources VALUES(?,?,?,?,?,?,?)',(operation,'legacy-sm',1,collection,path,doc,uid))
    attempt=c.execute('SELECT current_attempt_id FROM operation_lifecycle WHERE operation_id=?',(operation,)).fetchone()[0]
    c.execute('INSERT INTO resource_progress VALUES(?,?,?,?,?,?,?)',(attempt,'legacy-sm',uid,1,record['variant'],'旧平台 SM 原执行记录','2026-10-22T00:00:00Z'))
    c.execute('UPDATE operations SET state=? WHERE id=?',(record['variant'],operation));c.execute('UPDATE execution_attempts SET state=?,possibly_sent=1 WHERE id=?',(record['variant'],attempt))
    c.execute('INSERT INTO instance_protections VALUES(?,?)',(instance,operation))
c.commit();assert c.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
print(json.dumps({'records':len(records),'integrity':'ok'}))
""".replace('RECORDS',repr(json.dumps(records)))
    _offline(s,script)
    return records


# 将管理员显式确认绑定原盘点快照和冻结身份。
def confirmation(s, instance, snapshot, resource, parent=False):
    body={key:resource[key] for key in ('operationId','resourceKey','objectPath','originalUid','documentFingerprint')}
    body.update(snapshotId=snapshot['snapshotId'],confirmExternalHandling=True)
    return s.call('POST','/instances/'+instance['id']+('/historical-operation-releases' if parent else '/historical-servicemonitor-resolutions'),body)


def remove_old(s, record):
    """管理员夹具仅按已登记原 UID/RV 清理，非产品 API。"""
    old=record['old']['metadata'];path='/apis/monitoring.coreos.com/v1/namespaces/'+old['namespace']+'/servicemonitors/'+old['name']
    current=s.get('servicemonitor',old['name'],'-n',old['namespace'],target=s.target)
    if current['metadata']['uid']!=record['oldUid']:raise RuntimeError('旧 fixture UID 已替换，拒绝处理')
    if current['spec']!=record['old']['spec']:raise RuntimeError('旧fixture配置已漂移，拒绝自动处理')
    s.delete_uid(s.target,path,record['oldUid'],resource_version=current['metadata']['resourceVersion'])
    wait(lambda:s.k('get','servicemonitor',old['name'],'-n',old['namespace'],'-o','json',target=s.target,check=False),lambda value:value.returncode!=0 and b'(NotFound)' in value.stderr,timeout=40,message='旧 SM 尚未证明404')
    key=(s.target,'servicemonitor',old['namespace'],old['name'])
    if s.owned_crs.get(key)==record['oldUid']:del s.owned_crs[key]


# 验证历史 exporter 的显式处理与新发布切换。
def migrate(s):
    s.by('I29-K02：成功和未知旧 SM 均须管理员外部处理，原任务不改成功')
    s.prepare('-historical-migration',target='control')
    records=prepare_legacy(s)
    from historical_collection import begin, finish
    collection=begin(s,records)
    proof={'testedCommit':s.env.candidate,'kind':'legacy','cases':[]}
    for record in records:
        i=record['instance'];s.crname=i['name'];base='/instances/'+i['id'];before=s.call('GET',base+'/historical-migration')
        old=_facts(s.call('GET','/operations/'+record['operation']['operationId']))
        cfg=copy.deepcopy(s.call('GET',base+'/draft')['ksm']);cfg['serviceMonitor']={'enabled':True,'interval':'30s','scrapeTimeout':'10s'};s.save(i,cfg)
        rev=s.call('GET',base+'/draft')['revision'];blocked=s.call('POST',base+'/previews',{'draftRevision':rev},status=409)
        window_start=time.time()
        remove_old(s,record)
        sm=next(resource for resource in before['resources'] if resource['resourceKey']=='legacy-sm')
        resolved=confirmation(s,i,before,sm)
        parent=next(resource for resource in before['resources'] if resource['resourceKey']=='component')
        released=confirmation(s,i,before,parent,True)
        published=s.publish(i)
        current=wait(lambda:s.cr(),lambda value:value.get('status',{}).get('serviceMonitor',{}).get('state')=='Ready',timeout=100,message='新Operator配套未收敛')
        reference=current['status']['serviceMonitor']['resource'];companion=s.get('servicemonitor',reference['name'],'-n',reference['namespace'],target=s.target)
        all_sm=s.get('servicemonitors','-A',target=s.target)['items']
        selected=[obj for obj in all_sm if obj['metadata'].get('ownerReferences') and any(owner.get('uid')==record['parentUid'] for owner in obj['metadata']['ownerReferences'])]
        proof['cases'].append({'variant':record['variant'],'originalUid':record['parentUid'],'currentUid':current['metadata']['uid'],'inventory':before,'inventoryAfter':s.call('GET',base+'/historical-migration'),'blockedCode':blocked['code'],'resolution':resolved,'release':released,'oldOperation':old,'oldOperationAfter':_facts(s.call('GET','/operations/'+record['operation']['operationId'])),'newOperation':_facts(published),'parent':current,'companion':companion,'ownedCompanions':selected,'oldName':record['old']['metadata']['name'],'oldPathAbsent':s.k('get','servicemonitor',record['old']['metadata']['name'],'-n',s.targetns,target=s.target,check=False).returncode!=0,'collectionWindowSeconds':time.time()-window_start})
    proof['collection']=finish(s,records,collection)
    # 代理只能看到平台身份；四类 SM verb 中被拒绝请求也计入。
    platform_id=s.call('GET','/clusters/'+s.cluster['id'])
    proof['platformSMWrites']=len([event for event in s.capture_proxy() if '/servicemonitors' in event['path'] and event['method'] in ('POST','PUT','PATCH','DELETE')])
    s.acceptance_evidence['issue29']=proof
    return records


def browser_fixture(s):
    """浏览器只调用平台确认，外部清理由隔离管理员夹具按预登记原 UID 完成。"""
    import http.server
    import secrets
    import threading
    record=prepare_legacy(s,('IDENTITY_UNCERTAIN',))[0]
    token=secrets.token_urlsafe(32)
    class Handler(http.server.BaseHTTPRequestHandler):
        # 抑制测试 HTTP 服务的原始请求日志。
        def log_message(self,*args): pass
        # 接收浏览器明确确认的迁移请求并保留请求事实。
        def do_POST(self):
            if self.path!='/process-old-sm' or self.headers.get('Authorization')!='Bearer '+token:
                self.send_response(403);self.end_headers();return
            try:
                remove_old(s,record)
                self.send_response(200);self.end_headers();self.wfile.write(b'{"processed":true}')
            except Exception:
                self.send_response(409);self.end_headers();self.wfile.write(b'{"processed":false}')
    server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler)
    threading.Thread(target=server.serve_forever,daemon=True).start();s.issue29_admin_server=server
    return {'instance':record['instance'],'operationId':record['operation']['operationId'],'oldUid':record['oldUid'],'parentUid':record['parentUid'],
            'adminURL':'http://127.0.0.1:'+str(server.server_port)+'/process-old-sm','adminToken':token,
            'resultPath':str(s.out/'issue29-browser-result.json')}


# 将真实浏览器交互结果与原操作事实对照。
def browser_result(s, fixture):
    value=json.loads(open(fixture['resultPath']).read())
    if value['instanceId']!=fixture['instance']['id']:raise RuntimeError('浏览器迁移交接实例不符')
    from historical_cleanup import handoff_publication
    operation=handoff_publication(s,fixture,value)
    s.acceptance_evidence['issue29']={'testedCommit':s.env.candidate,'kind':'browser','instanceId':value['instanceId'],
        'oldOperation':_facts(s.call('GET','/operations/'+fixture['operationId'])),'newOperation':_facts(operation),
        'newVersion':s.call('GET','/instances/'+value['instanceId']+'/versions/'+value['versionId']),
        'inventory':value['inventory'],
        'platformSMWrites':len([event for event in s.capture_proxy() if '/servicemonitors' in event['path'] and event['method'] in ('POST','PUT','PATCH','DELETE')])}
