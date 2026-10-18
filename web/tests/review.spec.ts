import {test,expect,type Page,type TestInfo} from '@playwright/test';
import {readFileSync} from 'node:fs';

// 仅正式runner的0600私有fixture可驱动B；业务全部经真实平台Service，不拦截业务HTTP。
type Control={baseURL:string;controlToken:string;writePath:string};
type Fixture={adminUsername:string;adminPassword:string;clusterId:string;clusterName:string;reviewAgent:{id:string;name:string;uid:string;failureControl:Control}};
const fixture:Fixture|undefined=process.env.WORKBENCH_UI_FIXTURE?JSON.parse(readFileSync(process.env.WORKBENCH_UI_FIXTURE,'utf8')):undefined;
const safe=(text:string)=>[fixture?.adminPassword,fixture?.reviewAgent.failureControl.controlToken].filter((v):v is string=>Boolean(v)).reduce((v,secret)=>v.split(secret).join('[敏感内容已隐藏]'),text);
const events=new WeakMap<Page,{console:unknown[];pageErrors:string[];httpErrors:unknown[];operationReads:string[]}>();
const headers=(base:string)=>({Origin:new URL(base).origin,'Idempotency-Key':crypto.randomUUID()});
test.beforeEach(async({page})=>{
 test.skip(!fixture,'需要真实平台Pod Service与0600 WORKBENCH_UI_FIXTURE，无fixture不计B通过');
 const value={console:[] as unknown[],pageErrors:[] as string[],httpErrors:[] as unknown[],operationReads:[] as string[]};events.set(page,value);
 page.on('console',event=>value.console.push({type:event.type(),text:safe(event.text())}));
 page.on('pageerror',event=>value.pageErrors.push(safe(event.message)));
 page.on('response',response=>{if(response.status()>=400)value.httpErrors.push({status:response.status(),method:response.request().method(),path:new URL(response.url()).pathname})});
 page.on('request',request=>{const path=new URL(request.url()).pathname;if(request.method()==='GET'&&/^\/api\/v1\/operations\/[^/]+$/.test(path))value.operationReads.push(path)});
});
test.afterEach(async({page},info)=>{
 if(!fixture)return;
 await info.attach('browser-console.json',{body:Buffer.from(safe(JSON.stringify(events.get(page)))),contentType:'application/json'});
 const layout=await page.evaluate(()=>({viewport:window.innerWidth,scrollWidth:document.documentElement.scrollWidth})).catch(()=>null);
 await info.attach('browser-layout.json',{body:Buffer.from(JSON.stringify(layout)),contentType:'application/json'});
 expect(events.get(page)?.pageErrors).toEqual([]);if(layout)expect(layout.scrollWidth).toBeLessThanOrEqual(layout.viewport);
});
async function enter(page:Page,info:TestInfo){
 info.annotations.push({type:'browserVersion',description:page.context().browser()?.version()??'unknown'});
 await page.goto('/');await page.getByLabel('账号',{exact:true}).fill(fixture!.adminUsername);await page.getByLabel('密码',{exact:true}).fill(fixture!.adminPassword);await page.getByRole('button',{name:'登录',exact:true}).click();
 await expect(page.getByRole('heading',{name:'账号管理',exact:true})).toBeVisible();await page.getByRole('button',{name:'观测配置',exact:true}).click();await expect(page.getByRole('heading',{name:'观测实例',exact:true})).toBeVisible();
}
async function fault(control:Control,reject:boolean){
 const result=await fetch(control.baseURL+'/faults',{method:'POST',headers:{'Content-Type':'application/json','X-Fault-Control':control.controlToken},body:JSON.stringify({readMode:'none',rejectWritePath:reject?control.writePath:''})});expect(result.status).toBe(200);
}
async function snapshot(info:TestInfo,page:Page,name:string,proof:unknown){
 await info.attach('review-result.json',{body:Buffer.from(JSON.stringify(proof)),contentType:'application/json'});await info.attach(name+'.png',{body:await page.screenshot(),contentType:'image/png'});
}

test('REG19-AGENT-DELETE-OP-001 删除后运行页接收并轮询原operation',async({page},info)=>{
 test.setTimeout(120000);await enter(page,info);const item=fixture!.reviewAgent;
 const row=page.getByRole('row').filter({hasText:item.name});await row.getByRole('button',{name:'删除',exact:true}).click();
 const confirm=page.getByRole('dialog',{name:'删除实例',exact:true});await expect(confirm).toContainText('原部署版本');
 const accepted=page.waitForResponse(r=>new URL(r.url()).pathname===`/api/v1/instances/${item.id}/deletions`&&r.request().method()==='POST');
 await confirm.getByRole('button',{name:'确认删除实例',exact:true}).click();const op=await(await accepted).json();expect(op.action).toBe('DELETE');
 const drawer=page.getByRole('dialog',{name:'实例详情',exact:true});await expect(drawer.getByRole('tab',{name:'运行信息',exact:true})).toHaveAttribute('aria-selected','true');
 await expect(drawer.getByText('操作 '+op.operationId,{exact:true})).toBeVisible();
 await expect.poll(()=>events.get(page)!.operationReads.filter(v=>v===`/api/v1/operations/${op.operationId}`).length,{timeout:15000}).toBeGreaterThan(0);
 await expect(drawer.locator('.lifecycle-actions')).toContainText('执行尝试');await expect(drawer.locator('.lifecycle-actions')).toContainText('独立存储结果');
 const current=await(await page.request.get(`/api/v1/operations/${op.operationId}`)).json();
 if(['DELETION_VERIFYING','DELETION_BLOCKED','RESULT_UNCERTAIN'].includes(current.state)||['Unknown','Deleting','Pending'].includes(current.storageResult))await expect(drawer.getByRole('button',{name:'只读核验结果',exact:true})).toBeEnabled();
 expect((await(await page.request.get(`/api/v1/instances/${item.id}`)).json()).uid).toBe(item.uid);
 await snapshot(info,page,'REG19-Agent-delete',{operationId:op.operationId,action:current.action,state:current.state,storageResult:current.storageResult,agentUID:item.uid,operationReads:events.get(page)!.operationReads});
});

test('REG19-AGENT-HISTORY-OP-001 重开历史重试原失败版本而非新草稿',async({page},info)=>{
 test.setTimeout(180000);await enter(page,info);const item=fixture!.reviewAgent;
 await page.getByRole('row').filter({hasText:item.name}).getByRole('button',{name:'编辑',exact:true}).click();
 let drawer=page.getByRole('dialog',{name:'实例详情',exact:true});const cpu=drawer.getByLabel('Agent CPU request',{exact:true});
 await cpu.fill('321m');await drawer.getByRole('button',{name:'保存草稿',exact:true}).click();await expect(drawer.getByText('草稿 v2 已保存',{exact:true})).toBeVisible();await drawer.getByRole('button',{name:'预览下发',exact:true}).click();
 try{
  await fault(item.failureControl,true);
  const accepted=page.waitForResponse(r=>new URL(r.url()).pathname===`/api/v1/instances/${item.id}/publications`&&r.request().method()==='POST');await page.getByRole('button',{name:'确认下发',exact:true}).click();const failed=await(await accepted).json();
  await expect.poll(async()=> (await(await page.request.get(`/api/v1/operations/${failed.operationId}`)).json()).state,{timeout:25000}).toBe('FAILED');
  await drawer.getByRole('tab',{name:'配置草稿',exact:true}).click();await cpu.fill('777m');await drawer.getByRole('button',{name:'保存草稿',exact:true}).click();await expect(drawer.getByText('草稿 v3 已保存',{exact:true})).toBeVisible();await drawer.getByRole('button',{name:'关闭实例详情',exact:true}).click();
  await page.getByRole('row').filter({hasText:item.name}).getByRole('button',{name:'编辑',exact:true}).click();drawer=page.getByRole('dialog',{name:'实例详情',exact:true});await drawer.getByRole('tab',{name:'下发历史',exact:true}).click();
  const history=drawer.locator('article.publication-item').filter({hasText:'操作 '+failed.operationId});await expect(history).toContainText('失败');await expect(history.getByRole('button',{name:'重试原版本',exact:true})).toBeEnabled();
  await fault(item.failureControl,false);const retry=page.waitForResponse(r=>new URL(r.url()).pathname===`/api/v1/operations/${failed.operationId}/retry`&&r.request().method()==='POST');await history.getByRole('button',{name:'重试原版本',exact:true}).click();const next=await(await retry).json();
  expect(next.operationId).toBe(failed.operationId);expect(next.versionId).toBe(failed.versionId);expect(next.attemptId).not.toBe(failed.attemptId);
  await expect.poll(async()=> (await(await page.request.get(`/api/v1/operations/${failed.operationId}`)).json()).state,{timeout:25000}).toBe('SUCCEEDED');
  const current=await(await page.request.get(`/api/v1/instances/${item.id}`)).json();expect(current.currentVersionId).toBe(failed.versionId);expect(current.uid).toBe(item.uid);
  const version=await(await page.request.get(`/api/v1/instances/${item.id}/versions/${failed.versionId}`)).json();expect(version.agent.resources.requestCPU).toBe('321m');expect(version.sourceDraftRevision).toBe(2);
  const draft=await(await page.request.get(`/api/v1/instances/${item.id}/draft`)).json();expect(draft.agent.resources.requestCPU).toBe('777m');expect(draft.revision).toBe(3);
  const attempts=await(await page.request.get(`/api/v1/operations/${failed.operationId}/attempts`)).json();expect(attempts.attempts).toHaveLength(2);expect(attempts.attempts.map((v:{attemptId:string})=>v.attemptId)).toContain(failed.attemptId);
  await snapshot(info,page,'REG19-Agent-history',{operationId:failed.operationId,versionId:failed.versionId,sourceDraftRevision:version.sourceDraftRevision,publishedCPU:version.agent.resources.requestCPU,draftCPU:draft.agent.resources.requestCPU,agentUID:current.uid,attempts:attempts.attempts.map((v:{attemptId:string;state:string})=>({attemptId:v.attemptId,state:v.state}))});
 }finally{await fault(item.failureControl,false)}
});
