import {expect, test, type Page, type TestInfo} from '@playwright/test';

type Fixture = {password:string;clusterId:string;namespace:string;agentNamespace:string;runId:string;historical:{id:string;name:string;component:string}[];issue30KnownAgent:{id:string;name:string}};

// 快捷只驱动原生selector，实际采集与完整权限矩阵分别由Ginkgo真实集群/HTTP测试负责。
export async function agentCreatorAssociation(page:Page,baseURL:string|undefined,fixture:Fixture,info:TestInfo) {
 const send=async(path:string,data:unknown,status=200)=>{const response=await page.request.post('/api/v1'+path,{headers:{Origin:baseURL!,'Idempotency-Key':crypto.randomUUID()},data});expect(response.status(),path).toBe(status);return response.json()};
 const admin=(await(await page.request.get('/api/v1/session')).json()).account;
 const loginAdmin=async()=>{await send('/sessions',{username:'admin',password:fixture.password},201)};
 const rule=()=>({object:{mode:'None'},namespace:{mode:'Own'}});
 const agent={name:'browser-association-agent',namespace:fixture.agentNamespace,desiredState:'Running',shards:1,resources:{requestCPU:'200m',requestMemory:'512Mi',limitMemory:'1Gi'},storage:{mode:'PVC',capacity:'1Gi',whenScaled:'Retain',whenDeleted:'Retain'},serviceAccountName:'prometheus-agent',associations:{serviceMonitor:rule(),podMonitor:rule(),probe:rule(),scrapeConfig:rule()},remoteWrite:[{url:'http://receiver.example.invalid/write',auth:{mode:'None'}}]};
 const protectedAgent=await send('/clusters/'+fixture.clusterId+'/instances',{component:'Prometheus Agent',agent:{...agent,name:'browser-association-protected'}},201);
 const username='association-owner-'+crypto.randomUUID().slice(0,8),temporary=crypto.randomUUID(),password=crypto.randomUUID();
 const user=await send('/accounts',{username,password:temporary,role:'USER'},201);
 await send('/clusters/'+fixture.clusterId+'/transfer',{ownerId:user.id});
 const drawer=page.getByRole('dialog',{name:'实例详情',exact:true});
 const open=async(instance:{name:string},manage=true)=>{await page.reload();await page.getByRole('button',{name:'观测配置',exact:true}).click();await page.getByRole('row').filter({has:page.getByText(instance.name,{exact:true})}).getByRole('button',{name:manage?'编辑':'查看',exact:true}).click()};
 const save=async(id:string)=>{const saving=page.waitForResponse(r=>new URL(r.url()).pathname===`/api/v1/instances/${id}/draft`&&r.request().method()==='PUT');await drawer.getByRole('button',{name:'保存草稿',exact:true}).click();const response=await saving;expect(response.status()).toBe(200);const value=await response.json();await expect(drawer.getByText(`草稿 v${value.revision} 已保存`,{exact:true})).toBeVisible();return value};
 try {
  await send('/sessions',{username,password:temporary},201);await send('/session/password',{currentPassword:temporary,newPassword:password});await send('/sessions',{username,password},201);
  const instance=await send('/clusters/'+fixture.clusterId+'/instances',{component:'Prometheus Agent',agent},201);
  await test.step('I30-B01.quick 默认None/Own，显式快捷保存后重开固定创建用户',async()=>{
   await open(instance);const quick=drawer.getByLabel('按用户关联 exporter',{exact:true});
   await expect(drawer.getByLabel('ServiceMonitor 对象模式',{exact:true})).toHaveValue('None');await expect(drawer.getByLabel('ServiceMonitor namespace模式',{exact:true})).toHaveValue('Own');
   await expect(quick).toContainText(`快捷默认用户：${user.id}`);await expect(quick).toContainText('未按 user 标签关联');
   await quick.getByRole('button',{name:'按创建用户关联 exporter',exact:true}).click();await expect(drawer.getByLabel('ServiceMonitor 对象 label 1 value',{exact:true})).toHaveValue(user.id);
   await expect(drawer.getByLabel('ServiceMonitor namespace模式',{exact:true})).toHaveValue('Own');await save(instance.id);
   await drawer.getByRole('button',{name:'关闭实例详情',exact:true}).click();await open(instance);await expect(quick).toContainText(`当前规则目标用户：${user.id}`);
  });
  await test.step('I30-B01.custom.all namespace All保存重开且不扩大object规则',async()=>{
   await drawer.getByLabel('ServiceMonitor namespace模式',{exact:true}).selectOption('All');await save(instance.id);await drawer.getByRole('button',{name:'关闭实例详情',exact:true}).click();await open(instance);await expect(drawer.getByLabel('ServiceMonitor namespace模式',{exact:true})).toHaveValue('All');await expect(drawer.getByLabel('ServiceMonitor 对象 label 1 value',{exact:true})).toHaveValue(user.id);
  });
  await test.step('I30-B01.custom.labels namespace标签和自定义表达式独立保存，匹配与下发预览可读',async()=>{
   await drawer.getByLabel('ServiceMonitor namespace模式',{exact:true}).selectOption('Labels');await drawer.getByRole('button',{name:'添加 ServiceMonitor namespace label',exact:true}).click();
   await drawer.getByLabel('ServiceMonitor namespace label 1 key',{exact:true}).fill('monitor-mgr.test/run');await drawer.getByLabel('ServiceMonitor namespace label 1 value',{exact:true}).fill(fixture.runId);
   await drawer.getByRole('button',{name:'添加 ServiceMonitor 对象 expression',exact:true}).click();await drawer.getByLabel('ServiceMonitor 对象 expression 1 key',{exact:true}).fill('disabled');await drawer.getByLabel('ServiceMonitor 对象 expression 1 operator',{exact:true}).selectOption('DoesNotExist');
   await save(instance.id);
   const matching=page.waitForResponse(r=>new URL(r.url()).pathname===`/api/v1/instances/${instance.id}/association-previews`&&r.request().method()==='POST');await drawer.getByRole('button',{name:'刷新采集关联预览',exact:true}).click();const response=await matching;expect(response.status()).toBe(200);const source=(await response.json()).sources.find((s:{source:string})=>s.source==='association:ServiceMonitor');expect(source.error).toBe('');expect(source.lastSuccessAt).toBeTruthy();
   await drawer.getByRole('button',{name:'预览下发',exact:true}).click();const preview=page.getByRole('dialog',{name:`下发预览 · ${instance.name}`,exact:true});await expect(preview).toBeVisible();await expect(preview).toContainText(user.id);await preview.getByRole('button',{name:'关闭下发预览',exact:true}).click();
   await drawer.getByRole('button',{name:'关闭实例详情',exact:true}).click();
  });
  await test.step('I30-B01.admin-edit 管理员编辑CPU不改变user目标、namespace和表达式',async()=>{
   await loginAdmin();await open(instance);await drawer.getByLabel('Agent CPU request',{exact:true}).fill('210m');await save(instance.id);await drawer.getByRole('button',{name:'关闭实例详情',exact:true}).click();await open(instance);
   await expect(drawer.getByLabel('按用户关联 exporter',{exact:true})).toContainText(`当前规则目标用户：${user.id}`);await expect(drawer.getByLabel('ServiceMonitor namespace模式',{exact:true})).toHaveValue('Labels');await expect(drawer.getByLabel('ServiceMonitor 对象 expression 1 operator',{exact:true})).toHaveValue('DoesNotExist');
   await page.screenshot({path:info.outputPath('agent-creator-association.png')});await drawer.getByRole('button',{name:'关闭实例详情',exact:true}).click();
  });
  await test.step('I30-B02.known 可靠user但角色未知仍可作为快捷目标',async()=>{
   const known=fixture.issue30KnownAgent;await open(known);const detail=await(await page.request.get('/api/v1/instances/'+known.id)).json();expect(detail.creatorUserId).toBeTruthy();expect(detail.creatorRole).toBe('');expect(detail.creatorIdentityKnown).toBe(false);
   const quick=drawer.getByLabel('按用户关联 exporter',{exact:true});await expect(quick.getByRole('button',{name:'按创建用户关联 exporter',exact:true})).toBeEnabled();await quick.getByRole('button',{name:'按创建用户关联 exporter',exact:true}).click();await expect(drawer.getByLabel('ServiceMonitor 对象 label 1 value',{exact:true})).toHaveValue(detail.creatorUserId);await drawer.getByRole('button',{name:'关闭实例详情',exact:true}).click();
  });
  await test.step('I30-B02.unknown 未知user禁快捷且当前管理员可保存自定义规则',async()=>{
   const unknown=fixture.historical.find(value=>value.component==='Prometheus Agent')!;await open(unknown);const quick=drawer.getByLabel('按用户关联 exporter',{exact:true});await expect(quick).toContainText('实例创建用户未知');await expect(quick.getByRole('button',{name:'按创建用户关联 exporter',exact:true})).toBeDisabled();
   await drawer.getByLabel('ServiceMonitor 对象模式',{exact:true}).selectOption('Labels');await drawer.getByRole('button',{name:'添加 ServiceMonitor 对象 label',exact:true}).click();await drawer.getByLabel('ServiceMonitor 对象 label 1 key',{exact:true}).fill('team');await drawer.getByLabel('ServiceMonitor 对象 label 1 value',{exact:true}).fill('custom');await save(unknown.id);
   const detail=await(await page.request.get('/api/v1/instances/'+unknown.id)).json();expect(detail.creatorUserId).toBe('');expect(detail.creatorRole).toBe('');await drawer.getByRole('button',{name:'关闭实例详情',exact:true}).click();
  });
  await test.step('I30-B02.protected 标签无法绕过管理员Agent只读保护',async()=>{
   await send('/sessions',{username,password},201);await open(protectedAgent,false);await expect(drawer.getByRole('button',{name:'保存草稿',exact:true})).toHaveCount(0);await expect(drawer.getByLabel('按用户关联 exporter',{exact:true}).getByRole('button',{name:'按创建用户关联 exporter',exact:true})).toBeDisabled();await drawer.getByRole('button',{name:'关闭实例详情',exact:true}).click();
  });
 } finally {await loginAdmin();await send('/clusters/'+fixture.clusterId+'/transfer',{ownerId:admin.id});await page.reload();await page.getByRole('button',{name:'观测配置',exact:true}).click()}
}
