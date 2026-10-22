// 实例创建身份与当前管理权限分别表达，历史未知事实不能由账号当前角色补造。
package control

import rpc "github.com/fjsxy/monitor-mgr/kitex_gen/control"

// instanceCreatorSchema 仅扩展新字段；旧实例保留未知，可靠历史补齐由独立流程负责。
var instanceCreatorSchema = []string{
	`ALTER TABLE instances ADD COLUMN creator_user_id TEXT NOT NULL DEFAULT ''`,
	`ALTER TABLE instances ADD COLUMN creator_role TEXT NOT NULL DEFAULT '' CHECK(creator_role IN ('','ADMIN','USER'))`,
}

// projectInstanceAccess 根据当前身份计算读模型，不修改固定创建事实或外部实例归属。
func projectInstanceAccess(p *Principal, cl *rpc.Cluster, i *rpc.ObservationInstance) {
	i.CreatorIdentityKnown = i.CreatorUserId != "" && i.CreatorRole != ""
	i.CanManage = true
	i.ReadOnlyReason = ""
	switch {
	case i.ReadOnly:
		i.ReadOnlyReason = "平台未纳管实例只读"
	case cl.State != "ACTIVE":
		i.ReadOnlyReason = "集群已注销，历史只读"
	case i.Lifecycle == "DELETED":
		i.ReadOnlyReason = "实例已删除，历史只读"
	case p.Account.Role != "ADMIN" && i.CreatorRole == "ADMIN":
		i.ReadOnlyReason = "管理员创建的实例对普通用户只读"
	case p.Account.Role != "ADMIN" && i.CreatorRole != "USER":
		i.ReadOnlyReason = "创建时角色未知，须由管理员确认管理权限"
	}
	i.CanManage = i.ReadOnlyReason == ""
}

// instanceWritePermission 在已有集群访问授权之上收紧实例写权限，不限制普通用户之间的创建关系。
func instanceWritePermission(p *Principal, i *rpc.ObservationInstance) error {
	// 关键：保护依据创建时角色快照；降级、升权或归属转移均不能改写历史授权事实。
	if p.Account.Role == "ADMIN" {
		return nil
	}
	if i.CreatorRole == "ADMIN" {
		return Error("FORBIDDEN", "管理员创建的实例对普通用户只读")
	}
	if i.CreatorRole != "USER" {
		return Error("FORBIDDEN", "创建时角色未知，须由管理员确认管理权限")
	}
	return nil
}
