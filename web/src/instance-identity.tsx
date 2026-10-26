// 创建事实与当前管理能力分别展示；未知身份不采用集群归属或当前登录账号补造。
export type InstanceIdentityValue = {
  managementConfirmation?: { actorId: string; confirmedAt: string; allowClusterManagers: boolean };
  creatorUserId: string;
  creatorRole: string;
  creatorIdentityKnown: boolean;
  canManage: boolean;
  readOnlyReason: string;
};

export function InstanceIdentity({ value, compact = false }: { value: InstanceIdentityValue; compact?: boolean }) {
  const role = value.creatorRole === "ADMIN" ? "管理员" : value.creatorRole === "USER" ? "普通用户" : "未知";
  return <div className="help" aria-label={compact ? "实例创建身份摘要" : "实例创建身份"}>
    <p>创建用户：{value.creatorUserId || "未知"}</p>
    <p>创建时角色：{role}{!value.creatorIdentityKnown && " · 创建身份未完整核实"}</p>
    {value.managementConfirmation && <p>管理权限确认：{value.managementConfirmation.allowClusterManagers ? "允许现有集群管理用户" : "仅管理员管理"} · 确认用户：{value.managementConfirmation.actorId} · 确认时间：{value.managementConfirmation.confirmedAt}</p>}
    {!value.canManage && <p role={compact ? undefined : "status"}>只读：{value.readOnlyReason || "当前无管理权限"}</p>}
  </div>;
}
