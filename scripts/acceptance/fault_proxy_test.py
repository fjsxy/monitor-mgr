"""仅证明故障fixture控制HTTP机制，不替代真实Kubernetes或mTLS验收。"""
import http.client,json,pathlib,socket,ssl,sys,threading,types,unittest
sys.path.insert(0,str(pathlib.Path(__file__).parent/'fixtures'))
from fault_proxy import APIHandler,ControlHandler,FaultState,FixtureServer

class Log:
 def __init__(self):self.events=[]
 def emit(self,*args,**kwargs):self.events.append((args,kwargs))

class FaultControlTest(unittest.TestCase):
 def test_original_create_response_requires_prior_exact_identity(self):
  """响应丢失时只接受先登记请求的原UID白名单事件，不凭事后同名GET认领。"""
  state=FaultState();info={'apiVersion':'monitoring.fjsxy.github.io/v1alpha1','kind':'KubeStateMetrics','namespace':'','name':'owned'}
  body={'apiVersion':info['apiVersion'],'kind':info['kind'],'metadata':{'name':'owned'},'spec':{'sensitive':'fixture-private-canary'}}
  response={**body,'metadata':{'name':'owned','uid':'original','resourceVersion':'9'}}
  path='/apis/monitoring.fjsxy.github.io/v1alpha1/kubestatemetrics'
  self.assertIsNone(state.original_create('POST',path,body,response,201))
  state.register_creation({'intentId':'a'*32,'identity':info})
  self.assertIsNone(state.original_create('GET',path,body,response,200))
  self.assertIsNone(state.original_create('POST',path,body,{**response,'metadata':{**response['metadata'],'name':'replacement'}},201))
  actual=state.original_create('POST',path,body,response,201)
  self.assertEqual(actual,{'intentId':'a'*32,'identity':info,'uid':'original','resourceVersion':'9'})
  self.assertNotIn('fixture-private-canary',json.dumps(actual))

 def setUp(self):
  self.closed=socket.socket();self.closed.bind(('127.0.0.1',0))
  self.app=types.SimpleNamespace(state=FaultState(),control_token='x'*40,log=Log(),limits={'io_timeout_seconds':.2,'max_body_bytes':4096,'pause_timeout_seconds':.2,'max_response_bytes':4096},backend={'host':'127.0.0.1','port':self.closed.getsockname()[1],'server_name':'localhost'},backend_context=ssl.create_default_context())
  self.api=FixtureServer(('127.0.0.1',0),APIHandler);self.control=FixtureServer(('127.0.0.1',0),ControlHandler)
  self.threads=[]
  for server in [self.api,self.control]:
   server.app=self.app;thread=threading.Thread(target=server.serve_forever,kwargs={'poll_interval':.01});thread.start();self.threads.append(thread)
 def tearDown(self):
  for server in [self.api,self.control]:server.shutdown();server.server_close()
  for thread in self.threads:thread.join()
  self.closed.close()
 def request(self,server,method,path,body=None,token=True):
  c=http.client.HTTPConnection(*server.server_address,timeout=2)
  headers={'Content-Type':'application/json'}
  if token:headers['X-Fault-Control']=self.app.control_token
  c.request(method,path,None if body is None else json.dumps(body).encode(),headers);r=c.getresponse();status=r.status;r.read();c.close();return status
 def test_read_fault_control_and_reset(self):
  fault={'readMode':'all','rejectWritePath':''}
  self.assertEqual(self.request(self.control,'POST','/faults',fault,False),403)
  self.assertEqual(self.request(self.control,'POST','/faults',{**fault,'extra':True}),409)
  self.assertEqual(self.request(self.control,'POST','/faults',fault),200)
  self.assertEqual(self.request(self.api,'GET','/api/v1/namespaces'),403)
  self.assertEqual(self.request(self.control,'POST','/reset',{}),200)
  self.assertEqual(self.request(self.api,'GET','/api/v1/namespaces'),502)
 def test_exact_write_rejection_excludes_dry_run(self):
  path='/apis/monitoring.example.io/v1/kubestatemetrics/owned'
  self.assertEqual(self.request(self.control,'POST','/faults',{'readMode':'none','rejectWritePath':path+'?x=1'}),409)
  self.assertEqual(self.request(self.control,'POST','/faults',{'readMode':'none','rejectWritePath':path}),200)
  self.assertEqual(self.request(self.api,'PUT',path,{}),403)
  self.assertEqual(self.request(self.api,'PUT',path+'?dryRun=All',{}),502)
  self.assertEqual(self.request(self.api,'PUT',path+'-other',{}),502)
  self.assertEqual(self.request(self.api,'GET',path),502)
  self.assertEqual(self.request(self.control,'POST','/reset',{}),200)
  self.assertEqual(self.request(self.api,'PUT',path,{}),502)

if __name__=='__main__':unittest.main()
