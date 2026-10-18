import { test, expect, type Page, type TestInfo } from "@playwright/test";
import { readFileSync } from "node:fs";

// 每例独立平台由正式runner准备；Host诊断只验证真实HTTP/Kitex，不替代Pod B。
type Fixture = {
  adminUsername?: string;
  adminPassword: string;
  clusterId: string;
  clusterName: string;
  namespace: string;
  runId: string;
  diagnosticOnly?: boolean;
  secondClusterId?: string;
  secondClusterName?: string;
  largeRevisionInstanceId?: string;
  largeRevision?: string;
  expectedLargeRevision?: string;
  initialStatistics?: {managed: number; unmanaged: number};
  unmanagedInstances?: {id: string; name: string}[];
  versionedInstances?: { id: string; component: string; name: string; namespace: string; clusterName?: string; expectedRequestCPU: string; expectedSourceDraftRevision: string | number; failureControl?: {baseURL: string; controlToken: string; writePath: string} }[];
};
const fixture: Fixture | undefined = process.env.WORKBENCH_UI_FIXTURE
  ? JSON.parse(readFileSync(process.env.WORKBENCH_UI_FIXTURE, "utf8"))
  : undefined;
test.use({ actionTimeout: 15000 });
const observations = new WeakMap<Page, { console: unknown[]; pageErrors: string[]; httpErrors: unknown[] }>();
const safe = (text: string) => [fixture?.adminPassword, ...(fixture?.versionedInstances ?? []).map(item => item.failureControl?.controlToken)].filter((value): value is string => Boolean(value)).reduce((value, secret) => value.split(secret).join("[敏感内容已隐藏]"), text);
const headers = (baseURL: string) => ({ Origin: new URL(baseURL).origin, "Idempotency-Key": crypto.randomUUID() });

test.beforeEach(async ({ page }) => {
  test.skip(!fixture, "需要独立平台与0600 WORKBENCH_UI_FIXTURE；无fixture不计运行通过");
  const value = { console: [] as unknown[], pageErrors: [] as string[], httpErrors: [] as unknown[] };
  observations.set(page, value);
  page.on("console", event => value.console.push({ type: event.type(), text: safe(event.text()), location: event.location() }));
  page.on("pageerror", event => value.pageErrors.push(safe(event.message)));
  page.on("response", response => {
    if (response.status() >= 400) value.httpErrors.push({ status: response.status(), method: response.request().method(), path: new URL(response.url()).pathname });
  });
});
test.afterEach(async ({ page }, info) => {
  if (!fixture) return;
  await info.attach("browser-console.json", { body: Buffer.from(safe(JSON.stringify(observations.get(page)))), contentType: "application/json" });
  const layout = await page.evaluate(() => ({ viewport: window.innerWidth, scrollWidth: document.documentElement.scrollWidth })).catch(() => null);
  await info.attach("browser-layout.json", { body: Buffer.from(JSON.stringify(layout)), contentType: "application/json" });
  expect(observations.get(page)?.pageErrors ?? []).toEqual([]);
  expect(JSON.stringify(observations.get(page)?.console ?? [])).not.toMatch(/invalid regular expression|SyntaxError|TypeError/i);
  if (layout) expect(layout.scrollWidth).toBeLessThanOrEqual(layout.viewport);
});

async function prepare(page: Page, baseURL: string, info: TestInfo) {
  info.annotations.push({ type: "browserVersion", description: page.context().browser()?.version() ?? "unknown" });
  if (fixture!.diagnosticOnly) info.annotations.push({ type: "diagnosticOnly", description: "Host真实HTTP/Kitex诊断，非正式Pod B" });
  const initialized = await (await page.request.get("/api/v1/initialization")).json();
  if (!initialized.initialized) expect((await page.request.post("/api/v1/initialization", { headers: headers(baseURL), data: { username: fixture!.adminUsername ?? "admin", password: fixture!.adminPassword } })).status()).toBe(201);
  await page.goto("/");
  await page.getByLabel("账号", { exact: true }).fill(fixture!.adminUsername ?? "admin");
  await page.getByLabel("密码", { exact: true }).fill(fixture!.adminPassword);
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await expect(page.getByRole("heading", { name: "账号管理", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "观测配置", exact: true }).click();
  await expect(page.getByRole("heading", { name: "观测实例", exact: true })).toBeVisible();
}

function configuration(name: string, agent = false) {
  const resources = { requestCPU: agent ? "200m" : "100m", requestMemory: agent ? "512Mi" : "250Mi", limitMemory: agent ? "1024Mi" : "500Mi" };
  if (!agent) return { component: "kube-state-metrics", ksm: { name, targetNamespace: fixture!.namespace, desiredState: "Running", deploymentMode: "Deployment", resources, collectors: { mode: "Default" }, metricFilter: { mode: "All" }, namespaceFilter: { mode: "All" }, advancedResources: false } };
  const rule = { object: { mode: "None" }, namespace: { mode: "Own" } };
  return { component: "Prometheus Agent", agent: { name, namespace: fixture!.namespace, desiredState: "Running", shards: 1, resources, storage: { mode: "PVC", capacity: "1Gi", whenScaled: "Retain", whenDeleted: "Retain" }, serviceAccountName: "prometheus-agent", associations: { serviceMonitor: rule, podMonitor: rule, probe: rule, scrapeConfig: rule }, remoteWrite: [{ url: "https://receiver.example.invalid/api/v1/write", auth: { mode: "None" } }] } };
}

test("U04.primary 服务端搜索分页、独立统计与显式活动排序", async ({ page, baseURL }, info) => {
  test.setTimeout(120000);
  await prepare(page, baseURL!, info);
  const prefix = `ui19-${crypto.randomUUID().slice(0, 8)}`;
  for (let index = 0; index < 21; index++) {
    const response = await page.request.post(`/api/v1/clusters/${fixture!.clusterId}/instances`, { headers: headers(baseURL!), data: configuration(`${prefix}-${String(index).padStart(2, "0")}`, index === 20) });
    expect(response.status()).toBe(201);
  }
  const response = page.waitForResponse(r => new URL(r.url()).pathname === `/api/v1/clusters/${fixture!.clusterId}/instances` && r.request().method() === "GET");
  await page.getByRole("button", { name: "刷新列表", exact: true }).click();
  const url = new URL((await response).url());
  expect(url.searchParams.get("page")).toBe("1");
  expect(url.searchParams.get("pageSize")).toBe("20");
  await expect(page.getByRole("button", { name: "下一页", exact: true })).toBeEnabled();
  const managed = page.getByText("平台纳管实例", { exact: true }).locator("..").locator("strong");
  expect(fixture!.initialStatistics).toBeTruthy();
  const totalManaged = String(fixture!.initialStatistics!.managed + 21);
  await expect(managed).toHaveText(totalManaged);
  await page.getByLabel("名称搜索", { exact: true }).fill(prefix.toUpperCase());
  await expect(page.getByText("共 21 个实例", { exact: true })).toBeVisible();
  await expect(managed).toHaveText(totalManaged);
  await page.getByLabel("组件", { exact: true }).selectOption("Prometheus Agent");
  await page.getByLabel("类型", { exact: true }).selectOption("exporter");
  await expect(page.getByLabel("组件", { exact: true })).toHaveValue("");
  await expect(page.getByLabel("组件", { exact: true }).locator("option[value='Prometheus Agent']")).toHaveCount(0);
  await expect(page.getByText("共 20 个实例", { exact: true })).toBeVisible();
  await page.getByLabel("运行状态", { exact: true }).selectOption("Available");
  await expect(page.getByText("没有符合条件的观测实例", { exact: true })).toBeVisible();
  await expect(managed).toHaveText(totalManaged);
  await page.getByRole("button", { name: "清除筛选", exact: true }).click();
  await page.getByLabel("名称搜索", { exact: true }).fill(prefix);
  await page.getByRole("button", { name: "下一页", exact: true }).click();
  await expect(page.getByText(`${prefix}-00`, { exact: true })).toBeVisible();
  await page.getByRole("row").filter({ hasText: `${prefix}-00` }).getByRole("button", { name: "编辑", exact: true }).click();
  await page.getByLabel("通用 CPU request", { exact: true }).fill("110m");
  await page.getByRole("button", { name: "保存草稿", exact: true }).click();
  await expect(page.getByText("草稿 v2 已保存", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "关闭实例详情", exact: true }).click();
  await page.getByRole("button", { name: "上一页", exact: true }).click();
  await expect(page.locator("tbody tr").first()).toContainText(`${prefix}-00`);
  const input = page.getByLabel("名称搜索", { exact: true });
  await input.focus();
  await page.evaluate(() => window.scrollTo(0, 300));
  await page.waitForTimeout(10500);
  await expect(input).toBeFocused();
  await expect(input).toHaveValue(prefix);
  await expect(page.locator("tbody tr").first()).toContainText(`${prefix}-00`);
  expect(await page.evaluate(() => window.scrollY)).toBeGreaterThanOrEqual(250);
  await info.attach("U04-workbench.png", { body: await page.screenshot(), contentType: "image/png" });
  // 未纳管置底必须有真实fixture，空集合不能冒充覆盖。
  expect(fixture!.unmanagedInstances?.length).toBeGreaterThan(0);
  await page.getByRole("button", { name: "清除筛选", exact: true }).click();
  await page.getByLabel("每页条数", { exact: true }).selectOption("100");
  await expect.poll(async () => await page.locator("tbody tr td:first-child strong").count()).toBe(Math.min(100, fixture!.initialStatistics!.managed + 21 + fixture!.initialStatistics!.unmanaged));
  const allRows: {name: string; unmanaged: boolean; version: boolean}[] = [];
  for (let index = 1; ; index++) {
    await expect(page.locator(".instance-pagination")).toContainText(`第 ${index} /`);
    allRows.push(...await page.locator("tbody tr").evaluateAll(rows => rows.filter(row => row.querySelector("td strong")).map(row => ({name: row.querySelector("td strong")!.textContent!, unmanaged: row.textContent!.includes("平台未纳管"), version: Boolean(row.querySelector(".version-link"))}))));
    if (await page.getByRole("button", { name: "下一页", exact: true }).isDisabled()) break;
    await page.getByRole("button", { name: "下一页", exact: true }).click();
  }
  expect(allRows.length).toBe(fixture!.initialStatistics!.managed + 21 + fixture!.initialStatistics!.unmanaged);
  const firstExternal = allRows.findIndex(row => row.unmanaged);
  expect(firstExternal).toBe(fixture!.initialStatistics!.managed + 21);
  expect(allRows.slice(firstExternal).every(row => row.unmanaged && !row.version)).toBe(true);
  for (const item of fixture!.unmanagedInstances!) expect(allRows.some(row => row.name === item.name && row.unmanaged && !row.version)).toBe(true);
});

// 两级信息与键盘选择都从实际页面判定，切换不会把集群元信息变成主名称。
test("U01.primary C方案层级、集群键盘与安全页面", async ({ page, baseURL }, info) => {
  await prepare(page, baseURL!, info);
  const name = `ui19-layout-${crypto.randomUUID().slice(0, 8)}`;
  expect((await page.request.post(`/api/v1/clusters/${fixture!.clusterId}/instances`, { headers: headers(baseURL!), data: configuration(name) })).status()).toBe(201);
  await page.getByRole("button", { name: "刷新列表", exact: true }).click();
  await expect(page.locator(".brand")).toContainText("可观测管控平台");
  await expect(page.getByRole("columnheader", { name: "名称/命名空间", exact: true })).toBeVisible();
  await expect(page.getByRole("columnheader", { name: "类型/组件", exact: true })).toBeVisible();
  await expect(page.getByRole("columnheader", { name: "运行状态", exact: true })).toBeVisible();
  const row = page.getByRole("row").filter({ hasText: name });
  await expect(row.locator("td").first().locator("strong")).toHaveText(name);
  await expect(row.locator("td").first().locator(".help")).toHaveText(fixture!.namespace);
  const chooser = page.getByLabel("选择已注册集群", { exact: true });
  await expect(chooser.locator("strong")).toHaveText(fixture!.clusterName);
  await expect(chooser.locator(".help")).toContainText("Kubernetes");
  await expect(chooser.locator(".help")).toContainText("所属");
  await chooser.focus();
  await chooser.press("ArrowDown");
  await expect(page.getByRole("listbox", { name: "已注册集群", exact: true })).toBeVisible();
  await expect(page.getByRole("option", { name: new RegExp(fixture!.clusterName) })).toBeFocused();
  await page.keyboard.press("End");
  await page.keyboard.press("Escape");
  await expect(page.getByRole("listbox", { name: "已注册集群", exact: true })).not.toBeVisible();
  await expect(chooser).toBeFocused();
  await expect(page.getByText("Operator 状态报告", { exact: true })).toHaveCount(0);
  await info.attach("U01-workbench.png", { body: await page.screenshot(), contentType: "image/png" });
});

// 故障控制仅在Node侧使用私有fixture，不进入浏览器、业务请求或截图。
async function rejectWrite(control: { baseURL: string; controlToken: string; writePath: string }, reject: boolean) {
  const response = await fetch(`${control.baseURL}/faults`, { method: "POST", headers: { "Content-Type": "application/json", "X-Fault-Control": control.controlToken }, body: JSON.stringify({ readMode: "none", rejectWritePath: reject ? control.writePath : "" }) });
  expect(response.status).toBe(200);
}

test("U05.primary 冻结来源版本、未保存编辑和失败不提升", async ({ page, baseURL }, info) => {
  test.setTimeout(180000);
  await prepare(page, baseURL!, info);
  const draftName = `ui19-unpublished-${crypto.randomUUID().slice(0, 8)}`;
  expect((await page.request.post(`/api/v1/clusters/${fixture!.clusterId}/instances`, { headers: headers(baseURL!), data: configuration(draftName) })).status()).toBe(201);
  await page.getByRole("button", { name: "刷新列表", exact: true }).click();
  await expect(page.getByRole("row").filter({ hasText: draftName }).locator(".version-link")).toHaveCount(0);
  expect(fixture!.unmanagedInstances?.length).toBeGreaterThan(0);
  for (const outside of fixture!.unmanagedInstances!) {
    await expect(page.getByRole("row").filter({ hasText: outside.name })).toContainText("平台未纳管");
    await expect(page.getByRole("row").filter({ hasText: outside.name }).locator(".version-link")).toHaveCount(0);
  }
  const items = fixture!.versionedInstances ?? [];
  expect(items.map(item => item.component).sort()).toEqual(["Prometheus Agent", "kube-state-metrics"]);
  for (const item of items) {
    if (item.clusterName && item.clusterName !== fixture!.clusterName) {
      await page.getByLabel("选择已注册集群", { exact: true }).click();
      await page.getByRole("option").filter({ hasText: item.clusterName }).click();
    }
    await page.getByRole("button", { name: "刷新列表", exact: true }).click();
    const row = page.getByRole("row").filter({ hasText: item.name });
    const original = (await (await page.request.get(`/api/v1/instances/${item.id}`)).json()).currentVersionId;
    expect(typeof original).toBe("string"); expect(original).not.toBe("");
    await row.locator(".version-link").click();
    const drawer = page.getByRole("dialog", { name: "实例详情", exact: true });
    const cpuName = item.component === "Prometheus Agent" ? "Agent CPU request" : "通用 CPU request";
    const cpu = drawer.getByLabel(cpuName, { exact: true });
    await expect(drawer.getByText("只读发布版本", { exact: true })).toBeVisible();
    await expect(cpu).toHaveValue(item.expectedRequestCPU); await expect(cpu).toBeDisabled();
    await drawer.getByRole("button", { name: "返回当前草稿", exact: true }).click();
    await cpu.fill("321m");
    await cpu.evaluate((input: HTMLInputElement) => input.setSelectionRange(1, 2));
    const scroll = await drawer.locator(".drawer-body").evaluate(element => { element.scrollTop = 180; return element.scrollTop; });
    await drawer.getByRole("button", { name: /^查看当前使用版本 / }).click();
    await expect(drawer.getByText("只读发布版本", { exact: true })).toBeVisible();
    await expect(drawer.getByText(new RegExp(`来源草稿 v${item.expectedSourceDraftRevision} ·`))).toBeVisible();
    await expect(cpu).toHaveValue(item.expectedRequestCPU); await expect(cpu).toBeDisabled();
    await drawer.getByRole("button", { name: "返回当前草稿", exact: true }).click();
    await expect(cpu).toHaveValue("321m"); await expect(cpu).toBeFocused();
    expect(await cpu.evaluate((input: HTMLInputElement) => [input.selectionStart, input.selectionEnd])).toEqual([1, 2]);
    expect(await drawer.locator(".drawer-body").evaluate(element => element.scrollTop)).toBe(scroll);
    await page.waitForTimeout(10500);
    await expect(cpu).toBeFocused(); await expect(cpu).toHaveValue("321m");
    await drawer.getByRole("button", { name: "保存草稿", exact: true }).click();
    await drawer.getByRole("button", { name: "预览下发", exact: true }).click();
    const publication = page.waitForResponse(response => new URL(response.url()).pathname === `/api/v1/instances/${item.id}/publications` && response.request().method() === "POST");
    await page.getByRole("button", { name: "确认下发", exact: true }).click();
    const accepted = await (await publication).json();
    await expect(drawer.getByRole("tab", { name: "运行信息", exact: true })).toHaveAttribute("aria-selected", "true");
    await expect.poll(async () => (await (await page.request.get(`/api/v1/operations/${accepted.operationId}`)).json()).state, { timeout: 25000 }).toBe("SUCCEEDED");
    await drawer.getByRole("button", { name: "刷新当前状态", exact: true }).click();
    const source = drawer.getByLabel("来源 CR", { exact: true });
    await expect(source).toContainText("最后成功观察");
    const confirmed = (await (await page.request.get(`/api/v1/instances/${item.id}`)).json()).currentVersionId;
    expect(confirmed).not.toBe(original);
    await drawer.getByRole("tab", { name: "配置草稿", exact: true }).click();
    await cpu.fill("444m");
    await drawer.getByRole("button", { name: "保存草稿", exact: true }).click();
    await drawer.getByRole("button", { name: "预览下发", exact: true }).click();
    expect(item.failureControl, "每组件必须有独立受控拒绝写入口").toBeTruthy();
    try {
      await rejectWrite(item.failureControl!, true);
      const failure = page.waitForResponse(response => new URL(response.url()).pathname === `/api/v1/instances/${item.id}/publications` && response.request().method() === "POST");
      await page.getByRole("button", { name: "确认下发", exact: true }).click();
      const failed = await (await failure).json();
      await expect.poll(async () => (await (await page.request.get(`/api/v1/operations/${failed.operationId}`)).json()).state, { timeout: 25000 }).toBe("FAILED");
      await drawer.getByRole("button", { name: "刷新当前状态", exact: true }).click();
      expect((await (await page.request.get(`/api/v1/instances/${item.id}`)).json()).currentVersionId).toBe(confirmed);
      await drawer.getByRole("tab", { name: "配置草稿", exact: true }).click();
      await drawer.getByRole("button", { name: /^查看当前使用版本 / }).click();
      await expect(cpu).toHaveValue("321m"); await expect(cpu).toBeDisabled();
      await drawer.locator(".drawer-body").evaluate(element => { element.scrollTop = 0; });
      await info.attach(`U05-${item.component === "Prometheus Agent" ? "Agent" : "KSM"}-version.png`, { body: await page.screenshot(), contentType: "image/png" });
    } finally { await rejectWrite(item.failureControl!, false); }
    await drawer.getByRole("button", { name: "关闭实例详情", exact: true }).click();
  }
});

test("REG19-HTTP-I64-001 浏览器大修订精确读写", async ({ page, baseURL }, info) => {
  await prepare(page, baseURL!, info);
  expect(fixture!.largeRevisionInstanceId).toBeTruthy();
  const item = await (await page.request.get(`/api/v1/instances/${fixture!.largeRevisionInstanceId}`)).json();
  await page.getByRole("row").filter({ hasText: item.name }).getByRole("button", { name: "编辑", exact: true }).click();
  const drawer = page.getByRole("dialog", { name: "实例详情", exact: true });
  await expect(drawer.getByText(`草稿 v${fixture!.largeRevision} 已保存`, { exact: true })).toBeVisible();
  await drawer.getByLabel("通用 CPU request", { exact: true }).fill("111m");
  const saving = page.waitForRequest(request => new URL(request.url()).pathname === `/api/v1/instances/${item.id}/draft` && request.method() === "PUT");
  await drawer.getByRole("button", { name: "保存草稿", exact: true }).click();
  expect((await saving).postDataJSON().expectedDraftRevision).toBe(fixture!.largeRevision);
  await expect(drawer.getByText(`草稿 v${fixture!.expectedLargeRevision} 已保存`, { exact: true })).toBeVisible();
  const draft = await (await page.request.get(`/api/v1/instances/${item.id}/draft`)).json();
  expect(draft.revision).toBe(fixture!.expectedLargeRevision);
  await info.attach("REG19-i64.png", { body: await page.screenshot(), contentType: "image/png" });
});
