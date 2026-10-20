"""PROJECTION013验收fixture：原始URL只驻内存，公开证据仅含身份白名单和安全布尔值。"""
import copy,hashlib,json,pathlib,secrets,socket,tempfile,time
from transport import process as subprocess
import yaml

ROOT=pathlib.Path(__file__).resolve().parents[2]

def no_marker(value,markers):
 """认证marker仅在内存比较，不返回命中的原文。"""
 raw=json.dumps(value,ensure_ascii=False) if not isinstance(value,str) else value
 return not any(marker in raw for marker in markers)

def metadata(obj):
 """只比较身份和用户metadata；Operator可独立更新RV/status，不能归为平台写入。"""
 return {k:copy.deepcopy(obj['metadata'][k]) for k in ['name','namespace','uid','labels','annotations','finalizers'] if k in obj['metadata']}

def projection_failure(s):
 """关键：失败分支也不能回落到raw CR、事件message、异常参数或日志文本。"""
 markers=s.projection_markers
 out={'error':'PROJECTION013安全投影验收失败（原始目标与异常值不输出）','failedStage':getattr(s,'projection_stage','fixture')}
 out['operatorStatusBranch']=getattr(s,'projection_status_branch',{'required':False})
 out['runtimeReasonBranch']=getattr(s,'projection_reason_branch',{'branchObserved':False})
 out['publicReadDiagnostics']=getattr(s,'projection_read_diagnostics',{})
 try:
  result=s.k('logs','pod/'+s.pod,'-n',s.ns,'--tail=200',check=False)
  out['platformLogs']={'readable':result.returncode==0,'containsMarker':not no_marker(result.stdout.decode(errors='replace'),markers)}
 except Exception:out['platformLogs']={'readable':False}
 if s.targetns:
  try:
   out['failureEvents']=[{'name':e['metadata']['name'],'uid':e['metadata']['uid'],'messageContainsMarker':not no_marker(e.get('message',''),markers)} for e in s.get('events','-n',s.targetns)['items']]
   out['actualFixtureAgents']=[{k:a['metadata'][k] for k in ['name','namespace','uid'] if k in a['metadata']} for a in s.get('prometheusagents','-n',s.targetns)['items']]
  except Exception:out['failureInventoryReadable']=False
 return out

def wait_operator_status(s,names,markers,timeout=90):
 """关键：真实proxy错误分支必须实际出现；过早读取或超时均不能冒充防泄漏通过。"""
 deadline=time.monotonic()+timeout;observed={name:False for name in names}
 while True:
  objects={name:s.cr(name) for name in names}
  for name,obj in objects.items():observed[name]=not no_marker(obj.get('status',{}),markers)
  s.projection_status_branch={'required':True,'managedObserved':observed[names[0]],'externalObserved':observed[names[1]],'branchObserved':all(observed.values())}
  if all(observed.values()):return objects
  if time.monotonic()>=deadline:raise AssertionError('deadline内未观察到真实Operator认证URL错误status分支（原文不输出）')
  time.sleep(.5)

def reason_runtime_projection(s,instance,outside_name,markers,malformed,timeout=45):
 """同一来源验证reason→Runtime.Error；普通reason或Operator抢先复写不能代替安全分支。"""
 deadline=time.monotonic()+timeout;managed_name=instance['name']
 while True:
  for name in [managed_name,outside_name]:
   for attempt in range(8):
    cr=s.cr(name);status=cr.setdefault('status',{});conditions=status.setdefault('conditions',[])
    condition=next((c for c in conditions if c.get('type')=='Available'),None)
    if condition is None:condition={'type':'Available'};conditions.append(condition)
    condition.update(status='False',observedGeneration=cr['metadata']['generation'],reason=malformed(markers[0]),message='Waiting for workload readiness')
    status.setdefault('replicas',0);status['availableReplicas']=0
    path='/apis/monitoring.coreos.com/v1alpha1/namespaces/'+s.targetns+'/prometheusagents/'+name+'/status'
    result=s.k('replace','--raw',path,'-f','-',data=cr,check=False)
    if result.returncode==0:break
    # 关键：仅标准Conflict或传输前明确的本地RV过期允许重读；未知/权限/Invalid不能盲重试。
    retryable=b'(Conflict)' in result.stderr or result.stderr==b'Error from acceptance boundary (stale_fixture_revision)'
    assert retryable and attempt<7,'reason来源fixture写入失败（原文不输出）'
  observed=[]
  for name in [managed_name,outside_name]:
   cr=s.cr(name);conditions=cr.get('status',{}).get('conditions',[])
   observed.append(any(c.get('type')=='Available' and c.get('reason')==malformed(markers[0]) and c.get('observedGeneration')==cr['metadata']['generation'] for c in conditions))
  managed=s.call('POST','/instances/'+instance['id']+'/refresh',{})
  listing=s.call('GET','/clusters/'+s.cluster['id']+'/instances')
  external=next((i for i in listing['instances'] if i['name']==outside_name and i['readOnly']),{})
  assert no_marker([managed,listing],markers),'reason派生Runtime.Error泄漏认证marker（原值不输出）'
  hidden=[managed.get('error')=='[敏感内容已隐藏]',external.get('runtime',{}).get('error')=='[敏感内容已隐藏]']
  s.projection_reason_branch={'managedReasonObserved':observed[0],'externalReasonObserved':observed[1],'managedRuntimeHidden':hidden[0],'externalRuntimeHidden':hidden[1],'branchObserved':all(observed+hidden)}
  if all(observed+hidden):return listing
  if time.monotonic()>=deadline:raise AssertionError('deadline内未观察到两类Runtime.Error明确隐藏的reason分支（原文不输出）')
  time.sleep(.5)

def projection_artifact_scan(s,record):
 """关键：命中仍判失败，但交付目录不能保留原文；仅此013 fixture处理已知内存marker。"""
 markers=s.projection_markers;paths=[p for p in s.out.rglob('*') if p.is_file()]
 safe_record=no_marker(record,markers);removed=[]
 for path in paths:
  if any(m.encode() in path.read_bytes() for m in markers):
   # 原文件不归档；安全占位保留可核验失败，不能只让gate失败却把秘密留在artifact。
   path.write_text(json.dumps({'omitted':True,'originalContainsMarker':True,'reason':'013测试敏感原文已移除'},ensure_ascii=False)+'\n')
   removed.append(str(path.relative_to(s.out)))
 def hide(value):
  if isinstance(value,str):return value if no_marker(value,markers) else '[013测试敏感原文已移除]'
  if isinstance(value,list):return [hide(v) for v in value]
  if isinstance(value,dict):return {hide(k):hide(v) for k,v in value.items()}
  return value
 cleaned=hide(record);record.clear();record.update(cleaned)
 if not safe_record or removed:record['error']='PROJECTION013证据安全检查失败，原文已移除';record['outcome']='failed'
 # 原文件被替换后，公开引用必须绑定安全占位的新字节，不能保留旧SHA冒充完整原证据。
 for refs in [record.get('evidenceRefs',[]),record.get('evidence',{}).get('evidenceRefs',[])]:
  for ref in refs:
   if ref['path'] in removed:ref['sha256']=hashlib.sha256((s.out/ref['path']).read_bytes()).hexdigest()
 published_safe=no_marker(record,markers) and all(not any(m.encode() in p.read_bytes() for m in markers) for p in paths)
 assert published_safe,'013安全占位未清除marker（原文不输出）'
 return {'noMarker':safe_record and not removed,'candidateRecordSafe':safe_record,'existingArtifactsSafe':not removed,'checkedFileCount':len(paths),'replacedFiles':removed,'publishedArtifactsSafe':published_safe}

def database_probe(s,markers,expected=None):
 """复用既有只读卷probe；数据库/ WAL原字节不离开Pod，只回传布尔值与内存比较指纹。"""
 script="""import glob,hashlib,json,pathlib,sqlite3
markers=MARKERS
c=sqlite3.connect('file:/data/platform.db?mode=ro',uri=True)
# 保留SQLite原始storage class与bytes编码；BLOB不能直接交给JSON，也不能丢失TEXT/BLOB差异。
rows=c.execute('select id,typeof(effective_spec),hex(effective_spec) from publication_versions order by id').fetchall()
c.close()
fingerprint=hashlib.sha256(json.dumps(rows,separators=(',',':')).encode()).hexdigest()
leak=any(m.encode() in pathlib.Path(p).read_bytes() for p in glob.glob('/data/platform.db*') for m in markers)
print(json.dumps({'noMarker':not leak,'frozenFingerprint':fingerprint}))""".replace('MARKERS',repr(markers))
 result=s.job('projection-db-'+secrets.token_hex(4),script,True)
 assert result['noMarker'],'平台数据库或WAL存在认证marker（原值不输出）'
 if expected is not None:assert result['frozenFingerprint']==expected,'历史effective_spec被读投影改写'
 return result['frozenFingerprint']

def rpc_reads(s,ids,markers):
 """复用生成RPC探针，经原Pod loopback port-forward；Session和marker只通过stdin传入。"""
 port=s.port+300
 command=[s.a.kubectl,'--kubeconfig',s.a.control_config,'--context',s.a.control_context,'-n',s.ns,'port-forward','pod/'+s.pod,str(port)+':8888']
 forward=subprocess.Popen(command,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
 try:
  deadline=time.monotonic()+15
  while time.monotonic()<deadline:
   try:
    with socket.create_connection(('127.0.0.1',port),timeout=.2):break
   except OSError:time.sleep(.1)
  else:raise AssertionError('直接RPC port-forward未就绪')
  with tempfile.TemporaryDirectory(prefix='mm18-projection-rpc-') as directory:
   binary=pathlib.Path(directory)/'rpcprobe'
   built=subprocess.run(['go','build','-o',str(binary),'./scripts/acceptance/rpcprobe'],cwd=ROOT,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
   assert built.returncode==0,'生成RPC探针构建失败（工具输出不进入产品证据）'
   token=next(c.value for c in s.cookies if c.name=='monitor_session')
   data={'mode':'projection','address':'127.0.0.1:'+str(port),'token':token,'clusterId':s.cluster['id'],'instanceIds':ids,'markers':markers}
   result=subprocess.run([str(binary)],input=json.dumps(data).encode(),stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=100)
   assert result.returncode==0,'直接RPC探针执行失败'
   value=json.loads(result.stdout)
   assert value=={'noRPCError':True,'noMarkerInReadModels':True,'readCount':9},'直接RPC读投影失败（仅输出布尔结果）'
   return value
 finally:
  forward.terminate();forward.wait(timeout=5)

def projection_case(s,case):
 """真实CRD允许不满足URL解析器的字符串；fixture不要求恶意配置健康或样本交付。"""
 field='url' if case.endswith('.url-parse-error') else 'proxyUrl'
 markers=[secrets.token_hex(24),secrets.token_hex(24)];s.projection_markers=markers;s.projection_stage='managed-baseline'
 instance=s.create();s.publish(instance)
 frozen=database_probe(s,markers)
 s.projection_stage='inject-external-and-managed'
 malformed=lambda marker:'https://reader:'+marker+'@receiver.example.invalid/%zz'
 def drift(marker):
  for _ in range(8):
   actual=s.cr();actual['spec']['remoteWrite'][0][field]=malformed(marker)
   result=s.k('replace','-f','-',data=actual,check=False)
   if result.returncode==0:return s.cr()
   if b'(Conflict)' not in result.stderr:raise AssertionError('固定CRD拒绝投影fixture（目标响应不输出）')
  raise AssertionError('fixture外部修改持续冲突')
 managed=drift(markers[0]);outside_name='external-projection-'+secrets.token_hex(5)
 outside={k:copy.deepcopy(managed[k]) for k in ['apiVersion','kind','spec']};outside['metadata']={'name':outside_name,'namespace':s.targetns,'annotations':{'fixture.example.invalid/preserve':'external-metadata'}}
 s.apply(outside);outside=s.cr(outside_name)
 s.projection_status_branch={'required':field=='proxyUrl'}
 if field=='proxyUrl':
  s.projection_stage='operator-error-status'
  observed=wait_operator_status(s,[managed['metadata']['name'],outside_name],markers)
  managed,outside=observed[managed['metadata']['name']],observed[outside_name]
 originals={managed['metadata']['name']:metadata(managed),outside_name:metadata(outside)}
 logs_before=s.k('logs','pod/'+s.pod,'-n',s.ns).stdout.decode(errors='replace').count('目标API写请求')
 s.projection_stage='public-read-models'
 listing=reason_runtime_projection(s,instance,outside_name,markers,malformed)
 external=next((i for i in listing['instances'] if i['name']==outside_name and i['readOnly']),None)
 managed_listing=next((i for i in listing['instances'] if i['id']==instance['id']),{})
 s.projection_read_diagnostics={'listingContainsMarker':not no_marker(listing,markers),'externalFound':external is not None,'managedRuntimeErrorContainsMarker':not no_marker(managed_listing.get('runtime',{}).get('error',''),markers),'unmanagedYAMLContainsMarker':not no_marker((external or {}).get('yaml',''),markers)}
 assert no_marker(listing,markers),'发现读模型含认证marker'
 assert external is not None,'外部只读Agent未发现'
 assert external['ownership']=='unmanaged'
 for item in [instance,external]:
  for suffix in ['', '/runtime','/versions']:
   assert no_marker(s.call('GET','/instances/'+item['id']+suffix),markers),'详情/运行/版本读模型含认证marker'
  assert no_marker(s.call('POST','/instances/'+item['id']+'/refresh',{}),markers),'刷新读模型含认证marker'
  assert no_marker(s.call('GET','/operations?instanceId='+item['id']),markers),'历史读模型含认证marker'
 preview=s.preview(instance);assert no_marker(preview,markers) and '[敏感内容已隐藏]' in preview['leftYAML'],'左右YAML未失败关闭'
 projected=yaml.safe_load(preview['leftYAML']);checked_conditions=0;checked_fields=0
 for original in s.cr().get('status',{}).get('conditions',[]):
  if no_marker(original,markers):
   # 普通Operator诊断必须仍可读；只比较稳定type/reason/message，不比较独立变化的时间戳。
   fields=[k for k in ['reason','message'] if isinstance(original.get(k),str) and original[k]]
   if not fields:continue
   kept=next((c for c in projected.get('status',{}).get('conditions',[]) if c.get('type')==original.get('type')),None)
   assert kept is not None and all(kept.get(k)==original.get(k) for k in fields),'普通Operator诊断被隐藏'
   checked_conditions+=1;checked_fields+=len(fields)
 rpc=rpc_reads(s,[instance['id'],external['id']],markers)
 s.projection_stage='hidden-value-drift'
 managed=drift(markers[1]);rejected=s.call('POST','/instances/'+instance['id']+'/publications',{'draftRevision':preview['draftRevision'],'previewId':preview['previewId'],'overwriteDrift':True},status=409)
 assert rejected['code']=='CONFLICT','隐藏认证值改变未使原preview冲突'
 assert no_marker(s.call('GET','/clusters/'+s.cluster['id']+'/instances'),markers),'再次发现含认证marker'
 s.projection_stage='database-logs-and-identity'
 database_probe(s,markers,frozen)
 logs=s.k('logs','pod/'+s.pod,'-n',s.ns).stdout.decode(errors='replace')
 assert no_marker(logs,markers),'平台安全日志含认证marker'
 assert logs.count('目标API写请求')==logs_before,'只读投影产生平台目标写请求'
 for name,before in originals.items():assert metadata(s.cr(name))==before,'投影改写目标原UID或metadata'
 assert s.cr()['spec']['remoteWrite'][0][field]==malformed(markers[1]),'投影改写原始认证URL'
 return {'field':field,'fixedCRDAcceptedMalformedURL':True,'operatorStatusBranch':s.projection_status_branch,'runtimeReasonBranch':s.projection_reason_branch,'publicReadDiagnostics':s.projection_read_diagnostics,'benignOperatorDiagnosticsPreserved':True if checked_fields else None,'benignOperatorDiagnosticCoverage':{'checkedConditions':checked_conditions,'checkedFields':checked_fields,'coveredInThisK':checked_fields>0,'positiveControlBoundary':'K只记录实际比较；无普通诊断样本时正控由同013 Q真实HTTP/RPC覆盖'},'externalReadOnly':True,'noMarkerInHTTPYAMLHistory':True,'directRPC':rpc,'noMarkerInDatabaseWALOrLogs':True,'hiddenValueChangeRejectsOldPreview':True,'originalMetadataUnchanged':True,'historicalEffectiveSpecUnchanged':True,'noPlatformReadSideTargetMutation':True,'sampleDeliveryClaimed':False,'malformedConfigHealthClaimed':False,'fixtureUIDs':{name:before['uid'] for name,before in originals.items()}}
