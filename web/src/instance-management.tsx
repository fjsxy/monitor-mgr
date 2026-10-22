import { useState } from "react";
import { api } from "./api";
import type { InstanceIdentityValue } from "./instance-identity";

// 历史角色未知时由管理员明确选择管理权限；确认只改变权限，不能补造创建用户或历史角色。
export function InstanceManagement<T extends InstanceIdentityValue & { id: string; readOnly: boolean; lifecycle: string }>({ value, isAdmin, onConfirmed }: { value: T; isAdmin: boolean; onConfirmed: (next: T) => void }) {
  const [open, setOpen] = useState(false), [allow, setAllow] = useState(""), [busy, setBusy] = useState(false), [error, setError] = useState("");
  if (!isAdmin || value.creatorRole !== "" || value.readOnly || value.lifecycle === "DELETED") return null;
  async function confirm() {
    if (!allow || busy) return;
    setBusy(true); setError("");
    try {
      // 关键：服务端重新校验当前管理员及未知历史角色，客户端只提交明确权限结果。
      const next = await api<T>(`/instances/${value.id}/management-confirmations`, "POST", { allowClusterManagers: allow === "allow" }, crypto.randomUUID());
      onConfirmed({ ...value, ...next }); setOpen(false); setAllow("");
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  return <div>
    <button onClick={() => { setOpen(true); setAllow(""); setError(""); }}>确认历史实例管理权限</button>
    {open && <section role="dialog" aria-modal="true" aria-label="确认历史实例管理权限">
      <p>创建时角色仍保留未知。此操作不会补造创建用户，只确定现有集群管理用户是否可以变更该实例。</p>
      <label>管理权限结果<select aria-label="管理权限结果" value={allow} onChange={e => setAllow(e.target.value)}>
        <option value="">请选择确认结果</option><option value="restrict">仅管理员管理</option><option value="allow">允许现有集群管理用户</option>
      </select></label>
      {error && <p role="alert">{error}</p>}
      <button disabled={busy} onClick={() => setOpen(false)}>返回</button><button disabled={busy || !allow} onClick={() => void confirm()}>确认管理权限</button>
    </section>}
  </div>;
}
