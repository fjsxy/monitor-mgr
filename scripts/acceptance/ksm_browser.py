"""真实平台Pod上的U03浏览器验收；禁用trace，不导出含上传材料的网络记录。"""
import hashlib,json,os,pathlib
from transport import process as subprocess

def run_browser(s):
 root=pathlib.Path(__file__).resolve().parents[2];out=s.out/'browser';out.mkdir(parents=True,exist_ok=True)
 before={t:s.inventory(t) for t in s.target_names}
 env=dict(os.environ,BASE_URL=s.base,ADMIN_PASSWORD=s.password,KSM_UI_KUBECONFIG=s.a.control_pod_config)
 # 关键：凭据只经进程环境和原0600文件传递；不写argv、浏览器trace或收据。
 command=['npm','--prefix','web','run','test:e2e','--','ksm.spec.ts','--reporter=json','--output='+str(out)]
 result=subprocess.run(command,cwd=root,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=100)
 # npm前导文本不属于reporter；从完整JSON根开始解析，原输出不直接进入收据。
 raw=result.stdout.decode();start=raw.find('{\n  "config"');assert start>=0,'浏览器未输出可判定JSON报告'
 report=json.loads(raw[start:]);(out/'results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2));(out/'stderr.log').write_bytes(result.stderr)
 stats=report['stats'];assert result.returncode==0 and stats['expected']==1 and not stats['unexpected'] and not stats['skipped'],{'exitCode':result.returncode,'stats':stats}
 after={t:s.inventory(t) for t in s.target_names};assert before==after,'U03草稿行为改变了观测CR'
 screenshots=list(out.rglob('ksm-collector-namespace-preserved.png'));assert len(screenshots)==1,'缺少安全页面截图'
 clusters=s.call('GET','/clusters')['clusters'];assert len(clusters)==1
 instances=s.call('GET','/clusters/'+clusters[0]['id']+'/instances')['instances'];assert len(instances)==1 and not instances[0]['currentVersionId']
 draft=s.call('GET','/instances/'+instances[0]['id']+'/draft');assert draft['ksm']['collectors']['names']==['pods','deployments'] and set(draft['ksm']['namespaceFilter']['names'])=={'default','kube-system'}
 return {'command':command,'exitCode':result.returncode,'stats':stats,'browserReport':'browser/results.json','screenshot':str(screenshots[0].relative_to(s.out)),'screenshotSHA256':hashlib.sha256(screenshots[0].read_bytes()).hexdigest(),'observabilityInventoryUnchanged':True,'draft':draft,'instance':instances[0]}
