import { test, expect, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";

const fixture = process.env.KSM_UI_KUBECONFIG ?? process.env.CLUSTER_KUBECONFIG;
const adminPassword = process.env.ADMIN_PASSWORD ?? "first-password-123";

// 关键：每例建立独立平台基线，只注册读取目标身份，不创建或修改任何集群资源。
async function prepare(page: Page, baseURL: string) {
  const headers = {
    Origin: new URL(baseURL).origin,
    "Idempotency-Key": crypto.randomUUID(),
  };
  const initial = await (
    await page.request.get("/api/v1/initialization")
  ).json();
  if (!initial.initialized)
    expect(
      (
        await page.request.post("/api/v1/initialization", {
          headers,
          data: { username: "admin", password: adminPassword },
        })
      ).status(),
    ).toBe(201);
  await page.goto("/");
  await page.getByLabel("账号", { exact: true }).fill("admin");
  await page.getByLabel("密码", { exact: true }).fill(adminPassword);
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await expect(page.getByRole("heading", { name: "账号管理" })).toBeVisible();
  const session = await (await page.request.get("/api/v1/session")).json();
  const raw = readFileSync(fixture!, "utf8");
  const contexts = await (
    await page.request.post("/api/v1/clusters/contexts", {
      headers,
      data: { kubeconfig: raw },
    })
  ).json();
  let clusters = (await (await page.request.get("/api/v1/clusters")).json())
    .clusters;
  if (!clusters.some((c: { state: string }) => c.state === "ACTIVE")) {
    const response = await page.request.post("/api/v1/clusters", {
      headers: { ...headers, "Idempotency-Key": crypto.randomUUID() },
      data: {
        name: "ksm-ui-control",
        ownerId: session.account.id,
        context: contexts.contexts[0],
        kubeconfig: raw,
      },
    });
    expect(response.status()).toBe(202);
    const op = await response.json();
    await expect
      .poll(
        async () =>
          (
            await (
              await page.request.get(`/api/v1/operations/${op.operationId}`)
            ).json()
          ).state,
      )
      .toBe("SUCCEEDED");
  }
}

test("U03.primary 连续选择保留编辑、焦点和滚动，namespace 失败不清空", async ({
  page,
  baseURL,
}, info) => {
  test.setTimeout(60000);
  info.annotations.push({ type: "browserVersion", description: page.context().browser()?.version() ?? "unknown" });
  test.skip(!fixture, "需要显式真实集群 kubeconfig，仅执行读取和平台草稿保存");
  await prepare(page, baseURL!);
  await page.getByRole("button", { name: "观测配置", exact: true }).click();
  await expect(page.getByRole("heading", { name: "观测实例" })).toBeVisible();
  await page.getByRole("button", { name: "添加", exact: true }).click();
  await page.getByRole("button", { name: "配置实例", exact: true }).click();
  const drawer = page.getByRole("dialog", { name: "实例详情" });
  await expect(drawer.getByLabel("名称", { exact: true })).toHaveValue("");
  await drawer
    .getByLabel("名称", { exact: true })
    .fill(`ui-draft-${Date.now()}`);
  await drawer
    .getByLabel("collectors", { exact: true })
    .selectOption("Include");
  const collectors = drawer.getByRole("group", { name: "collector 选择" });
  await expect(collectors.getByRole("checkbox").first()).toHaveAccessibleName(
    "pods",
  );
  await collectors.getByRole("checkbox", { name: "pods", exact: true }).check();
  await collectors
    .getByRole("checkbox", { name: "deployments", exact: true })
    .check();
  await drawer
    .getByLabel("namespace 过滤", { exact: true })
    .selectOption("Include");
  await drawer
    .getByRole("button", { name: "选择 namespace", exact: true })
    .click();
  await drawer.getByRole("checkbox", { name: "default", exact: true }).check();
  await drawer
    .getByRole("checkbox", { name: "kube-system", exact: true })
    .check();
  const area = drawer.locator(".drawer-body");
  await area.evaluate((el) => {
    el.scrollTop = el.scrollHeight;
  });
  const before = await area.evaluate((el) => el.scrollTop);
  const pattern = drawer.getByLabel("namespace 全名正则（每行一项）");
  await pattern.fill("^team-.*$");
  await pattern.focus();
  await page.waitForTimeout(10500);
  await expect(pattern).toBeFocused();
  await expect(pattern).toHaveValue("^team-.*$");
  expect(await area.evaluate((el) => el.scrollTop)).toBeGreaterThanOrEqual(
    before - 5,
  );
  await page.route("**/api/v1/clusters/*/namespaces", (route) =>
    route.abort("connectionfailed"),
  );
  await drawer
    .getByRole("button", { name: "刷新 namespace", exact: true })
    .click();
  await expect(drawer.getByRole("alert")).toContainText("namespace");
  await expect(drawer.getByRole("alert")).toContainText(
    "网络请求失败，请稍后重试",
  );
  await expect(
    drawer.getByRole("checkbox", { name: "default", exact: true }),
  ).toBeChecked();
  await expect(
    drawer.getByRole("checkbox", { name: "kube-system", exact: true }),
  ).toBeChecked();
  await page.unroute("**/api/v1/clusters/*/namespaces");
  await drawer.getByRole("button", { name: "保存草稿", exact: true }).click();
  await expect(drawer.getByRole("status")).toContainText("已保存");
  await expect(drawer.getByLabel("名称", { exact: true })).not.toHaveValue("");
  await page.screenshot({
    path: info.outputPath("ksm-collector-namespace-preserved.png"),
  });
});
