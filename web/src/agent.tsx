import { SourceHistory, type SourceStatus } from "./source-history";
import { useDraftBookmark } from "./draft-bookmark";
import {AgentLifecycleFields,AgentStorageFields,StorageImpact,StoragePolicies,type StorageImpactValue,type StatefulPolicy,StorageVolumeOutcome} from "./agent-storage";
import type { Int64 } from "./api";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { api, APIError } from "./api";
import type { Cluster } from "./clusters";
import { YAML, YAMLDiff } from "./instance-yaml";
import { LifecycleActions, type Operation } from "./lifecycle";

// Agent 仅表达平台支持的类型化配置；认证/TLS 始终引用同 namespace 的既有 Secret。
type Resources = {
  requestCPU?: string;
  requestMemory?: string;
  limitCPU?: string;
  limitMemory?: string;
};
type Expression = { key: string; operator: string; values?: string[] };
type Selector = {
  matchLabels?: Record<string, string>;
  matchExpressions?: Expression[];
};
type Rule = { mode: string; selector?: Selector };
type Association = { object: Rule; namespace: Rule };
type SecretRef = { name: string; key: string };
type TLS = {
  ca?: SecretRef;
  cert?: SecretRef;
  key?: SecretRef;
  serverName?: string;
};
type Endpoint = {
  url: string;
  name?: string;
  remoteTimeout?: string;
  auth: {
    mode: string;
    username?: SecretRef;
    password?: SecretRef;
    credentials?: SecretRef;
  };
  tls?: TLS;
};
export type AgentConfiguration = {
  name: string;
  namespace: string;
  desiredState: string;
  shards: number;
  resources: Resources;
  storage: {
    mode: string;
    capacity?: string;
    whenScaled: string;
    whenDeleted: string;
  };
  serviceAccountName: string;
  associations: Record<
    "serviceMonitor" | "podMonitor" | "probe" | "scrapeConfig",
    Association
  >;
  remoteWrite: Endpoint[];
};
type Runtime = {
  summary: string;
  desiredState: string;
  deploymentMode: string;
  availablePods?: number;
  desiredPods?: number;
  lastSuccessAt: string;
  error: string;
  observedAt: string;
  shards?: number;
  paused?: boolean;
};
export type Source = SourceStatus & {
  policies?: StatefulPolicy[]; storageMode?: string;
  matches?: { kind: string; namespace: string; name: string; uid: string }[];
  volumes?: {
    namespace: string;
    name: string;
    uid: string;
    phase: string;
    requestedStorage: string;
    accessModes: string[];
    storageClass: string;
    mountedByPods: string[];
    expectedPolicy?:string;retentionResult?:string;lastAttemptAt?:string;lastSuccessAt?:string;error?:string;statefulUID?:string;statefulName?:string;shard?:number;agentUID?:string;impactAction?:string;
  }[];
};
type Instance = {
  id: string;
  name: string;
  readOnly: boolean;
  uid: string;
  lifecycle: string;
  currentVersionId: string;
  runtime?: Runtime;
  yaml?: string;
  crNamespace?: string;
  sources?: Source[];
};
type Draft = { revision: Int64; agent: AgentConfiguration; yaml: string };
type Version = {
  versionId: string;
  sourceDraftRevision: Int64;
  agent: AgentConfiguration;
  yaml: string;
  createdAt: string;
};
type Preview = StorageImpactValue & {
  previewId: string;
  draftRevision: Int64;
  leftYAML: string;
  rightYAML: string;
  drift: boolean;
  warnings: string[];
};
type Reference = {
  kind: string;
  name: string;
  namespace: string;
  keys: string[];
};
type Tab = "draft" | "runtime" | "history" | "yaml";
type Props = {
  selection: {
    instance?: Instance;
    agentTemplate?: AgentConfiguration;
    versionId?: string;
    tab?: Tab;
    operation?: Operation;
  };
  cluster: Cluster;
  onAction: (work: () => Promise<void>) => Promise<void>;
  onChange: () => void;
  onClose: () => void;
  onCopy: (config: AgentConfiguration) => void;
};
const kinds = [
  ["serviceMonitor", "ServiceMonitor"],
  ["podMonitor", "PodMonitor"],
  ["probe", "Probe"],
  ["scrapeConfig", "ScrapeConfig"],
] as const;
const clone = <T,>(v: T): T => JSON.parse(JSON.stringify(v));
const errorText = (e: unknown) =>
  e instanceof APIError ? e.message : "网络请求失败，请稍后重试";
const timestamp = (value?: string) =>
  value ? new Date(value).toLocaleString() : "尚无成功读取";
const stateText = (value?: string) =>
  ({
    Available: "可用",
    NotReady: "未就绪",
    Unknown: "未知",
    Suspended: "挂起",
    Disabled: "停用",
    Missing: "原 CR 不存在",
    QUEUED: "已受理",
    CHECKING: "检查中",
    DISPATCHED: "正在写入",
    SUCCEEDED: "CR 已写入",
    FAILED: "失败",
    WAITING_CONFIRMATION: "待确认",
    RESULT_UNCERTAIN: "结果待确认",
    IDENTITY_UNCERTAIN: "资源身份无法确认",
  })[value ?? ""] ??
  value ??
  "未知";
const terminal = new Set(["SUCCEEDED", "FAILED", "CANCELLED"]);
const defaultAgent = (): AgentConfiguration => ({
  name: "",
  namespace: "",
  desiredState: "Running",
  shards: 1,
  resources: { requestCPU: "200m", requestMemory: "512Mi", limitMemory: "1Gi" },
  storage: {
    mode: "PVC",
    capacity: "1Gi",
    whenScaled: "Retain",
    whenDeleted: "Retain",
  },
  serviceAccountName: "",
  associations: Object.fromEntries(
    kinds.map(([key]) => [
      key,
      { object: { mode: "None" }, namespace: { mode: "Own" } },
    ]),
  ) as AgentConfiguration["associations"],
  remoteWrite: [{ url: "", auth: { mode: "None" } }],
});
// 非活动模式的 selector/认证引用不透传；空的可选资源按省略处理，{} selector 与省略仍严格区分。
function canonical(config: AgentConfiguration): AgentConfiguration {
  const value = clone(config);
  value.resources = Object.fromEntries(
    Object.entries(value.resources).filter(([, v]) => v !== ""),
  );
  for (const [key] of kinds)
    for (const scope of ["object", "namespace"] as const) {
      const rule = value.associations[key][scope];
      if (rule.mode !== "Labels") delete rule.selector;
      else rule.selector ??= {};
    }
  value.remoteWrite = value.remoteWrite.map((item) => {
    const out: Endpoint = { url: item.url, auth: { mode: item.auth.mode } };
    if (item.name) out.name = item.name;
    if (item.remoteTimeout) out.remoteTimeout = item.remoteTimeout;
    if (item.auth.mode === "BasicAuth") {
      out.auth.username = item.auth.username;
      out.auth.password = item.auth.password;
    }
    if (item.auth.mode === "Bearer")
      out.auth.credentials = item.auth.credentials;
    if (item.tls && Object.values(item.tls).some(Boolean)) out.tls = item.tls;
    return out;
  });
  return value;
}

// 编辑草稿独立于运行/来源刷新，避免轮询回填未保存编辑或重建控件。
export function AgentEditor({
  selection,
  cluster,
  onAction,
  onChange,
  onClose,
  onCopy,
}: Props) {
  const [current, setCurrent] = useState(selection.instance),
    [config, setConfig] = useState<AgentConfiguration>(() =>
      selection.agentTemplate
        ? { ...clone(selection.agentTemplate), name: "" }
        : defaultAgent(),
    ),
    [draft, setDraft] = useState<Draft | null>(null),
    [saved, setSaved] = useState(""),
    [tab, setTab] = useState<Tab>(selection.tab ?? "draft"),
    [ready, setReady] = useState(!selection.instance),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [notice, setNotice] = useState(""),
    [namespaces, setNamespaces] = useState<string[]>([]),
    [namespaceError, setNamespaceError] = useState(""),
    [references, setReferences] = useState<Reference[]>([]),
    [referenceErrors, setReferenceErrors] = useState<Record<string, string>>(
      {},
    ),
    [runtime, setRuntime] = useState(selection.instance?.runtime),
    [runtimeSources, setRuntimeSources] = useState<Source[]>(
      selection.instance?.sources ?? [],
    ),
    [previewSources, setPreviewSources] = useState<{ draftRevision: Int64; sources: Source[] } | null>(null),
    [preview, setPreview] = useState<Preview | null>(null),
    [overwrite, setOverwrite] = useState(false),
    // 关键：列表已受理的删除仍是原任务，切到Agent抽屉不能丢掉其ID和后续只读核验。
    [operation, setOperation] = useState<Operation | null>(selection.operation ?? null),
    [history, setHistory] = useState<Operation[]>([]),
    [versions, setVersions] = useState<Version[]>([]),
    [viewed, setViewed] = useState<Version | null>(null);
  const currentRef = useRef(current),
    bodyRef = useRef<HTMLDivElement>(null),
    editingTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined),
    referenceEpoch = useRef(0),
    command = useRef<{
      key: ReturnType<typeof crypto.randomUUID>;
      signature: string;
    } | null>(null);
  const bookmark = useDraftBookmark(bodyRef);
  currentRef.current = current;
  const readOnly = Boolean(current?.readOnly),
    fixed = Boolean(current?.uid),
    dirty = JSON.stringify(canonical(config)) !== saved;
  async function run(work: () => Promise<void>) {
    setBusy(true);
    setError("");
    await onAction(async () => {
      try {
        await work();
      } catch (e) {
        setError(errorText(e));
        throw e;
      } finally {
        setBusy(false);
      }
    });
  }
  async function loadNamespaces() {
    try {
      const result = await api<{ namespaces: string[] }>(
        `/clusters/${cluster.id}/namespaces`,
      );
      setNamespaces(result.namespaces);
      setNamespaceError("");
      setConfig((previous) =>
        previous.namespace
          ? previous
          : {
              ...previous,
              namespace: result.namespaces.includes("monitoring")
                ? "monitoring"
                : (result.namespaces[0] ?? ""),
            },
      );
    } catch (e) {
      setNamespaceError(`${errorText(e)}；已保留 namespace 选择。`);
    }
  }
  // 关键：引用列表只含 name/key 元信息。并发 namespace 切换时忽略过期响应，读取失败不抹去已有引用。
  async function loadReferences(namespace: string) {
    if (!namespace || readOnly) return;
    const epoch = ++referenceEpoch.current;
    const results = await Promise.allSettled(
      ["Secret", "ServiceAccount"].map((kind) =>
        api<{ references: Reference[] }>(
          `/clusters/${cluster.id}/references?namespace=${encodeURIComponent(namespace)}&kind=${kind}`,
        ),
      ),
    );
    if (epoch !== referenceEpoch.current) return;
    setReferences((previous) =>
      results.flatMap((result, index) =>
        result.status === "fulfilled"
          ? result.value.references
          : previous.filter(
              (ref) =>
                ref.namespace === namespace &&
                ref.kind === ["Secret", "ServiceAccount"][index],
            ),
      ),
    );
    setReferenceErrors(
      Object.fromEntries(
        results.flatMap((result, index) =>
          result.status === "rejected"
            ? [[["Secret", "ServiceAccount"][index], errorText(result.reason)]]
            : [],
        ),
      ),
    );
    const accounts = results[1];
    if (accounts.status === "fulfilled")
      setConfig((previous) =>
        previous.namespace !== namespace || previous.serviceAccountName
          ? previous
          : {
              ...previous,
              serviceAccountName: accounts.value.references.some(
                (ref) => ref.name === "prometheus-agent",
              )
                ? "prometheus-agent"
                : (accounts.value.references[0]?.name ?? ""),
            },
      );
  }
  async function loadHistory(id: string) {
    const [a, b] = await Promise.all([
      api<{ versions: Version[] }>(`/instances/${id}/versions`),
      api<{ operations: Operation[] }>(
        `/operations?instanceId=${encodeURIComponent(id)}`,
      ),
    ]);
    setVersions(a.versions);
    setHistory(b.operations);
  }
  // 任务动作由共用LifecycleActions携原operation/attempt修订发出，不能把当前草稿当作重试来源。
  async function followOperation(value: Operation, showRuntime = false) {
    setOperation(value);
    if (showRuntime) setTab("runtime");
    await refresh();
    if (currentRef.current) await loadHistory(currentRef.current.id);
  }
  async function refresh() {
    const item = currentRef.current;
    if (!item) return;
    try {
      const next = await api<Runtime>(
        `/instances/${item.id}/${item.uid || item.readOnly ? "refresh" : "runtime"}`,
        item.uid || item.readOnly ? "POST" : "GET",
      );
      setRuntime(next);
      const detail = await api<Instance>(`/instances/${item.id}`);
      setCurrent(detail);
      setRuntimeSources(detail.sources ?? []);
      onChange();
    } catch (e) {
      setRuntime((previous) => ({
        summary: "Unknown",
        desiredState: "",
        deploymentMode: "",
        lastSuccessAt: previous?.lastSuccessAt ?? "",
        observedAt: "",
        error: errorText(e),
      }));
      setRuntimeSources(previous => previous.map(source => ({ ...source, historical: true })));
      setError(errorText(e));
    }
  }
  useEffect(() => {
    void loadNamespaces();
    if (selection.instance)
      void run(async () => {
        const item = selection.instance!;
        if (!item.readOnly) {
          const value = await api<Draft>(`/instances/${item.id}/draft`);
          setDraft(value);
          setConfig(clone(value.agent));
          setSaved(JSON.stringify(canonical(value.agent)));
          await loadHistory(item.id);
        }
        if (selection.versionId)
          setViewed(
            await api<Version>(
              `/instances/${item.id}/versions/${selection.versionId}`,
            ),
          );
        setReady(true);
      });
    return () => {
      referenceEpoch.current++;
      if (editingTimer.current) clearTimeout(editingTimer.current);
    };
  }, []);
  useEffect(() => {
    void loadReferences(config.namespace);
  }, [config.namespace]);
  useEffect(() => {
    const timer = setInterval(() => void refresh(), 10000);
    return () => clearInterval(timer);
  }, []);
  useEffect(() => {
    if (!operation || terminal.has(operation.state)) return;
    let active = true;
    const timer = setInterval(async () => {
      try {
        const result = await api<Operation>(
          `/operations/${operation.operationId}`,
        );
        if (!active) return;
        setOperation(result);
        if (terminal.has(result.state)) {
          await refresh();
          if (currentRef.current) await loadHistory(currentRef.current.id);
        }
      } catch (e) {
        if (active) setError(errorText(e));
      }
    }, 700);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [operation?.operationId, operation?.state]);
  function change(value: AgentConfiguration) {
    setConfig(value);
    setNotice("");
    setPreview(null);
    if (currentRef.current && !readOnly) {
      if (editingTimer.current) clearTimeout(editingTimer.current);
      editingTimer.current = setTimeout(() => {
        void api(
          `/instances/${currentRef.current!.id}/editing-activity`,
          "POST",
          {},
        ).catch((e) => setError(errorText(e)));
      }, 600);
    }
  }
  async function save(event: FormEvent) {
    event.preventDefault();
    await run(async () => {
      const value = canonical(config);
      let id = current?.id;
      if (!id) {
        const created = await api<Instance>(
          `/clusters/${cluster.id}/instances`,
          "POST",
          { component: "Prometheus Agent", agent: value },
        );
        id = created.id;
        setCurrent(created);
        setDraft(await api<Draft>(`/instances/${id}/draft`));
      } else
        setDraft(
          await api<Draft>(`/instances/${id}/draft`, "PUT", {
            expectedDraftRevision: draft!.revision,
            agent: value,
          }),
        );
      setSaved(JSON.stringify(value));
      setNotice("草稿已保存，尚未下发");
      onChange();
    });
  }
  // 关键：预览绑定已保存修订；确认丢响应始终复用同一命令键，不自动创建第二次发布。
  async function previewPublication() {
    if (!current || !draft || dirty) return;
    await run(async () => {
      setPreview(
        await api<Preview>(`/instances/${current.id}/previews`, "POST", {
          draftRevision: draft.revision,
        }),
      );
      setOverwrite(false);
    });
  }
  async function confirm() {
    if (!current || !preview) return;
    await run(async () => {
      const body = {
          previewId: preview.previewId,
          draftRevision: preview.draftRevision,
          overwriteDrift: overwrite,
        },
        signature = JSON.stringify(body);
      if (command.current?.signature !== signature)
        command.current = { key: crypto.randomUUID(), signature };
      const value = await api<Operation>(
        `/instances/${current.id}/publications`,
        "POST",
        body,
        command.current.key,
      );
      setOperation(value);
      setPreview(null);
      setTab("runtime");
      setNotice("已受理；CR 写入、组件就绪与远端接收分别判定。");
      await loadHistory(current.id);
      onChange();
    });
  }
  async function recover() {
    if (!current || !command.current) return;
    await run(async () => {
      const result = await api<{ operations: Operation[] }>(
        `/operations?instanceId=${encodeURIComponent(current.id)}&idempotencyKey=${encodeURIComponent(command.current!.key)}`,
      );
      if (result.operations[0]) {
        setOperation(result.operations[0]);
        setPreview(null);
        setTab("runtime");
        setNotice("已找回原受理操作。");
      } else setNotice("尚未找到已受理操作，可用原请求继续提交。");
    });
  }
  async function openVersion(id: string) {
    if (!current) return;
    await run(async () => {
      bookmark.capture();
      setViewed(await api<Version>(`/instances/${current.id}/versions/${id}`));
    });
  }
  function closeVersion() {
    setViewed(null);
    setTab("draft");
    bookmark.restore();
  }
  async function associationPreview() {
    if (!current || !draft || dirty) return;
    await run(async () => {
      const result = await api<{ draftRevision: Int64; sources: Source[] }>(
        `/instances/${current.id}/association-previews`,
        "POST",
        { draftRevision: draft.revision },
      );
      // 关键：草稿预览按修订隔离，运行页只使用实际CR来源。
      setPreviewSources(result);
    });
  }
  async function copy() {
    if (!current) return;
    await run(async () => {
      const value = await api<Draft>(`/instances/${current.id}/copy-template`);
      onCopy({ ...clone(value.agent), name: "" });
    });
  }
  return (
    <div className="drawer-backdrop">
      <aside
        className="instance-drawer"
        role="dialog"
        aria-modal="true"
        aria-label="实例详情"
      >
        <div className="drawer-header">
          <div>
            <p className="help">
              实例详情 · {cluster.name} · collector / Prometheus Agent
            </p>
            <h2>{current?.name || config.name || "新 Agent 实例"}</h2>
            <span className={`badge ${readOnly ? "gray" : "ok"}`}>
              {readOnly
                ? "平台未纳管 · 只读"
                : fixed
                  ? "平台纳管实例"
                  : "未部署草稿"}
            </span>
          </div>
          <button aria-label="关闭实例详情" onClick={onClose}>
            关闭 ×
          </button>
        </div>
        <div className="drawer-tabs" role="tablist" aria-label="实例信息">
          {(["draft", "runtime", "history", "yaml"] as Tab[]).map(
            (value, index) => (
              <button
                key={value}
                role="tab"
                aria-selected={tab === value && !viewed}
                disabled={readOnly && value === "draft"}
                onClick={() => {
                  bookmark.capture();
                  setViewed(null);
                  setTab(value);
                }}
              >
                {["配置草稿", "运行信息", "下发历史", "只读 YAML"][index]}
              </button>
            ),
          )}
        </div>
        <div
          className="drawer-body"
          ref={bodyRef}
          onFocusCapture={bookmark.remember}
          onBlurCapture={bookmark.remember}
          onSelectCapture={bookmark.remember}
        >
          {error && !preview && (
            <div className="alert error" role="alert">
              {error}
            </div>
          )}
          {notice && (
            <div className="alert" role="status">
              {notice}
            </div>
          )}
          {!ready && <p className="help">正在读取实例配置…</p>}
          {viewed ? (
            <section>
              <button onClick={closeVersion}>返回当前草稿</button>
              <span className="badge gray">只读发布版本</span>
              <h3>版本 {viewed.versionId.slice(0, 8)}</h3>
              <p className="help">
                来源草稿 v{viewed.sourceDraftRevision} ·{" "}
                {timestamp(viewed.createdAt)}
              </p>
              <AgentForm
                value={viewed.agent}
                onChange={() => {}}
                fixed
                disabled
                namespaces={[]}
                references={[]}
                errors={{}}
                onRefresh={() => {}}
              />
              <YAML text={viewed.yaml} />
            </section>
          ) : (
            ready && (
              <>
                {tab === "draft" && !readOnly && (
                  <form
                    id="agent-draft-form"
                    onSubmit={(event) => void save(event)}
                  >
                    <AgentForm
                      value={config}
                      onChange={change}
                      fixed={fixed}
                      disabled={busy}
                      namespaces={namespaces}
                      references={references}
                      errors={{
                        ...referenceErrors,
                        ...(namespaceError
                          ? { namespace: namespaceError }
                          : {}),
                      }}
                      onRefresh={() => {
                        void loadNamespaces();
                        void loadReferences(config.namespace);
                      }}
                    />
                    <section className="config-section">
                      <h3>采集关联匹配预览</h3>
                      <p className="help">
                        仅表示规则匹配资源，不代表实际采集或发送成功。保存后可独立刷新。
                      </p>
                      <button
                        type="button"
                        disabled={!draft || dirty || busy}
                        onClick={() => void associationPreview()}
                      >
                        刷新采集关联预览
                      </button>
                      {previewSources && <p className="help">草稿修订 {previewSources.draftRevision} 的匹配预览{dirty || draft?.revision !== previewSources.draftRevision ? " · 已过期，请保存并重新刷新" : ""}</p>}
                      <SourceViews sources={previewSources?.sources ?? []} />
                    </section>
                  </form>
                )}
                {tab === "runtime" && (
                  <section>
                    <div className="runtime-cards">
                      <div>
                        <span className="help">当前运行状态</span>
                        <strong>{stateText(runtime?.summary)}</strong>
                      </div>
                      <div>
                        <span className="help">可用 / 期望 Pod</span>
                        <strong>
                          {runtime?.availablePods !== undefined &&
                          runtime.desiredPods !== undefined
                            ? `${runtime.availablePods} / ${runtime.desiredPods}`
                            : "—"}
                        </strong>
                      </div>
                    </div>
                    <dl className="runtime-details">
                      <dt>部署模式</dt>
                      <dd>{runtime?.deploymentMode || "未知"}</dd>
                      <dt>实际 shards</dt>
                      <dd>{runtime?.shards ?? "未知"}</dd>
                      <dt>实际 paused</dt>
                      <dd>
                        {runtime?.paused === undefined
                          ? "未知"
                          : String(runtime.paused)}
                      </dd>
                      <dt>CR 最后成功观察</dt>
                      <dd>{timestamp(runtime?.lastSuccessAt)}</dd>
                    </dl>
                    {runtime?.error && (
                      <div className="alert error">{runtime.error}</div>
                    )}
                    {operation && (
                      <article className="alert">
                        <strong>{stateText(operation.state)}</strong>
                        <p>{operation.message}</p>
                        <p className="help">操作 {operation.operationId}</p>
                        <LifecycleActions operation={operation} onOperation={followOperation} />
                      </article>
                    )}
                    <p className="help">
                      CR 已写入不等于组件就绪；关联匹配和 Pod Ready
                      均不证明远端已收到样本。
                    </p>
                    <div className="row-actions">
                      <button
                        disabled={!current || busy}
                        onClick={() => void refresh()}
                      >
                        刷新当前状态
                      </button>
                      {!readOnly && current && (
                        <button onClick={() => void copy()}>
                          复制为新实例
                        </button>
                      )}
                    </div>
                    <SourceViews sources={runtimeSources} />
                  </section>
                )}
                {tab === "history" && (
                  <section>
                    <h3>下发历史</h3>
                    {readOnly ? (
                      <p className="help">平台未纳管实例没有平台发布历史。</p>
                    ) : (
                      <>
                        <button
                          onClick={() =>
                            current && void run(() => loadHistory(current.id))
                          }
                        >
                          刷新历史
                        </button>
                        {history.length === 0 && (
                          <p className="help">尚无下发历史</p>
                        )}
                        {history.map((item) => (
                          <article
                            className="publication-item"
                            key={item.operationId}
                          >
                            <strong>{stateText(item.state)}</strong>
                            <p>{item.message}</p>
                            <p className="help">
                              操作 {item.operationId} ·{" "}
                              {timestamp(item.createdAt)}
                            </p>
                            {item.versionId && (
                              <button
                                className="version-link"
                                onClick={() =>
                                  void openVersion(item.versionId!)
                                }
                              >
                                查看冻结版本 {item.versionId.slice(0, 8)}
                              </button>
                            )}
                            <LifecycleActions operation={item} onOperation={value => followOperation(value, true)} />
                          </article>
                        ))}
                        <h3>发布版本</h3>
                        {versions.map((version) => (
                          <p key={version.versionId}>
                            <button
                              className="version-link"
                              onClick={() =>
                                void openVersion(version.versionId)
                              }
                            >
                              版本 {version.versionId.slice(0, 8)}
                            </button>{" "}
                            · 来源草稿 v{version.sourceDraftRevision}
                          </p>
                        ))}
                      </>
                    )}
                  </section>
                )}
                {tab === "yaml" && (
                  <section>
                    <h3>
                      {readOnly
                        ? "集群当前 CR YAML"
                        : `已保存草稿 YAML${draft ? ` · v${draft.revision}` : ""}`}
                    </h3>
                    {dirty && !readOnly && (
                      <p className="help">当前未保存编辑仍保留在配置草稿中。</p>
                    )}
                    <YAML
                      text={
                        readOnly ? (current?.yaml ?? "") : (draft?.yaml ?? "")
                      }
                    />
                  </section>
                )}
              </>
            )
          )}
        </div>
        {!viewed && !readOnly && tab === "draft" && (
          <div className="drawer-footer">
            <div>
              <span className={`badge ${dirty ? "warn" : "gray"}`}>
                {dirty
                  ? "有未保存修改"
                  : draft
                    ? `草稿 v${draft.revision} 已保存`
                    : "未保存草稿"}
              </span>
              <p className="help">保存不会改变集群</p>
            </div>
            <div className="row-actions">
              {current?.currentVersionId && (
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => void openVersion(current.currentVersionId)}
                >
                  查看当前使用版本 {current.currentVersionId.slice(0, 8)}
                </button>
              )}
              <button
                type="submit"
                form="agent-draft-form"
                disabled={
                  !ready ||
                  busy ||
                  !config.name ||
                  !config.namespace ||
                  !config.serviceAccountName
                }
              >
                保存草稿
              </button>
              <button
                className="primary"
                disabled={busy || !draft || dirty}
                onClick={() => void previewPublication()}
              >
                预览下发
              </button>
            </div>
          </div>
        )}
        {preview && (
          <div className="backdrop preview-backdrop">
            <section
              className="dialog yaml-dialog"
              role="dialog"
              aria-modal="true"
              aria-label={`下发预览 · ${current?.name}`}
            >
              <div className="page-title">
                <div>
                  <h2>下发预览 · {current?.name}</h2>
                  <p className="help">
                    已保存草稿 v{preview.draftRevision}；确认后冻结发布版本。
                  </p>
                </div>
                <button
                  aria-label="关闭下发预览"
                  onClick={() => setPreview(null)}
                >
                  ×
                </button>
              </div>
              {error && (
                <div role="alert" className="alert error">
                  {error}
                </div>
              )}
              <h3>CR YAML 对比</h3>
              {preview.warnings.map((warning, index) => (
                <p className="alert" key={index}>
                  {warning}
                </p>
              ))}
              {preview.drift && (
                <label className="checkbox-line">
                  <input
                    type="checkbox"
                    checked={overwrite}
                    onChange={(event) => setOverwrite(event.target.checked)}
                  />
                  明确覆盖当前配置漂移并清除右侧未包含的额外 spec
                </label>
              )}
              <StorageImpact value={preview}/><YAMLDiff left={preview.leftYAML} right={preview.rightYAML} />
              <div className="dialog-footer">
                {command.current && (
                  <button disabled={busy} onClick={() => void recover()}>
                    找回已受理操作
                  </button>
                )}
                <button disabled={busy} onClick={() => setPreview(null)}>
                  返回编辑
                </button>
                <button
                  className="primary"
                  disabled={busy || (preview.drift && !overwrite)}
                  onClick={() => void confirm()}
                >
                  确认下发
                </button>
              </div>
            </section>
          </div>
        )}
      </aside>
    </div>
  );
}

// 配置表单只开放首期单 shard/PVC 运行形态；既有 CR 的身份和存储不可原地迁移。
function AgentForm({
  value,
  onChange,
  fixed,
  disabled,
  namespaces,
  references,
  errors,
  onRefresh,
}: {
  value: AgentConfiguration;
  onChange: (value: AgentConfiguration) => void;
  fixed: boolean;
  disabled: boolean;
  namespaces: string[];
  references: Reference[];
  errors: Record<string, string>;
  onRefresh: () => void;
}) {
  const patch = (next: Partial<AgentConfiguration>) =>
    onChange({ ...value, ...next });
  const updateEndpoint = (index: number, next: Endpoint) =>
    patch({
      remoteWrite: value.remoteWrite.map((item, i) =>
        i === index ? next : item,
      ),
    });
  const accounts = references
    .filter((item) => item.kind === "ServiceAccount")
    .map((item) => item.name);
  const namespaceOptions = [
    ...new Set([...namespaces, ...(value.namespace ? [value.namespace] : [])]),
  ];
  return (
    <fieldset className="ksm-fields" disabled={disabled}>
      <div className="field-grid">
        <label>
          名称
          <input
            aria-label="名称"
            required
            disabled={fixed}
            maxLength={Math.min(49,63-11-(value.shards>1?`-shard-${value.shards-1}`.length:0)-2)}
            pattern="[a-z0-9](([a-z0-9]|-)*[a-z0-9])?"
            placeholder="例如 metrics-agent"
            value={value.name}
            onChange={(event) => patch({ name: event.target.value })}
          />
          <span className="help">
            直接作为 CR name；小写英文、数字和连字符。名称长度须容纳所选 shards 的派生资源，单 shard 最长49字符。
          </span>
        </label>
        <label>
          Agent namespace
          <select
            aria-label="Agent namespace"
            required
            disabled={fixed}
            value={value.namespace}
            onChange={(event) =>
              patch({ namespace: event.target.value, serviceAccountName: "" })
            }
          >
            <option value="">请选择已有 namespace</option>
            {namespaceOptions.map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </select>
        </label>
      </div>
      <AgentLifecycleFields value={value} patch={patch}/>
      <section className="config-section">
        <h3>每个 prometheus 容器的资源声明</h3>
        <div className="resources-grid">
          <span />
          <span className="help">request</span>
          <span className="help">limit（可不填）</span>
          {(["CPU", "Memory"] as const).map((kind) => (
            <div className="resource-row" key={kind}>
              <span>{kind === "CPU" ? "CPU" : "内存"}</span>
              {(["request", "limit"] as const).map((type) => {
                const key = `${type}${kind}` as keyof Resources;
                return (
                  <input
                    key={key}
                    aria-label={`Agent ${kind === "CPU" ? "CPU" : "内存"} ${type}`}
                    value={value.resources[key] ?? ""}
                    onChange={(event) =>
                      patch({
                        resources: {
                          ...value.resources,
                          [key]: event.target.value,
                        },
                      })
                    }
                  />
                );
              })}
            </div>
          ))}
        </div>
        <p className="help">
          200m / 512Mi / limit 1Gi 为可修改资源声明，不代表实际利用率。
        </p>
      </section>
      <AgentStorageFields value={value} patch={patch} fixed={fixed}/>
      <section className="config-section">
        <div className="page-title">
          <h3>既有依赖</h3>
          <button type="button" onClick={onRefresh}>
            刷新已有依赖
          </button>
        </div>
        {Object.entries(errors).map(([kind, message]) => (
          <div className="alert error" role="alert" key={kind}>
            {kind}：{message}；已保留当前引用。
          </div>
        ))}
        <label>
          ServiceAccount
          <select
            aria-label="ServiceAccount"
            required
            value={value.serviceAccountName}
            onChange={(event) =>
              patch({ serviceAccountName: event.target.value })
            }
          >
            <option value="">请选择同 namespace 已有 ServiceAccount</option>
            {[
              ...new Set([
                ...accounts,
                ...(value.serviceAccountName ? [value.serviceAccountName] : []),
              ]),
            ].map((name) => (
              <option key={name} value={name}>
                {name}
                {!accounts.includes(name) ? "（当前未确认）" : ""}
              </option>
            ))}
          </select>
        </label>
        <p className="help">
          平台不创建 SA、Secret 或采集 RBAC；缺依赖或权限会阻止发布。
        </p>
      </section>
      <section className="config-section">
        <h3>采集关联标签规则</h3>
        <p className="help">
          每类独立设置对象与 namespace 规则。默认 None / Own，不修改已有资源
          labels；匹配预览不等于实际采集目标。
        </p>
        {kinds.map(([key, label]) => (
          <details className="publication-item" key={key} open>
            <summary>
              <strong>{label}</strong>
            </summary>
            {(["object", "namespace"] as const).map((scope) => {
              const rule = value.associations[key][scope],
                prefix = `${label} ${scope === "object" ? "对象" : "namespace"}`;
              return (
                <div key={scope}>
                  <label>
                    {prefix}模式
                    <select
                      aria-label={`${prefix}模式`}
                      value={rule.mode}
                      onChange={(event) =>
                        patch({
                          associations: {
                            ...value.associations,
                            [key]: {
                              ...value.associations[key],
                              [scope]: {
                                ...rule,
                                mode: event.target.value,
                                ...(event.target.value === "Labels"
                                  ? { selector: rule.selector ?? {} }
                                  : {}),
                              },
                            },
                          },
                        })
                      }
                    >
                      {(scope === "object"
                        ? ["None", "All", "Labels"]
                        : ["Own", "All", "Labels"]
                      ).map((mode) => (
                        <option key={mode} value={mode}>
                          {mode} ·{" "}
                          {
                            (
                              {
                                None: "不关联",
                                Own: "Agent 自身 namespace",
                                All: "全部",
                                Labels: "标签规则",
                              } as Record<string, string>
                            )[mode]
                          }
                        </option>
                      ))}
                    </select>
                  </label>
                  {rule.mode === "Labels" && (
                    <SelectorFields
                      prefix={prefix}
                      value={rule.selector ?? {}}
                      onChange={(selector) =>
                        patch({
                          associations: {
                            ...value.associations,
                            [key]: {
                              ...value.associations[key],
                              [scope]: { mode: "Labels", selector },
                            },
                          },
                        })
                      }
                    />
                  )}
                </div>
              );
            })}
          </details>
        ))}
      </section>
      <section className="config-section">
        <h3>remoteWrite</h3>
        <p className="help">
          至少一个合法 HTTP(S) endpoint，URL 不能含 userinfo。所有认证/TLS
          只选既有 Secret 引用；平台不回显或持久化 Secret 值。
        </p>
        {value.remoteWrite.map((item, index) => {
          const prefix = `remoteWrite ${index + 1}`,
            tls = item.tls;
          return (
            <section className="publication-item" key={index}>
              <div className="page-title">
                <h4>{prefix}</h4>
                <button
                  type="button"
                  disabled={value.remoteWrite.length === 1}
                  onClick={() =>
                    patch({
                      remoteWrite: value.remoteWrite.filter(
                        (_, i) => i !== index,
                      ),
                    })
                  }
                >
                  删除 endpoint {index + 1}
                </button>
              </div>
              <label>
                {prefix} URL
                <input
                  aria-label={`${prefix} URL`}
                  type="url"
                  required
                  value={item.url}
                  placeholder="https://接收端/api/v1/write"
                  onChange={(event) =>
                    updateEndpoint(index, { ...item, url: event.target.value })
                  }
                />
              </label>
              <div className="field-grid">
                <label>
                  name（可选且唯一）
                  <input
                    aria-label={`${prefix} name`}
                    value={item.name ?? ""}
                    onChange={(event) =>
                      updateEndpoint(index, {
                        ...item,
                        name: event.target.value,
                      })
                    }
                  />
                </label>
                <label>
                  remoteTimeout（可选）
                  <input
                    aria-label={`${prefix} remoteTimeout`}
                    value={item.remoteTimeout ?? ""}
                    placeholder="例如 30s"
                    onChange={(event) =>
                      updateEndpoint(index, {
                        ...item,
                        remoteTimeout: event.target.value,
                      })
                    }
                  />
                </label>
              </div>
              <label>
                认证模式
                <select
                  aria-label={`${prefix} 认证模式`}
                  value={item.auth.mode}
                  onChange={(event) =>
                    updateEndpoint(index, {
                      ...item,
                      auth: { ...item.auth, mode: event.target.value },
                    })
                  }
                >
                  <option value="None">None · 无认证</option>
                  <option value="BasicAuth">BasicAuth · Secret 引用</option>
                  <option value="Bearer">Bearer · Secret 引用</option>
                </select>
              </label>
              {item.auth.mode === "BasicAuth" &&
                (["username", "password"] as const).map((key) => (
                  <SecretField
                    key={key}
                    label={`${prefix} ${key}`}
                    value={item.auth[key]}
                    references={references}
                    onChange={(ref) =>
                      updateEndpoint(index, {
                        ...item,
                        auth: { ...item.auth, [key]: ref },
                      })
                    }
                  />
                ))}
              {item.auth.mode === "Bearer" && (
                <SecretField
                  label={`${prefix} credentials`}
                  value={item.auth.credentials}
                  references={references}
                  onChange={(ref) =>
                    updateEndpoint(index, {
                      ...item,
                      auth: { ...item.auth, credentials: ref },
                    })
                  }
                />
              )}
              <label className="checkbox-line">
                <input
                  type="checkbox"
                  checked={Boolean(tls)}
                  onChange={(event) =>
                    updateEndpoint(index, {
                      ...item,
                      tls: event.target.checked ? {} : undefined,
                    })
                  }
                />
                配置 TLS Secret 引用 {index + 1}
              </label>
              {tls && (
                <div>
                  <p className="help">
                    TLS 校验始终开启；客户端证书与私钥必须成对。可仅使用 Secret
                    CA 或 serverName。
                  </p>
                  {(["ca", "cert", "key"] as const).map((key) => (
                    <div key={key}>
                      <label className="checkbox-line">
                        <input
                          type="checkbox"
                          checked={Boolean(tls[key])}
                          onChange={(event) => {
                            const next = { ...tls };
                            if (event.target.checked)
                              next[key] = { name: "", key: "" };
                            else delete next[key];
                            updateEndpoint(index, { ...item, tls: next });
                          }}
                        />
                        使用 {prefix} TLS {key}
                      </label>
                      {tls[key] && (
                        <SecretField
                          label={`${prefix} TLS ${key}`}
                          value={tls[key]}
                          references={references}
                          onChange={(ref) =>
                            updateEndpoint(index, {
                              ...item,
                              tls: { ...tls, [key]: ref },
                            })
                          }
                        />
                      )}
                    </div>
                  ))}
                  <label>
                    serverName
                    <input
                      aria-label={`${prefix} TLS serverName`}
                      value={tls.serverName ?? ""}
                      onChange={(event) =>
                        updateEndpoint(index, {
                          ...item,
                          tls: {
                            ...tls,
                            serverName: event.target.value || undefined,
                          },
                        })
                      }
                    />
                  </label>
                </div>
              )}
            </section>
          );
        })}
        <button
          type="button"
          onClick={() =>
            patch({
              remoteWrite: [
                ...value.remoteWrite,
                { url: "", auth: { mode: "None" } },
              ],
            })
          }
        >
          添加 remoteWrite endpoint
        </button>
      </section>
    </fieldset>
  );
}

// 标签编辑保留空对象 {} 的显式语义；表达式只支持 Kubernetes LabelSelector 的四种 operator。
function SelectorFields({
  value,
  prefix,
  onChange,
}: {
  value: Selector;
  prefix: string;
  onChange: (value: Selector) => void;
}) {
  const labels = Object.entries(value.matchLabels ?? {}),
    expressions = value.matchExpressions ?? [];
  return (
    <div>
      <h4>{prefix} matchLabels</h4>
      {labels.map(([key, label], index) => (
        <div className="field-grid" key={index}>
          <label>
            label key
            <input
              aria-label={`${prefix} label ${index + 1} key`}
              value={key}
              required
              onChange={(event) => {
                const next = event.target.value;
                if (
                  next !== key &&
                  Object.hasOwn(value.matchLabels ?? {}, next)
                ) {
                  event.target.setCustomValidity("标签 key 不能重复");
                  return;
                }
                event.target.setCustomValidity("");
                const entries = [...labels];
                entries[index] = [next, label];
                onChange({
                  ...value,
                  matchLabels: Object.fromEntries(entries),
                });
              }}
            />
          </label>
          <label>
            label value
            <input
              aria-label={`${prefix} label ${index + 1} value`}
              value={label}
              onChange={(event) =>
                onChange({
                  ...value,
                  matchLabels: Object.fromEntries(
                    labels.map((row, i) =>
                      i === index ? [key, event.target.value] : row,
                    ),
                  ),
                })
              }
            />
          </label>
          <button
            type="button"
            onClick={() =>
              onChange({
                ...value,
                matchLabels: Object.fromEntries(
                  labels.filter((_, i) => i !== index),
                ),
              })
            }
          >
            删除 {prefix} label {index + 1}
          </button>
        </div>
      ))}
      <button
        type="button"
        onClick={() =>
          onChange({ ...value, matchLabels: { ...value.matchLabels, "": "" } })
        }
      >
        添加 {prefix} label
      </button>
      <h4>{prefix} matchExpressions</h4>
      {expressions.map((expression, index) => (
        <div className="publication-item" key={index}>
          <label>
            key
            <input
              aria-label={`${prefix} expression ${index + 1} key`}
              required
              value={expression.key}
              onChange={(event) =>
                onChange({
                  ...value,
                  matchExpressions: expressions.map((item, i) =>
                    i === index ? { ...item, key: event.target.value } : item,
                  ),
                })
              }
            />
          </label>
          <label>
            operator
            <select
              aria-label={`${prefix} expression ${index + 1} operator`}
              value={expression.operator}
              onChange={(event) =>
                onChange({
                  ...value,
                  matchExpressions: expressions.map((item, i) =>
                    i === index
                      ? {
                          ...item,
                          operator: event.target.value,
                          ...(["Exists", "DoesNotExist"].includes(
                            event.target.value,
                          )
                            ? { values: undefined }
                            : {}),
                        }
                      : item,
                  ),
                })
              }
            >
              {["In", "NotIn", "Exists", "DoesNotExist"].map((operator) => (
                <option key={operator}>{operator}</option>
              ))}
            </select>
          </label>
          {["In", "NotIn"].includes(expression.operator) && (
            <label>
              values（每行一项）
              <textarea
                aria-label={`${prefix} expression ${index + 1} values`}
                value={(expression.values ?? []).join("\n")}
                required
                onChange={(event) =>
                  onChange({
                    ...value,
                    matchExpressions: expressions.map((item, i) =>
                      i === index
                        ? { ...item, values: event.target.value.split("\n") }
                        : item,
                    ),
                  })
                }
              />
            </label>
          )}
          <button
            type="button"
            onClick={() =>
              onChange({
                ...value,
                matchExpressions: expressions.filter((_, i) => i !== index),
              })
            }
          >
            删除 {prefix} expression {index + 1}
          </button>
        </div>
      ))}
      <button
        type="button"
        onClick={() =>
          onChange({
            ...value,
            matchExpressions: [
              ...expressions,
              { key: "", operator: "In", values: [] },
            ],
          })
        }
      >
        添加 {prefix} expression
      </button>
      <p className="help">
        空标签规则显式表示 {"{}"}；None/Own/All 模式不携带 selector。
      </p>
    </div>
  );
}

// Secret 选择器只消费元信息，缺失/失败保留原引用并明确标记，绝不请求 Secret data。
function SecretField({
  label,
  value,
  references,
  onChange,
}: {
  label: string;
  value?: SecretRef;
  references: Reference[];
  onChange: (value: SecretRef) => void;
}) {
  const secrets = references.filter((item) => item.kind === "Secret"),
    names = [
      ...new Set([
        ...secrets.map((item) => item.name),
        ...(value?.name ? [value.name] : []),
      ]),
    ],
    keys = secrets.find((item) => item.name === value?.name)?.keys ?? [];
  return (
    <div className="field-grid">
      <label>
        {label} Secret
        <select
          aria-label={`${label} Secret`}
          value={value?.name ?? ""}
          required
          onChange={(event) => onChange({ name: event.target.value, key: "" })}
        >
          <option value="">请选择已有 Secret</option>
          {names.map((name) => (
            <option key={name} value={name}>
              {name}
              {!secrets.some((item) => item.name === name)
                ? "（当前未确认）"
                : ""}
            </option>
          ))}
        </select>
      </label>
      <label>
        {label} key
        <select
          aria-label={`${label} key`}
          value={value?.key ?? ""}
          required
          onChange={(event) =>
            onChange({ name: value?.name ?? "", key: event.target.value })
          }
        >
          <option value="">请选择已有 key</option>
          {[...new Set([...keys, ...(value?.key ? [value.key] : [])])].map(
            (key) => (
              <option key={key} value={key}>
                {key}
                {!keys.includes(key) ? "（当前未确认）" : ""}
              </option>
            ),
          )}
        </select>
      </label>
    </div>
  );
}

// 各来源分别显示成功/尝试时间；失败或历史集合只能作历史参考，不能伪装当前零匹配。
function SourceViews({ sources }: { sources: Source[] }) {
  if (sources.length === 0)
    return <p className="help">尚未读取来源；当前匹配与 PVC 状态未知。</p>;
  return (
    <div>
      {sources.map((source) => (
        <SourceHistory key={source.source} source={source}>
          {source.matches !== undefined && (
            <>
              <p>
                {source.error || source.historical ? "历史匹配" : "当前匹配"}{" "}
                {source.matches.length} 个资源（不代表采集成功）
              </p>
              {source.matches.map((item) => (
                <p className="help" key={item.uid}>
                  {item.kind} · {item.namespace}/{item.name}
                </p>
              ))}
            </>
          )}
          <StoragePolicies policies={source.policies} mode={source.storageMode}/>
          {source.volumes !== undefined && (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>PVC / namespace</th>
                    <th>状态 / 请求容量</th>
                    <th>StorageClass</th>
                    <th>挂载 Pod</th><th>策略 / 独立结果</th>
                  </tr>
                </thead>
                <tbody>
                  {source.volumes.map((volume) => (
                    <tr key={volume.uid}>
                      <td>
                        {volume.name}
                        <div className="help">{volume.namespace}</div>
                      </td>
                      <td>
                        {volume.phase} / {volume.requestedStorage}
                        <div className="help">
                          {volume.accessModes.join(", ")}
                        </div>
                      </td>
                      <td>{volume.storageClass || "—"}</td>
                      <td>
                        {volume.mountedByPods.join(", ") || "未观察到挂载"}
                      </td><td><StorageVolumeOutcome volume={volume}/></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {source.matches === undefined && source.volumes === undefined && !source.runtimeSnapshot && !source.policies && !source.storageMode && (
            <p className="help">当前读取结果未知</p>
          )}
        </SourceHistory>
      ))}
    </div>
  );
}
