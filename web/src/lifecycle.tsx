import { useEffect, useRef, useState } from "react";
import { api, APIError } from "./api";

// 通用生命周期界面只发送类型化意图；服务端以当前权限和修订决定能否执行。
export type Operation = { operationId:string; action?:string; state:string; message:string; createdAt:string; versionId?:string; attemptId?:string; revision?:number };
type Attempt = { attemptId:string; state:string; possiblySent:boolean; revision:number; sequence:number; actorId:string; message:string; createdAt:string };
const names:Record<string,string> = {QUEUED:"已受理",CHECKING:"检查中",DISPATCHED:"可能已发送",SUCCEEDED:"已完成",FAILED:"失败",CANCELLED:"已取消",WAITING_CONFIRMATION:"等待明确继续",RESULT_UNCERTAIN:"结果待确认",IDENTITY_UNCERTAIN:"资源身份无法确认",DELETION_VERIFYING:"清理核验中",DELETION_BLOCKED:"清理受阻"};
export function LifecycleActions({operation,onOperation}:{operation:Operation;onOperation:(value:Operation)=>Promise<void>|void}) {
 const [attempts,setAttempts]=useState<Attempt[]>([]),[busy,setBusy]=useState(false),[error,setError]=useState("");
 const command=useRef<{signature:string;key:ReturnType<typeof crypto.randomUUID>}|null>(null);
 async function load(){const result=await api<{attempts:Attempt[]}>(`/operations/${operation.operationId}/attempts`);setAttempts(result.attempts)}
 useEffect(()=>{void load().catch(()=>setError("执行尝试读取失败，请刷新历史"))},[operation.operationId,operation.state,operation.revision]);
 const current=attempts.find(a=>a.attemptId===operation.attemptId);
 const unsent=current && !current.possiblySent && ["QUEUED","CHECKING","WAITING_CONFIRMATION"].includes(operation.state);
 async function act(action:string){
  if(!current)return;setBusy(true);setError("");
  const body={expectedOperationRevision:operation.revision,expectedAttemptId:current.attemptId,expectedAttemptRevision:current.revision};
  const signature=JSON.stringify([operation.operationId,action,body]);if(command.current?.signature!==signature)command.current={signature,key:crypto.randomUUID()};
  try{const next=await api<Operation>(`/operations/${operation.operationId}/${action}`,"POST",action==="refresh"?undefined:body,command.current!.key);await onOperation(next);command.current=null;await load()}
  catch(e){setError(e instanceof APIError?e.message:"网络请求失败，可用同一动作查询或重试受理结果")}
  finally{setBusy(false)}
 }
 return <div className="lifecycle-actions">
  {error&&<p className="alert error" role="alert">{error}</p>}
  <div className="row-actions">
   {operation.state==="FAILED"&&<button disabled={busy||!current} onClick={()=>void act("retry")}>重试原版本</button>}
   {unsent&&<button disabled={busy} onClick={()=>void act("cancel")}>取消未发送任务</button>}
   {unsent&&operation.state==="WAITING_CONFIRMATION"&&<button disabled={busy} onClick={()=>void act("continue-unsent")}>继续原任务</button>}
   {["RESULT_UNCERTAIN","IDENTITY_UNCERTAIN","DELETION_VERIFYING","DELETION_BLOCKED"].includes(operation.state)&&<button disabled={busy||!current} onClick={()=>void act("refresh")}>只读核验结果</button>}
  </div>
  {operation.state==="IDENTITY_UNCERTAIN"&&<p className="help">未确认原 UID，不能凭同名资源补认、重新下发或删除。</p>}
  <details><summary>执行尝试（{attempts.length}）</summary>{attempts.map(a=><article className="publication-item" key={a.attemptId}><strong>尝试 {a.sequence} · {names[a.state]??a.state}</strong><p>{a.message}</p><p className="help">{a.possiblySent?"可能已发送":"持久记录可证明未发送"} · {new Date(a.createdAt).toLocaleString()}</p><p className="help">发起账号 {a.actorId}</p><p className="help">attemptId {a.attemptId}</p></article>)}</details>
 </div>
}
