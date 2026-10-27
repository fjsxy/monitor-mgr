import { HistoricalMigration } from "./historical-migration";
import { InstanceManagement } from "./instance-management";
import { InstanceIdentity, type InstanceIdentityValue } from "./instance-identity";
import { SourceHistory, type SourceStatus } from "./source-history";
import { useDraftBookmark } from "./draft-bookmark";
import {StorageImpact} from "./agent-storage";
import type { Int64 } from "./api";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { api, APIError, type Account } from "./api";
import type { Cluster } from "./clusters";
import { ClusterPicker } from "./cluster-picker";
import { YAML, YAMLDiff } from "./instance-yaml";
import { AgentEditor, type AgentConfiguration } from "./agent";
import {LifecycleActions,type Operation,type DeletionPreview} from "./lifecycle";

// KSM 页面只保存类型化草稿；所有身份、权限与发布保护仍由真实 RPC 执行。
type Resources = {
  requestCPU?: string;
  requestMemory?: string;
  limitCPU?: string;
  limitMemory?: string;
};
type Role = { mode: "Inherit" | "Override"; resources?: Resources };
type KSM = {
  name: string;
  targetNamespace: string;
  deploymentMode: string;
  desiredState: string;
  resources: Resources;
  collectors: { mode: string; names?: string[] };
  metricFilter: { mode: string; patterns?: string[] };
  namespaceFilter: { mode: string; names?: string[]; namePatterns?: string[] };
  advancedResources: boolean;
  shards?: number;
  nodeResources?: Role;
  clusterResources?: Role;
  unscheduledResources?: Role;
  serviceMonitor?: { enabled: boolean; interval: string; scrapeTimeout: string };
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
};
type Instance = InstanceIdentityValue & {
  id: string;
  clusterId: string;
  name: string;
  targetNamespace: string;
  component: string;
  type: string;
  readOnly: boolean;
  uid: string;
  lifecycle: string;
  currentVersionId: string;
  activityAt: string;
  runtime?: Runtime;
  ownership: string;
  yaml?: string;
  sources?: SourceStatus[];
};
type Statistics = { managed: number; unmanaged: number; available: number; blocked: number };
type Pagination = { number: number; size: number; total: number; pages: number };
type QueryResult = { instances: Instance[]; statistics: Statistics; page: Pagination };
type Draft = { revision: Int64; ksm: KSM; yaml: string };
type Version = {
  versionId: string;
  instanceId: string;
  sourceDraftRevision: Int64;
  ksm: KSM;
  yaml: string;
  createdAt: string;
};
type Preview = {
  previewId: string;
  draftRevision: Int64;
  leftYAML: string;
  rightYAML: string;
  drift: boolean;
  warnings: string[];
};
type Selection = {
  key: string;
  instance?: Instance;
  template?: KSM;
  versionId?: string;
  tab?: Tab;
  component?: string;
  agentTemplate?: AgentConfiguration;
  operation?:Operation;
};
type Tab = "draft" | "runtime" | "history" | "yaml";
const collectors = [
  "pods",
  "deployments",
  "statefulsets",
  "daemonsets",
  "nodes",
  "namespaces",
  "services",
  "persistentvolumeclaims",
  "persistentvolumes",
  "jobs",
  "cronjobs",
  "replicasets",
  "configmaps",
  "secrets",
  "endpoints",
  "ingresses",
  "horizontalpodautoscalers",
  "certificatesigningrequests",
  "leases",
  "limitranges",
  "mutatingwebhookconfigurations",
  "networkpolicies",
  "poddisruptionbudgets",
  "replicationcontrollers",
  "resourcequotas",
  "storageclasses",
  "validatingwebhookconfigurations",
  "volumeattachments",
];
const terminal = new Set(["SUCCEEDED", "FAILED", "CANCELLED"]);
const uncertain = new Set([
  "WAITING_CONFIRMATION",
  "RESULT_UNCERTAIN",
  "IDENTITY_UNCERTAIN",
  "DELETION_VERIFYING",
  "DELETION_BLOCKED",
]);
const defaultConfig = (): KSM => ({
  name: "",
  targetNamespace: "",
  deploymentMode: "Deployment",
  desiredState: "Running",
  resources: {
    requestCPU: "100m",
    requestMemory: "250Mi",
    limitMemory: "500Mi",
  },
  collectors: { mode: "Default" },
  metricFilter: { mode: "All" },
  namespaceFilter: { mode: "All" },
  advancedResources: false,
  serviceMonitor: {enabled: true, interval: "30s", scrapeTimeout: "10s"},
});
const clone = <T,>(value: T): T => JSON.parse(JSON.stringify(value)) as T;
// 后端明确字段错误保留原文；浏览器网络异常统一中文，避免直接暴露英文实现提示。
const errorMessage = (error: unknown) =>
  error instanceof APIError ? error.message : "网络请求失败，请稍后重试";
const stateName = (value: string) =>
  (
    ({
      Unknown: "未知",
      Available: "可用",
      NotReady: "未就绪",
      Suspended: "挂起",
      Disabled: "停用",
      NoMatch: "无匹配",
      Missing: "原 CR 不存在",
      Deleted:"已删除",
      DELETION_VERIFYING:"清理核验中",
      HISTORICAL_RESOLVED:"历史删除已显式收尾",
      DELETION_BLOCKED:"清理受阻",
      QUEUED: "已受理",
      CHECKING: "检查中",
      DISPATCHED: "正在写入",
      SUCCEEDED: "CR 已写入",
      FAILED: "失败",
      WAITING_CONFIRMATION: "待确认",
      RESULT_UNCERTAIN: "结果待确认",
      IDENTITY_UNCERTAIN: "资源身份无法确认",
      CANCELLED: "已取消",
    }) as Record<string, string>
  )[value] ?? value;
const desiredName = (value: string) =>
  (
    ({ Running: "运行", Suspended: "挂起", Disabled: "停用" }) as Record<
      string,
      string
    >
  )[value] ?? "未知";
const modeName = (value: string) =>
  (
    ({
      Deployment: "Deployment · 单副本",
      StatefulSetSharded: "StatefulSet · 分片",
      DaemonSetSharded: "DaemonSet · 三角色分片",
    }) as Record<string, string>
  )[value] ?? "未知";
const stamp = (value?: string) =>
  value ? new Date(value).toLocaleString() : "尚无成功观察";
const versionName = (id: string) => id.slice(0, 8);

// 关键：隐藏的角色配置保留在草稿中；只有有效模式字段进入服务端配置映射。
function requestConfig(config: KSM): KSM {
  const value = clone(config);
  if (value.deploymentMode !== "StatefulSetSharded") delete value.shards;
  value.resources = cleanResources(value.resources);
  for (const key of [
    "nodeResources",
    "clusterResources",
    "unscheduledResources",
  ] as const)
    if (value[key]?.resources)
      value[key]!.resources = cleanResources(value[key]!.resources!);
  if (value.collectors.mode === "Default") delete value.collectors.names;
  if (value.metricFilter.mode === "All") delete value.metricFilter.patterns;
  else value.metricFilter.patterns = cleanLines(value.metricFilter.patterns);
  if (value.namespaceFilter.mode === "All") {
    delete value.namespaceFilter.names;
    delete value.namespaceFilter.namePatterns;
  } else {
    value.namespaceFilter.names = [
      ...new Set(value.namespaceFilter.names ?? []),
    ];
    value.namespaceFilter.namePatterns = cleanLines(
      value.namespaceFilter.namePatterns,
    );
  }
  return value;
}
function cleanResources(value: Resources): Resources {
  return Object.fromEntries(
    Object.entries(value)
      .filter(([, v]) => v?.trim())
      .map(([k, v]) => [k, v!.trim()]),
  ) as Resources;
}
function cleanLines(values?: string[]) {
  return (values ?? []).map((value) => value.trim()).filter(Boolean);
}

// 列表刷新只替换观测结果；编辑器使用稳定 key，避免轮询重建抽屉和输入控件。
export function Instances({
  account,
  onAction,
}: {
  account: Account;
  onAction: (work: () => Promise<void>) => Promise<void>;
}) {
  const [clusters, setClusters] = useState<Cluster[]>([]),
    [accounts, setAccounts] = useState<Account[]>([]),
    [clusterId, setClusterId] = useState(""),
    [instances, setInstances] = useState<Instance[]>([]),
    [selection, setSelection] = useState<Selection | null>(null),
    [adding, setAdding] = useState(false),
    [addingType, setAddingType] = useState("exporter"),
    [error, setError] = useState(""),
    [loading, setLoading] = useState(false),
    [query, setQuery] = useState(""),
    [summaryFilter, setSummaryFilter] = useState(""),
    [typeFilter, setTypeFilter] = useState(""),
    [componentFilter, setComponentFilter] = useState(""),
    [pageNumber, setPageNumber] = useState(1),
    [pageSize, setPageSize] = useState(20),
    [statistics, setStatistics] = useState<Statistics>({ managed: 0, unmanaged: 0, available: 0, blocked: 0 }),
    [pagination, setPagination] = useState<Pagination>({ number: 1, size: 20, total: 0, pages: 0 }),
    [deleting, setDeleting] = useState<Instance | null>(null),
    [deletePreview,setDeletePreview]=useState<DeletionPreview|null>(null),
    [deletePreviewError,setDeletePreviewError]=useState("");
  const current = clusters.find((cluster) => cluster.id === clusterId);
  const currentId = useRef(clusterId);
  currentId.current = clusterId;
  const requestSequence = useRef(0);
  const queryState = useRef({ query, summaryFilter, typeFilter, componentFilter, pageNumber, pageSize });
  queryState.current = { query, summaryFilter, typeFilter, componentFilter, pageNumber, pageSize };
  const username = (id: string) =>
    accounts.find((item) => item.id === id)?.username ??
    (id === account.id ? account.username : id);
  async function loadClusters() {
    const result = await api<{ clusters: Cluster[] }>("/clusters");
    const active = result.clusters.filter((item) => item.state === "ACTIVE");
    setClusters(active);
    setClusterId((previous) =>
      active.some((item) => item.id === previous)
        ? previous
        : (active[0]?.id ?? ""),
    );
    if (account.role === "ADMIN")
      setAccounts((await api<{ accounts: Account[] }>("/accounts")).accounts);
  }
  // 关键：始终携带分页参数进入授权全集查询；轮询读取最新条件，迟到响应不能覆盖新筛选。
  async function load(id = clusterId) {
    if (!id) return;
    const sequence = ++requestSequence.current;
    const value = queryState.current;
    const params = new URLSearchParams({ page: String(value.pageNumber), pageSize: String(value.pageSize) });
    for (const [key, text] of Object.entries({ name: value.query, summary: value.summaryFilter, type: value.typeFilter, component: value.componentFilter }))
      if (text) params.set(key, text);
    try {
      const result = await api<QueryResult>(`/clusters/${id}/instances?${params}`);
      if (currentId.current !== id || requestSequence.current !== sequence) return;
      setInstances(result.instances);
      setStatistics(result.statistics);
      setPagination(result.page);
      setError("");
    } catch (e) {
      if (currentId.current !== id || requestSequence.current !== sequence) return;
      setError(errorMessage(e));
      if (e instanceof APIError && ["FORBIDDEN", "UNAUTHENTICATED"].includes(e.code)) {
        setInstances([]);
        setSelection(null);
        void onAction(async () => { throw e; });
      }
    }
  }
  useEffect(() => {
    void onAction(async () => {
      setLoading(true);
      try {
        await loadClusters();
      } finally {
        setLoading(false);
      }
    });
  }, []);
  useEffect(() => {
    setInstances([]);
    setSelection(null);
    setPageNumber(1);
    setStatistics({ managed: 0, unmanaged: 0, available: 0, blocked: 0 });
  }, [clusterId]);
  useEffect(() => {
    if (clusterId) void load(clusterId);
  }, [clusterId, query, summaryFilter, typeFilter, componentFilter, pageNumber, pageSize]);
  useEffect(() => {
    if (!clusterId) return;
    const timer = setInterval(() => void load(clusterId), 10000);
    return () => clearInterval(timer);
  }, [clusterId]);
  function open(instance: Instance, tab: Tab = "draft", versionId?: string) {
    setSelection({
      key: crypto.randomUUID(),
      instance,
      tab: instance.readOnly ? "runtime" : tab,
      versionId,
    });
  }
  // 后端已经按持久活动序号排序，前端不能用时间戳或当前页重新排序。
  const ordered = instances;
  async function openDeletion(item:Instance){
    setDeleting(item);setDeletePreview(null);setDeletePreviewError("");
    if(item.uid)try{setDeletePreview(await api<DeletionPreview>(`/instances/${item.id}/deletion-previews`,"POST",{}))}
    catch(e){setDeletePreviewError(e instanceof APIError?e.message:"删除影响读取失败，请重新打开预览")}
  }
  async function removeDraft() {
    if (!deleting) return;
    await onAction(async () => {
      if(deleting.uid){if(!deletePreview)return;const result=await api<Operation>(`/instances/${deleting.id}/deletions`,"POST",{versionId:deletePreview.versionId,previewId:deletePreview.previewId});setSelection({key:crypto.randomUUID(),instance:deleting,tab:"runtime",operation:result})}
      else await api(`/instances/${deleting.id}`, "DELETE");
      setDeleting(null);
      await load();
    });
  }
  return (
    <section className="instances-page">
      <div className="page-title">
        <div>
          <h1>观测实例</h1>
          <p className="muted">先查看实例的运行与版本，选择一行后展开配置。</p>
        </div>
        <button onClick={() => void load()} disabled={!current || loading}>
          刷新列表
        </button>
      </div>
      <div className="cluster-toolbar">
        <ClusterPicker clusters={clusters} value={clusterId} ownerName={username} onChange={id => {
          setInstances([]); setSelection(null); setClusterId(id);
        }} />
        <button
          className="primary"
          onClick={() => setAdding(true)}
          disabled={!current}
        >
          添加
        </button>
      </div>
      {error && (
        <div role="alert" className="alert error">
          {error}
        </div>
      )}
      <div className="instance-stats">
        <div>
          <span className="help">平台纳管实例</span>
          <strong>{statistics.managed}</strong>
        </div>
        <div>
          <span className="help">当前可用</span>
          <strong>{statistics.available}</strong>
        </div>
        <div>
          <span className="help">待确认 / 受阻任务</span>
          <strong>{statistics.blocked}</strong>
        </div>
        <div>
          <span className="help">平台未纳管实例</span>
          <strong>{statistics.unmanaged}</strong>
        </div>
      </div>
      <div className="instance-filters">
        <label>
          名称搜索
          <input
            value={query}
            onChange={(event) => { setPageNumber(1); setQuery(event.target.value); }}
            placeholder="输入名称关键词"
          />
        </label>
        <label>
          类型
          <select
            aria-label="类型"
            value={typeFilter}
            onChange={(event) => {
              setPageNumber(1);
              setTypeFilter(event.target.value);
              setComponentFilter("");
            }}
          >
            <option value="">全部类型</option>
            <option value="exporter">exporter</option>
            <option value="collector">collector</option>
          </select>
        </label>
        <label>
          组件
          <select
            aria-label="组件"
            value={componentFilter}
            onChange={(event) => { setPageNumber(1); setComponentFilter(event.target.value); }}
          >
            <option value="">全部组件</option>
            {typeFilter !== "collector" && <option value="kube-state-metrics">kube-state-metrics</option>}
            {typeFilter !== "exporter" && <option value="Prometheus Agent">Prometheus Agent</option>}
          </select>
        </label>
        <label>
          运行状态
          <select
            aria-label="运行状态"
            value={summaryFilter}
            onChange={(event) => { setPageNumber(1); setSummaryFilter(event.target.value); }}
          >
            <option value="">全部状态</option>
            {[
              "Available",
              "NotReady",
              "Unknown",
              "Suspended",
              "Disabled",
              "NoMatch",
              "Missing",
            ].map((value) => (
              <option key={value} value={value}>
                {stateName(value)}
              </option>
            ))}
          </select>
        </label>
        <button
          onClick={() => {
            setPageNumber(1);
            setQuery("");
            setTypeFilter("");
            setComponentFilter("");
            setSummaryFilter("");
          }}
          disabled={!query && !typeFilter && !componentFilter && !summaryFilter}
        >
          清除筛选
        </button>
      </div>
      <p className="help">
        <span>共 {pagination.total} 个实例</span> · 本页 {instances.length} 个
      </p>
      <div className="table-wrap instance-table">
        <table>
          <thead>
            <tr>
              <th>名称/命名空间</th>
              <th>类型/组件</th>
              <th>运行状态</th>
              <th>可用 / 期望 Pod</th>
              <th>版本</th>
              <th>最后成功观察</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            {ordered.map((item) => (
              <tr key={item.id}>
                <td>
                  <strong>{item.name}</strong>
                  <div className="help">{item.targetNamespace}</div>
                  <InstanceIdentity value={item} compact />
                  {item.readOnly && (
                    <span className="badge gray">平台未纳管 · 只读</span>
                  )}
                </td>
                <td>
                  <strong>{item.type}</strong>
                  <div className="help">{item.component}</div>
                </td>
                <td>
                  <RuntimeBadge runtime={item.runtime} />
                </td>
                <td>
                  {item.runtime?.availablePods !== undefined &&
                  item.runtime.desiredPods !== undefined
                    ? `${item.runtime.availablePods} / ${item.runtime.desiredPods}`
                    : "—"}
                </td>
                <td>
                  {item.currentVersionId &&
                  !item.readOnly &&
                  item.lifecycle !== "DELETED" ? (
                    <button
                      className="version-link"
                      title={item.currentVersionId}
                      onClick={() => open(item, "draft", item.currentVersionId)}
                    >
                      {versionName(item.currentVersionId)}
                    </button>
                  ) : (
                    "—"
                  )}
                </td>
                <td>
                  {item.runtime?.lastSuccessAt ? (
                    <>
                      <span>
                        {new Date(
                          item.runtime.lastSuccessAt,
                        ).toLocaleTimeString()}
                      </span>
                      <div className="help">
                        {stamp(item.runtime.lastSuccessAt)}
                      </div>
                    </>
                  ) : (
                    "尚无成功观察"
                  )}
                </td>
                <td>
                  <div className="row-actions">
                    <button onClick={() => open(item)}>
                      {item.canManage ? "编辑" : "查看"}
                    </button>
                    <button
                      onClick={() => open(item, "draft")}
                      disabled={!item.canManage}
                      title={item.readOnlyReason}
                    >
                      下发
                    </button>
                    <button
                      className="danger"
                      disabled={!item.canManage || item.lifecycle==="DELETED"}
                      title={item.readOnlyReason || "删除原 UID 对应的实例"}
                      onClick={() => void openDeletion(item)}
                    >
                      删除
                    </button>
                  </div>
                </td>
              </tr>
            ))}
            {ordered.length === 0 && (
              <tr>
                <td colSpan={7} className="empty-state">
                  {current
                    ? "没有符合条件的观测实例"
                    : "请先注册有权访问的集群"}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      {adding && (
        <div className="backdrop">
          <section
            className="dialog"
            role="dialog"
            aria-modal="true"
            aria-labelledby="add-instance-title"
          >
            <button
              className="close"
              aria-label="关闭"
              onClick={() => setAdding(false)}
            >
              ×
            </button>
            <h2 id="add-instance-title">添加观测实例</h2>
            <label>
              类型
              <select
                aria-label="类型"
                value={addingType}
                onChange={(event) => setAddingType(event.target.value)}
              >
                <option value="exporter">exporter</option>
                <option value="collector">collector</option>
              </select>
            </label>
            <label>
              组件
              <select
                aria-label="组件"
                value={
                  addingType === "collector"
                    ? "Prometheus Agent"
                    : "kube-state-metrics"
                }
                disabled
              >
                <option
                  value={
                    addingType === "collector"
                      ? "Prometheus Agent"
                      : "kube-state-metrics"
                  }
                >
                  {addingType === "collector"
                    ? "Prometheus Agent"
                    : "kube-state-metrics"}
                </option>
              </select>
            </label>
            <p className="help">
              先填写配置，保存后建立平台草稿；下发需要单独确认。
            </p>
            <div className="dialog-footer">
              <button onClick={() => setAdding(false)}>取消</button>
              <button
                className="primary"
                onClick={() => {
                  setAdding(false);
                  setSelection({
                    key: crypto.randomUUID(),
                    component:
                      addingType === "collector"
                        ? "Prometheus Agent"
                        : "kube-state-metrics",
                  });
                }}
              >
                配置实例
              </button>
            </div>
          </section>
        </div>
      )}
      <nav className="instance-pagination" aria-label="实例分页">
        <label>每页条数<select aria-label="每页条数" value={pageSize} onChange={event => { setPageNumber(1); setPageSize(parseInt(event.target.value, 10)); }}>
          {[20, 50, 100].map(size => <option key={size} value={size}>{size}</option>)}
        </select></label>
        <span className="help">第 {pagination.number} / {pagination.pages || 1} 页</span>
        <button disabled={pagination.number <= 1} onClick={() => setPageNumber(pagination.number - 1)}>上一页</button>
        <button disabled={pagination.pages === 0 || pagination.number >= pagination.pages} onClick={() => setPageNumber(pagination.number + 1)}>下一页</button>
      </nav>
      {selection &&
        current &&
        (selection.instance?.component === "Prometheus Agent" ||
        selection.component === "Prometheus Agent" ? (
          <AgentEditor
            key={selection.key}
            selection={selection}
            cluster={current}
            isAdmin={account.role === "ADMIN"}
            onAction={onAction}
            onChange={() => void load()}
            onClose={() => setSelection(null)}
            onCopy={(agentTemplate) =>
              setSelection({
                key: crypto.randomUUID(),
                component: "Prometheus Agent",
                agentTemplate,
              })
            }
          />
        ) : (
          <Editor
            key={selection.key}
            selection={selection}
            cluster={current}
            isAdmin={account.role === "ADMIN"}
            onAction={onAction}
            onChange={() => void load()}
            onClose={() => setSelection(null)}
            onCopy={(template) =>
              setSelection({ key: crypto.randomUUID(), template })
            }
          />
        ))}
      {deleting && (
        <div className="backdrop">
          <section
            className="dialog"
            role="dialog"
            aria-modal="true"
            aria-labelledby="delete-draft-title"
          >
            <h2 id="delete-draft-title">删除实例</h2>
            <p>
              名称：<strong>{deleting.name}</strong>
            </p>
            <p className="help">命名空间：{deletePreview?.namespace??deleting.targetNamespace}</p>
            {deleting.uid&&<p className="help">{deletePreview?`已确认影响范围：${deletePreview.impactNamespaces.join("、")} · 原部署版本 ${deletePreview.versionId}`:deletePreviewError||"正在读取原身份和删除影响…"}</p>}
            {deletePreview&&<StorageImpact value={deletePreview}/> }
            <p>{deleting.uid?"确认按原 UID 删除对应 CR。只有 CR、对应 workload 和 Pod 全部核验消失才完成；平台保留配置与历史，不移除 finalizer 或直接删除 PVC。":"删除尚未部署的配置草稿，集群资源不会变化。"}</p>
            <div className="dialog-footer">
              <button onClick={() => setDeleting(null)}>取消</button>
              <button className="danger" disabled={!!deleting.uid&&!deletePreview} onClick={() => void removeDraft()}>
                确认删除实例
              </button>
            </div>
          </section>
        </div>
      )}
    </section>
  );
}

// 抽屉中的配置状态独立于运行轮询，保存冲突或读取失败均不覆盖本地编辑。
function Editor({
  isAdmin,
  selection,
  cluster,
  onAction,
  onChange,
  onClose,
  onCopy,
}: {
  isAdmin: boolean;
  selection: Selection;
  cluster: Cluster;
  onAction: (work: () => Promise<void>) => Promise<void>;
  onChange: () => void;
  onClose: () => void;
  onCopy: (template: KSM) => void;
}) {
  const [current, setCurrent] = useState<Instance | undefined>(
      selection.instance,
    ),
    [config, setConfig] = useState<KSM>(() =>
      selection.template
        ? { ...clone(selection.template), name: "", targetNamespace: "" }
        : defaultConfig(),
    ),
    [draft, setDraft] = useState<Draft | null>(null),
    [saved, setSaved] = useState(""),
    [tab, setTab] = useState<Tab>(selection.tab ?? "draft"),
    [namespaces, setNamespaces] = useState<string[]>([]),
    [namespaceError, setNamespaceError] = useState(""),
    [error, setError] = useState(""),
    [notice, setNotice] = useState(""),
    [busy, setBusy] = useState(false),
    [ready, setReady] = useState(!selection.instance),
    [runtime, setRuntime] = useState<Runtime | undefined>(
      selection.instance?.runtime,
    ),
    [preview, setPreview] = useState<Preview | null>(null),
    [overwrite, setOverwrite] = useState(false),
    [operation, setOperation] = useState<Operation | null>(selection.operation??null),
    [history, setHistory] = useState<Operation[]>([]),
    [versions, setVersions] = useState<Version[]>([]),
    [viewedVersion, setViewedVersion] = useState<Version | null>(null),
    [namespaceOpen, setNamespaceOpen] = useState(false);
  const canManage = !current || current.canManage;
  const readOnly = Boolean(current?.readOnly);
  const fixed = Boolean(current?.uid);
  const dirty = JSON.stringify(requestConfig(config)) !== saved;
  const bodyRef = useRef<HTMLDivElement>(null),
    editingTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined),
    currentRef = useRef(current),
    commandRef = useRef<{
      key: ReturnType<typeof crypto.randomUUID>;
      signature: string;
    } | null>(null);
  const bookmark = useDraftBookmark(bodyRef);
  currentRef.current = current;
  async function run(work: () => Promise<void>) {
    setError("");
    setBusy(true);
    await onAction(async () => {
      try {
        await work();
      } catch (e) {
        setError(errorMessage(e));
        throw e;
      } finally {
        setBusy(false);
      }
    });
  }
  // 关键：读取失败保留缓存选项和已有选择，避免一次网络失败改写采集范围。
  async function loadNamespaces() {
    try {
      const result = await api<{ namespaces: string[] }>(
        `/clusters/${cluster.id}/namespaces`,
      );
      setNamespaces(result.namespaces);
      setNamespaceError("");
      setConfig((previous) =>
        previous.targetNamespace
          ? previous
          : {
              ...previous,
              targetNamespace: result.namespaces.includes("monitoring")
                ? "monitoring"
                : (result.namespaces[0] ?? ""),
            },
      );
    } catch (e) {
      setNamespaceError(
        `namespace 列表读取失败：${errorMessage(e)}；已保留当前选择。`,
      );
    }
  }
  async function loadHistory(id: string) {
    const [versionResult, operationResult] = await Promise.all([
      api<{ versions: Version[] }>(`/instances/${id}/versions`),
      api<{ operations: Operation[] }>(
        `/operations?instanceId=${encodeURIComponent(id)}`,
      ),
    ]);
    setVersions(versionResult.versions);
    setHistory(operationResult.operations);
  }
  async function refreshRuntime() {
    const item = currentRef.current;
    if (!item) return;
    try {
      const value = await api<Runtime>(
        `/instances/${item.id}/${item.uid || item.readOnly ? "refresh" : "runtime"}`,
        item.uid || item.readOnly ? "POST" : "GET",
      );
      setRuntime(value);
      const detail = await api<Instance>(`/instances/${item.id}`);
      setCurrent(detail);
      onChange();
    } catch (e) {
      setRuntime((previous) => ({
        summary: "Unknown",
        desiredState: "",
        deploymentMode: "",
        error: errorMessage(e),
        lastSuccessAt: previous?.lastSuccessAt ?? "",
        observedAt: new Date().toISOString(),
      }));
      setCurrent(previous => previous ? { ...previous, sources: previous.sources?.map(source => ({ ...source, historical: true })) } : previous);
      setError(errorMessage(e));
    }
  }
  useEffect(() => {
    let active = true;
    void loadNamespaces();
    if (selection.instance)
      void run(async () => {
        const item = selection.instance!;
        if (!item.readOnly) {
          const value = await api<Draft>(`/instances/${item.id}/draft`);
          if (!active) return;
          setDraft(value);
          setConfig(clone(value.ksm));
          setSaved(JSON.stringify(requestConfig(value.ksm)));
          await loadHistory(item.id);
        }
        if (selection.versionId) {
          const value = await api<Version>(
            `/instances/${item.id}/versions/${selection.versionId}`,
          );
          if (active) setViewedVersion(value);
        }
        if (active) setReady(true);
      });
    return () => {
      active = false;
      if (editingTimer.current) clearTimeout(editingTimer.current);
    };
  }, []);
  useEffect(() => {
    const timer = setInterval(() => void refreshRuntime(), 10000);
    return () => clearInterval(timer);
  }, []);
  useEffect(() => {
    if (!operation || terminal.has(operation.state)) return;
    let active = true;
    const timer = setInterval(async () => {
      try {
        const next = await api<Operation>(
          `/operations/${operation.operationId}`,
        );
        if (!active) return;
        setOperation(next);
        if (terminal.has(next.state)) {
          await refreshRuntime();
          if (currentRef.current) await loadHistory(currentRef.current.id);
        }
      } catch (e) {
        if (active) setError(errorMessage(e));
      }
    }, 700);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [operation?.operationId, operation?.state]);
  function change(next: KSM) {
    setConfig(next);
    setNotice("");
    setPreview(null);
    if (currentRef.current && canManage) {
      if (editingTimer.current) clearTimeout(editingTimer.current);
      editingTimer.current = setTimeout(() => {
        void api(
          `/instances/${currentRef.current!.id}/editing-activity`,
          "POST",
          {},
        ).catch((e) => setError(errorMessage(e)));
      }, 600);
    }
  }
  async function save(event?: FormEvent) {
    event?.preventDefault();
    if (!canManage) return;
    await run(async () => {
      const value = requestConfig(config);
      let id = current?.id;
      if (!id) {
        const created = await api<Instance>(
          `/clusters/${cluster.id}/instances`,
          "POST",
          { component: "kube-state-metrics", ksm: value },
        );
        id = created.id;
        setCurrent(created);
        const result = await api<Draft>(`/instances/${id}/draft`);
        setDraft(result);
      } else {
        const result = await api<Draft>(`/instances/${id}/draft`, "PUT", {
          expectedDraftRevision: draft!.revision,
          ksm: value,
        });
        setDraft(result);
      }
      setSaved(JSON.stringify(value));
      setNotice("草稿已保存，尚未下发");
      onChange();
    });
  }
  // 关键：预览只能绑定已保存修订，不能把未保存编辑静默换成另一个发布版本。
  async function previewPublication() {
    if (!current || !draft || dirty) {
      setError("请先保存当前配置，再预览下发。");
      return;
    }
    await run(async () => {
      setPreview(
        await api<Preview>(`/instances/${current.id}/previews`, "POST", {
          draftRevision: draft.revision,
        }),
      );
      setOverwrite(false);
    });
  }
  // 关键：丢响应后复用原命令键找回受理结果，不用新键制造第二次发布。
  async function confirmPublication() {
    if (!current || !preview) return;
    await run(async () => {
      const body = {
        previewId: preview.previewId,
        draftRevision: preview.draftRevision,
        overwriteDrift: overwrite,
      };
      const signature = JSON.stringify(body);
      if (commandRef.current?.signature !== signature)
        commandRef.current = { key: crypto.randomUUID(), signature };
      const result = await api<Operation>(
        `/instances/${current.id}/publications`,
        "POST",
        body,
        commandRef.current!.key,
      );
      setOperation(result);
      setPreview(null);
      setTab("runtime");
      setNotice("已受理；CR 写入结果与组件运行状态分别更新。");
      await loadHistory(current.id);
      onChange();
    });
  }
  async function recoverPublication() {
    if (!current || !commandRef.current) return;
    await run(async () => {
      const result = await api<{ operations: Operation[] }>(
        `/operations?instanceId=${encodeURIComponent(current.id)}&idempotencyKey=${encodeURIComponent(commandRef.current!.key)}`,
      );
      if (result.operations[0]) {
        setOperation(result.operations[0]);
        setPreview(null);
        setTab("runtime");
        setNotice("已找回原受理操作。");
      } else setNotice("当前尚未找到已受理操作，可使用原请求继续提交。");
    });
  }
  // 冻结版本单独呈现；返回时恢复草稿和原滚动位置，不覆盖未保存输入。
  async function openVersion(id: string) {
    if (!current) return;
    await run(async () => {
      bookmark.capture();
      setViewedVersion(
        await api<Version>(`/instances/${current.id}/versions/${id}`),
      );
    });
  }
  function closeVersion() {
    setViewedVersion(null);
    setTab("draft");
    bookmark.restore();
  }
  async function copy() {
    if (!current) return;
    await run(async () => {
      const value = await api<Draft>(`/instances/${current.id}/copy-template`);
      onCopy({ ...clone(value.ksm), name: "", targetNamespace: "" });
    });
  }
  const effectiveNamespaces = [
    ...new Set([...namespaces, ...(config.namespaceFilter.names ?? [])]),
  ];
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
            <p className="help">实例详情 · {cluster.name}</p>
            <h2>{current?.name || config.name || "新 KSM 实例"}</h2>
            {current && <><InstanceIdentity value={current} /><InstanceManagement value={current} clusterActive={cluster.state === "ACTIVE"} isAdmin={isAdmin} onConfirmed={next => { setCurrent(next); onChange(); }} /></>}
            <span className={"badge " + (readOnly ? "gray" : "ok")}>
              {readOnly
                ? "平台未纳管 · 只读"
                : current?.uid
                  ? "平台纳管实例"
                  : "未部署草稿"}
            </span>
          </div>
          <button onClick={onClose} aria-label="关闭实例详情">
            关闭 ×
          </button>
        </div>
        <div className="drawer-tabs" role="tablist" aria-label="实例信息">
          {(
            [
              ["draft", "配置草稿"],
              ["runtime", "运行信息"],
              ["history", "变更历史"],
              ["yaml", "只读 YAML"],
            ] as [Tab, string][]
          ).map(([value, label]) => (
            <button
              key={value}
              role="tab"
              aria-selected={tab === value && !viewedVersion}
              disabled={readOnly && value === "draft"}
              onClick={() => {
                bookmark.capture();
                setViewedVersion(null);
                setTab(value);
              }}
            >
              {label}
            </button>
          ))}
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
          {viewedVersion ? (
            <section className="frozen-version">
              <div className="row-actions">
                <button onClick={closeVersion}>返回当前草稿</button>
                <span className="badge gray">只读发布版本</span>
              </div>
              <h3>版本 {versionName(viewedVersion.versionId)}</h3>
              <p className="help">
                来源草稿 v{viewedVersion.sourceDraftRevision} ·{" "}
                {stamp(viewedVersion.createdAt)}
              </p>
              <p className="help">
                下列为发布时冻结的来源配置，当前草稿的编辑不会改变此版本。
              </p>
              <Configuration
                config={viewedVersion.ksm}
                onChange={() => {}}
                disabled
                fixed
                namespaces={namespaces}
                namespacesError=""
                namespaceOpen={false}
                onNamespaceOpen={() => {}}
                onRefreshNamespaces={() => {}}
              />
              <h3>该版本 CR YAML</h3>
              <YAML text={viewedVersion.yaml} />
            </section>
          ) : (
            <>
              {ready && tab === "draft" && !readOnly && (
                <form id="ksm-draft-form" onSubmit={save}>
                  <Configuration
                    config={config}
                    onChange={change}
                    disabled={busy || !canManage}
                    fixed={fixed}
                    namespaces={effectiveNamespaces}
                    namespacesError={namespaceError}
                    namespaceOpen={namespaceOpen}
                    onNamespaceOpen={() => setNamespaceOpen((value) => !value)}
                    onRefreshNamespaces={() => void loadNamespaces()}
                  />
                </form>
              )}
              {tab === "runtime" && (
                <section>
                  <div className="runtime-cards">
                    <div>
                      <span className="help">当前运行状态</span>
                      <RuntimeBadge runtime={runtime} />
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
                    <dd>{modeName(runtime?.deploymentMode ?? "")}</dd>
                    <dt>集群当前运行意图</dt>
                    <dd>{desiredName(runtime?.desiredState ?? "")}</dd>
                    <dt>最后成功观察</dt>
                    <dd>{stamp(runtime?.lastSuccessAt)}</dd>
                  </dl>
                  {!current?.uid && !readOnly && (
                    <p className="help">尚未部署；保存草稿不会改变集群。</p>
                  )}
                  {runtime?.error && (
                    <div className="alert error">{runtime.error}</div>
                  )}
                  {(current?.sources ?? []).map(source => <SourceHistory key={source.source} source={source} />)}
                  {operation && (
                    <div
                      className={
                        "alert " +
                        (uncertain.has(operation.state) ||
                        operation.state === "FAILED"
                          ? "error"
                          : "")
                      }
                    >
                      <strong>{stateName(operation.state)}</strong>
                      <p>{operation.message}</p>
                      <p className="help">
                        操作 {operation.operationId}
                        {operation.attemptId &&
                          ` · 执行尝试 ${operation.attemptId}`}
                      </p>
                      <LifecycleActions canManage={canManage} operation={operation} onOperation={async next=>{setOperation(next);setTab("runtime");await refreshRuntime();if(current)await loadHistory(current.id)}}/>
                    </div>
                  )}
                  <div className="row-actions">
                    <button
                      onClick={() => void refreshRuntime()}
                      disabled={!current || busy}
                    >
                      刷新当前状态
                    </button>
                    {!readOnly && current && (
                      <button onClick={() => void copy()}>复制为新实例</button>
                    )}
                  </div>
                </section>
              )}
              {tab === "history" && (
                <section>
                  <div className="page-title">
                    <h3>变更历史</h3>
                    <button
                      onClick={() =>
                        current && void run(() => loadHistory(current.id))
                      }
                      disabled={!current || busy}
                    >
                      刷新历史
                    </button>
                  </div>
                  {current&&!readOnly&&<HistoricalMigration key={current.id} instanceId={current.id} isAdmin={isAdmin} active={cluster.state==="ACTIVE"} onUpdated={async()=>{setCurrent(await api<Instance>(`/instances/${current.id}`));await loadHistory(current.id);onChange()}}/>}
                  {readOnly ? (
                    <p className="help">平台未纳管实例没有平台发布历史。</p>
                  ) : (
                    <>
                      {history.length === 0 && (
                        <p className="help">尚无下发历史</p>
                      )}
                      {history.map((item) => (
                        <article
                          className="publication-item"
                          key={item.operationId}
                        >
                          <span
                            className={
                              "badge " +
                              (item.state === "SUCCEEDED" ? "ok" : "gray")
                            }
                          >
                            {stateName(item.state)}
                          </span>
                          <p>{item.message}</p>
                          <p className="help">{stamp(item.createdAt)}</p>
                          {item.versionId && (
                            <button
                              className="version-link"
                              onClick={() => void openVersion(item.versionId!)}
                            >
                              查看冻结版本 {versionName(item.versionId)}
                            </button>
                          )}
                          <p className="help">{item.action==="DELETE"?"删除":"下发"}操作 {item.operationId}</p>
                          <LifecycleActions canManage={canManage} operation={item} onOperation={async next=>{setOperation(next);setTab("runtime");if(current)await loadHistory(current.id);onChange()}}/>
                        </article>
                      ))}
                      <h3>发布版本</h3>
                      {[...versions].reverse().map((value) => (
                        <p key={value.versionId}>
                          <button
                            className="version-link"
                            onClick={() => void openVersion(value.versionId)}
                          >
                            版本 {versionName(value.versionId)}
                          </button>
                          <span className="help">
                            {" "}
                            · 来源草稿 v{value.sourceDraftRevision} ·{" "}
                            {stamp(value.createdAt)}
                          </span>
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
                    <p className="help">
                      此处为已保存配置；尚未保存的编辑保留在配置草稿中。
                    </p>
                  )}
                  <YAML
                    text={
                      readOnly ? (current?.yaml ?? "") : (draft?.yaml ?? "")
                    }
                  />
                </section>
              )}
            </>
          )}
        </div>
        {!viewedVersion && canManage && tab === "draft" && (
          <div className="drawer-footer">
            <div>
              <span className={"badge " + (dirty ? "warn" : "gray")}>
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
                form="ksm-draft-form"
                disabled={
                  busy || !ready || !config.name || !config.targetNamespace
                }
              >
                保存草稿
              </button>
              <button
                className="primary"
                onClick={() => void previewPublication()}
                disabled={busy || !draft || dirty}
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
              aria-labelledby="publication-preview-title"
            >
              <div className="page-title">
                <div>
                  <h2 id="publication-preview-title">
                    下发预览 · {current?.name}
                  </h2>
                  <p className="help">
                    已保存草稿 v{preview.draftRevision}
                    。确认后固定发布版本，后续编辑不会改变该任务。
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
                <div className="alert error" role="alert">
                  {error}
                </div>
              )}
              <h3>CR YAML 对比</h3>
              <p className="help">
                左侧集群原 CR，右侧将下发 CR；红色为删除，绿色为新增。
              </p>
              {preview.warnings.map((warning, index) => (
                <p className="alert" key={index}>
                  {warning}
                </p>
              ))}
              {preview.drift && (
                <label className="checkbox-line drift-confirm">
                  <input
                    type="checkbox"
                    checked={overwrite}
                    onChange={(event) => setOverwrite(event.target.checked)}
                  />
                  明确覆盖当前配置漂移并清除右侧未包含的额外 spec
                </label>
              )}
              <YAMLDiff left={preview.leftYAML} right={preview.rightYAML} />
              <div className="dialog-footer">
                {commandRef.current && (
                  <button
                    onClick={() => void recoverPublication()}
                    disabled={busy}
                  >
                    找回已受理操作
                  </button>
                )}
                <button onClick={() => setPreview(null)} disabled={busy}>
                  返回编辑
                </button>
                <button
                  className="primary"
                  onClick={() => void confirmPublication()}
                  disabled={busy || !canManage || (preview.drift && !overwrite)}
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

// 表单中的选择只改变本地配置；名称和部署位置在首次创建后固定。
function Configuration({
  config,
  onChange,
  disabled,
  fixed,
  namespaces,
  namespacesError,
  namespaceOpen,
  onNamespaceOpen,
  onRefreshNamespaces,
}: {
  config: KSM;
  onChange: (value: KSM) => void;
  disabled: boolean;
  fixed: boolean;
  namespaces: string[];
  namespacesError: string;
  namespaceOpen: boolean;
  onNamespaceOpen: () => void;
  onRefreshNamespaces: () => void;
}) {
  const patch = (value: Partial<KSM>) => onChange({ ...config, ...value });
  const toggle = (values: string[] | undefined, name: string) =>
    values?.includes(name)
      ? values.filter((value) => value !== name)
      : [...(values ?? []), name];
  function role(
    key: "nodeResources" | "clusterResources" | "unscheduledResources",
    mode: "Inherit" | "Override",
  ) {
    const previous = config[key];
    patch({
      [key]: {
        mode,
        resources: previous?.resources ?? clone(config.resources),
      },
    });
  }
  const targetOptions = [
    ...new Set([
      ...namespaces,
      ...(config.targetNamespace ? [config.targetNamespace] : []),
    ]),
  ];
  return (
    <fieldset className="ksm-fields" disabled={disabled}>
      <div className="field-grid">
        <label>
          名称
          <input
            aria-label="名称"
            value={config.name}
            onChange={(event) => patch({ name: event.target.value })}
            disabled={fixed}
            placeholder="例如 cluster-metrics"
            required
            maxLength={253}
            pattern="[a-z0-9](([a-z0-9]|-|[.])*[a-z0-9])?"
          />
          <span className="help">
            直接作为 CR name，使用小写英文、数字、连字符和点。
          </span>
        </label>
        <label>
          运行意图（保存后需明确下发）
          <select
            aria-label="运行意图（保存后需明确下发）"
            value={config.desiredState}
            onChange={(event) => patch({ desiredState: event.target.value })}
          >
            <option value="Running">运行</option>
            <option value="Suspended">挂起</option>
            <option value="Disabled">停用</option>
          </select>
        </label>
        <label>
          部署命名空间
          <select
            aria-label="部署命名空间"
            value={config.targetNamespace}
            onChange={(event) => patch({ targetNamespace: event.target.value })}
            disabled={fixed}
            required
          >
            <option value="">请选择已有 namespace</option>
            {targetOptions.map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </select>
          <span className="help">
            平台不创建 namespace；首次创建 CR 后固定。
          </span>
        </label>
        <label>
          部署模式
          <select
            aria-label="部署模式"
            value={config.deploymentMode}
            onChange={(event) =>
              patch({
                deploymentMode: event.target.value,
                ...(event.target.value === "StatefulSetSharded"
                  ? { shards: config.shards ?? 2 }
                  : {}),
              })
            }
          >
            <option value="Deployment">Deployment · 单副本</option>
            <option value="StatefulSetSharded">
              StatefulSetSharded · 分片
            </option>
            <option value="DaemonSetSharded">
              DaemonSetSharded · 三角色分片
            </option>
          </select>
          <span className="help">
            切换模式会重建工作负载，期间可能出现采集空窗。
          </span>
        </label>
      </div>
      {config.deploymentMode === "StatefulSetSharded" && (
        <label>
          shards
          <input
            type="number"
            min={2}
            step={1}
            value={config.shards ?? 2}
            onChange={(event) => patch({ shards: Number(event.target.value) })}
            required
          />
        </label>
      )}
      <section className="config-section">
        <h3>每个 kube-state-metrics 容器的资源声明</h3>
        <ResourceFields
          value={config.resources}
          prefix="通用"
          onChange={(resources) => patch({ resources })}
        />
        <p className="help">
          CPU request 100m，内存 request 250Mi / limit 500Mi
          为可修改预设，不表示实际利用率或容量验证。
        </p>
      </section>
      {config.deploymentMode === "DaemonSetSharded" && (
        <section className="config-section">
          <h3>DaemonSet 三角色资源</h3>
          <label className="checkbox-line">
            <input
              type="checkbox"
              checked={config.advancedResources}
              onChange={(event) =>
                patch({ advancedResources: event.target.checked })
              }
            />
            启用高级资源配置
          </label>
          <p className="help">
            关闭时保留草稿值，各角色继承通用配置；独立配置按整块替换，不逐字段继承。
          </p>
          {config.advancedResources &&
            (
              [
                "nodeResources",
                "clusterResources",
                "unscheduledResources",
              ] as const
            ).map((key, index) => {
              const name = ["node", "cluster", "unscheduled"][index],
                value = config[key] ?? { mode: "Inherit" as const };
              return (
                <div className="role-config" key={key}>
                  <label>
                    {name} 资源策略
                    <select
                      aria-label={`${name} 资源策略`}
                      value={value.mode}
                      onChange={(event) =>
                        role(key, event.target.value as "Inherit" | "Override")
                      }
                    >
                      <option value="Inherit">继承通用配置</option>
                      <option value="Override">独立配置（整块覆盖）</option>
                    </select>
                  </label>
                  {value.mode === "Override" && (
                    <ResourceFields
                      prefix={name}
                      value={value.resources ?? {}}
                      onChange={(resources) =>
                        patch({ [key]: { mode: "Override", resources } })
                      }
                    />
                  )}
                </div>
              );
            })}
        </section>
      )}
      <section className="config-section">
        <h3>配套采集意图</h3>
        <label className="checkbox-line"><input type="checkbox" aria-label="启用配套采集" checked={config.serviceMonitor?.enabled ?? false} onChange={event => patch({serviceMonitor: {...(config.serviceMonitor ?? {interval:"30s",scrapeTimeout:"10s"}), enabled:event.target.checked}})} />启用配套采集</label>
        <p className="help">保存草稿不改变集群。明确下发后由 Operator 维护配套 ServiceMonitor；归属使用固定的实例创建用户。关闭采集不停止 exporter，挂起保留配套配置，停用清理全部本实例资源。</p>
        {config.serviceMonitor && <div className="field-grid">
          <label>采集间隔<input aria-label="采集间隔" value={config.serviceMonitor.interval} onChange={event => patch({serviceMonitor:{...config.serviceMonitor!,interval:event.target.value}})} placeholder="30s" /></label>
          <label>采集超时<input aria-label="采集超时" value={config.serviceMonitor.scrapeTimeout} onChange={event => patch({serviceMonitor:{...config.serviceMonitor!,scrapeTimeout:event.target.value}})} placeholder="10s" /></label>
        </div>}
      </section>
      <section className="config-section">
        <h3>采集内容与过滤</h3>
        <div className="field-grid">
          <label>
            collectors
            <select
              aria-label="collectors"
              value={config.collectors.mode}
              onChange={(event) =>
                patch({
                  collectors: {
                    ...config.collectors,
                    mode: event.target.value,
                  },
                })
              }
            >
              <option value="Default">Default · 默认 28 项</option>
              <option value="Include">Include · 明确选择</option>
            </select>
          </label>
          <label>
            指标过滤
            <select
              aria-label="指标过滤"
              value={config.metricFilter.mode}
              onChange={(event) =>
                patch({
                  metricFilter: {
                    ...config.metricFilter,
                    mode: event.target.value,
                  },
                })
              }
            >
              <option value="All">All · 全部指标</option>
              <option value="Allow">Allow · 允许匹配</option>
              <option value="Deny">Deny · 拒绝匹配</option>
            </select>
          </label>
        </div>
        {config.collectors.mode === "Include" && (
          <div
            className="collector-grid"
            role="group"
            aria-label="collector 选择"
          >
            {collectors.map((name) => (
              <label className="checkbox-line" key={name}>
                <input
                  type="checkbox"
                  checked={config.collectors.names?.includes(name) ?? false}
                  onChange={() =>
                    patch({
                      collectors: {
                        mode: "Include",
                        names: toggle(config.collectors.names, name),
                      },
                    })
                  }
                />
                {name}
              </label>
            ))}
          </div>
        )}
        {config.deploymentMode === "DaemonSetSharded" && (
          <p className="help">
            DaemonSetSharded 必须包含 pods 与至少一个非 Pod collector。
          </p>
        )}
        {config.metricFilter.mode !== "All" && (
          <label>
            指标正则（每行一项）
            <textarea
              aria-label="指标正则（每行一项）"
              rows={4}
              value={(config.metricFilter.patterns ?? []).join("\n")}
              onChange={(event) =>
                patch({
                  metricFilter: {
                    ...config.metricFilter,
                    patterns: event.target.value.split("\n"),
                  },
                })
              }
            />
            <span className="help">
              使用 Go regexp；每项不能包含逗号，不自动添加完整名称锚点。
            </span>
          </label>
        )}
        <div className="field-grid">
          <label>
            namespace 过滤
            <select
              aria-label="namespace 过滤"
              value={config.namespaceFilter.mode}
              onChange={(event) =>
                patch({
                  namespaceFilter: {
                    ...config.namespaceFilter,
                    mode: event.target.value,
                  },
                })
              }
            >
              <option value="All">All · 全部 namespace</option>
              <option value="Include">Include · 名称与正则并集</option>
            </select>
          </label>
          <div className="namespace-actions">
            <button type="button" onClick={onRefreshNamespaces}>
              刷新 namespace
            </button>
          </div>
        </div>
        {namespacesError && (
          <div className="alert error" role="alert">
            {namespacesError}
          </div>
        )}
        {config.namespaceFilter.mode === "Include" && (
          <>
            <div className="namespace-picker">
              <button
                type="button"
                aria-expanded={namespaceOpen}
                onClick={onNamespaceOpen}
              >
                选择 namespace
              </button>
              <span className="help">
                已选 {(config.namespaceFilter.names ?? []).length} 项
              </span>
              {namespaceOpen && (
                <div
                  className="namespace-options"
                  role="group"
                  aria-label="namespace 选择"
                >
                  {namespaces.map((name) => (
                    <label className="checkbox-line" key={name}>
                      <input
                        type="checkbox"
                        checked={
                          config.namespaceFilter.names?.includes(name) ?? false
                        }
                        onChange={() =>
                          patch({
                            namespaceFilter: {
                              ...config.namespaceFilter,
                              names: toggle(config.namespaceFilter.names, name),
                            },
                          })
                        }
                      />
                      {name}
                    </label>
                  ))}
                  {namespaces.length === 0 && (
                    <p className="help">当前没有可读取的 namespace</p>
                  )}
                </div>
              )}
            </div>
            <label>
              namespace 全名正则（每行一项）
              <textarea
                aria-label="namespace 全名正则（每行一项）"
                rows={3}
                value={(config.namespaceFilter.namePatterns ?? []).join("\n")}
                onChange={(event) =>
                  patch({
                    namespaceFilter: {
                      ...config.namespaceFilter,
                      namePatterns: event.target.value.split("\n"),
                    },
                  })
                }
              />
              <span className="help">
                名称与完整匹配的正则取并集。仅用无匹配正则且不选名称时，可得到零匹配；不会自动放宽为
                All。
              </span>
            </label>
          </>
        )}
      </section>
    </fieldset>
  );
}

// 空资源输入按省略处理；独立角色始终显示完整资源块，避免暗中逐字段继承。
function ResourceFields({
  value,
  prefix,
  onChange,
}: {
  value: Resources;
  prefix: string;
  onChange: (value: Resources) => void;
}) {
  return (
    <div className="resources-grid">
      <span />
      <span className="help">request</span>
      <span className="help">limit（可不填）</span>
      {(["CPU", "Memory"] as const).map((kind, index) => (
        <div className="resource-row" key={kind}>
          <span>{index === 0 ? "CPU" : "内存"}</span>
          {(["request", "limit"] as const).map((type) => {
            const key = `${type}${kind}` as keyof Resources;
            return (
              <input
                key={key}
                aria-label={`${prefix} ${index === 0 ? "CPU" : "内存"} ${type}`}
                value={value[key] ?? ""}
                placeholder={index === 0 ? "例如 100m" : "例如 250Mi"}
                onChange={(event) =>
                  onChange({ ...value, [key]: event.target.value })
                }
              />
            );
          })}
        </div>
      ))}
    </div>
  );
}
function RuntimeBadge({ runtime }: { runtime?: Runtime }) {
  const summary = runtime?.summary ?? "Unknown";
  return (
    <span
      title={runtime?.error}
      className={
        "badge " +
        (summary === "Available"
          ? "ok"
          : summary === "NotReady"
            ? "warn"
            : "gray")
      }
    >
      {stateName(summary)}
    </span>
  );
}
