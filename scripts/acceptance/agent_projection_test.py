"""013验收工具的公开证据边界；纯fixture检查不计为产品Q或真实K通过。"""
import contextlib,hashlib,io,json,pathlib,secrets,sqlite3,tempfile,types,unittest
from agent_projection import database_probe,projection_artifact_scan,wait_operator_status,reason_runtime_projection

class ProjectionArtifactSafety(unittest.TestCase):
 def test_reason_branch_requires_exact_hidden_runtime_in_both_read_models(self):
  marker=secrets.token_hex(24);malformed=lambda value:'https://reader:'+value+'@example.invalid/%zz'
  class Suite:
   targetns='test';cluster={'id':'cluster'};safe=False
   def cr(self,name=None):return {'metadata':{'name':name,'generation':2},'status':{'conditions':[{'type':'Available','observedGeneration':2,'reason':malformed(marker)}]}}
   def k(self,*args,**kwargs):return types.SimpleNamespace(returncode=0)
   def call(self,method,path,body=None):
    runtime={'error':'[敏感内容已隐藏]' if self.safe else 'OrdinaryReason'}
    return runtime if method=='POST' else {'instances':[{'name':'outside','readOnly':True,'runtime':runtime}]}
  suite=Suite()
  with self.assertRaises(AssertionError) as failure:reason_runtime_projection(suite,{'id':'instance','name':'managed'},'outside',[marker],malformed,timeout=0)
  self.assertFalse(suite.projection_reason_branch['branchObserved'])
  self.assertNotIn(marker,str(failure.exception))
  suite.safe=True;reason_runtime_projection(suite,{'id':'instance','name':'managed'},'outside',[marker],malformed,timeout=0)
  self.assertTrue(suite.projection_reason_branch['branchObserved'])
  self.assertNotIn(marker,json.dumps(suite.projection_reason_branch))

 def test_operator_branch_requires_actual_status_and_failure_is_safe(self):
  marker=secrets.token_hex(24)
  suite=types.SimpleNamespace(cr=lambda name:{'status':{'message':'waiting'}})
  with self.assertRaises(AssertionError) as failure:wait_operator_status(suite,['managed','external'],[marker],timeout=0)
  self.assertNotIn(marker,str(failure.exception))
  self.assertFalse(suite.projection_status_branch['branchObserved'])
  suite.cr=lambda name:{'status':{'message':'parse https://reader:'+marker+'@example.invalid/%zz'}}
  actual=wait_operator_status(suite,['managed','external'],[marker],timeout=0)
  self.assertTrue(set(actual)=={'managed','external'})
  self.assertTrue(suite.projection_status_branch['branchObserved'])
  self.assertNotIn(marker,json.dumps(suite.projection_status_branch))

 def test_hit_is_failed_but_public_record_and_file_are_safe(self):
  with tempfile.TemporaryDirectory(prefix='projection-artifact-') as directory:
   marker=secrets.token_hex(24);out=pathlib.Path(directory);trace=out/'case.http.jsonl'
   trace.write_text(json.dumps({'unexpected':'https://reader:'+marker+'@example.invalid/%zz'})+'\n')
   record={'outcome':'passed','evidence':{'raw':marker},'evidenceRefs':[{'path':trace.name,'sha256':hashlib.sha256(trace.read_bytes()).hexdigest()}]}
   suite=types.SimpleNamespace(out=out,projection_markers=[marker]);scan=projection_artifact_scan(suite,record)
   self.assertFalse(scan['noMarker'])
   self.assertTrue(marker not in trace.read_text())
   self.assertTrue(marker not in json.dumps(record))
   self.assertTrue(record['evidenceRefs'][0]['sha256']==hashlib.sha256(trace.read_bytes()).hexdigest())
   self.assertTrue(scan['publishedArtifactsSafe'])


 def test_database_probe_fingerprints_blob_bytes_and_storage_class(self):
  # 执行真实probe脚本和SQLite；job外部边界仅把/data映射到独立临时卷，不模拟SQL结果。
  with tempfile.TemporaryDirectory(prefix='projection-database-') as directory:
   data=pathlib.Path(directory);db=sqlite3.connect(data/'platform.db')
   db.execute('CREATE TABLE publication_versions(id TEXT PRIMARY KEY,effective_spec BLOB)')
   db.execute('INSERT INTO publication_versions VALUES(?,?)',('version',b'{}'));db.commit()
   self.assertTrue(db.execute('SELECT typeof(effective_spec) FROM publication_versions').fetchone()[0]=='blob')
   class Probe:
    def job(self,name,script,readonly):
     if not readonly:raise AssertionError('probe必须只读挂载')
     output=io.StringIO()
     with contextlib.redirect_stdout(output):exec(script.replace('/data/',str(data)+'/'),{})
     return json.loads(output.getvalue())
   marker=secrets.token_hex(24);probe=Probe();first=database_probe(probe,[marker])
   self.assertTrue(len(first)==64)
   self.assertTrue(database_probe(probe,[marker],first)==first)
   db.execute('UPDATE publication_versions SET effective_spec=?',(b'{"changed":true}',));db.commit()
   with self.assertRaises(AssertionError):database_probe(probe,[marker],first)
   db.execute('UPDATE publication_versions SET effective_spec=?',('{}',));db.commit()
   with self.assertRaises(AssertionError):database_probe(probe,[marker],first)
   db.close()

if __name__=='__main__':unittest.main()
