import { test, expect, type Page, type TestInfo } from "@playwright/test";
import { readFileSync } from "node:fs";
import { execFileSync } from "node:child_process";

type Fixture = {
  baseURL?: string;
  adminUsername?: string;
  adminPassword?: string;
  kubeconfigPath: string;
  kubectlKubeconfigPath?: string;
  kubeContext?: string;
  clusterId?: string;
  namespace?: string;
  serviceAccountName?: string;
  remoteWriteURL?: string;
  secretName?: string;
  secretKeys?: {
    username: string;
    password: string;
    credentials?: string;
    ca?: string;
    cert?: string;
    key?: string;
  };
  kubectl?: string;
  runId?: string;
};
const fixture: Fixture | undefined = process.env.AGENT_UI_FIXTURE
  ? JSON.parse(readFileSync(process.env.AGENT_UI_FIXTURE, "utf8"))
  : process.env.AGENT_UI_KUBECONFIG
    ? { kubeconfigPath: process.env.AGENT_UI_KUBECONFIG }
    : undefined;
const diagnostic = process.env.AGENT_UI_DRAFT_ONLY === "1";
const headers = (baseURL: string) => ({
  Origin: new URL(baseURL).origin,
  "Idempotency-Key": crypto.randomUUID(),
});

// 每例独立前置；凭据只从私有 fixture 读取，所有真实浏览器/namespace 准备须由调用方持有全局 fcntl lane。
async function prepare(page: Page, baseURL: string, info: TestInfo) {
  info.annotations.push({
    type: "browserVersion",
    description: page.context().browser()?.version() ?? "unknown",
  });
  if (diagnostic)
    info.annotations.push({
      type: "diagnosticOnly",
      description: "Host草稿诊断，不代表完整owned B通过",
    });
  const initialized = await (
      await page.request.get("/api/v1/initialization")
    ).json(),
    username = fixture!.adminUsername ?? "admin",
    password =
      fixture!.adminPassword ??
      process.env.ADMIN_PASSWORD ??
      "first-password-123";
  if (!initialized.initialized)
    expect(
      (
        await page.request.post("/api/v1/initialization", {
          headers: headers(baseURL),
          data: { username, password },
        })
      ).status(),
    ).toBe(201);
  await page.goto("/");
  await page.getByLabel("账号", { exact: true }).fill(username);
  await page.getByLabel("密码", { exact: true }).fill(password);
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "账号管理", exact: true }),
  ).toBeVisible();
  if (!fixture!.clusterId) {
    const session = await (await page.request.get("/api/v1/session")).json(),
      kubeconfig = readFileSync(fixture!.kubeconfigPath, "utf8"),
      contexts = await (
        await page.request.post("/api/v1/clusters/contexts", {
          headers: headers(baseURL),
          data: { kubeconfig },
        })
      ).json(),
      clusters = await (await page.request.get("/api/v1/clusters")).json();
    if (
      !clusters.clusters.some((c: { state: string }) => c.state === "ACTIVE")
    ) {
      const response = await page.request.post("/api/v1/clusters", {
        headers: headers(baseURL),
        data: {
          name: "agent-ui-control",
          ownerId: session.account.id,
          context: contexts.contexts[0],
          kubeconfig,
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
  await page.getByRole("button", { name: "观测配置", exact: true }).click();
  if (fixture!.clusterId) {
    const clusters = await (await page.request.get("/api/v1/clusters")).json(),
      cluster = clusters.clusters.find(
        (item: { id: string }) => item.id === fixture!.clusterId,
      );
    expect(cluster).toBeTruthy();
    await page.getByLabel("选择已注册集群").click();
    await page
      .locator(".cluster-options")
      .getByRole("button")
      .filter({ has: page.getByText(cluster.name, { exact: true }) })
      .click();
  }
}
async function add(page: Page, component: "agent" | "ksm", name: string) {
  await page.getByRole("button", { name: "添加", exact: true }).click();
  const modal = page.getByRole("dialog", { name: "添加观测实例" });
  await modal
    .getByLabel("类型", { exact: true })
    .selectOption(component === "agent" ? "collector" : "exporter");
  await expect(modal.getByLabel("组件", { exact: true })).toHaveValue(
    component === "agent" ? "Prometheus Agent" : "kube-state-metrics",
  );
  await modal.getByRole("button", { name: "配置实例", exact: true }).click();
  const drawer = page.getByRole("dialog", { name: "实例详情" }),
    input = drawer.getByLabel("名称", { exact: true });
  await expect(input).toHaveValue("");
  await expect(
    drawer.getByRole("button", { name: "保存草稿", exact: true }),
  ).toBeDisabled();
  await input.fill("中文");
  expect(
    await input.evaluate((el: HTMLInputElement) => el.checkValidity()),
  ).toBe(false);
  await input.fill(name);
  if (component === "agent") {
    await expect(
      drawer.getByLabel("Agent namespace", { exact: true }),
    ).not.toHaveValue("");
    if (fixture!.namespace)
      await drawer
        .getByLabel("Agent namespace", { exact: true })
        .selectOption(fixture!.namespace);
    if (fixture!.serviceAccountName)
      await drawer
        .getByLabel("ServiceAccount", { exact: true })
        .selectOption(fixture!.serviceAccountName);
    await expect(
      drawer.getByLabel("ServiceAccount", { exact: true }),
    ).not.toHaveValue("");
    await drawer
      .getByLabel("remoteWrite 1 URL", { exact: true })
      .fill(fixture!.remoteWriteURL ?? "http://receiver.example.invalid/write");
  } else if (fixture!.namespace)
    await drawer
      .getByLabel("部署命名空间", { exact: true })
      .selectOption(fixture!.namespace);
  return drawer;
}
async function save(page: Page, method = "POST") {
  const response = page.waitForResponse(
    (resp) =>
      resp.request().method() === method &&
      new URL(resp.url()).pathname.match(
        method === "POST"
          ? /\/clusters\/[^/]+\/instances$/
          : /\/instances\/[^/]+\/draft$/,
      ) !== null,
  );
  await page
    .getByRole("dialog", { name: "实例详情" })
    .getByRole("button", { name: "保存草稿", exact: true })
    .click();
  const result = await response;
  expect(result.status()).toBe(method === "POST" ? 201 : 200);
  await expect(
    page.getByRole("dialog", { name: "实例详情" }).getByRole("status"),
  ).toContainText("已保存");
  return result.json();
}
async function publish(page: Page) {
  const drawer = page.getByRole("dialog", { name: "实例详情" });
  await drawer.getByRole("button", { name: "预览下发", exact: true }).click();
  const modal = page.getByRole("dialog", { name: /^下发预览/ });
  await expect(modal.locator(".yaml-diff")).toBeVisible();
  const response = page.waitForResponse(
    (resp) =>
      resp.request().method() === "POST" &&
      new URL(resp.url()).pathname.endsWith("/publications"),
  );
  await modal.getByRole("button", { name: "确认下发", exact: true }).click();
  const accepted = await response;
  expect(accepted.status()).toBe(202);
  await expect(
    drawer.getByRole("tab", { name: "运行信息", exact: true }),
  ).toHaveAttribute("aria-selected", "true");
  const operation = await accepted.json();
  await expect
    .poll(
      async () =>
        (
          await (
            await page.request.get(
              `/api/v1/operations/${operation.operationId}`,
            )
          ).json()
        ).state,
      { timeout: 120000 },
    )
    .toBe("SUCCEEDED");
  await drawer
    .getByRole("button", { name: "刷新当前状态", exact: true })
    .click();
  return operation;
}
const newName = (component: string) =>
  `${component}-${Date.now()}-${Math.random().toString(16).slice(2, 6)}`;

// U02 两组件分别经单入口创建/发布/复制；Host 诊断必须显式标记且在发布前停止。
test("U02.primary 统一添加两类组件，名称与复制身份明确", async ({
  page,
  baseURL,
}, info) => {
  test.setTimeout(240000);
  test.skip(!fixture, "需要显式 Agent UI fixture");
  await prepare(page, baseURL!, info);
  for (const component of ["agent", "ksm"] as const) {
    const name = newName(component);
    let drawer = await add(page, component, name),
      created = await save(page);
    expect(created.name).toBe(name);
    if (component === "agent")
      await page.screenshot({ path: info.outputPath("U02-agent-draft.png") });
    if (diagnostic) {
      await drawer
        .getByRole("button", { name: "关闭实例详情", exact: true })
        .click();
      continue;
    }
    await publish(page);
    await drawer.getByRole("tab", { name: "配置草稿", exact: true }).click();
    await expect(drawer.getByLabel("名称", { exact: true })).toBeDisabled();
    await expect(
      drawer.getByLabel(
        component === "agent" ? "Agent namespace" : "部署命名空间",
        { exact: true },
      ),
    ).toBeDisabled();
    if (component === "agent")
      await expect(
        drawer.getByLabel("PVC 容量", { exact: true }),
      ).toBeDisabled();
    await drawer.getByRole("tab", { name: "运行信息", exact: true }).click();
    await drawer
      .getByRole("button", { name: "复制为新实例", exact: true })
      .click();
    drawer = page.getByRole("dialog", { name: "实例详情" });
    await expect(drawer.getByLabel("名称", { exact: true })).toHaveValue("");
    await expect(
      drawer.getByRole("button", { name: "保存草稿", exact: true }),
    ).toBeDisabled();
    await drawer
      .getByRole("button", { name: "关闭实例详情", exact: true })
      .click();
    const detail = await (
      await page.request.get(`/api/v1/instances/${created.id}`)
    ).json();
    expect(detail.uid).not.toBe("");
    expect(detail.name).toBe(name);
  }
  await page.screenshot({
    path: info.outputPath("U02-unified-create-copy.png"),
  });
});

// 产品上传使用Pod可达配置；Host kubectl使用独立配置与显式context，不能误用Docker内部endpoint。
function targetKubeArgs() {
  expect(fixture!.kubeContext, "真实目标必须提供显式 kubeContext").toBeTruthy();
  return [
    "--kubeconfig",
    fixture!.kubectlKubeconfigPath ?? fixture!.kubeconfigPath,
    "--context",
    fixture!.kubeContext!,
  ];
}

function yamlObject(text: string) {
  const kubectl =
    fixture!.kubectl ??
    "/tmp/monitor-mgr-implementation/environment/kubectl-v1.28.15";
  return JSON.parse(
    execFileSync(
      kubectl,
      [
        ...targetKubeArgs(),
        "create",
        "--dry-run=client",
        "--validate=false",
        "-f",
        "-",
        "-o",
        "json",
      ],
      { input: text, encoding: "utf8", stdio: ["pipe", "pipe", "pipe"] },
    ),
  );
}
async function renderedYAML(page: Page, side: number) {
  return page
    .locator(`.yaml-diff .yaml-cell:nth-child(${side})`)
    .evaluateAll((cells) =>
      cells
        .filter((cell) =>
          cell.querySelector(".line-number")?.textContent?.trim(),
        )
        .map((cell) => cell.querySelector("code")?.textContent ?? "")
        .join("\n"),
    );
}

// 固定 v0.83.0 CRD 仅补这两项默认值；其他字段保持严格比较，尤其不合并省略与 {} selector。
function agentCRDDefaults(spec: Record<string, unknown>) {
  return { portName: "web", scrapeInterval: "30s", ...spec };
}

// U06 通过真实左右 YAML 与集群对象校验两类 CR，显示冻结配置而不破坏未保存编辑。
test("U06.primary 两类 CR 左右 YAML、发布导航与冻结配置保留编辑", async ({
  page,
  baseURL,
}, info) => {
  test.setTimeout(360000);
  test.skip(
    !fixture || diagnostic,
    "完整U06需要最终真实Pod fixture，不接受Host草稿诊断",
  );
  await prepare(page, baseURL!, info);
  for (const component of ["agent", "ksm"] as const) {
    const name = newName(`diff-${component}`),
      drawer = await add(page, component, name),
      created = await save(page);
    await publish(page);
    await drawer.getByRole("tab", { name: "配置草稿", exact: true }).click();
    if (component === "agent") {
      await drawer
        .getByLabel("ServiceMonitor 对象模式", { exact: true })
        .selectOption("Labels");
      await drawer
        .getByRole("button", {
          name: "添加 ServiceMonitor 对象 label",
          exact: true,
        })
        .click();
      await drawer
        .getByLabel("ServiceMonitor 对象 label 1 key", { exact: true })
        .fill("app");
      await drawer
        .getByLabel("ServiceMonitor 对象 label 1 value", { exact: true })
        .fill("metrics");
      await drawer
        .getByLabel("ServiceMonitor namespace模式", { exact: true })
        .selectOption("All");
      await drawer
        .getByRole("button", { name: "添加 remoteWrite endpoint", exact: true })
        .click();
      await drawer
        .getByLabel("remoteWrite 2 URL", { exact: true })
        .fill(fixture!.remoteWriteURL!);
      await drawer
        .getByLabel("remoteWrite 1 name", { exact: true })
        .fill("primary");
      await drawer
        .getByLabel("remoteWrite 2 name", { exact: true })
        .fill("secondary");
      if (fixture!.secretName && fixture!.secretKeys) {
        await drawer
          .getByLabel("remoteWrite 1 认证模式", { exact: true })
          .selectOption("BasicAuth");
        for (const key of ["username", "password"] as const) {
          await drawer
            .getByLabel(`remoteWrite 1 ${key} Secret`, { exact: true })
            .selectOption(fixture!.secretName);
          await drawer
            .getByLabel(`remoteWrite 1 ${key} key`, { exact: true })
            .selectOption(fixture!.secretKeys[key]);
        }
      }
    } else {
      await drawer
        .getByLabel("collectors", { exact: true })
        .selectOption("Include");
      await drawer.getByRole("checkbox", { name: "pods", exact: true }).check();
      await drawer
        .getByRole("checkbox", { name: "nodes", exact: true })
        .check();
    }
    await save(page, "PUT");
    await drawer.getByRole("button", { name: "预览下发", exact: true }).click();
    const modal = page.getByRole("dialog", { name: /^下发预览/ });
    await expect(modal.locator(".yaml-diff")).toBeVisible();
    const left = await renderedYAML(page, 1),
      right = await renderedYAML(page, 2);
    expect(right.split("\n").some((line) => /^\s*-\s*$/.test(line))).toBe(
      false,
    );
    expect(left.split("\n").some((line) => /^\s*-\s*$/.test(line))).toBe(false);
    await page.screenshot({
      path: info.outputPath(`U06-${component}-yaml-diff.png`),
    });
    const before = yamlObject(left),
      next = yamlObject(right);
    expect(before.metadata.name).toBe(name);
    expect(next.metadata.name).toBe(name);
    expect(next.kind).toBe(
      component === "agent" ? "PrometheusAgent" : "KubeStateMetrics",
    );
    if (component === "agent") {
      expect(next.spec.remoteWrite).toHaveLength(2);
      expect(next.spec.serviceMonitorSelector.matchLabels.app).toBe("metrics");
      expect(next.spec.serviceMonitorNamespaceSelector).toEqual({});
    }
    await modal.getByRole("button", { name: "返回编辑", exact: true }).click();
    const operation = await publish(page);
    const detail = await (
      await page.request.get(`/api/v1/instances/${created.id}`)
    ).json();
    const args = [
      ...targetKubeArgs(),
      "get",
      component === "agent" ? "prometheusagent" : "kubestatemetrics",
      name,
      ...(component === "agent" ? ["-n", fixture!.namespace!] : []),
      "-o",
      "json",
    ];
    const actual = JSON.parse(
      execFileSync(
        fixture!.kubectl ??
          "/tmp/monitor-mgr-implementation/environment/kubectl-v1.28.15",
        args,
        { encoding: "utf8" },
      ),
    );
    if (component === "agent")
      expect(agentCRDDefaults(actual.spec)).toEqual(
        agentCRDDefaults(next.spec),
      );
    else expect(actual.spec).toEqual(next.spec);
    await drawer.getByRole("tab", { name: "下发历史", exact: true }).click();
    await expect(drawer.getByText(/执行尝试/).first()).toBeVisible();
    await expect(
      drawer.getByText(new RegExp(operation.operationId)),
    ).toBeVisible();
    await drawer.getByRole("tab", { name: "配置草稿", exact: true }).click();
    const cpu = drawer.getByLabel(
      component === "agent" ? "Agent CPU request" : "通用 CPU request",
      { exact: true },
    );
    await cpu.fill("225m");
    await cpu.focus();
    const area = drawer.locator(".drawer-body"),
      scroll = await area.evaluate((el) => el.scrollTop);
    await drawer
      .getByRole("button", {
        name: `查看当前使用版本 ${detail.currentVersionId.slice(0, 8)}`,
        exact: true,
      })
      .click();
    await expect(
      drawer.getByText("只读发布版本", { exact: true }),
    ).toBeVisible();
    await drawer
      .getByRole("button", { name: "返回当前草稿", exact: true })
      .click();
    await expect(cpu).toHaveValue("225m");
    expect(await area.evaluate((el) => el.scrollTop)).toBeGreaterThanOrEqual(
      scroll - 5,
    );
    await expect(cpu).toBeFocused();
    await drawer
      .getByRole("button", { name: "关闭实例详情", exact: true })
      .click();
  }
  await page.screenshot({ path: info.outputPath("U06-yaml-snapshots.png") });
});

// REG18-SOURCE-007：实际集群中不同对象区分规则A/B，不用假响应代替来源隔离验收。
test("REG18-SOURCE-007 草稿关联预览与已部署运行来源隔离", async ({
  page,
  baseURL,
}, info) => {
  test.setTimeout(240000);
  test.skip(
    !fixture || diagnostic,
    "需要最终真实平台Pod与独占namespace fixture；Host草稿诊断不执行本例",
  );
  await prepare(page, baseURL!, info);
  const caseName = newName("source"),
    monitorA = `${caseName}-a`,
    monitorB = `${caseName}-b`;
  const namespace = fixture!.namespace!;
  expect(namespace).toBeTruthy();
  const kubectl =
    fixture!.kubectl ??
    "/tmp/monitor-mgr-implementation/environment/kubectl-v1.28.15";
  const kubeArgs = targetKubeArgs();
  const monitor = (name: string, rule: string) => ({
    apiVersion: "monitoring.coreos.com/v1",
    kind: "ServiceMonitor",
    metadata: {
      name,
      namespace,
      labels: { "ui-case": caseName, "ui-rule": rule },
    },
    spec: {
      selector: { matchLabels: { "ui-no-service": caseName } },
      endpoints: [{ port: "web" }],
    },
  });
  // 关键：调用方必须全程持有real-api-lane；只创建、清理本例唯一namespace/name，标签匹配不等于采集成功。
  try {
    execFileSync(kubectl, [...kubeArgs, "apply", "-f", "-"], {
      input: JSON.stringify({
        apiVersion: "v1",
        kind: "List",
        items: [monitor(monitorA, "a"), monitor(monitorB, "b")],
      }),
      stdio: ["pipe", "pipe", "pipe"],
    });
    const drawer = await add(page, "agent", caseName);
    await drawer
      .getByLabel("ServiceMonitor 对象模式", { exact: true })
      .selectOption("Labels");
    for (const [index, key, value] of [
      [1, "ui-case", caseName],
      [2, "ui-rule", "a"],
    ] as const) {
      await drawer
        .getByRole("button", {
          name: "添加 ServiceMonitor 对象 label",
          exact: true,
        })
        .click();
      await drawer
        .getByLabel(`ServiceMonitor 对象 label ${index} key`, { exact: true })
        .fill(key);
      await drawer
        .getByLabel(`ServiceMonitor 对象 label ${index} value`, { exact: true })
        .fill(value);
    }
    const created = await save(page);
    await publish(page);
    const region = drawer.getByRole("region", {
      name: "来源 association:ServiceMonitor",
      exact: true,
    });
    await expect(region).toContainText(monitorA);
    await expect(region).not.toContainText(monitorB);

    await drawer.getByRole("tab", { name: "配置草稿", exact: true }).click();
    await drawer
      .getByLabel("ServiceMonitor 对象 label 2 value", { exact: true })
      .fill("b");
    const draftB = await save(page, "PUT");
    const previewResponse = page.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        new URL(response.url()).pathname.endsWith("/association-previews"),
    );
    await drawer
      .getByRole("button", { name: "刷新采集关联预览", exact: true })
      .click();
    const response = await previewResponse;
    expect(response.status()).toBe(200);
    const previewB = await response.json();
    expect(previewB.draftRevision).toBe(draftB.revision);
    expect(
      previewB.sources
        .find(
          (source: { source: string }) =>
            source.source === "association:ServiceMonitor",
        )
        .matches.map((match: { name: string }) => match.name),
    ).toEqual([monitorB]);
    await expect(
      drawer.getByText(`草稿修订 ${draftB.revision} 的匹配预览`, {
        exact: true,
      }),
    ).toBeVisible();
    await expect(region).toContainText(monitorB);
    await expect(region).not.toContainText(monitorA);

    await drawer.getByRole("tab", { name: "运行信息", exact: true }).click();
    await expect(region).toContainText(monitorA);
    await expect(region).not.toContainText(monitorB);
    await drawer
      .getByRole("button", { name: "刷新当前状态", exact: true })
      .click();
    await expect(region).toContainText(monitorA);
    await expect(region).not.toContainText(monitorB);
    const detail = await (
      await page.request.get(`/api/v1/instances/${created.id}`)
    ).json();
    expect(
      detail.sources
        .find(
          (source: { source: string }) =>
            source.source === "association:ServiceMonitor",
        )
        .matches.map((match: { name: string }) => match.name),
    ).toEqual([monitorA]);

    await drawer.getByRole("tab", { name: "配置草稿", exact: true }).click();
    await expect(region).toContainText(monitorB);
    const cpu = drawer.getByLabel("Agent CPU request", { exact: true });
    await cpu.fill("235m");
    await cpu.focus();
    await expect(
      drawer.getByText(/草稿修订.*已过期，请保存并重新刷新/),
    ).toBeVisible();
    await page.waitForTimeout(10500);
    await expect(cpu).toHaveValue("235m");
    await expect(cpu).toBeFocused();
    await expect(region).toContainText(monitorB);
    await expect(region).not.toContainText(monitorA);
    await page.screenshot({
      path: info.outputPath("REG18-SOURCE-007-draft-b-expired.png"),
    });
    await save(page, "PUT");
    await expect(
      drawer.getByText(/草稿修订.*已过期，请保存并重新刷新/),
    ).toBeVisible();
    await drawer.getByRole("tab", { name: "运行信息", exact: true }).click();
    await expect(region).toContainText(monitorA);
    await expect(region).not.toContainText(monitorB);
    await page.screenshot({
      path: info.outputPath("REG18-SOURCE-007-runtime-a.png"),
    });
    const actual = JSON.parse(
      execFileSync(
        kubectl,
        [
          ...kubeArgs,
          "get",
          "prometheusagent",
          caseName,
          "-n",
          namespace,
          "-o",
          "json",
        ],
        { encoding: "utf8" },
      ),
    );
    expect(actual.spec.serviceMonitorSelector.matchLabels["ui-rule"]).toBe("a");
  } finally {
    execFileSync(
      kubectl,
      [
        ...kubeArgs,
        "delete",
        "servicemonitor",
        monitorA,
        monitorB,
        "-n",
        namespace,
        "--ignore-not-found=true",
        "--wait=false",
      ],
      { stdio: ["pipe", "pipe", "pipe"] },
    );
  }
});
