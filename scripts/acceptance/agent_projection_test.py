"""013验收工具的公开证据边界；纯fixture检查不计为产品Q或真实K通过。"""
import hashlib,json,pathlib,secrets,tempfile,types,unittest
from agent_projection import projection_artifact_scan

class ProjectionArtifactSafety(unittest.TestCase):
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

if __name__=='__main__':unittest.main()
