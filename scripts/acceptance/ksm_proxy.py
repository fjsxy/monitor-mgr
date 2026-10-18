"""独占TLS API故障fixture；平台到达proxy时已经属于possibly-sent，不能充当产品未派发屏障。"""
import base64,copy,json,os,pathlib,secrets,subprocess,tempfile,time,urllib.parse,urllib.request
ROOT=pathlib.Path(__file__).resolve().parents[2]
class Proxy:
 def __init__(self,s,target):
  self.s=s;self.token=secrets.token_urlsafe(36);self.ns=s.ns;self.target=target
  with tempfile.TemporaryDirectory(prefix='ksm-proxy-') as raw:
   p=pathlib.Path(raw)
   def openssl(*a):
    r=subprocess.run(['openssl',*a],cwd=p,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    if r.returncode:raise RuntimeError('fixture PKI生成失败（敏感参数已隐藏）')
   openssl('req','-x509','-newkey','rsa:2048','-nodes','-keyout','ca.key','-out','ca.crt','-days','1','-subj','/CN=ksm-fixture-ca')
   for name in ['server','client']:
    openssl('req','-newkey','rsa:2048','-nodes','-keyout',name+'.key','-out',name+'.csr','-subj','/CN='+name)
    (p/'ext').write_text('subjectAltName=DNS:fault-proxy.'+self.ns+'.svc\nextendedKeyUsage=serverAuth\n' if name=='server' else 'extendedKeyUsage=clientAuth\n')
    openssl('x509','-req','-in',name+'.csr','-CA','ca.crt','-CAkey','ca.key','-CAcreateserial','-out',name+'.crt','-days','1','-extfile','ext')
   original=s.config(target);cluster=original['clusters'][0]['cluster'];user=original['users'][0]['user'];u=urllib.parse.urlsplit(cluster['server'])
   files={'backend-ca.crt':base64.b64decode(cluster['certificate-authority-data']),'backend-admin.crt':base64.b64decode(user['client-certificate-data']),'backend-admin.key':base64.b64decode(user['client-key-data']),'proxy-server.crt':(p/'server.crt').read_bytes(),'proxy-server.key':(p/'server.key').read_bytes(),'proxy-client-ca.crt':(p/'ca.crt').read_bytes(),'control-token':self.token.encode()}
   cfg={'backend':{'host':u.hostname,'port':u.port or 443,'server_name':cluster.get('tls-server-name',u.hostname),'ca_file':'/private/backend-ca.crt','client_cert_file':'/private/backend-admin.crt','client_key_file':'/private/backend-admin.key'},'frontend':{'bind':'0.0.0.0','port':8443,'cert_file':'/private/proxy-server.crt','key_file':'/private/proxy-server.key','client_ca_file':'/private/proxy-client-ca.crt'},'control':{'port':9090,'token_file':'/private/control-token'},'limits':{'io_timeout_seconds':15,'pause_timeout_seconds':30},'event_log_file':'/evidence/events.jsonl'}
   files['config.json']=json.dumps(cfg).encode()
   secret={'apiVersion':'v1','kind':'Secret','metadata':{'name':'fault-private','namespace':self.ns},'data':{k:base64.b64encode(v).decode() for k,v in files.items()}}
   code={'apiVersion':'v1','kind':'ConfigMap','metadata':{'name':'fault-code','namespace':self.ns},'data':{'proxy.py':(ROOT/'scripts/acceptance/fixtures/fault_proxy.py').read_text()}}
   pod={'apiVersion':'v1','kind':'Pod','metadata':{'name':'fault-proxy','namespace':self.ns},'spec':{'automountServiceAccountToken':False,'containers':[{'name':'proxy','image':'python:3.13.2-alpine','imagePullPolicy':'Never','command':['python','/fixture/proxy.py','serve','--config','/private/config.json'],'volumeMounts':[{'name':'private','mountPath':'/private','readOnly':True},{'name':'code','mountPath':'/fixture','readOnly':True},{'name':'evidence','mountPath':'/evidence'}],'readinessProbe':{'tcpSocket':{'port':8443},'periodSeconds':1}}],'volumes':[{'name':'private','secret':{'secretName':'fault-private','defaultMode':384}},{'name':'code','configMap':{'name':'fault-code'}},{'name':'evidence','emptyDir':{}}]}}
   svc={'apiVersion':'v1','kind':'Service','metadata':{'name':'fault-proxy','namespace':self.ns},'spec':{'selector':{'fixture':'fault-proxy'},'ports':[{'port':8443,'targetPort':8443}]}};pod['metadata']['labels']={'fixture':'fault-proxy'}
   for obj in [secret,code,pod,svc]:s.k('apply','-f','-',data=obj)
   s.k('-n',self.ns,'wait','--for=condition=Ready','pod/fault-proxy','--timeout=90s')
   self.config={'apiVersion':'v1','kind':'Config','clusters':[{'name':'proxy','cluster':{'server':'https://fault-proxy.'+self.ns+'.svc:8443','certificate-authority-data':base64.b64encode((p/'ca.crt').read_bytes()).decode()}}],'users':[{'name':'fixture','user':{'client-certificate-data':base64.b64encode((p/'client.crt').read_bytes()).decode(),'client-key-data':base64.b64encode((p/'client.key').read_bytes()).decode()}}],'contexts':[{'name':'explicit','context':{'cluster':'proxy','user':'fixture'}}]}
  self.port=s.port+200;self.base='http://127.0.0.1:'+str(self.port)
  self.pf=subprocess.Popen([s.a.kubectl,'--kubeconfig',s.a.control_config,'-n',self.ns,'port-forward','pod/fault-proxy',str(self.port)+':9090'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
  deadline=time.monotonic()+15
  while time.monotonic()<deadline:
   try:self.control('state');break
   except Exception:time.sleep(.1)
  else:raise AssertionError('fixture control不可达')
 def control(self,action,body=None):
  req=urllib.request.Request(self.base+'/'+('rule' if action=='arm' else action),data=None if body is None else json.dumps(body).encode(),method='GET' if action=='state' else 'POST',headers={'X-Fault-Control':self.token,'Content-Type':'application/json'})
  return json.load(urllib.request.urlopen(req,timeout=5))
 def arm(self,method,path,action='pause_before_send'):return self.control('arm',{'method':method,'path':path,'action':action})
 def paused(self):
  end=time.monotonic()+6
  while time.monotonic()<end:
   state=self.control('state');rule=state.get('rule') or {}
   if rule.get('stage')=='paused_before_send':return state
   time.sleep(.05)
  raise AssertionError('没有抵达真实API发送前fixture屏障')
 def close(self):
  try:self.control('reset',{})
  finally:self.pf.terminate();self.pf.wait(timeout=5)
 def events(self):return self.s.k('-n',self.ns,'exec','fault-proxy','--','cat','/evidence/events.jsonl').stdout.decode()
