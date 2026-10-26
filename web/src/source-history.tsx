import type { ReactNode } from "react";
import {YAML} from "./instance-yaml";
import type {Int64} from "./api";

// 每个来源保留独立成功快照；后台给出的historical/stale是可信度边界，不在浏览器续期。
export type RuntimeSnapshot = {
  summary: string; desiredState?: string; deploymentMode?: string;
  availablePods?: number; desiredPods?: number; lastSuccessAt?: string;
  error?: string; shards?: number; paused?: boolean;
};
export type SourceStatus = {
  source: string; lastAttemptAt: string; lastSuccessAt: string;
  error: string; historical: boolean; runtimeSnapshot?: RuntimeSnapshot; uid?: string; stale?: boolean;
  companion?: {state:string;reason:string;message:string;parentUid:string;generation:Int64;observedGeneration:Int64;resource?:{apiVersion:string;kind:string;namespace:string;name:string;uid:string};yaml?:string};
};
const timestamp = (value?: string) => value ? new Date(value).toLocaleString() : "尚无成功观察";
const state = (value: string) => ({ Available: "可用", NotReady: "未就绪", Unknown: "未知", Missing: "原 CR 不存在", NoMatch: "无匹配", Suspended: "挂起", Disabled: "停用", Deleted: "已删除" } as Record<string, string>)[value] ?? value;

// 关键：历史快照只作最后成功证据，不回填当前摘要，也不能用CR成功更新其他来源时间。
export function SourceHistory({ source, children }: { source: SourceStatus; children?: ReactNode }) {
  const history = source.historical || source.stale || Boolean(source.error), snapshot = source.runtimeSnapshot;
  return <section className="publication-item source-history" aria-label={`来源 ${source.source}`}>
    <h4>{source.source === "storagePolicy" ? "实际存储策略" : source.source.replace("association:", "")} <span className={`badge ${history ? "warn" : "gray"}`}>{source.error ? "读取失败" : source.stale ? "已过期 · 历史数据" : history ? "历史数据" : "已读取"}</span></h4>
    <p className="help">最后尝试：{timestamp(source.lastAttemptAt)} · 最后成功：{timestamp(source.lastSuccessAt)}</p>
    {source.error && <p className="alert error">{source.error}</p>}
    {history && <p className="help">以下仅为历史记录，当前状态未知。</p>}
    {snapshot && <div className="source-snapshot" aria-label="最后成功观察">
      <h5>最后成功观察{history ? " · 历史快照" : " · 来源快照"}</h5>
      <dl className="runtime-details">
        <dt>运行摘要</dt><dd>{state(snapshot.summary)}</dd>
        <dt>可用 / 期望 Pod</dt><dd>{snapshot.availablePods !== undefined && snapshot.desiredPods !== undefined ? `${snapshot.availablePods} / ${snapshot.desiredPods}` : "未知"}</dd>
        {snapshot.deploymentMode && <><dt>部署模式</dt><dd>{snapshot.deploymentMode}</dd></>}
        {snapshot.shards !== undefined && <><dt>实际 shards</dt><dd>{snapshot.shards}</dd></>}
        {snapshot.paused !== undefined && <><dt>实际 paused</dt><dd>{String(snapshot.paused)}</dd></>}
      </dl>
      {snapshot.error && <p className="help">当时诊断：{snapshot.error}</p>}
    </div>}
    {source.companion && <div aria-label="Operator 配套观察">
      <p><strong>配套收敛：{history ? "当前未知" : ({Ready:"已收敛",Degraded:"故障",Reconciling:"未收敛",Disabled:"已关闭",Deleting:"清理中",Unknown:"未知"} as Record<string,string>)[source.companion.state] ?? source.companion.state}</strong></p>
      <p className="help">父 CR UID：{source.companion.parentUid}</p>
      <p>{source.companion.reason}：{source.companion.message}</p>
      {source.companion.resource && <p className="help">{source.companion.resource.apiVersion} {source.companion.resource.kind} {source.companion.resource.namespace}/{source.companion.resource.name} · UID {source.companion.resource.uid}</p>}
      <p className="help">平台仅只读观察，配套故障由 Operator 按已发布意图修复。配套收敛与 CR 下发成功分别记录；实际采集尚需 Agent 与样本证据。</p>
      {source.companion.yaml && <details><summary>只读配套配置{history ? "（历史）" : ""}</summary><YAML text={source.companion.yaml} /></details>}
    </div>}
    {children}
  </section>;
}
