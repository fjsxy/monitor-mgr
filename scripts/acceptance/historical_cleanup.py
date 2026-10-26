"""测试夹具清理只信任本 run 原 UID，公共收据使用固定阶段分类。"""
import json


def kubectl_failure(stderr):
    """公开固定 Kubernetes 错误分类；原 stderr 不进入收据或控制台。"""
    for code, tokens in [('not-found',(b'(NotFound)',)),('conflict',(b'(Conflict)',)),
            ('precondition-failed',(b'testing value /metadata/resourceVersion failed',)),
            ('precondition-failed',(b'test failed',b'resourceVersion')),
            ('forbidden',(b'(Forbidden)',)),('wait-timeout',(b'timed out waiting for the condition',))]:
        if all(token in stderr for token in tokens):return code
    return 'unknown'


def fixture_finalizers(s, key, uid, *, add=(), remove=()):
    """关键：仅在原 UID/RV 上增减本例 hold；只对明确条件冲突最多重读3次，其他失败不盲重试。"""
    target,kind,namespace,name=key
    args=['get',kind,name,'-o','json']+(['-n',namespace] if namespace else [])
    observed=False
    for attempt in range(3):
        result=s.k(*args,target=target,check=False)
        if result.returncode:
            return dict(success=kubectl_failure(result.stderr)=='not-found',reason=kubectl_failure(result.stderr),observed=observed,object=None)
        obj=json.loads(result.stdout);metadata=obj.get('metadata',{})
        if metadata.get('uid')!=uid:return dict(success=False,reason='uid-mismatch',observed=observed,object=None)
        observed=True
        finalizers=metadata.get('finalizers',[])
        updated=[value for value in finalizers if value not in remove]+[value for value in add if value not in finalizers]
        if finalizers==updated:return dict(success=True,reason='unchanged',observed=True,object=obj)
        patch=[{'op':'test','path':'/metadata/uid','value':uid},{'op':'test','path':'/metadata/resourceVersion','value':metadata['resourceVersion']},{'op':'add','path':'/metadata/finalizers','value':updated}]
        result=s.k('patch',kind,name,*(['-n',namespace] if namespace else []),'--type=json','-p',json.dumps(patch),target=target,check=False)
        if result.returncode:
            reason=kubectl_failure(result.stderr)
            if reason in ('conflict','precondition-failed') and attempt<2:continue
            return dict(success=False,reason=reason,observed=True,object=None)
        # 关键：释放最后一个hold可能立刻触发真实删除；随后404是原UID责任已结束，不是读取失败。
        current=s.k(*args,target=target,check=False)
        if current.returncode:
            reason=kubectl_failure(current.stderr)
            return dict(success=reason=='not-found',reason=reason,observed=True,object=None)
        obj=json.loads(current.stdout)
        if obj.get('metadata',{}).get('uid')!=uid:return dict(success=False,reason='uid-mismatch',observed=True,object=None)
        return dict(success=True,reason='updated',observed=True,object=obj)


# 关键：按已登记原 UID 清理资源；同名替代必须拒绝。
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
            changed=fixture_finalizers(s,key,uid,remove=ours)
            if changed['reason']=='uid-mismatch':return {'success':False,'phase':'identity','reason':'uid-mismatch'}
            if not changed['success']:return {'success':False,'phase':'finalizers','reason':'finalizer-patch-failed'}
            if changed['object'] is None:
                if s.owned_crs.get(key)==uid:del s.owned_crs[key]
                return {'success':True,'phase':'finalizers','reason':'gone'}
            obj=changed['object']
        except Exception:return {'success':False,'phase':'finalizers','reason':'finalizer-patch-failed'}
    path='/apis/monitoring.coreos.com/v1/namespaces/'+namespace+'/servicemonitors/'+name if kind=='servicemonitor' else ('/apis/monitoring.coreos.com/v1alpha1/namespaces/'+namespace+'/prometheusagents/'+name if kind=='prometheusagent' else '/apis/monitoring.fjsxy.github.io/v1alpha1/kubestatemetrics/'+name)
    try:
        pods=s.capture_cleanup_pods(key,uid,obj) if hasattr(s,'capture_cleanup_pods') else []
        if pods:
            # 原图采证会读取多个对象；删除前单独刷新原UID的RV，不复用采证前的旧RV。
            fresh=s.k(*args,target=target,check=False)
            if fresh.returncode:
                if kubectl_failure(fresh.stderr)=='not-found':
                    if s.owned_crs.get(key)==uid:del s.owned_crs[key]
                    return {'success':True,'phase':'read','reason':'gone'}
                return receipt
            obj=json.loads(fresh.stdout)
            if obj.get('metadata',{}).get('uid')!=uid:return {'success':False,'phase':'identity','reason':'uid-mismatch'}
        s.delete_uid(target,path,uid,resource_version=obj['metadata']['resourceVersion'])
        if pods:s.accelerate_cleanup_pods(pods)
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
