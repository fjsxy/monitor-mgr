import {test, expect} from '@playwright/test';
import {readFileSync} from 'node:fs';

// 唯一浏览器场景只验证关键交互与精确对象身份；字段矩阵归后端 UT。
test('E2E-10 登录、草稿、精确修订、发布、版本和删除', async ({page, baseURL}, info) => {
  test.setTimeout(180000);
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
    await drawer.getByRole('button',{name:'保存草稿',exact:true}).click();await expect(drawer.getByText('草稿 v1 已保存',{exact:true})).toBeVisible();
    await drawer.getByRole('button',{name:'关闭实例详情',exact:true}).click();
  });
  const row=()=>page.getByRole('row').filter({hasText:fixture.largeName});
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
    const accepted=page.waitForResponse(r=>new URL(r.url()).pathname===`/api/v1/instances/${fixture.largeId}/publications`&&r.request().method()==='POST');
    await page.getByRole('button',{name:'确认下发',exact:true}).click();const op=await(await accepted).json();
    expect(op.instanceId).toBe(fixture.largeId);await expect.poll(async()=> (await(await page.request.get(`/api/v1/operations/${op.operationId}`)).json()).state,{timeout:45000}).toBe('SUCCEEDED');
    await drawer.getByRole('button',{name:'刷新当前状态',exact:true}).click();
    await expect(drawer.getByLabel('来源 CR',{exact:true})).toContainText('最后成功观察');
    const progress=drawer.getByLabel('逐资源执行进度',{exact:true}).first();await expect(progress).toBeVisible();await expect(progress).toContainText('KubeStateMetrics');await expect(progress).toContainText('UID：');
    await progress.getByText('查看冻结配置',{exact:true}).click();await expect(progress).toContainText('111m');await page.screenshot({path:info.outputPath('resource-progress.png')});
  });
  await test.step('版本入口回读冻结来源，明确删除原实例',async()=>{
    await drawer.getByRole('button',{name:'关闭实例详情',exact:true}).click();await page.getByRole('button',{name:'刷新列表',exact:true}).click();
    await row().locator('.version-link').click();await expect(drawer.getByText('只读发布版本',{exact:true})).toBeVisible();
    await expect(drawer.getByLabel('通用 CPU request',{exact:true})).toHaveValue('111m');
    await expect(drawer.getByLabel('冻结资源集合',{exact:true})).toContainText('KubeStateMetrics');
    await expect(drawer).toContainText('9007199254740994');await drawer.getByRole('button',{name:'关闭实例详情',exact:true}).click();
    await row().getByRole('button',{name:'删除',exact:true}).click();const modal=page.getByRole('dialog',{name:'删除实例',exact:true});
    await expect(modal).toContainText('原部署版本');const accepted=page.waitForResponse(r=>new URL(r.url()).pathname===`/api/v1/instances/${fixture.largeId}/deletions`&&r.request().method()==='POST');
    await modal.getByRole('button',{name:'确认删除实例',exact:true}).click();const op=await(await accepted).json();
    await expect.poll(async()=>{await page.request.post(`/api/v1/operations/${op.operationId}/refresh`,{headers:{Origin:baseURL!,'Idempotency-Key':crypto.randomUUID()},data:{}});return (await(await page.request.get(`/api/v1/operations/${op.operationId}`)).json()).state;},{timeout:90000}).toBe('SUCCEEDED');
  });
  expect(errors).toEqual([]);await page.screenshot({path:info.outputPath('main-flow.png')});
});
