import {test, expect} from '@playwright/test';
import {readFileSync} from 'node:fs';

// 唯一浏览器场景只验证关键交互与精确对象身份；字段矩阵归后端 UT。
test('E2E-10 登录、草稿、精确修订、发布、版本和删除', async ({page, baseURL}, info) => {
  test.setTimeout(180000);
  const path=process.env.PHASE1_E2E_FIXTURE;
  expect(path,'必须提供真实平台 Pod 的私有夹具').toBeTruthy();
  const fixture=JSON.parse(readFileSync(path!, 'utf8'));
  let browserOwner:{id:string;password:string};
  const errors:string[]=[];page.on('pageerror',error=>errors.push(error.message));
  await test.step('登录并用表单创建 KSM 草稿',async()=>{
    await page.goto('/');await page.getByLabel('账号',{exact:true}).fill('admin');await page.getByLabel('密码',{exact:true}).fill(fixture.password);
    await page.getByRole('button',{name:'登录',exact:true}).click();await expect(page.getByRole('heading',{name:'账号管理',exact:true})).toBeVisible();
    await page.getByRole('button',{name:'观测配置',exact:true}).click();await expect(page.getByRole('heading',{name:'观测实例',exact:true})).toBeVisible();
    await page.getByRole('button',{name:'添加',exact:true}).click();await page.getByRole('button',{name:'配置实例',exact:true}).click();
    const drawer=page.getByRole('dialog',{name:'实例详情',exact:true});await drawer.getByLabel('名称',{exact:true}).fill('browser-draft');
    await drawer.getByLabel('部署命名空间',{exact:true}).selectOption(fixture.namespace);
    await drawer.getByRole('button',{name:'保存草稿',exact:true}).click();await expect(drawer.getByText('草稿 v1 已保存',{exact:true})).toBeVisible();
    await drawer.getByRole('button',{name:'关闭实例详情',exact:true}).click();
  });
  const row=()=>page.getByRole('row').filter({has:page.getByText(fixture.largeName,{exact:true})});
  await row().getByRole('button',{name:'编辑',exact:true}).click();
  const drawer=page.getByRole('dialog',{name:'实例详情',exact:true});
  await test.step('大 revision 在保存和 YAML 预览中保持精确',async()=>{
    await expect(drawer.getByText('草稿 v9007199254740993 已保存',{exact:true})).toBeVisible();
    await drawer.getByLabel('通用 CPU request',{exact:true}).fill('111m');
    const saving=page.waitForRequest(r=>new URL(r.url()).pathname===`/api/v1/instances/${fixture.largeId}/draft`&&r.method()==='PUT');
    await drawer.getByRole('button',{name:'保存草稿',exact:true}).click();expect((await saving).postDataJSON().expectedDraftRevision).toBe('9007199254740993');
    await expect(drawer.getByText('草稿 v9007199254740994 已保存',{exact:true})).toBeVisible();
    await drawer.getByRole('tab',{name:'只读 YAML',exact:true}).click();await expect(drawer).toContainText('111m');
    await drawer.getByRole('tab',{name:'配置草稿',exact:true}).click();
  });
  await test.step('查看 diff，明确确认后才发布到真实集群',async()=>{
    await drawer.getByRole('button',{name:'预览下发',exact:true}).click();await expect(page.getByRole('heading',{name:'CR YAML 对比',exact:true})).toBeVisible();
    const before=await (await page.request.get(`/api/v1/instances/${fixture.largeId}`)).json();expect(before.uid).toBe('');
    const preview=page.getByRole('dialog',{name:/下发预览/});await expect(preview).toContainText('kind: ServiceMonitor');await expect(preview).toContainText('http-metrics');await expect(preview).toContainText('monitor-mgr.io/user-id');
    // 第二资源由真实目标代理明确拒绝，页面须显示部分成功且可人工补齐。
    expect((await page.request.post(`${fixture.proxyBase}/faults`,{headers:{'X-Fault-Control':fixture.proxyToken},data:{readMode:'none',rejectWritePath:`/apis/monitoring.coreos.com/v1/namespaces/${fixture.namespace}/servicemonitors`}})).ok()).toBeTruthy();
    const accepted=page.waitForResponse(r=>new URL(r.url()).pathname===`/api/v1/instances/${fixture.largeId}/publications`&&r.request().method()==='POST');
    await page.getByRole('button',{name:'确认下发',exact:true}).click();const op=await(await accepted).json();
    expect(op.instanceId).toBe(fixture.largeId);await expect.poll(async()=> (await(await page.request.get(`/api/v1/operations/${op.operationId}`)).json()).state,{timeout:45000}).toBe('FAILED');
    await drawer.getByRole('button',{name:'刷新当前状态',exact:true}).click();
    await expect(drawer.getByLabel('来源 CR',{exact:true})).toContainText('最后成功观察');
    const progress=drawer.getByLabel('逐资源执行进度',{exact:true}).first();await expect(progress).toBeVisible();await expect(progress).toContainText('KubeStateMetrics');await expect(progress).toContainText('UID：');
    await expect(progress).toContainText('ServiceMonitor');await expect(progress).toContainText('失败');
    await progress.getByText('查看冻结配置',{exact:true}).first().click();await expect(progress).toContainText('111m');await page.screenshot({path:info.outputPath('companion-partial-failure.png')});
    expect((await page.request.post(`${fixture.proxyBase}/reset`,{headers:{'X-Fault-Control':fixture.proxyToken},data:{}})).ok()).toBeTruthy();
    const retrying=page.waitForResponse(r=>new URL(r.url()).pathname===`/api/v1/operations/${op.operationId}/retry`&&r.request().method()==='POST');
    await drawer.getByRole('button',{name:'重试原版本',exact:true}).first().click();const retried=await(await retrying).json();expect(retried.versionId).toBe(op.versionId);expect(retried.attemptId).not.toBe(op.attemptId);
    await expect.poll(async()=> (await(await page.request.get(`/api/v1/operations/${op.operationId}`)).json()).state,{timeout:45000}).toBe('SUCCEEDED');
    await drawer.getByRole('button',{name:'刷新当前状态',exact:true}).click();
    await expect(drawer.getByLabel('逐资源执行进度',{exact:true}).first()).toContainText('ServiceMonitor');await page.screenshot({path:info.outputPath('resource-progress.png')});
  });
  await test.step('版本入口回读冻结来源，明确删除原实例',async()=>{
    await drawer.getByRole('button',{name:'关闭实例详情',exact:true}).click();await page.getByRole('button',{name:'刷新列表',exact:true}).click();
    await row().locator('.version-link').click();await expect(drawer.getByText('只读发布版本',{exact:true})).toBeVisible();
    await expect(drawer.getByLabel('通用 CPU request',{exact:true})).toHaveValue('111m');
    await expect(drawer.getByLabel('冻结资源集合',{exact:true})).toContainText('KubeStateMetrics');await expect(drawer.getByLabel('冻结资源集合',{exact:true})).toContainText('ServiceMonitor');
    await expect(drawer).toContainText('9007199254740994');await drawer.getByRole('button',{name:'关闭实例详情',exact:true}).click();
    await row().getByRole('button',{name:'删除',exact:true}).click();const modal=page.getByRole('dialog',{name:'删除实例',exact:true});
    await expect(modal).toContainText('原部署版本');const accepted=page.waitForResponse(r=>new URL(r.url()).pathname===`/api/v1/instances/${fixture.largeId}/deletions`&&r.request().method()==='POST');
    await modal.getByRole('button',{name:'确认删除实例',exact:true}).click();const op=await(await accepted).json();
    await expect.poll(async()=>{await page.request.post(`/api/v1/operations/${op.operationId}/refresh`,{headers:{Origin:baseURL!,'Idempotency-Key':crypto.randomUUID()},data:{}});return (await(await page.request.get(`/api/v1/operations/${op.operationId}`)).json()).state;},{timeout:90000}).toBe('SUCCEEDED');
  });
  await test.step('I29-B01 历史单资源版本只读，新明确发布才补建并展示独立进度',async()=>{
    const legacy=fixture.historicalPublication;
    const legacyRow=()=>page.getByRole('row').filter({has:page.getByText(legacy.name,{exact:true})});
    await page.getByRole('button',{name:'刷新列表',exact:true}).click();
    await legacyRow().locator('.version-link').click();
    const frozen=drawer.getByLabel('冻结资源集合',{exact:true});
    await expect(frozen).toContainText('冻结资源（1）');await expect(frozen).toContainText('KubeStateMetrics');await expect(frozen).not.toContainText('ServiceMonitor');
    await drawer.getByRole('button',{name:'关闭实例详情',exact:true}).click();
    await legacyRow().getByRole('button',{name:'编辑',exact:true}).click();
    await drawer.getByRole('tab',{name:'运行信息',exact:true}).click();
    const oldProgress=drawer.getByLabel('逐资源执行进度',{exact:true}).first();
    await expect(oldProgress).toContainText('KubeStateMetrics');await expect(oldProgress).not.toContainText('ServiceMonitor');
    await drawer.getByRole('tab',{name:'配置草稿',exact:true}).click();
    await drawer.getByRole('button',{name:'预览下发',exact:true}).click();
    const preview=page.getByRole('dialog',{name:/下发预览/});await expect(preview).toContainText('kind: ServiceMonitor');
    const before=await(await page.request.get(`/api/v1/instances/${legacy.instanceId}`)).json();
    await expect(preview).toContainText(`monitor-mgr.io/user-id: ${before.creatorUserId}`);
    const accepted=page.waitForResponse(r=>new URL(r.url()).pathname===`/api/v1/instances/${legacy.instanceId}/publications`&&r.request().method()==='POST');
    await preview.getByRole('button',{name:'确认下发',exact:true}).click();const op=await(await accepted).json();
    expect(op.versionId).not.toBe(legacy.versionId);
    await expect.poll(async()=> (await(await page.request.get(`/api/v1/operations/${op.operationId}`)).json()).state,{timeout:45000}).toBe('SUCCEEDED');
    await drawer.getByRole('button',{name:'刷新当前状态',exact:true}).click();
    const progress=drawer.getByLabel('逐资源执行进度',{exact:true}).first();await expect(progress).toContainText('KubeStateMetrics');await expect(progress).toContainText('ServiceMonitor');await expect(progress).toContainText('UID：');
    const after=await(await page.request.get(`/api/v1/instances/${legacy.instanceId}`)).json();expect(after.uid).toBe(before.uid);
    const old=await(await page.request.get(`/api/v1/instances/${legacy.instanceId}/versions/${legacy.versionId}`)).json();expect(old.resources).toHaveLength(1);
    await page.screenshot({path:info.outputPath('historical-explicit-companion.png')});
    await drawer.getByRole('button',{name:'关闭实例详情',exact:true}).click();await page.getByRole('button',{name:'刷新列表',exact:true}).click();
    await legacyRow().locator('.version-link').click();await expect(frozen).toContainText('冻结资源（2）');await expect(frozen).toContainText('ServiceMonitor');
    await drawer.getByRole('button',{name:'关闭实例详情',exact:true}).click();
  });
  await test.step('I25-B01 管理员与普通用户创建的 exporter/collector 显示不同管理权限',async()=>{
    const send=async(path:string,data:unknown,status=200)=>{
      const response=await page.request.post(`/api/v1${path}`,{headers:{Origin:baseURL!,'Idempotency-Key':crypto.randomUUID()},data});
      expect(response.status(),path).toBe(status);return response.json();
    };
    const draft=await(await page.request.get(`/api/v1/instances/${fixture.largeId}/draft`)).json();
    const ksm={...draft.ksm,name:'browser-protected-ksm'};
    const rule=()=>({object:{mode:'None'},namespace:{mode:'Own'}});
    const agent={name:'browser-protected-agent',namespace:fixture.agentNamespace,desiredState:'Running',shards:1,resources:{requestCPU:'200m',requestMemory:'512Mi',limitMemory:'1Gi'},storage:{mode:'PVC',capacity:'1Gi',whenScaled:'Retain',whenDeleted:'Retain'},serviceAccountName:'prometheus-agent',associations:{serviceMonitor:rule(),podMonitor:rule(),probe:rule(),scrapeConfig:rule()},remoteWrite:[{url:'http://receiver.example.invalid/write',auth:{mode:'None'}}]};
    const protectedKsm=await send(`/clusters/${fixture.clusterId}/instances`,{component:'kube-state-metrics',ksm},201);
    const protectedAgent=await send(`/clusters/${fixture.clusterId}/instances`,{component:'Prometheus Agent',agent},201);
    expect(protectedKsm.creatorRole).toBe('ADMIN');expect(protectedAgent.creatorRole).toBe('ADMIN');
    const temporary=crypto.randomUUID(),personal=crypto.randomUUID();
    const user=await send('/accounts',{username:'browser-owner',password:temporary,role:'USER'},201);browserOwner={id:user.id,password:personal};
    await send(`/clusters/${fixture.clusterId}/transfer`,{ownerId:user.id});
    await send('/sessions',{username:'browser-owner',password:temporary},201);
    await send('/session/password',{currentPassword:temporary,newPassword:personal});
    await send('/sessions',{username:'browser-owner',password:personal},201);
    await send(`/clusters/${fixture.clusterId}/instances`,{component:'kube-state-metrics',ksm:{...ksm,name:'browser-user-ksm'}},201);
    await send(`/clusters/${fixture.clusterId}/instances`,{component:'Prometheus Agent',agent:{...agent,name:'browser-user-agent'}},201);
    await page.reload();await page.getByRole('button',{name:'观测配置',exact:true}).click();
    await expect(page.getByRole('heading',{name:'观测实例',exact:true})).toBeVisible();
    for(const name of ['browser-protected-ksm','browser-protected-agent']){
      const protectedRow=page.getByRole('row').filter({has:page.getByText(name,{exact:true})});
      await expect(protectedRow.getByRole('button',{name:'下发',exact:true})).toBeDisabled();
      await expect(protectedRow.getByRole('button',{name:'删除',exact:true})).toBeDisabled();
      await protectedRow.getByRole('button',{name:'查看',exact:true}).click();
      await expect(drawer.getByLabel('实例创建身份',{exact:true})).toContainText(protectedKsm.creatorUserId);
      await expect(drawer.getByLabel('实例创建身份',{exact:true})).toContainText('管理员创建的实例对普通用户只读');
      await expect(drawer.getByLabel('名称',{exact:true})).toBeDisabled();
      await expect(drawer.getByRole('button',{name:'保存草稿',exact:true})).toHaveCount(0);
      await drawer.getByRole('tab',{name:'运行信息',exact:true}).click();
      await expect(drawer.getByRole('button',{name:'刷新当前状态',exact:true})).toBeEnabled();
      await drawer.getByRole('button',{name:'关闭实例详情',exact:true}).click();
    }
    for(const name of ['browser-user-ksm','browser-user-agent']){
      const userRow=page.getByRole('row').filter({has:page.getByText(name,{exact:true})});
      await userRow.getByRole('button',{name:'编辑',exact:true}).click();
      await expect(drawer.getByLabel('实例创建身份',{exact:true})).toContainText(user.id);
      await expect(drawer.getByLabel('名称',{exact:true})).toBeEnabled();
      await expect(drawer.getByRole('button',{name:'保存草稿',exact:true})).toBeEnabled();
      await drawer.getByRole('button',{name:'关闭实例详情',exact:true}).click();
    }
  });
  await test.step('I30-B01 快捷关联固定创建用户，namespace和自定义规则保留，管理员编辑不改目标',async()=>{
    const open=async()=>{await page.reload();await page.getByRole('button',{name:'观测配置',exact:true}).click();await page.getByRole('row').filter({has:page.getByText('browser-user-agent',{exact:true})}).getByRole('button',{name:'编辑',exact:true}).click();};
    await open();
    const quick=drawer.getByLabel('按用户关联 exporter',{exact:true});
    await expect(drawer.getByLabel('ServiceMonitor 对象模式',{exact:true})).toHaveValue('None');
    await expect(drawer.getByLabel('ServiceMonitor namespace模式',{exact:true})).toHaveValue('Own');
    await expect(quick).toContainText('未按 user 标签关联');await expect(quick).toContainText(`快捷默认用户：${browserOwner.id}`);
    await quick.getByRole('button',{name:'按创建用户关联 exporter',exact:true}).click();
    await expect(drawer.getByLabel('ServiceMonitor 对象 label 1 key',{exact:true})).toHaveValue('monitor-mgr.io/user-id');
    await expect(drawer.getByLabel('ServiceMonitor 对象 label 1 value',{exact:true})).toHaveValue(browserOwner.id);
    await expect(drawer.getByLabel('ServiceMonitor namespace模式',{exact:true})).toHaveValue('Own');
    await drawer.getByLabel('ServiceMonitor namespace模式',{exact:true}).selectOption('All');
    await drawer.getByRole('button',{name:'添加 ServiceMonitor 对象 expression',exact:true}).click();
    await drawer.getByLabel('ServiceMonitor 对象 expression 1 key',{exact:true}).fill('disabled');
    await drawer.getByLabel('ServiceMonitor 对象 expression 1 operator',{exact:true}).selectOption('DoesNotExist');
    await drawer.getByRole('button',{name:'保存草稿',exact:true}).click();await expect(drawer.getByText('草稿 v2 已保存',{exact:true})).toBeVisible();
    await drawer.getByRole('button',{name:'刷新采集关联预览',exact:true}).click();
    await expect(drawer.getByText('草稿修订 2 的匹配预览',{exact:true})).toBeVisible();
    await expect(drawer.getByLabel('来源 association:ServiceMonitor',{exact:true})).toContainText('已读取');
    await drawer.getByRole('button',{name:'预览下发',exact:true}).click();
    const preview=page.getByRole('dialog',{name:/下发预览/});await expect(preview).toContainText('serviceMonitorSelector:');await expect(preview).toContainText(browserOwner.id);await preview.getByRole('button',{name:'返回编辑',exact:true}).click();
    await drawer.getByRole('button',{name:'关闭实例详情',exact:true}).click();
    expect((await page.request.post('/api/v1/sessions',{headers:{Origin:baseURL!,'Idempotency-Key':crypto.randomUUID()},data:{username:'admin',password:fixture.password}})).status()).toBe(201);
    await open();await expect(quick).toContainText(`当前规则目标用户：${browserOwner.id}`);
    await drawer.getByLabel('Agent CPU request',{exact:true}).fill('210m');
    await drawer.getByRole('button',{name:'保存草稿',exact:true}).click();await expect(drawer.getByText('草稿 v3 已保存',{exact:true})).toBeVisible();
    await drawer.getByRole('button',{name:'关闭实例详情',exact:true}).click();await open();
    await expect(quick).toContainText(`当前规则目标用户：${browserOwner.id}`);
    await expect(drawer.getByLabel('ServiceMonitor namespace模式',{exact:true})).toHaveValue('All');
    await expect(drawer.getByLabel('ServiceMonitor 对象 expression 1 operator',{exact:true})).toHaveValue('DoesNotExist');
    await page.screenshot({path:info.outputPath('agent-creator-association.png')});await drawer.getByRole('button',{name:'关闭实例详情',exact:true}).click();
  });
  await test.step('I30-B02 历史可靠userId与未知创建用户分别处理，快捷配置不代替权限',async()=>{
    const known=fixture.historical.find((i:{component:string})=>i.component==='Prometheus Agent');
    for(const [instance,unknown] of [[known,false],[fixture.unknownCreatorAgent,true]] as const){
      await page.reload();await page.getByRole('button',{name:'观测配置',exact:true}).click();
      await page.getByRole('row').filter({has:page.getByText(instance.name,{exact:true})}).getByRole('button',{name:'编辑',exact:true}).click();
      await expect(drawer.getByLabel('实例创建身份',{exact:true})).toContainText('创建时角色：未知');
      const quick=drawer.getByLabel('按用户关联 exporter',{exact:true});
      if(unknown){
        await expect(quick).toContainText('实例创建用户未知');await expect(quick.getByRole('button',{name:'按创建用户关联 exporter',exact:true})).toBeDisabled();
        await drawer.getByLabel('ServiceMonitor 对象模式',{exact:true}).selectOption('Labels');
        await drawer.getByRole('button',{name:'添加 ServiceMonitor 对象 label',exact:true}).click();
        await drawer.getByLabel('ServiceMonitor 对象 label 1 key',{exact:true}).fill('team');await drawer.getByLabel('ServiceMonitor 对象 label 1 value',{exact:true}).fill('custom');
        await drawer.getByRole('button',{name:'保存草稿',exact:true}).click();await expect(drawer.getByText('草稿 v2 已保存',{exact:true})).toBeVisible();
      }else{
        const detail=await(await page.request.get(`/api/v1/instances/${instance.id}`)).json();expect(detail.creatorUserId).toBeTruthy();expect(detail.creatorIdentityKnown).toBe(false);
        await expect(quick.getByRole('button',{name:'按创建用户关联 exporter',exact:true})).toBeEnabled();
        await quick.getByRole('button',{name:'按创建用户关联 exporter',exact:true}).click();await expect(drawer.getByLabel('ServiceMonitor 对象 label 1 value',{exact:true})).toHaveValue(detail.creatorUserId);
      }
      await drawer.getByRole('button',{name:'关闭实例详情',exact:true}).click();
    }
  });
  await test.step('I26-B01 历史 KSM/Agent 由管理员明确确认，普通用户权限随允许和撤销变化',async()=>{
    const send=async(path:string,data:unknown,status=200)=>{
      const response=await page.request.post(`/api/v1${path}`,{headers:{Origin:baseURL!,'Idempotency-Key':crypto.randomUUID()},data});
      expect(response.status(),path).toBe(status);return response.json();
    };
    const loginAdmin=async()=>{await send('/sessions',{username:'admin',password:fixture.password},201);};
    await loginAdmin();
    const actor=(await(await page.request.get('/api/v1/session')).json()).account.id;
    const temporary=crypto.randomUUID(),personal=crypto.randomUUID();
    const user=await send('/accounts',{username:'historical-browser-owner',password:temporary,role:'USER'},201);
    await send(`/clusters/${fixture.clusterId}/transfer`,{ownerId:user.id});
    await send('/sessions',{username:'historical-browser-owner',password:temporary},201);
    await send('/session/password',{currentPassword:temporary,newPassword:personal});
    const loginUser=async()=>{await send('/sessions',{username:'historical-browser-owner',password:personal},201);};
    const open=async(instance:{name:string},manage:boolean)=>{
      await page.reload();await page.getByRole('button',{name:'观测配置',exact:true}).click();
      const row=page.getByRole('row').filter({has:page.getByText(instance.name,{exact:true})});
      await row.getByRole('button',{name:manage?'编辑':'查看',exact:true}).click();
      await expect(drawer.getByLabel('实例创建身份',{exact:true})).toContainText('创建时角色：未知');
    };
    const checkUser=async(manage:boolean)=>{
      await loginUser();
      for(const instance of fixture.historical){
        await open(instance,manage);
        await expect(drawer.getByRole('button',{name:'确认历史实例管理权限',exact:true})).toHaveCount(0);
        if(manage){
          await expect(drawer.getByLabel('名称',{exact:true})).toBeEnabled();
          await expect(drawer.getByLabel('实例创建身份',{exact:true})).toContainText('允许现有集群管理用户');
          await drawer.getByRole('button',{name:'保存草稿',exact:true}).click();
          await expect(drawer.getByText('草稿 v2 已保存',{exact:true})).toBeVisible();
        }else{
          await expect(drawer.getByLabel('名称',{exact:true})).toBeDisabled();
          await expect(drawer.getByRole('button',{name:'保存草稿',exact:true})).toHaveCount(0);
          await expect(drawer.getByLabel('实例创建身份',{exact:true})).toContainText('创建时角色未知');
        }
        await drawer.getByRole('button',{name:'关闭实例详情',exact:true}).click();
      }
    };
    await checkUser(false);
    for(const result of ['allow','restrict']){
      await loginAdmin();
      for(const instance of fixture.historical){
        await open(instance,true);
        await drawer.getByRole('button',{name:'确认历史实例管理权限',exact:true}).click();
        const confirm=page.getByRole('dialog',{name:'确认历史实例管理权限',exact:true});
        await expect(confirm.getByRole('button',{name:'确认管理权限',exact:true})).toBeDisabled();
        await confirm.getByLabel('管理权限结果',{exact:true}).selectOption(result);
        await confirm.getByRole('button',{name:'确认管理权限',exact:true}).click();
        await expect(confirm).toHaveCount(0);
        await expect(drawer.getByLabel('实例创建身份',{exact:true})).toContainText(`确认用户：${actor}`);
        await expect(drawer.getByLabel('实例创建身份',{exact:true})).toContainText('确认时间：');
        const detail=await(await page.request.get(`/api/v1/instances/${instance.id}`)).json();
        expect(detail.creatorRole).toBe('');expect(detail.managementConfirmation.allowClusterManagers).toBe(result==='allow');
        await drawer.getByRole('button',{name:'关闭实例详情',exact:true}).click();
      }
      await checkUser(result==='allow');
    }
    await loginAdmin();await open(fixture.historical[0],true);
    await drawer.getByRole('button',{name:'关闭实例详情',exact:true}).click();
    await send(`/clusters/${fixture.clusterId}/unregister`,{});
    await page.getByRole('button',{name:'刷新列表',exact:true}).click();
    // 不重载整个工作台，验证已打开页面刷新后即遵守注销状态，同时继续展示原确认记录。
    for(const instance of fixture.historical){
      const row=page.getByRole('row').filter({has:page.getByText(instance.name,{exact:true})});
      await row.getByRole('button',{name:'查看',exact:true}).click();
      await expect(drawer.getByRole('button',{name:'确认历史实例管理权限',exact:true})).toHaveCount(0);
      await expect(drawer.getByLabel('实例创建身份',{exact:true})).toContainText('集群已注销');
      await expect(drawer.getByLabel('实例创建身份',{exact:true})).toContainText(`确认用户：${actor}`);
      await expect(drawer.getByLabel('名称',{exact:true})).toBeDisabled();
      await drawer.getByRole('button',{name:'关闭实例详情',exact:true}).click();
    }
  });
  expect(errors).toEqual([]);await page.screenshot({path:info.outputPath('main-flow.png')});
});
