import {expect,type Page,type TestInfo} from '@playwright/test';
import {writeFileSync} from 'node:fs';

// 只验证管理员看见旧事实、外部处理后的明确确认与新的单父发布；外部删除是独占测试管理员接缝。
export async function historicalExporterMigration(page:Page,baseURL:string|undefined,fixture:any,info:TestInfo){
 const data=fixture.issue29;expect(data,'必须提供独立旧 SM 迁移夹具').toBeTruthy();
 const row=()=>page.getByRole('row').filter({has:page.getByText(data.instance.name,{exact:true})});
 await page.getByRole('button',{name:'刷新列表',exact:true}).click();await row().getByRole('button',{name:'编辑',exact:true}).click();
 const drawer=page.getByRole('dialog',{name:'实例详情',exact:true});await drawer.getByRole('tab',{name:'变更历史',exact:true}).click();
 const panel=drawer.getByLabel('历史采集迁移',{exact:true});await panel.getByRole('button',{name:'盘点历史采集迁移',exact:true}).click();
 await expect(panel).toContainText('Agent 选择范围已完整读取');await expect(panel).toContainText('未知（不能凭同名补认）');await expect(panel).toContainText(data.oldUid);
 const acknowledge=panel.getByRole('checkbox');await acknowledge.check();
 await expect(panel.getByRole('button',{name:'确认旧 ServiceMonitor 已外部处理',exact:true})).toBeDisabled();
 const download=page.waitForEvent('download');await panel.getByRole('button',{name:'保存迁移快照',exact:true}).click();await(await download).saveAs(info.outputPath('historical-migration-snapshot.json'));
 const processed=await page.request.post(data.adminURL,{headers:{Authorization:'Bearer '+data.adminToken}});expect(processed.status()).toBe(200);
 await panel.getByRole('button',{name:'盘点历史采集迁移',exact:true}).click();await expect(panel).toContainText('原冻结路径当前不存在');await acknowledge.check();
 await panel.getByRole('button',{name:'确认旧 ServiceMonitor 已外部处理',exact:true}).click();await expect(panel).toContainText('旧 SM 已处理确认');
 await acknowledge.check();await panel.getByRole('button',{name:'独立核验父 CR 并解除原任务互斥',exact:true}).click();
 await expect(panel.getByRole('button',{name:'独立核验父 CR 并解除原任务互斥',exact:true})).toHaveCount(0);
 await drawer.getByRole('tab',{name:'配置草稿',exact:true}).click();await drawer.getByLabel('启用配套采集',{exact:true}).check();await drawer.getByRole('button',{name:'保存草稿',exact:true}).click();
 await expect(drawer.getByText(/草稿 v2 已保存/)).toBeVisible();await drawer.getByRole('button',{name:'预览下发',exact:true}).click();
 const preview=page.getByRole('dialog',{name:/下发预览/});await expect(preview).toContainText('serviceMonitor:');await expect(preview).not.toContainText('kind: ServiceMonitor');
 const accepted=page.waitForResponse(r=>new URL(r.url()).pathname===`/api/v1/instances/${data.instance.id}/publications`&&r.request().method()==='POST');await preview.getByRole('button',{name:'确认下发',exact:true}).click();const operation=await(await accepted).json();
 await expect.poll(async()=> (await(await page.request.get(`/api/v1/operations/${operation.operationId}`)).json()).state,{timeout:60000}).toBe('SUCCEEDED');
 const old=await(await page.request.get(`/api/v1/operations/${data.operationId}`)).json();expect(old.resources.find((r:any)=>r.target.key==='legacy-sm').state).toBe('IDENTITY_UNCERTAIN');expect(old.resources.find((r:any)=>r.target.key==='legacy-sm').confirmedUid).toBe('');
 writeFileSync(data.resultPath,JSON.stringify({instanceId:data.instance.id,versionId:operation.versionId,operationId:operation.operationId,inventory:await(await page.request.get(`/api/v1/instances/${data.instance.id}/historical-migration`)).json()}),{mode:0o600});
 await page.screenshot({path:info.outputPath('historical-explicit-migration.png')});await drawer.getByRole('button',{name:'关闭实例详情',exact:true}).click();
}
