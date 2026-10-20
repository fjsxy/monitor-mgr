import {test,expect} from '@playwright/test';
const adminPassword=process.env.ADMIN_PASSWORD??'first-password-123';
// Each I07 case establishes its own administrator precondition; no dependency on I01.
test.beforeEach(async({page,baseURL},info)=>{
 if(!info.title.startsWith('I07.'))return;
 const state=await (await page.request.get('/api/v1/initialization')).json();
 if(!state.initialized){const response=await page.request.post('/api/v1/initialization',{headers:{Origin:new URL(baseURL!).origin},data:{username:'admin',password:adminPassword}});expect(response.status()).toBe(201)}
});
test('I01.primary 初始化与临时密码流程',async({page})=>{
 await page.goto('/');
 await expect(page.getByRole('heading',{name:'初始化管理员'})).toBeVisible();
 await page.getByLabel('账号',{exact:true}).fill('admin');await page.getByLabel('密码',{exact:true}).fill(adminPassword);await page.getByRole('button',{name:'初始化管理员',exact:true}).click();
 await expect(page.getByRole('heading',{name:'登录'})).toBeVisible();
 await page.getByLabel('账号',{exact:true}).fill('admin');await page.getByLabel('密码',{exact:true}).fill(adminPassword);await page.getByRole('button',{name:'登录',exact:true}).click();
 await expect(page.getByRole('heading',{name:'账号管理'})).toBeVisible();
 await page.getByRole('button',{name:'创建账号',exact:true}).click();await page.getByLabel('新账号').fill('browser-user');await page.getByLabel('临时密码').fill('temporary-password');await page.getByRole('button',{name:'确认创建'}).click();await expect(page.getByRole('cell',{name:'browser-user',exact:true})).toBeVisible();
 await page.getByRole('button',{name:'退出登录'}).click();await page.getByLabel('账号',{exact:true}).fill('browser-user');await page.getByLabel('密码',{exact:true}).fill('temporary-password');await page.getByRole('button',{name:'登录',exact:true}).click();
 await expect(page.getByRole('heading',{name:'修改临时密码'})).toBeVisible();await page.getByLabel('原密码').fill('temporary-password');await page.getByLabel('新密码').fill('personal-password');await page.getByRole('button',{name:'确认改密'}).click();
 await expect(page.getByRole('heading',{name:'登录',exact:true})).toBeVisible();await page.getByLabel('账号',{exact:true}).fill('browser-user');await page.getByLabel('密码',{exact:true}).fill('personal-password');await page.getByRole('button',{name:'登录',exact:true}).click();
 await expect(page.getByText('账号：browser-user')).toBeVisible();await expect(page.getByRole('button',{name:'创建账号',exact:true})).toHaveCount(0);
});

async function login(page:import('@playwright/test').Page,username='admin',password=adminPassword){await page.goto('/');await page.getByLabel('账号',{exact:true}).fill(username);await page.getByLabel('密码',{exact:true}).fill(password);await page.getByRole('button',{name:'登录',exact:true}).click()}
async function createUser(page:import('@playwright/test').Page,suffix:string){const username=`b-${suffix}-${Date.now()}`;await page.getByRole('button',{name:'创建账号',exact:true}).click();await page.getByLabel('新账号').fill(username);await page.getByLabel('临时密码').fill('temporary-password');await page.getByRole('button',{name:'确认创建'}).click();await expect(page.getByRole('cell',{name:username,exact:true})).toBeVisible();return username}

test('I07.reset 重置失效旧会话并保留账号',async({page,browser},info)=>{
 await login(page);const username=await createUser(page,'reset');const other=await browser.newContext();const user=await other.newPage();await login(user,username,'temporary-password');await expect(user.getByRole('heading',{name:'修改临时密码'})).toBeVisible();
 const row=page.getByRole('row').filter({has:page.getByRole('cell',{name:username,exact:true})});await row.getByRole('button',{name:'重置密码'}).click();await page.getByLabel('临时密码').fill('reset-password-123');await page.getByRole('button',{name:'确认重置'}).click();await expect(page.getByRole('dialog')).toHaveCount(0);await user.reload();await expect(user.getByRole('heading',{name:'登录',exact:true})).toBeVisible();await expect(row).toBeVisible();await page.screenshot({path:info.outputPath('account-retained.png')});await other.close();
});
test('I07.disable 停用失效旧会话且保留记录',async({page,browser},info)=>{
 await login(page);const username=await createUser(page,'disable');const other=await browser.newContext();const user=await other.newPage();await login(user,username,'temporary-password');await expect(user.getByRole('heading',{name:'修改临时密码'})).toBeVisible();const row=page.getByRole('row').filter({has:page.getByRole('cell',{name:username,exact:true})});await row.getByRole('button',{name:'停用',exact:true}).click();await expect(row.getByText('停用',{exact:true})).toBeVisible();await user.reload();await expect(user.getByRole('heading',{name:'登录',exact:true})).toBeVisible();await page.screenshot({path:info.outputPath('disabled-account-retained.png')});await other.close();
});
test('I07.role-change 角色变更立即使旧会话失效',async({page,browser},info)=>{
 await login(page);const username=await createUser(page,'role');const other=await browser.newContext();const user=await other.newPage();await login(user,username,'temporary-password');await user.getByLabel('原密码').fill('temporary-password');await user.getByLabel('新密码').fill('personal-password');await user.getByRole('button',{name:'确认改密'}).click();await expect(user.getByRole('heading',{name:'登录',exact:true})).toBeVisible();await login(user,username,'personal-password');await expect(user.getByRole('heading',{name:`欢迎，${username}`})).toBeVisible();const row=page.getByRole('row').filter({has:page.getByRole('cell',{name:username,exact:true})});await row.getByRole('button',{name:'设为管理员'}).click();await expect(row.getByRole('button',{name:'撤销管理员'})).toBeVisible();await user.reload();await expect(user.getByRole('heading',{name:'登录',exact:true})).toBeVisible();await login(user,username,'personal-password');await expect(user.getByRole('heading',{name:'账号管理'})).toBeVisible();await row.getByRole('button',{name:'撤销管理员'}).click();await expect(row.getByRole('button',{name:'设为管理员'})).toBeVisible();await user.reload();await expect(user.getByRole('heading',{name:'登录',exact:true})).toBeVisible();await page.screenshot({path:info.outputPath('role-change.png')});await other.close();
});
for(const action of ['disable','demote'] as const)test(`I07.last-admin-${action} 保留最后启用管理员`,async({page},info)=>{
 await login(page);const row=page.getByRole('row').filter({has:page.getByRole('cell',{name:'admin',exact:true})});await row.getByRole('button',{name:action==='disable'?'停用':'撤销管理员',exact:true}).click();await expect(page.getByRole('alert')).toHaveText('至少保留一名启用的平台管理员');await expect(row.getByRole('button',{name:'撤销管理员'})).toBeVisible();await expect(row.getByRole('button',{name:'停用',exact:true})).toBeVisible();await page.screenshot({path:info.outputPath(`last-admin-${action}.png`)});
});
