import {expect, test, type Page, type TestInfo} from '@playwright/test';

// 只验证用户能看到的创建身份和管理控件；API 与 RPC 完整权限矩阵由 Ginkgo UT 验证。
export async function instanceCreatorProtection(page:Page, baseURL:string|undefined, fixture:{password:string;clusterId:string;namespace:string;largeId:string}, info:TestInfo) {
  await test.step('I25-B01 管理员实例只读，普通用户实例仍可管理',async()=>{
    const send=async(path:string,data:unknown,status=200)=>{
      const response=await page.request.post('/api/v1'+path,{headers:{Origin:baseURL!,'Idempotency-Key':crypto.randomUUID()},data});
      expect(response.status(),path).toBe(status);return response.json();
    };
    const admin=(await(await page.request.get('/api/v1/session')).json()).account;
    const draft=await(await page.request.get('/api/v1/instances/'+fixture.largeId+'/draft')).json();
    const ksm={...draft.ksm,name:'browser-protected-ksm'};
    const rule=()=>({object:{mode:'None'},namespace:{mode:'Own'}});
    const agent={name:'browser-protected-agent',namespace:fixture.namespace,desiredState:'Running',shards:1,
      resources:{requestCPU:'200m',requestMemory:'512Mi',limitMemory:'1Gi'},
      storage:{mode:'PVC',capacity:'1Gi',whenScaled:'Retain',whenDeleted:'Retain'},serviceAccountName:'prometheus-agent',
      associations:{serviceMonitor:rule(),podMonitor:rule(),probe:rule(),scrapeConfig:rule()},
      remoteWrite:[{url:'http://receiver.example.invalid/write',auth:{mode:'None'}}]};
    const protectedKsm=await send('/clusters/'+fixture.clusterId+'/instances',{component:'kube-state-metrics',ksm},201);
    const protectedAgent=await send('/clusters/'+fixture.clusterId+'/instances',{component:'Prometheus Agent',agent},201);
    expect(protectedKsm.creatorRole).toBe('ADMIN');expect(protectedAgent.creatorRole).toBe('ADMIN');
    const username='browser-owner-'+crypto.randomUUID().slice(0,8),temporary=crypto.randomUUID(),personal=crypto.randomUUID();
    const user=await send('/accounts',{username,password:temporary,role:'USER'},201);
    await send('/clusters/'+fixture.clusterId+'/transfer',{ownerId:user.id});
    try{
      await send('/sessions',{username,password:temporary},201);
      await send('/session/password',{currentPassword:temporary,newPassword:personal});
      await send('/sessions',{username,password:personal},201);
      await send('/clusters/'+fixture.clusterId+'/instances',{component:'kube-state-metrics',ksm:{...ksm,name:'browser-user-ksm'}},201);
      await send('/clusters/'+fixture.clusterId+'/instances',{component:'Prometheus Agent',agent:{...agent,name:'browser-user-agent'}},201);
      await page.reload();await page.getByRole('button',{name:'观测配置',exact:true}).click();
      await expect(page.getByRole('heading',{name:'观测实例',exact:true})).toBeVisible();
      const drawer=page.getByRole('dialog',{name:'实例详情',exact:true});
      for(const name of ['browser-protected-ksm','browser-protected-agent']){
        const row=page.getByRole('row').filter({has:page.getByText(name,{exact:true})});
        await expect(row.getByRole('button',{name:'下发',exact:true})).toBeDisabled();
        await expect(row.getByRole('button',{name:'删除',exact:true})).toBeDisabled();
        await row.getByRole('button',{name:'查看',exact:true}).click();
        await expect(drawer.getByLabel('实例创建身份',{exact:true})).toContainText(protectedKsm.creatorUserId);
        await expect(drawer.getByLabel('实例创建身份',{exact:true})).toContainText('管理员创建的实例对普通用户只读');
        await expect(drawer.getByLabel('名称',{exact:true})).toBeDisabled();
        await expect(drawer.getByRole('button',{name:'保存草稿',exact:true})).toHaveCount(0);
        await drawer.getByRole('tab',{name:'运行信息',exact:true}).click();
        await expect(drawer.getByRole('button',{name:'刷新当前状态',exact:true})).toBeEnabled();
        await drawer.getByRole('button',{name:'关闭实例详情',exact:true}).click();
      }
      for(const name of ['browser-user-ksm','browser-user-agent']){
        const row=page.getByRole('row').filter({has:page.getByText(name,{exact:true})});
        await row.getByRole('button',{name:'编辑',exact:true}).click();
        await expect(drawer.getByLabel('实例创建身份',{exact:true})).toContainText(user.id);
        await expect(drawer.getByLabel('名称',{exact:true})).toBeEnabled();
        await expect(drawer.getByRole('button',{name:'保存草稿',exact:true})).toBeEnabled();
        await drawer.getByRole('button',{name:'关闭实例详情',exact:true}).click();
      }
      await page.screenshot({path:info.outputPath('instance-creator-protection.png')});
    }finally{
      // 恢复本例原管理员与集群归属，后续历史确认/关联场景不能借用上一段用户会话。
      await send('/sessions',{username:'admin',password:fixture.password},201);
      await send('/clusters/'+fixture.clusterId+'/transfer',{ownerId:admin.id});
      await page.reload();await page.getByRole('button',{name:'观测配置',exact:true}).click();
    }
  });
}
