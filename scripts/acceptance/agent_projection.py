"""PROJECTION013验收fixture：原始URL只驻内存，公开证据仅含身份白名单和安全布尔值。"""
import copy,hashlib,json,pathlib,secrets,socket,subprocess,tempfile,time

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
 originals={managed['metadata']['name']:metadata(managed),outside_name:metadata(outside)}
 logs_before=s.k('logs','pod/'+s.pod,'-n',s.ns).stdout.decode(errors='replace').count('目标API写请求')
 s.projection_stage='public-read-models'
 listing=s.call('GET','/clusters/'+s.cluster['id']+'/instances');assert no_marker(listing,markers),'发现读模型含认证marker'
 external=next(i for i in listing['instances'] if i['name']==outside_name and i['readOnly'])
 assert external['ownership']=='unmanaged'
 for item in [instance,external]:
  for suffix in ['', '/runtime','/versions']:
   assert no_marker(s.call('GET','/instances/'+item['id']+suffix),markers),'详情/运行/版本读模型含认证marker'
  assert no_marker(s.call('POST','/instances/'+item['id']+'/refresh',{}),markers),'刷新读模型含认证marker'
  assert no_marker(s.call('GET','/operations?instanceId='+item['id']),markers),'历史读模型含认证marker'
 preview=s.preview(instance);assert no_marker(preview,markers) and '[敏感内容已隐藏]' in preview['leftYAML'],'左右YAML未失败关闭'
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
 return {'field':field,'fixedCRDAcceptedMalformedURL':True,'externalReadOnly':True,'noMarkerInHTTPYAMLHistory':True,'directRPC':rpc,'noMarkerInDatabaseWALOrLogs':True,'hiddenValueChangeRejectsOldPreview':True,'originalMetadataUnchanged':True,'historicalEffectiveSpecUnchanged':True,'noPlatformReadSideTargetMutation':True,'sampleDeliveryClaimed':False,'malformedConfigHealthClaimed':False,'fixtureUIDs':{name:before['uid'] for name,before in originals.items()}}
