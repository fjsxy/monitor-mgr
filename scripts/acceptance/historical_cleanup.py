"""测试夹具清理只信任本 run 原 UID，公共收据使用固定阶段分类。"""
import json


def kubectl_failure(stderr):
    """公开固定 Kubernetes 错误分类；原 stderr 不进入收据或控制台。"""
    for code, tokens in [('not-found',(b'(NotFound)',)),('conflict',(b'(Conflict)',)),
            ('precondition-failed',(b'testing value /metadata/resourceVersion failed',)),
            ('precondition-failed',(b'test failed',b'resourceVersion')),
            ('invalid',(b'The request is invalid',)),('invalid',(b' is invalid:',)),
            ('forbidden',(b'(Forbidden)',)),('wait-timeout',(b'timed out waiting for the condition',))]:
        if all(token in stderr for token in tokens):return code
    return 'unknown'


def conditional_fixture_patch(s, key, uid, project, *, allow_gone=False):
    """关键：只用原UID/新读RV的最小MergePatch；仅明确Conflict最多重读3次，Invalid从不猜成RV冲突。"""
    target,kind,namespace,name=key
    args=['get',kind,name,'-o','json']+(['-n',namespace] if namespace else [])
    observed=False
    def outcome(success,reason,phase,attempt,obj=None):
        return dict(success=success,reason=reason,phase=phase,attempts=attempt,observed=observed,object=obj)
    for attempt in range(1,4):
        result=s.k(*args,target=target,check=False)
        if result.returncode:
            reason=kubectl_failure(result.stderr)
            return outcome(allow_gone and reason=='not-found',reason,'read',attempt)
        obj=json.loads(result.stdout);metadata=obj.get('metadata',{})
        if metadata.get('uid')!=uid:return outcome(False,'uid-mismatch','identity',attempt)
        if not uid or not metadata.get('resourceVersion'):return outcome(False,'identity-incomplete','identity',attempt)
        observed=True
        patch=project(obj)
        if patch is None:return outcome(True,'unchanged','complete',attempt,obj)
        # MergePatch将UID交给API不可变身份校验，将resourceVersion交给更新冲突校验；两者缺一不可。
        patch.setdefault('metadata',{}).update(uid=uid,resourceVersion=metadata['resourceVersion'])
        result=s.k('patch',kind,name,*(['-n',namespace] if namespace else []),'--type=merge','-p',json.dumps(patch),target=target,check=False)
        if result.returncode:
            reason=kubectl_failure(result.stderr)
            if reason=='conflict' and attempt<3:continue
            return outcome(False,reason,'patch',attempt)
        current=s.k(*args,target=target,check=False)
        if current.returncode:
            reason=kubectl_failure(current.stderr)
            return outcome(allow_gone and reason=='not-found',reason,'verify',attempt)
        obj=json.loads(current.stdout)
        if obj.get('metadata',{}).get('uid')!=uid:return outcome(False,'uid-mismatch','identity',attempt)
        if project(obj) is not None:return outcome(False,'field-not-applied','verify',attempt)
        return outcome(True,'updated','complete',attempt,obj)


def fixture_finalizers(s, key, uid, *, add=(), remove=()):
    """只编辑本例hold，保留同一新读对象的其它finalizer；最后hold释放后的404可结束原UID责任。"""
    def project(obj):
        original=obj.get('metadata',{}).get('finalizers',[])
        updated=[value for value in original if value not in remove]+[value for value in add if value not in original]
        return None if original==updated else dict(metadata=dict(finalizers=updated))
    return conditional_fixture_patch(s,key,uid,project,allow_gone=True)


def require_fixture_patch(s, result, operation):
    """失败只保存固定操作/阶段/分类/次数；原对象、路径和kubectl原文不进入public。"""
    if result['success']:return result
    operations={'fixture-hold-add','fixture-hold-release','legacy-service-monitor-remove','fixture-role-rules'}
    reasons={'not-found','conflict','invalid','forbidden','unknown','uid-mismatch','identity-incomplete','field-not-applied'}
    phases={'read','identity','patch','verify','complete'}
    value=dict(operation=operation if operation in operations else 'unknown',
               phase=result.get('phase') if result.get('phase') in phases else 'unknown',
               reason=result.get('reason') if result.get('reason') in reasons else 'unknown',
               attempts=result.get('attempts') if type(result.get('attempts')) is int and 1<=result['attempts']<=3 else 0)
    if hasattr(s,'env'):
        (s.env.public/'fixture-conditional-patch.json').write_text(json.dumps(value,ensure_ascii=False,indent=2))
    raise AssertionError('fixture条件写失败，见安全分类')


def remove_legacy_service_monitor(s, uid):
    """旧版本fixture仅移除新采集字段，不覆盖其它spec；每次Conflict重读均重新核原UID。"""
    def project(obj):
        return dict(spec=dict(serviceMonitor=None)) if 'serviceMonitor' in obj.get('spec',{}) else None
    result=conditional_fixture_patch(s,(s.target,'kubestatemetrics','',s.crname),uid,project)
    return require_fixture_patch(s,result,'legacy-service-monitor-remove')


def fixture_cluster_role_rules(s, target, name, uid, rules):
    """管理员RBAC故障注入仅替换原ClusterRole的既定rules，恢复仍使用完整原规则和原UID。"""
    def project(obj):
        return None if obj.get('rules')==rules else dict(rules=rules)
    result=conditional_fixture_patch(s,(target,'clusterrole','',name),uid,project)
    return require_fixture_patch(s,result,'fixture-role-rules')


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
