import {test, expect} from '@playwright/test';
import {readFileSync} from 'node:fs';
import {instanceCreatorProtection} from './issue25';
import {historicalExporterMigration} from './issue29';
import {agentCreatorAssociation} from './issue30';

// 唯一浏览器场景只验证关键交互与精确对象身份；字段矩阵归后端 UT。
test('E2E-10 登录、草稿、精确修订、发布、版本和删除', async ({page, baseURL}, info) => {
  test.setTimeout(300000);
  const path=process.env.PHASE1_E2E_FIXTURE;
  expect(path,'必须提供真实平台 Pod 的私有夹具').toBeTruthy();
  const fixture=JSON.parse(readFileSync(path!, 'utf8'));
  const errors:string[]=[];page.on('pageerror',error=>errors.push(error.message));
  await test.step('登录并用表单创建 KSM 草稿',async()=>{
    await page.goto('/');await page.getByLabel('账号',{exact:true}).fill('admin');await page.getByLabel('密码',{exact:true}).fill(fixture.password);
    await page.getByRole('button',{name:'登录',exact:true}).click();await expect(page.getByRole('heading',{name:'账号管理',exact:true})).toBeVisible();
    await page.getByRole('button',{name:'观测配置',exact:true}).click();await expect(page.getByRole('heading',{name:'观测实例',exact:true})).toBeVisible();
    await page.getByRole('button',{name:'添加',exact:true}).click();await page.getByRole('button',{name:'配置实例',exact:true}).click();
    const drawer=page.getByRole('dialog',{name:'实例详情',exact:true});await drawer.getByLabel('名称',{exact:true}).fill('browser-draft');
    await drawer.getByLabel('部署命名空间',{exact:true}).selectOption(fixture.namespace);
    await expect(drawer.getByLabel('启用配套采集',{exact:true})).toBeChecked();
    await drawer.getByLabel('采集间隔',{exact:true}).fill('40s');await drawer.getByLabel('采集超时',{exact:true}).fill('8s');
    await drawer.getByRole('button',{name:'保存草稿',exact:true}).click();await expect(drawer.getByText('草稿 v1 已保存',{exact:true})).toBeVisible();
    await drawer.getByRole('button',{name:'关闭实例详情',exact:true}).click();
  });
  const row=()=>page.getByRole('row').filter({hasText:fixture.largeName});
  await row().getByRole('button',{name:'编辑',exact:true}).click();
  const drawer=page.getByRole('dialog',{name:'实例详情',exact:true});
  await test.step('大 revision 在保存和 YAML 预览中保持精确',async()=>{
    await expect(drawer.getByText('草稿 v9007199254740993 已保存',{exact:true})).toBeVisible();
    await drawer.getByLabel('通用 CPU request',{exact:true}).fill('111m');
    // 关键：真实v10升级保持缺省关闭，必须由用户明确选择后才编辑并发布采集意图。
    await expect(drawer.getByLabel('启用配套采集',{exact:true})).not.toBeChecked();
    await drawer.getByLabel('启用配套采集',{exact:true}).check();
    await drawer.getByLabel('采集间隔',{exact:true}).fill('45s');await drawer.getByLabel('采集超时',{exact:true}).fill('12s');
    const saving=page.waitForRequest(r=>new URL(r.url()).pathname===`/api/v1/instances/${fixture.largeId}/draft`&&r.method()==='PUT');
    await drawer.getByRole('button',{name:'保存草稿',exact:true}).click();expect((await saving).postDataJSON().expectedDraftRevision).toBe('9007199254740993');
    await expect(drawer.getByText('草稿 v9007199254740994 已保存',{exact:true})).toBeVisible();
    await drawer.getByRole('tab',{name:'只读 YAML',exact:true}).click();await expect(drawer).toContainText('111m');
    await drawer.getByRole('tab',{name:'配置草稿',exact:true}).click();
  });
  await test.step('查看 diff，明确确认后才发布到真实集群',async()=>{
    await drawer.getByRole('button',{name:'预览下发',exact:true}).click();await expect(page.getByRole('heading',{name:'CR YAML 对比',exact:true})).toBeVisible();
    const before=await (await page.request.get(`/api/v1/instances/${fixture.largeId}`)).json();expect(before.uid).toBe('');
    const accepted=page.waitForResponse(r=>new URL(r.url()).pathname===`/api/v1/instances/${fixture.largeId}/publications`&&r.request().method()==='POST');
    await page.getByRole('button',{name:'确认下发',exact:true}).click();const op=await(await accepted).json();
    expect(op.instanceId).toBe(fixture.largeId);await expect.poll(async()=> (await(await page.request.get(`/api/v1/operations/${op.operationId}`)).json()).state,{timeout:45000}).toBe('SUCCEEDED');
    await drawer.getByRole('button',{name:'刷新当前状态',exact:true}).click();
    await expect(drawer.getByLabel('来源 CR',{exact:true})).toContainText('最后成功观察');
    await expect.poll(async()=>{await page.request.post(`/api/v1/instances/${fixture.largeId}/refresh`,{headers:{Origin:baseURL!,'Idempotency-Key':crypto.randomUUID()},data:{}});const current=await(await page.request.get(`/api/v1/instances/${fixture.largeId}`)).json();return current.sources?.find((source:any)=>source.source==='ServiceMonitor'&&!source.historical)?.companion?.state;},{timeout:60000}).toBe('Ready');
    await drawer.getByRole('button',{name:'刷新当前状态',exact:true}).click();
    const companion=drawer.getByLabel('来源 ServiceMonitor',{exact:true});await expect(companion).toContainText('配套收敛：已收敛');await expect(companion).toContainText('平台仅只读观察');

  });
  await test.step('版本入口回读冻结来源，明确删除原实例',async()=>{
    await drawer.getByRole('button',{name:'关闭实例详情',exact:true}).click();await page.getByRole('button',{name:'刷新列表',exact:true}).click();
    await row().locator('.version-link').click();await expect(drawer.getByText('只读发布版本',{exact:true})).toBeVisible();
    await expect(drawer.getByLabel('通用 CPU request',{exact:true})).toHaveValue('111m');
    await expect(drawer.getByLabel('启用配套采集',{exact:true})).toBeChecked();await expect(drawer.getByLabel('采集间隔',{exact:true})).toHaveValue('45s');await expect(drawer.getByLabel('采集超时',{exact:true})).toHaveValue('12s');
    await expect(drawer).toContainText('9007199254740994');await drawer.getByRole('button',{name:'关闭实例详情',exact:true}).click();
    await row().getByRole('button',{name:'删除',exact:true}).click();const modal=page.getByRole('dialog',{name:'删除实例',exact:true});
    await expect(modal).toContainText('原部署版本');const accepted=page.waitForResponse(r=>new URL(r.url()).pathname===`/api/v1/instances/${fixture.largeId}/deletions`&&r.request().method()==='POST');
    await modal.getByRole('button',{name:'确认删除实例',exact:true}).click();const op=await(await accepted).json();
    await expect.poll(async()=>{await page.request.post(`/api/v1/operations/${op.operationId}/refresh`,{headers:{Origin:baseURL!,'Idempotency-Key':crypto.randomUUID()},data:{}});return (await(await page.request.get(`/api/v1/operations/${op.operationId}`)).json()).state;},{timeout:90000}).toBe('SUCCEEDED');
  });
  await instanceCreatorProtection(page,baseURL,fixture,info);
  await test.step('I29-B01 历史采集迁移独立确认与新父 CR 发布',()=>historicalExporterMigration(page,baseURL,fixture,info));
  await agentCreatorAssociation(page,baseURL,fixture,info);
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
          const detail=await(await page.request.get(`/api/v1/instances/${instance.id}`)).json();
          const before=await(await page.request.get(`/api/v1/instances/${instance.id}/draft`)).json();
          const component=instance.component==='Prometheus Agent'?'agent':'ksm';
          // 关键：管理权限允许修改配置；I29 已发布的 KSM 身份仍固定，不能以名称可改代替授权判据。
          for(const label of ['名称',component==='agent'?'Agent namespace':'部署命名空间']){
            const identity=drawer.getByLabel(label,{exact:true});
            if(detail.uid)await expect(identity).toBeDisabled();else await expect(identity).toBeEnabled();
          }
          await expect(drawer.getByLabel('实例创建身份',{exact:true})).toContainText('允许现有集群管理用户');
          const cpu=drawer.getByLabel(component==='agent'?'Agent CPU request':'通用 CPU request',{exact:true});
          await expect(cpu).toBeEnabled();
          const nextCPU=before[component].resources.requestCPU==='123m'?'124m':'123m';
          await cpu.fill(nextCPU);
          const saving=page.waitForResponse(r=>new URL(r.url()).pathname===`/api/v1/instances/${instance.id}/draft`&&r.request().method()==='PUT');
          await drawer.getByRole('button',{name:'保存草稿',exact:true}).click();
          const response=await saving;expect(response.status()).toBe(200);
          expect(response.request().postDataJSON().expectedDraftRevision).toBe(before.revision);
          const nextRevision=(BigInt(before.revision)+1n).toString();
          expect(String((await response.json()).revision)).toBe(nextRevision);
          await expect(drawer.getByText(`草稿 v${nextRevision} 已保存`,{exact:true})).toBeVisible();
          const after=await(await page.request.get(`/api/v1/instances/${instance.id}/draft`)).json();
          expect(String(after.revision)).toBe(nextRevision);expect(after[component].resources.requestCPU).toBe(nextCPU);
          expect(after[component].name).toBe(before[component].name);
          expect(after[component][component==='agent'?'namespace':'targetNamespace']).toBe(before[component][component==='agent'?'namespace':'targetNamespace']);
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
