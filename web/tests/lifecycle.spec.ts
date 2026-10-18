import {test,expect} from '@playwright/test';

// P07实际平台Pod前置由独占K runner建立；浏览器沿页面触发原版本重试并验证运行导航。
test('P07.primary 页面重试固定版本并进入运行信息',async({page},info)=>{
 test.setTimeout(60000);
 test.skip(!process.env.LIFECYCLE_UI_OPERATION,'需要独占真实平台Pod与确定失败操作');
 const operationId=process.env.LIFECYCLE_UI_OPERATION!,instanceId=process.env.LIFECYCLE_UI_INSTANCE!,versionId=process.env.LIFECYCLE_UI_VERSION!,name=process.env.LIFECYCLE_UI_NAME!;
 await page.goto('/');await page.getByLabel('账号',{exact:true}).fill('admin');await page.getByLabel('密码',{exact:true}).fill(process.env.ADMIN_PASSWORD!);await page.getByRole('button',{name:'登录',exact:true}).click();
 await page.getByRole('button',{name:'观测配置',exact:true}).click();
 const row=page.getByRole('row').filter({has:page.getByText(name,{exact:true})});await row.getByRole('button',{name:'编辑',exact:true}).click();
 const drawer=page.getByRole('dialog',{name:'实例详情'});await drawer.getByRole('button',{name:'变更历史',exact:true}).click();
 const response=page.waitForResponse(r=>r.request().method()==='POST'&&r.url().endsWith(`/operations/${operationId}/retry`));
 await drawer.getByRole('button',{name:'重试原版本',exact:true}).click();const accepted=await response;expect(accepted.status()).toBe(202);const retried=await accepted.json();expect(retried.operationId).toBe(operationId);expect(retried.versionId).toBe(versionId);
 await expect(drawer.getByRole('button',{name:'运行信息',exact:true})).toHaveClass(/active/);
 await expect.poll(async()=>(await (await page.request.get(`/api/v1/operations/${operationId}`)).json()).state).toBe('SUCCEEDED');
 await expect(drawer.getByText('执行尝试（2）',{exact:true})).toBeVisible();await drawer.getByText('执行尝试（2）',{exact:true}).click();
 await expect(drawer.getByText(/尝试 1 · 失败/)).toBeVisible();await expect(drawer.getByText(/尝试 2 · 已完成/)).toBeVisible();
 const draft=await (await page.request.get(`/api/v1/instances/${instanceId}/draft`)).json();expect(draft.ksm.resources.requestCPU).toBe('900m');
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBe(true);
 await page.screenshot({path:info.outputPath('lifecycle-original-version-retry.png')});
 info.annotations.push({type:'browserVersion',description:page.context().browser()?.version()??'unknown'});
});
