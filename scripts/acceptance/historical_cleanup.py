"""测试夹具清理只信任本 run 原 UID，公共收据使用固定阶段分类。"""
import json


def cleanup_registered(s, key, uid):
    target,kind,namespace,name=key
    receipt={'success':False,'phase':'read','reason':'read-failed'}
    args=['get',kind,name,'-o','json']+(['-n',namespace] if namespace else [])
    try:
        result=s.k(*args,target=target,check=False)
        if result.returncode:
            if b'(NotFound)' in result.stderr:
                if s.owned_crs.get(key)==uid:del s.owned_crs[key]
                return {'success':True,'phase':'read','reason':'gone'}
            return receipt
        obj=json.loads(result.stdout)
    except Exception:return receipt
    if obj.get('metadata',{}).get('uid')!=uid:return {'success':False,'phase':'identity','reason':'uid-mismatch'}
    original=obj['metadata'];ours=[value for value in original.get('finalizers',[]) if value in ('phase1.fixture/hold','fixture.monitor-mgr/hold')]
    if ours:
        try:
            s.k('patch',kind,name,*(['-n',namespace] if namespace else []),'--type=json','-p',json.dumps([{'op':'test','path':'/metadata/uid','value':uid},{'op':'test','path':'/metadata/resourceVersion','value':original['resourceVersion']},{'op':'replace','path':'/metadata/finalizers','value':[value for value in original.get('finalizers',[]) if value not in ours]}]),target=target)
            result=s.k(*args,target=target,check=False)
            if result.returncode:return {'success':False,'phase':'read','reason':'read-failed'}
            obj=json.loads(result.stdout)
            if obj['metadata']['uid']!=uid:return {'success':False,'phase':'identity','reason':'uid-mismatch'}
        except Exception:return {'success':False,'phase':'finalizers','reason':'finalizer-patch-failed'}
    path='/apis/monitoring.coreos.com/v1/namespaces/'+namespace+'/servicemonitors/'+name if kind=='servicemonitor' else ('/apis/monitoring.coreos.com/v1alpha1/namespaces/'+namespace+'/prometheusagents/'+name if kind=='prometheusagent' else '/apis/monitoring.fjsxy.github.io/v1alpha1/kubestatemetrics/'+name)
    try:
        s.delete_uid(target,path,uid,resource_version=obj['metadata']['resourceVersion'])
        s.k('wait','--for=delete',kind+'/'+name,*(['-n',namespace] if namespace else []),'--timeout=100s',target=target)
        absent=s.k(*args,target=target,check=False)
        if not absent.returncode or b'(NotFound)' not in absent.stderr:return {'success':False,'phase':'read','reason':'read-failed'}
    except Exception:return {'success':False,'phase':'delete','reason':'delete-failed'}
    if s.owned_crs.get(key)==uid:del s.owned_crs[key]
    return {'success':True,'phase':'delete','reason':'gone'}


def handoff_publication(s, expected, identity):
    """公开 HTTP 精确回读新操作；confirmedUID 为空或跨实例/版本都不登记。"""
    if identity['instanceId']!=expected['instance']['id'] or identity['operationId']==expected['operationId']:raise RuntimeError('foreign-publication')
    operation=s.call('GET','/operations/'+identity['operationId'])
    if operation.get('operationId')!=identity['operationId'] or operation.get('instanceId')!=expected['instance']['id'] or operation.get('versionId')!=identity['versionId'] or operation.get('clusterId')!=expected['instance']['clusterId'] or operation.get('action')!='PUBLISH':raise RuntimeError('foreign-publication')
    resources=operation.get('resources',[])
    if len(resources)!=1:raise RuntimeError('foreign-resource')
    item=resources[0];target=item['target'];uid=item.get('confirmedUid','')
    if not uid:
        if item.get('possiblySent'):raise RuntimeError('unconfirmed-sent-identity')
        return operation
    if uid!=expected['parentUid'] or target.get('kind')!='KubeStateMetrics' or target.get('apiVersion')!='monitoring.fjsxy.github.io/v1alpha1' or target.get('name')!=expected['instance']['name'] or target.get('namespace','')!='':raise RuntimeError('foreign-resource')
    s.track_cr(uid,name=target['name'],namespace='',component='ksm')
    return operation
