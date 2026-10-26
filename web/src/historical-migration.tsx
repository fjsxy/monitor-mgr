import {useState} from 'react';
import {api, APIError} from './api';

type Resource={operationId:string;resourceKey:string;objectPath:string;originalUid:string;documentFingerprint:string;state:string;possiblySent:boolean;frozenYaml:string;currentUid:string;resourceVersion:string;currentYaml:string;missing:boolean;error:string;resolvedBy:string;resolvedAt:string};
type Inventory={instanceId:string;snapshotId:string;observedAt:string;schemaVersion:number;currentVersionId:string;resources:Resource[];agents:{name:string;namespace:string;uid:string;selectionYaml:string;selectedPaths:string[]}[];impactComplete:boolean;error:string;protectionOperationId:string};

// 迁移面板只盘点与确认外部处理事实；页面不能删除、修复或接管 ServiceMonitor。
export function HistoricalMigration({instanceId,isAdmin,active,onUpdated}:{instanceId:string;isAdmin:boolean;active:boolean;onUpdated:()=>void|Promise<void>}){
 const [value,setValue]=useState<Inventory|null>(null),[error,setError]=useState(''),[busy,setBusy]=useState(false),[confirmed,setConfirmed]=useState(false);
 async function load(){setBusy(true);setError('');try{setValue(await api<Inventory>(`/instances/${instanceId}/historical-migration`));setConfirmed(false)}catch(e){setError(e instanceof APIError?e.message:'迁移盘点读取失败')}finally{setBusy(false)}}
 async function record(resource:Resource,parent:boolean){if(!value)return;setBusy(true);setError('');try{
 // 关键：原UID未知明确传空，禁止用currentUid代替；两个确认入口分别绑定原SM与父CR。
 await api(`/instances/${instanceId}/${parent?'historical-operation-releases':'historical-servicemonitor-resolutions'}`,'POST',{snapshotId:value.snapshotId,operationId:resource.operationId,resourceKey:resource.resourceKey,objectPath:resource.objectPath,originalUid:resource.originalUid,documentFingerprint:resource.documentFingerprint,confirmExternalHandling:true},crypto.randomUUID());await onUpdated();await load();
 }catch(e){setError(e instanceof APIError?e.message:'确认失败，请重新盘点')}finally{setBusy(false)}}
 function download(){if(!value)return;const url=URL.createObjectURL(new Blob([JSON.stringify(value,null,2)],{type:'application/json'}));const link=document.createElement('a');link.href=url;link.download=`historical-migration-${value.snapshotId}.json`;link.click();URL.revokeObjectURL(url)}
 return <section className="publication-item" aria-label="历史采集迁移">
 <h4>历史 exporter 显式迁移</h4><p className="help">先保存父 CR、旧 ServiceMonitor 与 Agent 选择范围快照。由集群管理员核实 UID/RV、所有权和采集空窗后在平台外处理旧对象；平台仅记录处理事实，新明确发布才启用 Operator 采集。旧删除须先确认原父 CR、旧 SM 与全部 workload/Pod 已在平台外清理，随后明确收尾；原发送结果保持原样。</p>
 <button disabled={busy||!active} onClick={()=>void load()}>盘点历史采集迁移</button>
 {error&&<p role="alert" className="alert error">{error}</p>}
 {value&&<><p>快照 {value.snapshotId} · {new Date(value.observedAt).toLocaleString()}</p><button onClick={download}>保存迁移快照</button><p>{value.impactComplete?'Agent 选择范围已完整读取':'Agent 影响范围未确认'} {value.error}</p>
 <p className="help">选择匹配只说明配置影响，不代表实际采集成功。切换后仍需核对真实 targets 与样本；可能出现采集空窗。</p>
 {value.agents.map(agent=><details key={agent.uid}><summary>Agent {agent.namespace}/{agent.name} · UID {agent.uid} · 选择旧路径 {agent.selectedPaths.length}</summary><pre>{agent.selectionYaml}</pre>{agent.selectedPaths.map(path=><p key={path}>{path}</p>)}</details>)}
 {isAdmin&&active&&<label><input type="checkbox" checked={confirmed} onChange={e=>setConfirmed(e.target.checked)}/>我已保存快照、核实采集影响并在平台外显式处理原对象；未知原 UID 保持未知</label>}
 {value.resources.map(item=><article className="publication-item" key={`${item.operationId}/${item.resourceKey}`} aria-label={`历史资源 ${item.operationId} ${item.resourceKey}`}>
 <strong>{item.objectPath}</strong><p>原操作 {item.operationId} · {item.state} · {item.possiblySent?'可能已发送':'未发送'}</p><p>原 UID：{item.originalUid||'未知（不能凭同名补认）'} · 当前 UID：{item.currentUid||'无'} · RV：{item.resourceVersion||'无'}</p><p>{item.error||(item.missing?'原冻结路径当前不存在':'原冻结路径当前存在对象')}</p><p>冻结指纹 {item.documentFingerprint}</p>
 <details><summary>原冻结配置</summary><pre>{item.frozenYaml}</pre></details><details><summary>当前 UID/RV/owner/spec 快照</summary><pre>{item.currentYaml||'当前无对象或读取失败'}</pre></details>
 {item.resolvedBy&&<p>旧 SM 已处理确认：{item.resolvedBy} · {item.resolvedAt}；旧执行状态保持原样</p>}
 {isAdmin&&active&&item.resourceKey!=='component'&&!item.resolvedBy&&<button disabled={busy||!confirmed||!value.impactComplete||!item.missing||!!item.error} onClick={()=>void record(item,false)}>确认旧 ServiceMonitor 已外部处理</button>}
 {isAdmin&&active&&item.resourceKey==='component'&&value.protectionOperationId===item.operationId&&<button disabled={busy||!confirmed||!value.impactComplete||!!item.error} onClick={()=>void record(item,true)}>独立核验父 CR 并解除原任务互斥</button>}
 </article>)}
 {!value.resources.length&&<p>没有旧冻结资源；无需制造旧 ServiceMonitor，可按新明确发布流程启用。</p>}
 </>}
 </section>
}
