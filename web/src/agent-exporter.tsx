// 快捷关联只在明确点击时修改原生selector；读取页面或切换登录身份不改变已保存目标。
export type ExporterRule = {mode: string; selector?: {matchLabels?: Record<string,string>; matchExpressions?: {key:string;operator:string;values?:string[]}[]}};
const userLabel = "monitor-mgr.io/user-id";

// 展示固定创建用户的快捷关联，并保留原生 selector 编辑。
export function AgentExporterAssociation({creatorUserId,object,disabled,onChange}:{creatorUserId?:string;object:ExporterRule;disabled:boolean;onChange:(rule:ExporterRule)=>void}) {
  const target = object.mode === "Labels" ? object.selector?.matchLabels?.[userLabel] : undefined;
  return <section className="publication-item" aria-label="按用户关联 exporter">
    <h4>按用户关联 exporter</h4>
    <p>当前规则目标用户：<strong>{target || "未按 user 标签关联"}</strong></p>
    <p className="help">快捷默认用户：{creatorUserId || "未知"}。选择后固定为原生 ServiceMonitor 对象标签；namespace 发现范围仍需单独配置。</p>
    <button type="button" disabled={disabled || !creatorUserId} onClick={()=>{
      if (!creatorUserId) return;
      // 关键：使用实例的可靠创建用户，不依据当前登录账号或创建时角色推测身份。
      onChange({mode:"Labels",selector:{...object.selector,matchLabels:{...object.selector?.matchLabels,[userLabel]:creatorUserId}}});
    }}>按创建用户关联 exporter</button>
    {!creatorUserId && <p role="status" className="help">实例创建用户未知，无法使用快捷关联。取得可靠创建用户后可用；尚未保存的新实例请先保存草稿。仍可按现有权限手动配置自定义标签规则。</p>}
    <p className="help">可继续编辑下方 matchLabels / matchExpressions；标签用于采集分组，实例管理权限单独检查。匹配不代表已采到指标。</p>
  </section>;
}
