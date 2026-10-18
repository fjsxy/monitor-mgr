import type { AgentConfiguration } from "./agent";

// 存储界面只表达类型化意图和真实核验结果，不提供PVC清理或owner修补入口。
export type StorageVolume = {namespace:string;name:string;uid:string;phase?:string;requestedStorage?:string;accessModes?:string[];storageClass?:string;mountedByPods?:string[];expectedPolicy?:string;retentionResult?:string;lastAttemptAt?:string;lastSuccessAt?:string;error?:string;statefulUID?:string;statefulName?:string;shard?:number;agentUID?:string;impactAction?:string};
export type StatefulPolicy={name:string;uid:string;whenScaled:string;whenDeleted:string};
export type StorageImpactValue={storageMode?:string;policies?:StatefulPolicy[];storageVolumes?:StorageVolume[]};
const resultNames:Record<string,string>={Retained:"已保留",Deleted:"已删除",Deleting:"删除中",Pending:"待独立核验",Unknown:"未知",Lost:"原保留卷已缺失",Completed:"分别完成",NOT_APPLICABLE:"不适用PVC保留策略",NOT_AFFECTED:"无受影响PVC",NotApplied:"未执行"};

export function AgentLifecycleFields({value,patch}:{value:AgentConfiguration;patch:(p:Partial<AgentConfiguration>)=>void}){
 return <><div className="field-grid"><label>期望运行状态<select aria-label="Agent期望运行状态" value={value.desiredState} onChange={e=>patch({desiredState:e.target.value})}><option value="Running">运行</option><option value="Suspended">挂起</option></select></label><label>shards<input aria-label="Agent shards" type="number" min={1} step={1} required value={value.shards} onChange={e=>patch({shards:e.currentTarget.valueAsNumber})}/></label></div><p className="help">每 shard 单副本，共用同一容器资源声明；运行/挂起分别 replicas=1/0，始终 paused=false。减少 shards 使用 whenDeleted。</p></>
}
export function AgentStorageFields({value,patch,fixed}:{value:AgentConfiguration;patch:(p:Partial<AgentConfiguration>)=>void;fixed:boolean}){
 const storage=value.storage;
 return <section className="config-section"><h3>Agent WAL 存储</h3><div className="field-grid"><label>存储模式<select aria-label="存储模式" value={storage.mode} disabled={fixed} onChange={e=>patch({storage:{...storage,mode:e.target.value,capacity:e.target.value==="PVC"?"1Gi":undefined}})}><option value="PVC">PVC</option><option value="emptyDir">emptyDir</option></select></label>{storage.mode==="PVC"&&<label>PVC 容量<input aria-label="PVC 容量" value={storage.capacity??""} required disabled={fixed} onChange={e=>patch({storage:{...storage,capacity:e.target.value}})}/></label>}</div>
 {storage.mode==="PVC"?<><div className="field-grid">{(["whenScaled","whenDeleted"] as const).map(key=><label key={key}>{key} · {key==="whenScaled"?"挂起缩零":"移除shard/删除工作负载"}<select aria-label={key} value={storage[key]} onChange={e=>patch({storage:{...storage,[key]:e.target.value}})}><option value="Retain">Retain · 保留</option><option value="Delete">Delete · 删除</option></select></label>)}</div><p className="help">RWO · 集群默认 StorageClass。先单独发布策略，再核验实际 StatefulSet 采用后执行危险操作；whenDeleted 也影响 Operator 替换 StatefulSet。PVC结果独立确认，平台不清理PVC。</p><p className="help">Retain不保证namespace或集群删除后的底层数据；PVC生命周期不等于WAL保留时长。</p></>:<p className="help">emptyDir不创建WAL PVC，保留策略不生效。Pod删除/替换或挂起缩零可丢失未发WAL；容器重启本身不删除emptyDir。</p>}
 <p className="help">首次成功创建后模式、PVC容量及身份固定；调整须复制为新实例并填写新名称，使用新CR和新卷，不自动挂载、迁移或清理旧Retain卷。</p></section>
}
export function StoragePolicies({policies,mode}:{policies?:StatefulPolicy[];mode?:string}){
 if(mode==="emptyDir")return <p className="help">实际存储：emptyDir · PVC保留策略不适用。</p>;
 return <>{mode&&<p>实际存储：{mode}</p>}{policies&&<div className="table-wrap"><table><thead><tr><th>实际 StatefulSet</th><th>whenScaled</th><th>whenDeleted</th></tr></thead><tbody>{policies.map(p=><tr key={p.uid}><td>{p.name}<div className="help">UID {p.uid}</div></td><td>{p.whenScaled}</td><td>{p.whenDeleted}</td></tr>)}</tbody></table>{policies.length===0&&<p className="help">本次完整读取未发现原Agent控制的StatefulSet。</p>}</div>}</>
}
export function StorageImpact({value}:{value:StorageImpactValue}){
 if(!value.storageMode)return null;
 return <section className="config-section"><h3>实际存储影响</h3><StoragePolicies policies={value.policies} mode={value.storageMode}/>{value.storageMode==="PVC"&&<><p className="help">挂起按whenScaled；移除shard/删除Agent按whenDeleted。策略未采用或原卷身份不明时禁止执行；原CR/工作负载完成不代表PVC结果已确认。</p><StorageVolumeResults volumes={value.storageVolumes??[]} preview/></>}{value.storageMode==="emptyDir"&&<p className="help">挂起或删除会删除Pod中的emptyDir，未发WAL可能丢失。</p>}</section>
}
export function StorageVolumeResults({volumes,preview=false}:{volumes:StorageVolume[];preview?:boolean}){
 if(!volumes.length)return <p className="help">{preview?"本次完整读取没有受影响的原PVC。":"没有需要独立核验的PVC。"}</p>;
 return <div className="table-wrap"><table><thead><tr><th>PVC / namespace</th><th>原身份 / shard</th><th>策略 / {preview?"预期":"独立结果"}</th></tr></thead><tbody>{volumes.map(v=><tr key={`${v.uid}-${v.statefulUID}`}><td>{v.name}<div className="help">{v.namespace}</div></td><td><div className="help">PVC UID {v.uid}</div><div className="help">{v.statefulName} · shard {v.shard??"未知"}</div></td><td>{v.impactAction}={v.expectedPolicy??"未知"} · {preview?"执行后独立确认":resultNames[v.retentionResult??""]??v.retentionResult??"未知"}{v.lastAttemptAt&&<div className="help">最后尝试：{new Date(v.lastAttemptAt).toLocaleString()}</div>}{v.lastSuccessAt&&<div className="help">最后成功：{new Date(v.lastSuccessAt).toLocaleString()}</div>}{v.error&&<div className="alert error">{v.error}</div>}</td></tr>)}</tbody></table></div>
}
export function StorageResult({result,volumes}:{result?:string;volumes?:StorageVolume[]}){
 if(!result)return null;
 return <section className="config-section"><h4>独立存储结果：{resultNames[result]??result}</h4><p className="help">实例完成与PVC结果分别确认；未决卷继续只读核验，平台不直接删除PVC或移除finalizer。</p>{volumes&&volumes.length>0&&<StorageVolumeResults volumes={volumes}/>}</section>
}

// StorageVolumeOutcome在当前卷来源旁只展示该UID的独立历史结果，不冒充当前挂载状态。
export function StorageVolumeOutcome({volume:v}:{volume:StorageVolume}){
 if(!v.retentionResult)return <span className="help">尚无独立变更结果</span>;
 return <>{v.impactAction}={v.expectedPolicy} · {resultNames[v.retentionResult]??v.retentionResult}{v.lastAttemptAt&&<div className="help">最后尝试：{new Date(v.lastAttemptAt).toLocaleString()}</div>}{v.lastSuccessAt&&<div className="help">最后成功：{new Date(v.lastSuccessAt).toLocaleString()}</div>}{v.error&&<div className="alert error">{v.error}</div>}</>
}
